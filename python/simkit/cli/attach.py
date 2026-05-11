"""``pvt attach`` subcommand.

Copies a file into ``<dbRoot>/runs/<run_id>/artifacts/`` and inserts a
``source='manual'`` row into the ``artifacts`` table.

Exit codes:
    0  success
    1  domain error (run not found, dup artifact, invalid type)
    3  filesystem / DB IO error (file not found, can't open DB)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from simkit.attach import attach_artifact
from simkit.db import bootstrap, connect
from simkit.errors import (
    DuplicateArtifactError,
    InvalidArtifactTypeError,
    MissingDumpError,
    RunNotFoundError,
    SimkitError,
)
from simkit.project import PvtProjectError, load_pvtproject
from simkit.validate import VALID_ARTIFACT_TYPES


def add_subparser(sub) -> None:
    p = sub.add_parser(
        "attach",
        help="Post-hoc: attach a file to an existing run.",
        description=(
            "Copy <file> into <dbRoot>/runs/<run_id>/artifacts/ and record "
            "an artifacts row with source='manual'."
        ),
    )
    p.add_argument("run_id", help="run_id of an already-ingested run.")
    p.add_argument(
        "file", type=Path,
        help="Path to the source file to attach.",
    )
    p.add_argument(
        "--type", dest="artifact_type", required=True,
        choices=sorted(VALID_ARTIFACT_TYPES),
        help="Artifact type (closed enum from docs/schema.md §2.3).",
    )
    p.add_argument(
        "--desc", dest="description", default=None,
        help="Short human-readable note stored with the artifact row.",
    )
    p.add_argument(
        "--as", dest="dest_name", default=None,
        help=(
            "Override the destination filename (bare filename, no path). "
            "Useful when the source basename would collide."
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
        print(f"pvt attach: {exc}", file=sys.stderr)
        return 3

    if not db_path.is_file():
        print(
            f"pvt attach: DB not found: {db_path} "
            "(run `pvt ingest` first)",
            file=sys.stderr,
        )
        return 3

    runs_root = db_path.parent / "runs"

    try:
        con = connect(db_path)
    except Exception as exc:  # pragma: no cover - duckdb wraps OSError
        print(f"pvt attach: cannot open DB {db_path}: {exc}", file=sys.stderr)
        return 3
    try:
        bootstrap(con)
        try:
            res = attach_artifact(
                con,
                run_id=args.run_id,
                src_path=Path(args.file),
                artifact_type=args.artifact_type,
                runs_root=runs_root,
                description=args.description,
                dest_name=args.dest_name,
            )
        except MissingDumpError as exc:
            print(f"pvt attach: {exc}", file=sys.stderr)
            return 3
        except (
            RunNotFoundError, DuplicateArtifactError,
            InvalidArtifactTypeError,
        ) as exc:
            print(f"pvt attach: {exc}", file=sys.stderr)
            return 1
        except SimkitError as exc:
            print(f"pvt attach: {exc}", file=sys.stderr)
            return 1
    finally:
        con.close()

    print(
        f"pvt attach: attached {res.relative_path} to run_id={res.run_id} "
        f"(type={res.artifact_type})"
    )
    return 0


def main(argv: Optional[list] = None) -> int:
    """Standalone ``python -m simkit.cli.attach`` entry."""
    parser = argparse.ArgumentParser(prog="pvt-attach")
    sub = parser.add_subparsers(dest="cmd", required=True)
    add_subparser(sub)
    ns = parser.parse_args(["attach", *(argv if argv is not None else sys.argv[1:])])
    return ns.func(ns)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
