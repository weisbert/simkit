"""Smoke tests for :class:`simkit.gui.views.results_tab.ResultsTab`.

Stage-2 wiring assertions only — no widget-rendering / pixel-level
checks. We exercise:

* construction (no crash, signal exists with the right signature),
* :meth:`set_run` populates the table via DuckDB,
* :meth:`set_review_path` toggles the "Run this review" button.

Headless: ``QT_QPA_PLATFORM=offscreen`` set before any PyQt5 import.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "python"))

from PyQt5.QtCore import pyqtBoundSignal  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from simkit.db import bootstrap, connect  # noqa: E402
from simkit.gui.views.results_tab import ResultsTab  # noqa: E402


# Re-use existing QApplication when running with the model tests.
_QAPP = QApplication.instance() or QApplication(sys.argv)


def _con_with_two_rows():
    """Small in-memory DuckDB pre-loaded with 2 rows under run_id='R1'."""
    con = connect(":memory:")
    bootstrap(con)
    con.execute(
        """
        INSERT INTO runs(
          run_id, project_id, testbench_id, timestamp,
          author, history_name, schema_version, ingested_at
        ) VALUES
          ('R1', 'projA', 'tbA', TIMESTAMPTZ '2026-05-19 12:00:00+00',
           'tester', 'h1', 3, TIMESTAMPTZ '2026-05-19 12:00:00+00')
        """
    )
    con.execute(
        """
        INSERT INTO results(
          run_id, point, corner, test, output,
          value_num, value_str, status, sweep, corner_vars,
          test_note, spec, spec_status
        ) VALUES
          ('R1', 0, 'TT', 'pn', 'PN_1M',
           -125.0, NULL, 'pass', '{}', '{}',
           NULL, '< -100', 'pass'),
          ('R1', 0, 'SS', 'pn', 'PN_1M',
           -95.0, NULL, 'fail', '{}', '{}',
           NULL, '< -100', 'fail')
        """
    )
    return con


class ResultsTabConstructionTests(unittest.TestCase):
    """ResultsTab can be instantiated headless and exposes the spec'd API."""

    def test_construction_does_not_raise(self):
        tab = ResultsTab()
        self.assertIsNotNone(tab)
        # Defaults: empty header, no rows.
        # set_header has not been called; placeholder should be visible.
        self.assertEqual(tab.header_label.text(), "(no run selected)")

    def test_run_requested_signal_exists_with_str_signature(self):
        tab = ResultsTab()
        # Class-level signal -> bound signal on instance.
        sig = tab.run_requested
        self.assertIsInstance(sig, pyqtBoundSignal)
        # Smoke: connect a Python callable + emit a sample path; the
        # connected slot must observe it. This exercises the (str,)
        # signature implicitly — emit("foo") on a wrong-signature signal
        # would raise TypeError.
        received: list[str] = []
        sig.connect(received.append)
        sig.emit("/abs/path/to/a.review.json")
        self.assertEqual(received, ["/abs/path/to/a.review.json"])

    def test_run_button_disabled_by_default(self):
        tab = ResultsTab()
        self.assertFalse(tab.run_button.isEnabled())

    def test_set_review_path_enables_run_button(self):
        tab = ResultsTab()
        tab.set_review_path("/abs/some.review.json")
        self.assertTrue(tab.run_button.isEnabled())

    def test_set_review_path_none_disables_run_button(self):
        tab = ResultsTab()
        tab.set_review_path("/abs/some.review.json")
        self.assertTrue(tab.run_button.isEnabled())
        tab.set_review_path(None)
        self.assertFalse(tab.run_button.isEnabled())

    def test_set_review_path_empty_string_disables_run_button(self):
        tab = ResultsTab()
        tab.set_review_path("")
        self.assertFalse(tab.run_button.isEnabled())


class ResultsTabRunWiringTests(unittest.TestCase):
    """set_run populates the table; clicking the button emits the signal."""

    def test_set_run_populates_table_via_proxy(self):
        tab = ResultsTab()
        con = _con_with_two_rows()
        try:
            tab.set_run("R1", con)
        finally:
            con.close()
        # Proxy now wraps a real model with 2 rows × 7 columns.
        proxy = tab.table.model()
        self.assertEqual(proxy.rowCount(), 2)
        self.assertEqual(proxy.columnCount(), 7)

    def test_set_run_with_unknown_run_id_clears_to_empty(self):
        tab = ResultsTab()
        con = _con_with_two_rows()
        try:
            tab.set_run("does_not_exist", con)
        finally:
            con.close()
        proxy = tab.table.model()
        self.assertEqual(proxy.rowCount(), 0)

    def test_run_button_click_emits_run_requested_with_bound_path(self):
        tab = ResultsTab()
        tab.set_review_path("/abs/some.review.json")
        received: list[str] = []
        tab.run_requested.connect(received.append)
        # Programmatic click — same code path as a user click.
        tab.run_button.click()
        self.assertEqual(received, ["/abs/some.review.json"])

    def test_set_header_renders_summary(self):
        tab = ResultsTab()
        tab.set_header(
            history_name="pn_review_v3__1",
            project_id="NDIV",
            testbench_id="tb_pn",
            timestamp="2026-05-19T12:00:00",
            milestone="CDR-2026q2",
        )
        text = tab.header_label.text()
        # Each field appears somewhere in the rendered header.
        self.assertIn("pn_review_v3__1", text)
        self.assertIn("NDIV", text)
        self.assertIn("tb_pn", text)
        self.assertIn("2026-05-19T12:00:00", text)
        self.assertIn("CDR-2026q2", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
