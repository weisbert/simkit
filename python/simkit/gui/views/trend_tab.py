"""TrendTab — right-panel tab visualising one :class:`TrendResult` (G-6).

A single table: rows are ``(test, corner, point, output)`` keys, columns
are the resolved slices side-by-side, plus a trailing direction glyph.
Two checkboxes: "Changed rows only" (hide rows whose value never moved)
and a sentinel toggle (``__sim_status__`` rows hidden by default — they
are sweep bookkeeping, not review numbers).

Sibling of :class:`DiffTab`; the parent ``QTabWidget`` owns the close
X, the in-tab Close button emits :pyattr:`closed`.
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import QModelIndex, QSortFilterProxyModel, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
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

from simkit.gui.trend_model import TrendTableModel
from simkit.trend import TrendResult, provenance_consistency


class _TrendFilterProxy(QSortFilterProxyModel):
    """Row-visibility proxy — defers predicates to :class:`TrendTableModel`."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._changed_only = False
        self._show_sentinels = False

    def set_changed_only(self, on: bool) -> None:
        self._changed_only = bool(on)
        self.invalidateFilter()

    def set_show_sentinels(self, on: bool) -> None:
        self._show_sentinels = bool(on)
        self.invalidateFilter()

    def filterAcceptsRow(
        self, source_row: int, source_parent: QModelIndex,
    ) -> bool:
        model = self.sourceModel()
        if not isinstance(model, TrendTableModel):
            return True
        if not self._show_sentinels and model.is_sentinel_row(source_row):
            return False
        if self._changed_only and not model.changed_only_filter(source_row):
            return False
        return True


class TrendTab(QWidget):
    """Right-panel tab visualising a :class:`TrendResult`."""

    closed = pyqtSignal()

    def __init__(
        self,
        trend_result: TrendResult,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._trend_result = trend_result

        cols = " → ".join(c.display for c in trend_result.columns)
        self._title = f"Trend: {cols}" if cols else "Trend"

        # --- header --------------------------------------------------------
        self.header = QFrame(self)
        self.header.setObjectName("trendHeader")
        self.header.setFrameShape(QFrame.StyledPanel)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(8, 4, 8, 4)

        self.header_label = QLabel(self._title, self.header)
        self.header_label.setObjectName("trendHeaderLabel")
        self.header_label.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Preferred,
        )
        header_layout.addWidget(self.header_label, stretch=1)

        self.close_button = QPushButton("Close", self.header)
        self.close_button.setObjectName("trendCloseButton")
        self.close_button.clicked.connect(self.closed.emit)
        header_layout.addWidget(self.close_button, stretch=0)

        # --- controls ------------------------------------------------------
        controls = QHBoxLayout()
        controls.setContentsMargins(8, 4, 8, 0)
        self.changed_only_check = QCheckBox("Changed rows only", self)
        self.changed_only_check.setObjectName("trendChangedOnlyCheck")
        self.changed_only_check.setToolTip(
            "Hide rows whose value is identical across every milestone"
        )
        self.sentinel_check = QCheckBox("Show sim-status rows", self)
        self.sentinel_check.setObjectName("trendSentinelCheck")
        self.sentinel_check.setToolTip(
            "Show __sim_status__ sentinel rows (hidden by default — they "
            "are simulation bookkeeping rows)"
        )
        controls.addWidget(self.changed_only_check)
        controls.addWidget(self.sentinel_check)
        controls.addStretch(1)

        # --- model / proxy / view -----------------------------------------
        self.model = TrendTableModel(trend_result, parent=self)
        self.proxy = _TrendFilterProxy(self)
        self.proxy.setSourceModel(self.model)

        self.table = QTableView(self)
        self.table.setObjectName("trendTable")
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Interactive,
        )

        # --- empty-state hint ---------------------------------------------
        self.empty_label = QLabel(
            "No result rows can be aligned across these milestones.", self,
        )
        self.empty_label.setObjectName("trendEmptyLabel")
        self.empty_label.setStyleSheet("color: #888;")
        self.empty_label.setVisible(not trend_result.rows)

        # --- condition-consistency strip (G-5) ----------------------------
        # The dangerous review failure is a margin trend whose columns
        # quietly came from different model files / PDK / hosts. Flag it
        # here, before the review, rather than after silicon.
        mismatches = provenance_consistency(trend_result.columns)
        self.consistency_label = QLabel(self)
        self.consistency_label.setObjectName("trendConsistencyLabel")
        self.consistency_label.setWordWrap(True)
        if mismatches:
            self.consistency_label.setText(
                "⚠ Inconsistent run conditions — these milestones were "
                "not run under the same conditions:\n"
                + "\n".join(f"  • {m}" for m in mismatches)
            )
            self.consistency_label.setStyleSheet(
                "QLabel#trendConsistencyLabel { background: #fff3a3; "
                "border: 1px solid #d4b500; padding: 4px 8px; }"
            )
            self.consistency_label.setVisible(True)
        else:
            self.consistency_label.setVisible(False)

        # --- assemble ------------------------------------------------------
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self.header)
        v.addLayout(controls)
        v.addWidget(self.consistency_label)
        v.addWidget(self.empty_label)
        v.addWidget(self.table, stretch=1)

        self.changed_only_check.toggled.connect(self.proxy.set_changed_only)
        self.sentinel_check.toggled.connect(self.proxy.set_show_sentinels)

    # --- public ------------------------------------------------------------

    @property
    def title(self) -> str:
        return self._title

    @property
    def trend_result(self) -> TrendResult:
        return self._trend_result
