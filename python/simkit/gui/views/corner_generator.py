"""PVT Corner Generator — an independent dialog that authors corners by
pattern and imports the result into the corner table (痛点 a / h).

Two halves:

* **Level definitions** — three flexible grids (Process / Voltage /
  Temperature). Each grid is rows = named levels, columns = the variables
  that level sets. A level that controls two or more variables is a
  *composite* level (痛点 h: CT-tuning bound to the process corner, the
  ``.s5p`` inductor file bound to temperature); a level controlling one
  variable is *simple*. Process additionally carries a model file, so its
  levels pick a section (section cells are a Cadence-style dropdown
  populated from the parsed model file).
* **Patterns** — a six-column table:

      [✓] | Mode | Corner name | Process | Voltage | Temperature

  Each row is one corner pattern. **Mode is per row** (many patterns × many
  modes); the bottom dropdown is the default for newly-added rows. The
  Enabled checkbox + Shift / Ctrl multi-select + right-click ▸ Enable /
  Disable lets the user keep draft rows around without re-generating them.
  P / V / T cells double-click into an inline checkable combobox (or just
  type "TT, SS" directly). Corner name supports ``{mode}`` ``{process}``
  ``{voltage}`` ``{temp}`` tokens; an empty name defaults to ``{mode}``
  and composite-axis expansion adds per-level discriminators downstream.
  On *Generate* each row expands via
  :func:`simkit.corner_model.generate_pattern_columns` — composite axes
  split into one column each, simple axes stay multi-valued — and the
  columns land in the corner table.

The grids round-trip the cornermodel's ``correlated_axes``; the data layer
and on-disk format are unchanged.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QStandardItem, QStandardItemModel
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QAction,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from simkit.corner_model import (
    CornerModelError,
    CorrelatedAxis,
    CorrelatedTuple,
    add_column,
    add_correlated_axis,
    effective_name,
    generate_pattern_columns,
    update_correlated_axis,
)

# The three fixed PVT axes the generator manages, in pattern-column order.
_AXES = ("Process", "Voltage", "Temperature")
_VAR_NAME_RE = r"^[A-Za-z][A-Za-z0-9_]*$"
_LEVEL_RE = r"^[A-Za-z0-9_]+$"

# Pattern-table column indices. The header order here is the on-screen order.
_PAT_COL_ENABLED = 0
_PAT_COL_MODE = 1
_PAT_COL_NAME = 2
_PAT_COL_PROCESS = 3
_PAT_COL_VOLTAGE = 4
_PAT_COL_TEMP = 5
_PAT_AXIS_COLS = {
    "Process": _PAT_COL_PROCESS,
    "Voltage": _PAT_COL_VOLTAGE,
    "Temperature": _PAT_COL_TEMP,
}
_PAT_HEADERS = (
    "", "Mode", "Corner name", "Process", "Voltage", "Temperature",
)

# Spectre `.scs`: `section <name>` ... `endsection`. HSPICE `.lib`: `.lib <name>`
# ... `.endl` (and only when name is a bare identifier — the include form
# `.lib 'file' name` references a section, never defines one).
_SECTION_RE = re.compile(
    r"^\s*(?:section\s+([A-Za-z_][\w]*)"
    r"|\.lib\s+([A-Za-z_][\w]*)\s*(?:\*.*)?$)",
    re.MULTILINE,
)


def _parse_model_sections(model_path: str) -> list[str]:
    """Return the section names defined in a Spectre / HSPICE model file
    (best-effort; empty on parse failure or unsupported format). Used by
    the Process grid's section-cell dropdown so the user picks a real
    section instead of typing one — matching Cadence's corner-editor UX."""
    p = Path(model_path).expanduser()
    if not p.is_absolute() or not p.is_file():
        return []
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    seen: list[str] = []
    for m in _SECTION_RE.finditer(text):
        name = m.group(1) or m.group(2)
        if name and name not in seen:
            seen.append(name)
    return seen


class _SectionDelegate(QStyledItemDelegate):
    """Per-cell editor for the Process grid's section column — a freely
    editable combobox populated with the sections parsed from the current
    model file (Cadence-style "supported corner names")."""

    def __init__(self, get_sections: Callable[[], list[str]]) -> None:
        super().__init__()
        self._get_sections = get_sections

    def createEditor(self, parent, option, index):  # noqa: N802
        cb = QComboBox(parent)
        cb.setEditable(True)
        cb.addItems(self._get_sections())
        return cb

    def setEditorData(self, editor: QComboBox, index) -> None:  # noqa: N802
        editor.setCurrentText(index.data() or "")

    def setModelData(self, editor: QComboBox, model, index) -> None:  # noqa: N802
        model.setData(index, editor.currentText().strip())


class _CheckableComboBox(QComboBox):
    """A QComboBox whose drop-down rows have checkboxes so the user can
    multi-select; selected items render in the line edit as comma-separated
    text. Free-typed text in the line edit is preserved as-is — typing
    \"TT, SS\" works the same as ticking TT and SS in the popup."""

    def __init__(self, items: list[str], current: list[str], parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self._model: QStandardItemModel = QStandardItemModel(self)
        current_set = set(current)
        for label in items:
            item = QStandardItem(label)
            item.setFlags(
                Qt.ItemIsSelectable | Qt.ItemIsEnabled
                | Qt.ItemIsUserCheckable
            )
            item.setCheckState(
                Qt.Checked if label in current_set else Qt.Unchecked
            )
            self._model.appendRow(item)
        self.setModel(self._model)
        # Suppress the default "active item" behavior — we want clicks to
        # toggle checks, not commit + close.
        self.view().pressed.connect(self._toggle_at)
        self.setLineEdit(QLineEdit())
        self.lineEdit().setText(", ".join(current))
        self._model.dataChanged.connect(self._refresh_line_edit)

    def _toggle_at(self, index) -> None:
        item = self._model.itemFromIndex(index)
        if item is None:
            return
        item.setCheckState(
            Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked
        )

    def _refresh_line_edit(self, *_args) -> None:
        # Only overwrite the line edit from the checked state if the user
        # hasn't typed something that diverges — otherwise free-typed text
        # like "TT, SS" would be clobbered every time the popup updates.
        typed = self.lineEdit().text().strip()
        checked = self.checked_labels()
        if not typed or set(_split_levels(typed)) == set(_split_labels_in_model(self._model)):
            self.lineEdit().setText(", ".join(checked))

    def checked_labels(self) -> list[str]:
        return [
            self._model.item(i).text()
            for i in range(self._model.rowCount())
            if self._model.item(i).checkState() == Qt.Checked
        ]

    def committed_value(self) -> str:
        """Return what should go into the cell. Free-typed text wins when it
        diverges from the checked set; otherwise the comma-joined checks."""
        typed = self.lineEdit().text().strip()
        if not typed:
            return ", ".join(self.checked_labels())
        return typed


def _split_labels_in_model(model: QStandardItemModel) -> list[str]:
    return [model.item(i).text() for i in range(model.rowCount())]


class _MultiPickDelegate(QStyledItemDelegate):
    """Pattern-table delegate for the P/V/T cells: pops a _CheckableComboBox
    populated with the axis's currently defined levels. Free-typed text is
    preserved (the user can still type \"TT, SS\" without using the picker)."""

    def __init__(self, get_levels: Callable[[], list[str]]) -> None:
        super().__init__()
        self._get_levels = get_levels

    def createEditor(self, parent, option, index):  # noqa: N802
        current = _split_levels(index.data() or "")
        cb = _CheckableComboBox(self._get_levels(), current, parent)
        return cb

    def setEditorData(self, editor: "_CheckableComboBox", index) -> None:  # noqa: N802
        editor.lineEdit().setText(index.data() or "")

    def setModelData(  # noqa: N802
        self, editor: "_CheckableComboBox", model, index,
    ) -> None:
        model.setData(index, editor.committed_value())


class _ModeComboDelegate(QStyledItemDelegate):
    """Pattern-table delegate for the per-row Mode column — a plain (single-
    select, non-editable) QComboBox of the cornermodel's defined modes."""

    def __init__(self, get_modes: Callable[[], list[str]]) -> None:
        super().__init__()
        self._get_modes = get_modes

    def createEditor(self, parent, option, index):  # noqa: N802
        cb = QComboBox(parent)
        cb.addItems(self._get_modes())
        current = index.data() or ""
        i = cb.findText(current)
        if i >= 0:
            cb.setCurrentIndex(i)
        return cb

    def setEditorData(self, editor: QComboBox, index) -> None:  # noqa: N802
        i = editor.findText(index.data() or "")
        if i >= 0:
            editor.setCurrentIndex(i)

    def setModelData(self, editor: QComboBox, model, index) -> None:  # noqa: N802
        model.setData(index, editor.currentText())


class _LevelGrid(QWidget):
    """One axis's level-definition grid: rows = levels, columns = the
    variables each level sets. Columns are user-added / removed. The Process
    grid additionally carries a model file, which adds a 'section' column."""

    def __init__(
        self, axis_name: str, *, allow_model_file: bool,
        view: Optional[QWidget] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._axis_name = axis_name
        self._allow_model_file = allow_model_file
        self._has_section = False
        # ``view`` (a CornerManagerView) is only needed by the Process grid
        # so "Read from Cadence" can reach the main window for the loaded
        # project / session.
        self._view = view
        # Sections parsed from the current model file (Process grid only) —
        # surfaced as the section-cell dropdown. May also be seeded from
        # "Read from Cadence" when the file is not on disk yet.
        self._available_sections: list[str] = []
        # The absolute path most recently seen for the model file (set by
        # Browse and Read-from-Cadence) so manually-typed relative names
        # can still be parsed for sections.
        self._model_file_abs: Optional[str] = None

        v = QVBoxLayout(self)
        v.setContentsMargins(2, 2, 2, 2)
        v.addWidget(QLabel(f"<b>{axis_name}</b>"))

        self._model_file_edit: Optional[QLineEdit] = None
        if allow_model_file:
            mf = QHBoxLayout()
            self._model_file_edit = QLineEdit()
            self._model_file_edit.setPlaceholderText(
                "model file — levels pick a section of it"
            )
            self._browse_btn = QPushButton("Browse…")
            self._cadence_btn = QPushButton("Read from Cadence")
            self._cadence_btn.setToolTip(
                "Pull the model file(s) the live Maestro corner table uses."
            )
            mf.addWidget(self._model_file_edit, 1)
            mf.addWidget(self._browse_btn)
            mf.addWidget(self._cadence_btn)
            v.addLayout(mf)
            self._browse_btn.clicked.connect(self._browse_model_file)
            self._cadence_btn.clicked.connect(self._read_from_cadence)
            self._model_file_edit.textChanged.connect(self._sync_section_column)

        self._table = QTableWidget(0, 1, self)
        self._table.setHorizontalHeaderLabels(["Level"])
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Interactive
        )
        self._table.verticalHeader().setDefaultSectionSize(24)
        self._table.horizontalHeader().sectionDoubleClicked.connect(
            self._rename_variable
        )
        v.addWidget(self._table, 1)

        btns = QHBoxLayout()
        for label, slot in (
            ("+ Level", self._add_level),
            ("- Level", self._remove_level),
            ("+ Variable", self._add_variable),
            ("- Variable", self._remove_variable),
        ):
            b = QPushButton(label)
            b.clicked.connect(slot)
            btns.addWidget(b)
        v.addLayout(btns)
        v.addWidget(QLabel(
            "<i>Double-click a variable header to rename it.</i>"
        ))

    # --- column bookkeeping ---------------------------------------------
    def _member_start(self) -> int:
        """First member-variable column — 1, or 2 when a section column is in."""
        return 2 if self._has_section else 1

    def _member_names(self) -> list[str]:
        return [
            self._table.horizontalHeaderItem(c).text()
            for c in range(self._member_start(), self._table.columnCount())
        ]

    def _sync_section_column(self, *_args) -> None:
        want = bool(
            self._model_file_edit is not None
            and self._model_file_edit.text().strip()
        )
        if want and not self._has_section:
            self._table.insertColumn(1)
            self._table.setHorizontalHeaderItem(
                1, QTableWidgetItem("section")
            )
            self._has_section = True
            # Section cells get a combobox editor populated with whatever
            # sections we know — parsed from the file or seeded by "Read
            # from Cadence". The delegate looks up the live list lazily on
            # each edit, so updates after Browse / typing show through.
            self._table.setItemDelegateForColumn(
                1, _SectionDelegate(lambda: list(self._available_sections)),
            )
        elif not want and self._has_section:
            self._table.removeColumn(1)
            self._has_section = False
        # Whenever the file path changes, re-parse so the dropdown reflects
        # the new file (or empties when the file goes away / is unreadable).
        self._refresh_available_sections()

    def _refresh_available_sections(self) -> None:
        if self._model_file_edit is None:
            self._available_sections = []
            return
        typed = self._model_file_edit.text().strip()
        if not typed:
            self._available_sections = []
            return
        candidate = typed
        # If the user typed a relative name, fall back to the abs path we
        # captured from Browse / Read-from-Cadence.
        if not Path(candidate).is_absolute() and self._model_file_abs:
            if Path(self._model_file_abs).name == typed:
                candidate = self._model_file_abs
        parsed = _parse_model_sections(candidate)
        if parsed:
            self._available_sections = parsed

    # --- edits -----------------------------------------------------------
    def _add_variable(
        self, _checked: bool = False, *, initial: Optional[str] = None
    ) -> None:
        # _checked absorbs the bool QPushButton.clicked emits.
        if initial is not None:
            name = initial
        else:
            name, ok = QInputDialog.getText(
                self, "Add variable", "Variable name:"
            )
            if not ok or not name.strip():
                return
            name = name.strip()
        import re
        if not re.match(_VAR_NAME_RE, name):
            QMessageBox.warning(
                self, "Add variable", f"{name!r} is not a valid name."
            )
            return
        if name in self._member_names():
            QMessageBox.warning(
                self, "Add variable", f"{name!r} is already a variable."
            )
            return
        c = self._table.columnCount()
        self._table.insertColumn(c)
        self._table.setHorizontalHeaderItem(c, QTableWidgetItem(name))

    def _add_level(self) -> None:
        self._table.insertRow(self._table.rowCount())

    def _remove_level(self) -> None:
        r = self._table.currentRow()
        if r >= 0:
            self._table.removeRow(r)

    def _remove_variable(self) -> None:
        c = self._table.currentColumn()
        if c < self._member_start():
            QMessageBox.information(
                self, "Remove variable",
                "Select a cell in a variable column to remove it.",
            )
            return
        self._table.removeColumn(c)

    def _rename_variable(self, section: int) -> None:
        if section < self._member_start():
            return
        cur = self._table.horizontalHeaderItem(section).text()
        name, ok = QInputDialog.getText(
            self, "Rename variable", "Variable name:", text=cur
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        import re
        if not re.match(_VAR_NAME_RE, name):
            QMessageBox.warning(
                self, "Rename variable", f"{name!r} is not a valid name."
            )
            return
        if name != cur and name in self._member_names():
            QMessageBox.warning(
                self, "Rename variable", f"{name!r} is already a variable."
            )
            return
        self._table.setHorizontalHeaderItem(section, QTableWidgetItem(name))

    def _browse_model_file(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Select model file", str(Path.cwd()),
            "Model files (*.scs *.spi *.cir *.mod);;All files (*)",
        )
        if chosen:
            # Remember the abs path for the section dropdown — keeps working
            # even if the user later trims the field to just the basename.
            self._model_file_abs = chosen
            self._model_file_edit.setText(chosen)

    def _read_from_cadence(self) -> None:
        """Pull the model file(s) the live Maestro corner table uses."""
        from PyQt5.QtCore import Qt as _Qt
        from PyQt5.QtWidgets import QApplication
        try:
            from simkit.skill_bridge import read_model_files
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(
                self, "Read from Cadence",
                f"Cannot load the Cadence bridge:\n{exc}",
            )
            return
        # The dialog has no Qt parent (X11 grouped-drag fix) so self.window()
        # returns the dialog itself; reach the main window via the view,
        # which is still embedded in the QMainWindow's tab panel. The bridge
        # walks cwd / $PVT_PROJECT as a fallback when no path is passed, but
        # cwd is the launch dir (often the repo root) — pass the loaded
        # project explicitly so "Read from Cadence" hits the right project.
        kwargs: dict = {}
        main = self._view.window() if self._view is not None else None
        if main is not None and hasattr(main, "current_project_path"):
            pp = main.current_project_path()
            if pp is not None:
                kwargs["pvtproject_path"] = pp
        if main is not None and hasattr(main, "current_session_name"):
            sess = main.current_session_name()
            if sess:
                kwargs["session"] = sess
        # First-time / fresh-machine scenario: no .pvtproject loaded. The
        # bridge would otherwise fail with the cryptic "no .pvtproject found
        # walking up from <cwd>". Show the actionable hint here instead.
        if "pvtproject_path" not in kwargs:
            QMessageBox.information(
                self, "Read from Cadence",
                "No simkit project is loaded yet.\n\n"
                "In the main window, go to File ▸ New Project… — pick "
                "a directory (anywhere you want simkit to store its "
                "files), give it a name, click OK. Then come back to "
                "the Corner Generator and try Read from Cadence again.\n\n"
                "You can also type the model file path or use Browse "
                "to skip this step.",
            )
            return
        QApplication.setOverrideCursor(_Qt.WaitCursor)
        files = None
        err = None
        try:
            files = read_model_files(**kwargs)
        except Exception as exc:  # noqa: BLE001
            err = exc
        finally:
            QApplication.restoreOverrideCursor()
        if files is None:
            QMessageBox.warning(
                self, "Read from Cadence",
                f"Could not read from Cadence:\n{err}\n\n"
                f"You can still type the model file or use Browse.",
            )
            return
        if not files:
            QMessageBox.information(
                self, "Read from Cadence",
                "No model file found in the live Maestro corner table.",
            )
            return
        names = sorted(files)
        if len(names) == 1:
            chosen = names[0]
        else:
            chosen, ok = QInputDialog.getItem(
                self, "Read from Cadence", "Model file:", names, 0, False
            )
            if not ok:
                return
        # Capture the abs path BEFORE setting the line edit so the
        # textChanged-driven section parse can find the file on disk.
        self._model_file_abs = files[chosen].get("file_abs") or chosen
        self._model_file_edit.setText(chosen)   # adds the section column
        # Live-pulled sections (= currently in use in Maestro) get folded
        # into the dropdown as a fallback / hint — parsed-from-file
        # sections take precedence when both are present.
        live_sections = list(files[chosen].get("sections") or [])
        if live_sections and not self._available_sections:
            self._available_sections = live_sections
        if live_sections and QMessageBox.question(
            self, "Read from Cadence",
            f"Found sections: {', '.join(live_sections)}.\n"
            f"Add them as {self._axis_name} levels?",
        ) == QMessageBox.Yes:
            self._seed_sections(live_sections)

    def _seed_sections(self, sections: list[str]) -> None:
        """Replace the grid's level rows with one row per model section."""
        self._table.setRowCount(0)
        for sec in sections:
            r = self._table.rowCount()
            self._table.insertRow(r)
            self._table.setItem(r, 0, QTableWidgetItem(sec))
            if self._has_section:
                self._table.setItem(r, 1, QTableWidgetItem(sec))

    # --- load / dump -----------------------------------------------------
    def load(self, axis: CorrelatedAxis) -> None:
        """Populate the grid from an existing axis."""
        if axis.model_file is not None and self._model_file_edit is not None:
            self._model_file_edit.setText(axis.model_file)  # adds section col
        for member in axis.members:
            c = self._table.columnCount()
            self._table.insertColumn(c)
            self._table.setHorizontalHeaderItem(c, QTableWidgetItem(member))
        for ct in axis.tuples:
            r = self._table.rowCount()
            self._table.insertRow(r)
            self._table.setItem(r, 0, QTableWidgetItem(ct.label))
            if self._has_section:
                self._table.setItem(
                    r, 1, QTableWidgetItem(ct.section or "")
                )
            for c in range(self._member_start(), self._table.columnCount()):
                member = self._table.horizontalHeaderItem(c).text()
                self._table.setItem(
                    r, c, QTableWidgetItem(ct.values.get(member, ""))
                )

    def seed_blank(self, *, with_variable: bool) -> None:
        """Start a fresh grid: one blank level row, optionally one variable."""
        if with_variable:
            self._add_variable(initial="var1")
        self._add_level()

    def level_labels(self) -> list[str]:
        out: list[str] = []
        for r in range(self._table.rowCount()):
            lab = self._cell(r, 0)
            if lab:
                out.append(lab)
        return out

    def is_empty(self) -> bool:
        return self._table.rowCount() == 0 and not self._member_names()

    def _cell(self, row: int, col: int) -> str:
        item = self._table.item(row, col)
        return item.text().strip() if item is not None else ""

    def axis(self) -> Optional[CorrelatedAxis]:
        """Build a CorrelatedAxis from the grid, or warn + return None."""
        import re
        model_file = None
        if self._model_file_edit is not None:
            model_file = self._model_file_edit.text().strip() or None
        members = self._member_names()
        if not members and model_file is None:
            QMessageBox.warning(
                self, self._axis_name,
                f"{self._axis_name}: add a variable column"
                + (" or a model file." if self._allow_model_file else "."),
            )
            return None
        if self._table.rowCount() == 0:
            QMessageBox.warning(
                self, self._axis_name,
                f"{self._axis_name}: add at least one level.",
            )
            return None
        tuples: list[CorrelatedTuple] = []
        seen: set[str] = set()
        for r in range(self._table.rowCount()):
            label = self._cell(r, 0)
            if not label:
                QMessageBox.warning(
                    self, self._axis_name,
                    f"{self._axis_name} row {r + 1}: the level needs a name.",
                )
                return None
            if not re.match(_LEVEL_RE, label):
                QMessageBox.warning(
                    self, self._axis_name,
                    f"{self._axis_name}: level {label!r} must use only "
                    f"letters, digits, and underscores.",
                )
                return None
            if label in seen:
                QMessageBox.warning(
                    self, self._axis_name,
                    f"{self._axis_name}: level {label!r} is used twice.",
                )
                return None
            seen.add(label)
            section: Optional[str] = None
            if model_file is not None:
                section = self._cell(r, 1)
                if not section:
                    QMessageBox.warning(
                        self, self._axis_name,
                        f"{self._axis_name}: level {label!r} needs a section.",
                    )
                    return None
            values: dict[str, str] = {}
            for c in range(self._member_start(), self._table.columnCount()):
                val = self._cell(r, c)
                member = self._table.horizontalHeaderItem(c).text()
                if not val:
                    QMessageBox.warning(
                        self, self._axis_name,
                        f"{self._axis_name}: level {label!r} has no value "
                        f"for {member!r}.",
                    )
                    return None
                values[member] = val
            tuples.append(CorrelatedTuple(
                label=label, values=values, section=section
            ))
        return CorrelatedAxis(
            name=self._axis_name, members=tuple(members),
            tuples=tuple(tuples), model_file=model_file,
        )


class CornerGeneratorDialog(QDialog):
    """The PVT Corner Generator — see the module docstring."""

    def __init__(self, view) -> None:  # view: CornerManagerView
        # No Qt parent at all — passing any parent makes Qt set
        # WM_TRANSIENT_FOR on X11, and several WMs use that to "group" the
        # dialog with its parent (dragging the dialog moves the main window
        # too). ApplicationModal keeps input blocked on the main window so
        # the modal UX is preserved without geometric coupling.
        super().__init__()
        self.setWindowModality(Qt.ApplicationModal)
        self._view = view
        self.setWindowTitle("PVT Corner Generator")
        self.setMinimumSize(980, 660)

        v = QVBoxLayout(self)
        v.addWidget(QLabel(
            "Define your PVT levels, then describe corners as patterns. "
            "Generate expands each pattern into the corner table — a level "
            "that binds 2+ variables splits into its own column; a level "
            "with one variable stays a multi-valued cell."
        ))

        # --- level definitions ------------------------------------------
        v.addWidget(QLabel("<b>1 · Level definitions</b>"))
        grids = QHBoxLayout()
        self._grids: dict[str, _LevelGrid] = {}
        cm = self._view.cornermodel()
        for axis_name in _AXES:
            grid = _LevelGrid(
                axis_name, allow_model_file=(axis_name == "Process"),
                view=self._view if axis_name == "Process" else None,
            )
            existing = cm.correlated_axes.get(axis_name)
            if existing is not None:
                grid.load(existing)
            else:
                grid.seed_blank(with_variable=(axis_name != "Process"))
            self._grids[axis_name] = grid
            grids.addWidget(grid)
        v.addLayout(grids, 1)

        # --- patterns ----------------------------------------------------
        v.addWidget(QLabel(
            "<b>2 · Patterns</b> — one row per corner. Mode is per row "
            "(default below); P/V/T cells double-click for a checkable "
            "dropdown or type \"TT, SS\" directly. Name supports "
            "{mode} {process} {voltage} {temp} tokens — empty defaults "
            "to {mode}. Right-click rows to Enable / Disable."
        ))
        self._patterns = QTableWidget(0, len(_PAT_HEADERS))
        self._patterns.setHorizontalHeaderLabels(list(_PAT_HEADERS))
        hdr = self._patterns.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Stretch)
        # Squeeze the Enabled checkbox column so it doesn't waste a stretch.
        hdr.setSectionResizeMode(_PAT_COL_ENABLED, QHeaderView.ResizeToContents)
        self._patterns.verticalHeader().setDefaultSectionSize(24)
        self._patterns.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._patterns.setSelectionMode(QAbstractItemView.ExtendedSelection)
        # Per-column editor delegates: per-row Mode dropdown + per-axis
        # checkable multi-select. Free-typed text in P/V/T cells still
        # works alongside the picker. QTableWidget does NOT take Python
        # ownership of the delegate (only the C++ parent matters), so we
        # keep references on self to keep them alive.
        self._pattern_delegates: list[QStyledItemDelegate] = []
        mode_delegate = _ModeComboDelegate(
            lambda: sorted(self._view.cornermodel().modes)
        )
        self._pattern_delegates.append(mode_delegate)
        self._patterns.setItemDelegateForColumn(_PAT_COL_MODE, mode_delegate)
        for axis_name, col in _PAT_AXIS_COLS.items():
            d = _MultiPickDelegate(
                lambda an=axis_name: self._grids[an].level_labels()
            )
            self._pattern_delegates.append(d)
            self._patterns.setItemDelegateForColumn(col, d)
        self._patterns.setContextMenuPolicy(Qt.CustomContextMenu)
        self._patterns.customContextMenuRequested.connect(
            self._on_pattern_context_menu
        )
        v.addWidget(self._patterns, 1)

        prow = QHBoxLayout()
        b_add = QPushButton("+ Pattern row")
        b_del = QPushButton("- Pattern row")
        b_add.clicked.connect(lambda: self._add_pattern_row())
        b_del.clicked.connect(self._remove_pattern_row)
        prow.addWidget(b_add)
        prow.addWidget(b_del)
        prow.addStretch(1)
        v.addLayout(prow)

        # --- generate ----------------------------------------------------
        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("Default mode (for new rows):"))
        self._mode_combo = QComboBox()
        self._mode_combo.addItems(sorted(cm.modes))
        bottom.addWidget(self._mode_combo)
        bottom.addStretch(1)
        self._btn_generate = QPushButton("Generate → corner table")
        self._btn_generate.clicked.connect(self._on_generate)
        b_close = QPushButton("Close")
        b_close.clicked.connect(self.reject)
        bottom.addWidget(self._btn_generate)
        bottom.addWidget(b_close)
        v.addLayout(bottom)

        # Seed one editable row so the user has somewhere to type.
        self._add_pattern_row()

    # --- pattern table ---------------------------------------------------
    def _add_pattern_row(self) -> None:
        r = self._patterns.rowCount()
        self._patterns.insertRow(r)
        # Enabled checkbox (default on). Use ItemIsUserCheckable on the
        # item itself rather than a cell widget so right-click selection
        # picks it up like the rest of the row.
        chk = QTableWidgetItem()
        chk.setFlags(
            Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsUserCheckable
        )
        chk.setCheckState(Qt.Checked)
        chk.setTextAlignment(Qt.AlignCenter)
        self._patterns.setItem(r, _PAT_COL_ENABLED, chk)
        # Mode pre-fills from the bottom default-mode combo.
        default_mode = self._mode_combo.currentText() if hasattr(
            self, "_mode_combo"
        ) else ""
        self._patterns.setItem(
            r, _PAT_COL_MODE, QTableWidgetItem(default_mode)
        )
        for c in (_PAT_COL_NAME, _PAT_COL_PROCESS,
                  _PAT_COL_VOLTAGE, _PAT_COL_TEMP):
            self._patterns.setItem(r, c, QTableWidgetItem(""))

    def _remove_pattern_row(self) -> None:
        # Delete every selected row at once, not just the active one — feels
        # more natural with ExtendedSelection. Falls back to the active row
        # when no selection is set.
        rows = self._selected_rows()
        if not rows:
            r = self._patterns.currentRow()
            if r < 0:
                return
            rows = [r]
        for r in sorted(rows, reverse=True):
            self._patterns.removeRow(r)

    def _selected_rows(self) -> list[int]:
        sm = self._patterns.selectionModel()
        if sm is None:
            return []
        return sorted({idx.row() for idx in sm.selectedRows()})

    def _on_pattern_context_menu(self, pos) -> None:
        rows = self._selected_rows()
        # Right-clicking on an unselected row should target that row alone.
        idx = self._patterns.indexAt(pos)
        if idx.isValid() and idx.row() not in rows:
            self._patterns.selectRow(idx.row())
            rows = [idx.row()]
        if not rows:
            return
        menu = QMenu(self._patterns)
        a_enable = QAction(f"Enable ({len(rows)})", menu)
        a_disable = QAction(f"Disable ({len(rows)})", menu)
        a_enable.triggered.connect(lambda: self._set_rows_enabled(rows, True))
        a_disable.triggered.connect(lambda: self._set_rows_enabled(rows, False))
        menu.addAction(a_enable)
        menu.addAction(a_disable)
        menu.exec_(self._patterns.viewport().mapToGlobal(pos))

    def _set_rows_enabled(self, rows: list[int], enabled: bool) -> None:
        for r in rows:
            item = self._patterns.item(r, _PAT_COL_ENABLED)
            if item is not None:
                item.setCheckState(Qt.Checked if enabled else Qt.Unchecked)

    def _row_is_enabled(self, r: int) -> bool:
        item = self._patterns.item(r, _PAT_COL_ENABLED)
        return item is not None and item.checkState() == Qt.Checked

    # --- generate --------------------------------------------------------
    def _on_generate(self) -> None:
        rows = self._read_patterns()
        if not rows:
            QMessageBox.warning(
                self, "Generate",
                "No patterns — fill in at least one row.",
            )
            return
        live = [r for r in rows if r["enabled"]]
        if not live:
            QMessageBox.warning(
                self, "Generate",
                "Every pattern row is disabled — nothing to generate.",
            )
            return

        modeless = [r for r in live if not r["mode"]]
        if modeless:
            QMessageBox.warning(
                self, "Generate",
                f"{len(modeless)} row(s) have no Mode set — pick one "
                f"per row, or set a default mode at the bottom.",
            )
            return

        # Promote the axes referenced by enabled rows into the cornermodel
        # (each axis is upserted exactly once even if many rows reference it).
        referenced = {
            axis for r in live
            for axis, labs in r["selections"].items() if labs
        }
        cm = self._view.cornermodel()
        try:
            for axis_name in _AXES:
                if axis_name not in referenced:
                    continue
                axis = self._grids[axis_name].axis()
                if axis is None:
                    return  # the grid already warned
                if axis_name in cm.correlated_axes:
                    cm = update_correlated_axis(cm, axis)
                else:
                    cm = add_correlated_axis(cm, axis)
        except CornerModelError as exc:
            QMessageBox.warning(self, "Generate — level error", str(exc))
            return

        created: list[str] = []
        failed: list[str] = []
        for row in live:
            mode = row["mode"]
            sels = row["selections"]
            name = _resolve_pattern_name(row["name"], mode, sels)
            axis_selections = [
                (axis, tuple(labs))
                for axis in _AXES
                for labs in [sels.get(axis, [])]
                if labs
            ]
            try:
                cols = generate_pattern_columns(
                    cm, mode, name, axis_selections
                )
            except CornerModelError as exc:
                failed.append(f"{name or '(unnamed)'}: {exc}")
                continue
            for col in cols:
                try:
                    cm = add_column(cm, col)
                    created.append(effective_name(col))
                except CornerModelError as exc:
                    failed.append(f"{effective_name(col)}: {exc}")

        self._view._apply(cm)
        self._report(created, failed)

    def _read_patterns(self) -> list[dict]:
        """One dict per non-blank pattern row::

            {"enabled": bool, "mode": str, "name": str,
             "selections": {"Process": [...], "Voltage": [...], "Temperature": [...]}}
        """
        out: list[dict] = []
        for r in range(self._patterns.rowCount()):
            mode = self._cell(r, _PAT_COL_MODE)
            name = self._cell(r, _PAT_COL_NAME)
            sels = {
                axis: _split_levels(self._cell(r, col))
                for axis, col in _PAT_AXIS_COLS.items()
            }
            if not name and not mode and not any(sels.values()):
                continue  # a wholly blank row
            out.append({
                "enabled": self._row_is_enabled(r),
                "mode": mode,
                "name": name,
                "selections": sels,
            })
        return out

    def _cell(self, row: int, col: int) -> str:
        item = self._patterns.item(row, col)
        return item.text().strip() if item is not None else ""

    def _report(self, created: list[str], failed: list[str]) -> None:
        if created and not failed:
            QMessageBox.information(
                self, "Generate",
                f"Generated {len(created)} corner(s) into the corner table.",
            )
        elif created and failed:
            QMessageBox.warning(
                self, "Generate — partial",
                f"Generated {len(created)} corner(s).\n\n"
                f"{len(failed)} failed:\n" + "\n".join(failed[:10]),
            )
        else:
            QMessageBox.warning(
                self, "Generate — nothing created",
                "No corners were generated:\n" + "\n".join(failed[:10]),
            )


def _split_levels(text: str) -> list[str]:
    """Parse a P/V/T cell — level labels separated by commas or whitespace."""
    raw = text.replace(",", " ").split()
    seen: set[str] = set()
    out: list[str] = []
    for tok in raw:
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def _resolve_pattern_name(
    template: str, mode: str, selections: dict[str, list[str]],
) -> str:
    """Substitute name-template tokens with the row's mode + level picks.

    Tokens: ``{mode}``, ``{process}``, ``{voltage}``, ``{temp}``. Level
    picks are underscore-joined so the result remains a valid identifier
    (``TT, SS`` → ``TT_SS``). Empty template defaults to ``{mode}`` —
    matches the user's "auto-name from corner content" ask; composite-axis
    expansion in :func:`generate_pattern_columns` adds the per-level
    discriminators downstream, so the base name need not encode every
    level itself.
    """
    name = (template or "").strip() or "{mode}"
    return (
        name
        .replace("{mode}", mode)
        .replace("{process}", "_".join(selections.get("Process", [])))
        .replace("{voltage}", "_".join(selections.get("Voltage", [])))
        .replace("{temp}", "_".join(selections.get("Temperature", [])))
    )
