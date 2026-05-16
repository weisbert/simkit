"""``pvt`` argparse dispatcher.

Wires the per-subcommand modules in this package into a single CLI. Each
subcommand registers itself via ``add_subparser(subparsers)`` and sets a
``func`` default that takes the parsed ``args`` and returns an exit code.

Run as ``python -m simkit.cli`` or via the ``pvt`` console script.
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import List, Optional

from simkit.cli import attach as attach_cmd
from simkit.cli import corners as corners_cmd
from simkit.cli import diff as diff_cmd
from simkit.cli import ingest as ingest_cmd
from simkit.cli import label as label_cmd
from simkit.cli import list_runs as list_cmd
from simkit.cli import measure as measure_cmd
from simkit.cli import run as run_cmd
from simkit.cli import validate as validate_cmd


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pvt", description="simkit data-layer CLI (Phase 1).",
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true",
        help="Suppress informational logging.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    ingest_cmd.add_subparser(sub)
    validate_cmd.add_subparser(sub)
    attach_cmd.add_subparser(sub)
    label_cmd.add_subparser(sub)
    list_cmd.add_subparser(sub)
    diff_cmd.add_subparser(sub)
    corners_cmd.add_subparser(sub)
    measure_cmd.add_subparser(sub)
    run_cmd.add_subparser(sub)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = (sys.argv[1:] if argv is None else list(argv))
    parser = build_parser()
    ns = parser.parse_args(args)

    # Configure root logging once. Tests that want to capture the
    # `simkit.ingest` logger should add their own handler before invoking
    # main(); we use basicConfig only if no handler exists yet.
    if not logging.getLogger().handlers:
        logging.basicConfig(
            format="%(levelname)s %(name)s: %(message)s",
            level=logging.WARNING if ns.quiet else logging.INFO,
        )

    return ns.func(ns)


if __name__ == "__main__":  # pragma: no cover - module CLI
    sys.exit(main())
