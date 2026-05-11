"""``pvt`` command-line entry points.

Subcommands live one-per-file under this package. ``__main__`` wires them
into a single argparse dispatcher; ``python -m simkit.cli`` and the
``pvt`` console script both route through ``__main__.main``.
"""

from __future__ import annotations
