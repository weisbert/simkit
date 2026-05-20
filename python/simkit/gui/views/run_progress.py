"""Items-kanban widget for the bottom panel (Phase 4 spec §13).

Consumes JSONL events emitted by ``pvt run --gui-jsonl`` (spec §9.2):

* ``item_started``  — new item; appears in the list as "[ ] i/N  <name>  running"
* ``item_progress`` — running/completed/failed/total_corners; updates the row
* ``item_completed``— mark the row done; show pass/fail tally + run_id short
* ``log``           — left to the bottom-log panel; ignored here
* ``review_done``   — disable Cancel, header turns "done"
* ``error``         — surface message; disable Cancel

No bar charts, no ETAs — spec §13 explicitly prohibits "false precision".
"""

from __future__ import annotations

from typing import Any

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


# Status glyphs used in row labels. ASCII-only to keep the spec §18.3
# headless test runner happy on hosts without unicode-rich fonts.
_GLYPH_PENDING = "[ ]"
_GLYPH_RUNNING = "[~]"
_GLYPH_DONE = "[v]"
_GLYPH_FAIL = "[x]"


class RunProgressWidget(QWidget):
    """Right-hand items kanban + cancel button. Spec §13."""

    cancel_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("runProgressWidget")

        self._items: dict[int, dict[str, Any]] = {}
        self._total_items: int = 0
        self._review_name: str = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        header = QWidget(self, objectName="runProgressHeader")
        h = QHBoxLayout(header)
        h.setContentsMargins(0, 0, 0, 0)

        self.header_label = QLabel("Idle", header)
        self.header_label.setObjectName("runProgressHeaderLabel")
        h.addWidget(self.header_label, stretch=1)

        self.cancel_button = QPushButton("Cancel", header)
        self.cancel_button.setObjectName("cancelButton")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        h.addWidget(self.cancel_button)

        root.addWidget(header)

        self.items_list = QListWidget(self)
        self.items_list.setObjectName("itemsList")
        root.addWidget(self.items_list, stretch=1)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reset(self, review_name: str, total_items: int) -> None:
        """Clear state, show new review header, enable Cancel."""
        self._items = {}
        self._total_items = int(total_items)
        self._review_name = review_name or ""
        self.items_list.clear()
        self.header_label.setText(
            f"Running: {self._review_name}  (0 / {self._total_items} items)"
        )
        self.cancel_button.setEnabled(True)

    def set_running(self, running: bool) -> None:
        """Toggle Cancel-button enabled state directly."""
        self.cancel_button.setEnabled(bool(running))

    def mark_cancelled(self) -> None:
        """Run was cancelled by the user.

        Updates the header so it no longer reads "Running:" (which would
        otherwise persist and falsely imply the run is still going), and
        disables the Cancel button.
        """
        done = sum(
            1 for s in self._items.values()
            if s.get("status") in ("done", "fail")
        )
        self.header_label.setText(
            f"CANCELLED: {self._review_name}  "
            f"({done} / {self._total_items} items)"
        )
        self.cancel_button.setEnabled(False)

    def handle_event(self, event: dict) -> None:
        """Consume one JSONL event dict per spec §9.2."""
        kind = event.get("event") if isinstance(event, dict) else None
        if kind == "item_started":
            self._on_item_started(event)
        elif kind == "item_progress":
            self._on_item_progress(event)
        elif kind == "item_completed":
            self._on_item_completed(event)
        elif kind == "review_done":
            self._on_review_done(event)
        elif kind == "error":
            self._on_error(event)
        # "log" and unknown events are silently ignored — the bottom-log
        # panel owns "log" events, and unknowns shouldn't crash the GUI.

    # ------------------------------------------------------------------
    # Per-event handlers
    # ------------------------------------------------------------------

    def _on_item_started(self, event: dict) -> None:
        idx = int(event.get("item_index", 0))
        name = str(event.get("item_name", ""))
        total = int(event.get("total_items", self._total_items) or 0)
        if total > self._total_items:
            self._total_items = total
        state = self._items.setdefault(idx, {})
        state["name"] = name
        state["status"] = "running"
        state["running"] = 0
        state["completed"] = 0
        state["failed"] = 0
        state["total_corners"] = 0
        state["run_id_short"] = ""
        self._ensure_row(idx)
        self._refresh_row(idx)
        self._refresh_header()

    def _on_item_progress(self, event: dict) -> None:
        idx = int(event.get("item_index", 0))
        state = self._items.setdefault(idx, {})
        state.setdefault("status", "running")
        state.setdefault("name", "")
        for k in ("running", "completed", "failed", "total_corners"):
            if k in event:
                try:
                    state[k] = int(event[k])
                except (TypeError, ValueError):
                    pass
        self._ensure_row(idx)
        self._refresh_row(idx)

    def _on_item_completed(self, event: dict) -> None:
        idx = int(event.get("item_index", 0))
        state = self._items.setdefault(idx, {})
        for k in ("completed", "failed"):
            if k in event:
                try:
                    state[k] = int(event[k])
                except (TypeError, ValueError):
                    pass
        run_id = event.get("run_id")
        if isinstance(run_id, str) and run_id:
            state["run_id_short"] = run_id[:8]
        failed = int(state.get("failed", 0))
        state["status"] = "fail" if failed > 0 else "done"
        self._ensure_row(idx)
        self._refresh_row(idx)
        self._refresh_header()

    def _on_review_done(self, event: dict) -> None:
        exit_code = event.get("exit_code", 0)
        done_count = sum(
            1 for s in self._items.values()
            if s.get("status") in ("done", "fail")
        )
        verdict = "OK" if exit_code in (0, "0") else "FAIL"
        self.header_label.setText(
            f"{verdict}: {self._review_name}  "
            f"({done_count} / {self._total_items} items)"
        )
        self.cancel_button.setEnabled(False)

    def _on_error(self, event: dict) -> None:
        msg = event.get("msg", "")
        self.header_label.setText(
            f"ERROR: {self._review_name}  — {msg}"
        )
        self.cancel_button.setEnabled(False)

    # ------------------------------------------------------------------
    # Row helpers
    # ------------------------------------------------------------------

    def _ensure_row(self, idx: int) -> None:
        # Pad the list with blank rows up to idx so the row index always
        # matches the spec's 1-based item_index without needing a side dict.
        while self.items_list.count() < idx:
            self.items_list.addItem(QListWidgetItem(""))

    def _refresh_row(self, idx: int) -> None:
        state = self._items.get(idx)
        if state is None:
            return
        text = self._format_row(idx, state)
        row_index = idx - 1
        if 0 <= row_index < self.items_list.count():
            self.items_list.item(row_index).setText(text)

    def _format_row(self, idx: int, state: dict) -> str:
        status = state.get("status", "pending")
        if status == "running":
            glyph = _GLYPH_RUNNING
        elif status == "done":
            glyph = _GLYPH_DONE
        elif status == "fail":
            glyph = _GLYPH_FAIL
        else:
            glyph = _GLYPH_PENDING
        name = state.get("name", "")
        completed = int(state.get("completed", 0))
        failed = int(state.get("failed", 0))
        total = int(state.get("total_corners", 0))
        running = int(state.get("running", 0))
        tail_bits: list[str] = []
        if status == "running":
            if total:
                tail_bits.append(
                    f"running  {completed + failed}/{total} done, "
                    f"{running} active, {failed} fail"
                )
            else:
                tail_bits.append("running")
        elif status in ("done", "fail"):
            done_total = completed + failed
            tail_bits.append(f"completed  {completed}/{done_total} ok")
            if failed:
                tail_bits.append(f"{failed} fail")
            run_short = state.get("run_id_short")
            if run_short:
                tail_bits.append(f"run={run_short}")
        tail = "  ".join(tail_bits) if tail_bits else status
        return f"{glyph} {idx}/{self._total_items}  {name}  {tail}".rstrip()

    def _refresh_header(self) -> None:
        done = sum(
            1 for s in self._items.values()
            if s.get("status") in ("done", "fail")
        )
        self.header_label.setText(
            f"Running: {self._review_name}  "
            f"({done} / {self._total_items} items)"
        )
