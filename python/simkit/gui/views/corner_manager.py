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

import fnmatch
import json
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSplitter,
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
    Variant,
    add_column,
    add_mode,
    add_run_set,
    add_variant,
    apply_run_set,
    apply_template,
    check_cornermodel,
    effective_name,
    export_library,
    import_library,
    library_to_dict,
    load_library,
    run_set_membership,
    set_mode_var,
    set_var_order,
    set_variant_var,
    unbind_template,
)
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
        self._set_filter: Optional[str] = None
        self._reordering = False
        self._build_ui()
        self._refresh_side_panels()
        self._refresh_check_status()

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
        self.btn_new_mode = QPushButton("New Mode")
        self.btn_new_column = QPushButton("New Column")
        self.btn_pull = QPushButton("Pull")
        self.btn_push = QPushButton("Push")
        for b in (self.btn_new_mode, self.btn_new_column,
                  self.btn_pull, self.btn_push):
            top.addWidget(b)
        outer.addLayout(top)

        filt = QHBoxLayout()
        filt.addWidget(QLabel("Column filter:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter corner columns by name…")
        filt.addWidget(self.filter_edit)
        self.btn_filter_set = QPushButton("Filter to selected run set")
        self.btn_clear_filter = QPushButton("Show all columns")
        filt.addWidget(self.btn_filter_set)
        filt.addWidget(self.btn_clear_filter)
        outer.addLayout(filt)

        rowfilt = QHBoxLayout()
        rowfilt.addWidget(QLabel("Row filter:"))
        self.row_filter_edit = QLineEdit()
        self.row_filter_edit.setPlaceholderText(
            "Filter rows by variable name — supports and / or / * "
            "wildcards (e.g. ldo* or div12)"
        )
        rowfilt.addWidget(self.row_filter_edit)
        outer.addLayout(rowfilt)

        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        left_v = QVBoxLayout(left)
        left_v.addWidget(QLabel("PVT Profile (semantic mapping layer, read-only)"))
        self.profile_list = QListWidget()
        self.profile_list.setMaximumHeight(90)
        left_v.addWidget(self.profile_list)
        left_v.addWidget(QLabel("Modes"))
        self.modes_list = QListWidget()
        left_v.addWidget(self.modes_list)
        left_v.addWidget(QLabel("Registers (edit once — every referencing column syncs)"))
        self.mode_vars = QTableWidget(0, 2)
        self.mode_vars.setHorizontalHeaderLabels(["Register", "Value"])
        self.mode_vars.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.mode_vars.verticalHeader().setDefaultSectionSize(24)
        left_v.addWidget(self.mode_vars)

        var_hdr = QHBoxLayout()
        var_hdr.addWidget(QLabel("Variants (delta overlay on a mode)"))
        self.btn_new_variant = QPushButton("New Variant")
        var_hdr.addWidget(self.btn_new_variant)
        left_v.addLayout(var_hdr)
        self.variants_list = QListWidget()
        left_v.addWidget(self.variants_list)
        self.variant_vars = QTableWidget(0, 2)
        self.variant_vars.setHorizontalHeaderLabels(
            ["Overridden register", "Absolute value"]
        )
        self.variant_vars.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.variant_vars.verticalHeader().setDefaultSectionSize(24)
        left_v.addWidget(self.variant_vars)

        left_v.addWidget(QLabel("PVT Templates"))
        self.templates_list = QListWidget()
        left_v.addWidget(self.templates_list)
        tmpl_btns = QHBoxLayout()
        self.btn_apply_template = QPushButton("Apply to mode")
        self.btn_unbind_template = QPushButton("Unbind")
        tmpl_btns.addWidget(self.btn_apply_template)
        tmpl_btns.addWidget(self.btn_unbind_template)
        left_v.addLayout(tmpl_btns)
        lib_btns = QHBoxLayout()
        self.btn_export_lib = QPushButton("Export template library")
        self.btn_import_lib = QPushButton("Import template library")
        lib_btns.addWidget(self.btn_export_lib)
        lib_btns.addWidget(self.btn_import_lib)
        left_v.addLayout(lib_btns)

        left_v.addWidget(
            QLabel("Correlated axes (bound var bundle, cross-product as one axis)")
        )
        self.axes_list = QListWidget()
        left_v.addWidget(self.axes_list)

        rs_hdr = QHBoxLayout()
        rs_hdr.addWidget(QLabel("Run sets (cross-mode corner selection)"))
        self.btn_new_run_set = QPushButton("New Run Set")
        rs_hdr.addWidget(self.btn_new_run_set)
        left_v.addLayout(rs_hdr)
        self.run_sets_list = QListWidget()
        left_v.addWidget(self.run_sets_list)
        self.btn_apply_run_set = QPushButton("Switch to this run set")
        left_v.addWidget(self.btn_apply_run_set)
        splitter.addWidget(left)

        self.table = QTableView()
        self.table_model = CornerModelTableModel(
            self._cm, self._profile, self
        )
        self.table.setModel(self.table_model)
        self.table.verticalHeader().setDefaultSectionSize(24)
        # Stage 5: drag variable rows to reorder (pain-point g).
        self.table.verticalHeader().setSectionsMovable(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        splitter.addWidget(self.table)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        outer.addWidget(splitter)

        self.check_label = QLabel()
        outer.addWidget(self.check_label)

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
        self.btn_apply_template.clicked.connect(self._on_apply_template)
        self.btn_unbind_template.clicked.connect(self._on_unbind_template)
        self.btn_new_run_set.clicked.connect(self._on_new_run_set)
        self.btn_apply_run_set.clicked.connect(self._on_apply_run_set)
        self.filter_edit.textChanged.connect(self._apply_column_filter)
        self.btn_filter_set.clicked.connect(self._on_filter_set)
        self.btn_clear_filter.clicked.connect(self._on_clear_filter)
        self.row_filter_edit.textChanged.connect(self._apply_row_filter)
        self.table.verticalHeader().sectionMoved.connect(
            self._on_row_section_moved
        )
        self.btn_export_lib.clicked.connect(self._on_export_library)
        self.btn_import_lib.clicked.connect(self._on_import_library)
        self.btn_pull.clicked.connect(self.pull_requested.emit)
        self.btn_push.clicked.connect(
            lambda: self.push_requested.emit(self._cm)
        )

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
        self.filter_edit.blockSignals(True)
        self.row_filter_edit.blockSignals(True)
        self.filter_edit.clear()
        self.row_filter_edit.clear()
        self.filter_edit.blockSignals(False)
        self.row_filter_edit.blockSignals(False)
        self.table_model.set_cornermodel(model, profile)
        self._refresh_side_panels()
        self._apply_column_filter()
        self._apply_row_filter()
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
        name, ok = QInputDialog.getText(
            self, "New Run Set", "Run set name (^[A-Za-z][A-Za-z0-9_]*$):"
        )
        if not ok or not name.strip():
            return
        all_names = [effective_name(c) for c in self._cm.columns]
        text, ok = QInputDialog.getMultiLineText(
            self, "New Run Set — select corner columns",
            "One corner effective-name per line:", "\n".join(all_names),
        )
        if not ok:
            return
        columns = tuple(
            ln.strip() for ln in text.splitlines() if ln.strip()
        )
        try:
            new_cm = add_run_set(self._cm, name.strip(), columns)
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
                self, "Column filter", "Select a run set first."
            )
            return
        self._set_filter = name
        self._apply_column_filter()

    def _on_clear_filter(self) -> None:
        self._set_filter = None
        self.filter_edit.clear()
        self._apply_column_filter()

    def _apply_column_filter(self) -> None:
        text = self.filter_edit.text().strip().lower()
        members = (
            run_set_membership(self._cm, self._set_filter)
            if self._set_filter in self._cm.run_sets else None
        )
        for c in range(self.table_model.columnCount()):
            col = self.table_model.column_at(c)
            name = effective_name(col)
            hide = bool(text) and text not in name.lower()
            if members is not None and name not in members:
                hide = True
            self.table.setColumnHidden(c, hide)

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
        self._apply_column_filter()  # the model reset un-hid every column
        self._apply_row_filter()
        self._refresh_check_status()
        self.cornermodel_edited.emit(new_cm)

    def _on_table_edited(self, new_cm: CornerModel) -> None:
        """A cell edit in the corner table — the table model already reset
        itself; just keep our copy + side panels in sync and notify."""
        self._cm = new_cm
        self._refresh_side_panels()
        self._apply_column_filter()
        self._apply_row_filter()
        self._refresh_check_status()
        self.cornermodel_edited.emit(new_cm)

    # --- Stage 5: row filter / row reorder / check / library ------------

    def _refresh_check_status(self) -> None:
        issues = check_cornermodel(self._cm, profile=self._profile)
        if not issues:
            self.check_label.setText("Check: no issues")
        else:
            head = "; ".join(i.message for i in issues[:2])
            self.check_label.setText(
                f"Check: {len(issues)} issue(s) — {head}"
            )

    @staticmethod
    def _row_matches(var: str, expr: str) -> bool:
        expr = expr.strip().lower()
        if not expr:
            return True
        var_l = var.lower()
        for or_term in expr.split(" or "):
            ok = True
            for and_term in or_term.split(" and "):
                t = and_term.strip()
                if not t:
                    continue
                if "*" in t or "?" in t:
                    if not fnmatch.fnmatch(var_l, t):
                        ok = False
                        break
                elif t not in var_l:
                    ok = False
                    break
            if ok:
                return True
        return False

    def _apply_row_filter(self) -> None:
        expr = self.row_filter_edit.text()
        for r in range(self.table_model.rowCount()):
            var = self.table_model.var_at(r) or ""
            self.table.setRowHidden(r, not self._row_matches(var, expr))

    def _on_row_section_moved(self, *_args) -> None:
        if self._reordering:
            return
        header = self.table.verticalHeader()
        ordered: list[str] = []
        for visual in range(self.table_model.rowCount()):
            var = self.table_model.var_at(header.logicalIndex(visual))
            if var is not None:
                ordered.append(var)
        self._reordering = True
        try:
            self._apply(set_var_order(self._cm, tuple(ordered)))
        finally:
            self._reordering = False

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
        try:
            pvt = _parse_var_lines(text)
        except ValueError as exc:
            QMessageBox.warning(self, "New column failed", str(exc))
            return
        column = Column(
            mode=mode,
            enabled=True,
            pvt_vars={k: (v,) for k, v in pvt.items()},
            models=(),
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
