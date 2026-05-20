"""Widget tests for :class:`simkit.gui.views.summary_tab.SummaryTab`.

Headless: ``QT_QPA_PLATFORM=offscreen`` set before any PyQt5 import.
Exercises the health line, the margin rollup model, and set_run/clear.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "python"))

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtGui import QBrush  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from simkit.db import bootstrap, connect  # noqa: E402
from simkit.gui.run_summary import RunHealth  # noqa: E402
from simkit.gui.views.summary_tab import (  # noqa: E402
    MarginRollupModel,
    SummaryTab,
    health_line,
)

_QAPP = QApplication.instance() or QApplication(sys.argv)


def _con():
    """In-memory DB, run 'R1' (partial) with PN (fail) + gain (no_spec)."""
    con = connect(":memory:")
    bootstrap(con)
    con.execute(
        """
        INSERT INTO runs(
          run_id, project_id, testbench_id, timestamp,
          author, history_name, schema_version, ingested_at
        ) VALUES
          ('R1', 'p', 't', TIMESTAMPTZ '2026-05-19 12:00:00+00',
           'a', 'h', 4, TIMESTAMPTZ '2026-05-19 12:00:00+00')
        """
    )
    con.execute("UPDATE runs SET partial_run = TRUE WHERE run_id = 'R1'")
    con.execute(
        """
        INSERT INTO results(
          run_id, point, corner, test, output,
          value_num, value_str, status, sweep, corner_vars,
          test_note, spec, spec_status
        ) VALUES
          ('R1', 0, 'TT', 't', 'PN',
           25.0, NULL, 'ok', '{}', '{}', NULL, '>= 20', 'pass'),
          ('R1', 0, 'SS', 't', 'PN',
           15.0, NULL, 'ok', '{}', '{}', NULL, '>= 20', 'fail'),
          ('R1', 0, 'TT', 't', 'gain',
           5.0, NULL, 'ok', '{}', '{}', NULL, NULL, 'no_spec')
        """
    )
    return con


class HealthLineTests(unittest.TestCase):

    def test_health_line_lists_status_counts(self):
        h = RunHealth(
            total_rows=4,
            status_counts={"ok": 3, "eval_err": 1},
            sim_fail_corners=0,
            partial_run=False,
        )
        line = health_line(h)
        self.assertIn("4 行", line)
        self.assertIn("3 ok", line)
        self.assertIn("1 eval_err", line)

    def test_health_line_flags_partial_run(self):
        h = RunHealth(
            total_rows=1, status_counts={"ok": 1},
            sim_fail_corners=0, partial_run=True,
        )
        self.assertIn("部分运行", health_line(h))

    def test_health_line_flags_sim_failures(self):
        h = RunHealth(
            total_rows=2, status_counts={"ok": 2},
            sim_fail_corners=3, partial_run=False,
        )
        self.assertIn("3 角 sim 失败", health_line(h))


class MarginRollupModelTests(unittest.TestCase):

    def test_columns_and_row_count(self):
        con = _con()
        try:
            from simkit.gui.run_summary import margin_rollup
            model = MarginRollupModel(margin_rollup(con, "R1"))
        finally:
            con.close()
        self.assertEqual(model.columnCount(), 7)
        self.assertEqual(model.rowCount(), 2)  # PN, gain

    def test_fail_row_gets_red_brush(self):
        con = _con()
        try:
            from simkit.gui.run_summary import margin_rollup
            model = MarginRollupModel(margin_rollup(con, "R1"))
        finally:
            con.close()
        # Row 0 is PN (fail) — must carry a background brush.
        brush = model.data(model.index(0, 0), Qt.BackgroundRole)
        self.assertIsInstance(brush, QBrush)

    def test_display_role_renders_verdict(self):
        con = _con()
        try:
            from simkit.gui.run_summary import margin_rollup
            model = MarginRollupModel(margin_rollup(con, "R1"))
        finally:
            con.close()
        verdict_col = model.COLUMNS.index("判定")
        verdicts = {
            model.data(model.index(r, 0)): model.data(
                model.index(r, verdict_col)
            )
            for r in range(model.rowCount())
        }
        self.assertEqual(verdicts, {"PN": "fail", "gain": "no_spec"})


class SummaryTabTests(unittest.TestCase):

    def test_construction_does_not_raise(self):
        tab = SummaryTab()
        self.assertEqual(tab.health_label.text(), "(no run selected)")

    def test_set_run_populates_health_and_table(self):
        tab = SummaryTab()
        con = _con()
        try:
            tab.set_run("R1", con)
        finally:
            con.close()
        self.assertIn("部分运行", tab.health_label.text())
        self.assertEqual(tab._proxy.rowCount(), 2)

    def test_set_run_amber_styles_an_unhealthy_run(self):
        tab = SummaryTab()
        con = _con()
        try:
            tab.set_run("R1", con)
        finally:
            con.close()
        # partial_run → not clean → amber background styled.
        self.assertIn("background", tab.health_label.styleSheet())

    def test_clear_resets_table_and_label(self):
        tab = SummaryTab()
        con = _con()
        try:
            tab.set_run("R1", con)
        finally:
            con.close()
        tab.clear()
        self.assertEqual(tab.health_label.text(), "(no run selected)")
        self.assertEqual(tab._proxy.rowCount(), 0)
        self.assertEqual(tab.health_label.styleSheet(), "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
