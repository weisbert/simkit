"""Table model behind the Corner Manager view — Phase 5 Stage 1 (spec §7).

Layout mirrors Cadence's native corner manager. Rows are grouped, top to
bottom:

* **Temperature** — the ``temperature`` row, if any column carries it.
* **Model Files** — one row per process-model file; each cell shows that
  column's section (the process corner, e.g. ``tt`` / ``ss`` / ``ff``).
* **Design Variables** — every mode register / PVT variable.

Columns are corners. Each cell is a materialised value (spec §2.2). Colour
vocabulary:

* red  — a manual override that diverges from the mode base (D1, spec §6.4).
* blue tint — a mode-managed register cell (its value comes from the mode).
* warm tint — a temperature row; green tint — a model-file row.
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
    column_models,
    column_point_count,
    effective_name,
    is_cell_red,
    ordered_var_rows,
    set_column_model_section,
    set_column_override,
    set_pvt_var,
)

_MISSING = "—"
_TEMPERATURE_VAR = "temperature"

_BRUSH_RED = QBrush(QColor(0xFF, 0xD0, 0xD0))      # diverging override (D1)
_BRUSH_MANAGED = QBrush(QColor(0xE8, 0xF0, 0xFF))  # mode-managed register cell
_BRUSH_TEMP = QBrush(QColor(0xFF, 0xF2, 0xD8))     # temperature row
_BRUSH_MODEL = QBrush(QColor(0xE5, 0xF3, 0xE5))    # model-file row
_BRUSH_FOREIGN_HDR = QBrush(QColor(0xDD, 0xDD, 0xDD))  # unmanaged column header
_BRUSH_DISABLED_HDR = QBrush(QColor(0xEC, 0xEC, 0xEC))  # disabled column header


class CornerModelTableModel(QAbstractTableModel):
    """Read-through table model: rows = vars / model files, columns = corners.

    Editing a scalar cell mutates a *copy* of the cornermodel and re-seats it
    via a full reset. Editing a mode-managed register cell creates a per-column
    override (spec §6.4); editing a PVT cell updates that column's PVT var;
    editing a model-file cell retargets that column's process section.
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
        # Per-column resolved process-model entries (Model Files rows).
        self._col_models = [
            column_models(c, self._profile) for c in self._cols
        ]
        register_vars: set[str] = set()
        for mode in self._cm.modes.values():
            register_vars |= set(mode.vars)
        all_vars: set[str] = set()
        for disp in self._display:
            all_vars |= set(disp)
        self._register_vars = register_vars
        # Stage 5: honour the cornermodel's explicit var_order.
        ordered = ordered_var_rows(self._cm, all_vars)
        temp_vars = [v for v in ordered if v == _TEMPERATURE_VAR]
        design_vars = [v for v in ordered if v != _TEMPERATURE_VAR]
        # Model Files: one row per distinct file across every column.
        model_files: list[str] = []
        seen: set[str] = set()
        for entries in self._col_models:
            for m in entries:
                if m.file not in seen:
                    seen.add(m.file)
                    model_files.append(m.file)
        model_files.sort()
        # Each row is a ("var" | "model", key) descriptor — Cadence grouping:
        # temperature, then model files, then the design variables.
        self._rows: list[tuple[str, str]] = (
            [("var", v) for v in temp_vars]
            + [("model", f) for f in model_files]
            + [("var", v) for v in design_vars]
        )

    # --- Qt model surface ------------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._cols)

    def _model_entry(self, col: int, file: str) -> Any:
        """The ModelEntry for ``file`` on column ``col`` (or None)."""
        for m in self._col_models[col]:
            if m.file == file:
                return m
        return None

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
                    bits.append(f"Mode: {col.mode}")
                else:
                    bits.append(
                        "Unmanaged (foreign) column — preserved as-is on push"
                    )
                if col.variant is not None:
                    bits.append(f"Variant: {col.variant}")
                if col.template is not None:
                    bits.append(f"Generated from template {col.template}")
                if col.correlated_axes:
                    bits.append(
                        f"Correlated axes: {', '.join(col.correlated_axes)} "
                        f"({self._point_counts[section]} pts)"
                    )
                return "\n".join(bits)
            if role == Qt.BackgroundRole:
                if not col.is_managed:
                    return _BRUSH_FOREIGN_HDR
                if not col.enabled:
                    return _BRUSH_DISABLED_HDR
            return None
        # vertical: variable / model-file row names
        if not (0 <= section < len(self._rows)):
            return None
        kind, key = self._rows[section]
        if role == Qt.DisplayRole:
            return key
        if role == Qt.ToolTipRole:
            if kind == "model":
                return ("Process model file — the cell shows that corner's "
                        "section (process corner)")
            if key == _TEMPERATURE_VAR:
                return "Temperature"
            return "Register variable (mode-managed)" \
                if key in self._register_vars \
                else "PVT variable (per-column)"
        if role == Qt.BackgroundRole:
            if kind == "model":
                return _BRUSH_MODEL
            if key == _TEMPERATURE_VAR:
                return _BRUSH_TEMP
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        if not (0 <= row < len(self._rows)
                and 0 <= col < len(self._cols)):
            return None
        kind, key = self._rows[row]

        if kind == "model":
            entry = self._model_entry(col, key)
            if role == Qt.DisplayRole:
                return _MISSING if entry is None else ", ".join(entry.section)
            if role == Qt.EditRole:
                if entry is None or len(entry.section) != 1:
                    return ""
                return entry.section[0]
            if role == Qt.BackgroundRole:
                return _BRUSH_MODEL if entry is not None else None
            if role == Qt.ToolTipRole and entry is not None:
                return (f"{entry.file} · block {entry.block} · "
                        f"test {entry.test}")
            return None

        var = key
        column = self._cols[col]
        values = self._display[col].get(var)

        if role == Qt.DisplayRole:
            if values is None:
                return _MISSING
            return ", ".join(values)

        if role == Qt.EditRole:
            # 1c — the cell editor must open pre-filled with the current
            # value (Excel-like); only scalar cells are editable.
            if values is None or len(values) != 1:
                return ""
            return values[0]

        if role == Qt.BackgroundRole:
            if values is None:
                return None
            if is_cell_red(self._cm, column, var):
                return _BRUSH_RED
            if column.is_managed and var in self._cm.modes[column.mode].vars:
                return _BRUSH_MANAGED
            if var == _TEMPERATURE_VAR:
                return _BRUSH_TEMP
            return None

        if role == Qt.ToolTipRole and values is not None:
            if is_cell_red(self._cm, column, var):
                base = self._cm.modes[column.mode].vars.get(var)
                return (
                    f"Manual override {column.overrides[var]!r} diverges "
                    f"from mode base {base!r} (D1)"
                )
        return None

    # --- editing ---------------------------------------------------------

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        base = Qt.ItemIsSelectable | Qt.ItemIsEnabled
        if not index.isValid():
            return base
        kind, key = self._rows[index.row()]
        if kind == "model":
            entry = self._model_entry(index.column(), key)
            if entry is not None and len(entry.section) == 1:
                return base | Qt.ItemIsEditable
            return base
        values = self._display[index.column()].get(key)
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
        kind, key = self._rows[row]
        text = str(value).strip()
        if text == "":
            return False
        if kind == "model":
            new_cm = set_column_model_section(self._cm, col, key, text)
        elif self.is_managed_cell(row, col):
            new_cm = set_column_override(self._cm, col, key, text)
        else:
            new_cm = set_pvt_var(self._cm, col, key, text)
        self.set_cornermodel(new_cm)
        self.cornermodelChanged.emit(new_cm)
        return True

    # --- non-Qt helpers --------------------------------------------------

    def cornermodel(self) -> CornerModel:
        return self._cm

    def row_kind(self, row: int) -> Optional[str]:
        """``"var"`` or ``"model"`` for ``row`` (or None if out of range)."""
        if 0 <= row < len(self._rows):
            return self._rows[row][0]
        return None

    def row_label(self, row: int) -> Optional[str]:
        """The displayed row name — a variable name or a model-file name."""
        if 0 <= row < len(self._rows):
            return self._rows[row][1]
        return None

    def var_at(self, row: int) -> Optional[str]:
        """The variable name for a variable row — None for a model-file row."""
        if 0 <= row < len(self._rows) and self._rows[row][0] == "var":
            return self._rows[row][1]
        return None

    def model_at(self, row: int) -> Optional[str]:
        """The model-file name for a model-file row — None otherwise."""
        if 0 <= row < len(self._rows) and self._rows[row][0] == "model":
            return self._rows[row][1]
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
