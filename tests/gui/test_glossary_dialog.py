"""Tests for the G-7 glossary dialog."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "python"))

from PyQt5.QtWidgets import QApplication  # noqa: E402

from simkit.gui.views.glossary_dialog import (  # noqa: E402
    GLOSSARY,
    GlossaryDialog,
    glossary_html,
)

_QAPP = QApplication.instance() or QApplication(sys.argv)


def test_glossary_covers_the_core_vocabulary():
    terms = " ".join(t for t, _ in GLOSSARY)
    for needed in ("Module", "Bridge", "Session", "Review", "Union",
                   "Bundle", "Template", "Signal group", "Raw", "Sweep"):
        assert needed in terms, f"{needed!r} missing from the glossary"


def test_glossary_html_renders_every_term():
    html = glossary_html()
    for term, _ in GLOSSARY:
        assert term in html


def test_glossary_dialog_constructs_and_shows_terms():
    d = GlossaryDialog()
    body = d.browser.toPlainText()
    # "Review" misreads as a meeting — the definition must say otherwise.
    assert "Review" in body
    assert "not a meeting" in body
