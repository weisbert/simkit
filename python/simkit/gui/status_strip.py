"""Spec B1: cross-module 24h status strip.

Aggregates run activity across every recently-visited module (tracked
in :class:`simkit.gui.state.GuiAppState.recent_modules`) into a one-line
summary rendered at the top of the main window:

    Last 24h: X done / Y running / Z FAIL [chip-1] [chip-2] ...

Each FAIL chip is a clickable :class:`PyQt5.QtWidgets.QPushButton` that
emits :attr:`StatusStripWidget.fail_clicked` (``run_id``, ``project_id``)
so the main window can route the user to the failing review.

The query is pure DuckDB — opens each module's ``simkit.duckdb`` in
read-only mode, sums counts, and concatenates up to 8 chips. Missing /
unreadable / pre-v2 DBs are skipped silently (the user shouldn't see
errors from modules they barely touched).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from simkit.db import connect


MAX_FAIL_CHIPS = 8


@dataclass(frozen=True)
class FailChip:
    """One failing-run pill rendered in the status strip."""
    run_id: str
    project_id: str
    label: Optional[str]


@dataclass(frozen=True)
class Summary:
    """Aggregate counts + per-fail chips for the last-24h strip."""
    done: int
    running: int
    fail: int
    fail_chips: List[FailChip] = field(default_factory=list)


def last_24h_summary(
    db_paths: List[Path],
    *,
    running_count: int = 0,
    now: Optional[datetime] = None,
) -> Summary:
    """Aggregate ingest activity across multiple module DBs.

    For each existing DB, count runs with ``timestamp >= now-24h`` and
    classify each as a fail when its ``results`` table has any row with
    ``spec_status='fail'``. Aggregates sum across DBs; FAIL chips are
    capped at :data:`MAX_FAIL_CHIPS`, most recent first.

    ``running_count`` is supplied by the caller (the GUI knows whether
    its in-process :class:`RunController` is mid-run) — it is not
    derivable from the DB, which only sees ingested runs.

    ``now`` override is for tests; production callers should omit it
    to use ``datetime.now(timezone.utc)``.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=24)).isoformat()

    total_done = 0
    total_fail = 0
    chips: List[FailChip] = []

    for db_path in db_paths:
        if not db_path.is_file():
            continue
        try:
            con = connect(db_path, read_only=True)
        except Exception:
            # Corrupt / locked / pre-v2 DB — silently skip; the user
            # didn't open this module today and doesn't care.
            continue
        try:
            try:
                done = con.execute(
                    "SELECT COUNT(*) FROM runs WHERE timestamp >= ?",
                    [cutoff],
                ).fetchone()[0]
                total_done += int(done or 0)
            except Exception:
                # Pre-v1 DB without a runs table — skip rather than crash.
                continue

            try:
                fail_rows = con.execute(
                    """
                    SELECT runs.run_id, runs.project_id, runs.label
                    FROM runs
                    WHERE runs.timestamp >= ?
                      AND EXISTS (
                          SELECT 1 FROM results r
                          WHERE r.run_id = runs.run_id
                            AND r.spec_status = 'fail'
                      )
                    ORDER BY runs.timestamp DESC
                    """,
                    [cutoff],
                ).fetchall()
            except Exception:
                # Pre-v2 DB without spec_status column — no FAIL counts
                # available, but ``done`` was already counted.
                fail_rows = []

            total_fail += len(fail_rows)
            for run_id, project_id, label in fail_rows:
                if len(chips) >= MAX_FAIL_CHIPS:
                    break
                chips.append(
                    FailChip(
                        run_id=str(run_id),
                        project_id=str(project_id or ""),
                        label=(str(label) if label is not None else None),
                    )
                )
        finally:
            try:
                con.close()
            except Exception:
                pass

    return Summary(
        done=total_done,
        running=running_count,
        fail=total_fail,
        fail_chips=chips,
    )


class StatusStripWidget(QWidget):
    """Renders a :class:`Summary` as a one-line strip with FAIL chips.

    Emits :attr:`fail_clicked` ``(run_id, project_id)`` when the user
    clicks any FAIL chip.
    """

    fail_clicked = pyqtSignal(str, str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("statusStrip")
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(8, 2, 8, 4)
        self._layout.setSpacing(8)
        self._summary_label = QLabel("Last 24h: -")
        self._summary_label.setObjectName("statusStripSummary")
        self._layout.addWidget(self._summary_label)
        # Chips live in a sub-layout so we can clear+rebuild atomically.
        self._chips_container = QWidget()
        self._chips_layout = QHBoxLayout(self._chips_container)
        self._chips_layout.setContentsMargins(0, 0, 0, 0)
        self._chips_layout.setSpacing(4)
        self._layout.addWidget(self._chips_container)
        self._layout.addStretch(1)
        self._last_summary: Optional[Summary] = None

    def set_summary(self, summary: Summary) -> None:
        """Replace the rendered text + chips with ``summary``."""
        self._summary_label.setText(
            f"Last 24h: {summary.done} done / {summary.running} running / "
            f"{summary.fail} FAIL"
        )
        # Tear down old chip buttons before laying out new ones — otherwise
        # repeated refreshes accrete dead widgets that still receive paint
        # events.
        while self._chips_layout.count():
            item = self._chips_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        for chip in summary.fail_chips:
            text = chip.label or chip.run_id[:8]
            btn = QPushButton(text)
            btn.setObjectName("statusStripFailChip")
            btn.setStyleSheet(
                "QPushButton { background-color: #f8d7da; color: #721c24; "
                "border: 1px solid #f5c6cb; border-radius: 8px; "
                "padding: 1px 8px; }"
                "QPushButton:hover { background-color: #f1b0b7; }"
            )
            btn.setToolTip(
                f"FAIL — {chip.project_id}/{chip.run_id}\n"
                f"Click to open this failing run."
            )
            # Capture this chip by default-arg so the closure binds to
            # the loop-local instance, not the last iteration's.
            btn.clicked.connect(
                lambda _checked=False, c=chip: self.fail_clicked.emit(
                    c.run_id, c.project_id
                )
            )
            self._chips_layout.addWidget(btn)
        self._last_summary = summary

    def last_summary(self) -> Optional[Summary]:
        return self._last_summary

    def clear(self) -> None:
        self._summary_label.setText("Last 24h: -")
        while self._chips_layout.count():
            item = self._chips_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._last_summary = None
