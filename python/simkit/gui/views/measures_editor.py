"""Measure-bundle editor view (Phase 4 spec §12).

Implements Tier-1 capability #5 — edit a `.measure.json` bundle in-GUI
with a live render preview pipeline driving the right pane.

Layout (per spec §12.1)::

    ┌─ Edit (60%) ────────────────┬─ Live preview (40%) ─────────┐
    │ [+ Template] [+ Raw] [+ Sw] │ Status: OK / Error / Empty   │
    │ ┌─────────────────────────┐ │ ┌──────────────────────────┐ │
    │ │ entry list              │ │ │ output_name | test | expr│ │
    │ └─────────────────────────┘ │ └──────────────────────────┘ │
    │ [Delete] [Move ↑] [Move ↓]  │ [Apply to Maestro]           │
    └─────────────────────────────┴──────────────────────────────┘

The right pane re-renders on every edit by constructing an in-memory
``MeasureBundle`` from the current entry list and calling
``simkit.template_render.render_bundle``. Errors (missing param,
output-name collision, unbound ``$SIG``) surface as a red status line +
a disabled "Apply to Maestro" button.

The editor does NOT push to Maestro itself — the apply button fires
``apply_requested(rows)`` with the rendered rows so the parent
(MainWindow) can route to ``BridgeWorker``. The editor is also fully
unit-testable without a Bridge thread / SKILL session.

Public API
~~~~~~~~~~

* :meth:`load_bundle(bundle: dict)` — load a parsed JSON bundle dict.
* :meth:`dump_bundle() -> dict` — read back current state.
* :meth:`set_available_templates(templates)` — register templates for
  pickers + rendering. Accepts ``dict[str, Template]`` (full objects,
  required for rendering) or ``list[str]`` (names only, pickers only).
* :meth:`set_available_signal_groups(groups)` — same, for signal groups.

Signals
~~~~~~~

* :attr:`apply_requested(rows)` — fires with the rendered rows
  (``list[RenderedRow]``) when "Apply to Maestro" is clicked.
"""

from __future__ import annotations

from typing import Any, Optional, Union

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QStandardItem, QStandardItemModel
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from simkit.measure_bundle import MeasureApply, MeasureBundle
from simkit.signal_group import SignalGroup
from simkit.spec_eval import SpecParseError, parse_spec
from simkit.template import Template
from simkit.template_render import RenderedRow, render_bundle


# Syntax help for the per-entry ``spec:`` field. A spec is the auto
# pass/fail rule — without one a result row stays ``no_spec`` forever
# (G-1: specs were authorable but undiscoverable).
_SPEC_HINT_TEXT = (
    "写法: >= 下限  ·  <= 上限  ·  range 下 上  ·  "
    "maximize 目标  ·  minimize 目标 （支持 SI 后缀 k m u n p M G）"
)


# Default top-level metadata used when no bundle has been loaded yet.
# These are placeholder values — load_bundle() will overwrite them as
# soon as a real bundle is supplied.
_DEFAULT_BUNDLE_META = {
    "measure_schema_version": 2,
    "name": "untitled",
    "project": "untitled",
    "testbench_id": "LIB/cell/schematic",
    "test_name": "Test",
}


# Sentinel dict shapes for "+ Template" / "+ Raw" / "+ Sweep" button
# clicks. They produce valid-looking shells the user can flesh out.
_TEMPLATE_ENTRY_SHELL = {"template": "", "signal_group": None}
_RAW_ENTRY_SHELL = {
    "raw_expression": "",
    "output_name": "rawOut",
}
_SWEEP_ENTRY_SHELL = {
    "template": "",
    "signal_group": None,
    "param_sweep": {},
    "output_names": [],
}


class _EntryDialog(QDialog):
    """Minimal per-entry detail editor.

    Pops up on double-click of an entry list row. Lets the user edit
    a handful of key fields as raw text. JSON-typed fields (param_sweep,
    output_names, param_overrides) are edited as comma-separated strings
    and re-parsed on accept. Sufficient for Tier-1 — the heavy field
    editors (kv grid for params, multi-select for signals) are Tier-2.
    """

    def __init__(
        self,
        entry: dict,
        *,
        template_names: list[str],
        signal_group_names: list[str],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit entry")
        self._entry = dict(entry)  # local working copy
        self._kind = _entry_kind(self._entry)

        form = QFormLayout()

        # Template picker (only for template / sweep entries)
        self._template_combo: Optional[QComboBox] = None
        if self._kind in ("template", "sweep"):
            self._template_combo = QComboBox()
            self._template_combo.setEditable(True)
            self._template_combo.addItems(template_names)
            current = self._entry.get("template", "")
            if current:
                self._template_combo.setCurrentText(current)
            form.addRow("template:", self._template_combo)

        # Signal group picker
        self._sg_combo: Optional[QComboBox] = None
        if self._kind in ("template", "sweep"):
            self._sg_combo = QComboBox()
            self._sg_combo.setEditable(True)
            self._sg_combo.addItem("(none)")
            self._sg_combo.addItems(signal_group_names)
            current_sg = self._entry.get("signal_group")
            if current_sg:
                self._sg_combo.setCurrentText(current_sg)
            else:
                self._sg_combo.setCurrentText("(none)")
            form.addRow("signal_group:", self._sg_combo)

        # Output name override
        self._output_name_edit = QLineEdit(self._entry.get("output_name", "") or "")
        form.addRow("output_name:", self._output_name_edit)

        # Raw-only: expression
        self._raw_expr_edit: Optional[QLineEdit] = None
        if self._kind == "raw":
            self._raw_expr_edit = QLineEdit(self._entry.get("raw_expression", "") or "")
            form.addRow("raw_expression:", self._raw_expr_edit)

        # param_overrides — key=value;key=value
        self._overrides_edit = QLineEdit(_dump_kv(self._entry.get("param_overrides")))
        form.addRow("param_overrides (k=v;k=v):", self._overrides_edit)

        # alias_suffix
        self._alias_edit = QLineEdit(self._entry.get("alias_suffix", "") or "")
        form.addRow("alias_suffix:", self._alias_edit)

        # spec — the auto pass/fail rule. Free text, but parsed by
        # spec_eval.parse_spec; live-validate so a typo is caught here
        # rather than silently surfacing as parse_err at run time (G-1a).
        self._spec_edit = QLineEdit(self._entry.get("spec", "") or "")
        self._spec_edit.setPlaceholderText(
            ">= 20    ·    <= 1.5m    ·    range 1 5    ·    maximize 30"
        )
        form.addRow("spec:", self._spec_edit)
        self._spec_hint = QLabel(_SPEC_HINT_TEXT)
        self._spec_hint.setWordWrap(True)
        form.addRow("", self._spec_hint)
        self._spec_edit.textChanged.connect(self._validate_spec)
        self._validate_spec()

        # Sweep-only: param_sweep + output_names
        self._sweep_key_edit: Optional[QLineEdit] = None
        self._sweep_values_edit: Optional[QLineEdit] = None
        self._output_names_edit: Optional[QLineEdit] = None
        if self._kind == "sweep":
            ps = self._entry.get("param_sweep") or {}
            sweep_key = next(iter(ps.keys()), "")
            sweep_values = ps.get(sweep_key, []) if sweep_key else []
            self._sweep_key_edit = QLineEdit(sweep_key)
            self._sweep_values_edit = QLineEdit(",".join(sweep_values))
            self._output_names_edit = QLineEdit(
                ",".join(self._entry.get("output_names") or [])
            )
            form.addRow("sweep key:", self._sweep_key_edit)
            form.addRow("sweep values (csv):", self._sweep_values_edit)
            form.addRow("output_names (csv):", self._output_names_edit)

        layout = QVBoxLayout(self)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def updated_entry(self) -> dict:
        """Apply form edits back onto the working copy and return it.

        Empty fields are removed so the dumped JSON stays minimal
        (matches how human-authored `.measure.json` files look).
        """
        entry = dict(self._entry)
        if self._template_combo is not None:
            tmpl = self._template_combo.currentText().strip()
            if tmpl:
                entry["template"] = tmpl
            else:
                entry.pop("template", None)
        if self._sg_combo is not None:
            sg = self._sg_combo.currentText().strip()
            if sg in ("", "(none)"):
                entry["signal_group"] = None
            else:
                entry["signal_group"] = sg
        if self._raw_expr_edit is not None:
            v = self._raw_expr_edit.text().strip()
            if v:
                entry["raw_expression"] = v
            else:
                entry.pop("raw_expression", None)
        out = self._output_name_edit.text().strip()
        if out:
            entry["output_name"] = out
        else:
            entry.pop("output_name", None)
        overrides = _parse_kv(self._overrides_edit.text())
        if overrides:
            entry["param_overrides"] = overrides
        else:
            entry.pop("param_overrides", None)
        alias = self._alias_edit.text().strip()
        if alias:
            entry["alias_suffix"] = alias
        else:
            entry.pop("alias_suffix", None)
        spec = self._spec_edit.text().strip()
        if spec:
            entry["spec"] = spec
        else:
            entry.pop("spec", None)
        if self._kind == "sweep":
            key = (self._sweep_key_edit.text() or "").strip()
            vals_raw = (self._sweep_values_edit.text() or "").strip()
            names_raw = (self._output_names_edit.text() or "").strip()
            values = [v.strip() for v in vals_raw.split(",") if v.strip()]
            names = [n.strip() for n in names_raw.split(",") if n.strip()]
            if key and values:
                entry["param_sweep"] = {key: values}
            else:
                entry.pop("param_sweep", None)
            if names:
                entry["output_names"] = names
            else:
                entry.pop("output_names", None)
        return entry

    def _validate_spec(self) -> None:
        """Live-check the spec field; red border + reason on parse error."""
        text = self._spec_edit.text().strip()
        if not text:
            self._spec_edit.setStyleSheet("")
            self._spec_hint.setText(_SPEC_HINT_TEXT)
            self._spec_hint.setStyleSheet("color: #666;")
            return
        try:
            parse_spec(text)
        except SpecParseError as exc:
            self._spec_edit.setStyleSheet(
                "QLineEdit { border: 1px solid #c0392b; }"
            )
            self._spec_hint.setText(f"spec 解析失败: {exc}")
            self._spec_hint.setStyleSheet("color: #c0392b;")
        else:
            self._spec_edit.setStyleSheet("")
            self._spec_hint.setText(f"✓ 规格有效 — {_SPEC_HINT_TEXT}")
            self._spec_hint.setStyleSheet("color: #2e7d32;")


def _entry_kind(entry: dict) -> str:
    if "raw_expression" in entry:
        return "raw"
    if "param_sweep" in entry or "output_names" in entry:
        return "sweep"
    return "template"


def _entry_summary(entry: dict) -> str:
    kind = _entry_kind(entry)
    if kind == "raw":
        out = entry.get("output_name", "?")
        expr = entry.get("raw_expression", "")
        short = expr if len(expr) <= 40 else expr[:37] + "…"
        return f"[raw]      {out}  ← {short}"
    if kind == "sweep":
        tmpl = entry.get("template", "?")
        ps = entry.get("param_sweep") or {}
        key = next(iter(ps.keys()), "?")
        n = len(ps.get(key, [])) if key != "?" else 0
        return f"[sweep]    template={tmpl}  ${key} × {n}"
    tmpl = entry.get("template", "?")
    sg = entry.get("signal_group")
    sg_str = sg if sg else "(none)"
    return f"[template] {tmpl}  ⨯  signal_group={sg_str}"


def _parse_kv(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for piece in (text or "").split(";"):
        piece = piece.strip()
        if not piece:
            continue
        if "=" not in piece:
            continue
        k, v = piece.split("=", 1)
        k = k.strip()
        v = v.strip()
        if k:
            out[k] = v
    return out


def _dump_kv(d: Optional[dict]) -> str:
    if not d:
        return ""
    return ";".join(f"{k}={v}" for k, v in d.items())


class MeasuresEditor(QWidget):
    """Right-panel tab content for editing a measure bundle.

    See module docstring for behaviour summary. The editor stores its
    state as a raw JSON-shaped dict (matching ``.measure.json`` on
    disk) plus a ``dict[name, Template]`` / ``dict[name, SignalGroup]``
    lookup populated via ``set_available_*``. Live preview reconstructs
    a ``MeasureBundle`` dataclass on every edit and calls
    ``render_bundle`` — the same pipeline the CLI uses.
    """

    PREVIEW_COLUMNS: tuple[str, ...] = ("output_name", "test", "expression")

    apply_requested = pyqtSignal(object)
    """Carries the rendered rows (list[RenderedRow]) when Apply is clicked."""
    pull_requested = pyqtSignal()
    """Emitted when 'Pull from Maestro' is clicked; MainWindow handles
    the BridgeWorker dispatch + writes the resulting bundle to bundles/."""

    # Colours for the status label. Picked plain so the visual treatment
    # is obvious without a stylesheet dependency.
    _STATUS_GREEN = "#2ecc71"
    _STATUS_RED = "#e74c3c"
    _STATUS_GREY = "#888888"

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        # State -----------------------------------------------------------
        self._bundle_meta: dict[str, Any] = dict(_DEFAULT_BUNDLE_META)
        self._entries: list[dict] = []
        self._templates: dict[str, Template] = {}
        self._signal_groups: dict[str, SignalGroup] = {}
        self._template_names: list[str] = []
        self._signal_group_names: list[str] = []
        self._last_rendered_rows: list[RenderedRow] = []
        self._last_render_ok: bool = False
        self._has_loaded: bool = False  # tracks load_bundle() vs default state

        # Layout: top-level horizontal splitter --------------------------
        self._splitter = QSplitter(Qt.Horizontal, self)
        self._splitter.setObjectName("measuresEditorSplitter")

        self._left_pane = self._build_left_pane()
        self._right_pane = self._build_right_pane()

        self._splitter.addWidget(self._left_pane)
        self._splitter.addWidget(self._right_pane)
        # Spec §12.1: 60/40 default split.
        self._splitter.setStretchFactor(0, 6)
        self._splitter.setStretchFactor(1, 4)
        self._splitter.setSizes([600, 400])

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._splitter)

        self._refresh_entries()
        self._render_preview()

    # ---- pane builders --------------------------------------------------

    def _build_left_pane(self) -> QWidget:
        pane = QWidget(objectName="measuresEditorLeft")
        v = QVBoxLayout(pane)
        v.setContentsMargins(6, 6, 6, 6)

        # Top row: Pull from Maestro / + Template / + Raw / + Sweep
        top_row = QHBoxLayout()
        self._pull_btn = QPushButton("Pull from Maestro")
        self._pull_btn.setObjectName("pullFromMaestroBtn")
        self._pull_btn.clicked.connect(self.pull_requested.emit)
        top_row.addWidget(self._pull_btn)
        self._add_template_btn = QPushButton("+ Template")
        self._add_template_btn.setObjectName("addTemplateBtn")
        self._add_template_btn.clicked.connect(self._on_add_template)
        self._add_raw_btn = QPushButton("+ Raw")
        self._add_raw_btn.setObjectName("addRawBtn")
        self._add_raw_btn.clicked.connect(self._on_add_raw)
        self._add_sweep_btn = QPushButton("+ Sweep")
        self._add_sweep_btn.setObjectName("addSweepBtn")
        self._add_sweep_btn.clicked.connect(self._on_add_sweep)
        top_row.addWidget(self._add_template_btn)
        top_row.addWidget(self._add_raw_btn)
        top_row.addWidget(self._add_sweep_btn)
        top_row.addStretch(1)
        v.addLayout(top_row)

        # Entry list
        self._entry_list = QListWidget(objectName="entryList")
        self._entry_list.itemDoubleClicked.connect(self._on_entry_double_clicked)
        v.addWidget(self._entry_list, stretch=1)

        # Bottom row: Delete / Move up / Move down
        bottom_row = QHBoxLayout()
        self._delete_btn = QPushButton("Delete entry")
        self._delete_btn.setObjectName("deleteEntryBtn")
        self._delete_btn.clicked.connect(self._on_delete)
        self._up_btn = QPushButton("Move up")
        self._up_btn.setObjectName("moveUpBtn")
        self._up_btn.clicked.connect(self._on_move_up)
        self._down_btn = QPushButton("Move down")
        self._down_btn.setObjectName("moveDownBtn")
        self._down_btn.clicked.connect(self._on_move_down)
        bottom_row.addWidget(self._delete_btn)
        bottom_row.addWidget(self._up_btn)
        bottom_row.addWidget(self._down_btn)
        bottom_row.addStretch(1)
        v.addLayout(bottom_row)

        return pane

    def _build_right_pane(self) -> QWidget:
        pane = QWidget(objectName="measuresEditorRight")
        v = QVBoxLayout(pane)
        v.setContentsMargins(6, 6, 6, 6)

        self._status_label = QLabel("Empty bundle", objectName="previewStatusLabel")
        self._set_status("Empty bundle", self._STATUS_GREY)
        v.addWidget(self._status_label)

        # Multi-line error description (hidden unless an error fires).
        self._error_detail = QLabel("", objectName="previewErrorDetail")
        self._error_detail.setWordWrap(True)
        self._error_detail.setStyleSheet(f"color: {self._STATUS_RED};")
        self._error_detail.setVisible(False)
        v.addWidget(self._error_detail)

        # Preview table
        self._preview_model = QStandardItemModel(0, len(self.PREVIEW_COLUMNS), pane)
        self._preview_model.setHorizontalHeaderLabels(list(self.PREVIEW_COLUMNS))
        self._preview_table = QTableView(objectName="previewTable")
        self._preview_table.setModel(self._preview_model)
        self._preview_table.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self._preview_table, stretch=1)

        # Apply button
        self._apply_btn = QPushButton("Apply to Maestro")
        self._apply_btn.setObjectName("applyToMaestroBtn")
        self._apply_btn.setEnabled(False)
        self._apply_btn.clicked.connect(self._on_apply_clicked)
        v.addWidget(self._apply_btn)

        return pane

    # ---- public API -----------------------------------------------------

    def load_bundle(self, bundle: dict) -> None:
        """Load a parsed `.measure.json` dict into the editor.

        ``bundle`` is the raw JSON-decoded shape (top-level dict with
        ``measure_schema_version``, ``name``, ``project``,
        ``testbench_id``, ``test_name``, ``apply``). An empty dict
        clears the editor and shows "Empty bundle" status.
        """
        self._has_loaded = True
        if not bundle:
            self._bundle_meta = dict(_DEFAULT_BUNDLE_META)
            self._entries = []
            self._has_loaded = False  # treat as truly-empty
            self._refresh_entries()
            self._render_preview()
            return
        meta: dict[str, Any] = {}
        for k, default in _DEFAULT_BUNDLE_META.items():
            meta[k] = bundle.get(k, default)
        self._bundle_meta = meta
        raw_apply = bundle.get("apply", [])
        if isinstance(raw_apply, list):
            # Deep-copy each entry dict so editor edits don't mutate caller's input.
            self._entries = [dict(e) if isinstance(e, dict) else {} for e in raw_apply]
        else:
            self._entries = []
        self._refresh_entries()
        self._render_preview()

    def dump_bundle(self) -> dict:
        """Read back the current bundle as a JSON-shaped dict.

        Mirrors the `.measure.json` on-disk format. ``apply`` is the
        list of entry dicts in current order.
        """
        out = dict(self._bundle_meta)
        out["apply"] = [dict(e) for e in self._entries]
        return out

    def set_available_templates(
        self, templates: Union[list[str], dict[str, Template]]
    ) -> None:
        """Register templates for pickers + live rendering.

        Accepts either a ``list[str]`` (names only — populates the
        picker dropdowns but rendering will fail with "unknown template
        '<name>'" for any entry that references it) or a
        ``dict[name, Template]`` (names + objects — full rendering).

        Tests and real callers should pass the dict form. The list form
        exists for backward-compat with the §12 spec wording.
        """
        if isinstance(templates, dict):
            self._templates = dict(templates)
            self._template_names = sorted(templates.keys())
        else:
            self._templates = {}
            self._template_names = list(templates)
        self._render_preview()

    def set_available_signal_groups(
        self, groups: Union[list[str], dict[str, SignalGroup]]
    ) -> None:
        """Register signal groups for pickers + live rendering. See
        :meth:`set_available_templates` for the accept-list-or-dict
        contract."""
        if isinstance(groups, dict):
            self._signal_groups = dict(groups)
            self._signal_group_names = sorted(groups.keys())
        else:
            self._signal_groups = {}
            self._signal_group_names = list(groups)
        self._render_preview()

    # ---- left pane: list + buttons --------------------------------------

    def _refresh_entries(self) -> None:
        self._entry_list.clear()
        for entry in self._entries:
            item = QListWidgetItem(_entry_summary(entry))
            self._entry_list.addItem(item)

    def _selected_row(self) -> int:
        rows = self._entry_list.selectionModel().selectedRows()
        if not rows:
            cur = self._entry_list.currentRow()
            return cur if cur >= 0 else -1
        return rows[0].row()

    def _on_add_template(self) -> None:
        entry = dict(_TEMPLATE_ENTRY_SHELL)
        # Pre-fill with first registered template if any (cuts ↓ a click).
        if self._template_names:
            entry["template"] = self._template_names[0]
        if self._signal_group_names:
            entry["signal_group"] = self._signal_group_names[0]
        self._entries.append(entry)
        self._has_loaded = True
        self._refresh_entries()
        self._entry_list.setCurrentRow(len(self._entries) - 1)
        self._render_preview()

    def _on_add_raw(self) -> None:
        entry = dict(_RAW_ENTRY_SHELL)
        # Default to a harmless literal so the preview shows *something*.
        entry["raw_expression"] = "0"
        # Make output name unique to avoid render collisions when adding
        # multiple raw entries in a row.
        entry["output_name"] = self._unique_raw_name()
        self._entries.append(entry)
        self._has_loaded = True
        self._refresh_entries()
        self._entry_list.setCurrentRow(len(self._entries) - 1)
        self._render_preview()

    def _unique_raw_name(self) -> str:
        existing = {e.get("output_name") for e in self._entries}
        i = 1
        while f"rawOut{i}" in existing:
            i += 1
        return f"rawOut{i}"

    def _on_add_sweep(self) -> None:
        entry = {
            "template": (self._template_names[0] if self._template_names else ""),
            "signal_group": (
                self._signal_group_names[0]
                if self._signal_group_names
                else None
            ),
            "param_sweep": {},
            "output_names": [],
        }
        self._entries.append(entry)
        self._has_loaded = True
        self._refresh_entries()
        self._entry_list.setCurrentRow(len(self._entries) - 1)
        self._render_preview()

    def _on_delete(self) -> None:
        row = self._selected_row()
        if row < 0 or row >= len(self._entries):
            return
        del self._entries[row]
        self._refresh_entries()
        if self._entries:
            new_row = min(row, len(self._entries) - 1)
            self._entry_list.setCurrentRow(new_row)
        self._render_preview()

    def _on_move_up(self) -> None:
        row = self._selected_row()
        if row <= 0 or row >= len(self._entries):
            return
        self._entries[row - 1], self._entries[row] = (
            self._entries[row],
            self._entries[row - 1],
        )
        self._refresh_entries()
        self._entry_list.setCurrentRow(row - 1)
        self._render_preview()

    def _on_move_down(self) -> None:
        row = self._selected_row()
        if row < 0 or row >= len(self._entries) - 1:
            return
        self._entries[row + 1], self._entries[row] = (
            self._entries[row],
            self._entries[row + 1],
        )
        self._refresh_entries()
        self._entry_list.setCurrentRow(row + 1)
        self._render_preview()

    def _on_entry_double_clicked(self, item: QListWidgetItem) -> None:
        row = self._entry_list.row(item)
        if row < 0 or row >= len(self._entries):
            return
        dlg = _EntryDialog(
            self._entries[row],
            template_names=self._template_names,
            signal_group_names=self._signal_group_names,
            parent=self,
        )
        if dlg.exec_() == QDialog.Accepted:
            self._entries[row] = dlg.updated_entry()
            self._refresh_entries()
            self._entry_list.setCurrentRow(row)
            self._render_preview()

    # ---- right pane: status + preview + apply ---------------------------

    def _set_status(self, text: str, color: str) -> None:
        self._status_label.setText(text)
        self._status_label.setStyleSheet(
            f"color: {color}; font-weight: bold;"
        )

    def _render_preview(self) -> None:
        # Clear the table model regardless of outcome.
        self._preview_model.removeRows(0, self._preview_model.rowCount())

        # Empty editor → grey "Empty bundle" status, no rows, no apply.
        if not self._has_loaded and not self._entries:
            self._set_status("Empty bundle", self._STATUS_GREY)
            self._error_detail.setVisible(False)
            self._apply_btn.setEnabled(False)
            self._last_rendered_rows = []
            self._last_render_ok = False
            return
        if not self._entries:
            # Loaded but apply is empty — still "Empty bundle".
            self._set_status("Empty bundle", self._STATUS_GREY)
            self._error_detail.setVisible(False)
            self._apply_btn.setEnabled(False)
            self._last_rendered_rows = []
            self._last_render_ok = False
            return

        try:
            bundle = self._build_in_memory_bundle()
            rows = render_bundle(bundle)
        except Exception as exc:  # RenderError + any in-memory build error
            self._set_status("Error", self._STATUS_RED)
            self._error_detail.setText(str(exc))
            self._error_detail.setVisible(True)
            self._apply_btn.setEnabled(False)
            self._last_rendered_rows = []
            self._last_render_ok = False
            return

        # Happy path
        self._set_status("OK", self._STATUS_GREEN)
        self._error_detail.setVisible(False)
        self._error_detail.setText("")
        self._last_rendered_rows = rows
        self._last_render_ok = True
        self._apply_btn.setEnabled(True)

        test_name = self._bundle_meta.get("test_name", "")
        for row in rows:
            items = [
                QStandardItem(row.output_name),
                QStandardItem(test_name),
                QStandardItem(row.expression),
            ]
            for it in items:
                it.setEditable(False)
            self._preview_model.appendRow(items)

    def _build_in_memory_bundle(self) -> MeasureBundle:
        """Construct a ``MeasureBundle`` dataclass from the current state.

        This bypasses ``load_measure_bundle``'s file-path validation —
        templates and signal groups are looked up by name in the
        in-memory dicts registered via ``set_available_*``. Any other
        in-shape error (unknown template, missing signal_group when
        the template needs one, etc.) is raised as a plain
        ``ValueError`` so the status panel renders it.
        """
        from pathlib import Path

        apply_entries: list[MeasureApply] = []
        for i, raw in enumerate(self._entries):
            apply_entries.append(self._build_apply(i, raw))
        return MeasureBundle(
            measure_schema_version=int(
                self._bundle_meta.get("measure_schema_version", 2)
            ),
            name=self._bundle_meta.get("name", "untitled"),
            project=self._bundle_meta.get("project", "untitled"),
            testbench_id=self._bundle_meta.get("testbench_id", "LIB/cell/schematic"),
            test_name=self._bundle_meta.get("test_name", "Test"),
            apply=tuple(apply_entries),
            source_path=Path("<in-memory>"),
        )

    def _build_apply(self, idx: int, raw: dict) -> MeasureApply:
        # Raw-expression branch.
        if "raw_expression" in raw:
            expr = raw.get("raw_expression") or ""
            if not expr:
                raise ValueError(f"apply[{idx}]: raw_expression must not be empty")
            out_name = raw.get("output_name") or ""
            if not out_name:
                raise ValueError(f"apply[{idx}]: raw entry requires output_name")
            return MeasureApply(
                template=None,
                raw_expression=expr,
                output_name=out_name,
                raw_plot=bool(raw.get("plot", True)),
                raw_save=bool(raw.get("save", False)),
                raw_eval_type=raw.get("eval_type", "point"),
                spec=raw.get("spec"),
            )

        # Template-driven entry.
        tmpl_name = raw.get("template") or ""
        if not tmpl_name:
            raise ValueError(f"apply[{idx}]: 'template' is empty")
        if tmpl_name not in self._templates:
            raise ValueError(
                f"apply[{idx}]: template {tmpl_name!r} not registered "
                f"(call set_available_templates with a dict of "
                f"name → Template before rendering)"
            )
        template = self._templates[tmpl_name]

        sg_name = raw.get("signal_group")
        signal_group: Optional[SignalGroup] = None
        if sg_name:
            if sg_name not in self._signal_groups:
                raise ValueError(
                    f"apply[{idx}]: signal_group {sg_name!r} not registered"
                )
            signal_group = self._signal_groups[sg_name]
        # Surface the most common shape error ("signal-kind template +
        # no signal_group") with the same wording the file-loader uses.
        if template.signal_param() is not None and signal_group is None:
            raise ValueError(
                f"apply[{idx}]: template {tmpl_name!r} declares a "
                f"signal-kind param but no signal_group is bound"
            )

        overrides_raw = raw.get("param_overrides") or {}
        if not isinstance(overrides_raw, dict):
            raise ValueError(f"apply[{idx}]: param_overrides must be a dict")
        param_overrides = {str(k): str(v) for k, v in overrides_raw.items()}

        # Sweep / output_names parsing.
        sweep_raw = raw.get("param_sweep")
        output_names_raw = raw.get("output_names")
        param_sweep: Optional[dict[str, tuple[str, ...]]] = None
        output_names: Optional[tuple[str, ...]] = None
        if sweep_raw or output_names_raw:
            if not isinstance(sweep_raw, dict) or not sweep_raw:
                raise ValueError(
                    f"apply[{idx}]: param_sweep must be a non-empty dict"
                )
            if not isinstance(output_names_raw, list) or not output_names_raw:
                raise ValueError(
                    f"apply[{idx}]: output_names must be a non-empty list"
                )
            if len(sweep_raw) != 1:
                raise ValueError(
                    f"apply[{idx}]: param_sweep must declare exactly one axis"
                )
            (k, v), = sweep_raw.items()
            if not isinstance(v, (list, tuple)):
                raise ValueError(
                    f"apply[{idx}]: param_sweep values must be a list"
                )
            param_sweep = {str(k): tuple(str(x) for x in v)}
            output_names = tuple(str(n) for n in output_names_raw)
            if len(output_names) != len(param_sweep[str(k)]):
                raise ValueError(
                    f"apply[{idx}]: output_names ({len(output_names)}) and "
                    f"param_sweep values ({len(param_sweep[str(k)])}) must "
                    f"have the same length"
                )

        return MeasureApply(
            template=template,
            signal_group=signal_group,
            param_overrides=param_overrides,
            alias_suffix=raw.get("alias_suffix", "") or "",
            output_name=raw.get("output_name"),
            param_sweep=param_sweep,
            output_names=output_names,
            spec=raw.get("spec"),
        )

    def _on_apply_clicked(self) -> None:
        if not self._last_render_ok:
            return  # button is disabled on the error path, defensive
        self.apply_requested.emit(list(self._last_rendered_rows))
