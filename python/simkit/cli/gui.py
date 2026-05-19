"""``pvt gui`` subcommand (spec §17).

Thin shim — every meaningful flag is parsed by :func:`simkit.gui.app.main`,
which we hand the argv to. The PyQt5 import is gated inside ``main`` so
``pvt gui --help`` works on a machine without PyQt5 installed.

Exit codes:
    0  user closed the GUI cleanly
    4  PyQt5 not installed (printed install hint)
    other  propagated from Qt event loop
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional


def add_subparser(sub) -> None:
    """Register ``pvt gui`` under the top-level argparse dispatcher."""
    p = sub.add_parser(
        "gui",
        help="Launch the simkit desktop GUI.",
        description=(
            "Open the simkit PyQt5 GUI. By default restores the "
            "last-visited module from ~/.simkit/gui_app.json. "
            "Use --module to override; --safe-mode to skip restore."
        ),
    )
    p.add_argument(
        "--module", default=None,
        help="Open the GUI directly on this .pvtproject.",
    )
    p.add_argument(
        "--safe-mode", action="store_true",
        help="Skip restore of last-visited module + window geometry.",
    )
    p.set_defaults(func=run)


def run(args) -> int:
    """Dispatch into :func:`simkit.gui.app.main`.

    Translates the argparse namespace back into the small argv list that
    ``app.main`` knows how to parse. Keeping the parse logic in
    ``app.main`` lets it be tested independently of the CLI.
    """
    forwarded: list[str] = []
    if args.module:
        forwarded.extend(["--module", str(args.module)])
    if args.safe_mode:
        forwarded.append("--safe-mode")

    # Lazy import so `pvt gui --help` (handled by argparse before this
    # function runs) does NOT touch PyQt5.
    from simkit.gui.app import main as gui_main
    return gui_main(forwarded)


def main(argv: Optional[list] = None) -> int:  # pragma: no cover
    """Standalone ``python -m simkit.cli.gui`` entry."""
    parser = argparse.ArgumentParser(prog="pvt-gui")
    sub = parser.add_subparsers(dest="cmd", required=True)
    add_subparser(sub)
    ns = parser.parse_args(["gui", *(argv if argv is not None else sys.argv[1:])])
    return ns.func(ns)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
