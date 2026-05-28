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
* **Patterns** — a narrow library navigator on the left + a corner
  editor on the right. Each pattern is a *named collection of corners*::

      ┌─ Library ──┬── Editing: Pattern_1 ─────────────────────┐
      │ ☑ Pattern_1│ Name: [ Pattern_1                       ] │
      │ ☑ Worst    │                                            │
      │ ☐ draft    │ ┌─[✓]─Name─Process─Voltage─Temperature─┐  │
      │            │ │  ✓   c1   TT      NV       NT        │  │
      │ [+] [⎘][✕] │ │  ✓   c2   SS      LV       HT        │  │
      │ [Preset…]  │ │  ✓   c3   FF      HV       LT, HT    │  │
      │            │ └──────────────────────────────────────┘  │
      │            │ [+ Corner] [- Corner]                     │
      └────────────┴───────────────────────────────────────────┘

  The library list checkbox enables / disables the whole pattern; the
  per-corner checkbox in the right table enables / disables a single
  corner. ``Presets…`` browses built-in recipes (Standard, Classic
  5-corner) + your own saved presets and appends one as new patterns;
  ``Save as preset…`` stores the selected pattern in your personal
  preset library (``~/.simkit/pattern_presets.json``, see
  :mod:`simkit.gui.pattern_presets`) so it can be re-used in other
  projects. Patterns are mode-agnostic on
  purpose — the bottom dropdown picks the target mode at *Generate*
  time so the same authored pattern can be re-applied to different
  modes. P / V / T cells use an inline checkable combobox (or just
  type ``"TT, SS"`` directly). Corner names support ``{pattern}``
  ``{mode}`` ``{process}`` ``{voltage}`` ``{temp}`` tokens; an empty
  name defaults to ``{mode}`` and composite-axis expansion adds
  per-level discriminators downstream. The library persists with the
  cornermodel (``cm.patterns``), so authored work survives across GUI
  restarts — the dialog rehydrates from there on open and snapshots
  back on close / generate. Legacy single-corner patterns load as a
  one-corner container automatically.

The grids round-trip the cornermodel's ``correlated_axes``; the data layer
and on-disk format are unchanged.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Callable, Optional

from PyQt5.QtCore import QEvent, Qt, QItemSelectionModel
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
    PvtCornerEntry,
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
            enabled=True, name="Standard PVT",
            corners=(
                PvtCornerEntry(
                    enabled=True, name="{mode}_full_PVT",
                    process_levels=("TT", "SS", "FF"),
                    voltage_levels=("NV", "HV", "LV"),
                    temperature_levels=("NT", "HT", "LT"),
                ),
            ),
        ),
    ),
    "Classic 5-corner": (
        PvtPattern(
            enabled=True, name="Classic 5-corner",
            corners=(
                PvtCornerEntry(
                    enabled=True, name="{mode}_TT_NV_NT",
                    process_levels=("TT",), voltage_levels=("NV",),
                    temperature_levels=("NT",),
                ),
                PvtCornerEntry(
                    enabled=True, name="{mode}_SS_LV_HT",
                    process_levels=("SS",), voltage_levels=("LV",),
                    temperature_levels=("HT",),
                ),
                PvtCornerEntry(
                    enabled=True, name="{mode}_FF_HV_LT",
                    process_levels=("FF",), voltage_levels=("HV",),
                    temperature_levels=("LT",),
                ),
                PvtCornerEntry(
                    enabled=True, name="{mode}_SS_HV_HT",
                    process_levels=("SS",), voltage_levels=("HV",),
                    temperature_levels=("HT",),
                ),
                PvtCornerEntry(
                    enabled=True, name="{mode}_FF_LV_LT",
                    process_levels=("FF",), voltage_levels=("LV",),
                    temperature_levels=("LT",),
                ),
            ),
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
        # Keep the popup open across multiple ticks: a plain QComboBox
        # commits + closes on the first item click, which makes a
        # checkable combo feel single-select. Intercept the popup view's
        # mouse-release ourselves — toggle the row and swallow the event
        # so the popup stays open (user closes it by clicking away / Esc).
        self.view().viewport().installEventFilter(self)
        self.setLineEdit(QLineEdit())
        self.set_options(items, current)
        self._model.dataChanged.connect(self._refresh_line_edit)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt API)
        if (
            obj is self.view().viewport()
            and event.type() == QEvent.MouseButtonRelease
        ):
            index = self.view().indexAt(event.pos())
            if index.isValid():
                self._toggle_at(index)
            return True   # consume → the popup does not close
        return super().eventFilter(obj, event)

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


class _MultiPickDelegate(QStyledItemDelegate):
    """Per-cell editor that pops a _CheckableComboBox of the axis's
    currently defined levels. Used by the per-pattern corner table on the
    right side of the library — free-typed text in the cell is preserved."""

    def __init__(self, get_levels: Callable[[], list[str]]) -> None:
        super().__init__()
        self._get_levels = get_levels

    def createEditor(self, parent, option, index):  # noqa: N802
        current = _split_levels(index.data() or "")
        return _CheckableComboBox(self._get_levels(), current, parent)

    def setEditorData(self, editor: "_CheckableComboBox", index) -> None:  # noqa: N802
        editor.lineEdit().setText(index.data() or "")

    def setModelData(  # noqa: N802
        self, editor: "_CheckableComboBox", model, index,
    ) -> None:
        model.setData(index, editor.committed_value())


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


# Corner-table (right-side detail) column layout — one row per corner
# entry inside the currently-selected pattern.
_COR_COL_ENABLED = 0
_COR_COL_NAME = 1
_COR_COL_PROCESS = 2
_COR_COL_VOLTAGE = 3
_COR_COL_TEMP = 4
_COR_HEADERS = ("", "Name", "Process", "Voltage", "Temperature")
_COR_AXIS_COLS = {
    "Process": _COR_COL_PROCESS,
    "Voltage": _COR_COL_VOLTAGE,
    "Temperature": _COR_COL_TEMP,
}


class _PresetPickDialog(QDialog):
    """Pick a preset to load — built-in + user presets in one list —
    or delete a user preset. Result is read via :meth:`result_action`."""

    def __init__(
        self, builtin_names: list[str], user_names: list[str],
        parent: Optional[QWidget] = None,
    ) -> None:
        # Parentless + ApplicationModal, same X11 grouped-drag avoidance as
        # CornerGeneratorDialog.
        super().__init__()
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowTitle("Presets")
        self._action: Optional[str] = None   # "load" | "delete"
        self._kind: Optional[str] = None      # "builtin" | "user"
        self._name: Optional[str] = None

        v = QVBoxLayout(self)
        v.addWidget(QLabel(
            "Built-in and your saved presets. Load appends the preset's "
            "patterns to the library; Delete removes a user preset."
        ))
        self._list = QListWidget()
        for n in builtin_names:
            it = QListWidgetItem(f"[built-in]  {n}")
            it.setData(Qt.UserRole, ("builtin", n))
            self._list.addItem(it)
        for n in user_names:
            it = QListWidgetItem(f"[user]  {n}")
            it.setData(Qt.UserRole, ("user", n))
            self._list.addItem(it)
        if self._list.count():
            self._list.setCurrentRow(0)
        v.addWidget(self._list, 1)

        btns = QHBoxLayout()
        b_load = QPushButton("Load")
        b_delete = QPushButton("Delete (user)")
        b_cancel = QPushButton("Cancel")
        b_load.clicked.connect(lambda: self._finish("load"))
        b_delete.clicked.connect(lambda: self._finish("delete"))
        b_cancel.clicked.connect(self.reject)
        btns.addWidget(b_load)
        btns.addWidget(b_delete)
        btns.addStretch(1)
        btns.addWidget(b_cancel)
        v.addLayout(btns)

    def _current(self) -> Optional[tuple[str, str]]:
        item = self._list.currentItem()
        if item is None:
            return None
        return item.data(Qt.UserRole)

    def _finish(self, action: str) -> None:
        cur = self._current()
        if cur is None:
            self.reject()
            return
        kind, name = cur
        if action == "delete" and kind != "user":
            QMessageBox.information(
                self, "Presets",
                "Only your own (user) presets can be deleted — built-in "
                "presets are read-only.",
            )
            return
        self._action, self._kind, self._name = action, kind, name
        self.accept()

    def result_action(self) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """(action, kind, name) — action is None if cancelled."""
        return self._action, self._kind, self._name


class _PatternLibrary(QWidget):
    """Left narrow list of named patterns + right detail editor showing the
    selected pattern's name and its corners. Each pattern is a *named
    container of corners*; the user adds / removes / edits corners on the
    right, and manages patterns themselves on the left.

    ``level_provider(axis_name)`` returns the current level labels of the
    matching grid — used to populate the corner table's per-axis dropdowns.
    """

    def __init__(
        self, level_provider: Callable[[str], list[str]],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._level_provider = level_provider
        self._patterns: list[PvtPattern] = []
        self._current_index: Optional[int] = None
        self._loading = False

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # --- left: narrow navigator list + library actions -------------
        left_wrap = QWidget()
        left_wrap.setMaximumWidth(220)
        left_wrap.setMinimumWidth(170)
        left = QVBoxLayout(left_wrap)
        left.setContentsMargins(0, 0, 0, 0)
        left.addWidget(QLabel("<b>Library</b>"))
        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._list.currentRowChanged.connect(self._on_current_row_changed)
        self._list.itemChanged.connect(self._on_list_item_changed)
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
        preset_row = QHBoxLayout()
        b_save_preset = QPushButton("Save as preset…")
        b_save_preset.setToolTip(
            "Save the selected pattern to your personal preset library "
            "(~/.simkit) so you can re-use it in other projects."
        )
        b_save_preset.clicked.connect(self._save_as_preset)
        b_presets = QPushButton("Presets…")
        b_presets.setToolTip(
            "Load a built-in or saved preset into this library, or delete "
            "a user preset."
        )
        b_presets.clicked.connect(self._open_presets)
        preset_row.addWidget(b_save_preset)
        preset_row.addWidget(b_presets)
        left.addLayout(preset_row)
        outer.addWidget(left_wrap, 0)

        # --- right: pattern detail = name + corner table ---------------
        self._detail = QGroupBox("Editing")
        right = QVBoxLayout(self._detail)
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name:"))
        self._name_edit = QLineEdit()
        self._name_edit.editingFinished.connect(self._commit_pattern_name)
        name_row.addWidget(self._name_edit, 1)
        right.addLayout(name_row)
        right.addWidget(QLabel(
            "<i>Each row = one corner. Double-click a P/V/T cell for a "
            "checkable dropdown, or type \"TT, SS\" directly. Right-click "
            "rows for Enable / Disable. Names support {pattern} {mode} "
            "{process} {voltage} {temp} tokens.</i>"
        ))
        self._corner_table = QTableWidget(0, len(_COR_HEADERS))
        self._corner_table.setHorizontalHeaderLabels(list(_COR_HEADERS))
        hdr = self._corner_table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Stretch)
        hdr.setSectionResizeMode(
            _COR_COL_ENABLED, QHeaderView.ResizeToContents
        )
        hdr.setSectionResizeMode(_COR_COL_NAME, QHeaderView.Interactive)
        self._corner_table.setColumnWidth(_COR_COL_NAME, 140)
        self._corner_table.verticalHeader().setDefaultSectionSize(24)
        self._corner_table.setSelectionBehavior(
            QAbstractItemView.SelectRows
        )
        self._corner_table.setSelectionMode(
            QAbstractItemView.ExtendedSelection
        )
        # Per-axis multi-select dropdown editor; QTableWidget doesn't own
        # the Python delegate object, so we keep references on self.
        self._corner_delegates: list[QStyledItemDelegate] = []
        for axis_name, col in _COR_AXIS_COLS.items():
            d = _MultiPickDelegate(
                lambda an=axis_name: self._level_provider(an)
            )
            self._corner_delegates.append(d)
            self._corner_table.setItemDelegateForColumn(col, d)
        self._corner_table.itemChanged.connect(self._on_corner_item_changed)
        self._corner_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._corner_table.customContextMenuRequested.connect(
            self._on_corner_context_menu
        )
        right.addWidget(self._corner_table, 1)
        cbtns = QHBoxLayout()
        b_add_c = QPushButton("+ Corner")
        b_del_c = QPushButton("- Corner")
        b_add_c.clicked.connect(self._add_corner)
        b_del_c.clicked.connect(self._remove_selected_corners)
        cbtns.addWidget(b_add_c)
        cbtns.addWidget(b_del_c)
        cbtns.addStretch(1)
        right.addLayout(cbtns)
        self._detail.setEnabled(False)
        outer.addWidget(self._detail, 1)

    # --- public API ------------------------------------------------------
    def load_patterns(self, patterns) -> None:
        self._loading = True
        self._patterns = list(patterns)
        self._list.clear()
        for p in self._patterns:
            self._list.addItem(self._make_list_item(p))
        self._loading = False
        if self._patterns:
            self._list.setCurrentRow(0, QItemSelectionModel.ClearAndSelect)
        else:
            self._load_detail(None)

    def patterns(self) -> tuple:
        return tuple(self._patterns)

    # --- list rendering --------------------------------------------------
    def _list_label(self, p: PvtPattern) -> str:
        n = len(p.corners)
        return f"{p.name or '(unnamed)'}  ({n})"

    def _make_list_item(self, p: PvtPattern) -> QListWidgetItem:
        item = QListWidgetItem(self._list_label(p))
        item.setFlags(
            Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsUserCheckable
        )
        item.setCheckState(Qt.Checked if p.enabled else Qt.Unchecked)
        return item

    def _refresh_list_item(self, index: int) -> None:
        if not 0 <= index < self._list.count():
            return
        p = self._patterns[index]
        self._loading = True
        item = self._list.item(index)
        item.setText(self._list_label(p))
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

    def _on_list_item_changed(self, item: QListWidgetItem) -> None:
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
        # Right-clicking a row makes it the current one, so Rename /
        # Duplicate / Delete act on what the user clicked.
        if idx.isValid():
            self._list.setCurrentRow(
                idx.row(), QItemSelectionModel.ClearAndSelect
            )
        menu = QMenu(self._list)
        if idx.isValid():
            menu.addAction(QAction("Rename…", menu,
                                    triggered=self._rename_current))
            menu.addAction(QAction("Duplicate", menu,
                                    triggered=self._duplicate_current))
            menu.addAction(QAction("Save as preset…", menu,
                                    triggered=self._save_as_preset))
            menu.addAction(QAction("Delete", menu,
                                    triggered=self._delete_current))
            menu.addSeparator()
        menu.addAction(QAction("+ New", menu, triggered=self._new_pattern))
        menu.addAction(QAction("Presets…", menu,
                                triggered=self._open_presets))
        menu.exec_(self._list.viewport().mapToGlobal(pos))

    # --- detail load -----------------------------------------------------
    def _load_detail(self, pattern: Optional[PvtPattern]) -> None:
        self._loading = True
        try:
            if pattern is None:
                self._detail.setTitle("Editing: (no pattern selected)")
                self._detail.setEnabled(False)
                self._name_edit.setText("")
                self._corner_table.setRowCount(0)
                return
            self._detail.setTitle(f"Editing: {pattern.name or '(unnamed)'}")
            self._detail.setEnabled(True)
            self._name_edit.setText(pattern.name)
            self._corner_table.setRowCount(0)
            for c in pattern.corners:
                self._append_corner_row(c)
        finally:
            self._loading = False

    def _append_corner_row(self, corner: PvtCornerEntry) -> None:
        r = self._corner_table.rowCount()
        self._corner_table.insertRow(r)
        chk = QTableWidgetItem()
        chk.setFlags(
            Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsUserCheckable
        )
        chk.setCheckState(Qt.Checked if corner.enabled else Qt.Unchecked)
        chk.setTextAlignment(Qt.AlignCenter)
        self._corner_table.setItem(r, _COR_COL_ENABLED, chk)
        self._corner_table.setItem(
            r, _COR_COL_NAME, QTableWidgetItem(corner.name)
        )
        self._corner_table.setItem(
            r, _COR_COL_PROCESS,
            QTableWidgetItem(", ".join(corner.process_levels)),
        )
        self._corner_table.setItem(
            r, _COR_COL_VOLTAGE,
            QTableWidgetItem(", ".join(corner.voltage_levels)),
        )
        self._corner_table.setItem(
            r, _COR_COL_TEMP,
            QTableWidgetItem(", ".join(corner.temperature_levels)),
        )

    # --- detail commit ---------------------------------------------------
    def _commit_pattern_name(self) -> None:
        if self._loading or self._current_index is None:
            return
        i = self._current_index
        new = self._name_edit.text().strip()
        old = self._patterns[i]
        if old.name == new:
            return
        self._patterns[i] = replace(old, name=new)
        self._refresh_list_item(i)
        self._detail.setTitle(f"Editing: {new or '(unnamed)'}")

    def _on_corner_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or self._current_index is None:
            return
        row = item.row()
        i = self._current_index
        pattern = self._patterns[i]
        if not 0 <= row < len(pattern.corners):
            return
        corner = pattern.corners[row]
        col = item.column()
        if col == _COR_COL_ENABLED:
            new_enabled = item.checkState() == Qt.Checked
            if corner.enabled == new_enabled:
                return
            corner = replace(corner, enabled=new_enabled)
        elif col == _COR_COL_NAME:
            new_name = item.text().strip()
            if corner.name == new_name:
                return
            corner = replace(corner, name=new_name)
        elif col in (_COR_COL_PROCESS, _COR_COL_VOLTAGE, _COR_COL_TEMP):
            new_levels = tuple(_split_levels(item.text()))
            field = {
                _COR_COL_PROCESS: "process_levels",
                _COR_COL_VOLTAGE: "voltage_levels",
                _COR_COL_TEMP: "temperature_levels",
            }[col]
            if getattr(corner, field) == new_levels:
                return
            corner = replace(corner, **{field: new_levels})
        else:
            return
        new_corners = list(pattern.corners)
        new_corners[row] = corner
        self._patterns[i] = replace(pattern, corners=tuple(new_corners))

    def _on_corner_context_menu(self, pos) -> None:
        rows = sorted({
            idx.row()
            for idx in self._corner_table.selectionModel().selectedRows()
        })
        idx = self._corner_table.indexAt(pos)
        if idx.isValid() and idx.row() not in rows:
            self._corner_table.selectRow(idx.row())
            rows = [idx.row()]
        if not rows:
            return
        menu = QMenu(self._corner_table)
        menu.addAction(QAction(
            f"Enable ({len(rows)})", menu,
            triggered=lambda: self._set_corners_enabled(rows, True),
        ))
        menu.addAction(QAction(
            f"Disable ({len(rows)})", menu,
            triggered=lambda: self._set_corners_enabled(rows, False),
        ))
        menu.addSeparator()
        menu.addAction(QAction(
            f"Delete ({len(rows)})", menu,
            triggered=lambda: self._remove_corner_rows(rows),
        ))
        menu.exec_(self._corner_table.viewport().mapToGlobal(pos))

    def _set_corners_enabled(self, rows: list[int], enabled: bool) -> None:
        if self._current_index is None:
            return
        i = self._current_index
        pattern = self._patterns[i]
        new_corners = list(pattern.corners)
        for r in rows:
            if 0 <= r < len(new_corners):
                new_corners[r] = replace(new_corners[r], enabled=enabled)
        self._patterns[i] = replace(pattern, corners=tuple(new_corners))
        # Refresh just the affected checkboxes in the table.
        self._loading = True
        for r in rows:
            item = self._corner_table.item(r, _COR_COL_ENABLED)
            if item is not None:
                item.setCheckState(Qt.Checked if enabled else Qt.Unchecked)
        self._loading = False

    # --- corner table actions -------------------------------------------
    def _add_corner(self) -> None:
        if self._current_index is None:
            self._new_pattern()
            return
        i = self._current_index
        pattern = self._patterns[i]
        n = len(pattern.corners) + 1
        existing = {c.name for c in pattern.corners}
        name = f"c{n}"
        while name in existing:
            n += 1
            name = f"c{n}"
        new = PvtCornerEntry(
            enabled=True, name=name,
            process_levels=(), voltage_levels=(),
            temperature_levels=(),
        )
        self._patterns[i] = replace(
            pattern, corners=pattern.corners + (new,)
        )
        self._loading = True
        self._append_corner_row(new)
        self._loading = False
        self._refresh_list_item(i)

    def _remove_selected_corners(self) -> None:
        rows = sorted({
            idx.row()
            for idx in self._corner_table.selectionModel().selectedRows()
        }, reverse=True)
        if not rows:
            r = self._corner_table.currentRow()
            if r < 0:
                return
            rows = [r]
        self._remove_corner_rows(rows)

    def _remove_corner_rows(self, rows: list[int]) -> None:
        if self._current_index is None:
            return
        i = self._current_index
        pattern = self._patterns[i]
        new_corners = [
            c for j, c in enumerate(pattern.corners) if j not in rows
        ]
        self._patterns[i] = replace(pattern, corners=tuple(new_corners))
        self._loading = True
        for r in sorted(rows, reverse=True):
            self._corner_table.removeRow(r)
        self._loading = False
        self._refresh_list_item(i)

    # --- library actions -------------------------------------------------
    def _next_default_name(self) -> str:
        existing = {p.name for p in self._patterns}
        n = len(self._patterns) + 1
        while f"Pattern_{n}" in existing:
            n += 1
        return f"Pattern_{n}"

    def _new_pattern(self) -> None:
        p = PvtPattern(
            enabled=True, name=self._next_default_name(), corners=(),
        )
        self._patterns.append(p)
        self._list.addItem(self._make_list_item(p))
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
        cur = self._current_index + 1
        self._loading = True
        self._list.clear()
        for p in self._patterns:
            self._list.addItem(self._make_list_item(p))
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
        self._refresh_list_item(i)
        self._load_detail(self._patterns[i])

    def _delete_current(self) -> None:
        rows = sorted({
            idx.row()
            for idx in self._list.selectionModel().selectedRows()
        }, reverse=True)
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
            self._list.addItem(self._make_list_item(p))
        self._loading = False
        if self._patterns:
            self._list.setCurrentRow(
                min(rows[-1], len(self._patterns) - 1),
                QItemSelectionModel.ClearAndSelect,
            )
        else:
            self._current_index = None
            self._load_detail(None)

    def _save_as_preset(self) -> None:
        """Save the currently-selected pattern to the user preset library
        so it can be re-loaded in any project (2026 UX)."""
        from simkit.gui import pattern_presets as pp
        if self._current_index is None:
            QMessageBox.information(
                self, "Save as preset",
                "Select a pattern on the left first.",
            )
            return
        pattern = self._patterns[self._current_index]
        default = pattern.name or "MyPreset"
        name, ok = QInputDialog.getText(
            self, "Save as preset", "Preset name:", text=default,
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        existing = pp.load_user_presets()
        if name in existing and QMessageBox.question(
            self, "Save as preset",
            f"A preset named {name!r} already exists. Overwrite it?",
        ) != QMessageBox.Yes:
            return
        try:
            pp.save_user_preset(name, pattern)
        except OSError as exc:
            QMessageBox.warning(
                self, "Save as preset",
                f"Could not write the preset library:\n{exc}",
            )
            return
        QMessageBox.information(
            self, "Save as preset",
            f"Saved {name!r} to your preset library "
            f"({pp.presets_path()}).",
        )

    def _open_presets(self) -> None:
        """Browse built-in + user presets; load one (appends its patterns)
        or delete a user preset."""
        from simkit.gui import pattern_presets as pp
        user = pp.load_user_presets()
        dlg = _PresetPickDialog(
            builtin_names=sorted(_BUILTIN_PRESETS),
            user_names=sorted(user),
        )
        if dlg.exec_() != QDialog.Accepted:
            return
        action, kind, name = dlg.result_action()
        if action is None or name is None:
            return
        if action == "delete":
            if QMessageBox.question(
                self, "Delete preset",
                f"Delete user preset {name!r}? This removes it from your "
                f"preset library on disk.",
            ) != QMessageBox.Yes:
                return
            try:
                pp.delete_user_preset(name)
            except OSError as exc:
                QMessageBox.warning(
                    self, "Delete preset",
                    f"Could not update the preset library:\n{exc}",
                )
            return
        # action == "load": append the preset's patterns to the library.
        if kind == "builtin":
            patterns = _BUILTIN_PRESETS.get(name, ())
        else:
            pat = user.get(name)
            patterns = (pat,) if pat is not None else ()
        self._append_preset_patterns(patterns)

    def _append_preset_patterns(self, patterns) -> None:
        if not patterns:
            return
        before = len(self._patterns)
        for src in patterns:
            self._patterns.append(src)
            self._list.addItem(self._make_list_item(src))
        self._list.setCurrentRow(before, QItemSelectionModel.ClearAndSelect)
        missing = self._missing_levels_after_load(patterns)
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

    def _missing_levels_after_load(
        self, preset: "tuple[PvtPattern, ...]"
    ) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for axis_name in _AXES:
            available = set(self._level_provider(axis_name))
            referenced: set[str] = set()
            for p in preset:
                for c in p.corners:
                    referenced |= set(getattr(
                        c,
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
            "<b>2 · Patterns</b> — each pattern is a named collection of "
            "corners. Pick a pattern on the left, edit its corners on "
            "the right. Patterns persist with the cornermodel; the "
            "target mode is picked below at Generate time so the same "
            "pattern can be re-applied to different modes."
        ))
        self._library = _PatternLibrary(
            level_provider=lambda an: self._grids[an].level_labels()
        )
        self._library.load_patterns(cm.patterns)
        # Empty library → start with a blank row so the user has somewhere
        # to type immediately (mirrors the old auto-seed behaviour).
        if not cm.patterns:
            self._library._new_pattern()
            self._library._add_corner()
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
        live_pairs: list[tuple[PvtPattern, PvtCornerEntry]] = []
        for p in patterns:
            if not p.enabled:
                continue
            for c in p.corners:
                if c.enabled and not _corner_is_blank(c):
                    live_pairs.append((p, c))
        if not live_pairs:
            QMessageBox.warning(
                self, "Generate",
                "No enabled corner with any level picked — tick at "
                "least one pattern + corner, and give the corner some "
                "levels.",
            )
            return

        # Promote the axes referenced by enabled corners into the cornermodel
        # (each axis is upserted exactly once even if many corners use it).
        referenced = {
            axis_name
            for _p, c in live_pairs
            for axis_name, labs in _corner_axis_iter(c)
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
        for p, c in live_pairs:
            sels = {
                "Process": list(c.process_levels),
                "Voltage": list(c.voltage_levels),
                "Temperature": list(c.temperature_levels),
            }
            name = _resolve_pattern_name(c.name, mode, p.name, sels)
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


def _corner_is_blank(c: PvtCornerEntry) -> bool:
    return (
        not c.name
        and not c.process_levels
        and not c.voltage_levels
        and not c.temperature_levels
    )


def _corner_axis_iter(c: PvtCornerEntry):
    yield ("Process", c.process_levels)
    yield ("Voltage", c.voltage_levels)
    yield ("Temperature", c.temperature_levels)


def _resolve_pattern_name(
    template: str, mode: str, pattern_name: str,
    selections: dict[str, list[str]],
) -> str:
    """Substitute name-template tokens with the corner's mode + level picks
    + the parent pattern's name.

    Tokens: ``{pattern}``, ``{mode}``, ``{process}``, ``{voltage}``,
    ``{temp}``. Level picks are underscore-joined so the result remains a
    valid identifier (``TT, SS`` → ``TT_SS``). Empty template defaults to
    ``{mode}`` — composite-axis expansion in
    :func:`generate_pattern_columns` adds the per-level discriminators
    downstream, so the base name need not encode every level itself.
    """
    name = (template or "").strip() or "{mode}"
    return (
        name
        .replace("{pattern}", pattern_name or "")
        .replace("{mode}", mode)
        .replace("{process}", "_".join(selections.get("Process", [])))
        .replace("{voltage}", "_".join(selections.get("Voltage", [])))
        .replace("{temp}", "_".join(selections.get("Temperature", [])))
    )
