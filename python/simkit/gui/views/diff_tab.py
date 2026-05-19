"""DiffTab — right-panel tab visualising one :class:`DiffResult`.

Three sub-tabs (spec §10):

  * Spec delta:   QTableView on DiffResultsModel + filter combo
  * Netlist delta: monospace unified-diff text
  * Spec-string delta: small table of (test, output, spec_a, spec_b)
    rows where the spec string itself changed between the two slices

The parent ``QTabWidget`` (in MainWindow) owns the tab-close X; the
``closed`` signal here is for an in-tab Close button to let the user
dismiss the diff from inside the diff view itself.
"""

from __future__ import annotations

from typing import List, Optional

from PyQt5.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    pyqtSignal,
)
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from simkit.diff import DiffResult, DiffRow
from simkit.gui.diff_model import DiffResultsModel


# Filter combo entries — index 0 is "show everything", which is the
# default. The strings are public; tests pin them.
_FILTER_ALL = "All rows"
_FILTER_CHANGED = "Show only changed"
_FILTER_VERDICT = "Show only verdict-flipped"


class _ChangedRowsProxy(QSortFilterProxyModel):
    """Proxy that defers row visibility to :class:`DiffResultsModel`.

    The model owns the per-row predicates because the DiffRow object
    knows the full delta vocabulary (kind / sentinel / spec_status); the
    proxy stays a thin dispatcher.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._mode = _FILTER_ALL

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        self.invalidateFilter()

    def mode(self) -> str:
        return self._mode

    def filterAcceptsRow(
        self, source_row: int, source_parent: QModelIndex,
    ) -> bool:
        model = self.sourceModel()
        if model is None:
            return True
        if self._mode == _FILTER_ALL:
            return True
        if self._mode == _FILTER_CHANGED:
            return bool(model.changed_only_filter(source_row))
        if self._mode == _FILTER_VERDICT:
            return bool(model.verdict_flipped_filter(source_row))
        # Unknown mode — fail open rather than hide everything silently.
        return True


# Spec-string sub-tab columns. Distinct from DiffResultsModel.COLUMNS
# because this tab focuses on string text, not numeric deltas.
_SPEC_STRING_COLUMNS: tuple[str, ...] = (
    "test", "output", "spec_a", "spec_b",
)


class _SpecStringModel(QAbstractTableModel):
    """Tiny table model for the spec-string sub-tab.

    Inlined rather than its own module because it has zero reuse —
    only DiffTab consumes it, and externalising would be over-design.
    """

    COLUMNS: tuple[str, ...] = _SPEC_STRING_COLUMNS

    def __init__(
        self,
        rows: List[DiffRow],
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._rows = list(rows)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal and 0 <= section < len(self.COLUMNS):
            return self.COLUMNS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if role != Qt.DisplayRole or not index.isValid():
            return None
        r, c = index.row(), index.column()
        if not (0 <= r < len(self._rows) and 0 <= c < len(self.COLUMNS)):
            return None
        v = getattr(self._rows[r], self.COLUMNS[c], None)
        return "—" if v is None else str(v)


class DiffTab(QWidget):
    """Right-panel tab visualising a :class:`DiffResult`."""

    closed = pyqtSignal()

    def __init__(
        self,
        diff_result: DiffResult,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._diff_result = diff_result

        # --- header ----------------------------------------------------
        self.header = QFrame(self)
        self.header.setObjectName("diffHeader")
        self.header.setFrameShape(QFrame.StyledPanel)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(8, 4, 8, 4)

        short_a = _short(diff_result.slice_a_run_id)
        short_b = _short(diff_result.slice_b_run_id)
        self._title = f"Diff: {short_a} vs {short_b}"
        self.header_label = QLabel(self._title, self.header)
        self.header_label.setObjectName("diffHeaderLabel")
        self.header_label.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Preferred,
        )
        header_layout.addWidget(self.header_label, stretch=1)

        self.close_button = QPushButton("Close", self.header)
        self.close_button.setObjectName("diffCloseButton")
        self.close_button.clicked.connect(self.closed.emit)
        header_layout.addWidget(self.close_button, stretch=0)

        # --- tabs ------------------------------------------------------
        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("diffSubTabs")
        self.tabs.addTab(self._build_spec_delta_tab(), "Spec delta")
        self.tabs.addTab(self._build_netlist_tab(), "Netlist delta")
        self.tabs.addTab(self._build_spec_string_tab(), "Spec-string delta")

        # --- assemble --------------------------------------------------
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self.header)
        v.addWidget(self.tabs, stretch=1)

    # --- public ---------------------------------------------------------

    @property
    def title(self) -> str:
        """Tab title — used by MainWindow when inserting into QTabWidget."""
        return self._title

    @property
    def diff_result(self) -> DiffResult:
        return self._diff_result

    # --- sub-tab builders ----------------------------------------------

    def _build_spec_delta_tab(self) -> QWidget:
        page = QWidget(self.tabs)
        v = QVBoxLayout(page)
        v.setContentsMargins(8, 8, 8, 8)

        # Filter combo row.
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        self.filter_combo = QComboBox(page)
        self.filter_combo.setObjectName("diffFilterCombo")
        self.filter_combo.addItems([
            _FILTER_ALL, _FILTER_CHANGED, _FILTER_VERDICT,
        ])
        controls.addWidget(QLabel("Filter:", page))
        controls.addWidget(self.filter_combo)
        controls.addStretch(1)
        v.addLayout(controls)

        # Model + proxy + view.
        self.results_model = DiffResultsModel(
            list(self._diff_result.rows), parent=page,
        )
        self.results_proxy = _ChangedRowsProxy(page)
        self.results_proxy.setSourceModel(self.results_model)

        self.results_view = QTableView(page)
        self.results_view.setObjectName("diffResultsTable")
        self.results_view.setModel(self.results_proxy)
        self.results_view.setSortingEnabled(True)
        self.results_view.setAlternatingRowColors(True)
        self.results_view.setSelectionBehavior(QTableView.SelectRows)
        self.results_view.setSelectionMode(QTableView.SingleSelection)
        self.results_view.verticalHeader().setVisible(False)
        self.results_view.horizontalHeader().setStretchLastSection(True)
        self.results_view.horizontalHeader().setSectionResizeMode(
            QHeaderView.Interactive,
        )
        v.addWidget(self.results_view, stretch=1)

        self.filter_combo.currentTextChanged.connect(self._on_filter_changed)

        return page

    def _build_netlist_tab(self) -> QWidget:
        page = QWidget(self.tabs)
        v = QVBoxLayout(page)
        v.setContentsMargins(8, 8, 8, 8)

        netlist = self._diff_result.netlist
        self.netlist_note_label = QLabel(page)
        self.netlist_note_label.setObjectName("diffNetlistNote")
        self.netlist_note_label.setWordWrap(True)
        self.netlist_note_label.setStyleSheet("color: #888; font-style: italic;")
        if netlist.note:
            self.netlist_note_label.setText(netlist.note)
            self.netlist_note_label.show()
        else:
            self.netlist_note_label.hide()
        v.addWidget(self.netlist_note_label)

        self.netlist_view = QPlainTextEdit(page)
        self.netlist_view.setObjectName("diffNetlistView")
        self.netlist_view.setReadOnly(True)
        mono = QFont("Monospace")
        mono.setStyleHint(QFont.TypeWriter)
        self.netlist_view.setFont(mono)

        if netlist.diff_text is None:
            self.netlist_view.setPlainText("")
        elif netlist.diff_text == "":
            # Empty unified-diff string means "identical files" — be
            # explicit so the user doesn't think the view failed to load.
            self.netlist_view.setPlainText("(netlists are identical)")
        else:
            self.netlist_view.setPlainText(netlist.diff_text)
        v.addWidget(self.netlist_view, stretch=1)
        return page

    def _build_spec_string_tab(self) -> QWidget:
        page = QWidget(self.tabs)
        v = QVBoxLayout(page)
        v.setContentsMargins(8, 8, 8, 8)

        changed = [r for r in self._diff_result.rows if r.spec_a != r.spec_b]
        if not changed:
            label = QLabel("No spec-string changes.", page)
            label.setObjectName("diffSpecStringEmpty")
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet("color: #888;")
            v.addWidget(label, stretch=1)
            self.spec_string_model = _SpecStringModel([], parent=page)
            self.spec_string_view = None
            return page

        self.spec_string_model = _SpecStringModel(changed, parent=page)
        self.spec_string_view = QTableView(page)
        self.spec_string_view.setObjectName("diffSpecStringTable")
        self.spec_string_view.setModel(self.spec_string_model)
        self.spec_string_view.setAlternatingRowColors(True)
        self.spec_string_view.setSelectionBehavior(QTableView.SelectRows)
        self.spec_string_view.verticalHeader().setVisible(False)
        self.spec_string_view.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self.spec_string_view, stretch=1)
        return page

    # --- slots ----------------------------------------------------------

    def _on_filter_changed(self, text: str) -> None:
        self.results_proxy.set_mode(text)


def _short(run_id: str) -> str:
    """Short-form display of a run_id (first 8 chars)."""
    if not run_id:
        return "?"
    return run_id[:8]
