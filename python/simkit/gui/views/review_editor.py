"""Review authoring — copy-edit dialog + shared building blocks (spec §14).

This module implements Tier-1 capability #7 (**copy-edit review**) and
provides the reusable pieces the from-scratch wizard (capability #8,
:mod:`simkit.gui.views.review_wizard`) builds on:

* :class:`ReviewItemsTable` — an editable table of review *items*
  (``name`` / ``tests`` / ``union`` / ``bundle`` / per-item ``on_failure``
  policy). Each row carries its *source* item dict so fields the GUI does
  not surface (``ic_from``, ``baseline_corner``, ``on_failure.strategies``)
  survive a copy-edit round-trip untouched.
* :class:`SuiteFailureControls` — suite-level ``on_failure`` default
  policy + a one-entry strategy chain (``naive_retry`` + ``max_attempts``).
* :func:`build_review_dict` / :func:`validate_review_dict` — assemble the
  ``.review.json`` body and validate it through ``simkit.review`` *before*
  it ever touches the real target path.
* :class:`ReviewEditorDialog` — the form editor itself.

Nothing here touches the Bridge; the whole module is unit-testable with
``pytest-qt`` and no live Maestro session.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from simkit.review import ReviewError, load_review, validate_paths_exist


# Mirror review.py's _REVIEW_NAME_RE — the on-disk file stem regex.
_REVIEW_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")

_UNIONS_SUBDIR = "unions"
_BUNDLES_SUBDIR = "bundles"
_REVIEWS_SUBDIR = "reviews"

# Strategy names selectable in the GUI. v1 ships naive_retry only
# (DECISIONS #52); "(none)" means an empty strategy chain.
_STRATEGY_NONE = "(none)"
_STRATEGY_CHOICES = (_STRATEGY_NONE, "naive_retry")

_POLICY_INHERIT = "(inherit suite)"
_POLICY_CHOICES = (_POLICY_INHERIT, "skip", "halt")


# ---------------------------------------------------------------------------
# Filesystem discovery
# ---------------------------------------------------------------------------


def is_valid_review_name(name: str) -> bool:
    """True when ``name`` is a legal ``.review.json`` file stem."""
    return bool(_REVIEW_NAME_RE.match(name))


def discover_unions(project_root: Path) -> list[str]:
    """Return ``../unions/<file>`` rel-paths for every union sidecar.

    The path form mirrors what a real ``.review.json`` stores: the file
    lives in ``reviews/`` and references siblings via ``../unions/``.
    """
    return _discover(project_root / _UNIONS_SUBDIR, ".union.json", _UNIONS_SUBDIR)


def discover_bundles(project_root: Path) -> list[str]:
    """Return ``../bundles/<file>`` rel-paths for every measure bundle."""
    return _discover(
        project_root / _BUNDLES_SUBDIR, ".measure.json", _BUNDLES_SUBDIR
    )


def _discover(directory: Path, suffix: str, subdir: str) -> list[str]:
    if not directory.is_dir():
        return []
    out = [
        f"../{subdir}/{p.name}"
        for p in sorted(directory.iterdir())
        if p.is_file() and p.name.endswith(suffix)
    ]
    return out


# ---------------------------------------------------------------------------
# Review-dict assembly + validation
# ---------------------------------------------------------------------------


def build_review_dict(
    name: str,
    project: str,
    items: list[dict[str, Any]],
    suite_on_failure: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Assemble a ``.review.json`` body (schema v1)."""
    doc: dict[str, Any] = {
        "_doc": "Authored via the simkit GUI review editor — edit freely.",
        "review_schema_version": 1,
        "name": name,
        "project": project,
    }
    if suite_on_failure:
        doc["on_failure"] = suite_on_failure
    doc["items"] = items
    return doc


def validate_review_dict(
    review_dict: dict[str, Any], reviews_dir: Path
) -> tuple[Optional[str], list[str]]:
    """Validate ``review_dict`` by round-tripping it through the real loader.

    Writes a throw-away ``<name>.review.json`` into ``reviews_dir`` (the
    real filename — ``load_review`` requires the ``name`` field to equal
    the file's basename, and relative ``../unions`` / ``../bundles`` paths
    must resolve from ``reviews/``), runs ``simkit.review.load_review``,
    then deletes it.

    Returns ``(error, warnings)`` — ``error`` is ``None`` when the review
    is structurally valid; ``warnings`` lists union/bundle paths that do
    not exist on disk (non-fatal).
    """
    reviews_dir.mkdir(parents=True, exist_ok=True)
    tmp = reviews_dir / f"{review_dict.get('name', '')}.review.json"
    if tmp.exists():
        return (f"{tmp.name} already exists — choose another name.", [])
    try:
        tmp.write_text(json.dumps(review_dict, indent=2), encoding="utf-8")
        try:
            review = load_review(tmp)
        except ReviewError as exc:
            return (str(exc), [])
        except Exception as exc:  # noqa: BLE001
            return (f"{type(exc).__name__}: {exc}", [])
        warnings = [
            f"[{issue.item_name}] {issue.kind} {issue.reason}: {issue.path}"
            for issue in validate_paths_exist(review)
        ]
        return (None, warnings)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Items table
# ---------------------------------------------------------------------------


class ReviewItemsTable(QWidget):
    """Editable table of review items, reused by the editor and the wizard.

    Each row remembers the *source* item dict it was loaded from; only the
    five surfaced fields (name / tests / union / bundle / on-fail policy)
    are overwritten on :meth:`to_items`, so a copy-edit preserves any
    schema-v2 keys (``ic_from`` …) the GUI does not expose.
    """

    changed = pyqtSignal()

    _COLS = ("Item name", "Tests (comma-separated)", "Union", "Bundle",
             "On failure")

    def __init__(
        self,
        unions: list[str],
        bundles: list[str],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._unions = list(unions)
        self._bundles = list(bundles)
        # Parallel to table rows: the dict each row was seeded from.
        self._sources: list[dict[str, Any]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.table = QTableWidget(0, len(self._COLS))
        self.table.setHorizontalHeaderLabels(self._COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.itemChanged.connect(lambda _i: self.changed.emit())
        layout.addWidget(self.table)

        btn_row = QHBoxLayout()
        self.add_btn = QPushButton("+ Add item")
        self.remove_btn = QPushButton("− Remove selected")
        self.add_btn.clicked.connect(lambda: self.add_item())
        self.remove_btn.clicked.connect(self.remove_selected)
        btn_row.addWidget(self.add_btn)
        btn_row.addWidget(self.remove_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

    # -- row construction -------------------------------------------------

    def _make_combo(self, choices: list[str], current: str) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItems(choices)
        if current and current not in choices:
            combo.addItem(current)
        combo.setCurrentText(current)
        combo.currentTextChanged.connect(lambda _t: self.changed.emit())
        return combo

    def add_item(self, source: Optional[dict[str, Any]] = None) -> int:
        """Append a row seeded from ``source`` (a parsed item dict)."""
        src = dict(source) if source else {}
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._sources.insert(row, src)

        self.table.setItem(row, 0, QTableWidgetItem(str(src.get("name", ""))))
        tests = src.get("tests") or []
        self.table.setItem(
            row, 1, QTableWidgetItem(", ".join(str(t) for t in tests))
        )

        union_combo = self._make_combo(
            [""] + self._unions, str(src.get("union", "") or "")
        )
        self.table.setCellWidget(row, 2, union_combo)

        bundle_combo = self._make_combo(
            [""] + self._bundles, str(src.get("bundle", "") or "")
        )
        self.table.setCellWidget(row, 3, bundle_combo)

        policy_combo = QComboBox()
        policy_combo.addItems(list(_POLICY_CHOICES))
        item_policy = ((src.get("on_failure") or {}).get("item_policy"))
        policy_combo.setCurrentText(
            item_policy if item_policy in _POLICY_CHOICES else _POLICY_INHERIT
        )
        policy_combo.currentTextChanged.connect(lambda _t: self.changed.emit())
        self.table.setCellWidget(row, 4, policy_combo)

        self.changed.emit()
        return row

    def remove_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        self.table.removeRow(row)
        del self._sources[row]
        self.changed.emit()

    def load_items(self, items: list[dict[str, Any]]) -> None:
        """Replace all rows with ``items`` (parsed item dicts)."""
        self.table.setRowCount(0)
        self._sources.clear()
        for it in items:
            self.add_item(it)

    # -- read-back --------------------------------------------------------

    def row_count(self) -> int:
        return self.table.rowCount()

    def to_items(self) -> list[dict[str, Any]]:
        """Build item dicts, overlaying edits onto each row's source dict."""
        out: list[dict[str, Any]] = []
        for row in range(self.table.rowCount()):
            item = dict(self._sources[row])

            name_item = self.table.item(row, 0)
            item["name"] = name_item.text().strip() if name_item else ""

            tests_item = self.table.item(row, 1)
            raw_tests = tests_item.text() if tests_item else ""
            item["tests"] = [t.strip() for t in raw_tests.split(",") if t.strip()]

            union = self.table.cellWidget(row, 2).currentText().strip()
            item["union"] = union

            bundle = self.table.cellWidget(row, 3).currentText().strip()
            if bundle:
                item["bundle"] = bundle
            else:
                item.pop("bundle", None)

            policy = self.table.cellWidget(row, 4).currentText()
            on_fail = dict(item.get("on_failure") or {})
            if policy in ("skip", "halt"):
                on_fail["item_policy"] = policy
            else:
                on_fail.pop("item_policy", None)
            if on_fail:
                item["on_failure"] = on_fail
            else:
                item.pop("on_failure", None)

            out.append(item)
        return out


# ---------------------------------------------------------------------------
# Suite-level failure controls
# ---------------------------------------------------------------------------


class SuiteFailureControls(QGroupBox):
    """Suite-wide ``on_failure``: default policy + one strategy entry."""

    changed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("Suite-level failure handling", parent)
        form = QFormLayout(self)

        self.default_combo = QComboBox()
        self.default_combo.addItems(["skip", "halt"])
        self.default_combo.currentTextChanged.connect(
            lambda _t: self.changed.emit()
        )
        form.addRow("Default policy:", self.default_combo)

        self.strategy_combo = QComboBox()
        self.strategy_combo.addItems(list(_STRATEGY_CHOICES))
        self.strategy_combo.currentTextChanged.connect(self._on_strategy_changed)
        form.addRow("Retry strategy:", self.strategy_combo)

        self.max_attempts = QSpinBox()
        self.max_attempts.setRange(1, 99)
        self.max_attempts.setValue(3)
        self.max_attempts.setEnabled(False)
        self.max_attempts.valueChanged.connect(lambda _v: self.changed.emit())
        form.addRow("Max attempts:", self.max_attempts)

    def _on_strategy_changed(self, text: str) -> None:
        self.max_attempts.setEnabled(text != _STRATEGY_NONE)
        self.changed.emit()

    def set_policy(self, on_failure: Optional[dict[str, Any]]) -> None:
        """Pre-fill from a suite-level ``on_failure`` dict."""
        on_failure = on_failure or {}
        default = on_failure.get("default", "skip")
        self.default_combo.setCurrentText(
            default if default in ("skip", "halt") else "skip"
        )
        strategies = on_failure.get("strategies") or []
        if strategies:
            first = strategies[0]
            self.strategy_combo.setCurrentText(
                first.get("name", _STRATEGY_NONE)
            )
            self.max_attempts.setValue(int(first.get("max_attempts", 3) or 3))
        else:
            self.strategy_combo.setCurrentText(_STRATEGY_NONE)

    def to_on_failure(self) -> Optional[dict[str, Any]]:
        """Build the suite ``on_failure`` dict, or ``None`` when trivial."""
        out: dict[str, Any] = {}
        default = self.default_combo.currentText()
        if default == "halt":
            out["default"] = "halt"
        strategy = self.strategy_combo.currentText()
        if strategy != _STRATEGY_NONE:
            out["strategies"] = [
                {"name": strategy, "max_attempts": self.max_attempts.value()}
            ]
        return out or None


# ---------------------------------------------------------------------------
# Copy-edit dialog (capability #7)
# ---------------------------------------------------------------------------


class ReviewEditorDialog(QDialog):
    """Form editor for a ``.review.json`` — the copy-edit path (spec §14.1).

    Construct it pre-filled from an existing review (``source_review``) for
    "Copy as…", or empty for a blank form. On accept it has written a
    validated ``.review.json``; the written path is :attr:`saved_path`.
    """

    def __init__(
        self,
        project_root: Path,
        project_name: str,
        *,
        source_review: Optional[dict[str, Any]] = None,
        default_name: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("simkit — Edit Review")
        self.setMinimumWidth(640)
        self._project_root = Path(project_root)
        self._project_name = project_name
        self._reviews_dir = self._project_root / _REVIEWS_SUBDIR
        self.saved_path: Optional[Path] = None

        layout = QVBoxLayout(self)

        # -- name -------------------------------------------------------
        name_row = QFormLayout()
        self.name_edit = QLineEdit(default_name)
        self.name_edit.setPlaceholderText("letters / digits / _ / - only")
        name_row.addRow("Review name:", self.name_edit)
        proj_label = QLabel(project_name)
        name_row.addRow("Project:", proj_label)
        layout.addLayout(name_row)

        # -- items ------------------------------------------------------
        layout.addWidget(QLabel("Items (each runs sequentially):"))
        self.items_table = ReviewItemsTable(
            discover_unions(self._project_root),
            discover_bundles(self._project_root),
        )
        layout.addWidget(self.items_table)

        # -- suite failure ---------------------------------------------
        self.suite_controls = SuiteFailureControls()
        layout.addWidget(self.suite_controls)

        # -- inline error line -----------------------------------------
        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #b00020;")
        layout.addWidget(self.error_label)

        # -- buttons ----------------------------------------------------
        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # -- pre-fill ---------------------------------------------------
        if source_review is not None:
            self.items_table.load_items(list(source_review.get("items") or []))
            self.suite_controls.set_policy(source_review.get("on_failure"))
        if self.items_table.row_count() == 0:
            self.items_table.add_item()

    # -- save -------------------------------------------------------------

    def _on_save(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            self._fail("Review name is required.")
            return
        if not is_valid_review_name(name):
            self._fail(
                f"Review name {name!r} is invalid — "
                f"only letters, digits, '_' and '-' are allowed."
            )
            return
        target = self._reviews_dir / f"{name}.review.json"
        if target.exists():
            self._fail(f"{target.name} already exists — choose another name.")
            return
        if self.items_table.row_count() == 0:
            self._fail("A review needs at least one item.")
            return

        review_dict = build_review_dict(
            name,
            self._project_name,
            self.items_table.to_items(),
            self.suite_controls.to_on_failure(),
        )
        error, warnings = validate_review_dict(review_dict, self._reviews_dir)
        if error:
            self._fail(error)
            return
        if warnings and not self._confirm_warnings(warnings):
            return

        try:
            self._reviews_dir.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(review_dict, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            self._fail(f"Could not write {target}: {exc}")
            return
        self.saved_path = target
        self.accept()

    def _fail(self, message: str) -> None:
        self.error_label.setText(message)

    def _confirm_warnings(self, warnings: list[str]) -> bool:
        text = "Some referenced files do not exist yet:\n\n" + "\n".join(
            warnings
        ) + "\n\nSave anyway?"
        return (
            QMessageBox.question(
                self, "simkit — missing files", text,
                QMessageBox.Save | QMessageBox.Cancel,
            )
            == QMessageBox.Save
        )
