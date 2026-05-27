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
* **Patterns** — a left-list ▸ right-detail library:

      ┌── Library ───────────┬── Editing: <name> ─────────┐
      │  ☑ Pattern_3         │ Name:    Pattern_3         │
      │  ☑ Worst_PVT         │ Process: TT, SSWC, FFest ▼ │
      │  ☐ draft             │ Voltage: NV              ▼ │
      │  …                   │ Temp:    NT, HT          ▼ │
      │  [+ New] [Duplicate] │                            │
      │  [Delete]            │                            │
      │  [Load preset…]      │                            │
      └──────────────────────┴────────────────────────────┘

  Each library entry is one named PVT pattern (= one row in the
  ``cm.patterns`` tuple). The list checkbox enables / disables the
  pattern for Generate; clicking an entry loads it on the right for
  edit. ``Load preset…`` appends built-in PVT recipes (Standard,
  Classic 5-corner) to the library. Patterns are mode-agnostic on
  purpose — the bottom dropdown picks the target mode at *Generate*
  time so the same authored pattern can be re-applied to different
  modes. P / V / T cells use an inline checkable combobox (or just
  type ``"TT, SS"`` directly). Corner name supports ``{mode}``
  ``{process}`` ``{voltage}`` ``{temp}`` tokens; an empty name defaults
  to ``{mode}`` and composite-axis expansion adds per-level
  discriminators downstream. The library persists with the cornermodel
  (``cm.patterns``), so authored work survives across GUI restarts —
  the dialog rehydrates from there on open and snapshots back on close
  / generate.

The grids round-trip the cornermodel's ``correlated_axes``; the data layer
and on-disk format are unchanged.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Callable, Optional

from PyQt5.QtCore import Qt, QItemSelectionModel
from PyQt5.QtGui import QStandardItem, QStandardItemModel
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QAction,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
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
    PvtPattern,
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

# A few built-in pattern presets, surfaced by the Library's "Load preset…"
# button. Loading a preset APPENDS its patterns to the project's library;
# the level names referenced here (TT/SS/FF/NV/HV/LV/NT/HT/LT) follow the
# common PVT shorthand — the user is expected to have defined matching
# levels in the level grids above (the Library warns if any are missing).
_BUILTIN_PRESETS: "dict[str, tuple[PvtPattern, ...]]" = {
    "Standard PVT (3×3×3)": (
        PvtPattern(
            enabled=True, name="{mode}_full_PVT",
            process_levels=("TT", "SS", "FF"),
            voltage_levels=("NV", "HV", "LV"),
            temperature_levels=("NT", "HT", "LT"),
        ),
    ),
    "Classic 5-corner": (
        PvtPattern(
            enabled=True, name="{mode}_TT_NV_NT",
            process_levels=("TT",), voltage_levels=("NV",),
            temperature_levels=("NT",),
        ),
        PvtPattern(
            enabled=True, name="{mode}_SS_LV_HT",
            process_levels=("SS",), voltage_levels=("LV",),
            temperature_levels=("HT",),
        ),
        PvtPattern(
            enabled=True, name="{mode}_FF_HV_LT",
            process_levels=("FF",), voltage_levels=("HV",),
            temperature_levels=("LT",),
        ),
        PvtPattern(
            enabled=True, name="{mode}_SS_HV_HT",
            process_levels=("SS",), voltage_levels=("HV",),
            temperature_levels=("HT",),
        ),
        PvtPattern(
            enabled=True, name="{mode}_FF_LV_LT",
            process_levels=("FF",), voltage_levels=("LV",),
            temperature_levels=("LT",),
        ),
    ),
}

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
    \"TT, SS\" works the same as ticking TT and SS in the popup.

    ``refresh_hook`` (optional) is called each time the dropdown is about
    to open and returns the current item list — this lets a long-lived
    combo (e.g. embedded in a form) re-pull the freshest level list from
    the corresponding grid without being recreated."""

    def __init__(
        self, items: list[str], current: list[str],
        parent: Optional[QWidget] = None,
        refresh_hook: "Optional[Callable[[], list[str]]]" = None,
    ):
        super().__init__(parent)
        self.setEditable(True)
        self._model: QStandardItemModel = QStandardItemModel(self)
        self.setModel(self._model)
        self._refresh_hook = refresh_hook
        self.view().pressed.connect(self._toggle_at)
        self.setLineEdit(QLineEdit())
        self.set_options(items, current)
        self._model.dataChanged.connect(self._refresh_line_edit)

    def set_options(self, items: list[str], current: list[str]) -> None:
        """Reseed the dropdown items, preserving ``current`` checks. The
        line edit's free-typed text is overwritten with the comma-joined
        ``current`` list — call this when loading a fresh pattern, not
        for in-place refresh."""
        current_set = set(current)
        self._model.blockSignals(True)
        self._model.clear()
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
        self._model.blockSignals(False)
        self.lineEdit().setText(", ".join(current))

    def showPopup(self) -> None:  # noqa: N802 (Qt API)
        # Refresh from the upstream provider (= the level grid) so the
        # dropdown always reflects the current level definitions, even
        # if the user added/removed/renamed levels since opening the
        # dialog. The currently checked items are preserved by name.
        if self._refresh_hook is not None:
            current = _split_levels(self.lineEdit().text())
            items = self._refresh_hook()
            # Keep extras the user typed but the grid no longer has —
            # we still surface them in the dropdown so the user can
            # uncheck explicitly.
            for c in current:
                if c not in items:
                    items = list(items) + [c]
            self._model.blockSignals(True)
            self._model.clear()
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
            self._model.blockSignals(False)
        super().showPopup()

    def _toggle_at(self, index) -> None:
        item = self._model.itemFromIndex(index)
        if item is None:
            return
        item.setCheckState(
            Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked
        )

    def _refresh_line_edit(self, *_args) -> None:
        # The user toggled a checkbox — recompute the comma-joined text.
        self.lineEdit().setText(", ".join(self.checked_labels()))

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


class _PatternLibrary(QWidget):
    """Left list of named patterns + right detail editor for the selected
    one. The user authors / copies / deletes patterns via the list, and
    edits the active pattern's P / V / T cells on the right. Patterns live
    in this widget's in-memory list until the dialog persists them.

    ``level_provider(axis_name)`` returns the current level labels of the
    matching grid — used to refresh the right-side dropdowns when the
    user opens them.
    """

    def __init__(
        self, level_provider: Callable[[str], list[str]],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._level_provider = level_provider
        self._patterns: list[PvtPattern] = []
        self._current_index: Optional[int] = None
        self._loading = False   # gate re-entry during programmatic loads

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # --- left: the library list + action buttons --------------------
        left_wrap = QWidget()
        left = QVBoxLayout(left_wrap)
        left.setContentsMargins(0, 0, 0, 0)
        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._list.currentRowChanged.connect(self._on_current_row_changed)
        self._list.itemChanged.connect(self._on_item_changed)
        self._list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list.customContextMenuRequested.connect(
            self._on_list_context_menu
        )
        left.addWidget(self._list, 1)
        btn_row1 = QHBoxLayout()
        for label, slot in (
            ("+ New", self._new_pattern),
            ("Duplicate", self._duplicate_current),
            ("Delete", self._delete_current),
        ):
            b = QPushButton(label)
            b.clicked.connect(slot)
            btn_row1.addWidget(b)
        left.addLayout(btn_row1)
        btn_row2 = QHBoxLayout()
        b_preset = QPushButton("Load preset…")
        b_preset.clicked.connect(self._load_preset)
        btn_row2.addWidget(b_preset)
        btn_row2.addStretch(1)
        left.addLayout(btn_row2)
        outer.addWidget(left_wrap, 2)

        # --- right: the detail editor for the selected pattern ----------
        self._detail = QGroupBox("Editing")
        form = QFormLayout(self._detail)
        self._name_edit = QLineEdit()
        self._name_edit.editingFinished.connect(self._commit_name)
        form.addRow("Name:", self._name_edit)
        self._combos: dict[str, _CheckableComboBox] = {}
        for axis_name in _AXES:
            cb = _CheckableComboBox(
                self._level_provider(axis_name), [],
                refresh_hook=lambda an=axis_name: self._level_provider(an),
            )
            cb.lineEdit().editingFinished.connect(
                lambda an=axis_name: self._commit_axis(an)
            )
            # Toggling a checkbox auto-updates the line edit text — also a
            # commit point (otherwise the in-memory pattern stays stale
            # until the user moves focus away).
            cb._model.dataChanged.connect(
                lambda *_a, an=axis_name: self._commit_axis(an)
            )
            self._combos[axis_name] = cb
            form.addRow(f"{axis_name}:", cb)
        self._detail.setEnabled(False)
        outer.addWidget(self._detail, 3)

    # --- public API ------------------------------------------------------
    def load_patterns(self, patterns) -> None:
        """Hydrate the library from a tuple of PvtPattern (cm.patterns).
        Selects the first row so the detail editor is immediately useful."""
        self._loading = True
        self._patterns = list(patterns)
        self._list.clear()
        for p in self._patterns:
            self._list.addItem(self._make_item(p))
        self._loading = False
        if self._patterns:
            self._list.setCurrentRow(0, QItemSelectionModel.ClearAndSelect)
        else:
            self._load_detail(None)

    def patterns(self) -> tuple:
        """Snapshot the current library as a PvtPattern tuple for saving."""
        return tuple(self._patterns)

    # --- list rendering --------------------------------------------------
    def _make_item(self, pattern: PvtPattern) -> QListWidgetItem:
        item = QListWidgetItem(pattern.name or "(unnamed)")
        item.setFlags(
            Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsUserCheckable
        )
        item.setCheckState(Qt.Checked if pattern.enabled else Qt.Unchecked)
        return item

    def _refresh_item(self, index: int) -> None:
        if not 0 <= index < self._list.count():
            return
        p = self._patterns[index]
        self._loading = True
        item = self._list.item(index)
        item.setText(p.name or "(unnamed)")
        item.setCheckState(Qt.Checked if p.enabled else Qt.Unchecked)
        self._loading = False

    # --- list events -----------------------------------------------------
    def _on_current_row_changed(self, row: int) -> None:
        if self._loading:
            return
        if 0 <= row < len(self._patterns):
            self._current_index = row
            self._load_detail(self._patterns[row])
        else:
            self._current_index = None
            self._load_detail(None)

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        if self._loading:
            return
        row = self._list.row(item)
        if not 0 <= row < len(self._patterns):
            return
        new_enabled = item.checkState() == Qt.Checked
        old = self._patterns[row]
        if old.enabled != new_enabled:
            self._patterns[row] = replace(old, enabled=new_enabled)

    def _on_list_context_menu(self, pos) -> None:
        idx = self._list.indexAt(pos)
        menu = QMenu(self._list)
        if idx.isValid():
            menu.addAction(QAction("Duplicate", menu,
                                    triggered=self._duplicate_current))
            menu.addAction(QAction("Rename…", menu,
                                    triggered=self._rename_current))
            menu.addAction(QAction("Delete", menu,
                                    triggered=self._delete_current))
            menu.addSeparator()
        menu.addAction(QAction("+ New", menu, triggered=self._new_pattern))
        menu.addAction(QAction("Load preset…", menu,
                                triggered=self._load_preset))
        menu.exec_(self._list.viewport().mapToGlobal(pos))

    # --- detail load + commit -------------------------------------------
    def _load_detail(self, pattern: Optional[PvtPattern]) -> None:
        self._loading = True
        try:
            if pattern is None:
                self._detail.setTitle("Editing: (no pattern selected)")
                self._detail.setEnabled(False)
                self._name_edit.setText("")
                for cb in self._combos.values():
                    cb.set_options(cb._refresh_hook() if cb._refresh_hook else [], [])
                return
            self._detail.setTitle(f"Editing: {pattern.name or '(unnamed)'}")
            self._detail.setEnabled(True)
            self._name_edit.setText(pattern.name)
            self._combos["Process"].set_options(
                self._level_provider("Process"),
                list(pattern.process_levels),
            )
            self._combos["Voltage"].set_options(
                self._level_provider("Voltage"),
                list(pattern.voltage_levels),
            )
            self._combos["Temperature"].set_options(
                self._level_provider("Temperature"),
                list(pattern.temperature_levels),
            )
        finally:
            self._loading = False

    def _commit_name(self) -> None:
        if self._loading or self._current_index is None:
            return
        new_name = self._name_edit.text().strip()
        i = self._current_index
        old = self._patterns[i]
        if old.name == new_name:
            return
        self._patterns[i] = replace(old, name=new_name)
        self._refresh_item(i)
        self._detail.setTitle(f"Editing: {new_name or '(unnamed)'}")

    def _commit_axis(self, axis_name: str) -> None:
        if self._loading or self._current_index is None:
            return
        cb = self._combos[axis_name]
        levels = tuple(_split_levels(cb.committed_value()))
        i = self._current_index
        old = self._patterns[i]
        field = {
            "Process": "process_levels",
            "Voltage": "voltage_levels",
            "Temperature": "temperature_levels",
        }[axis_name]
        if getattr(old, field) == levels:
            return
        self._patterns[i] = replace(old, **{field: levels})

    # --- library actions -------------------------------------------------
    def _next_default_name(self) -> str:
        existing = {p.name for p in self._patterns}
        n = len(self._patterns) + 1
        while f"Pattern_{n}" in existing:
            n += 1
        return f"Pattern_{n}"

    def _new_pattern(self) -> None:
        p = PvtPattern(
            enabled=True, name=self._next_default_name(),
            process_levels=(), voltage_levels=(),
            temperature_levels=(),
        )
        self._patterns.append(p)
        self._list.addItem(self._make_item(p))
        self._list.setCurrentRow(
            len(self._patterns) - 1,
            QItemSelectionModel.ClearAndSelect,
        )

    def _duplicate_current(self) -> None:
        if self._current_index is None:
            return
        src = self._patterns[self._current_index]
        existing = {p.name for p in self._patterns}
        new_name = src.name + "_copy" if src.name else self._next_default_name()
        n = 2
        while new_name in existing:
            new_name = f"{src.name}_copy{n}"
            n += 1
        dup = replace(src, name=new_name)
        self._patterns.insert(self._current_index + 1, dup)
        # Reload list to keep indices straight.
        cur = self._current_index + 1
        self._loading = True
        self._list.clear()
        for p in self._patterns:
            self._list.addItem(self._make_item(p))
        self._loading = False
        self._list.setCurrentRow(cur, QItemSelectionModel.ClearAndSelect)

    def _rename_current(self) -> None:
        if self._current_index is None:
            return
        i = self._current_index
        old = self._patterns[i]
        name, ok = QInputDialog.getText(
            self, "Rename pattern", "New name:", text=old.name,
        )
        if not ok:
            return
        self._patterns[i] = replace(old, name=name.strip())
        self._refresh_item(i)
        # Re-load detail to refresh title + name edit.
        self._load_detail(self._patterns[i])

    def _delete_current(self) -> None:
        rows = sorted(
            {idx.row() for idx in self._list.selectionModel().selectedRows()},
            reverse=True,
        )
        if not rows and self._current_index is not None:
            rows = [self._current_index]
        if not rows:
            return
        if QMessageBox.question(
            self, "Delete pattern(s)",
            f"Delete {len(rows)} pattern(s)? This cannot be undone "
            f"(re-open the dialog to load the last saved state).",
        ) != QMessageBox.Yes:
            return
        for r in rows:
            del self._patterns[r]
        self._loading = True
        self._list.clear()
        for p in self._patterns:
            self._list.addItem(self._make_item(p))
        self._loading = False
        if self._patterns:
            self._list.setCurrentRow(
                min(rows[-1], len(self._patterns) - 1),
                QItemSelectionModel.ClearAndSelect,
            )
        else:
            self._current_index = None
            self._load_detail(None)

    def _load_preset(self) -> None:
        names = sorted(_BUILTIN_PRESETS)
        if not names:
            return
        chosen, ok = QInputDialog.getItem(
            self, "Load preset",
            "Append the patterns from this preset to your library:",
            names, 0, False,
        )
        if not ok:
            return
        before = len(self._patterns)
        for src in _BUILTIN_PRESETS[chosen]:
            self._patterns.append(src)
            self._list.addItem(self._make_item(src))
        if len(self._patterns) > before:
            self._list.setCurrentRow(before, QItemSelectionModel.ClearAndSelect)
            # Friendly nudge if the preset references levels the user has
            # not defined yet — generation would fail otherwise.
            missing = self._missing_levels_after_load(
                _BUILTIN_PRESETS[chosen]
            )
            if missing:
                QMessageBox.information(
                    self, "Load preset",
                    "Preset loaded. The level grid(s) are missing some "
                    "labels this preset references — add them before "
                    "generating:\n\n" + "\n".join(
                        f"  • {axis}: {', '.join(sorted(labels))}"
                        for axis, labels in missing.items()
                    ),
                )

    def _missing_levels_after_load(self, preset) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for axis_name in _AXES:
            available = set(self._level_provider(axis_name))
            referenced: set[str] = set()
            for p in preset:
                referenced |= set(getattr(
                    p,
                    {"Process": "process_levels",
                     "Voltage": "voltage_levels",
                     "Temperature": "temperature_levels"}[axis_name],
                ))
            missing = sorted(referenced - available)
            if missing:
                out[axis_name] = missing
        return out


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
            "<b>2 · Patterns</b> — your library of named PVT patterns. "
            "Pick one on the left to edit on the right. + New / Duplicate "
            "/ Delete / Load preset… manage the library; each item's "
            "checkbox enables it for Generate. Name supports {mode} "
            "{process} {voltage} {temp} tokens — empty defaults to "
            "{mode}. Patterns persist with the cornermodel — the target "
            "mode is picked below at Generate time so the same pattern "
            "can be re-applied to different modes."
        ))
        self._library = _PatternLibrary(
            level_provider=lambda an: self._grids[an].level_labels()
        )
        self._library.load_patterns(cm.patterns)
        # Empty library → start with a blank row so the user has somewhere
        # to type immediately (mirrors the old auto-seed behaviour).
        if not cm.patterns:
            self._library._new_pattern()
        v.addWidget(self._library, 1)

        # --- generate ----------------------------------------------------
        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("Target mode:"))
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

    # --- generate --------------------------------------------------------
    def _on_generate(self) -> None:
        mode = self._mode_combo.currentText()
        if not mode:
            QMessageBox.warning(
                self, "Generate",
                "No mode picked — create a mode in the corner manager, "
                "then re-open this dialog.",
            )
            return
        patterns = self._library.patterns()
        live = [p for p in patterns if p.enabled and not _pattern_is_blank(p)]
        if not live:
            QMessageBox.warning(
                self, "Generate",
                "No enabled pattern with any level picked — tick at "
                "least one pattern and give it some levels.",
            )
            return

        # Promote the axes referenced by enabled patterns into the cornermodel
        # (each axis is upserted exactly once even if many patterns use it).
        referenced = {
            axis_name
            for p in live
            for axis_name, labs in _pattern_axis_iter(p)
            if labs
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

        # Persist the library — Generate is a commit point.
        cm = replace(cm, patterns=patterns)

        created: list[str] = []
        failed: list[str] = []
        for p in live:
            sels = {
                "Process": list(p.process_levels),
                "Voltage": list(p.voltage_levels),
                "Temperature": list(p.temperature_levels),
            }
            name = _resolve_pattern_name(p.name, mode, sels)
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

    # --- persistence -----------------------------------------------------
    def _persist_patterns_silently(self) -> None:
        """Snapshot the library into the cornermodel and route it through
        the view's apply hook — that fires _persist_cornermodel on the
        main window, so authored patterns survive a GUI restart."""
        cm = self._view.cornermodel()
        snapshot = self._library.patterns()
        if snapshot == cm.patterns:
            return
        self._view._apply(replace(cm, patterns=snapshot))

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        self._persist_patterns_silently()
        super().closeEvent(event)

    def reject(self) -> None:
        self._persist_patterns_silently()
        super().reject()

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


def _pattern_is_blank(p: PvtPattern) -> bool:
    return (
        not p.name
        and not p.process_levels
        and not p.voltage_levels
        and not p.temperature_levels
    )


def _pattern_axis_iter(p: PvtPattern):
    yield ("Process", p.process_levels)
    yield ("Voltage", p.voltage_levels)
    yield ("Temperature", p.temperature_levels)


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
