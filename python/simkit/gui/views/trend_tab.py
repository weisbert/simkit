"""TrendTab — right-panel tab visualising one :class:`TrendResult` (G-6).

A single table: rows are ``(test, corner, point, output)`` keys, columns
are the resolved slices side-by-side, plus a trailing direction glyph.
Two checkboxes: "只看变化的行" (hide rows whose value never moved) and a
sentinel toggle (``__sim_status__`` rows hidden by default — they are
sweep bookkeeping, not review numbers).

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
from simkit.trend import TrendResult


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
        self.changed_only_check = QCheckBox("只看变化的行", self)
        self.changed_only_check.setObjectName("trendChangedOnlyCheck")
        self.changed_only_check.setToolTip(
            "隐藏数值在所有里程碑之间完全相同的行"
        )
        self.sentinel_check = QCheckBox("显示 sim-status 行", self)
        self.sentinel_check.setObjectName("trendSentinelCheck")
        self.sentinel_check.setToolTip(
            "显示 __sim_status__ 哨兵行(默认隐藏,它们是仿真记账行)"
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
            "这些里程碑之间没有可对齐的结果行。", self,
        )
        self.empty_label.setObjectName("trendEmptyLabel")
        self.empty_label.setStyleSheet("color: #888;")
        self.empty_label.setVisible(not trend_result.rows)

        # --- assemble ------------------------------------------------------
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self.header)
        v.addLayout(controls)
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
