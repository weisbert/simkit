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
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSizePolicy,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from simkit.gui.results_model import (
    _PROBLEM_STATUSES,
    ResultsModel,
    load_rows_for_run,
)
from simkit.spec_eval import SpecParseError, parse_spec


# Syntax help shared by the Set-spec dialog. Mirrors the Measures-editor
# spec hint so the two spec-authoring entry points read consistently.
_SPEC_HINT_TEXT = (
    "Syntax: >= lower  ·  <= upper  ·  range lo hi  ·  "
    "maximize target  ·  minimize target  (SI suffixes k m u n p M G "
    "are supported)"
)


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


class _FailedRowProxy(QSortFilterProxyModel):
    """Sort/filter proxy that can hide all but the failing result rows (G-4).

    A row "fails" the same way :func:`results_model._is_fail_row` paints it
    red: a problem ``status`` (fail / eval_err / sim failures) or a
    ``spec_status`` in ``{fail, eval_err}``.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._only_failed = False

    def set_only_failed(self, on: bool) -> None:
        if on != self._only_failed:
            self._only_failed = on
            self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:
        if not self._only_failed:
            return True
        model = self.sourceModel()
        if model is None:
            return True
        cols = getattr(model, "COLUMNS", ())

        def cell(name: str) -> str:
            if name not in cols:
                return ""
            idx = model.index(source_row, cols.index(name))
            return model.data(idx, Qt.DisplayRole) or ""

        return (
            cell("status") in _PROBLEM_STATUSES
            or cell("spec_status") in ("fail", "eval_err")
        )


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
    # (output, spec) — user set a spec from the results table; an empty
    # spec string means "clear". MainWindow owns the DB + bundle writes.
    set_spec_requested = pyqtSignal(str, str)

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

        # Failed-only filter (G-4) — hide everything but failing rows so a
        # 240-corner sweep collapses to just the problems. Wired to the
        # proxy below, once it exists.
        self.failed_only_check = QCheckBox("Failed rows only", self.header)
        self.failed_only_check.setObjectName("failedOnlyCheck")
        header_layout.addWidget(self.failed_only_check, stretch=0)

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

        # --- zero-spec hint strip (G-1c) --------------------------------
        # When a run carries no specs at all, every row reads `no_spec`
        # and the user has no signal that auto pass/fail is even a thing.
        # This strip points them at the fix; hidden whenever any spec
        # exists (or no run is loaded).
        self.no_spec_hint = QLabel(
            "This run has no specs — right-click a row and choose "
            "'Set spec…', or add a spec to the output in the Measures "
            "tab, to get automatic pass/fail.",
            self,
        )
        self.no_spec_hint.setObjectName("noSpecHint")
        self.no_spec_hint.setWordWrap(True)
        self.no_spec_hint.setStyleSheet(
            "QLabel#noSpecHint { background: #fff3a3; "
            "border: 1px solid #d4b500; padding: 4px 8px; }"
        )
        self.no_spec_hint.setVisible(False)

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
        # underlying data (A3). The failed-row proxy adds the G-4 filter.
        self._proxy = _FailedRowProxy(self)
        self._proxy.setSortRole(Qt.DisplayRole)
        self.table.setModel(self._proxy)
        self.failed_only_check.toggled.connect(self._proxy.set_only_failed)

        # Right-click → "Set spec for this output…" (G-1b).
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(
            self._on_table_context_menu
        )

        # --- assemble ---------------------------------------------------
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self.header)
        v.addWidget(self.no_spec_hint)
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
        self._update_no_spec_hint()

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

    def set_review_path(
        self, path: Optional[str], *, runnable: bool = True,
    ) -> None:
        """Bind the "Run this review" button to ``path``.

        ``None`` or empty string disables the button (no review selected).
        ``runnable=False`` keeps the button disabled even with a valid
        path — used for reviews that fail to parse, where dispatching a
        ``pvt run`` would just fail downstream.
        """
        self._review_path = path or None
        self.run_button.setEnabled(self._review_path is not None and runnable)

    def show_review_summary(
        self,
        review_name: str,
        item_count: int,
        parse_error: Optional[str] = None,
    ) -> None:
        """Show a selected review's summary in the header.

        Selecting a review node picks a *review*, not a *run* — there is
        no result set to show. Make the header say so (and drop any
        stale run table) instead of leaving the previous run's text and
        rows, which looked like they belonged to the review.
        """
        if parse_error:
            self.header_label.setText(
                f"Review: {review_name}  ·  parse failed: {parse_error}"
            )
        else:
            self.header_label.setText(
                f"Review: {review_name}  ·  {item_count} items  ·  "
                f"select a run under History to view results, or click "
                f"Run this review"
            )
        self._model = None
        self._proxy.setSourceModel(None)
        self._current_run_id = None
        self.compare_button.setEnabled(False)
        self.no_spec_hint.setVisible(False)

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

    def _update_no_spec_hint(self) -> None:
        """Show the hint strip iff the run has rows but not one carries a spec."""
        rows = self._model.rows() if self._model is not None else []
        all_no_spec = bool(rows) and not any(_row_has_spec(r) for r in rows)
        self.no_spec_hint.setVisible(all_no_spec)

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

    def _on_table_context_menu(self, pos) -> None:
        """Right-click a results row → offer "Set spec for <output>…"."""
        if self._model is None:
            return
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        row = self._proxy.mapToSource(index).row()
        rows = self._model.rows()
        if not (0 <= row < len(rows)):
            return
        record = rows[row]
        output = record.get("output")
        if not output:
            return
        current_spec = record.get("spec") or ""
        menu = QMenu(self.table)
        act = menu.addAction(f"Set spec for '{output}'…")
        chosen = menu.exec_(self.table.viewport().mapToGlobal(pos))
        if chosen is not act:
            return
        dialog = SetSpecDialog(str(output), str(current_spec), parent=self)
        if dialog.exec_() == QDialog.Accepted:
            self.set_spec_requested.emit(str(output), dialog.spec_text())


def _row_has_spec(record: dict) -> bool:
    """True if a result row carries a non-empty spec string."""
    spec = record.get("spec")
    return isinstance(spec, str) and spec.strip() != ""


class SetSpecDialog(QDialog):
    """Prompt for a spec string to apply to one output (G-1b).

    Live-validates via :func:`simkit.spec_eval.parse_spec`; OK is disabled
    while the text is unparseable. An empty field is allowed and means
    "clear the spec" — the output reverts to ``no_spec``.
    """

    def __init__(
        self,
        output: str,
        current_spec: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Set spec — {output}")

        form = QFormLayout()
        self._edit = QLineEdit(current_spec or "")
        self._edit.setPlaceholderText(
            ">= 20    ·    <= 1.5m    ·    range 1 5    ·    maximize 30"
        )
        form.addRow(f"spec for {output}:", self._edit)
        self._hint = QLabel(_SPEC_HINT_TEXT)
        self._hint.setWordWrap(True)
        form.addRow("", self._hint)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self._buttons)

        self._edit.textChanged.connect(self._validate)
        self._validate()

    def _validate(self) -> None:
        text = self._edit.text().strip()
        ok = self._buttons.button(QDialogButtonBox.Ok)
        if not text:
            self._edit.setStyleSheet("")
            self._hint.setText(
                "Leave blank = clear the spec (the output reverts to no_spec)"
            )
            self._hint.setStyleSheet("color: #666;")
            ok.setEnabled(True)
            return
        try:
            parse_spec(text)
        except SpecParseError as exc:
            self._edit.setStyleSheet(
                "QLineEdit { border: 1px solid #c0392b; }"
            )
            self._hint.setText(f"spec failed to parse: {exc}")
            self._hint.setStyleSheet("color: #c0392b;")
            ok.setEnabled(False)
        else:
            self._edit.setStyleSheet("")
            self._hint.setText("✓ spec valid")
            self._hint.setStyleSheet("color: #2e7d32;")
            ok.setEnabled(True)

    def spec_text(self) -> str:
        """The entered spec, stripped. Empty string means "clear"."""
        return self._edit.text().strip()
