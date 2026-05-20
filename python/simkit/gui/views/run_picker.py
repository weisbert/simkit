"""RunPickerDialog — modal "compare against which run?" chooser (spec §10).

Trivial composition: a :class:`QLineEdit` filter on top of a
:class:`QListWidget`. Each row is a dict with ``run_id``, ``short_id``,
``timestamp`` and ``label`` keys (the same shape Agent A's loaders emit
for the left-tree History group).

Filtering: substring match on either ``short_id`` or ``label``
(case-insensitive). The current run (``current_run_id``) is excluded
from the list — comparing a run with itself is not a use case.

OK button is disabled until something is selected; double-click is a
shortcut for "select + OK".
"""

from __future__ import annotations

from typing import List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)


# Role to stash the run_id on each list item — Qt.UserRole is the
# canonical "your data here" slot.
_RUN_ID_ROLE = Qt.UserRole


class RunPickerDialog(QDialog):
    """Modal dialog returning a chosen ``run_id`` (or None on Cancel)."""

    def __init__(
        self,
        runs: List[dict],
        *,
        current_run_id: Optional[str] = None,
        title: str = "Compare against which run?",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self._selected_run_id: Optional[str] = None

        # Exclude the current run up front — keeps the filter logic and
        # the empty-list message simple.
        self._all_runs: List[dict] = [
            r for r in runs if r.get("run_id") != current_run_id
        ]

        v = QVBoxLayout(self)
        v.setContentsMargins(12, 12, 12, 12)

        self.filter_edit = QLineEdit(self)
        self.filter_edit.setPlaceholderText("Filter by short_id or label…")
        self.filter_edit.setObjectName("runPickerFilter")
        v.addWidget(self.filter_edit)

        self.list_widget = QListWidget(self)
        self.list_widget.setObjectName("runPickerList")
        v.addWidget(self.list_widget, stretch=1)

        # Empty-state label, visible when no rows match the filter.
        self.empty_label = QLabel(self)
        self.empty_label.setObjectName("runPickerEmptyLabel")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet("color: #888;")
        self.empty_label.hide()
        v.addWidget(self.empty_label)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            parent=self,
        )
        self.button_box.accepted.connect(self._on_accept)
        self.button_box.rejected.connect(self.reject)
        v.addWidget(self.button_box)

        self.filter_edit.textChanged.connect(self._on_filter_changed)
        self.list_widget.itemSelectionChanged.connect(
            self._on_selection_changed,
        )
        self.list_widget.itemDoubleClicked.connect(
            self._on_item_double_clicked,
        )

        self._repopulate("")

    # --- public API ------------------------------------------------------

    @property
    def selected_run_id(self) -> Optional[str]:
        """The chosen run_id, or None if the dialog was cancelled."""
        return self._selected_run_id

    # --- internals -------------------------------------------------------

    def _repopulate(self, needle: str) -> None:
        """Rebuild list_widget contents matching ``needle`` (case-insensitive)."""
        needle = needle.strip().lower()
        self.list_widget.clear()
        shown = 0
        for r in self._all_runs:
            if needle and not _matches(r, needle):
                continue
            item = QListWidgetItem(_format_row(r))
            item.setData(_RUN_ID_ROLE, r.get("run_id"))
            self.list_widget.addItem(item)
            shown += 1

        if shown == 0:
            if not self._all_runs:
                self.empty_label.setText("No other runs available to compare.")
            else:
                self.empty_label.setText("No runs match the filter.")
            self.empty_label.show()
            self.list_widget.hide()
        else:
            self.empty_label.hide()
            self.list_widget.show()

        # No selection after a repopulate → disable OK.
        self._update_ok_enabled()

    def _on_filter_changed(self, text: str) -> None:
        self._repopulate(text)

    def _on_selection_changed(self) -> None:
        self._update_ok_enabled()

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        # Double-click is "pick this row and accept" — set selection
        # explicitly (it usually is already) and call _on_accept directly.
        item.setSelected(True)
        self._on_accept()

    def _update_ok_enabled(self) -> None:
        ok_btn = self.button_box.button(QDialogButtonBox.Ok)
        if ok_btn is not None:
            ok_btn.setEnabled(self.list_widget.currentItem() is not None
                              and self.list_widget.currentItem().isSelected())

    def _on_accept(self) -> None:
        item = self.list_widget.currentItem()
        if item is None or not item.isSelected():
            # Defensive: should be guarded by the OK-disabled state.
            return
        self._selected_run_id = item.data(_RUN_ID_ROLE)
        self.accept()


class MultiRunPickerDialog(QDialog):
    """Modal dialog returning 2+ chosen ``run_id``s for a trend (G-6).

    Same filter-over-list shape as :class:`RunPickerDialog`, but with
    extended selection and an OK button gated on *two or more* rows
    being selected — a trend of one run is not a trend. The returned
    :pyattr:`selected_run_ids` preserves the dialog's display order
    (newest-first); the caller re-orders for the trend axis.
    """

    def __init__(
        self,
        runs: List[dict],
        *,
        title: str = "Milestone trend — select 2 or more runs",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self._selected_run_ids: List[str] = []
        self._all_runs: List[dict] = list(runs)

        v = QVBoxLayout(self)
        v.setContentsMargins(12, 12, 12, 12)

        hint = QLabel(
            "Hold Ctrl / Shift to multi-select. Pick in milestone order "
            "(PDR → CDR → FDR); the trend is arranged chronologically.",
            self,
        )
        hint.setObjectName("multiRunPickerHint")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888;")
        v.addWidget(hint)

        self.filter_edit = QLineEdit(self)
        self.filter_edit.setPlaceholderText("Filter by short_id or label…")
        self.filter_edit.setObjectName("multiRunPickerFilter")
        v.addWidget(self.filter_edit)

        self.list_widget = QListWidget(self)
        self.list_widget.setObjectName("multiRunPickerList")
        self.list_widget.setSelectionMode(QListWidget.ExtendedSelection)
        v.addWidget(self.list_widget, stretch=1)

        self.empty_label = QLabel(self)
        self.empty_label.setObjectName("multiRunPickerEmptyLabel")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet("color: #888;")
        self.empty_label.hide()
        v.addWidget(self.empty_label)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            parent=self,
        )
        self.button_box.accepted.connect(self._on_accept)
        self.button_box.rejected.connect(self.reject)
        v.addWidget(self.button_box)

        self.filter_edit.textChanged.connect(self._on_filter_changed)
        self.list_widget.itemSelectionChanged.connect(
            self._update_ok_enabled,
        )

        self._repopulate("")

    @property
    def selected_run_ids(self) -> List[str]:
        """The chosen run_ids, or [] if the dialog was cancelled."""
        return list(self._selected_run_ids)

    def _repopulate(self, needle: str) -> None:
        needle = needle.strip().lower()
        self.list_widget.clear()
        shown = 0
        for r in self._all_runs:
            if needle and not _matches(r, needle):
                continue
            item = QListWidgetItem(_format_row(r))
            item.setData(_RUN_ID_ROLE, r.get("run_id"))
            self.list_widget.addItem(item)
            shown += 1

        if shown == 0:
            self.empty_label.setText(
                "No runs available." if not self._all_runs
                else "No runs match the filter."
            )
            self.empty_label.show()
            self.list_widget.hide()
        else:
            self.empty_label.hide()
            self.list_widget.show()
        self._update_ok_enabled()

    def _on_filter_changed(self, text: str) -> None:
        self._repopulate(text)

    def _update_ok_enabled(self) -> None:
        ok_btn = self.button_box.button(QDialogButtonBox.Ok)
        if ok_btn is not None:
            ok_btn.setEnabled(len(self.list_widget.selectedItems()) >= 2)

    def _on_accept(self) -> None:
        items = self.list_widget.selectedItems()
        if len(items) < 2:
            return
        # selectedItems() ordering is not guaranteed; restore list order.
        # QListWidgetItem is unhashable, so membership is an identity scan.
        selected_ids = {id(it) for it in items}
        self._selected_run_ids = [
            self.list_widget.item(i).data(_RUN_ID_ROLE)
            for i in range(self.list_widget.count())
            if id(self.list_widget.item(i)) in selected_ids
        ]
        self.accept()


def _matches(run: dict, needle_lower: str) -> bool:
    """Case-insensitive substring match on short_id or label."""
    short = (run.get("short_id") or "").lower()
    label = (run.get("label") or "").lower()
    milestone = (run.get("milestone") or "").lower()
    return (
        needle_lower in short
        or needle_lower in label
        or needle_lower in milestone
    )


def _format_row(run: dict) -> str:
    """One-line display text for a run row in the picker."""
    short = run.get("short_id") or (run.get("run_id") or "")[:8] or "?"
    ts = run.get("timestamp") or ""
    label = run.get("label")
    milestone = run.get("milestone")
    parts = [short, ts]
    if milestone:
        parts.append(f"[{milestone}]")
    if label:
        parts.append(label)
    return "  ·  ".join(parts)
