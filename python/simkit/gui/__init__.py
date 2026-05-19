"""Phase 4 GUI package — PyQt5 desktop app skeleton.

The first real user entry point for simkit. See ``docs/phase4_gui_spec.md``
for the binding design contract (§§2.1, 7, 8, 13, 17 in particular).

This package gates the PyQt5 import behind callable entry points so that
``python -c 'from simkit.gui import app'`` works on a machine without
PyQt5 installed — the failure happens later, inside ``main()``, with a
clear message instead of an obscure import trace.
"""

from __future__ import annotations
