"""``pvt label`` subcommand.

Sets or clears ``runs.label``. Non-null label promotes a run to a slice
(DECISIONS #11). ``--clear`` demotes back to a draft run.

Exit codes:
    0  success (including noop when --clear hits an already-null label)
    1  domain error (run not found, label conflict without --force)
    3  filesystem / DB IO error
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from simkit.db import bootstrap, connect
from simkit.errors import (
    LabelConflictError,
    RunNotFoundError,
    SimkitError,
)
from simkit.label import set_run_label
from simkit.project import PvtProjectError, load_pvtproject


def add_subparser(sub) -> None:
    p = sub.add_parser(
        "label",
        help="Set, overwrite, or clear runs.label (slice promotion).",
        description=(
            "Without --clear: set runs.label = <label>. Errors if a label "
            "is already present unless --force. With --clear: set "
            "runs.label = NULL (label arg ignored)."
        ),
    )
    p.add_argument("run_id", help="run_id of an already-ingested run.")
    p.add_argument(
        "label", nargs="?", default=None,
        help="New label text (required unless --clear is passed).",
    )
    p.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing non-null label.",
    )
    p.add_argument(
        "--clear", action="store_true",
        help="Clear the label (set runs.label = NULL).",
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
    if args.clear and args.label is not None:
        print(
            "pvt label: --clear takes no label argument",
            file=sys.stderr,
        )
        return 2
    if not args.clear and args.label is None:
        print(
            "pvt label: <label> is required (or pass --clear to clear)",
            file=sys.stderr,
        )
        return 2

    try:
        db_path = _resolve_db_path(args)
    except PvtProjectError as exc:
        print(f"pvt label: {exc}", file=sys.stderr)
        return 3

    if not db_path.is_file():
        print(
            f"pvt label: DB not found: {db_path} "
            "(run `pvt ingest` first)",
            file=sys.stderr,
        )
        return 3

    try:
        con = connect(db_path)
    except Exception as exc:  # pragma: no cover - duckdb wraps OSError
        print(f"pvt label: cannot open DB {db_path}: {exc}", file=sys.stderr)
        return 3
    try:
        bootstrap(con)
        try:
            res = set_run_label(
                con,
                run_id=args.run_id,
                label=None if args.clear else args.label,
                force=args.force,
            )
        except (RunNotFoundError, LabelConflictError) as exc:
            print(f"pvt label: {exc}", file=sys.stderr)
            return 1
        except SimkitError as exc:
            print(f"pvt label: {exc}", file=sys.stderr)
            return 1
    finally:
        con.close()

    if res.action == "set":
        print(f"pvt label: set run_id={res.run_id} label={res.current!r}")
    elif res.action == "overwritten":
        print(
            f"pvt label: overwrote run_id={res.run_id} "
            f"label={res.current!r} (was {res.previous!r})"
        )
    elif res.action == "cleared":
        print(
            f"pvt label: cleared run_id={res.run_id} "
            f"(was {res.previous!r})"
        )
    else:  # noop
        print(f"pvt label: run_id={res.run_id} already unlabelled (noop)")
    return 0


def main(argv: Optional[list] = None) -> int:
    """Standalone ``python -m simkit.cli.label`` entry."""
    parser = argparse.ArgumentParser(prog="pvt-label")
    sub = parser.add_subparsers(dest="cmd", required=True)
    add_subparser(sub)
    ns = parser.parse_args(["label", *(argv if argv is not None else sys.argv[1:])])
    return ns.func(ns)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
