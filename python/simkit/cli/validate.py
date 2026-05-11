"""``pvt validate`` subcommand.

Audits a run dump against the I1–I24 / W1, W2 invariants. Two input
modes:

* file mode (default): ``pvt validate <path-to-run.json>``
* DB mode: ``pvt validate --from-db <run_id> [--db PATH]`` — rebuild
  the JSON dump shape from rows already in DuckDB, then validate.

Exit codes:
    0  clean — no violations
    1  warnings only
    2  any error-severity violation
    3  IO error / file not found / run_id not in DB
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from simkit.db import bootstrap, connect
from simkit.errors import RunNotFoundError
from simkit.from_db import load_dump_from_db
from simkit.project import PvtProjectError, load_pvtproject
from simkit.validate import (
    _format_violations,
    validate_dump,
    validate_dump_file,
)


def add_subparser(sub) -> None:
    p = sub.add_parser(
        "validate",
        help="Audit a run.json against simkit's schema invariants.",
        description=(
            "Run the I1-I24 / W1, W2 invariant checks. By default reads "
            "a run.json file. With --from-db, loads the run by run_id "
            "from <dbRoot>/simkit.duckdb. Exit: 0 clean, 1 warnings, "
            "2 errors, 3 IO."
        ),
    )
    p.add_argument(
        "target", nargs="?", default=None,
        help=(
            "Path to a run.json file (default mode), or a run_id when "
            "--from-db is set."
        ),
    )
    p.add_argument(
        "--from-db", action="store_true",
        help=(
            "Treat <target> as a run_id and load the dump from the DB. "
            "DB path resolved via --db, PVT_PROJECT, or the cwd-walker."
        ),
    )
    p.add_argument(
        "--db", type=Path, default=None,
        help=(
            "Override DB path for --from-db. Default: "
            "<dbRoot>/simkit.duckdb."
        ),
    )
    p.add_argument(
        "-v", "--verbose", action="count", default=0,
        help="Increase output verbosity (currently a no-op).",
    )
    p.set_defaults(func=run)


def _resolve_db_path(args) -> Path:
    if args.db is not None:
        return Path(args.db).expanduser().resolve()
    proj = load_pvtproject()
    return Path(proj.db_root) / "simkit.duckdb"


def run(args) -> int:
    if args.target is None:
        print(
            "pvt validate: a target is required "
            "(<run.json> in file mode, <run_id> with --from-db).",
            file=sys.stderr,
        )
        return 2

    if args.from_db:
        return _run_from_db(args)
    return _run_from_file(args)


def _run_from_file(args) -> int:
    path = Path(args.target).expanduser().resolve()
    if not path.is_file():
        print(f"pvt validate: not a file: {path}", file=sys.stderr)
        return 3
    violations = validate_dump_file(path)
    print(_format_violations(violations))
    return _exit_code_for(violations)


def _run_from_db(args) -> int:
    run_id = args.target
    try:
        db_path = _resolve_db_path(args)
    except PvtProjectError as exc:
        print(f"pvt validate: {exc}", file=sys.stderr)
        return 3

    if not db_path.is_file():
        print(
            f"pvt validate: DB not found: {db_path} "
            "(run `pvt ingest` first)",
            file=sys.stderr,
        )
        return 3

    try:
        con = connect(db_path, read_only=True)
    except Exception as exc:  # pragma: no cover - duckdb wraps OSError
        print(
            f"pvt validate: cannot open DB {db_path}: {exc}",
            file=sys.stderr,
        )
        return 3
    try:
        try:
            dump = load_dump_from_db(con, run_id)
        except RunNotFoundError as exc:
            print(f"pvt validate: {exc}", file=sys.stderr)
            return 3
    finally:
        con.close()

    violations = validate_dump(dump)
    print(_format_violations(violations))
    return _exit_code_for(violations)


def _exit_code_for(violations) -> int:
    if any(v.severity == "error" for v in violations):
        return 2
    if violations:
        return 1
    return 0


def main(argv: Optional[list] = None) -> int:
    """Standalone ``python -m simkit.cli.validate`` entry."""
    parser = argparse.ArgumentParser(prog="pvt-validate")
    sub = parser.add_subparsers(dest="cmd", required=True)
    add_subparser(sub)
    ns = parser.parse_args(["validate", *(argv if argv is not None else sys.argv[1:])])
    return ns.func(ns)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
