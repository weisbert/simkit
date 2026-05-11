"""``pvt list`` subcommand.

Lists runs (or only slices) in the project DB.

Output modes:

* default: aligned plain-text table to stdout, ordered ``timestamp DESC``.
* ``--json``: machine-readable JSON array of run dicts.

Exit codes:
    0  success (even when zero rows)
    3  filesystem / DB IO error
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from simkit.db import bootstrap, connect
from simkit.list_runs import RunRow, list_runs
from simkit.project import PvtProjectError, load_pvtproject


# Column widths for the default table view. Chosen to fit a typical
# 100-character terminal; longer strings are truncated with an ellipsis.
_COL_WIDTHS = {
    "run_id": 8,
    "timestamp": 25,
    "project": 14,
    "testbench": 22,
    "label": 14,
    "note": 30,
}


def add_subparser(sub) -> None:
    p = sub.add_parser(
        "list",
        help="List runs (and slices) in the project DB.",
        description=(
            "Print one row per run in <dbRoot>/simkit.duckdb, ordered by "
            "timestamp DESC. Default: aligned plain-text table. Use "
            "--json for machine-readable output."
        ),
    )
    p.add_argument(
        "--project", default=None,
        help="Filter to a specific runs.project_id (exact match).",
    )
    p.add_argument(
        "--slice-only", action="store_true",
        help="Show only labeled runs (slices).",
    )
    p.add_argument(
        "--json", dest="as_json", action="store_true",
        help="Emit a JSON array instead of the default table.",
    )
    p.add_argument(
        "--limit", type=int, default=None,
        help="Show at most N rows.",
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
        print(f"pvt list: {exc}", file=sys.stderr)
        return 3

    if not db_path.is_file():
        print(
            f"pvt list: DB not found: {db_path} "
            "(run `pvt ingest` first)",
            file=sys.stderr,
        )
        return 3

    try:
        con = connect(db_path, read_only=True)
    except Exception as exc:  # pragma: no cover - duckdb wraps OSError
        print(f"pvt list: cannot open DB {db_path}: {exc}", file=sys.stderr)
        return 3
    try:
        # Read-only mode: schemas already exist. Skip bootstrap (would
        # fail on read-only conn).
        rows = list_runs(
            con,
            project=args.project,
            slice_only=args.slice_only,
            limit=args.limit,
        )
    finally:
        con.close()

    if args.as_json:
        print(json.dumps([r.to_dict() for r in rows], indent=2))
    else:
        _print_table(rows)
    return 0


def _print_table(rows: List[RunRow]) -> None:
    headers = ("run_id", "timestamp", "project", "testbench", "label", "note")
    widths = [_COL_WIDTHS[h] for h in headers]
    sep = "  "

    line = sep.join(h.ljust(w) for h, w in zip(headers, widths))
    print(line)
    print(sep.join("-" * w for w in widths))

    if not rows:
        print("(no runs)")
        return

    for r in rows:
        cells = (
            r.run_id[:8],
            _trunc(r.timestamp, widths[1]),
            _trunc(r.project_id, widths[2]),
            _trunc(r.testbench_alias or r.testbench_id, widths[3]),
            _trunc(r.label or "", widths[4]),
            _trunc(r.note or "", widths[5]),
        )
        print(sep.join(c.ljust(w) for c, w in zip(cells, widths)))


def _trunc(s: str, width: int) -> str:
    if len(s) <= width:
        return s
    if width <= 1:
        return s[:width]
    return s[: width - 1] + "…"


def main(argv: Optional[list] = None) -> int:
    """Standalone ``python -m simkit.cli.list_runs`` entry."""
    parser = argparse.ArgumentParser(prog="pvt-list")
    sub = parser.add_subparsers(dest="cmd", required=True)
    add_subparser(sub)
    ns = parser.parse_args(["list", *(argv if argv is not None else sys.argv[1:])])
    return ns.func(ns)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
