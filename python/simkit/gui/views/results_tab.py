"""Results tab — right-panel content for spec Tier-1 cap #1 (View Results).

Layout (spec §6 ASCII + §11 review-header mandate B2):

    ┌─ ResultsTab ───────────────────────────────────────────────────┐
    │ <history>  <project>  <testbench>  <ts>  <milestone>  [Run]    │   ← header
    ├────────────────────────────────────────────────────────────────┤
    │ corner | test | output | value | status | spec | spec_status   │
    │ ...                                                              │   ← QTableView
    └────────────────────────────────────────────────────────────────┘

The header always carries the primary "Run this review" button (spec B2:
not buried inside a Run tab). Stage-2 wires this as a plain signal
``run_requested(review_path)`` — ``MainWindow`` is responsible for
routing the click to a ``QProcess`` ``pvt run`` invocation (spec §9).

Why no direct ``BridgeWorker`` call here:
``ResultsTab`` is a pure view; spec mandate (architecture-review review
of this file): "tabs never import bridge_worker". All side effects are
signal-emits → MainWindow.

Stage-2 deliberately leaves out:
* Baseline pin / Compare button (spec B3) → comes with Diff tab in §5.
* Failed-corner-only filter → can be added cheaply via the proxy model
  later; not in Tier-1 cap #1.
* "Set milestone…" right-click → comes with §15 milestone tagging.
"""

from __future__ import annotations

from typing import Optional

import duckdb

from PyQt5.QtCore import QSortFilterProxyModel, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from simkit.gui.results_model import ResultsModel, load_rows_for_run


# Default column widths for the results table. Picked to fit a typical
# 1200-px window without horizontal scrolling on common content; the
# user can drag any of them in-app. ``-1`` means "stretch the remaining
# space" (handled separately via the header's last-section-stretch).
_COL_WIDTHS: dict[str, int] = {
    "corner": 160,
    "test": 140,
    "output": 200,
    "value": 110,
    "status": 70,
    "spec": 180,
    "spec_status": 90,
}


class ResultsTab(QWidget):
    """Right-panel tab for viewing one run's results.

    Signals:
      * ``run_requested(review_path: str)`` — emitted when the user
        clicks the "Run this review" button. ``review_path`` is the
        absolute path on disk; ``MainWindow`` is responsible for the
        actual ``pvt run`` ``QProcess`` invocation.
      * ``compare_requested()`` — user clicked the "Compare to…" button.
        MainWindow already knows the current run (via the prior
        :meth:`set_run` call) so no payload is needed.
      * ``baseline_pinned(run_id_or_none)`` — user toggled the baseline
        pin. ``None`` means "unpin"; otherwise the str run_id that was
        pinned. Emitted from :meth:`set_baseline`.
    """

    run_requested = pyqtSignal(str)
    compare_requested = pyqtSignal()
    baseline_pinned = pyqtSignal(object)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._review_path: Optional[str] = None
        self._model: Optional[ResultsModel] = None
        self._current_run_id: Optional[str] = None
        self._baseline_run_id: Optional[str] = None

        # --- header (spec §11 / B2) -------------------------------------
        self.header = QFrame(self)
        self.header.setObjectName("resultsHeader")
        self.header.setFrameShape(QFrame.StyledPanel)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(8, 4, 8, 4)

        self.header_label = QLabel("(no run selected)", self.header)
        self.header_label.setObjectName("resultsHeaderLabel")
        self.header_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        header_layout.addWidget(self.header_label, stretch=1)

        # Baseline pin label — clickable to toggle off. Defaults to
        # "Baseline: —" (em dash, no pin) per spec §10.
        self.baseline_label = QLabel("Baseline: —", self.header)
        self.baseline_label.setObjectName("resultsBaselineLabel")
        self.baseline_label.setCursor(Qt.PointingHandCursor)
        self.baseline_label.setToolTip(
            "Click to unpin the baseline run (when one is pinned).",
        )
        # QLabel has no clicked signal; we intercept mousePressEvent on
        # the instance via a closure so the label stays a plain QLabel
        # and the cursor still flips to a hand.
        self.baseline_label.mousePressEvent = self._on_baseline_clicked  # type: ignore[assignment]
        header_layout.addWidget(self.baseline_label, stretch=0)

        # "Compare to…" — disabled until set_run() establishes a current run.
        self.compare_button = QPushButton("Compare to…", self.header)
        self.compare_button.setObjectName("compareToButton")
        self.compare_button.setEnabled(False)
        self.compare_button.clicked.connect(self.compare_requested.emit)
        header_layout.addWidget(self.compare_button, stretch=0)

        self.run_button = QPushButton("Run this review", self.header)
        self.run_button.setObjectName("runReviewButton")
        # Disabled until a review path is set — spec B2 wants the primary
        # action visible at all times, but it only makes sense once a
        # review is actually selected in the left tree.
        self.run_button.setEnabled(False)
        self.run_button.clicked.connect(self._on_run_clicked)
        header_layout.addWidget(self.run_button, stretch=0)

        # --- table (spec A3 mandate) ------------------------------------
        self.table = QTableView(self)
        self.table.setObjectName("resultsTable")
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)

        # Proxy in front of the model so sort/filter never copies the
        # underlying data (A3).
        self._proxy = QSortFilterProxyModel(self)
        self._proxy.setSortRole(Qt.DisplayRole)
        self.table.setModel(self._proxy)

        # --- assemble ---------------------------------------------------
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self.header)
        v.addWidget(self.table, stretch=1)

    # --- public surface --------------------------------------------------

    def set_run(
        self,
        run_id: str,
        con: duckdb.DuckDBPyConnection,
    ) -> None:
        """Reload the table for ``run_id``.

        Queries DuckDB for the row set, builds a fresh
        :class:`ResultsModel`, wires it through the proxy. The caller
        owns the connection's lifetime; we don't ``close()`` it.
        """
        rows = load_rows_for_run(con, run_id)
        # Build a fresh model — replacing rather than mutating keeps the
        # proxy/view wiring trivial and avoids stale-index pitfalls.
        self._model = ResultsModel(rows, parent=self)
        self._proxy.setSourceModel(self._model)
        self._apply_column_widths()
        # Track the current run + enable Compare; MainWindow reads
        # ``current_run_id()`` when it handles compare_requested.
        self._current_run_id = run_id
        self.compare_button.setEnabled(True)

    def set_header(
        self,
        history_name: str = "",
        project_id: str = "",
        testbench_id: str = "",
        timestamp: str = "",
        milestone: str = "",
    ) -> None:
        """Update the header summary text (spec Tier-1 cap #1 description).

        Empty strings are simply skipped — keeps the header compact when
        a field isn't known yet (e.g. milestone often blank).
        """
        parts: list[str] = []
        if history_name:
            parts.append(history_name)
        if project_id:
            parts.append(project_id)
        if testbench_id:
            parts.append(testbench_id)
        if timestamp:
            parts.append(timestamp)
        if milestone:
            # The ★ glyph here matches the left-tree milestone group
            # rendering described in spec §15.3.
            parts.append(f"★ {milestone}")
        text = "  ·  ".join(parts) if parts else "(no run selected)"
        self.header_label.setText(text)

    def set_review_path(self, path: Optional[str]) -> None:
        """Bind the "Run this review" button to ``path``.

        ``None`` or empty string disables the button (no review selected).
        """
        self._review_path = path or None
        self.run_button.setEnabled(self._review_path is not None)

    def set_baseline(self, run_id: Optional[str]) -> None:
        """Pin (or unpin, with ``None``) a baseline run for diff workflows.

        Updates the header label text and emits
        :pyattr:`baseline_pinned`. Idempotent — emitting on every call is
        intentional so MainWindow can re-sync state without a separate
        "is this a change?" check.
        """
        self._baseline_run_id = run_id
        if run_id is None:
            self.baseline_label.setText("Baseline: —")
        else:
            # First 8 chars matches the short_id convention used in the
            # picker + diff tab title.
            short = run_id[:8] if len(run_id) > 8 else run_id
            self.baseline_label.setText(f"Baseline: ★ {short}")
        self.baseline_pinned.emit(run_id)

    def current_run_id(self) -> Optional[str]:
        """Return the run_id last passed to :meth:`set_run`, or None."""
        return self._current_run_id

    def baseline_run_id(self) -> Optional[str]:
        """Return the currently pinned baseline run_id (or None)."""
        return self._baseline_run_id

    # --- internals -------------------------------------------------------

    def _apply_column_widths(self) -> None:
        """Push the default widths from ``_COL_WIDTHS`` onto the header."""
        if self._model is None:
            return
        for col_index, name in enumerate(self._model.COLUMNS):
            width = _COL_WIDTHS.get(name)
            if width is not None:
                self.table.setColumnWidth(col_index, width)

    def _on_run_clicked(self) -> None:
        """Slot: emit ``run_requested`` with the bound review path."""
        if self._review_path is None:
            # Defensive: button should be disabled, but emit nothing
            # rather than a bogus empty string if something races.
            return
        self.run_requested.emit(self._review_path)

    def _on_baseline_clicked(self, _event) -> None:
        """Slot: clicking the baseline label unpins (only when pinned)."""
        if self._baseline_run_id is not None:
            self.set_baseline(None)
