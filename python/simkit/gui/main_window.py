"""MainWindow scaffold (spec §13 layout, §6.1 zone descriptions).

Phase 4 §3 shipped pure placeholders; Stage 2 (§4 / §7 / §8) fills the
right panel with three live tabs (Results / Corners / Measures) backed
by the views in ``simkit.gui.views``. The BridgeWorker routing for the
tabs' outbound signals is **deferred** to Stage 3 — for now the signals
are wired only to the bottom log panel for visibility, since the GUI
has no module-loading / project-context plumbing yet (without that,
``pvt_corners_pull`` etc. have nothing to call against).

Zones (per spec §6 ASCII diagram):
  * ``topBar``        — module selector dropdown + recent-5 + bridge dot
  * ``statusStrip``   — narrow cross-module 24h summary (B1)
  * ``leftTree``      — Reviews / Milestones / History tree
  * ``rightPanel``    — ``QTabWidget`` hosting Results / Corners / Measures
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
    QTabWidget,
    QTextEdit,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from simkit.gui.bridge_worker import BridgeStatus
from simkit.gui.views.corners_editor import CornersEditor
from simkit.gui.views.measures_editor import MeasuresEditor
from simkit.gui.views.results_tab import ResultsTab


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
        # Stage 2 (§4 / §7 / §8): QTabWidget with three live tabs. Tab
        # order = Tier-1 capability order from spec §4 (view first, then
        # edit corners, then edit measures). Diff / Wizard / Run-progress
        # tabs are Stage 3+; their slots open here on-demand.
        self.right_panel = QTabWidget(objectName="rightPanel")
        self.right_panel.setDocumentMode(True)

        self.results_tab = ResultsTab()
        self.corners_editor = CornersEditor()
        self.measures_editor = MeasuresEditor()

        self.right_panel.addTab(self.results_tab, "Results")
        self.right_panel.addTab(self.corners_editor, "Corners")
        self.right_panel.addTab(self.measures_editor, "Measures")

        # Stage 2 wiring: every outbound signal lands in the bottom log
        # for visibility. Stage 3 will replace these log-only handlers
        # with BridgeWorker.queue_op calls once module loading + project
        # context are in place. The signal contracts (names, signatures)
        # are pinned now so Stage 3 just swaps the slot bodies.
        self.results_tab.run_requested.connect(self._on_run_requested)
        self.corners_editor.pull_requested.connect(self._on_corners_pull_requested)
        self.corners_editor.push_requested.connect(self._on_corners_push_requested)
        self.corners_editor.show_diff.connect(self._on_corners_show_diff)
        self.corners_editor.pull_overrides_sidecar.connect(
            self._on_corners_pull_overrides_sidecar
        )
        self.corners_editor.keep_sidecar.connect(self._on_corners_keep_sidecar)
        self.measures_editor.apply_requested.connect(self._on_measures_apply_requested)

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

    # --- Stage 2 right-panel signal handlers (log-only stubs) -----------
    # Stage 3 swaps these for real BridgeWorker.queue_op calls + QProcess
    # subprocess dispatch. The signatures match the views' signal
    # contracts so the swap is local to this file.

    def _on_run_requested(self, review_path: str) -> None:
        self.append_log(f"[run] review_path={review_path} (Stage 3 will dispatch QProcess pvt run)")

    def _on_corners_pull_requested(self) -> None:
        self.append_log("[corners] pull requested (Stage 3 will call BridgeWorker pvt_corners_pull)")

    def _on_corners_push_requested(self, payload: object) -> None:
        row_count = len(payload) if isinstance(payload, list) else "?"
        self.append_log(
            f"[corners] push requested ({row_count} rows) "
            "(Stage 3 will call BridgeWorker pvt_corners_push --replace)"
        )

    def _on_corners_show_diff(self) -> None:
        self.append_log("[corners] show-diff requested (Stage 3 will open diff dialog)")

    def _on_corners_pull_overrides_sidecar(self) -> None:
        self.append_log("[corners] pull-overrides-sidecar requested (Stage 3 will pull + replace local)")

    def _on_corners_keep_sidecar(self) -> None:
        self.append_log("[corners] keep-sidecar requested (Stage 3 will dismiss divergence strip)")

    def _on_measures_apply_requested(self, rendered_rows: object) -> None:
        row_count = len(rendered_rows) if isinstance(rendered_rows, list) else "?"
        self.append_log(
            f"[measures] apply requested ({row_count} rendered rows) "
            "(Stage 3 will call BridgeWorker pvt_measure_apply)"
        )
