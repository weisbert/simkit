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
    QButtonGroup,
    QMenu,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QShortcut,
    QSplitter,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtCore import Qt

from simkit.corner_model import (
    Column,
    CornerModel,
    CornerModelError,
    CorrelatedAxis,
    CorrelatedTuple,
    add_column,
    add_correlated_axis,
    add_mode,
    assign_mode_to_column,
    columns_of_mode,
    add_run_set,
    apply_run_set,
    check_cornermodel,
    column_models,
    delete_column,
    effective_name,
    mode_from_column,
    move_column,
    reclassify_mode,
    remove_correlated_axis,
    remove_mode,
    remove_run_set,
    rename_column,
    rename_variable,
    reorder_columns,
    remove_variable,
    run_set_membership,
    set_columns_enabled,
    update_correlated_axis,
    set_column_enabled,
    set_mode_var,
    set_pvt_var,
    set_var_order,
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
        self.btn_dimensions = QPushButton("Dimensions…")
        self.btn_dimensions.setToolTip(
            "Manage reusable dimensions — lists of levels (Process: "
            "TT/SS/FF…, Temperature, Voltage). A corner is a crossing of "
            "dimensions; one edit here updates every corner that uses it."
        )
        for b in (self.btn_modes, self.btn_dimensions):
            top.addWidget(b)
        self.btn_new_corner = QPushButton("New Corner")
        self.btn_new_corner.setToolTip(
            "Build a corner by crossing dimensions — tick the levels of "
            "each, then stamp the corner onto one or more modes."
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
        for b in (self.btn_new_corner, self.btn_pull, self.btn_push):
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
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        # The Run Set panel is always on, docked left of the corner table —
        # signoff switches Enable states constantly (痛点 d).
        self.run_set_panel = _RunSetPanel(self)
        body = QSplitter(Qt.Horizontal)
        body.addWidget(self.run_set_panel)
        body.addWidget(self.table)
        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)
        body.setSizes([200, 800])
        outer.addWidget(body, 1)

        self.check_label = QLabel()
        outer.addWidget(self.check_label)

        # Editor pop-ups — built once, hidden until a toolbar button raises
        # them; non-modal so corner-table edits stay live behind.
        self._build_modes_dialog()

        self.btn_modes.clicked.connect(
            lambda: self._open_dialog(self._modes_dialog))

        self.modes_list.currentItemChanged.connect(self._on_mode_selected)
        self.mode_vars.itemChanged.connect(self._on_mode_var_changed)
        self.table_model.cornermodelChanged.connect(self._on_table_edited)
        self.btn_dimensions.clicked.connect(self._on_dimensions)
        self.btn_new_mode.clicked.connect(self._on_new_mode)
        self.btn_edit_mode.clicked.connect(self._on_edit_mode)
        self.btn_delete_mode.clicked.connect(self._on_delete_mode)
        self.btn_new_corner.clicked.connect(self._on_new_corner)
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
        self.btn_delete_mode = QPushButton("Delete Mode")
        self.btn_delete_mode.setToolTip(
            "Delete the selected mode — also deletes its corner columns "
            "and any variants based on it."
        )
        hdr.addWidget(self.btn_new_mode)
        hdr.addWidget(self.btn_edit_mode)
        hdr.addWidget(self.btn_delete_mode)
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
        self.run_set_panel.refresh()

    # --- column filter ---------------------------------------------------

    def _on_clear_all_filters(self) -> None:
        self._set_filter = None
        self.table_model.clear_all_filters()
        self.run_set_panel.refresh()
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
        menu.addAction("New Corner…", self._on_new_corner)
        menu.addAction(
            f"Duplicate {name!r}", lambda: self._duplicate_column(j)
        )
        menu.addAction(f"Rename {name!r}…", lambda: self._rename_column(j))
        if not column.is_managed:
            menu.addAction(
                f"Add {name!r} to a mode…",
                lambda: self._assign_column_mode(j),
            )
        toggle = "Disable" if column.enabled else "Enable"
        menu.addAction(
            f"{toggle} {name!r}", lambda: self._toggle_column_enabled(j)
        )
        selected = self._selected_column_indices()
        if len(selected) > 1:
            sel = tuple(selected)
            menu.addAction(
                f"Enable {len(sel)} selected corners",
                lambda: self._set_selected_enabled(sel, True),
            )
            menu.addAction(
                f"Disable {len(sel)} selected corners",
                lambda: self._set_selected_enabled(sel, False),
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

    def _assign_column_mode(self, j: int) -> None:
        """Fold a raw / foreign column into a mode (痛点: pulled columns)."""
        if not self._cm.modes:
            QMessageBox.warning(self, "Add to a mode", "Create a mode first.")
            return
        name = effective_name(self._cm.columns[j])
        mode, ok = QInputDialog.getItem(
            self, "Add to a mode",
            f"Add corner {name!r} to which mode:",
            sorted(self._cm.modes), 0, False,
        )
        if not ok:
            return
        try:
            new_cm = assign_mode_to_column(self._cm, j, mode)
        except CornerModelError as exc:
            QMessageBox.warning(self, "Add to a mode failed", str(exc))
            return
        self._apply(new_cm)

    def _toggle_column_enabled(self, j: int) -> None:
        column = self._cm.columns[j]
        self._apply(set_column_enabled(self._cm, j, not column.enabled))

    def _selected_column_indices(self) -> list[int]:
        """Data-column indices with at least one selected cell in the table."""
        out: set[int] = set()
        sm = self.table.selectionModel()
        if sm is not None:
            for idx in sm.selectedIndexes():
                j = self.table_model.column_index_at(idx.column())
                if j is not None:
                    out.add(j)
        return sorted(out)

    def _set_selected_enabled(
        self, indices: tuple[int, ...], enabled: bool
    ) -> None:
        self._apply(set_columns_enabled(self._cm, indices, enabled))

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

    # --- correlated axes ------------------------------------------------

    def _on_dimensions(self) -> None:
        """Open the Dimensions manager — define the reusable dimensions a
        corner is built from (痛点 a + h)."""
        _DimensionsDialog(self).exec_()

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

    # --- check status ---------------------------------------------------

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

    def _on_delete_mode(self) -> None:
        item = self.modes_list.currentItem()
        if item is None:
            QMessageBox.warning(self, "Delete Mode", "Select a mode first.")
            return
        name = item.text()
        n = columns_of_mode(self._cm, name)
        detail = f" and its {n} corner column(s)" if n else ""
        if QMessageBox.question(
            self, "Delete Mode", f"Delete mode {name!r}{detail}?"
        ) != QMessageBox.Yes:
            return
        try:
            new_cm = remove_mode(self._cm, name)
        except CornerModelError as exc:
            QMessageBox.warning(self, "Delete Mode failed", str(exc))
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

    def _on_new_corner(self) -> None:
        """Open the New Corner builder — cross dimensions into a corner."""
        _NewCornerDialog(self).exec_()

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


class _RunSetPanel(QWidget):
    """The always-on Run Set panel, docked left of the corner table. Click a
    run set to switch the table's Enable states; Exclusive turns the rest
    off, Additive keeps them. Save the current Enable state as a set, or
    pick corners explicitly via New (痛点 d)."""

    def __init__(self, view: "CornerManagerView") -> None:
        super().__init__(view)
        self._view = view
        self.setMaximumWidth(230)

        v = QVBoxLayout(self)
        v.setContentsMargins(2, 2, 2, 2)
        v.addWidget(QLabel("Run Sets"))
        self._list = QListWidget()
        self._list.setToolTip(
            "Click a run set to switch the corner table's Enable states."
        )
        v.addWidget(self._list, 1)

        v.addWidget(QLabel("Switch mode:"))
        self._radio_exclusive = QRadioButton("Exclusive — others off")
        self._radio_additive = QRadioButton("Additive — others kept")
        self._radio_exclusive.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self._radio_exclusive)
        grp.addButton(self._radio_additive)
        v.addWidget(self._radio_exclusive)
        v.addWidget(self._radio_additive)

        self._btn_new = QPushButton("New…")
        self._btn_new.setToolTip("Pick corners explicitly to form a run set.")
        self._btn_save = QPushButton("Save current as…")
        self._btn_save.setToolTip(
            "Save the corner table's current Enable state as a run set."
        )
        self._btn_del = QPushButton("Delete")
        self._btn_filter = QPushButton("Filter table to set")
        self._btn_filter.setCheckable(True)
        for b in (self._btn_new, self._btn_save, self._btn_del,
                  self._btn_filter):
            v.addWidget(b)

        self._list.itemClicked.connect(self._on_switch)
        self._btn_new.clicked.connect(self._on_new)
        self._btn_save.clicked.connect(self._on_save_current)
        self._btn_del.clicked.connect(self._on_delete)
        self._btn_filter.toggled.connect(self._on_filter_toggled)
        self.refresh()

    def _cm(self) -> CornerModel:
        return self._view.cornermodel()

    def _names(self) -> list[str]:
        return sorted(self._cm().run_sets)

    def refresh(self) -> None:
        names = self._names()
        prev = self._selected_name()
        self._list.blockSignals(True)
        self._list.clear()
        for name in names:
            count = len(self._cm().run_sets[name].columns)
            self._list.addItem(f"{name}  ({count} cols)")
        if prev in names:
            self._list.setCurrentRow(names.index(prev))
        self._list.blockSignals(False)
        self._btn_filter.blockSignals(True)
        self._btn_filter.setChecked(self._view._set_filter is not None)
        self._btn_filter.blockSignals(False)
        self._btn_del.setEnabled(bool(names))
        self._btn_filter.setEnabled(bool(names))

    def _selected_name(self) -> Optional[str]:
        item = self._list.currentItem()
        return None if item is None else item.text().split("  (")[0]

    def _on_switch(self, item: QListWidgetItem) -> None:
        name = item.text().split("  (")[0]
        try:
            new_cm = apply_run_set(
                self._cm(), name, self._radio_additive.isChecked()
            )
        except CornerModelError as exc:
            QMessageBox.warning(self, "Switch run set failed", str(exc))
            return
        self._view._apply(new_cm)

    def _on_new(self) -> None:
        cm = self._cm()
        if not cm.columns:
            QMessageBox.warning(self, "New Run Set", "Add a corner first.")
            return
        name, ok = QInputDialog.getText(
            self, "New Run Set",
            "Run set name (^[A-Za-z][A-Za-z0-9_]*$):",
        )
        if not ok or not name.strip():
            return
        all_names = [effective_name(c) for c in cm.columns]
        dlg = _ColumnPickerDialog(
            "New Run Set — select corners",
            "Tick the corners this run set enables:",
            all_names, parent=self,
        )
        if dlg.exec_() != QDialog.Accepted:
            return
        try:
            new_cm = add_run_set(cm, name.strip(), dlg.checked_columns())
        except CornerModelError as exc:
            QMessageBox.warning(self, "New run set failed", str(exc))
            return
        self._view._apply(new_cm)

    def _on_save_current(self) -> None:
        cm = self._cm()
        if not cm.columns:
            QMessageBox.warning(self, "Save run set", "Add a corner first.")
            return
        name, ok = QInputDialog.getText(
            self, "Save current Enable state as a run set",
            "Run set name (^[A-Za-z][A-Za-z0-9_]*$):",
        )
        if not ok or not name.strip():
            return
        members = tuple(
            effective_name(c) for c in cm.columns if c.enabled
        )
        try:
            new_cm = add_run_set(cm, name.strip(), members)
        except CornerModelError as exc:
            QMessageBox.warning(self, "Save run set failed", str(exc))
            return
        self._view._apply(new_cm)

    def _on_delete(self) -> None:
        name = self._selected_name()
        if name is None:
            QMessageBox.information(
                self, "Delete run set", "Select a run set."
            )
            return
        if QMessageBox.question(
            self, "Delete run set", f"Delete run set {name!r}?"
        ) != QMessageBox.Yes:
            return
        if self._view._set_filter == name:
            self._view._set_filter = None
        self._view._apply(remove_run_set(self._cm(), name))

    def _on_filter_toggled(self, checked: bool) -> None:
        if checked:
            name = self._selected_name()
            if name is None:
                QMessageBox.information(
                    self, "Filter to run set", "Select a run set first."
                )
                self._btn_filter.blockSignals(True)
                self._btn_filter.setChecked(False)
                self._btn_filter.blockSignals(False)
                return
            self._view._set_filter = name
        else:
            self._view._set_filter = None
        self._view._apply_filters()


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


class _DimensionGridDialog(QDialog):
    """Author one dimension as a small grid: each level is a row, each member
    variable a column. Set a Model file to make it a section-bearing
    dimension — then a 'section' column appears, one process section per
    level (痛点 h)."""

    def __init__(
        self, axis: Optional[CorrelatedAxis] = None,
        taken_names: tuple[str, ...] = (),
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._editing = axis is not None
        self._taken = set(taken_names)
        self._axis: Optional[CorrelatedAxis] = None
        self._has_section = False
        self.setWindowTitle(
            "Edit Dimension" if self._editing else "New Dimension"
        )
        self.setMinimumWidth(480)

        v = QVBoxLayout(self)
        v.addWidget(QLabel(
            "A dimension is a small table: each level is a row, each member "
            "variable a column. Members move together — picking a level "
            "sets every member at once. Fill in a Model file to make it a "
            "section dimension (the process-corner case)."
        ))
        form = QFormLayout()
        self._name_edit = QLineEdit(axis.name if self._editing else "")
        if self._editing:
            self._name_edit.setReadOnly(True)
        form.addRow("Dimension name:", self._name_edit)
        self._model_file_edit = QLineEdit()
        self._model_file_edit.setPlaceholderText(
            "leave blank unless levels pick a model-file section"
        )
        form.addRow("Model file (optional):", self._model_file_edit)
        v.addLayout(form)

        self._table = QTableWidget(0, 1, self)
        self._table.setHorizontalHeaderLabels(["Level"])
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self._table.verticalHeader().setDefaultSectionSize(24)
        self._table.horizontalHeader().sectionDoubleClicked.connect(
            self._rename_member
        )
        v.addWidget(self._table)

        btns = QHBoxLayout()
        for label, slot in (
            ("+ Level", self._add_level),
            ("+ Variable", self._add_member),
            ("Remove Level", self._remove_level),
            ("Remove Variable", self._remove_member),
        ):
            b = QPushButton(label)
            b.clicked.connect(slot)
            btns.addWidget(b)
        v.addLayout(btns)
        v.addWidget(QLabel(
            "Double-click a variable header to rename it."
        ))

        bb = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        bb.accepted.connect(self._on_ok)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        if self._editing:
            self._load(axis)
        else:
            self._add_member(initial="var1")
            self._add_level()
        self._model_file_edit.textChanged.connect(self._sync_section_column)

    # --- column bookkeeping --------------------------------------------
    def _member_start(self) -> int:
        """First member-variable column (1, or 2 when a section column is in)."""
        return 2 if self._has_section else 1

    def _member_names(self) -> list[str]:
        return [
            self._table.horizontalHeaderItem(c).text()
            for c in range(self._member_start(), self._table.columnCount())
        ]

    def _sync_section_column(self, *_args) -> None:
        want = bool(self._model_file_edit.text().strip())
        if want and not self._has_section:
            self._table.insertColumn(1)
            self._table.setHorizontalHeaderItem(1, QTableWidgetItem("section"))
            self._has_section = True
        elif not want and self._has_section:
            self._table.removeColumn(1)
            self._has_section = False

    def _add_member(self, initial: Optional[str] = None) -> None:
        if initial is not None:
            name = initial
        else:
            name, ok = QInputDialog.getText(
                self, "Add variable", "Member variable name:"
            )
            if not ok or not name.strip():
                return
            name = name.strip()
        if not re.match(r"^[A-Za-z][A-Za-z0-9_]*$", name):
            QMessageBox.warning(
                self, "Add variable",
                f"{name!r} is not a valid variable name.",
            )
            return
        if name in self._member_names():
            QMessageBox.warning(
                self, "Add variable", f"{name!r} is already a member."
            )
            return
        c = self._table.columnCount()
        self._table.insertColumn(c)
        self._table.setHorizontalHeaderItem(c, QTableWidgetItem(name))

    def _add_level(self) -> None:
        self._table.insertRow(self._table.rowCount())

    def _remove_member(self) -> None:
        c = self._table.currentColumn()
        if c < self._member_start():
            QMessageBox.information(
                self, "Remove variable",
                "Select a cell in a member-variable column to remove it.",
            )
            return
        self._table.removeColumn(c)

    def _remove_level(self) -> None:
        r = self._table.currentRow()
        if r >= 0:
            self._table.removeRow(r)

    def _rename_member(self, section: int) -> None:
        if section < self._member_start():
            return
        cur = self._table.horizontalHeaderItem(section).text()
        name, ok = QInputDialog.getText(
            self, "Rename variable", "Member variable name:", text=cur
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        if not re.match(r"^[A-Za-z][A-Za-z0-9_]*$", name):
            QMessageBox.warning(
                self, "Rename variable",
                f"{name!r} is not a valid variable name.",
            )
            return
        if name != cur and name in self._member_names():
            QMessageBox.warning(
                self, "Rename variable", f"{name!r} is already a member."
            )
            return
        self._table.setHorizontalHeaderItem(section, QTableWidgetItem(name))

    def _load(self, axis: CorrelatedAxis) -> None:
        if axis.model_file is not None:
            self._model_file_edit.setText(axis.model_file)
            self._table.insertColumn(1)
            self._table.setHorizontalHeaderItem(1, QTableWidgetItem("section"))
            self._has_section = True
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

    def _cell(self, row: int, col: int) -> str:
        item = self._table.item(row, col)
        return item.text().strip() if item is not None else ""

    def _on_ok(self) -> None:
        name = self._name_edit.text().strip()
        if not self._editing:
            if not re.match(r"^[A-Za-z][A-Za-z0-9_]*$", name):
                QMessageBox.warning(
                    self, "New Dimension",
                    "Dimension name must start with a letter and use only "
                    "letters, digits, and underscores.",
                )
                return
            if name in self._taken:
                QMessageBox.warning(
                    self, "New Dimension",
                    f"A dimension named {name!r} already exists.",
                )
                return
        model_file = self._model_file_edit.text().strip() or None
        members = self._member_names()
        if not members and model_file is None:
            QMessageBox.warning(
                self, "Dimension",
                "Add a member variable, or fill in a Model file to make "
                "this a section dimension.",
            )
            return
        if self._table.rowCount() == 0:
            QMessageBox.warning(self, "Dimension", "Add at least one level.")
            return
        tuples: list[CorrelatedTuple] = []
        seen: set[str] = set()
        for r in range(self._table.rowCount()):
            label = self._cell(r, 0)
            if not label:
                QMessageBox.warning(
                    self, "Dimension", f"Row {r + 1}: the Level needs a name."
                )
                return
            if label in seen:
                QMessageBox.warning(
                    self, "Dimension", f"Level {label!r} is used twice."
                )
                return
            seen.add(label)
            section: Optional[str] = None
            if model_file is not None:
                section = self._cell(r, 1)
                if not section:
                    QMessageBox.warning(
                        self, "Dimension",
                        f"Level {label!r}: a section dimension needs a "
                        f"section for every level.",
                    )
                    return
            values: dict[str, str] = {}
            for c in range(self._member_start(), self._table.columnCount()):
                val = self._cell(r, c)
                if not val:
                    QMessageBox.warning(
                        self, "Dimension",
                        f"Level {label!r}: variable "
                        f"{self._member_names()[c - self._member_start()]!r} "
                        f"has no value.",
                    )
                    return
                values[self._table.horizontalHeaderItem(c).text()] = val
            tuples.append(CorrelatedTuple(
                label=label, values=values, section=section
            ))
        self._axis = CorrelatedAxis(
            name=name, members=tuple(members), tuples=tuple(tuples),
            model_file=model_file,
        )
        self.accept()

    def axis(self) -> Optional[CorrelatedAxis]:
        return self._axis


class _DimensionsDialog(QDialog):
    """Manage the project's reusable dimensions — New / Edit / Delete. One
    edit here updates every corner that crosses the dimension."""

    def __init__(self, view: "CornerManagerView") -> None:
        super().__init__(view)
        self._view = view
        self.setWindowTitle("Dimensions")
        self.setMinimumWidth(460)

        v = QVBoxLayout(self)
        v.addWidget(QLabel(
            "A dimension is a reusable list of levels (e.g. Process: "
            "TT/SS/FF…). Cross dimensions in New Corner to build a corner; "
            "editing a level here updates every corner that uses it."
        ))
        self._list = QListWidget()
        v.addWidget(self._list)
        ab = QHBoxLayout()
        self._btn_new = QPushButton("New Dimension")
        self._btn_edit = QPushButton("Edit")
        self._btn_del = QPushButton("Delete")
        for b in (self._btn_new, self._btn_edit, self._btn_del):
            ab.addWidget(b)
        v.addLayout(ab)

        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        self._btn_new.clicked.connect(self._on_new)
        self._btn_edit.clicked.connect(self._on_edit)
        self._btn_del.clicked.connect(self._on_delete)
        self._refresh()

    def _cm(self) -> CornerModel:
        return self._view.cornermodel()

    def _names(self) -> list[str]:
        return sorted(self._cm().correlated_axes)

    def _refresh(self) -> None:
        cm = self._cm()
        prev = self._list.currentRow()
        self._list.clear()
        for name in self._names():
            ax = cm.correlated_axes[name]
            tag = "  ·section" if ax.model_file else ""
            members = f" ({', '.join(ax.members)})" if ax.members else ""
            self._list.addItem(
                f"{name}  —  {len(ax.tuples)} levels{members}{tag}"
            )
        if 0 <= prev < self._list.count():
            self._list.setCurrentRow(prev)

    def _selected(self) -> Optional[str]:
        row = self._list.currentRow()
        names = self._names()
        return names[row] if 0 <= row < len(names) else None

    def _on_new(self) -> None:
        dlg = _DimensionGridDialog(
            taken_names=tuple(self._names()), parent=self
        )
        if dlg.exec_() != QDialog.Accepted or dlg.axis() is None:
            return
        try:
            new_cm = add_correlated_axis(self._cm(), dlg.axis())
        except CornerModelError as exc:
            QMessageBox.warning(self, "New Dimension failed", str(exc))
            return
        self._view._apply(new_cm)
        self._refresh()

    def _on_edit(self) -> None:
        name = self._selected()
        if name is None:
            QMessageBox.information(
                self, "Edit Dimension", "Select a dimension."
            )
            return
        dlg = _DimensionGridDialog(
            axis=self._cm().correlated_axes[name], parent=self
        )
        if dlg.exec_() != QDialog.Accepted or dlg.axis() is None:
            return
        try:
            new_cm = update_correlated_axis(self._cm(), dlg.axis())
        except CornerModelError as exc:
            QMessageBox.warning(self, "Edit Dimension failed", str(exc))
            return
        self._view._apply(new_cm)
        self._refresh()

    def _on_delete(self) -> None:
        name = self._selected()
        if name is None:
            QMessageBox.information(
                self, "Delete Dimension", "Select a dimension."
            )
            return
        if QMessageBox.question(
            self, "Delete Dimension", f"Delete dimension {name!r}?"
        ) != QMessageBox.Yes:
            return
        try:
            new_cm = remove_correlated_axis(self._cm(), name)
        except CornerModelError as exc:
            QMessageBox.warning(self, "Delete Dimension failed", str(exc))
            return
        self._view._apply(new_cm)
        self._refresh()


class _NewCornerDialog(QDialog):
    """Build a corner by crossing dimensions. Tick a dimension and, under it,
    the levels this corner uses — a scalar corner just ticks one level each.
    One Create stamps the corner onto every ticked mode (痛点 a + h)."""

    def __init__(self, view: "CornerManagerView") -> None:
        super().__init__(view)
        self._view = view
        self._inline: list[CorrelatedAxis] = []
        self.setWindowTitle("New Corner")
        self.setMinimumWidth(520)

        v = QVBoxLayout(self)
        v.addWidget(QLabel(
            "A corner is a crossing of dimensions. Tick the dimensions to "
            "cross and, under each, the levels this corner uses."
        ))
        form = QFormLayout()
        self._corner_name = QLineEdit()
        form.addRow("Corner label:", self._corner_name)
        v.addLayout(form)
        v.addWidget(QLabel("Stamp the corner onto these modes:"))
        self._mode_list = QListWidget()
        self._mode_list.setMaximumHeight(96)
        v.addWidget(self._mode_list)
        v.addWidget(QLabel("Cross dimensions — tick the levels to use:"))
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        v.addWidget(self._tree, 1)
        db = QHBoxLayout()
        self._btn_newdim = QPushButton("New dimension…")
        self._btn_inline = QPushButton("+ Inline dimension")
        db.addWidget(self._btn_newdim)
        db.addWidget(self._btn_inline)
        db.addStretch(1)
        v.addLayout(db)
        self._count_label = QLabel()
        v.addWidget(self._count_label)

        bb = QDialogButtonBox(QDialogButtonBox.Close)
        self._btn_create = QPushButton("Create corner")
        bb.addButton(self._btn_create, QDialogButtonBox.AcceptRole)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        self._btn_create.clicked.connect(self._on_create)
        self._btn_newdim.clicked.connect(self._on_new_dimension)
        self._btn_inline.clicked.connect(self._on_inline_dimension)
        self._tree.itemChanged.connect(self._update_count)
        self._refresh()

    def _cm(self) -> CornerModel:
        return self._view.cornermodel()

    def _checked_modes(self) -> list[str]:
        return [
            self._mode_list.item(i).text()
            for i in range(self._mode_list.count())
            if self._mode_list.item(i).checkState() == Qt.Checked
        ]

    def _axis_for(self, key) -> CorrelatedAxis:
        kind, ref = key
        if kind == "lib":
            return self._cm().correlated_axes[ref]
        return self._inline[ref]

    def _add_dim_item(self, ax: CorrelatedAxis, key) -> None:
        tag = "  ·section" if ax.model_file else ""
        kind = "inline" if key[0] == "inline" else ""
        suffix = "   (inline)" if kind else ""
        top = QTreeWidgetItem(self._tree, [f"{ax.name}{tag}{suffix}"])
        top.setFlags(
            top.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate
        )
        top.setData(0, Qt.UserRole, key)
        top.setCheckState(0, Qt.Unchecked)
        for ct in ax.tuples:
            ch = QTreeWidgetItem(top, [ct.label])
            ch.setFlags(ch.flags() | Qt.ItemIsUserCheckable)
            ch.setData(0, Qt.UserRole, ct.label)
            ch.setCheckState(0, Qt.Unchecked)
        top.setExpanded(True)

    def _refresh(self) -> None:
        cm = self._cm()
        prev_modes = set(self._checked_modes())
        self._mode_list.blockSignals(True)
        self._mode_list.clear()
        for mode_name in sorted(cm.modes):
            item = QListWidgetItem(mode_name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(
                Qt.Checked if mode_name in prev_modes else Qt.Unchecked
            )
            self._mode_list.addItem(item)
        self._mode_list.blockSignals(False)
        self._tree.blockSignals(True)
        self._tree.clear()
        for name in sorted(cm.correlated_axes):
            self._add_dim_item(cm.correlated_axes[name], ("lib", name))
        for i, ax in enumerate(self._inline):
            self._add_dim_item(ax, ("inline", i))
        self._tree.blockSignals(False)
        self._update_count()

    def _crossed(self) -> list:
        """(key, axis, [selected level labels]) for every crossed dimension."""
        out = []
        for i in range(self._tree.topLevelItemCount()):
            top = self._tree.topLevelItem(i)
            if top.checkState(0) == Qt.Unchecked:
                continue
            labels = [
                top.child(j).data(0, Qt.UserRole)
                for j in range(top.childCount())
                if top.child(j).checkState(0) == Qt.Checked
            ]
            if not labels:
                continue
            key = top.data(0, Qt.UserRole)
            out.append((key, self._axis_for(key), labels))
        return out

    def _update_count(self, *_args) -> None:
        crossed = self._crossed()
        if not crossed:
            self._count_label.setText("Tick at least one dimension level.")
            return
        counts = [len(labels) for _key, _ax, labels in crossed]
        total = 1
        for c in counts:
            total *= c
        self._count_label.setText(
            f"{' × '.join(str(c) for c in counts)} = {total} corner points"
        )

    def _on_new_dimension(self) -> None:
        taken = tuple(self._cm().correlated_axes)
        dlg = _DimensionGridDialog(taken_names=taken, parent=self)
        if dlg.exec_() != QDialog.Accepted or dlg.axis() is None:
            return
        try:
            new_cm = add_correlated_axis(self._cm(), dlg.axis())
        except CornerModelError as exc:
            QMessageBox.warning(self, "New Dimension failed", str(exc))
            return
        self._view._apply(new_cm)
        self._refresh()

    def _on_inline_dimension(self) -> None:
        taken = tuple(self._cm().correlated_axes) + tuple(
            ax.name for ax in self._inline
        )
        dlg = _DimensionGridDialog(taken_names=taken, parent=self)
        if dlg.exec_() != QDialog.Accepted or dlg.axis() is None:
            return
        self._inline.append(dlg.axis())
        self._refresh()

    def _on_create(self) -> None:
        label = self._corner_name.text().strip()
        if not re.match(r"^[A-Za-z0-9_]+$", label):
            QMessageBox.warning(
                self, "Create corner",
                "The corner label must use only letters, digits, and "
                "underscores.",
            )
            return
        crossed = self._crossed()
        if not crossed:
            QMessageBox.warning(
                self, "Create corner",
                "Tick at least one dimension and the levels to use.",
            )
            return
        modes = self._checked_modes()
        if not modes:
            QMessageBox.warning(
                self, "Create corner", "Tick at least one mode to stamp."
            )
            return
        lib_axes: list[str] = []
        selected: dict[str, tuple[str, ...]] = {}
        inline_axes: list[CorrelatedAxis] = []
        for key, ax, labels in crossed:
            keep = set(labels)
            if key[0] == "lib":
                lib_axes.append(ax.name)
                if len(labels) < len(ax.tuples):
                    selected[ax.name] = tuple(labels)
            else:
                inline_axes.append(_dc_replace(ax, tuples=tuple(
                    t for t in ax.tuples if t.label in keep
                )))
        new_cm = self._cm()
        created: list[str] = []
        for mode in modes:
            column = Column(
                mode=mode, enabled=True, pvt_vars={}, models=(),
                pvt_label=label, correlated_axes=tuple(lib_axes),
                selected_levels=dict(selected),
                inline_axes=tuple(inline_axes),
            )
            try:
                new_cm = add_column(new_cm, column)
            except CornerModelError as exc:
                QMessageBox.warning(self, "Create corner failed", str(exc))
                return
            created.append(effective_name(column))
        self._view._apply(new_cm)
        QMessageBox.information(
            self, "New Corner",
            f"Created {len(created)} corner(s): {', '.join(created)}.",
        )
        self.accept()
