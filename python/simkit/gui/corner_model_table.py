"""Table model behind the Corner Manager view — Phase 5 Stage 1 (spec §7).

Layout mirrors Cadence's native corner manager: **variables are rows,
corners are columns**. Each cell is a materialised value (spec §2.2). Colour
vocabulary:

* red  — a manual override that diverges from the mode base (D1, spec §6.4).
* blue tint — a mode-managed register cell (its value comes from the mode).
* default — a per-column PVT cell, or any cell on an unmanaged column.

The model is read-through over a :class:`simkit.corner_model.CornerModel`;
edits go through the owning view, which rebuilds the cornermodel and calls
:meth:`set_cornermodel` (a full reset — Stage 1 keeps the model dumb).
"""

from __future__ import annotations

from typing import Any, Optional

from PyQt5.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    Qt,
    pyqtSignal,
)
from PyQt5.QtGui import QBrush, QColor

from simkit.corner_model import (
    Column,
    CornerModel,
    column_display_vars,
    column_point_count,
    effective_name,
    is_cell_red,
    ordered_var_rows,
    set_column_override,
    set_pvt_var,
)

_MISSING = "—"

_BRUSH_RED = QBrush(QColor(0xFF, 0xD0, 0xD0))      # diverging override (D1)
_BRUSH_MANAGED = QBrush(QColor(0xE8, 0xF0, 0xFF))  # mode-managed register cell
_BRUSH_FOREIGN_HDR = QBrush(QColor(0xDD, 0xDD, 0xDD))  # unmanaged column header
_BRUSH_DISABLED_HDR = QBrush(QColor(0xEC, 0xEC, 0xEC))  # disabled column header


class CornerModelTableModel(QAbstractTableModel):
    """Read-through table model: rows = vars, columns = corners.

    Editing a scalar cell mutates a *copy* of the cornermodel and re-seats it
    via a full reset. Editing a mode-managed register cell creates a per-column
    override (spec §6.4); editing a PVT cell updates that column's PVT var.
    ``cornermodelChanged`` fires after any edit so the owning view can persist.
    """

    cornermodelChanged = pyqtSignal(object)

    def __init__(
        self, model: CornerModel, profile: object = None,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self._cm = model
        self._profile = profile   # PvtProfile | None — resolves axis_levels
        self._rebuild()

    # --- rebuild ---------------------------------------------------------

    def set_cornermodel(
        self, model: CornerModel, profile: object = "_keep"
    ) -> None:
        self.beginResetModel()
        self._cm = model
        if profile != "_keep":
            self._profile = profile
        self._rebuild()
        self.endResetModel()

    def _rebuild(self) -> None:
        self._cols: list[Column] = list(self._cm.columns)
        # Per-column display vars (a correlated/aggregation column merges the
        # distinct values across its whole expansion) + simulation-point count.
        # Stage 6: the bound profile resolves axis_levels.
        self._display: list[dict[str, tuple[str, ...]]] = [
            column_display_vars(self._cm, c, self._profile)
            for c in self._cols
        ]
        self._point_counts: list[int] = [
            column_point_count(self._cm, c, self._profile)
            for c in self._cols
        ]
        register_vars: set[str] = set()
        for mode in self._cm.modes.values():
            register_vars |= set(mode.vars)
        all_vars: set[str] = set()
        for disp in self._display:
            all_vars |= set(disp)
        self._register_vars = register_vars
        # Stage 5: honour the cornermodel's explicit var_order.
        self._var_rows: list[str] = ordered_var_rows(self._cm, all_vars)

    # --- Qt model surface ------------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._var_rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._cols)

    def headerData(
        self, section: int, orientation: Qt.Orientation,
        role: int = Qt.DisplayRole,
    ) -> Any:
        if orientation == Qt.Horizontal:
            if not (0 <= section < len(self._cols)):
                return None
            col = self._cols[section]
            if role == Qt.DisplayRole:
                name = effective_name(col)
                points = self._point_counts[section]
                # Aggregation column — surface the simulation-point count.
                return f"{name} ·{points}" if points > 1 else name
            if role == Qt.ToolTipRole:
                bits = []
                if col.is_managed:
                    bits.append(f"模式: {col.mode}")
                else:
                    bits.append("未托管列(foreign)— push 时原样保留")
                if col.variant is not None:
                    bits.append(f"变体: {col.variant}")
                if col.template is not None:
                    bits.append(f"由模板 {col.template} 生成")
                if col.correlated_axes:
                    bits.append(
                        f"复合轴: {', '.join(col.correlated_axes)} "
                        f"({self._point_counts[section]} 点)"
                    )
                return "\n".join(bits)
            if role == Qt.BackgroundRole:
                if not col.is_managed:
                    return _BRUSH_FOREIGN_HDR
                if not col.enabled:
                    return _BRUSH_DISABLED_HDR
            return None
        # vertical: variable names
        if not (0 <= section < len(self._var_rows)):
            return None
        var = self._var_rows[section]
        if role == Qt.DisplayRole:
            return var
        if role == Qt.ToolTipRole:
            return "寄存器变量(模式管理)" if var in self._register_vars \
                else "PVT 变量(逐列设置)"
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        if not (0 <= row < len(self._var_rows)
                and 0 <= col < len(self._cols)):
            return None
        var = self._var_rows[row]
        column = self._cols[col]
        values = self._display[col].get(var)

        if role == Qt.DisplayRole:
            if values is None:
                return _MISSING
            return ", ".join(values)

        if role == Qt.BackgroundRole:
            if values is None:
                return None
            if is_cell_red(self._cm, column, var):
                return _BRUSH_RED
            if column.is_managed and var in self._cm.modes[column.mode].vars:
                return _BRUSH_MANAGED
            return None

        if role == Qt.ToolTipRole and values is not None:
            if is_cell_red(self._cm, column, var):
                base = self._cm.modes[column.mode].vars.get(var)
                return (
                    f"手改覆盖 {column.overrides[var]!r},与模式 base "
                    f"{base!r} 不一致(D1)"
                )
        return None

    # --- editing ---------------------------------------------------------

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        base = Qt.ItemIsSelectable | Qt.ItemIsEnabled
        if not index.isValid():
            return base
        values = self._display[index.column()].get(
            self._var_rows[index.row()]
        )
        # Only present, scalar cells are editable — sweeps / correlated
        # aggregation cells round-trip via the sidecar, not a single cell.
        if values is not None and len(values) == 1:
            return base | Qt.ItemIsEditable
        return base

    def setData(
        self, index: QModelIndex, value: Any, role: int = Qt.EditRole
    ) -> bool:
        if role != Qt.EditRole or not index.isValid():
            return False
        row, col = index.row(), index.column()
        var = self._var_rows[row]
        text = str(value).strip()
        if text == "":
            return False
        if self.is_managed_cell(row, col):
            new_cm = set_column_override(self._cm, col, var, text)
        else:
            new_cm = set_pvt_var(self._cm, col, var, text)
        self.set_cornermodel(new_cm)
        self.cornermodelChanged.emit(new_cm)
        return True

    # --- non-Qt helpers --------------------------------------------------

    def cornermodel(self) -> CornerModel:
        return self._cm

    def var_at(self, row: int) -> Optional[str]:
        if 0 <= row < len(self._var_rows):
            return self._var_rows[row]
        return None

    def column_at(self, col: int) -> Optional[Column]:
        if 0 <= col < len(self._cols):
            return self._cols[col]
        return None

    def is_managed_cell(self, row: int, col: int) -> bool:
        """True if the cell is a mode-managed register cell — a manual edit
        here creates an override rather than a plain PVT-var change."""
        var = self.var_at(row)
        column = self.column_at(col)
        if var is None or column is None or not column.is_managed:
            return False
        return var in self._cm.modes[column.mode].vars
