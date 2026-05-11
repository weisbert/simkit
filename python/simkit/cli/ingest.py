"""``pvt ingest`` subcommand.

Walks a path, ingests every ``run.json`` it finds, prints a one-line
summary per run, and returns a process exit code.

Exit codes:
    0  full success
    1  any IngestError (incl. validation, schema_version, duplicate)
    2  argparse / usage error (handled by argparse itself)
    3  filesystem / DB IO error (file-not-found, can't open DB)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from simkit.db import bootstrap, connect
from simkit.errors import (
    IngestError,
    MissingDumpError,
)
from simkit.ingest import ingest_dump_dir, ingest_run_json
from simkit.project import (
    PvtProjectError,
    load_pvtproject,
)


def add_subparser(sub) -> None:
    p = sub.add_parser(
        "ingest",
        help="Load collector dump JSON into DuckDB.",
        description=(
            "Walk <path> for run.json files and load each into "
            "<dbRoot>/simkit.duckdb."
        ),
    )
    p.add_argument(
        "path", type=Path,
        help=(
            "Either a dbRoot dir (containing runs/<run_id>/run.json), "
            "a single run dir (containing run.json), or a run.json file."
        ),
    )
    p.add_argument(
        "--db", type=Path, default=None,
        help=(
            "Override DB path. Default: <dbRoot>/simkit.duckdb derived "
            "from the .pvtproject discovered via PVT_PROJECT or cwd-walker."
        ),
    )
    p.add_argument(
        "--force", action="store_true",
        help=(
            "Replace existing rows for any run_id already in the DB. "
            "Destroys post-hoc labels and attached artifacts for those "
            "run_ids."
        ),
    )
    p.add_argument(
        "--no-validate", action="store_true",
        help="Skip the inline schema-invariant validator (DECISIONS #17).",
    )
    p.add_argument(
        "--continue-on-error", action="store_true",
        help="Skip malformed dumps instead of aborting the whole walk.",
    )
    p.add_argument(
        "-v", "--verbose", action="count", default=0,
        help="Increase logging verbosity.",
    )
    p.set_defaults(func=run)


def _resolve_db_path(args) -> Path:
    if args.db is not None:
        return Path(args.db).expanduser().resolve()
    proj = load_pvtproject()
    return Path(proj.db_root) / "simkit.duckdb"


def run(args) -> int:
    path = Path(args.path).expanduser().resolve()

    try:
        db_path = _resolve_db_path(args)
    except PvtProjectError as exc:
        print(f"pvt ingest: {exc}", file=sys.stderr)
        return 3

    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        con = connect(db_path)
    except Exception as exc:  # pragma: no cover - duckdb wraps OSError
        print(f"pvt ingest: cannot open DB {db_path}: {exc}", file=sys.stderr)
        return 3
    try:
        bootstrap(con)
        on_conflict = "replace" if args.force else "error"
        validate = not args.no_validate

        try:
            if path.is_file() and path.name == "run.json":
                results = [ingest_run_json(
                    con, path,
                    on_conflict=on_conflict,
                    validate=validate,
                )]
            elif path.is_dir():
                results = ingest_dump_dir(
                    con, path,
                    on_conflict=on_conflict,
                    validate=validate,
                    continue_on_error=args.continue_on_error,
                )
            else:
                print(
                    f"pvt ingest: not a run.json or directory: {path}",
                    file=sys.stderr,
                )
                return 3
        except MissingDumpError as exc:
            print(f"pvt ingest: {exc}", file=sys.stderr)
            return 3
        except IngestError as exc:
            print(f"pvt ingest: {exc}", file=sys.stderr)
            return 1
    finally:
        con.close()

    if not results:
        print("pvt ingest: no run.json files found.")
        return 0

    print(f"pvt ingest: {len(results)} run(s) processed:")
    for r in results:
        print(
            f"  [{r.action:8}] run_id={r.run_id} "
            f"results={r.n_results} artifacts={r.n_artifacts} "
            f"warnings={r.n_warnings} source={r.source_path}"
        )
    return 0


def main(argv: Optional[list] = None) -> int:
    """Standalone ``python -m simkit.cli.ingest`` entry."""
    parser = argparse.ArgumentParser(prog="pvt-ingest")
    sub = parser.add_subparsers(dest="cmd", required=True)
    add_subparser(sub)
    ns = parser.parse_args(["ingest", *(argv if argv is not None else sys.argv[1:])])
    return ns.func(ns)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
