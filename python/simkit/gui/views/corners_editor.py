"""Corner editor view (spec §11, mandate B4).

Tier-1 capability #4: edit the corner union in the GUI rather than
forcing the user back into Maestro Tools->Corners. This view owns the
**presentation layer** of the union — a flat list-of-dicts model whose
columns map onto the row fields the user actually edits
(``Enable / row_name / process / temperature / vdd / model_file /
extra_vars``).

The on-disk ``.union.json`` (see :mod:`simkit.union`) is structurally
richer than this flat view (vars dict + models list). The translation
between flat-row and full Union sidecar happens **outside** this view —
``MainWindow`` (or a future ``ModuleSession`` adapter) is responsible
for serializing the dicts ``load_union(...)`` accepts / ``dump_union()``
returns. This keeps the editor unit-testable without any union loader /
SKILL bridge plumbing.

Wiring contract (mandate A1): the editor never imports or touches
``BridgeWorker`` directly. It emits ``pull_requested`` /
``push_requested(payload)`` signals; ``MainWindow`` routes those to
``BridgeWorker.queue_op(...)`` and feeds results back via
``load_union`` + ``set_last_sync`` + ``set_divergence``.

Validation (spec §11.3): live; only the two minimal checks for now —
missing ``row_name`` and duplicate ``row_name``. Spec §11.3 also calls
for ``model_file`` filesystem-existence validation; that requires
project-root context which this view doesn't have yet, so it's
deliberately deferred. The hook to add it is :meth:`validation_errors`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QKeySequence, QStandardItem, QStandardItemModel

from simkit.gui.corner_expand import (
    coherence_warnings as _row_coherence_warnings,
    expansion_count,
    expansion_tooltip,
)
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QShortcut,
    QStyledItemDelegate,
    QTableView,
    QVBoxLayout,
    QWidget,
)


# Column layout (spec §11; system prompt 1.layout.Center table):
COL_ENABLE = 0
COL_ROW_NAME = 1
COL_PROCESS = 2
COL_TEMPERATURE = 3
COL_VDD = 4
COL_MODEL_FILE = 5
COL_EXTRA_VARS = 6
# G-9: read-only computed column — how many sub-corners this row expands
# to. Makes a "secretly 6 corners" process-sweep row visually obvious.
COL_EXPANDS = 7

_HEADERS = [
    "Enable",
    "row_name",
    "process",
    "temperature",
    "vdd",
    "model_file",
    "extra_vars",
    "expands to",
]

# Header tooltips (G-9) — explain the corner model so the user is not
# left guessing why supply lives in two places or what a comma means.
_HEADER_TOOLTIPS = {
    COL_PROCESS: (
        "工艺角。逗号分隔即多 process 扫描:\n"
        "  tt,ss,ff  →  这一行展开为 3 个 sub-corner"
    ),
    COL_VDD: (
        "供电电压。供电请统一写在这一列,\n"
        "不要藏在 extra_vars 的自由文本里。"
    ),
    COL_TEMPERATURE: "温度 (°C)。下拉是常用值,可自由输入其它温度。",
    COL_EXTRA_VARS: (
        "其它 corner 变量,形如  K=v; K2=a,b\n"
        "值用逗号分隔即扫描。供电变量应放回 vdd 列。"
    ),
    COL_EXPANDS: (
        "这一行实际展开成多少个 sub-corner(只读,自动计算)。\n"
        "鼠标悬停可看到每个 sub-corner 的名字与变量组合。"
    ),
}

# Per-cell dropdown vocabularies (spec §11.1 "Per-cell dropdown for
# known-value cells").
PROCESS_VALUES = ["tt", "ss", "ff", "sf", "fs"]
TEMPERATURE_VALUES = ["-40", "0", "27", "85", "125"]


class _ComboBoxDelegate(QStyledItemDelegate):
    """Editor delegate that pops a QComboBox over a cell.

    ``editable`` controls whether the user can type a free-form value
    that's not in ``values`` (true for temperature per spec §11.1 —
    common values surfaced as suggestions, anything else still allowed).
    """

    def __init__(self, values: list[str], editable: bool, parent=None):
        super().__init__(parent)
        self._values = list(values)
        self._editable = editable

    def createEditor(self, parent, option, index):  # noqa: N802 (Qt API)
        combo = QComboBox(parent)
        combo.addItems(self._values)
        combo.setEditable(self._editable)
        return combo

    def setEditorData(self, editor: QComboBox, index):  # noqa: N802
        value = index.data(Qt.EditRole)
        if value is None:
            value = ""
        i = editor.findText(str(value))
        if i >= 0:
            editor.setCurrentIndex(i)
        elif self._editable:
            editor.setEditText(str(value))

    def setModelData(self, editor: QComboBox, model, index):  # noqa: N802
        model.setData(index, editor.currentText(), Qt.EditRole)


class CornersEditor(QWidget):
    """Right-panel tab content for the corner union (spec §11).

    Construct with ``parent=`` only; populate via ``load_union(rows)``.
    Public API + signals are documented on the class — keep them stable;
    ``MainWindow`` wires against this surface.
    """

    # MainWindow -> BridgeWorker routing signals (see module docstring).
    pull_requested = pyqtSignal()
    push_requested = pyqtSignal(object)

    # Divergence-strip action signals (spec §11.2).
    show_diff = pyqtSignal()
    pull_overrides_sidecar = pyqtSignal()
    keep_sidecar = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("cornersEditor")

        # Optional project-root context for model_file existence checks
        # (Phase 4 Stage 3 §11.3). None unbinds — validation falls back to
        # the row_name / duplicate checks only.
        self._project_root: Optional[Path] = None

        # Reentrancy guard (G-9): _refresh_coherence writes the computed
        # "expands to" cells + tooltips, which itself fires itemChanged.
        # Without this flag that would recurse into _refresh_push_enabled.
        self._refreshing = False

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # --- header bar (spec §11.1 pull/push + last-sync label) -------
        self._header = QWidget(self, objectName="cornersHeader")
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(0, 0, 0, 0)

        self.pull_button = QPushButton("Pull from Maestro", self._header)
        self.pull_button.setObjectName("pullButton")
        self.pull_button.clicked.connect(self.pull_requested.emit)
        header_layout.addWidget(self.pull_button)

        self.push_button = QPushButton("Send to Maestro", self._header)
        self.push_button.setObjectName("pushButton")
        # Disabled by default — no rows means nothing valid to push.
        # ``_refresh_push_enabled`` recomputes this whenever the model
        # changes (validation gate per spec §11.3).
        self.push_button.setEnabled(False)
        self.push_button.clicked.connect(self._on_push_clicked)
        header_layout.addWidget(self.push_button)

        header_layout.addStretch(1)

        self.last_sync_label = QLabel("Last sync: —", self._header)
        self.last_sync_label.setObjectName("lastSyncLabel")
        header_layout.addWidget(self.last_sync_label)

        root.addWidget(self._header)

        # --- divergence strip (spec §11.2) ------------------------------
        self._divergence = QFrame(self, objectName="divergenceStrip")
        self._divergence.setFrameShape(QFrame.StyledPanel)
        self._divergence.setStyleSheet(
            "QFrame#divergenceStrip { background: #fff3a3; "
            "border: 1px solid #d4b500; }"
        )
        div_layout = QHBoxLayout(self._divergence)
        div_layout.setContentsMargins(6, 4, 6, 4)
        self.divergence_label = QLabel("", self._divergence)
        self.divergence_label.setObjectName("divergenceLabel")
        self.divergence_label.setWordWrap(True)
        div_layout.addWidget(self.divergence_label, stretch=1)

        self.show_diff_button = QPushButton("show diff", self._divergence)
        self.show_diff_button.setObjectName("showDiffButton")
        self.show_diff_button.clicked.connect(self.show_diff.emit)
        div_layout.addWidget(self.show_diff_button)

        self.pull_overrides_button = QPushButton(
            "pull overrides sidecar", self._divergence
        )
        self.pull_overrides_button.setObjectName("pullOverridesButton")
        self.pull_overrides_button.clicked.connect(
            self.pull_overrides_sidecar.emit
        )
        div_layout.addWidget(self.pull_overrides_button)

        self.keep_sidecar_button = QPushButton("keep sidecar", self._divergence)
        self.keep_sidecar_button.setObjectName("keepSidecarButton")
        self.keep_sidecar_button.clicked.connect(self.keep_sidecar.emit)
        div_layout.addWidget(self.keep_sidecar_button)

        self._divergence.setVisible(False)
        root.addWidget(self._divergence)

        # --- validation-error strip (B-2) -------------------------------
        # "Send to Maestro" is gated on validation_errors(); without this
        # strip the button just greys out with no explanation (observed
        # 1AXX dogfood: a bare model_file fails the existence check and
        # the user has no idea why push is dead).
        self._errors = QFrame(self, objectName="cornerErrorStrip")
        self._errors.setFrameShape(QFrame.StyledPanel)
        self._errors.setStyleSheet(
            "QFrame#cornerErrorStrip { background: #f8d7da; "
            "border: 1px solid #c0392b; }"
        )
        err_layout = QHBoxLayout(self._errors)
        err_layout.setContentsMargins(6, 4, 6, 4)
        self.errors_label = QLabel("", self._errors)
        self.errors_label.setObjectName("cornerErrorLabel")
        self.errors_label.setWordWrap(True)
        err_layout.addWidget(self.errors_label, stretch=1)
        self._errors.setVisible(False)
        root.addWidget(self._errors)

        # --- coherence-warning strip (G-9) ------------------------------
        # Amber, distinct from the red error strip: these are advisories
        # (supply split across vdd column / extra_vars) — they never
        # disable push, they just nudge the user toward a tidy corner
        # model.
        self._warnings = QFrame(self, objectName="cornerWarnStrip")
        self._warnings.setFrameShape(QFrame.StyledPanel)
        self._warnings.setStyleSheet(
            "QFrame#cornerWarnStrip { background: #fff3a3; "
            "border: 1px solid #d4b500; }"
        )
        warn_layout = QHBoxLayout(self._warnings)
        warn_layout.setContentsMargins(6, 4, 6, 4)
        self.warnings_label = QLabel("", self._warnings)
        self.warnings_label.setObjectName("cornerWarnLabel")
        self.warnings_label.setWordWrap(True)
        warn_layout.addWidget(self.warnings_label, stretch=1)
        self._warnings.setVisible(False)
        root.addWidget(self._warnings)

        # --- center table (spec §11 affordances + A3 model layer) -------
        # We use QStandardItemModel here (not a custom QAbstractTableModel)
        # because the editor mutates rows freely (add/dup/delete) and the
        # checkable-Enable column is most naturally expressed as a
        # QStandardItem with the Qt.ItemIsUserCheckable flag. A3's hard
        # "QAbstractTableModel only" rule applies to *results* tables
        # where sort+filter proxies wrap a large read-only view; an
        # editor table is the explicit exception.
        self._model = QStandardItemModel(0, len(_HEADERS), self)
        self._model.setHorizontalHeaderLabels(_HEADERS)
        # G-9 — header tooltips explain the corner model (process comma
        # sweep, vdd-vs-extra_vars, the computed expands column).
        for col, tip in _HEADER_TOOLTIPS.items():
            header_item = self._model.horizontalHeaderItem(col)
            if header_item is not None:
                header_item.setToolTip(tip)
        # Recompute validation + push-enabled on any cell edit.
        self._model.itemChanged.connect(self._on_item_changed)

        self.table = QTableView(self)
        self.table.setObjectName("cornersTable")
        self.table.setModel(self._model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        # extra_vars (free text) is the column worth stretching, not the
        # narrow computed expands column that now sits last (G-9).
        self.table.horizontalHeader().setStretchLastSection(False)
        # Explicit row height — Qt's auto-section-size can resolve to 0 px on
        # some plugin/platform combos (observed on the 1AXX dogfood host:
        # log says "pulled 3 rows" but the table is visually empty because
        # every row got 0 pixels). Fixed-size sections + ResizeToContents
        # gives Qt the strongest hint that rows must have non-zero height.
        from PyQt5.QtWidgets import QHeaderView
        vh = self.table.verticalHeader()
        vh.setDefaultSectionSize(24)
        vh.setMinimumSectionSize(20)
        vh.setSectionResizeMode(QHeaderView.Fixed)
        # Stretch the free-text extra_vars column; the trailing computed
        # expands column stays at its (narrow) content width (G-9).
        self.table.horizontalHeader().setSectionResizeMode(
            COL_EXTRA_VARS, QHeaderView.Stretch
        )

        # Per-cell dropdowns for the known-value columns (spec §11.1).
        # editable=True so the user can type comma-separated values for
        # process sweeps (e.g. "tt,ss,ff" — a single-row 3-process sweep,
        # which is how Maestro encodes process variation via model.section).
        # The dropdown still offers the 5 canonical single-process picks.
        self._process_delegate = _ComboBoxDelegate(
            PROCESS_VALUES, editable=True, parent=self.table
        )
        self.table.setItemDelegateForColumn(COL_PROCESS, self._process_delegate)

        # Temperature dropdown is editable: suggest the common set but
        # allow free-form (spec §11.1 "Unknown cells stay free-form").
        self._temperature_delegate = _ComboBoxDelegate(
            TEMPERATURE_VALUES, editable=True, parent=self.table
        )
        self.table.setItemDelegateForColumn(
            COL_TEMPERATURE, self._temperature_delegate
        )

        root.addWidget(self.table, stretch=1)

        # --- bottom button row ------------------------------------------
        button_row = QWidget(self, objectName="cornersButtonRow")
        btn_layout = QHBoxLayout(button_row)
        btn_layout.setContentsMargins(0, 0, 0, 0)

        self.add_row_button = QPushButton("Add row", button_row)
        self.add_row_button.setObjectName("addRowButton")
        self.add_row_button.clicked.connect(self.add_row)
        btn_layout.addWidget(self.add_row_button)

        self.duplicate_row_button = QPushButton(
            "Duplicate row", button_row
        )
        self.duplicate_row_button.setObjectName("duplicateRowButton")
        self.duplicate_row_button.clicked.connect(self.duplicate_row)
        btn_layout.addWidget(self.duplicate_row_button)

        self.delete_row_button = QPushButton("Delete row", button_row)
        self.delete_row_button.setObjectName("deleteRowButton")
        self.delete_row_button.clicked.connect(self.delete_row)
        btn_layout.addWidget(self.delete_row_button)

        btn_layout.addStretch(1)

        root.addWidget(button_row)

        # Ctrl+Shift+N — Add row shortcut (spec §11.1).
        self._add_row_shortcut = QShortcut(
            QKeySequence("Ctrl+Shift+N"), self
        )
        self._add_row_shortcut.activated.connect(self.add_row)

        # Initial validation refresh (empty model -> errors -> disable push).
        self._refresh_push_enabled()

    # ------------------------------------------------------------------
    # Public API — load / dump
    # ------------------------------------------------------------------

    def load_union(self, rows: Iterable[dict]) -> None:
        """Replace the table contents with ``rows`` (presentation dicts).

        Each row is a dict; recognised keys are the column names
        (``row_name`` / ``process`` / ``temperature`` / ``vdd`` /
        ``model_file`` / ``extra_vars``). The boolean ``_enabled``
        controls the checkbox state (defaults to True if absent).

        Unknown keys in input dicts are dropped — round-tripping via
        ``dump_union()`` returns only the recognised columns. The
        translation between the editor's flat shape and the on-disk
        ``.union.json`` is done by MainWindow.
        """
        self._model.blockSignals(True)
        try:
            self._model.removeRows(0, self._model.rowCount())
            for row in rows:
                self._append_row_from_dict(row)
        finally:
            self._model.blockSignals(False)
        # blockSignals suppressed every rowsInserted notification — the
        # QTableView attached to this model never learned about the new rows,
        # so its row heights stayed at zero and the table rendered visually
        # empty (1AXX dogfood: "pulled 3 rows" log but blank Corners tab).
        # layoutChanged is the standard Qt way to say "the whole shape just
        # changed, reset everything" without re-emitting one-rowsInserted
        # per row.
        self._model.layoutChanged.emit()
        # One refresh after the bulk load so validation reflects the
        # final contents, not each intermediate insert.
        self._refresh_push_enabled()

    def dump_union(self) -> list[dict]:
        """Read the current model state back as a list of dicts.

        Disabled rows carry ``_enabled: False``; enabled rows omit the
        flag (the default is True). Empty cells are emitted as empty
        strings rather than ``None`` so the schema stays string-typed
        and the round-trip stays stable.
        """
        out: list[dict] = []
        for r in range(self._model.rowCount()):
            entry: dict[str, Any] = {
                "row_name": self._cell_text(r, COL_ROW_NAME),
                "process": self._cell_text(r, COL_PROCESS),
                "temperature": self._cell_text(r, COL_TEMPERATURE),
                "vdd": self._cell_text(r, COL_VDD),
                "model_file": self._cell_text(r, COL_MODEL_FILE),
                "extra_vars": self._cell_text(r, COL_EXTRA_VARS),
            }
            enable_item = self._model.item(r, COL_ENABLE)
            if enable_item is not None and enable_item.checkState() != Qt.Checked:
                entry["_enabled"] = False
            out.append(entry)
        return out

    def validation_errors(self) -> list[str]:
        """Collect every live validation error.

        Empty list means "ready to push" — used to gate the "Send to
        Maestro" button per spec §11.3.

        Checks:
          * Missing ``row_name`` on any row.
          * Duplicate ``row_name`` across rows.
          * When :meth:`set_project_root` is bound: every non-empty
            ``model_file`` cell is resolved relative to project_root and
            checked for existence.
        """
        errors: list[str] = []
        names: dict[str, int] = {}
        for r in range(self._model.rowCount()):
            name = self._cell_text(r, COL_ROW_NAME).strip()
            if not name:
                errors.append(f"row {r + 1}: missing row_name")
            else:
                names.setdefault(name, 0)
                names[name] += 1
            if self._project_root is not None:
                model_file = self._cell_text(r, COL_MODEL_FILE).strip()
                if model_file:
                    candidate = Path(model_file)
                    if not candidate.is_absolute():
                        candidate = self._project_root / candidate
                    abs_path = candidate
                    if not abs_path.exists():
                        errors.append(
                            f"row {r + 1}: model_file {model_file!r} not "
                            f"found at {abs_path}"
                        )
        for name, count in names.items():
            if count > 1:
                errors.append(f"duplicate row_name: {name!r} ({count} rows)")
        return errors

    # ------------------------------------------------------------------
    # Public API — header + divergence helpers
    # ------------------------------------------------------------------

    def set_last_sync(self, datetime_str: str) -> None:
        """Update the "Last sync:" label (spec §11.1).

        Pass an empty string or ``"—"`` to indicate "never".
        """
        text = datetime_str if datetime_str else "—"
        self.last_sync_label.setText(f"Last sync: {text}")

    def set_project_root(self, project_root: Path | None) -> None:
        """Bind the editor to a project root so model_file existence checks fire.

        ``None`` unbinds; validation reverts to row_name / duplicate checks
        only.
        """
        if project_root is None:
            self._project_root = None
        else:
            self._project_root = Path(project_root).expanduser().resolve()
        self._refresh_push_enabled()

    def set_divergence(self, live_count: int, sidecar_count: int) -> None:
        """Show / hide the live-vs-sidecar divergence strip (spec §11.2).

        When counts match the strip is hidden; otherwise it appears with
        the message verbatim from spec §11.2.
        """
        if live_count == sidecar_count:
            self._divergence.setVisible(False)
            return
        self.divergence_label.setText(
            f"Maestro session has {live_count} rows, "
            f"your sidecar has {sidecar_count} — "
            "[show diff] [pull overrides sidecar] [keep sidecar]"
        )
        self._divergence.setVisible(True)

    # ------------------------------------------------------------------
    # Row mutations — Add / Duplicate / Delete
    # ------------------------------------------------------------------

    def add_row(self) -> None:
        """Append a new empty row with an auto-generated unique name.

        Generated name is ``corner_<n>`` where ``n`` is the smallest
        positive integer that doesn't collide with any existing row
        name in this editor (spec §11.1 wording: "New row gets a
        generated unique row_name").

        The new row inherits the ``model_file`` path from the first
        existing row that carries one.  Rows within a corner normally
        share a single model file, so this avoids the empty-``_file_abs``
        / ``include ""`` Spectre error (SFE-73) that appears when an
        add-row corner is pushed without a model file.
        """
        name = self._next_unique_name("corner_")
        inherited_model_file = self._first_nonempty_model_file()
        row: dict = {"row_name": name}
        if inherited_model_file:
            row["model_file"] = inherited_model_file
        self._append_row_from_dict(row)
        self._refresh_push_enabled()

    def duplicate_row(self) -> None:
        """Duplicate the selected row with a ``_copy`` suffix (spec §11.1).

        If no row is selected, no-op. If the suffixed name already
        exists, the helper keeps adding ``_copy`` until it's unique
        (prevents the duplicate-name validation error firing
        immediately).
        """
        r = self._selected_row()
        if r is None:
            return
        src = self._row_to_dict(r)
        base = (src.get("row_name") or "corner") + "_copy"
        src["row_name"] = self._uniquify_name(base)
        self._append_row_from_dict(src)
        self._refresh_push_enabled()

    def delete_row(self) -> None:
        """Remove the selected row (spec §11.1).

        No-op if nothing is selected.
        """
        r = self._selected_row()
        if r is None:
            return
        self._model.removeRow(r)
        self._refresh_push_enabled()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _append_row_from_dict(self, row: dict) -> None:
        """Append one row, applying the dict's known keys."""
        items: list[QStandardItem] = []

        enable_item = QStandardItem()
        enable_item.setCheckable(True)
        enable_item.setEditable(False)
        # ``_enabled`` defaults to True (spec §11.1 "default ON").
        enabled = row.get("_enabled", True)
        enable_item.setCheckState(Qt.Checked if enabled else Qt.Unchecked)
        items.append(enable_item)

        for key in (
            "row_name",
            "process",
            "temperature",
            "vdd",
            "model_file",
            "extra_vars",
        ):
            value = row.get(key, "")
            if value is None:
                value = ""
            items.append(QStandardItem(str(value)))

        # COL_EXPANDS — read-only computed cell (G-9). Filled in by
        # _refresh_coherence; created here so the column is never None.
        expands_item = QStandardItem("")
        expands_item.setEditable(False)
        expands_item.setTextAlignment(Qt.AlignCenter)
        items.append(expands_item)

        self._model.appendRow(items)

    def _on_item_changed(self, _item: QStandardItem) -> None:
        # itemChanged fires for every cell edit + every checkstate toggle.
        # Either can affect validation outcomes (row_name edits) or the
        # dump_union payload (checkstate); push-enabled depends only on
        # validation. Cheap to recompute either way.
        #
        # Reentrancy guard: _refresh_coherence writes the computed
        # expands cells + tooltips, which re-fires itemChanged. Bail out
        # so that cosmetic write does not recurse.
        if self._refreshing:
            return
        self._refresh_push_enabled()

    def _on_push_clicked(self) -> None:
        # The button is gated by ``_refresh_push_enabled`` so we should
        # only get here when validation passes. Re-check defensively
        # before emitting so a buggy enabled-state can't sneak an
        # invalid payload onto the wire.
        if self.validation_errors():
            return
        self.push_requested.emit(self.dump_union())

    def _refresh_push_enabled(self) -> None:
        errors = self.validation_errors()
        has_rows = self._model.rowCount() > 0
        self.push_button.setEnabled(has_rows and not errors)
        if has_rows and errors:
            joined = "; ".join(errors)
            self.errors_label.setText(
                f"Send to Maestro disabled — fix "
                f"{len(errors)} issue(s): {joined}"
            )
            self._errors.setVisible(True)
            self.push_button.setToolTip(joined)
        else:
            self._errors.setVisible(False)
            self.errors_label.setText("")
            self.push_button.setToolTip(
                "Add at least one corner row before sending to Maestro."
                if not has_rows
                else "Send the current corner set to the live Maestro session."
            )
        self._refresh_coherence()

    def coherence_warnings(self) -> list[str]:
        """Non-blocking supply-coherence advisories across all rows (G-9).

        Distinct from :meth:`validation_errors`: these never disable
        push. They flag the friction-#2 split where supply is hidden in
        ``extra_vars`` or defined in both the ``vdd`` column and there.
        """
        out: list[str] = []
        for r in range(self._model.rowCount()):
            out.extend(_row_coherence_warnings(self._row_to_dict(r)))
        return out

    def _refresh_coherence(self) -> None:
        """Recompute the expands column, process tooltips + warning strip.

        Writes computed model cells, so it runs under the
        :pyattr:`_refreshing` guard to keep :meth:`_on_item_changed`
        from recursing.
        """
        self._refreshing = True
        try:
            for r in range(self._model.rowCount()):
                flat = self._row_to_dict(r)
                self._update_expands_cell(r, flat)
                self._update_process_tooltip(r)
        finally:
            self._refreshing = False

        warnings = self.coherence_warnings()
        if warnings:
            self.warnings_label.setText(
                "供电定义不一致 — " + "; ".join(warnings)
            )
            self._warnings.setVisible(True)
        else:
            self._warnings.setVisible(False)
            self.warnings_label.setText("")

    def _update_expands_cell(self, r: int, flat: dict) -> None:
        item = self._model.item(r, COL_EXPANDS)
        if item is None:
            item = QStandardItem("")
            item.setEditable(False)
            item.setTextAlignment(Qt.AlignCenter)
            self._model.setItem(r, COL_EXPANDS, item)
        count = expansion_count(flat)
        item.setText("—" if count == 0 else f"× {count}")
        item.setToolTip(expansion_tooltip(flat))
        # Tint rows that are secretly multi-corner so a process sweep is
        # not mistaken for a single corner.
        item.setBackground(
            QBrush(QColor(0xE6, 0xF0, 0xFF)) if count > 1 else QBrush()
        )

    def _update_process_tooltip(self, r: int) -> None:
        item = self._model.item(r, COL_PROCESS)
        if item is None:
            return
        text = (item.text() or "").strip()
        sections = [p.strip() for p in text.split(",") if p.strip()]
        if len(sections) > 1:
            item.setToolTip(
                f"{len(sections)}-process 扫描: {', '.join(sections)}\n"
                "(逗号分隔 = 多个 process,这一行会展开成多个 sub-corner)"
            )
        else:
            item.setToolTip("")

    def _cell_text(self, row: int, col: int) -> str:
        item = self._model.item(row, col)
        if item is None:
            return ""
        return item.text() or ""

    def _selected_row(self) -> Optional[int]:
        sel = self.table.selectionModel()
        if sel is None:
            return None
        idx = sel.currentIndex()
        if not idx.isValid():
            return None
        return idx.row()

    def _row_to_dict(self, r: int) -> dict:
        d = {
            "row_name": self._cell_text(r, COL_ROW_NAME),
            "process": self._cell_text(r, COL_PROCESS),
            "temperature": self._cell_text(r, COL_TEMPERATURE),
            "vdd": self._cell_text(r, COL_VDD),
            "model_file": self._cell_text(r, COL_MODEL_FILE),
            "extra_vars": self._cell_text(r, COL_EXTRA_VARS),
        }
        enable_item = self._model.item(r, COL_ENABLE)
        if enable_item is not None:
            d["_enabled"] = enable_item.checkState() == Qt.Checked
        return d

    def _existing_names(self) -> set[str]:
        out: set[str] = set()
        for r in range(self._model.rowCount()):
            out.add(self._cell_text(r, COL_ROW_NAME))
        return out

    def _next_unique_name(self, prefix: str) -> str:
        existing = self._existing_names()
        n = 1
        while f"{prefix}{n}" in existing:
            n += 1
        return f"{prefix}{n}"

    def _uniquify_name(self, base: str) -> str:
        existing = self._existing_names()
        if base not in existing:
            return base
        n = 2
        while f"{base}{n}" in existing:
            n += 1
        return f"{base}{n}"

    def _first_nonempty_model_file(self) -> str:
        """Return the first non-empty ``model_file`` value in the model.

        Used by :meth:`add_row` so new rows inherit the corner's shared
        model file rather than leaving ``model_file`` blank (SFE-73).
        Returns an empty string when no row has a model file yet.
        """
        for r in range(self._model.rowCount()):
            value = self._cell_text(r, COL_MODEL_FILE).strip()
            if value:
                return value
        return ""
