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
    provenance_line,
    provenance_tooltip,
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


_PROV_JSON = (
    '{"host": "rhel7-farm-03", "captured_at": "2026-05-20T22:14:05+08:00", '
    '"pdk_version": "rf018_v1.9", '
    '"model_files": [{"path": "/pdk/rf018.scs", "exists": true, '
    '"size": 184320, "mtime": "2026-04-02T09:11:00+08:00"}]}'
)


class ProvenanceLineTests(unittest.TestCase):
    """G-5 — the run-condition provenance line."""

    def test_none_says_not_recorded(self):
        line = provenance_line(None)
        self.assertIn("未记录", line)

    def test_with_data_lists_host_and_pdk(self):
        prov = {
            "host": "farm-03", "pdk_version": "v1.9",
            "captured_at": "2026-05-20T22:14:05+08:00",
            "model_files": [{"path": "/m.scs"}],
        }
        line = provenance_line(prov)
        self.assertIn("farm-03", line)
        self.assertIn("v1.9", line)
        self.assertIn("1 个 model", line)

    def test_tooltip_lists_model_files(self):
        prov = {
            "host": "h", "model_files": [
                {"path": "/pdk/x.scs", "exists": True,
                 "size": 100, "mtime": "2026-01-01T00:00:00"},
            ],
        }
        self.assertIn("/pdk/x.scs", provenance_tooltip(prov))

    def test_tooltip_none_explains_risk(self):
        self.assertIn("无法证明", provenance_tooltip(None))


class SummaryTabProvenanceTests(unittest.TestCase):
    """G-5 — SummaryTab.set_run wires the provenance label."""

    def test_run_without_provenance_shows_amber_not_recorded(self):
        tab = SummaryTab()
        con = _con()
        try:
            tab.set_run("R1", con)
        finally:
            con.close()
        self.assertTrue(tab.provenance_label.isVisibleTo(tab))
        self.assertIn("未记录", tab.provenance_label.text())
        # Amber treatment when conditions are unknown.
        self.assertIn("fff3a3", tab.provenance_label.styleSheet())

    def test_run_with_provenance_shows_host(self):
        tab = SummaryTab()
        con = _con()
        try:
            con.execute(
                "UPDATE runs SET provenance = ? WHERE run_id = 'R1'",
                [_PROV_JSON],
            )
            tab.set_run("R1", con)
        finally:
            con.close()
        self.assertIn("rhel7-farm-03", tab.provenance_label.text())
        self.assertIn("rf018_v1.9", tab.provenance_label.text())
        # No amber when provenance is present.
        self.assertNotIn("fff3a3", tab.provenance_label.styleSheet())

    def test_clear_hides_provenance_label(self):
        tab = SummaryTab()
        con = _con()
        try:
            tab.set_run("R1", con)
        finally:
            con.close()
        tab.clear()
        self.assertFalse(tab.provenance_label.isVisibleTo(tab))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
