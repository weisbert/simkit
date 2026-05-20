"""``pvt trend`` subcommand — N-way cross-milestone comparison.

Where ``pvt diff`` aligns two slices, ``pvt trend`` aligns any number
of them side-by-side: one column per slice, one row per
``(test, corner, point, output)`` key. Slice args resolve via
exact-label, run_id-prefix, then milestone tag — so

    pvt trend PDR CDR FDR

does the obvious thing. Pass the slices oldest-first; the column order
is preserved.

Output modes:

* default: a plain-text table, one value column per slice, with a
  trailing ``dir`` column flagging the monotonic direction.
* ``--json``: structured :class:`simkit.trend.TrendResult` payload.

``--changed-only`` hides rows whose value is identical across every
slice — the usual "what actually moved" view.

Exit codes:
    0  success
    1  domain error (slice not found, ambiguous, db corrupt)
    3  filesystem / DB IO error
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from simkit.db import connect
from simkit.errors import AmbiguousSliceError, SimkitError, SliceNotFoundError
from simkit.project import PvtProjectError, load_pvtproject
from simkit.trend import TrendResult, TrendRow, compute_trend


def add_subparser(sub) -> None:
    p = sub.add_parser(
        "trend",
        help="Compare 2+ slices side-by-side (cross-milestone trend).",
        description=(
            "Aligns N slices into one table: a value column per slice "
            "plus a monotonic-direction flag. Slice args resolve via "
            "exact-label, run_id-prefix, then milestone tag. Pass them "
            "oldest-first, e.g. `pvt trend PDR CDR FDR`."
        ),
    )
    p.add_argument(
        "slices", nargs="+",
        help="Two or more slices (label, run_id prefix, or milestone).",
    )
    p.add_argument(
        "--changed-only", dest="changed_only", action="store_true",
        help="Hide rows whose value is identical across every slice.",
    )
    p.add_argument(
        "--include-status", action="store_true",
        help="Include __sim_status__ sentinel rows in the output.",
    )
    p.add_argument(
        "--json", dest="as_json", action="store_true",
        help="Emit a JSON object instead of the default table.",
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
    if len(args.slices) < 2:
        print("pvt trend: need at least two slices", file=sys.stderr)
        return 1

    try:
        db_path = _resolve_db_path(args)
    except PvtProjectError as exc:
        print(f"pvt trend: {exc}", file=sys.stderr)
        return 3

    if not db_path.is_file():
        print(
            f"pvt trend: DB not found: {db_path} (run `pvt ingest` first)",
            file=sys.stderr,
        )
        return 3

    try:
        con = connect(db_path, read_only=True)
    except Exception as exc:  # pragma: no cover - duckdb wraps OSError
        print(f"pvt trend: cannot open DB {db_path}: {exc}", file=sys.stderr)
        return 3
    try:
        try:
            result = compute_trend(con, slices=list(args.slices))
        except (SliceNotFoundError, AmbiguousSliceError, SimkitError) as exc:
            print(f"pvt trend: {exc}", file=sys.stderr)
            return 1
        except ValueError as exc:
            print(f"pvt trend: {exc}", file=sys.stderr)
            return 1
    finally:
        con.close()

    if args.as_json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        _print_table(
            result,
            changed_only=args.changed_only,
            include_status=args.include_status,
        )
    return 0


_DIR_GLYPH = {"up": "▲", "down": "▼", "mixed": "≈", "flat": "=", None: ""}


def _print_table(
    result: TrendResult,
    *,
    changed_only: bool,
    include_status: bool,
) -> None:
    for i, col in enumerate(result.columns):
        print(
            f"# col {i + 1}: {col.identifier!r} -> {col.display} "
            f"(run_id={col.run_id}, ts={col.timestamp})"
        )
    print()

    key_headers = ["test", "corner", "point", "output"]
    key_widths = [14, 7, 5, 14]
    val_width = 13
    val_headers = [c.display[:val_width] for c in result.columns]
    sep = "  "

    headers = key_headers + val_headers + ["dir"]
    widths = key_widths + [val_width] * len(result.columns) + [3]
    print(sep.join(h.ljust(w) for h, w in zip(headers, widths)))
    print(sep.join("-" * w for w in widths))

    shown = 0
    hidden_unchanged = 0
    hidden_sentinel = 0
    for r in result.rows:
        if r.is_sentinel and not include_status:
            hidden_sentinel += 1
            continue
        if changed_only and not r.varies:
            hidden_unchanged += 1
            continue
        _print_row(r, widths, sep)
        shown += 1

    if shown == 0:
        print("(no rows)")

    notes = []
    if hidden_unchanged:
        notes.append(f"{hidden_unchanged} unchanged rows hidden (--changed-only)")
    if hidden_sentinel:
        notes.append(
            f"{hidden_sentinel} __sim_status__ rows hidden "
            "(--include-status to show)"
        )
    if notes:
        print()
        for n in notes:
            print(f"[{n}]")


def _print_row(r: TrendRow, widths: List[int], sep: str) -> None:
    cells = [
        _trunc(r.test, widths[0]),
        _trunc(r.corner, widths[1]),
        str(r.point),
        _trunc(r.output, widths[3]),
    ]
    for c in r.cells:
        cells.append(_fmt_cell(c))
    cells.append(_DIR_GLYPH.get(r.direction, ""))
    print(sep.join(c.ljust(w) for c, w in zip(cells, widths)))


def _fmt_cell(cell) -> str:
    if not cell.present:
        return "—"
    txt = _fmt_value(cell.value)
    if cell.spec_status and cell.spec_status not in ("no_spec",):
        txt = f"{txt}[{cell.spec_status[:4].upper()}]"
    return txt


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


def _trunc(s: str, width: int) -> str:
    if len(s) <= width:
        return s
    if width <= 1:
        return s[:width]
    return s[: width - 1] + "…"


def main(argv: Optional[list] = None) -> int:
    """Standalone ``python -m simkit.cli.trend`` entry."""
    parser = argparse.ArgumentParser(prog="pvt-trend")
    sub = parser.add_subparsers(dest="cmd", required=True)
    add_subparser(sub)
    ns = parser.parse_args(["trend", *(argv if argv is not None else sys.argv[1:])])
    return ns.func(ns)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
