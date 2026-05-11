"""``pvt validate`` subcommand.

Audits a run.json (or, in a future extension, a row set already in the DB)
against the I1–I24 / W1, W2 invariants. Pure-JSON for now; the
``--from-db`` seam is reserved for §5 work.

Exit codes:
    0  clean — no violations
    1  warnings only
    2  any error-severity violation
    3  IO error / file not found
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from simkit.validate import (
    Violation,
    _format_violations,
    validate_dump_file,
)


def add_subparser(sub) -> None:
    p = sub.add_parser(
        "validate",
        help="Audit a run.json against simkit's schema invariants.",
        description=(
            "Run the I1-I24 / W1, W2 invariant checks against a single "
            "run.json file. Returns 0 (clean), 1 (warnings only), or "
            "2 (any error)."
        ),
    )
    p.add_argument(
        "path", type=Path,
        help="Path to a run.json file.",
    )
    # Reserved for §5; accepted but not yet implemented.
    p.add_argument(
        "--from-db", type=Path, default=None,
        help=argparse.SUPPRESS,
    )
    p.add_argument(
        "--run-id", default=None,
        help=argparse.SUPPRESS,
    )
    p.add_argument(
        "-v", "--verbose", action="count", default=0,
        help="Increase output verbosity (currently a no-op).",
    )
    p.set_defaults(func=run)


def run(args) -> int:
    if args.from_db is not None or args.run_id is not None:
        print(
            "pvt validate: --from-db / --run-id are reserved for §5 and "
            "not yet implemented.",
            file=sys.stderr,
        )
        return 3
    path = Path(args.path).expanduser().resolve()
    if not path.is_file():
        print(f"pvt validate: not a file: {path}", file=sys.stderr)
        return 3
    violations = validate_dump_file(path)
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
