"""Tests for the spec B1 cross-module 24h status strip."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


# Headless Qt — set BEFORE PyQt5 import (mirrors test_run_controller.py).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "python"))


pytest.importorskip("PyQt5")


from simkit.db import connect  # noqa: E402
from simkit.gui.status_strip import (  # noqa: E402
    FailChip,
    StatusStripWidget,
    Summary,
    last_24h_summary,
)


# --- last_24h_summary -------------------------------------------------------


def _make_minimal_db(path: Path) -> None:
    """Build the smallest DuckDB shape the summary query knows: a ``runs``
    table with a TIMESTAMPTZ ``timestamp`` and a ``results`` table with
    ``spec_status``.
    """
    con = connect(path)
    try:
        con.execute(
            "CREATE TABLE runs ("
            "  run_id VARCHAR, project_id VARCHAR, "
            "  testbench_id VARCHAR, testbench_alias VARCHAR, "
            "  timestamp TIMESTAMPTZ, author VARCHAR, "
            "  label VARCHAR, note VARCHAR, netlist_path VARCHAR, "
            "  history_name VARCHAR, schema_version INT, "
            "  ingested_at TIMESTAMPTZ, starred BOOLEAN DEFAULT FALSE"
            ")"
        )
        con.execute(
            "CREATE TABLE results ("
            "  run_id VARCHAR, spec_status VARCHAR"
            ")"
        )
    finally:
        con.close()


def _insert_run(
    path: Path,
    *,
    run_id: str,
    project_id: str = "p1",
    ts: datetime,
    label: str | None = None,
    fail_count: int = 0,
    pass_count: int = 0,
) -> None:
    con = connect(path)
    try:
        con.execute(
            "INSERT INTO runs VALUES "
            "(?, ?, 'tb', NULL, ?, 'me', ?, NULL, NULL, ?, 2, ?, FALSE)",
            [run_id, project_id, ts, label, run_id, ts],
        )
        for _ in range(fail_count):
            con.execute(
                "INSERT INTO results VALUES (?, 'fail')", [run_id]
            )
        for _ in range(pass_count):
            con.execute(
                "INSERT INTO results VALUES (?, 'pass')", [run_id]
            )
    finally:
        con.close()


def test_last_24h_summary_empty_when_no_dbs():
    summary = last_24h_summary([])
    assert summary.done == 0
    assert summary.running == 0
    assert summary.fail == 0
    assert summary.fail_chips == []


def test_last_24h_summary_skips_missing_path(tmp_path):
    summary = last_24h_summary([tmp_path / "does-not-exist.duckdb"])
    assert summary.done == 0


def test_last_24h_summary_counts_recent_runs(tmp_path):
    db = tmp_path / "m.duckdb"
    _make_minimal_db(db)
    now = datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)
    # Two runs in last 24h.
    _insert_run(db, run_id="r1", ts=now - timedelta(hours=2), pass_count=3)
    _insert_run(db, run_id="r2", ts=now - timedelta(hours=20))
    # One run older than 24h — must NOT be counted.
    _insert_run(db, run_id="r3", ts=now - timedelta(hours=48))
    summary = last_24h_summary([db], now=now)
    assert summary.done == 2
    assert summary.fail == 0


def test_last_24h_summary_aggregates_fail_chips(tmp_path):
    db = tmp_path / "m.duckdb"
    _make_minimal_db(db)
    now = datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)
    _insert_run(
        db, run_id="bad-1", ts=now - timedelta(hours=1),
        label="PN check CDR", fail_count=4,
    )
    _insert_run(
        db, run_id="ok-1", ts=now - timedelta(hours=2), pass_count=3,
    )
    _insert_run(
        db, run_id="bad-2", ts=now - timedelta(hours=5),
        fail_count=1, pass_count=1,
    )
    summary = last_24h_summary([db], now=now)
    assert summary.done == 3
    assert summary.fail == 2
    chip_ids = [c.run_id for c in summary.fail_chips]
    # Order is most-recent first within a single DB.
    assert chip_ids == ["bad-1", "bad-2"]
    # Labels carry through; missing label falls back at render time.
    assert summary.fail_chips[0].label == "PN check CDR"
    assert summary.fail_chips[1].label is None


def test_last_24h_summary_unions_across_dbs(tmp_path):
    db_a = tmp_path / "a.duckdb"
    db_b = tmp_path / "b.duckdb"
    _make_minimal_db(db_a)
    _make_minimal_db(db_b)
    now = datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)
    _insert_run(db_a, run_id="a1", project_id="proj_a",
                ts=now - timedelta(hours=1), pass_count=2)
    _insert_run(db_a, run_id="a2", project_id="proj_a",
                ts=now - timedelta(hours=3), fail_count=1)
    _insert_run(db_b, run_id="b1", project_id="proj_b",
                ts=now - timedelta(hours=2), fail_count=2)

    summary = last_24h_summary([db_a, db_b], now=now)
    assert summary.done == 3
    assert summary.fail == 2
    projects = {c.project_id for c in summary.fail_chips}
    assert projects == {"proj_a", "proj_b"}


def test_last_24h_summary_caps_chips_at_eight(tmp_path):
    db = tmp_path / "m.duckdb"
    _make_minimal_db(db)
    now = datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)
    for i in range(12):
        _insert_run(
            db, run_id=f"bad-{i}",
            ts=now - timedelta(hours=1, minutes=i),
            fail_count=1,
        )
    summary = last_24h_summary([db], now=now)
    assert summary.fail == 12  # count is uncapped
    assert len(summary.fail_chips) == 8  # chips ARE capped


def test_last_24h_summary_running_count_passthrough(tmp_path):
    db = tmp_path / "m.duckdb"
    _make_minimal_db(db)
    summary = last_24h_summary([db], running_count=1)
    assert summary.running == 1


# --- StatusStripWidget ------------------------------------------------------


def test_status_strip_widget_renders_counts(qtbot):
    w = StatusStripWidget()
    qtbot.addWidget(w)
    w.set_summary(Summary(done=7, running=1, fail=2))
    assert "7 done" in w._summary_label.text()
    assert "1 running" in w._summary_label.text()
    assert "2 FAIL" in w._summary_label.text()


def test_status_strip_widget_renders_fail_chips(qtbot):
    w = StatusStripWidget()
    qtbot.addWidget(w)
    s = Summary(
        done=2, running=0, fail=2,
        fail_chips=[
            FailChip(run_id="abc12345xyz", project_id="p", label="PN_CDR"),
            FailChip(run_id="def67890xyz", project_id="p", label=None),
        ],
    )
    w.set_summary(s)
    assert w._chips_layout.count() == 2
    # Chip text falls back to run_id[:8] when label is missing.
    chip2_text = w._chips_layout.itemAt(1).widget().text()
    assert chip2_text == "def67890"


def test_status_strip_widget_chip_click_emits_fail_clicked(qtbot):
    w = StatusStripWidget()
    qtbot.addWidget(w)
    s = Summary(
        done=1, running=0, fail=1,
        fail_chips=[FailChip(run_id="xyz123", project_id="my_proj", label="L")],
    )
    w.set_summary(s)
    with qtbot.waitSignal(w.fail_clicked, timeout=500) as block:
        w._chips_layout.itemAt(0).widget().click()
    assert block.args == ["xyz123", "my_proj"]


def test_status_strip_widget_refresh_clears_old_chips(qtbot):
    w = StatusStripWidget()
    qtbot.addWidget(w)
    w.set_summary(Summary(
        done=1, running=0, fail=1,
        fail_chips=[FailChip(run_id="a", project_id="p", label=None)],
    ))
    assert w._chips_layout.count() == 1
    # Subsequent refresh with zero chips must not leave the old one behind.
    w.set_summary(Summary(done=5, running=0, fail=0))
    # deleteLater() is async — pump events so we observe the real count.
    qtbot.wait(10)
    assert w._chips_layout.count() == 0


# --- MainWindow integration -------------------------------------------------


def test_main_window_uses_status_strip_widget(qtbot):
    from simkit.gui.main_window import MainWindow

    w = MainWindow()
    qtbot.addWidget(w)
    assert isinstance(w.status_strip, StatusStripWidget)


def test_main_window_refresh_status_strip_calls_aggregate(qtbot, tmp_path):
    from simkit.gui.main_window import MainWindow

    db = tmp_path / "m.duckdb"
    _make_minimal_db(db)
    now = datetime.now(timezone.utc)
    _insert_run(db, run_id="r1", ts=now - timedelta(hours=1), pass_count=1)

    w = MainWindow()
    qtbot.addWidget(w)
    w.set_status_strip_paths_provider(lambda: [db])
    # set_status_strip_paths_provider triggers an immediate refresh.
    summary = w.status_strip.last_summary()
    assert summary is not None
    assert summary.done == 1


def test_main_window_refresh_safe_without_provider(qtbot):
    from simkit.gui.main_window import MainWindow

    w = MainWindow()
    qtbot.addWidget(w)
    # Default: no provider wired. Must not raise.
    w.refresh_status_strip()
    # Strip stays in its placeholder ("Last 24h: -") — no Summary yet.
    assert w.status_strip.last_summary() is None
