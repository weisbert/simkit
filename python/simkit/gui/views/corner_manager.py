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
    CorrelatedAxis,
    CorrelatedTuple,
    ModelEntry,
    PvtTemplate,
    TemplateColumn,
    Variant,
    add_column,
    add_correlated_axis,
    add_mode,
    add_pvt_template,
    add_run_set,
    add_variant,
    apply_run_set,
    apply_template,
    check_cornermodel,
    delete_column,
    effective_name,
    export_library,
    import_library,
    library_to_dict,
    load_library,
    mode_from_column,
    move_column,
    rename_column,
    rename_variable,
    reorder_columns,
    remove_variable,
    run_set_membership,
    set_column_enabled,
    set_mode_var,
    set_var_order,
    set_variant_var,
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
        self._loading_variant_vars = False
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
            "Define operating modes and edit their register values."
        )
        self.btn_variants = QPushButton("Variants…")
        self.btn_variants.setToolTip(
            "Manage variants — delta overlays on a mode."
        )
        self.btn_templates = QPushButton("Templates…")
        self.btn_templates.setToolTip(
            "Author / apply reusable PVT templates; import / export the "
            "template library."
        )
        self.btn_axes = QPushButton("Axes…")
        self.btn_axes.setToolTip(
            "Manage correlated axes — variable bundles that vary together."
        )
        self.btn_run_sets = QPushButton("Run Sets…")
        self.btn_run_sets.setToolTip(
            "Manage run sets — named cross-mode corner selections."
        )
        self.btn_profile = QPushButton("Profile…")
        self.btn_profile.setToolTip(
            "Inspect the bound PVT profile (semantic mapping layer)."
        )
        for b in (self.btn_modes, self.btn_variants, self.btn_templates,
                  self.btn_axes, self.btn_run_sets, self.btn_profile):
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
        self.table.verticalHeader().setDefaultSectionSize(24)
        # Variable names live in column 0 now — the row header is redundant.
        self.table.verticalHeader().hide()
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
        self._build_variants_dialog()
        self._build_templates_dialog()
        self._build_axes_dialog()
        self._build_run_sets_dialog()
        self._build_profile_dialog()

        self.btn_modes.clicked.connect(
            lambda: self._open_dialog(self._modes_dialog))
        self.btn_variants.clicked.connect(
            lambda: self._open_dialog(self._variants_dialog))
        self.btn_templates.clicked.connect(
            lambda: self._open_dialog(self._templates_dialog))
        self.btn_axes.clicked.connect(
            lambda: self._open_dialog(self._axes_dialog))
        self.btn_run_sets.clicked.connect(
            lambda: self._open_dialog(self._run_sets_dialog))
        self.btn_profile.clicked.connect(
            lambda: self._open_dialog(self._profile_dialog))

        self.modes_list.currentItemChanged.connect(self._on_mode_selected)
        self.mode_vars.itemChanged.connect(self._on_mode_var_changed)
        self.variants_list.currentItemChanged.connect(
            self._on_variant_selected
        )
        self.variant_vars.itemChanged.connect(self._on_variant_var_changed)
        self.table_model.cornermodelChanged.connect(self._on_table_edited)
        self.btn_new_mode.clicked.connect(self._on_new_mode)
        self.btn_new_column.clicked.connect(self._on_new_column)
        self.btn_new_variant.clicked.connect(self._on_new_variant)
        self.btn_new_template.clicked.connect(self._on_new_template)
        self.btn_new_axis.clicked.connect(self._on_new_axis)
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
        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("Modes"))
        hdr.addStretch(1)
        self.btn_new_mode = QPushButton("New Mode")
        self.btn_new_mode.setToolTip(
            "Define a new operating mode — a named bag of register values "
            "(e.g. BT_2G_RX). Columns reference a mode."
        )
        hdr.addWidget(self.btn_new_mode)
        v.addLayout(hdr)
        self.modes_list = QListWidget()
        v.addWidget(self.modes_list)
        v.addWidget(QLabel(
            "Registers (edit once — every referencing column syncs)"
        ))
        self.mode_vars = QTableWidget(0, 2)
        self.mode_vars.setHorizontalHeaderLabels(["Register", "Value"])
        self.mode_vars.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.mode_vars.verticalHeader().setDefaultSectionSize(24)
        v.addWidget(self.mode_vars)

    def _build_variants_dialog(self) -> None:
        self._variants_dialog = QDialog(self)
        self._variants_dialog.setWindowTitle("Variants")
        self._variants_dialog.resize(420, 460)
        v = QVBoxLayout(self._variants_dialog)
        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("Variants (delta overlay on a mode)"))
        hdr.addStretch(1)
        self.btn_new_variant = QPushButton("New Variant")
        hdr.addWidget(self.btn_new_variant)
        v.addLayout(hdr)
        self.variants_list = QListWidget()
        v.addWidget(self.variants_list)
        v.addWidget(QLabel("Overridden registers (absolute values)"))
        self.variant_vars = QTableWidget(0, 2)
        self.variant_vars.setHorizontalHeaderLabels(
            ["Overridden register", "Absolute value"]
        )
        self.variant_vars.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.variant_vars.verticalHeader().setDefaultSectionSize(24)
        v.addWidget(self.variant_vars)

    def _build_templates_dialog(self) -> None:
        self._templates_dialog = QDialog(self)
        self._templates_dialog.setWindowTitle("PVT Templates")
        self._templates_dialog.resize(440, 420)
        v = QVBoxLayout(self._templates_dialog)
        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("PVT Templates"))
        hdr.addStretch(1)
        self.btn_new_template = QPushButton("New Template")
        self.btn_new_template.setToolTip(
            "Author a reusable PVT template — a list of corner columns "
            "you can apply to any mode or variant (pain point a)."
        )
        hdr.addWidget(self.btn_new_template)
        v.addLayout(hdr)
        self.templates_list = QListWidget()
        v.addWidget(self.templates_list)
        tmpl_btns = QHBoxLayout()
        self.btn_apply_template = QPushButton("Apply to mode")
        self.btn_unbind_template = QPushButton("Unbind")
        tmpl_btns.addWidget(self.btn_apply_template)
        tmpl_btns.addWidget(self.btn_unbind_template)
        v.addLayout(tmpl_btns)
        lib_btns = QHBoxLayout()
        self.btn_export_lib = QPushButton("Export template library")
        self.btn_import_lib = QPushButton("Import template library")
        lib_btns.addWidget(self.btn_export_lib)
        lib_btns.addWidget(self.btn_import_lib)
        v.addLayout(lib_btns)

    def _build_axes_dialog(self) -> None:
        self._axes_dialog = QDialog(self)
        self._axes_dialog.setWindowTitle("Correlated Axes")
        self._axes_dialog.resize(440, 340)
        v = QVBoxLayout(self._axes_dialog)
        hdr = QHBoxLayout()
        hdr.addWidget(QLabel(
            "Correlated axes (bound var bundle, cross-product as one axis)"
        ))
        hdr.addStretch(1)
        self.btn_new_axis = QPushButton("New Axis")
        self.btn_new_axis.setToolTip(
            "Define a correlated axis — a bundle of variables that must "
            "vary together, cross-multiplied as one axis (pain point h)."
        )
        hdr.addWidget(self.btn_new_axis)
        v.addLayout(hdr)
        self.axes_list = QListWidget()
        v.addWidget(self.axes_list)

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

    def _build_profile_dialog(self) -> None:
        self._profile_dialog = QDialog(self)
        self._profile_dialog.setWindowTitle("PVT Profile")
        self._profile_dialog.resize(380, 260)
        v = QVBoxLayout(self._profile_dialog)
        v.addWidget(QLabel(
            "PVT Profile (semantic mapping layer, read-only)"
        ))
        self.profile_list = QListWidget()
        v.addWidget(self.profile_list)

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
        self._refresh_profile_panel()
        self._refresh_modes_panel()
        self._refresh_variants_panel()
        self._refresh_templates_panel()
        self._refresh_run_sets_panel()

    def _refresh_profile_panel(self) -> None:
        self.profile_list.clear()
        if self._profile is None:
            bound = self._cm.pvt_profile
            self.profile_list.addItem(
                "(no PVT profile bound)" if bound is None
                else f"⚠ profile {bound} bound but not loaded"
            )
            return
        self.profile_list.addItem(f"profile: {self._profile.name}")
        for axis_name in sorted(self._profile.axes):
            levels = ", ".join(sorted(self._profile.axes[axis_name].levels))
            self.profile_list.addItem(f"  {axis_name}: {levels}")

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
            self._show_variable_menu(var, gpos)

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

    def _show_variable_menu(self, var: str, gpos) -> None:
        menu = QMenu(self)
        menu.addAction(
            f"Rename {var!r}…", lambda: self._rename_variable(var)
        )
        _temp, design = self.table_model.variable_order()
        if var in design:
            i = design.index(var)
            up = menu.addAction("Move Up", lambda: self._move_var(var, -1))
            up.setEnabled(i > 0)
            down = menu.addAction(
                "Move Down", lambda: self._move_var(var, 1)
            )
            down.setEnabled(i < len(design) - 1)
        menu.addSeparator()
        menu.addAction(f"Remove {var!r}", lambda: self._remove_variable(var))
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

    # --- variants panel --------------------------------------------------

    def _refresh_variants_panel(self) -> None:
        prev = self._selected_variant_name()
        self.variants_list.blockSignals(True)
        self.variants_list.clear()
        names = sorted(self._cm.variants)
        for name in names:
            base = self._cm.variants[name].base_mode
            self.variants_list.addItem(f"{name}  → {base}")
        self.variants_list.blockSignals(False)
        if prev in names:
            self.variants_list.setCurrentRow(names.index(prev))
        elif names:
            self.variants_list.setCurrentRow(0)
        else:
            self._populate_variant_vars(None)

    def _selected_variant_name(self) -> Optional[str]:
        item = self.variants_list.currentItem()
        if item is None:
            return None
        return item.text().split("  → ")[0]

    def _on_variant_selected(self, *_args) -> None:
        self._populate_variant_vars(self._selected_variant_name())

    def _populate_variant_vars(self, variant_name: Optional[str]) -> None:
        self._loading_variant_vars = True
        self.variant_vars.setRowCount(0)
        if variant_name is not None and variant_name in self._cm.variants:
            variant = self._cm.variants[variant_name]
            for var, value in sorted(variant.vars.items()):
                row = self.variant_vars.rowCount()
                self.variant_vars.insertRow(row)
                name_item = QTableWidgetItem(var)
                name_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                self.variant_vars.setItem(row, 0, name_item)
                self.variant_vars.setItem(row, 1, QTableWidgetItem(value))
        self._loading_variant_vars = False

    def _on_variant_var_changed(self, item: QTableWidgetItem) -> None:
        if self._loading_variant_vars or item.column() != 1:
            return
        variant_name = self._selected_variant_name()
        if variant_name is None:
            return
        var = self.variant_vars.item(item.row(), 0).text()
        new_value = item.text().strip()
        if new_value == "":
            self._refresh_variants_panel()
            return
        try:
            new_cm = set_variant_var(
                self._cm, variant_name, var, new_value
            )
        except CornerModelError as exc:
            QMessageBox.warning(self, "Edit variant failed", str(exc))
            self._refresh_variants_panel()
            return
        self._apply(new_cm)

    def _on_new_variant(self) -> None:
        if not self._cm.modes:
            QMessageBox.warning(self, "New Variant", "Create a mode first.")
            return
        base, ok = QInputDialog.getItem(
            self, "New Variant", "Base mode:", sorted(self._cm.modes), 0, False
        )
        if not ok:
            return
        name, ok = QInputDialog.getText(
            self, "New Variant", "Variant name (^[A-Za-z][A-Za-z0-9_]*$):"
        )
        if not ok or not name.strip():
            return
        text, ok = QInputDialog.getMultiLineText(
            self, "New Variant — override registers",
            "One var=absolute-value per line (may only override "
            "registers the base mode already has):",
            "d_div12_en=0",
        )
        if not ok:
            return
        try:
            overlay = _parse_var_lines(text)
            new_cm = add_variant(self._cm, Variant(
                name=name.strip(), base_mode=base, vars=overlay
            ))
        except (ValueError, CornerModelError) as exc:
            QMessageBox.warning(self, "New variant failed", str(exc))
            return
        self._apply(new_cm)

    def _on_new_template(self) -> None:
        if not self._cm.modes:
            QMessageBox.warning(self, "New Template", "Create a mode first.")
            return
        name, ok = QInputDialog.getText(
            self, "New Template", "Template name (^[A-Za-z][A-Za-z0-9_]*$):"
        )
        if not ok or not name.strip():
            return
        cols_text, ok = QInputDialog.getMultiLineText(
            self, "New Template — columns",
            "One column per line — pvt_label: var=value, var=value\n"
            "(use +axisName to attach an existing correlated axis):",
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
            QMessageBox.warning(self, "New template failed", str(exc))
            return
        self._apply(new_cm)

    def _on_new_axis(self) -> None:
        name, ok = QInputDialog.getText(
            self, "New Axis", "Axis name (^[A-Za-z][A-Za-z0-9_]*$):"
        )
        if not ok or not name.strip():
            return
        members_text, ok = QInputDialog.getText(
            self, "New Axis",
            "Member variables that vary together (comma-separated):",
        )
        if not ok or not members_text.strip():
            return
        members = tuple(
            m.strip() for m in members_text.split(",") if m.strip()
        )
        tuples_text, ok = QInputDialog.getMultiLineText(
            self, "New Axis — correlated points",
            "One point per line — label: member=value, member=value\n"
            "(each line must assign exactly the members above):",
            "TT: " + ", ".join(f"{m}=" for m in members),
        )
        if not ok:
            return
        try:
            tuples = _parse_axis_tuples(tuples_text)
            new_cm = add_correlated_axis(self._cm, CorrelatedAxis(
                name=name.strip(), members=members, tuples=tuples,
            ))
        except (ValueError, CornerModelError) as exc:
            QMessageBox.warning(self, "New axis failed", str(exc))
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
        self.axes_list.clear()
        for name, axis in sorted(self._cm.correlated_axes.items()):
            self.axes_list.addItem(
                f"{name}  ({len(axis.tuples)} pts · "
                f"{'+'.join(axis.members)})"
            )

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

    # --- templates: apply / unbind --------------------------------------

    _VARIANT_PREFIX = "Variant: "

    def _on_apply_template(self) -> None:
        tmpl = self._selected_template_name()
        if tmpl is None:
            QMessageBox.warning(
                self, "Apply template", "Select a template first."
            )
            return
        if not self._cm.modes:
            QMessageBox.warning(
                self, "Apply template", "Create a mode first."
            )
            return
        targets = sorted(self._cm.modes) + [
            self._VARIANT_PREFIX + v for v in sorted(self._cm.variants)
        ]
        target, ok = QInputDialog.getItem(
            self, "Apply template",
            f"Apply template {tmpl} to (mode / variant):",
            targets, 0, False,
        )
        if not ok:
            return
        try:
            new_cm = self._apply_template_to_target(tmpl, target)
        except CornerModelError as exc:
            QMessageBox.warning(self, "Apply template failed", str(exc))
            return
        self._apply(new_cm)

    def _apply_template_to_target(
        self, tmpl: str, target: str
    ) -> CornerModel:
        if target.startswith(self._VARIANT_PREFIX):
            vname = target[len(self._VARIANT_PREFIX):]
            base = self._cm.variants[vname].base_mode
            return apply_template(self._cm, base, tmpl, variant=vname)
        return apply_template(self._cm, target, tmpl)

    def _on_unbind_template(self) -> None:
        tmpl = self._selected_template_name()
        if tmpl is None:
            QMessageBox.warning(
                self, "Unbind template", "Select a template first."
            )
            return
        bound = [
            b for b in self._cm.template_bindings if b.template == tmpl
        ]
        if not bound:
            QMessageBox.warning(
                self, "Unbind template",
                f"Template {tmpl} is not bound to any mode / variant."
            )
            return
        labels = [
            (self._VARIANT_PREFIX + b.variant) if b.variant else b.mode
            for b in bound
        ]
        label, ok = QInputDialog.getItem(
            self, "Unbind template",
            f"Unbind {tmpl} from which target (its columns stay, frozen):",
            labels, 0, False,
        )
        if not ok:
            return
        binding = bound[labels.index(label)]
        self._apply(unbind_template(
            self._cm, binding.mode, tmpl, variant=binding.variant
        ))

    # --- new mode / new column ------------------------------------------

    def _on_new_mode(self) -> None:
        # A mode is best *derived* from a corner the user already authored in
        # Cadence — pick a column, classify its vars. Only fall back to typing
        # vars by hand when the project has no column to derive from yet.
        if self._cm.columns:
            self._new_mode_from_column()
        else:
            self._new_mode_manual()

    def _new_mode_from_column(self) -> None:
        dialog = _NewModeDialog(self._cm, self)
        if dialog.exec_() != QDialog.Accepted:
            return
        try:
            new_cm = mode_from_column(
                self._cm, dialog.selected_column_index(),
                dialog.mode_name(), dialog.register_vars(),
                dialog.pvt_label(),
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
            "Unticked → mode registers (value editable). "
            "Ticked → per-column PVT variables."
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
        for var in sorted(col.pvt_vars):
            tup = col.pvt_vars[var]
            if len(tup) != 1:
                continue   # a swept var cannot be a scalar register
            row = self._table.rowCount()
            self._table.insertRow(row)
            name_item = QTableWidgetItem(var)
            name_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            self._table.setItem(row, 0, name_item)
            self._table.setItem(row, 1, QTableWidgetItem(tup[0]))
            pvt_item = QTableWidgetItem()
            pvt_item.setFlags(
                Qt.ItemIsSelectable | Qt.ItemIsEnabled
                | Qt.ItemIsUserCheckable
            )
            pvt_item.setCheckState(
                Qt.Checked if _default_is_pvt(var) else Qt.Unchecked
            )
            self._table.setItem(row, 2, pvt_item)

    def selected_column_index(self) -> int:
        return self._column_combo.currentIndex()

    def mode_name(self) -> str:
        return self._mode_edit.text().strip()

    def pvt_label(self) -> str:
        return self._label_edit.text().strip()

    def register_vars(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for row in range(self._table.rowCount()):
            if self._table.item(row, 2).checkState() == Qt.Checked:
                continue   # ticked → PVT, not a register
            var = self._table.item(row, 0).text()
            out[var] = self._table.item(row, 1).text().strip()
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


def _parse_axis_tuples(text: str) -> tuple[CorrelatedTuple, ...]:
    """Parse the New-Axis multi-line dialog into CorrelatedTuples."""
    tuples: list[CorrelatedTuple] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        label, rest = _split_label_line(line)
        pairs, axes = _parse_kv_comma(rest)
        if axes:
            raise ValueError(
                f"point {label!r}: '+axis' tokens are not valid here"
            )
        tuples.append(CorrelatedTuple(label=label, values=pairs))
    if not tuples:
        raise ValueError("a correlated axis needs at least one point")
    return tuple(tuples)
