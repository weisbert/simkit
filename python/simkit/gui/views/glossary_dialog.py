"""Glossary dialog (G-7) — explains simkit's vocabulary for new users.

The backtest found that terms like *review*, *union*, *bundle* and
*session* have no in-app explanation; "review" especially misreads as a
meeting. This dialog is reachable from Help ▸ Glossary and the same entries
back the per-widget tooltips.
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)


# (term, one-line definition). Order is roughly "outermost concept first".
GLOSSARY: tuple[tuple[str, str], ...] = (
    ("Module",
     "A .pvtproject workspace, one per circuit under test. Every review / "
     "union / bundle / run history in simkit belongs to a module."),
    ("Bridge",
     "The SKILL communication channel between simkit and Cadence Virtuoso. "
     "The status dot at the top shows whether it is connected; when down, "
     "Pull / Run / Apply are unavailable."),
    ("Session",
     "The name of an open Maestro simulation window (e.g. fnxSession0). "
     "Every Pull / Run / Apply must name which session to act on."),
    ("Review (a run set)",
     "A .review.json that bundles several items (test + corner group + "
     "measurement bundle) into one repeatable batch simulation. Note: it "
     "is not a meeting — it is 'a set of things to run'."),
    ("Union (corner group / PVT grid)",
     "A .union.json listing the process / voltage / temperature corners "
     "to sweep."),
    ("Bundle (measurement bundle)",
     "A .measure.json that defines which output quantities to extract "
     "from simulation results."),
    ("Template (measurement template)",
     "A parameterised measurement definition; a bundle reuses one "
     "template by filling in parameters."),
    ("Signal group",
     "A set of named signals. A template uses $SIG as a placeholder, "
     "which the signal group expands into one row per signal."),
    ("Raw (raw-expression entry)",
     "A bundle entry written as a direct OCEAN / calculator expression, "
     "bypassing templates."),
    ("Sweep (sweep entry)",
     "A bundle entry that takes several values for one parameter, "
     "expanding into several outputs (e.g. PN_1M / PN_10M / PN_100M)."),
)


def glossary_html() -> str:
    """Render :data:`GLOSSARY` as a definition-list HTML fragment."""
    rows = []
    for term, definition in GLOSSARY:
        rows.append(
            f"<p><b>{term}</b><br>"
            f"<span style='color:#444'>{definition}</span></p>"
        )
    return "\n".join(rows)


class GlossaryDialog(QDialog):
    """Read-only dialog listing simkit's core vocabulary."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("simkit — Glossary")
        self.setMinimumSize(560, 480)

        self.browser = QTextBrowser(self)
        self.browser.setObjectName("glossaryBrowser")
        self.browser.setOpenExternalLinks(False)
        self.browser.setHtml(glossary_html())

        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(self.browser, stretch=1)
        layout.addWidget(buttons)
