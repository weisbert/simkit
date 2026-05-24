"""PVT Corner Generator — an independent dialog that authors corners by
pattern and imports the result into the corner table (痛点 a / h).

Two halves:

* **Level definitions** — three flexible grids (Process / Voltage /
  Temperature). Each grid is rows = named levels, columns = the variables
  that level sets. A level that controls two or more variables is a
  *composite* level (痛点 h: CT-tuning bound to the process corner, the
  ``.s5p`` inductor file bound to temperature); a level controlling one
  variable is *simple*. Process additionally carries a model file, so its
  levels pick a section.
* **Patterns** — a four-column table (Corner name / Process / Voltage /
  Temperature). Each row crosses the picked levels. On *Generate* each row
  expands via :func:`simkit.corner_model.generate_pattern_columns` — composite
  axes split into one column each, simple axes stay multi-valued — and the
  columns land in the corner table.

The grids round-trip the cornermodel's ``correlated_axes``; the data layer
and on-disk format are unchanged.
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
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


class _LevelGrid(QWidget):
    """One axis's level-definition grid: rows = levels, columns = the
    variables each level sets. Columns are user-added / removed. The Process
    grid additionally carries a model file, which adds a 'section' column."""

    def __init__(
        self, axis_name: str, *, allow_model_file: bool,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._axis_name = axis_name
        self._allow_model_file = allow_model_file
        self._has_section = False

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
        elif not want and self._has_section:
            self._table.removeColumn(1)
            self._has_section = False

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
        from pathlib import Path
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Select model file", str(Path.cwd()),
            "Model files (*.scs *.spi *.cir *.mod);;All files (*)",
        )
        if chosen:
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
        QApplication.setOverrideCursor(_Qt.WaitCursor)
        files = None
        err = None
        try:
            files = read_model_files()
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
        self._model_file_edit.setText(chosen)   # adds the section column
        sections = files[chosen].get("sections") or []
        if sections and QMessageBox.question(
            self, "Read from Cadence",
            f"Found sections: {', '.join(sections)}.\n"
            f"Add them as {self._axis_name} levels?",
        ) == QMessageBox.Yes:
            self._seed_sections(sections)

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


class _LevelPickDialog(QDialog):
    """A small checkable list — pick which levels of an axis a pattern uses."""

    def __init__(
        self, axis_name: str, levels: list[str], current: list[str],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{axis_name} levels")
        v = QVBoxLayout(self)
        v.addWidget(QLabel(f"Tick the {axis_name} levels for this corner:"))
        self._list = QListWidget()
        for lvl in levels:
            item = QListWidgetItem(lvl)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(
                Qt.Checked if lvl in current else Qt.Unchecked
            )
            self._list.addItem(item)
        v.addWidget(self._list)
        bb = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def selected(self) -> list[str]:
        return [
            self._list.item(i).text()
            for i in range(self._list.count())
            if self._list.item(i).checkState() == Qt.Checked
        ]


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
            "<b>2 · Patterns</b> — one row per corner; "
            "double-click a P/V/T cell to pick levels."
        ))
        self._patterns = QTableWidget(0, 4)
        self._patterns.setHorizontalHeaderLabels(
            ["Corner name", "Process", "Voltage", "Temperature"]
        )
        self._patterns.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self._patterns.verticalHeader().setDefaultSectionSize(24)
        self._patterns.cellDoubleClicked.connect(self._on_pattern_double_click)
        v.addWidget(self._patterns, 1)
        self._add_pattern_row()

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

    # --- pattern table ---------------------------------------------------
    def _add_pattern_row(self) -> None:
        r = self._patterns.rowCount()
        self._patterns.insertRow(r)
        for c in range(4):
            self._patterns.setItem(r, c, QTableWidgetItem(""))

    def _remove_pattern_row(self) -> None:
        r = self._patterns.currentRow()
        if r >= 0:
            self._patterns.removeRow(r)

    def _on_pattern_double_click(self, row: int, col: int) -> None:
        if not 1 <= col <= 3:
            return  # the name column is plain text
        axis_name = _AXES[col - 1]
        levels = self._grids[axis_name].level_labels()
        if not levels:
            QMessageBox.information(
                self, axis_name,
                f"Define {axis_name} levels above first.",
            )
            return
        item = self._patterns.item(row, col)
        current = _split_levels(item.text() if item is not None else "")
        dlg = _LevelPickDialog(axis_name, levels, current, self)
        if dlg.exec_() == QDialog.Accepted:
            self._patterns.setItem(
                row, col, QTableWidgetItem(", ".join(dlg.selected()))
            )

    # --- generate --------------------------------------------------------
    def _on_generate(self) -> None:
        mode = self._mode_combo.currentText()
        if not mode:
            QMessageBox.warning(
                self, "Generate",
                "No mode to attach corners to — create a mode in the corner "
                "manager first.",
            )
            return

        rows = self._read_patterns()
        if not rows:
            QMessageBox.warning(
                self, "Generate",
                "No patterns — fill in at least one row.",
            )
            return
        referenced = {
            axis for _name, sels in rows for axis, labs in sels.items() if labs
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
        for name, sels in rows:
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

    def _read_patterns(self) -> list[tuple[str, dict[str, list[str]]]]:
        """(name, {axis: [levels]}) for every non-empty pattern row."""
        out: list[tuple[str, dict[str, list[str]]]] = []
        for r in range(self._patterns.rowCount()):
            name = self._cell(r, 0)
            sels = {
                _AXES[c - 1]: _split_levels(self._cell(r, c))
                for c in (1, 2, 3)
            }
            if not name and not any(sels.values()):
                continue  # a wholly blank row
            out.append((name, sels))
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
