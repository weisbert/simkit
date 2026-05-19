"""MainWindow scaffold (spec §13 layout, §6.1 zone descriptions).

Pure placeholders in Phase 4 §3 — every zone is an empty ``QWidget`` with
a clear ``objectName`` so later phases (§4 results, §5 corner editor,
§6 measure editor, etc.) can locate + populate it. No business logic
here; the wiring lives in ``app.py`` / ``BridgeWorker``.

Zones (per spec §6 ASCII diagram):
  * ``topBar``        — module selector dropdown + recent-5 + bridge dot
  * ``statusStrip``   — narrow cross-module 24h summary (B1)
  * ``leftTree``      — Reviews / Milestones / History tree
  * ``rightPanel``    — tabbed view (Results / Corners / Measures / ...)
  * ``bottomLog``     — collapsible log panel (default expanded)
  * ``statusDot``     — bridge heartbeat indicator inside ``topBar``

PyQt5 is imported at module load. Callers gate that via ``app.py`` —
this module is only imported once we're certain PyQt5 is available.
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QSplitter,
    QTextEdit,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from simkit.gui.bridge_worker import BridgeStatus


# Status-dot colours per spec §8.2. Plain hex; the visual treatment will
# be refined when the design picks an icon set (QtAwesome is locked in
# spec §2 but not exercised yet).
_DOT_COLORS = {
    BridgeStatus.GREEN: "#2ecc71",
    BridgeStatus.AMBER: "#f1c40f",
    BridgeStatus.RED: "#e74c3c",
}


class MainWindow(QMainWindow):
    """Top-level window. Placeholders only in Phase 4 §3."""

    DEFAULT_SIZE = (1200, 800)
    """Spec §6: single ~1200x800 default window."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("simkit")
        self.resize(*self.DEFAULT_SIZE)

        # --- top bar ----------------------------------------------------
        self.top_bar = QWidget(objectName="topBar")
        top_bar_layout = QHBoxLayout(self.top_bar)
        top_bar_layout.setContentsMargins(8, 4, 8, 4)

        self.module_selector = QWidget(objectName="moduleSelector")
        # Placeholder label until the real combo + recent-5 lands.
        ms_layout = QHBoxLayout(self.module_selector)
        ms_layout.setContentsMargins(0, 0, 0, 0)
        ms_layout.addWidget(QLabel("[Module: -]"))
        top_bar_layout.addWidget(self.module_selector)

        top_bar_layout.addStretch(1)

        self.status_dot = QLabel(objectName="statusDot")
        self.status_dot.setFixedSize(14, 14)
        self.status_dot.setToolTip("Bridge status")
        self.set_bridge_status(BridgeStatus.AMBER)
        top_bar_layout.addWidget(self.status_dot)

        # --- status strip (spec B1) -------------------------------------
        self.status_strip = QLabel(
            "Last 24h: -", objectName="statusStrip",
        )
        self.status_strip.setContentsMargins(8, 0, 8, 4)

        # --- left tree --------------------------------------------------
        self.left_tree = QTreeView(objectName="leftTree")
        self.left_tree.setHeaderHidden(True)
        self.left_tree.setMinimumWidth(180)

        # --- right panel ------------------------------------------------
        # Pure placeholder — spec §9-§12 turn this into a QTabWidget with
        # Results / Corners / Measures / Wizard tabs. Kept as a plain
        # widget here so we don't pre-commit to tab order before review.
        self.right_panel = QWidget(objectName="rightPanel")
        right_layout = QVBoxLayout(self.right_panel)
        right_layout.setContentsMargins(8, 8, 8, 8)
        right_layout.addWidget(
            QLabel("Right panel placeholder — populated by §4 (Results) onward.")
        )
        right_layout.addStretch(1)

        # --- bottom log -------------------------------------------------
        self.bottom_log = QTextEdit(objectName="bottomLog")
        self.bottom_log.setReadOnly(True)
        self.bottom_log.setFixedHeight(160)  # spec §6: 160px default
        self.bottom_log.setPlaceholderText(
            "Log output streams here when pvt run is active."
        )

        # --- assemble: splitters --------------------------------------
        # Horizontal splitter: leftTree | rightPanel
        h_splitter = QSplitter(Qt.Horizontal, objectName="mainHSplitter")
        h_splitter.addWidget(self.left_tree)
        h_splitter.addWidget(self.right_panel)
        h_splitter.setStretchFactor(0, 0)
        h_splitter.setStretchFactor(1, 1)
        h_splitter.setSizes([260, 940])  # spec §6.1: default 260 px left tree

        # Vertical container: topBar / statusStrip / h_splitter / bottomLog
        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self.top_bar)
        v.addWidget(self.status_strip)
        v.addWidget(h_splitter, stretch=1)
        v.addWidget(self.bottom_log)
        self.setCentralWidget(central)

    # --- public surface used by AppController ----------------------------

    def set_bridge_status(self, status: BridgeStatus) -> None:
        """Render the heartbeat dot in the top-right corner."""
        color = _DOT_COLORS.get(status, "#888")
        self.status_dot.setStyleSheet(
            f"background-color: {color}; border-radius: 7px;"
        )
        self.status_dot.setToolTip(f"Bridge: {status.value}")

    def append_log(self, line: str) -> None:
        """Append one line to the bottom log panel."""
        self.bottom_log.append(line)

    def set_status_strip(self, text: str) -> None:
        """Update the cross-module status strip text (spec B1)."""
        self.status_strip.setText(text)
