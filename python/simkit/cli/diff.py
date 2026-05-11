"""``pvt diff`` subcommand.

Diffs two slices: aligned result-table + unified netlist diff.

Output modes:

* default: plain-text aligned table (rows filtered by ``--threshold``
  unless they are ``only_a`` / ``only_b`` / ``status_mismatch``)
  followed by the netlist diff.
* ``--json``: structured payload with every row (no threshold filter,
  no sentinel hiding).

Exit codes:
    0  success — runs differ, no errors
    1  domain error (slice not found, ambiguous, db corrupt)
    3  filesystem / DB IO error
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import List, Optional

from simkit.db import bootstrap, connect
from simkit.diff import DiffResult, DiffRow, compute_diff
from simkit.errors import AmbiguousSliceError, SliceNotFoundError, SimkitError
from simkit.project import PvtProjectError, load_pvtproject


_COL = {
    "test": 14,
    "corner": 7,
    "point": 5,
    "output": 14,
    "value_a": 12,
    "value_b": 12,
    "dAbs": 11,
    "dRel": 9,
}


def add_subparser(sub) -> None:
    p = sub.add_parser(
        "diff",
        help="Diff two slices: results table + netlist.",
        description=(
            "Aligned table of (test, corner, point, output, value_a, "
            "value_b, dAbs, dRel) followed by unified diff of input.scs. "
            "Slice args resolve via exact-label-match then run_id-prefix."
        ),
    )
    p.add_argument("slice_a", help="First slice (label or run_id prefix).")
    p.add_argument("slice_b", help="Second slice (label or run_id prefix).")
    p.add_argument(
        "--threshold", type=float, default=0.0,
        help=(
            "Hide rows where |rel_delta| ≤ THRESHOLD. Only filters "
            "fully-numeric matched rows; only_a / only_b / "
            "status_mismatch rows always shown. Default 0 (show all)."
        ),
    )
    p.add_argument(
        "--include-status", action="store_true",
        help="Include __sim_status__ sentinel rows in the output.",
    )
    p.add_argument(
        "--json", dest="as_json", action="store_true",
        help=(
            "Emit a JSON object instead of the default table. Includes "
            "every row regardless of --threshold."
        ),
    )
    p.add_argument(
        "--db", type=Path, default=None,
        help=(
            "Override DB path. Default: <dbRoot>/simkit.duckdb from the "
            ".pvtproject discovered via PVT_PROJECT or cwd-walker."
        ),
    )
    p.set_defaults(func=run)


def _resolve_db_path(args) -> Path:
    if args.db is not None:
        return Path(args.db).expanduser().resolve()
    proj = load_pvtproject()
    return Path(proj.db_root) / "simkit.duckdb"


def run(args) -> int:
    try:
        db_path = _resolve_db_path(args)
    except PvtProjectError as exc:
        print(f"pvt diff: {exc}", file=sys.stderr)
        return 3

    if not db_path.is_file():
        print(
            f"pvt diff: DB not found: {db_path} "
            "(run `pvt ingest` first)",
            file=sys.stderr,
        )
        return 3

    runs_root = db_path.parent / "runs"

    try:
        con = connect(db_path, read_only=True)
    except Exception as exc:  # pragma: no cover - duckdb wraps OSError
        print(f"pvt diff: cannot open DB {db_path}: {exc}", file=sys.stderr)
        return 3
    try:
        try:
            result = compute_diff(
                con,
                slice_a=args.slice_a,
                slice_b=args.slice_b,
                runs_root=runs_root,
            )
        except (SliceNotFoundError, AmbiguousSliceError) as exc:
            print(f"pvt diff: {exc}", file=sys.stderr)
            return 1
        except SimkitError as exc:
            print(f"pvt diff: {exc}", file=sys.stderr)
            return 1
    finally:
        con.close()

    if args.as_json:
        print(json.dumps(result.to_dict(), indent=2, default=_json_default))
    else:
        _print_table(
            result,
            threshold=args.threshold,
            include_status=args.include_status,
        )
    return 0


def _json_default(o):
    # difflib output may include None deltas; everything else is JSON-native.
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    raise TypeError(f"not serialisable: {type(o).__name__}")


def _print_table(
    result: DiffResult,
    *,
    threshold: float,
    include_status: bool,
) -> None:
    headers = ("test", "corner", "point", "output",
               "value_a", "value_b", "dAbs", "dRel")
    widths = [_COL[h] for h in headers]
    sep = "  "

    print(
        f"# diff slice_a={result.slice_a_identifier!r} "
        f"(run_id={result.slice_a_run_id})"
    )
    print(
        f"#      slice_b={result.slice_b_identifier!r} "
        f"(run_id={result.slice_b_run_id})"
    )
    print()
    print(sep.join(h.ljust(w) for h, w in zip(headers, widths)))
    print(sep.join("-" * w for w in widths))

    shown = 0
    hidden_threshold = 0
    hidden_sentinel = 0
    for r in result.rows:
        if r.is_sentinel and not include_status:
            hidden_sentinel += 1
            continue
        if r.kind == "match" and r.rel_delta is not None:
            if abs(r.rel_delta) <= threshold:
                hidden_threshold += 1
                continue
        _print_row(r, widths, sep)
        shown += 1

    if shown == 0:
        print("(no rows)")
    notes = []
    if hidden_threshold:
        notes.append(
            f"{hidden_threshold} matched rows hidden by --threshold "
            f"{threshold:g}"
        )
    if hidden_sentinel:
        notes.append(
            f"{hidden_sentinel} __sim_status__ rows hidden "
            "(--include-status to show)"
        )
    if notes:
        print()
        for n in notes:
            print(f"[{n}]")

    # Netlist section.
    print()
    if result.netlist.note is not None:
        print(f"[netlist: {result.netlist.note}]")
    elif not result.netlist.diff_text:
        print(
            f"--- slice_a/{result.netlist.a_path}\n"
            f"+++ slice_b/{result.netlist.b_path}\n"
            "[netlist: files identical]"
        )
    else:
        sys.stdout.write(result.netlist.diff_text)


def _print_row(r: DiffRow, widths: List[int], sep: str) -> None:
    cells = (
        _trunc(r.test, widths[0]),
        _trunc(r.corner, widths[1]),
        str(r.point),
        _trunc(r.output, widths[3]),
        _fmt_value(r.value_a),
        _fmt_value(r.value_b),
        _fmt_signed(r.abs_delta),
        _fmt_pct(r.rel_delta),
    )
    print(sep.join(c.ljust(w) for c, w in zip(cells, widths)))
    if r.kind in ("only_a", "only_b", "status_mismatch"):
        tag = {
            "only_a": "[only in slice_a]",
            "only_b": "[only in slice_b]",
            "status_mismatch": (
                f"[status mismatch: a={r.status_a!r} b={r.status_b!r}]"
            ),
        }[r.kind]
        print(" " * (widths[0] + len(sep)) + tag)


def _fmt_value(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        return f"{v:.4g}"
    if isinstance(v, int):
        return str(v)
    return str(v)


def _fmt_signed(v) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else "-"
    return f"{sign}{abs(v):.3g}"


def _fmt_pct(v) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else "-"
    return f"{sign}{abs(v) * 100:.2f}%"


def _trunc(s: str, width: int) -> str:
    if len(s) <= width:
        return s
    if width <= 1:
        return s[:width]
    return s[: width - 1] + "…"


def main(argv: Optional[list] = None) -> int:
    """Standalone ``python -m simkit.cli.diff`` entry."""
    parser = argparse.ArgumentParser(prog="pvt-diff")
    sub = parser.add_subparsers(dest="cmd", required=True)
    add_subparser(sub)
    ns = parser.parse_args(["diff", *(argv if argv is not None else sys.argv[1:])])
    return ns.func(ns)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
