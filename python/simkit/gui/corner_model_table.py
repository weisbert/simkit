"""Table model behind the Corner Manager view — Phase 5 (spec §7).

Layout mirrors Cadence's native corner manager, with the filter frame woven
into the grid (2026 UX feedback). Model coordinates:

* **column 0** — variable / model-file name.
* **column 1** — the *Filter corner* strip: a per-variable value filter that
  hides corner columns.
* **columns 2+** — the corner data columns.
* **row 0** — the filter row: ``(0,0)`` filters variable rows by name,
  ``(0,1)`` filters corner columns by name, ``(0,2+)`` are per-corner value
  filters that hide variable rows.
* **rows 1+** — the data rows, Cadence-grouped: Temperature, then one row per
  process model file (cell = section / process corner), then design vars.

Each filter cell carries a :class:`~simkit.gui.corner_filter.Matcher` (mode +
pattern); its display text leads with the mode chip so the active mode is
always visible. Filter state lives in the model and survives a cornermodel
rebuild.

Colour vocabulary for data cells:

* red — a manual override that diverges from the mode base (D1, spec §6.4).
* blue tint — a mode-managed register cell.
* warm tint — a temperature row; green tint — a model-file row.
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

from simkit.gui.corner_filter import FilterMode, Matcher
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

# Model geometry — the filter frame occupies row 0 and columns 0-1.
_FILTER_ROW = 0
_NAME_COL = 0          # variable / model-file name
_CFILTER_COL = 1       # "Filter corner" per-variable value strip
_DATA_COL0 = 2         # first corner column
_DATA_ROW0 = 1         # first data row

_BRUSH_RED = QBrush(QColor(0xFF, 0xD0, 0xD0))      # diverging override (D1)
_BRUSH_MANAGED = QBrush(QColor(0xE8, 0xF0, 0xFF))  # mode-managed register cell
_BRUSH_TEMP = QBrush(QColor(0xFF, 0xF2, 0xD8))     # temperature row
_BRUSH_MODEL = QBrush(QColor(0xE5, 0xF3, 0xE5))    # model-file row
_BRUSH_FOREIGN_HDR = QBrush(QColor(0xDD, 0xDD, 0xDD))  # unmanaged column header
_BRUSH_DISABLED_HDR = QBrush(QColor(0xEC, 0xEC, 0xEC))  # disabled column header
_BRUSH_NAME = QBrush(QColor(0xF4, 0xF4, 0xF4))     # variable-name column
_BRUSH_FILTER = QBrush(QColor(0xF0, 0xF0, 0xF6))   # an empty filter cell
_BRUSH_FILTER_ACTIVE = QBrush(QColor(0xFF, 0xF6, 0xC8))  # a filter cell in use


class CornerModelTableModel(QAbstractTableModel):
    """Read-through table model: data rows = vars / model files, data
    columns = corners, plus an embedded filter frame (row 0, columns 0-1).

    ``filtersChanged`` fires whenever a filter cell's pattern or mode changes
    so the owning view can re-apply row/column visibility.
    """

    cornermodelChanged = pyqtSignal(object)
    filtersChanged = pyqtSignal()

    def __init__(
        self, model: CornerModel, profile: object = None,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self._cm = model
        self._profile = profile   # PvtProfile | None — resolves axis_levels
        # Filter state — keyed slots, kept across cornermodel rebuilds.
        self._filters: dict[tuple, Matcher] = {}
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
        self._display: list[dict[str, tuple[str, ...]]] = [
            column_display_vars(self._cm, c, self._profile)
            for c in self._cols
        ]
        self._point_counts: list[int] = [
            column_point_count(self._cm, c, self._profile)
            for c in self._cols
        ]
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
        ordered = ordered_var_rows(self._cm, all_vars)
        temp_vars = [v for v in ordered if v == _TEMPERATURE_VAR]
        design_vars = [v for v in ordered if v != _TEMPERATURE_VAR]
        model_files: list[str] = []
        seen: set[str] = set()
        for entries in self._col_models:
            for m in entries:
                if m.file not in seen:
                    seen.add(m.file)
                    model_files.append(m.file)
        model_files.sort()
        # Cadence grouping: temperature, then model files, then design vars.
        self._rows: list[tuple[str, str]] = (
            [("var", v) for v in temp_vars]
            + [("model", f) for f in model_files]
            + [("var", v) for v in design_vars]
        )

    # --- geometry --------------------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else _DATA_ROW0 + len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else _DATA_COL0 + len(self._cols)

    def _model_entry(self, data_col: int, file: str) -> Any:
        for m in self._col_models[data_col]:
            if m.file == file:
                return m
        return None

    # --- filter frame ----------------------------------------------------

    def _filter_key(self, row: int, col: int) -> Optional[tuple]:
        """The matcher-slot key for a filter cell, or None for a non-filter
        cell. Slots: filter variables/corners by name (the two corner cells)
        and per-corner / per-variable value filters."""
        if row == _FILTER_ROW:
            if col == _NAME_COL:
                return ("var_by_name",)
            if col == _CFILTER_COL:
                return ("corner_by_name",)
            j = col - _DATA_COL0
            if 0 <= j < len(self._cols):
                return ("var_by_value", effective_name(self._cols[j]))
            return None
        if col == _CFILTER_COL:
            i = row - _DATA_ROW0
            if 0 <= i < len(self._rows):
                return ("corner_by_value", self._rows[i][1])
        return None

    def matcher_at(self, row: int, col: int) -> Optional[Matcher]:
        """The Matcher behind a filter cell (or None if it is not one)."""
        key = self._filter_key(row, col)
        if key is None:
            return None
        return self._filters.get(key, Matcher())

    def set_filter_options(
        self, row: int, col: int,
        mode: Optional[FilterMode] = None,
        case_sensitive: Optional[bool] = None,
    ) -> None:
        """Change a filter cell's mode / case flag (keeps its pattern)."""
        key = self._filter_key(row, col)
        if key is None:
            return
        cur = self._filters.get(key, Matcher())
        self._filters[key] = Matcher(
            mode=cur.mode if mode is None else mode,
            pattern=cur.pattern,
            case_sensitive=(cur.case_sensitive if case_sensitive is None
                            else case_sensitive),
        )
        idx = self.index(row, col)
        self.dataChanged.emit(idx, idx)
        self.filtersChanged.emit()

    def clear_all_filters(self) -> None:
        if not self._filters:
            return
        self._filters = {}
        top_left = self.index(0, 0)
        bot_right = self.index(self.rowCount() - 1, self.columnCount() - 1)
        self.dataChanged.emit(top_left, bot_right)
        self.filtersChanged.emit()

    def has_active_filters(self) -> bool:
        return any(m.active for m in self._filters.values())

    # --- visibility ------------------------------------------------------

    def _data_value(self, data_row: int, data_col: int) -> str:
        """The plain display text of a data cell — used for filtering."""
        kind, key = self._rows[data_row]
        if kind == "model":
            entry = self._model_entry(data_col, key)
            return "" if entry is None else ", ".join(entry.section)
        values = self._display[data_col].get(key)
        return "" if values is None else ", ".join(values)

    def is_data_col_visible(self, data_col: int) -> bool:
        """True if corner column ``data_col`` passes the name + value
        filters (the Filter-corner strip)."""
        if not (0 <= data_col < len(self._cols)):
            return True
        name = effective_name(self._cols[data_col])
        if not self._filters.get(("corner_by_name",), Matcher()).matches(name):
            return False
        for i, (_kind, label) in enumerate(self._rows):
            m = self._filters.get(("corner_by_value", label))
            if m is not None and m.active \
                    and not m.matches(self._data_value(i, data_col)):
                return False
        return True

    def is_data_row_visible(self, data_row: int) -> bool:
        """True if data row ``data_row`` passes the name + value filters
        (the Filter-variable row)."""
        if not (0 <= data_row < len(self._rows)):
            return True
        label = self._rows[data_row][1]
        if not self._filters.get(("var_by_name",), Matcher()).matches(label):
            return False
        for j, col in enumerate(self._cols):
            m = self._filters.get(("var_by_value", effective_name(col)))
            if m is not None and m.active \
                    and not m.matches(self._data_value(data_row, j)):
                return False
        return True

    # --- Qt model surface ------------------------------------------------

    def headerData(
        self, section: int, orientation: Qt.Orientation,
        role: int = Qt.DisplayRole,
    ) -> Any:
        if orientation != Qt.Horizontal:
            return None   # the variable name lives in column 0, not a header
        if role == Qt.DisplayRole:
            if section == _NAME_COL:
                return "Variable"
            if section == _CFILTER_COL:
                return "Filter corner"
            j = section - _DATA_COL0
            if not (0 <= j < len(self._cols)):
                return None
            col = self._cols[j]
            name = effective_name(col)
            points = self._point_counts[j]
            return f"{name} ·{points}" if points > 1 else name
        j = section - _DATA_COL0
        if not (0 <= j < len(self._cols)):
            return None
        col = self._cols[j]
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
                    f"({self._point_counts[j]} pts)"
                )
            return "\n".join(bits)
        if role == Qt.BackgroundRole:
            if not col.is_managed:
                return _BRUSH_FOREIGN_HDR
            if not col.enabled:
                return _BRUSH_DISABLED_HDR
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row, col = index.row(), index.column()

        key = self._filter_key(row, col)
        if key is not None:
            m = self._filters.get(key, Matcher())
            if role == Qt.DisplayRole:
                return (f"{m.mode.chip}  {m.pattern}" if m.pattern
                        else f"{m.mode.chip}  ⌕")
            if role == Qt.EditRole:
                return m.pattern
            if role == Qt.BackgroundRole:
                return _BRUSH_FILTER_ACTIVE if m.active else _BRUSH_FILTER
            if role == Qt.ToolTipRole:
                cs = " · case-sensitive" if m.case_sensitive else ""
                return (f"Filter — {m.mode.value}{cs}\n"
                        f"right-click to change the match mode")
            return None

        if col == _NAME_COL:
            i = row - _DATA_ROW0
            if not (0 <= i < len(self._rows)):
                return None
            kind, label = self._rows[i]
            if role == Qt.DisplayRole:
                return label
            if role == Qt.BackgroundRole:
                if kind == "model":
                    return _BRUSH_MODEL
                if label == _TEMPERATURE_VAR:
                    return _BRUSH_TEMP
                return _BRUSH_NAME
            if role == Qt.ToolTipRole:
                if kind == "model":
                    return ("Process model file — its cells show the "
                            "section (process corner)")
                if label == _TEMPERATURE_VAR:
                    return "Temperature"
                return ("Register variable (mode-managed)"
                        if label in self._register_vars
                        else "PVT variable (per-column)")
            return None

        # data cell — col >= _DATA_COL0, row >= _DATA_ROW0
        i, j = row - _DATA_ROW0, col - _DATA_COL0
        if not (0 <= i < len(self._rows) and 0 <= j < len(self._cols)):
            return None
        kind, key2 = self._rows[i]
        column = self._cols[j]

        if kind == "model":
            entry = self._model_entry(j, key2)
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

        var = key2
        values = self._display[j].get(var)
        if role == Qt.DisplayRole:
            return _MISSING if values is None else ", ".join(values)
        if role == Qt.EditRole:
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

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        base = Qt.ItemIsSelectable | Qt.ItemIsEnabled
        if not index.isValid():
            return base
        row, col = index.row(), index.column()
        if self._filter_key(row, col) is not None:
            return base | Qt.ItemIsEditable
        if col == _NAME_COL:
            return base
        i, j = row - _DATA_ROW0, col - _DATA_COL0
        if not (0 <= i < len(self._rows) and 0 <= j < len(self._cols)):
            return base
        kind, key2 = self._rows[i]
        if kind == "model":
            entry = self._model_entry(j, key2)
            if entry is not None and len(entry.section) == 1:
                return base | Qt.ItemIsEditable
            return base
        values = self._display[j].get(key2)
        if values is not None and len(values) == 1:
            return base | Qt.ItemIsEditable
        return base

    def setData(
        self, index: QModelIndex, value: Any, role: int = Qt.EditRole
    ) -> bool:
        if role != Qt.EditRole or not index.isValid():
            return False
        row, col = index.row(), index.column()

        key = self._filter_key(row, col)
        if key is not None:
            cur = self._filters.get(key, Matcher())
            self._filters[key] = Matcher(
                mode=cur.mode, pattern=str(value),
                case_sensitive=cur.case_sensitive,
            )
            self.dataChanged.emit(index, index)
            self.filtersChanged.emit()
            return True

        text = str(value).strip()
        if text == "":
            return False
        i, j = row - _DATA_ROW0, col - _DATA_COL0
        if not (0 <= i < len(self._rows) and 0 <= j < len(self._cols)):
            return False
        kind, key2 = self._rows[i]
        if kind == "model":
            new_cm = set_column_model_section(self._cm, j, key2, text)
        elif self.is_managed_cell(row, col):
            new_cm = set_column_override(self._cm, j, key2, text)
        else:
            new_cm = set_pvt_var(self._cm, j, key2, text)
        self.set_cornermodel(new_cm)
        self.cornermodelChanged.emit(new_cm)
        return True

    # --- non-Qt helpers --------------------------------------------------

    def cornermodel(self) -> CornerModel:
        return self._cm

    def data_row_kind(self, row: int) -> Optional[str]:
        i = row - _DATA_ROW0
        if 0 <= i < len(self._rows):
            return self._rows[i][0]
        return None

    def row_label(self, row: int) -> Optional[str]:
        """Displayed name of a data row (variable or model file)."""
        i = row - _DATA_ROW0
        if 0 <= i < len(self._rows):
            return self._rows[i][1]
        return None

    def var_at(self, row: int) -> Optional[str]:
        """Variable name for a variable data row — None for the filter row,
        a model-file row, or out of range."""
        i = row - _DATA_ROW0
        if 0 <= i < len(self._rows) and self._rows[i][0] == "var":
            return self._rows[i][1]
        return None

    def model_at(self, row: int) -> Optional[str]:
        """Model-file name for a model-file data row — None otherwise."""
        i = row - _DATA_ROW0
        if 0 <= i < len(self._rows) and self._rows[i][0] == "model":
            return self._rows[i][1]
        return None

    def column_at(self, col: int) -> Optional[Column]:
        """The Column behind a corner data column — None for the name /
        Filter-corner columns."""
        j = col - _DATA_COL0
        if 0 <= j < len(self._cols):
            return self._cols[j]
        return None

    def is_managed_cell(self, row: int, col: int) -> bool:
        """True if the cell is a mode-managed register cell — a manual edit
        here creates an override rather than a plain PVT-var change."""
        var = self.var_at(row)
        column = self.column_at(col)
        if var is None or column is None or not column.is_managed:
            return False
        return var in self._cm.modes[column.mode].vars
