"""CornerManagerView — Phase 5 corner-manager view (spec §7).

Layout follows Cadence's native corner manager: a left **modes panel** and a
central **corner table** (variables as rows, corners as columns). Editing a
register value in the modes panel is the pain-point-b global edit — every
column referencing that mode re-materialises at once.

The view is self-contained and bridge-free: live pull / push are surfaced as
signals (:pyattr:`pull_requested` / :pyattr:`push_requested`) for the owning
window to route. It always holds a valid cornermodel (an empty one when the
project has none yet), so the Corners tab is usable with no load step.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace as _dc_replace
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
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
    QShortcut,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtCore import Qt

from simkit.corner_model import (
    Column,
    CornerModel,
    CornerModelError,
    ModelEntry,
    PvtTemplate,
    TemplateColumn,
    add_column,
    add_mode,
    add_pvt_template,
    add_run_set,
    apply_run_set,
    apply_template,
    check_cornermodel,
    column_models,
    delete_column,
    effective_name,
    export_library,
    import_library,
    library_to_dict,
    load_library,
    mode_from_column,
    move_column,
    reclassify_mode,
    rename_column,
    rename_variable,
    reorder_columns,
    remove_variable,
    run_set_membership,
    set_column_enabled,
    set_mode_var,
    set_pvt_var,
    set_var_order,
    unbind_template,
)
from simkit.gui.corner_filter import MENU_ORDER
from simkit.gui.corner_model_table import CornerModelTableModel


class CornerManagerView(QWidget):
    """The corner manager — modes panel + corner table."""

    pull_requested = pyqtSignal()
    push_requested = pyqtSignal(object)        # current CornerModel
    cornermodel_edited = pyqtSignal(object)    # CornerModel — owner persists

    def __init__(
        self, model: CornerModel, profile: object = None,
        source_path: object = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._cm = model
        self._profile = profile   # PvtProfile | None (Stage 6)
        self._source_path = source_path  # Path | None — where to persist
        self._loading_mode_vars = False
        self._reordering = False
        self._set_filter: Optional[str] = None
        self._build_ui()
        self._refresh_side_panels()
        self._refresh_check_status()
        self._apply_filters()

    def profile(self) -> object:
        """The bound PVT profile (or None) — main_window reads it for push."""
        return self._profile

    def source_path(self) -> object:
        """The .cornermodel.json this view is backed by (or None)."""
        return self._source_path

    # --- construction ----------------------------------------------------

    def _title_text(self) -> str:
        return f"Corner Manager — {self._cm.name}"

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        top = QHBoxLayout()
        self.title_label = QLabel(self._title_text())
        top.addWidget(self.title_label)
        top.addStretch(1)
        # Editor launchers — each opens a focused pop-up so the corner table
        # itself stays the subject of the view (the setup controls are not
        # walls on the side any more).
        self.btn_modes = QPushButton("Modes…")
        self.btn_modes.setToolTip(
            "Define operating modes (register configurations) and edit "
            "their registers. A variant is just a mode derived from "
            "another — use New Mode ▸ from an existing mode."
        )
        self.btn_templates = QPushButton("Corner Sets…")
        self.btn_templates.setToolTip(
            "A Corner Set is a reusable list of PVT corner columns. "
            "Design it once, then apply it to every mode — no need to "
            "rebuild the same corners per mode."
        )
        self.btn_run_sets = QPushButton("Run Sets…")
        self.btn_run_sets.setToolTip(
            "Manage run sets — named cross-mode corner selections."
        )
        for b in (self.btn_modes, self.btn_templates, self.btn_run_sets):
            top.addWidget(b)
        self.btn_new_column = QPushButton("New Column")
        self.btn_new_column.setToolTip(
            "Add one corner column to a mode (a PVT label + per-column "
            "PVT variables and process model files)."
        )
        self.btn_pull = QPushButton("Pull")
        self.btn_pull.setToolTip(
            "Pull the current corners from the live Maestro session. If "
            "this corner model is empty, the pull seeds it."
        )
        self.btn_push = QPushButton("Push")
        self.btn_push.setToolTip(
            "Materialise this corner model and push the corners to the "
            "live Maestro session (replaces the Maestro corner table)."
        )
        for b in (self.btn_new_column, self.btn_pull, self.btn_push):
            top.addWidget(b)
        outer.addLayout(top)

        hint = QHBoxLayout()
        hint.addWidget(QLabel(
            "Right-click a corner or a variable row for actions; "
            "type in a Filter cell to filter."
        ))
        hint.addStretch(1)
        self.btn_move_up = QPushButton("▲ Move Up")
        self.btn_move_up.setToolTip(
            "Move the selected Design Variable row up one position."
        )
        self.btn_move_down = QPushButton("▼ Move Down")
        self.btn_move_down.setToolTip(
            "Move the selected Design Variable row down one position."
        )
        self.btn_reorder_corners = QPushButton("Reorder Corners…")
        self.btn_reorder_corners.setToolTip(
            "Open a list to reorder the corner columns."
        )
        self.btn_clear_filters = QPushButton("Clear filters")
        self.btn_clear_filters.setToolTip(
            "Clear every embedded filter cell and the run-set filter."
        )
        for b in (self.btn_move_up, self.btn_move_down,
                  self.btn_reorder_corners, self.btn_clear_filters):
            hint.addWidget(b)
        outer.addLayout(hint)

        # The corner table is the subject of the view — it fills the body.
        # Its filter frame (row 0, columns 0-1) is woven into the grid.
        self.table = QTableView()
        self.table_model = CornerModelTableModel(
            self._cm, self._profile, self
        )
        self.table.setModel(self.table_model)
        vheader = self.table.verticalHeader()
        vheader.setDefaultSectionSize(24)
        # The row header is a thin drag gutter — grab it to reorder Design
        # Variable rows. Variable names still live in column 0.
        vheader.setFixedWidth(18)
        vheader.setSectionsMovable(True)
        vheader.setToolTip("Drag a row to reorder Design Variables")
        # #3.4 — columns are interactively resizable.
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setDefaultSectionSize(96)
        # Grab a corner name to drag the whole corner column (2026 UX item 10).
        header.setSectionsMovable(True)
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        outer.addWidget(self.table, 1)

        self.check_label = QLabel()
        outer.addWidget(self.check_label)

        # Editor pop-ups — built once, hidden until a toolbar button raises
        # them; non-modal so corner-table edits stay live behind.
        self._build_modes_dialog()
        self._build_templates_dialog()
        self._build_run_sets_dialog()

        self.btn_modes.clicked.connect(
            lambda: self._open_dialog(self._modes_dialog))
        self.btn_templates.clicked.connect(
            lambda: self._open_dialog(self._templates_dialog))
        self.btn_run_sets.clicked.connect(
            lambda: self._open_dialog(self._run_sets_dialog))

        self.modes_list.currentItemChanged.connect(self._on_mode_selected)
        self.mode_vars.itemChanged.connect(self._on_mode_var_changed)
        self.table_model.cornermodelChanged.connect(self._on_table_edited)
        self.btn_new_mode.clicked.connect(self._on_new_mode)
        self.btn_edit_mode.clicked.connect(self._on_edit_mode)
        self.btn_new_column.clicked.connect(self._on_new_column)
        self.btn_new_template.clicked.connect(self._on_new_template)
        self.btn_apply_template.clicked.connect(self._on_apply_template)
        self.btn_unbind_template.clicked.connect(self._on_unbind_template)
        self.btn_new_run_set.clicked.connect(self._on_new_run_set)
        self.btn_apply_run_set.clicked.connect(self._on_apply_run_set)
        self.btn_filter_set.clicked.connect(self._on_filter_set)
        self.btn_clear_filters.clicked.connect(self._on_clear_all_filters)
        self.table_model.filtersChanged.connect(self._apply_filters)
        self.table.customContextMenuRequested.connect(
            self._on_table_context_menu
        )
        self.btn_move_up.clicked.connect(lambda: self._move_selected_row(-1))
        self.btn_move_down.clicked.connect(
            lambda: self._move_selected_row(1)
        )
        self.btn_reorder_corners.clicked.connect(self._on_reorder_corners)
        header.sectionMoved.connect(self._on_section_moved)
        vheader.sectionMoved.connect(self._on_row_section_moved)
        header.sectionDoubleClicked.connect(self._on_header_double_clicked)
        header.customContextMenuRequested.connect(
            self._on_header_context_menu
        )
        copy_sc = QShortcut(QKeySequence.Copy, self.table)
        copy_sc.setContext(Qt.WidgetWithChildrenShortcut)
        copy_sc.activated.connect(self._copy_selection)
        paste_sc = QShortcut(QKeySequence.Paste, self.table)
        paste_sc.setContext(Qt.WidgetWithChildrenShortcut)
        paste_sc.activated.connect(self._paste_selection)
        self.btn_export_lib.clicked.connect(self._on_export_library)
        self.btn_import_lib.clicked.connect(self._on_import_library)
        self.btn_pull.clicked.connect(self.pull_requested.emit)
        self.btn_push.clicked.connect(
            lambda: self.push_requested.emit(self._cm)
        )

    # --- editor pop-ups --------------------------------------------------

    def _open_dialog(self, dialog: QDialog) -> None:
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _build_modes_dialog(self) -> None:
        self._modes_dialog = QDialog(self)
        self._modes_dialog.setWindowTitle("Modes")
        self._modes_dialog.resize(420, 500)
        v = QVBoxLayout(self._modes_dialog)
        v.addWidget(QLabel(
            "A mode is a named register configuration (e.g. BT_2G_RX). "
            "Every corner column belongs to a mode; edit a register here "
            "and all of that mode's columns update at once."
        ))
        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("Modes"))
        hdr.addStretch(1)
        self.btn_new_mode = QPushButton("New Mode")
        self.btn_new_mode.setToolTip(
            "Define a new mode — from scratch, from a corner column, or "
            "derived from an existing mode (the old 'variant')."
        )
        self.btn_edit_mode = QPushButton("Edit Mode…")
        self.btn_edit_mode.setToolTip(
            "Re-classify which variables are registers vs PVT, and "
            "add / remove registers, for the selected mode."
        )
        hdr.addWidget(self.btn_new_mode)
        hdr.addWidget(self.btn_edit_mode)
        v.addLayout(hdr)
        self.modes_list = QListWidget()
        v.addWidget(self.modes_list)
        v.addWidget(QLabel(
            "Registers (edit a value — every column of this mode syncs)"
        ))
        self.mode_vars = QTableWidget(0, 2)
        self.mode_vars.setHorizontalHeaderLabels(["Register", "Value"])
        self.mode_vars.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.mode_vars.verticalHeader().setDefaultSectionSize(24)
        v.addWidget(self.mode_vars)

    def _build_templates_dialog(self) -> None:
        self._templates_dialog = QDialog(self)
        self._templates_dialog.setWindowTitle("Corner Sets")
        self._templates_dialog.resize(440, 420)
        v = QVBoxLayout(self._templates_dialog)
        v.addWidget(QLabel(
            "A Corner Set is a reusable list of PVT corner columns "
            "(TT, SS_1, FF_1, …). Author it once, then apply it to every "
            "mode so you do not rebuild the same corners per mode."
        ))
        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("Corner Sets"))
        hdr.addStretch(1)
        self.btn_new_template = QPushButton("New Corner Set")
        self.btn_new_template.setToolTip(
            "Author a reusable list of PVT corner columns."
        )
        hdr.addWidget(self.btn_new_template)
        v.addLayout(hdr)
        self.templates_list = QListWidget()
        v.addWidget(self.templates_list)
        tmpl_btns = QHBoxLayout()
        self.btn_apply_template = QPushButton("Apply to a mode")
        self.btn_unbind_template = QPushButton("Unbind")
        tmpl_btns.addWidget(self.btn_apply_template)
        tmpl_btns.addWidget(self.btn_unbind_template)
        v.addLayout(tmpl_btns)
        lib_btns = QHBoxLayout()
        self.btn_export_lib = QPushButton("Export to file")
        self.btn_import_lib = QPushButton("Import from file")
        lib_btns.addWidget(self.btn_export_lib)
        lib_btns.addWidget(self.btn_import_lib)
        v.addLayout(lib_btns)

    def _build_run_sets_dialog(self) -> None:
        self._run_sets_dialog = QDialog(self)
        self._run_sets_dialog.setWindowTitle("Run Sets")
        self._run_sets_dialog.resize(440, 340)
        v = QVBoxLayout(self._run_sets_dialog)
        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("Run sets (cross-mode corner selection)"))
        hdr.addStretch(1)
        self.btn_new_run_set = QPushButton("New Run Set")
        hdr.addWidget(self.btn_new_run_set)
        v.addLayout(hdr)
        self.run_sets_list = QListWidget()
        v.addWidget(self.run_sets_list)
        btns = QHBoxLayout()
        self.btn_apply_run_set = QPushButton("Switch to this run set")
        self.btn_filter_set = QPushButton("Filter table to this run set")
        btns.addWidget(self.btn_apply_run_set)
        btns.addWidget(self.btn_filter_set)
        v.addLayout(btns)

    # --- model swap ------------------------------------------------------

    def load_model(
        self, model: CornerModel, profile: object = None,
        source_path: object = None,
    ) -> None:
        """Replace the displayed cornermodel — used when the owning window
        discovers or opens a different ``.cornermodel.json``. This is a load,
        not an edit, so ``cornermodel_edited`` does not fire.
        """
        self._cm = model
        self._profile = profile
        self._source_path = source_path
        self._set_filter = None
        self.title_label.setText(self._title_text())
        self.table_model.clear_all_filters()
        self.table_model.set_cornermodel(model, profile)
        self._refresh_side_panels()
        self._apply_filters()
        self._refresh_check_status()

    # --- modes panel -----------------------------------------------------

    def _refresh_modes_panel(self) -> None:
        prev = self.modes_list.currentItem()
        prev_name = prev.text() if prev is not None else None
        self.modes_list.blockSignals(True)
        self.modes_list.clear()
        for mode_name in sorted(self._cm.modes):
            self.modes_list.addItem(mode_name)
        self.modes_list.blockSignals(False)
        if self.modes_list.count() == 0:
            self._populate_mode_vars(None)
            return
        target = 0
        if prev_name is not None:
            for i in range(self.modes_list.count()):
                if self.modes_list.item(i).text() == prev_name:
                    target = i
                    break
        self.modes_list.setCurrentRow(target)

    def _refresh_side_panels(self) -> None:
        self._refresh_modes_panel()
        self._refresh_templates_panel()
        self._refresh_run_sets_panel()

    # --- run-sets panel + column filter ---------------------------------

    def _refresh_run_sets_panel(self) -> None:
        prev = self.run_sets_list.currentItem()
        prev_name = prev.text().split("  ")[0] if prev is not None else None
        self.run_sets_list.clear()
        names = sorted(self._cm.run_sets)
        for name in names:
            count = len(self._cm.run_sets[name].columns)
            self.run_sets_list.addItem(f"{name}  ({count} cols)")
        if prev_name in names:
            self.run_sets_list.setCurrentRow(names.index(prev_name))

    def _selected_run_set_name(self) -> Optional[str]:
        item = self.run_sets_list.currentItem()
        if item is None:
            return None
        return item.text().split("  ")[0]

    def _on_new_run_set(self) -> None:
        if not self._cm.columns:
            QMessageBox.warning(
                self, "New Run Set", "Add a corner column first."
            )
            return
        name, ok = QInputDialog.getText(
            self, "New Run Set", "Run set name (^[A-Za-z][A-Za-z0-9_]*$):"
        )
        if not ok or not name.strip():
            return
        all_names = [effective_name(c) for c in self._cm.columns]
        dialog = _ColumnPickerDialog(
            "New Run Set — select corner columns",
            "Tick the corner columns this run set should enable:",
            all_names, parent=self,
        )
        if dialog.exec_() != QDialog.Accepted:
            return
        try:
            new_cm = add_run_set(
                self._cm, name.strip(), dialog.checked_columns()
            )
        except CornerModelError as exc:
            QMessageBox.warning(self, "New run set failed", str(exc))
            return
        self._apply(new_cm)

    def _on_apply_run_set(self) -> None:
        name = self._selected_run_set_name()
        if name is None:
            QMessageBox.warning(
                self, "Switch run set", "Select a run set first."
            )
            return
        self._apply(apply_run_set(self._cm, name))

    def _on_filter_set(self) -> None:
        name = self._selected_run_set_name()
        if name is None:
            QMessageBox.warning(
                self, "Filter to run set", "Select a run set first."
            )
            return
        self._set_filter = name
        self._apply_filters()

    def _on_clear_all_filters(self) -> None:
        self._set_filter = None
        self.table_model.clear_all_filters()
        self._apply_filters()

    def _apply_filters(self) -> None:
        """Re-apply row / column visibility from the table's embedded filter
        cells, ANDed with the active run-set filter (if any)."""
        model = self.table_model
        members = (
            run_set_membership(self._cm, self._set_filter)
            if self._set_filter in self._cm.run_sets else None
        )
        n_cols = model.columnCount() - 2
        n_rows = model.rowCount() - 1
        for j in range(n_cols):
            visible = model.is_data_col_visible(j)
            if visible and members is not None:
                col = model.column_at(j + 2)
                if col is not None and effective_name(col) not in members:
                    visible = False
            self.table.setColumnHidden(j + 2, not visible)
        for i in range(n_rows):
            self.table.setRowHidden(i + 1, not model.is_data_row_visible(i))

    def _on_table_context_menu(self, pos) -> None:
        """Right-click the table — a filter cell shows its match-mode menu, a
        corner cell its corner menu, a variable name its variable menu."""
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        row, col = index.row(), index.column()
        gpos = self.table.viewport().mapToGlobal(pos)
        matcher = self.table_model.matcher_at(row, col)
        if matcher is not None:
            self._show_filter_menu(row, col, matcher, gpos)
            return
        j = self.table_model.column_index_at(col)
        if j is not None:
            self._show_column_menu(j, gpos)
            return
        var = self.table_model.var_at(row)
        if var is not None:
            self._show_variable_menu(
                var, self.table_model.data_row_kind(row), gpos
            )

    def _on_header_context_menu(self, pos) -> None:
        """Right-click a corner header → the corner menu (2026 UX item 1)."""
        header = self.table.horizontalHeader()
        j = self.table_model.column_index_at(header.logicalIndexAt(pos))
        if j is not None:
            self._show_column_menu(j, header.viewport().mapToGlobal(pos))

    def _show_filter_menu(self, row, col, matcher, gpos) -> None:
        menu = QMenu(self)
        for mode in MENU_ORDER:
            act = menu.addAction(mode.value)
            act.setCheckable(True)
            act.setChecked(mode is matcher.mode)
            act.triggered.connect(
                lambda _c, m=mode: self.table_model.set_filter_options(
                    row, col, mode=m
                )
            )
        menu.addSeparator()
        cs = menu.addAction("Case sensitive")
        cs.setCheckable(True)
        cs.setChecked(matcher.case_sensitive)
        cs.triggered.connect(
            lambda checked: self.table_model.set_filter_options(
                row, col, case_sensitive=checked
            )
        )
        menu.exec_(gpos)

    def _show_column_menu(self, j: int, gpos) -> None:
        column = self._cm.columns[j]
        name = effective_name(column)
        menu = QMenu(self)
        menu.addAction("New Corner…", self._on_new_column)
        menu.addAction(
            f"Duplicate {name!r}", lambda: self._duplicate_column(j)
        )
        menu.addAction(f"Rename {name!r}…", lambda: self._rename_column(j))
        toggle = "Disable" if column.enabled else "Enable"
        menu.addAction(
            f"{toggle} {name!r}", lambda: self._toggle_column_enabled(j)
        )
        menu.addSeparator()
        act_left = menu.addAction(
            "Move Left", lambda: self._shift_column(j, -1)
        )
        act_left.setEnabled(j > 0)
        act_right = menu.addAction(
            "Move Right", lambda: self._shift_column(j, 1)
        )
        act_right.setEnabled(j < len(self._cm.columns) - 1)
        menu.addAction("Reorder Corners…", self._on_reorder_corners)
        menu.addSeparator()
        menu.addAction(f"Delete {name!r}", lambda: self._delete_column(j))
        menu.exec_(gpos)

    def _show_variable_menu(self, var: str, kind: str, gpos) -> None:
        """Right-click menu for a variable row. Temperature is intrinsic —
        it cannot be renamed, moved, or removed."""
        menu = QMenu(self)
        is_temp = kind == "temp"
        if not is_temp:
            menu.addAction(
                f"Rename {var!r}…", lambda: self._rename_variable(var)
            )
            _temp, design = self.table_model.variable_order()
            if var in design:
                i = design.index(var)
                up = menu.addAction(
                    "Move Up", lambda: self._move_var(var, -1)
                )
                up.setEnabled(i > 0)
                down = menu.addAction(
                    "Move Down", lambda: self._move_var(var, 1)
                )
                down.setEnabled(i < len(design) - 1)
        menu.addAction("Add Design Variable…", self._add_variable)
        if not is_temp:
            menu.addSeparator()
            menu.addAction(
                f"Remove {var!r}", lambda: self._remove_variable(var)
            )
        menu.exec_(gpos)

    # --- corner column actions ------------------------------------------

    def _duplicate_column(self, j: int) -> None:
        src = self._cm.columns[j]
        base = effective_name(src)
        existing = {effective_name(c) for c in self._cm.columns}
        new_name, n = f"{base}_copy", 2
        while new_name in existing:
            new_name, n = f"{base}_copy{n}", n + 1
        dup = (_dc_replace(src, alias=new_name) if src.is_managed
               else _dc_replace(src, name=new_name))
        try:
            new_cm = add_column(self._cm, dup)
        except CornerModelError as exc:
            QMessageBox.warning(self, "Duplicate corner failed", str(exc))
            return
        # Place the duplicate immediately after the source column.
        order = list(range(len(new_cm.columns)))
        order.insert(j + 1, order.pop())
        self._apply(reorder_columns(new_cm, tuple(order)))

    def _rename_column(self, j: int) -> None:
        old = effective_name(self._cm.columns[j])
        new, ok = QInputDialog.getText(
            self, "Rename corner", "New corner name:", text=old
        )
        if not ok or not new.strip() or new.strip() == old:
            return
        try:
            new_cm = rename_column(self._cm, j, new.strip())
        except CornerModelError as exc:
            QMessageBox.warning(self, "Rename corner failed", str(exc))
            return
        self._apply(new_cm)

    def _toggle_column_enabled(self, j: int) -> None:
        column = self._cm.columns[j]
        self._apply(set_column_enabled(self._cm, j, not column.enabled))

    def _shift_column(self, j: int, delta: int) -> None:
        new_cm = move_column(self._cm, j, delta)
        if new_cm is not self._cm:
            self._apply(new_cm)

    def _delete_column(self, j: int) -> None:
        name = effective_name(self._cm.columns[j])
        if QMessageBox.question(
            self, "Delete corner",
            f"Delete corner {name!r}? Push to Maestro to apply it there.",
        ) != QMessageBox.Yes:
            return
        self._apply(delete_column(self._cm, j))

    def _on_reorder_corners(self) -> None:
        if len(self._cm.columns) < 2:
            QMessageBox.information(
                self, "Reorder Corners",
                "Add at least two corner columns first."
            )
            return
        names = [effective_name(c) for c in self._cm.columns]
        dialog = _ReorderDialog("Reorder Corners", names, self)
        if dialog.exec_() != QDialog.Accepted:
            return
        order = dialog.new_order()
        if order != tuple(range(len(names))):
            self._apply(reorder_columns(self._cm, order))

    def _on_section_moved(self, _logical, _old, _new) -> None:
        """A corner header was dragged — re-seat the model to match. The
        header itself is restored to identity (a model reset does not undo a
        section move); the cornermodel carries the new order."""
        if self._reordering:
            return
        self._reordering = True
        try:
            header = self.table.horizontalHeader()
            n = self.table_model.columnCount()
            visual = sorted(range(n), key=header.visualIndex)
            header.blockSignals(True)
            for logical in range(n):
                cur = header.visualIndex(logical)
                if cur != logical:
                    header.moveSection(cur, logical)
            header.blockSignals(False)
            if n < 4 or visual[0] != 0 or visual[1] != 1:
                return   # the Variable / Filter columns must stay first
            data_order = tuple(lg - 2 for lg in visual[2:])
            if data_order != tuple(range(len(data_order))):
                self._apply(reorder_columns(self._cm, data_order))
        finally:
            self._reordering = False

    def _on_row_section_moved(self, _logical, _old, _new) -> None:
        """A row was dragged in the vertical-header gutter — re-order the
        Design Variable rows to match. The header is restored to identity;
        a drag that pulls a row out of the Design Variable block snaps back."""
        if self._reordering:
            return
        self._reordering = True
        try:
            vheader = self.table.verticalHeader()
            n = self.table_model.rowCount()
            visual = sorted(range(n), key=vheader.visualIndex)
            vheader.blockSignals(True)
            for logical in range(n):
                cur = vheader.visualIndex(logical)
                if cur != logical:
                    vheader.moveSection(cur, logical)
            vheader.blockSignals(False)
            design_rows = [
                r for r in range(n)
                if self.table_model.data_row_kind(r) == "var"
            ]
            if len(design_rows) < 2:
                return
            positions = [
                p for p, r in enumerate(visual)
                if self.table_model.data_row_kind(r) == "var"
            ]
            if positions != design_rows:
                return   # a row left the Design Variable block — reject
            new_design = [
                self.table_model.var_at(visual[p]) for p in positions
            ]
            temp, design = self.table_model.variable_order()
            if new_design != design:
                self._apply(
                    set_var_order(self._cm, tuple(temp + new_design))
                )
        finally:
            self._reordering = False

    def _on_header_double_clicked(self, section: int) -> None:
        j = self.table_model.column_index_at(section)
        if j is not None:
            self._rename_column(j)

    # --- variable row actions -------------------------------------------

    def _rename_variable(self, var: str) -> None:
        new, ok = QInputDialog.getText(
            self, "Rename variable", "New variable name:", text=var
        )
        if not ok or not new.strip() or new.strip() == var:
            return
        try:
            new_cm = rename_variable(self._cm, var, new.strip())
        except CornerModelError as exc:
            QMessageBox.warning(self, "Rename variable failed", str(exc))
            return
        self._apply(new_cm)
        self._select_var_row(new.strip())

    def _remove_variable(self, var: str) -> None:
        if QMessageBox.question(
            self, "Remove variable",
            f"Remove variable {var!r} from every corner?",
        ) != QMessageBox.Yes:
            return
        try:
            new_cm = remove_variable(self._cm, var)
        except CornerModelError as exc:
            QMessageBox.warning(self, "Remove variable failed", str(exc))
            return
        self._apply(new_cm)

    def _add_variable(self) -> None:
        """Add a new Design Variable row across every corner (issue: the
        right-click menu could not add a row)."""
        if not self._cm.columns:
            QMessageBox.information(
                self, "Add Design Variable",
                "Add a corner first — a variable needs a column to live in.",
            )
            return
        text, ok = QInputDialog.getText(
            self, "Add Design Variable",
            "New Design Variable name "
            "(optionally  name = value , e.g.  vctrl = 0.6):",
        )
        if not ok:
            return
        name, _sep, value = text.strip().partition("=")
        name, value = name.strip(), value.strip()
        if not name:
            return
        if not re.match(r"^[A-Za-z][A-Za-z0-9_]*$", name):
            QMessageBox.warning(
                self, "Add Design Variable",
                f"{name!r} is not a valid variable name — it must start "
                f"with a letter and use only letters, digits, and "
                f"underscores.",
            )
            return
        existing: set[str] = set()
        for c in self._cm.columns:
            existing |= set(c.pvt_vars)
        if name in existing:
            QMessageBox.warning(
                self, "Add Design Variable",
                f"Variable {name!r} already exists.",
            )
            return
        cm = self._cm
        for j in range(len(cm.columns)):
            cm = set_pvt_var(cm, j, name, value)
        if name not in cm.var_order:
            cm = set_var_order(cm, tuple(cm.var_order) + (name,))
        self._apply(cm)
        self._select_var_row(name)

    def _move_selected_row(self, delta: int) -> None:
        idx = self.table.currentIndex()
        var = self.table_model.var_at(idx.row()) if idx.isValid() else None
        if var is None:
            QMessageBox.information(
                self, "Move row", "Select a Design Variable row first."
            )
            return
        self._move_var(var, delta)

    def _move_var(self, var: str, delta: int) -> None:
        temp, design = self.table_model.variable_order()
        if var not in design:
            QMessageBox.information(
                self, "Move row",
                "Only Design Variable rows can be reordered."
            )
            return
        i = design.index(var)
        target = i + delta
        if not (0 <= target < len(design)):
            return
        design[i], design[target] = design[target], design[i]
        self._apply(set_var_order(self._cm, tuple(temp + design)))
        self._select_var_row(var)

    def _select_var_row(self, var: str) -> None:
        for r in range(self.table_model.rowCount()):
            if self.table_model.var_at(r) == var:
                self.table.setCurrentIndex(self.table_model.index(r, 0))
                return

    # --- clipboard (Excel-style copy / paste) ---------------------------

    def _copy_selection(self) -> None:
        """Copy the selected cells as tab / newline separated text."""
        sel = self.table.selectionModel().selectedIndexes()
        if not sel:
            return
        rows = sorted({i.row() for i in sel})
        cols = sorted({i.column() for i in sel})
        by_cell = {(i.row(), i.column()): i for i in sel}
        lines = []
        for r in rows:
            cells = []
            for c in cols:
                idx = by_cell.get((r, c))
                value = (self.table_model.data(idx, Qt.DisplayRole)
                         if idx is not None else None)
                cells.append("" if value is None else str(value))
            lines.append("\t".join(cells))
        QApplication.clipboard().setText("\n".join(lines))

    def _paste_selection(self) -> None:
        """Paste tab / newline separated text over cells, anchored at the
        current cell — non-editable cells are skipped."""
        text = QApplication.clipboard().text()
        cur = self.table.currentIndex()
        if not text or not cur.isValid():
            return
        grid = [line.split("\t") for line in text.split("\n")]
        while grid and grid[-1] == [""]:
            grid.pop()
        r0, c0 = cur.row(), cur.column()
        for dr, line in enumerate(grid):
            for dc, value in enumerate(line):
                idx = self.table_model.index(r0 + dr, c0 + dc)
                if not idx.isValid():
                    continue
                if not (self.table_model.flags(idx) & Qt.ItemIsEditable):
                    continue
                self.table_model.setData(idx, value, Qt.EditRole)

    # --- corner sets (templates) ----------------------------------------

    def _on_new_template(self) -> None:
        if not self._cm.modes:
            QMessageBox.warning(
                self, "New Corner Set", "Create a mode first."
            )
            return
        name, ok = QInputDialog.getText(
            self, "New Corner Set",
            "Corner set name (^[A-Za-z][A-Za-z0-9_]*$):"
        )
        if not ok or not name.strip():
            return
        cols_text, ok = QInputDialog.getMultiLineText(
            self, "New Corner Set — corner columns",
            "One corner column per line — label: var=value, var=value\n"
            "e.g.   TT: temperature=55, VDD=0.9",
            "TT: temperature=55, VDD=0.9",
        )
        if not ok:
            return
        try:
            columns = _parse_template_columns(cols_text)
            new_cm = add_pvt_template(self._cm, PvtTemplate(
                name=name.strip(), columns=columns,
            ))
        except (ValueError, CornerModelError) as exc:
            QMessageBox.warning(self, "New corner set failed", str(exc))
            return
        self._apply(new_cm)

    def _refresh_templates_panel(self) -> None:
        prev = self._selected_template_name()
        self.templates_list.clear()
        names = sorted(self._cm.pvt_templates)
        for name in names:
            bound = sorted(
                b.mode for b in self._cm.template_bindings
                if b.template == name
            )
            suffix = f"  → {', '.join(bound)}" if bound else ""
            self.templates_list.addItem(f"{name}{suffix}")
        if prev in names:
            self.templates_list.setCurrentRow(names.index(prev))

    def _selected_template_name(self) -> Optional[str]:
        item = self.templates_list.currentItem()
        if item is None:
            return None
        return item.text().split("  → ")[0]

    def _on_mode_selected(self, *_args) -> None:
        item = self.modes_list.currentItem()
        self._populate_mode_vars(item.text() if item is not None else None)

    def _populate_mode_vars(self, mode_name: Optional[str]) -> None:
        self._loading_mode_vars = True
        self.mode_vars.setRowCount(0)
        if mode_name is not None and mode_name in self._cm.modes:
            mode = self._cm.modes[mode_name]
            for var, value in sorted(mode.vars.items()):
                row = self.mode_vars.rowCount()
                self.mode_vars.insertRow(row)
                name_item = QTableWidgetItem(var)
                name_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                self.mode_vars.setItem(row, 0, name_item)
                self.mode_vars.setItem(row, 1, QTableWidgetItem(value))
        self._loading_mode_vars = False

    def _on_mode_var_changed(self, item: QTableWidgetItem) -> None:
        if self._loading_mode_vars or item.column() != 1:
            return
        mode_item = self.modes_list.currentItem()
        if mode_item is None:
            return
        var = self.mode_vars.item(item.row(), 0).text()
        new_value = item.text().strip()
        if new_value == "":
            self._refresh_modes_panel()  # revert empty edit
            return
        try:
            new_cm = set_mode_var(
                self._cm, mode_item.text(), var, new_value
            )
        except CornerModelError as exc:
            QMessageBox.warning(self, "Edit register failed", str(exc))
            self._refresh_modes_panel()
            return
        self._apply(new_cm)

    # --- edit application ------------------------------------------------

    def _apply(self, new_cm: CornerModel) -> None:
        """Seat a mode-panel / dialog edit: refresh table + side panels."""
        self._cm = new_cm
        self.table_model.set_cornermodel(new_cm)
        self._refresh_side_panels()
        self._apply_filters()  # the model reset un-hid every row / column
        self._refresh_check_status()
        self.cornermodel_edited.emit(new_cm)

    def _on_table_edited(self, new_cm: CornerModel) -> None:
        """A cell edit in the corner table — the table model already reset
        itself; just keep our copy + side panels in sync and notify."""
        self._cm = new_cm
        self._refresh_side_panels()
        self._apply_filters()
        self._refresh_check_status()
        self.cornermodel_edited.emit(new_cm)

    # --- check status / library -----------------------------------------

    def _refresh_check_status(self) -> None:
        base = (
            Path(self._source_path).parent
            if self._source_path is not None else None
        )
        issues = check_cornermodel(
            self._cm, base_dir=base, profile=self._profile
        )
        if not issues:
            self.check_label.setText("Check: no issues")
        else:
            head = "; ".join(i.message for i in issues[:2])
            self.check_label.setText(
                f"Check: {len(issues)} issue(s) — {head}"
            )

    def _on_export_library(self) -> None:
        path, ok = QInputDialog.getText(
            self, "Export template library",
            "Path to write the .cornerlib.json:"
        )
        if not ok or not path.strip():
            return
        p = Path(path.strip()).expanduser()
        stem = p.name
        libname = (
            stem[:-len(".cornerlib.json")]
            if stem.endswith(".cornerlib.json") else "exported_lib"
        )
        try:
            lib = export_library(self._cm, libname)
            p.write_text(
                json.dumps(library_to_dict(lib), indent=2, ensure_ascii=False)
                + "\n",
                encoding="utf-8",
            )
        except (CornerModelError, OSError) as exc:
            QMessageBox.warning(
                self, "Export template library failed", str(exc)
            )

    def _on_import_library(self) -> None:
        path, ok = QInputDialog.getText(
            self, "Import template library", "Path to the .cornerlib.json:"
        )
        if not ok or not path.strip():
            return
        try:
            lib = load_library(path.strip())
            new_cm = import_library(self._cm, lib)
        except CornerModelError as exc:
            QMessageBox.warning(
                self, "Import template library failed", str(exc)
            )
            return
        self._apply(new_cm)

    # --- corner sets: apply / unbind ------------------------------------

    def _on_apply_template(self) -> None:
        tmpl = self._selected_template_name()
        if tmpl is None:
            QMessageBox.warning(
                self, "Apply corner set", "Select a corner set first."
            )
            return
        if not self._cm.modes:
            QMessageBox.warning(
                self, "Apply corner set", "Create a mode first."
            )
            return
        mode, ok = QInputDialog.getItem(
            self, "Apply corner set",
            f"Apply corner set {tmpl} to which mode:",
            sorted(self._cm.modes), 0, False,
        )
        if not ok:
            return
        try:
            new_cm = apply_template(self._cm, mode, tmpl)
        except CornerModelError as exc:
            QMessageBox.warning(self, "Apply corner set failed", str(exc))
            return
        self._apply(new_cm)

    def _on_unbind_template(self) -> None:
        tmpl = self._selected_template_name()
        if tmpl is None:
            QMessageBox.warning(
                self, "Unbind corner set", "Select a corner set first."
            )
            return
        bound = [
            b for b in self._cm.template_bindings if b.template == tmpl
        ]
        if not bound:
            QMessageBox.warning(
                self, "Unbind corner set",
                f"Corner set {tmpl} is not applied to any mode."
            )
            return
        labels = [b.variant or b.mode for b in bound]
        label, ok = QInputDialog.getItem(
            self, "Unbind corner set",
            f"Unbind {tmpl} from which mode (its columns stay, frozen):",
            labels, 0, False,
        )
        if not ok:
            return
        binding = bound[labels.index(label)]
        self._apply(unbind_template(
            self._cm, binding.mode, tmpl, variant=binding.variant
        ))

    # --- new / edit mode, new column ------------------------------------

    def _on_new_mode(self) -> None:
        methods = []
        if self._cm.columns:
            methods.append("From a corner column")
        if self._cm.modes:
            methods.append("Derived from an existing mode")
        methods.append("Type registers by hand")
        if len(methods) == 1:
            choice = methods[0]
        else:
            choice, ok = QInputDialog.getItem(
                self, "New Mode", "Create the new mode:", methods, 0, False
            )
            if not ok:
                return
        if choice == "From a corner column":
            self._new_mode_from_column()
        elif choice == "Derived from an existing mode":
            self._new_mode_from_mode()
        else:
            self._new_mode_manual()

    def _new_mode_from_mode(self) -> None:
        """痛点 c — a 'variant' is just a mode derived from another: copy a
        mode's registers, tweak a few, give it a new name."""
        base, ok = QInputDialog.getItem(
            self, "New Mode — derive from a mode",
            "Base mode to copy registers from:",
            sorted(self._cm.modes), 0, False,
        )
        if not ok:
            return
        name, ok = QInputDialog.getText(
            self, "New Mode — derive from a mode",
            "New mode name (^[A-Za-z][A-Za-z0-9_]*$):", text=f"{base}_PN",
        )
        if not ok or not name.strip():
            return
        seed = "\n".join(
            f"{k}={v}"
            for k, v in sorted(self._cm.modes[base].vars.items())
        )
        text, ok = QInputDialog.getMultiLineText(
            self, "New Mode — registers",
            "Edit the registers for the new mode (one var=value per line) — "
            "e.g. turn d_div12_en off for a PSS variant:",
            seed,
        )
        if not ok:
            return
        try:
            mode_vars = _parse_var_lines(text)
            new_cm = add_mode(self._cm, name.strip(), mode_vars)
        except (ValueError, CornerModelError) as exc:
            QMessageBox.warning(self, "New mode failed", str(exc))
            return
        self._apply(new_cm)

    def _on_edit_mode(self) -> None:
        item = self.modes_list.currentItem()
        if item is None:
            QMessageBox.warning(self, "Edit Mode", "Select a mode first.")
            return
        mode_name = item.text()
        dialog = _EditModeDialog(self._cm, mode_name, self)
        if dialog.exec_() != QDialog.Accepted:
            return
        try:
            new_cm = reclassify_mode(
                self._cm, mode_name, dialog.register_vars()
            )
        except CornerModelError as exc:
            QMessageBox.warning(self, "Edit Mode failed", str(exc))
            return
        self._apply(new_cm)

    def _new_mode_from_column(self) -> None:
        dialog = _NewModeDialog(self._cm, self)
        if dialog.exec_() != QDialog.Accepted:
            return
        regs = dialog.register_vars()
        multi = sorted(v for v, val in regs.items() if "," in val)
        if multi:
            QMessageBox.warning(
                self, "New mode failed",
                f"A register must be a single value — {', '.join(multi)} "
                f"{'is' if len(multi) == 1 else 'are'} multi-valued. Either "
                f"tick them as PVT to keep the sweep per-column, or edit "
                f"them down to one value.",
            )
            return
        try:
            new_cm = mode_from_column(
                self._cm, dialog.selected_column_index(),
                dialog.mode_name(), regs, dialog.pvt_label(),
            )
        except CornerModelError as exc:
            QMessageBox.warning(self, "New mode failed", str(exc))
            return
        self._apply(new_cm)

    def _new_mode_manual(self) -> None:
        # Blank-project bootstrap — there is no column to derive a mode from.
        name, ok = QInputDialog.getText(
            self, "New Mode", "Mode name (^[A-Za-z][A-Za-z0-9_]*$):"
        )
        if not ok or not name.strip():
            return
        text, ok = QInputDialog.getMultiLineText(
            self, "New Mode — register settings",
            "One var=value per line:", "d_en_dummy=1",
        )
        if not ok:
            return
        try:
            mode_vars = _parse_var_lines(text)
        except ValueError as exc:
            QMessageBox.warning(self, "New mode failed", str(exc))
            return
        try:
            new_cm = add_mode(self._cm, name.strip(), mode_vars)
        except CornerModelError as exc:
            QMessageBox.warning(self, "New mode failed", str(exc))
            return
        self._apply(new_cm)

    def _on_new_column(self) -> None:
        if not self._cm.modes:
            QMessageBox.warning(self, "New Column", "Create a mode first.")
            return
        mode, ok = QInputDialog.getItem(
            self, "New Column", "Mode:", sorted(self._cm.modes), 0, False
        )
        if not ok:
            return
        label, ok = QInputDialog.getText(
            self, "New Column", "PVT label (^[A-Za-z0-9_]+$):"
        )
        if not ok or not label.strip():
            return
        text, ok = QInputDialog.getMultiLineText(
            self, "New Column — PVT variables",
            "One var=value per line (may be empty):", "temperature=55",
        )
        if not ok:
            return
        models_text, ok = QInputDialog.getMultiLineText(
            self, "New Column — process model files",
            "One 'file: section' per line, e.g. rf018.scs: tt "
            "(may be empty):", "",
        )
        if not ok:
            return
        try:
            pvt = _parse_var_lines(text)
            models = _parse_model_lines(models_text)
        except ValueError as exc:
            QMessageBox.warning(self, "New column failed", str(exc))
            return
        column = Column(
            mode=mode,
            enabled=True,
            pvt_vars={k: (v,) for k, v in pvt.items()},
            models=models,
            pvt_label=label.strip(),
        )
        try:
            new_cm = add_column(self._cm, column)
        except CornerModelError as exc:
            QMessageBox.warning(self, "New column failed", str(exc))
            return
        self._apply(new_cm)

    # --- accessors -------------------------------------------------------

    def cornermodel(self) -> CornerModel:
        return self._cm


def _parse_var_lines(text: str) -> dict[str, str]:
    """Parse ``var=value`` lines into a dict. Blank lines ignored."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if "=" not in line:
            raise ValueError(f"line {line!r} is not in var=value format")
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not key:
            raise ValueError(f"line {line!r} is missing the var name")
        out[key] = value
    return out


def _parse_model_lines(text: str) -> tuple[ModelEntry, ...]:
    """Parse ``file: section`` lines into process-model entries. ``block`` /
    ``test`` default to the Maestro corner-table defaults (Global / All)."""
    out: list[ModelEntry] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if ":" not in line:
            raise ValueError(
                f"line {line!r} is not in 'file: section' format"
            )
        file, _, section = line.partition(":")
        file, section = file.strip(), section.strip()
        if not file or not section:
            raise ValueError(
                f"line {line!r} must give both a model file and a section"
            )
        out.append(ModelEntry(
            file=file, block="Global", test="All", section=(section,),
        ))
    return tuple(out)


class _ColumnPickerDialog(QDialog):
    """A checkable list of corner columns — used to assemble a run set
    without retyping column names (pain point d)."""

    def __init__(
        self, title: str, prompt: str, names: list[str],
        preselected: tuple[str, ...] = (),
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(320)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(prompt))
        self._list = QListWidget()
        pre = set(preselected)
        for name in names:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(
                Qt.Checked if name in pre else Qt.Unchecked
            )
            self._list.addItem(item)
        layout.addWidget(self._list)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def checked_columns(self) -> tuple[str, ...]:
        return tuple(
            self._list.item(i).text()
            for i in range(self._list.count())
            if self._list.item(i).checkState() == Qt.Checked
        )


_PVT_NAME_HINTS = (
    "temperature", "temp", "vdd", "vss", "vcc", "vbat", "vdda",
    "vddd", "vref", "voltage", "vsup",
)


def _default_is_pvt(var: str) -> bool:
    """Heuristic for which vars to pre-tick as PVT — temperature / supply
    names. The user can re-tick freely; this only sets the default."""
    v = var.lower()
    return any(hint in v for hint in _PVT_NAME_HINTS)


def _derive_mode_label(column_name: str) -> tuple[str, str]:
    """Guess a (mode, column-label) split from a corner name like
    ``RX_TT`` → ``("RX", "TT")``. Falls back to the whole name."""
    mode, _, label = column_name.rpartition("_")
    if mode and label:
        return mode, label
    return column_name, column_name


class _NewModeDialog(QDialog):
    """New Mode authored *from* an existing corner column (痛点 — the user
    has already defined every variable in Cadence). Tick the PVT variables;
    the rest, with editable values, become the new mode's registers."""

    def __init__(self, model: CornerModel, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("New Mode — from a corner column")
        self.setMinimumWidth(460)
        self._columns = list(model.columns)
        # Every design variable across the cornermodel — so a sparse corner
        # can still seed a full register set (a corner only stores the vars
        # it overrides; the others are blank for the user to fill).
        all_vars: set[str] = set()
        for c in model.columns:
            all_vars |= set(c.pvt_vars)
        self._all_vars = sorted(all_vars)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Derive a mode from a corner you already authored: tick the "
            "PVT variables — the rest become the mode's registers."
        ))
        form = QFormLayout()
        self._column_combo = QComboBox()
        for col in self._columns:
            self._column_combo.addItem(effective_name(col))
        form.addRow("Source column:", self._column_combo)
        self._mode_edit = QLineEdit()
        form.addRow("New mode name:", self._mode_edit)
        self._label_edit = QLineEdit()
        form.addRow("Column label:", self._label_edit)
        layout.addLayout(form)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Variable", "Value", "PVT?"])
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch
        )
        self._table.verticalHeader().setDefaultSectionSize(24)
        layout.addWidget(self._table)
        layout.addWidget(QLabel(
            "Unticked + a value → mode register. Unticked + blank → left "
            "out of the mode. Ticked → per-column PVT variable. A register "
            "must be a single value — tick a swept var as PVT, or edit it "
            "down to one value. The 'Process · <file>' rows are the P of "
            "PVT — always per-column, never a register."
        ))

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._column_combo.currentIndexChanged.connect(self._reload)
        if self._columns:
            self._reload(0)

    def _reload(self, index: int) -> None:
        if not (0 <= index < len(self._columns)):
            return
        col = self._columns[index]
        default_mode, default_label = _derive_mode_label(effective_name(col))
        self._mode_edit.setText(default_mode)
        self._label_edit.setText(default_label)
        self._table.setRowCount(0)
        # List every design variable, not only the ones this column
        # overrides — the selected column pre-fills the values it has, the
        # rest stay blank and editable for the user to type a register value.
        for var in self._all_vars:
            tup = col.pvt_vars.get(var)
            row = self._table.rowCount()
            self._table.insertRow(row)
            name_item = QTableWidgetItem(var)
            name_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            self._table.setItem(row, 0, name_item)
            self._table.setItem(
                row, 1,
                QTableWidgetItem("" if tup is None else ", ".join(tup)),
            )
            pvt_item = QTableWidgetItem()
            pvt_item.setFlags(
                Qt.ItemIsSelectable | Qt.ItemIsEnabled
                | Qt.ItemIsUserCheckable
            )
            # A swept (multi-value) var defaults to PVT — a register is a
            # single scalar, so the user unticks a sweep only to commit it
            # to one value.
            multi = tup is not None and len(tup) > 1
            pvt_item.setCheckState(
                Qt.Checked if (_default_is_pvt(var) or multi)
                else Qt.Unchecked
            )
            self._table.setItem(row, 2, pvt_item)
        # Process (the P of PVT) is stored as model.section, not a var —
        # surface it read-only so the user can see it is part of the corner.
        for entry in column_models(col):
            row = self._table.rowCount()
            self._table.insertRow(row)
            name_item = QTableWidgetItem(f"Process · {entry.file}")
            name_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            self._table.setItem(row, 0, name_item)
            val_item = QTableWidgetItem(", ".join(entry.section))
            val_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            self._table.setItem(row, 1, val_item)
            pvt_item = QTableWidgetItem()
            pvt_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            pvt_item.setCheckState(Qt.Checked)
            self._table.setItem(row, 2, pvt_item)

    def selected_column_index(self) -> int:
        return self._column_combo.currentIndex()

    def mode_name(self) -> str:
        return self._mode_edit.text().strip()

    def pvt_label(self) -> str:
        return self._label_edit.text().strip()

    def register_vars(self) -> dict[str, str]:
        """Unticked rows with a value become the mode's registers. An
        unticked row left blank is simply left out of the mode."""
        out: dict[str, str] = {}
        for row in range(self._table.rowCount()):
            if self._table.item(row, 2).checkState() == Qt.Checked:
                continue   # ticked → PVT, not a register
            var = self._table.item(row, 0).text()
            value = self._table.item(row, 1).text().strip()
            if value:
                out[var] = value
        return out


class _EditModeDialog(QDialog):
    """Re-classify an existing mode's variables (register vs PVT) and edit
    register values — the New-Mode classification used to be frozen
    (2026 UX item #1)."""

    def __init__(
        self, model: CornerModel, mode_name: str,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"Edit Mode — {mode_name}")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"Re-classify {mode_name}'s variables. Unticked → mode "
            "register (one value, shared by every column of the mode). "
            "Ticked → per-column PVT variable."
        ))
        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Variable", "Value", "PVT?"])
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch
        )
        self._table.verticalHeader().setDefaultSectionSize(24)
        layout.addWidget(self._table)
        layout.addWidget(QLabel(
            "'Process · <file>' rows are shown for reference — Process is "
            "always per-column PVT and can never be a register."
        ))
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        mode = model.modes[mode_name]
        cols = [c for c in model.columns if c.mode == mode_name]
        for var in sorted(mode.vars):
            self._add_row(var, mode.vars[var], is_pvt=False, eligible=True)
        pvt_seen: dict[str, tuple] = {}
        for c in cols:
            for v, tup in c.pvt_vars.items():
                pvt_seen.setdefault(v, tup)
        for var in sorted(pvt_seen):
            tup = pvt_seen[var]
            self._add_row(
                var, tup[0] if len(tup) == 1 else ", ".join(tup),
                is_pvt=True, eligible=len(tup) == 1,
            )
        files_seen: set[str] = set()
        for c in cols:
            for entry in column_models(c):
                if entry.file in files_seen:
                    continue
                files_seen.add(entry.file)
                self._add_row(
                    f"Process · {entry.file}", ", ".join(entry.section),
                    is_pvt=True, eligible=False, is_var=False,
                )

    def _add_row(
        self, name: str, value: str, *,
        is_pvt: bool, eligible: bool, is_var: bool = True,
    ) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        name_item = QTableWidgetItem(name)
        name_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        name_item.setData(Qt.UserRole, is_var)
        self._table.setItem(row, 0, name_item)
        val_item = QTableWidgetItem(value)
        if not eligible:
            val_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        self._table.setItem(row, 1, val_item)
        pvt_item = QTableWidgetItem()
        flags = Qt.ItemIsSelectable | Qt.ItemIsEnabled
        if eligible:
            flags |= Qt.ItemIsUserCheckable
        pvt_item.setFlags(flags)
        pvt_item.setCheckState(Qt.Checked if is_pvt else Qt.Unchecked)
        self._table.setItem(row, 2, pvt_item)

    def register_vars(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for row in range(self._table.rowCount()):
            name_item = self._table.item(row, 0)
            if not name_item.data(Qt.UserRole):
                continue   # a Process / model reference row
            if self._table.item(row, 2).checkState() == Qt.Checked:
                continue   # ticked → PVT, not a register
            out[name_item.text()] = self._table.item(row, 1).text().strip()
        return out


class _ReorderDialog(QDialog):
    """A draggable vertical list for reordering corner columns — easier than
    dragging columns sideways when there are many (2026 UX item 11)."""

    def __init__(
        self, title: str, names: list[str],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(340)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Drag a row — or select one and use the arrows — to reorder "
            "the corner columns:"
        ))
        body = QHBoxLayout()
        self._list = QListWidget()
        self._list.setDragDropMode(QAbstractItemView.InternalMove)
        self._list.setSelectionMode(QAbstractItemView.SingleSelection)
        for idx, name in enumerate(names):
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, idx)
            self._list.addItem(item)
        body.addWidget(self._list, 1)
        arrows = QVBoxLayout()
        self._btn_up = QPushButton("▲")
        self._btn_down = QPushButton("▼")
        arrows.addStretch(1)
        arrows.addWidget(self._btn_up)
        arrows.addWidget(self._btn_down)
        arrows.addStretch(1)
        body.addLayout(arrows)
        layout.addLayout(body)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._btn_up.clicked.connect(lambda: self._nudge(-1))
        self._btn_down.clicked.connect(lambda: self._nudge(1))
        self._list.setCurrentRow(0)

    def _nudge(self, delta: int) -> None:
        row = self._list.currentRow()
        target = row + delta
        if row < 0 or not (0 <= target < self._list.count()):
            return
        item = self._list.takeItem(row)
        self._list.insertItem(target, item)
        self._list.setCurrentRow(target)

    def new_order(self) -> tuple[int, ...]:
        """The new column order as a permutation of the original indices."""
        return tuple(
            self._list.item(i).data(Qt.UserRole)
            for i in range(self._list.count())
        )


def _split_label_line(line: str) -> tuple[str, str]:
    """Split ``label: rest`` into ``(label, rest)``."""
    if ":" not in line:
        raise ValueError(f"line {line!r} must be 'label: ...'")
    label, _, rest = line.partition(":")
    label = label.strip()
    if not label:
        raise ValueError(f"line {line!r} is missing the label")
    return label, rest


def _parse_kv_comma(segment: str) -> tuple[dict[str, str], list[str]]:
    """Parse ``a=1, b=2, +axis`` → ``({a:1, b:2}, ['axis'])``."""
    pairs: dict[str, str] = {}
    axes: list[str] = []
    for raw in segment.split(","):
        tok = raw.strip()
        if not tok:
            continue
        if tok.startswith("+"):
            axes.append(tok[1:].strip())
            continue
        if "=" not in tok:
            raise ValueError(
                f"token {tok!r} is not var=value or +axisName"
            )
        key, _, value = tok.partition("=")
        pairs[key.strip()] = value.strip()
    return pairs, axes


def _parse_template_columns(text: str) -> tuple[TemplateColumn, ...]:
    """Parse the New-Template multi-line dialog into TemplateColumns."""
    cols: list[TemplateColumn] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        label, rest = _split_label_line(line)
        pairs, axes = _parse_kv_comma(rest)
        cols.append(TemplateColumn(
            pvt_label=label,
            pvt_vars={k: (v,) for k, v in pairs.items()},
            correlated_axes=tuple(axes),
        ))
    if not cols:
        raise ValueError("a template needs at least one column")
    return tuple(cols)


