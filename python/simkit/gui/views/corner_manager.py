"""CornerManagerView — Phase 5 Stage 1 corner-manager view (spec §7).

Layout follows Cadence's native corner manager: a left **modes panel** and a
central **corner table** (variables as rows, corners as columns). Editing a
register value in the modes panel is the痛点-b global edit — every column
referencing that mode re-materialises at once.

The view is self-contained and bridge-free: live pull / push are surfaced as
signals (:pyattr:`pull_requested` / :pyattr:`push_requested`) for the owning
window to route, mirroring the Phase 4 editor convention.
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
    """The Stage 1 corner manager — modes panel + corner table."""

    pull_requested = pyqtSignal()
    push_requested = pyqtSignal(object)        # current CornerModel
    cornermodel_edited = pyqtSignal(object)    # CornerModel — owner persists

    def __init__(
        self, model: CornerModel, profile: object = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._cm = model
        self._profile = profile   # PvtProfile | None (Stage 6)
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

    # --- construction ----------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        top = QHBoxLayout()
        self.title_label = QLabel(f"Corner Manager — {self._cm.name}")
        top.addWidget(self.title_label)
        top.addStretch(1)
        self.btn_new_mode = QPushButton("新建模式")
        self.btn_new_column = QPushButton("新建列")
        self.btn_pull = QPushButton("Pull")
        self.btn_push = QPushButton("Push")
        for b in (self.btn_new_mode, self.btn_new_column,
                  self.btn_pull, self.btn_push):
            top.addWidget(b)
        outer.addLayout(top)

        filt = QHBoxLayout()
        filt.addWidget(QLabel("列筛选:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("按名字过滤 corner 列…")
        filt.addWidget(self.filter_edit)
        self.btn_filter_set = QPushButton("筛选到选中运行集")
        self.btn_clear_filter = QPushButton("显示全部列")
        filt.addWidget(self.btn_filter_set)
        filt.addWidget(self.btn_clear_filter)
        outer.addLayout(filt)

        rowfilt = QHBoxLayout()
        rowfilt.addWidget(QLabel("行筛选:"))
        self.row_filter_edit = QLineEdit()
        self.row_filter_edit.setPlaceholderText(
            "按变量名过滤行 — 支持 and / or / 通配 *(如 ldo* or div12)"
        )
        rowfilt.addWidget(self.row_filter_edit)
        outer.addLayout(rowfilt)

        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        left_v = QVBoxLayout(left)
        left_v.addWidget(QLabel("PVT Profile(语义映射层,只读)"))
        self.profile_list = QListWidget()
        self.profile_list.setMaximumHeight(90)
        left_v.addWidget(self.profile_list)
        left_v.addWidget(QLabel("模式"))
        self.modes_list = QListWidget()
        left_v.addWidget(self.modes_list)
        left_v.addWidget(QLabel("寄存器配置(改一处,所有引用列同步)"))
        self.mode_vars = QTableWidget(0, 2)
        self.mode_vars.setHorizontalHeaderLabels(["寄存器", "值"])
        self.mode_vars.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.mode_vars.verticalHeader().setDefaultSectionSize(24)
        left_v.addWidget(self.mode_vars)

        var_hdr = QHBoxLayout()
        var_hdr.addWidget(QLabel("变体(模式的差量覆盖)"))
        self.btn_new_variant = QPushButton("新建变体")
        var_hdr.addWidget(self.btn_new_variant)
        left_v.addLayout(var_hdr)
        self.variants_list = QListWidget()
        left_v.addWidget(self.variants_list)
        self.variant_vars = QTableWidget(0, 2)
        self.variant_vars.setHorizontalHeaderLabels(["覆盖寄存器", "绝对值"])
        self.variant_vars.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.variant_vars.verticalHeader().setDefaultSectionSize(24)
        left_v.addWidget(self.variant_vars)

        left_v.addWidget(QLabel("PVT 模板"))
        self.templates_list = QListWidget()
        left_v.addWidget(self.templates_list)
        tmpl_btns = QHBoxLayout()
        self.btn_apply_template = QPushButton("套用到模式")
        self.btn_unbind_template = QPushButton("解绑")
        tmpl_btns.addWidget(self.btn_apply_template)
        tmpl_btns.addWidget(self.btn_unbind_template)
        left_v.addLayout(tmpl_btns)
        lib_btns = QHBoxLayout()
        self.btn_export_lib = QPushButton("导出模板库")
        self.btn_import_lib = QPushButton("导入模板库")
        lib_btns.addWidget(self.btn_export_lib)
        lib_btns.addWidget(self.btn_import_lib)
        left_v.addLayout(lib_btns)

        left_v.addWidget(QLabel("复合轴(绑定 var 捆,叉乘算一个轴)"))
        self.axes_list = QListWidget()
        left_v.addWidget(self.axes_list)

        rs_hdr = QHBoxLayout()
        rs_hdr.addWidget(QLabel("运行集(跨模式 corner 勾选)"))
        self.btn_new_run_set = QPushButton("新建运行集")
        rs_hdr.addWidget(self.btn_new_run_set)
        left_v.addLayout(rs_hdr)
        self.run_sets_list = QListWidget()
        left_v.addWidget(self.run_sets_list)
        self.btn_apply_run_set = QPushButton("切换到此运行集")
        left_v.addWidget(self.btn_apply_run_set)
        splitter.addWidget(left)

        self.table = QTableView()
        self.table_model = CornerModelTableModel(
            self._cm, self._profile, self
        )
        self.table.setModel(self.table_model)
        self.table.verticalHeader().setDefaultSectionSize(24)
        # Stage 5: drag variable rows to reorder (痛点 g).
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
                "(未绑定 PVT profile)" if bound is None
                else f"⚠ 绑定了 profile {bound} 但未加载"
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
            self.run_sets_list.addItem(f"{name}  ({count} 列)")
        if prev_name in names:
            self.run_sets_list.setCurrentRow(names.index(prev_name))

    def _selected_run_set_name(self) -> Optional[str]:
        item = self.run_sets_list.currentItem()
        if item is None:
            return None
        return item.text().split("  ")[0]

    def _on_new_run_set(self) -> None:
        name, ok = QInputDialog.getText(
            self, "新建运行集", "运行集名 (^[A-Za-z][A-Za-z0-9_]*$):"
        )
        if not ok or not name.strip():
            return
        all_names = [effective_name(c) for c in self._cm.columns]
        text, ok = QInputDialog.getMultiLineText(
            self, "新建运行集 — 勾选 corner 列",
            "每行一个 corner 有效名:", "\n".join(all_names),
        )
        if not ok:
            return
        columns = tuple(
            ln.strip() for ln in text.splitlines() if ln.strip()
        )
        try:
            new_cm = add_run_set(self._cm, name.strip(), columns)
        except CornerModelError as exc:
            QMessageBox.warning(self, "新建运行集失败", str(exc))
            return
        self._apply(new_cm)

    def _on_apply_run_set(self) -> None:
        name = self._selected_run_set_name()
        if name is None:
            QMessageBox.warning(self, "切换运行集", "请先选中一个运行集。")
            return
        self._apply(apply_run_set(self._cm, name))

    def _on_filter_set(self) -> None:
        name = self._selected_run_set_name()
        if name is None:
            QMessageBox.warning(self, "列筛选", "请先选中一个运行集。")
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
            QMessageBox.warning(self, "改变体失败", str(exc))
            self._refresh_variants_panel()
            return
        self._apply(new_cm)

    def _on_new_variant(self) -> None:
        if not self._cm.modes:
            QMessageBox.warning(self, "新建变体", "请先新建一个模式。")
            return
        base, ok = QInputDialog.getItem(
            self, "新建变体", "基础模式:", sorted(self._cm.modes), 0, False
        )
        if not ok:
            return
        name, ok = QInputDialog.getText(
            self, "新建变体", "变体名 (^[A-Za-z][A-Za-z0-9_]*$):"
        )
        if not ok or not name.strip():
            return
        text, ok = QInputDialog.getMultiLineText(
            self, "新建变体 — 覆盖寄存器",
            "每行一个 var=绝对值(只能覆盖基础模式已有寄存器):",
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
            QMessageBox.warning(self, "新建变体失败", str(exc))
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
                f"{name}  ({len(axis.tuples)} 点 · "
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
            QMessageBox.warning(self, "改寄存器失败", str(exc))
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
            self.check_label.setText("校验: 无问题")
        else:
            head = "; ".join(i.message for i in issues[:2])
            self.check_label.setText(
                f"校验: {len(issues)} 个问题 — {head}"
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
            self, "导出模板库", "写到 .cornerlib.json 路径:"
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
            QMessageBox.warning(self, "导出模板库失败", str(exc))

    def _on_import_library(self) -> None:
        path, ok = QInputDialog.getText(
            self, "导入模板库", ".cornerlib.json 路径:"
        )
        if not ok or not path.strip():
            return
        try:
            lib = load_library(path.strip())
            new_cm = import_library(self._cm, lib)
        except CornerModelError as exc:
            QMessageBox.warning(self, "导入模板库失败", str(exc))
            return
        self._apply(new_cm)

    # --- templates: apply / unbind --------------------------------------

    _VARIANT_PREFIX = "变体: "

    def _on_apply_template(self) -> None:
        tmpl = self._selected_template_name()
        if tmpl is None:
            QMessageBox.warning(self, "套用模板", "请先选中一个模板。")
            return
        if not self._cm.modes:
            QMessageBox.warning(self, "套用模板", "请先新建一个模式。")
            return
        targets = sorted(self._cm.modes) + [
            self._VARIANT_PREFIX + v for v in sorted(self._cm.variants)
        ]
        target, ok = QInputDialog.getItem(
            self, "套用模板", f"把模板 {tmpl} 套用到(模式 / 变体):",
            targets, 0, False,
        )
        if not ok:
            return
        try:
            new_cm = self._apply_template_to_target(tmpl, target)
        except CornerModelError as exc:
            QMessageBox.warning(self, "套用模板失败", str(exc))
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
            QMessageBox.warning(self, "解绑模板", "请先选中一个模板。")
            return
        bound = [
            b for b in self._cm.template_bindings if b.template == tmpl
        ]
        if not bound:
            QMessageBox.warning(
                self, "解绑模板", f"模板 {tmpl} 没有绑定任何模式 / 变体。"
            )
            return
        labels = [
            (self._VARIANT_PREFIX + b.variant) if b.variant else b.mode
            for b in bound
        ]
        label, ok = QInputDialog.getItem(
            self, "解绑模板", f"从哪个目标解绑 {tmpl}(列冻结保留):",
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
            self, "新建模式", "模式名 (^[A-Za-z][A-Za-z0-9_]*$):"
        )
        if not ok or not name.strip():
            return
        text, ok = QInputDialog.getMultiLineText(
            self, "新建模式 — 寄存器配置",
            "每行一个 var=value:", "d_en_dummy=1",
        )
        if not ok:
            return
        try:
            mode_vars = _parse_var_lines(text)
        except ValueError as exc:
            QMessageBox.warning(self, "新建模式失败", str(exc))
            return
        try:
            new_cm = add_mode(self._cm, name.strip(), mode_vars)
        except CornerModelError as exc:
            QMessageBox.warning(self, "新建模式失败", str(exc))
            return
        self._apply(new_cm)

    def _on_new_column(self) -> None:
        if not self._cm.modes:
            QMessageBox.warning(self, "新建列", "请先新建一个模式。")
            return
        mode, ok = QInputDialog.getItem(
            self, "新建列", "模式:", sorted(self._cm.modes), 0, False
        )
        if not ok:
            return
        label, ok = QInputDialog.getText(
            self, "新建列", "PVT 标签 (^[A-Za-z0-9_]+$):"
        )
        if not ok or not label.strip():
            return
        text, ok = QInputDialog.getMultiLineText(
            self, "新建列 — PVT 变量",
            "每行一个 var=value(可留空):", "temperature=55",
        )
        if not ok:
            return
        try:
            pvt = _parse_var_lines(text)
        except ValueError as exc:
            QMessageBox.warning(self, "新建列失败", str(exc))
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
            QMessageBox.warning(self, "新建列失败", str(exc))
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
            raise ValueError(f"行 {line!r} 不是 var=value 格式")
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not key:
            raise ValueError(f"行 {line!r} 缺少 var 名")
        out[key] = value
    return out
