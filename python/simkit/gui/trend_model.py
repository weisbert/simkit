"""Trend table model — backing for :class:`TrendTab` (G-6 / FDR-6).

Wraps a :class:`simkit.trend.TrendResult` in a ``QAbstractTableModel``.
Unlike :class:`DiffResultsModel` the column count is *dynamic*: four
key columns (test / corner / point / output), then one value column per
resolved slice, then a trailing ``dir`` column carrying the monotonic
verdict.

Colour vocabulary is deliberately lighter than the diff model's: a
trend is a survey, not a regression triage. A value cell whose
``spec_status`` failed is tinted red; a row whose values move at all is
left a faint yellow so the eye can skim the table for "what shifted"
without a filter. Everything else stays default.
"""

from __future__ import annotations

from typing import Any, List, Optional

from PyQt5.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt
from PyQt5.QtGui import QBrush, QColor

from simkit.trend import TrendResult, TrendRow

_MISSING = "—"

_BRUSH_FAIL = QBrush(QColor(0xFF, 0xD0, 0xD0))   # spec_status fail/eval_err
_BRUSH_VARIES = QBrush(QColor(0xFF, 0xF5, 0xB3))  # value moved across slices

_FAIL_LIKE = frozenset({"fail", "eval_err", "no_convergence"})

_KEY_COLUMNS: tuple[str, ...] = ("test", "corner", "point", "output")

_DIR_GLYPH = {"up": "▲", "down": "▼", "mixed": "≈", "flat": "=", None: ""}
_DIR_TOOLTIP = {
    "up": "每一步都上升",
    "down": "每一步都下降",
    "mixed": "方向不一致(有升有降)",
    "flat": "数值未变",
    None: "数值列不足两个,无法判断趋势",
}


class TrendTableModel(QAbstractTableModel):
    """Read-only table model over a :class:`TrendResult`."""

    KEY_COLUMNS: tuple[str, ...] = _KEY_COLUMNS

    def __init__(
        self,
        result: TrendResult,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self._result = result
        self._rows: List[TrendRow] = list(result.rows)
        self._n_slices = len(result.columns)

    # --- Qt model surface ------------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        # 4 key columns + one per slice + 1 trailing direction column.
        return len(self.KEY_COLUMNS) + self._n_slices + 1

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.DisplayRole,
    ) -> Any:
        if orientation != Qt.Horizontal:
            if role == Qt.DisplayRole:
                return section + 1
            return None
        n_key = len(self.KEY_COLUMNS)
        if role == Qt.DisplayRole:
            if section < n_key:
                return self.KEY_COLUMNS[section]
            if section < n_key + self._n_slices:
                return self._result.columns[section - n_key].display
            return "dir"
        if role == Qt.ToolTipRole and n_key <= section < n_key + self._n_slices:
            col = self._result.columns[section - n_key]
            return (
                f"{col.identifier}\nrun_id: {col.run_id}\n{col.timestamp}"
            )
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        if not (0 <= row < len(self._rows)):
            return None
        n_key = len(self.KEY_COLUMNS)
        trend_row = self._rows[row]

        if role == Qt.DisplayRole:
            if col < n_key:
                return str(getattr(trend_row, self.KEY_COLUMNS[col]))
            if col < n_key + self._n_slices:
                return _fmt_cell(trend_row.cells[col - n_key])
            return _DIR_GLYPH.get(trend_row.direction, "")

        if role == Qt.ToolTipRole and col == n_key + self._n_slices:
            return _DIR_TOOLTIP.get(trend_row.direction)

        if role == Qt.BackgroundRole and n_key <= col < n_key + self._n_slices:
            cell = trend_row.cells[col - n_key]
            if cell.present and cell.spec_status in _FAIL_LIKE:
                return _BRUSH_FAIL
            if trend_row.varies:
                return _BRUSH_VARIES
            return None

        return None

    # --- non-Qt helpers --------------------------------------------------

    def trend_row_at(self, row: int) -> Optional[TrendRow]:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    def is_sentinel_row(self, row: int) -> bool:
        tr = self.trend_row_at(row)
        return tr is not None and tr.is_sentinel

    def changed_only_filter(self, row: int) -> bool:
        """True iff the row's values are not identical across all slices."""
        tr = self.trend_row_at(row)
        return tr is not None and tr.varies


def _fmt_cell(cell) -> str:
    if not cell.present:
        return _MISSING
    val = cell.value
    if val is None:
        txt = _MISSING
    elif isinstance(val, float):
        txt = f"{val:g}"
    else:
        txt = str(val)
    if cell.spec_status and cell.spec_status != "no_spec":
        txt = f"{txt}  [{cell.spec_status}]"
    return txt
