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
from PyQt5.QtWidgets import QApplication, QDialogButtonBox  # noqa: E402

from simkit.db import bootstrap, connect  # noqa: E402
from simkit.gui.views.results_tab import ResultsTab, SetSpecDialog  # noqa: E402


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


class ResultsTabBaselinePinTests(unittest.TestCase):
    """Phase 4 Stage 3 additions: Baseline pin label + signal."""

    def test_baseline_label_default_text(self):
        tab = ResultsTab()
        self.assertEqual(tab.baseline_label.text(), "Baseline: —")

    def test_baseline_pinned_signal_exists_with_object_signature(self):
        tab = ResultsTab()
        sig = tab.baseline_pinned
        self.assertIsInstance(sig, pyqtBoundSignal)
        received: list[object] = []
        sig.connect(received.append)
        sig.emit("some-run-id")
        self.assertEqual(received, ["some-run-id"])

    def test_set_baseline_with_id_updates_label_and_emits(self):
        tab = ResultsTab()
        received: list[object] = []
        tab.baseline_pinned.connect(received.append)
        tab.set_baseline("aaaaaaaa-1234-abcd")
        self.assertIn("aaaaaaaa", tab.baseline_label.text())
        self.assertIn("★", tab.baseline_label.text())
        self.assertEqual(received, ["aaaaaaaa-1234-abcd"])

    def test_set_baseline_none_unpins_and_emits_none(self):
        tab = ResultsTab()
        tab.set_baseline("aaaaaaaa-1234-abcd")
        received: list[object] = []
        tab.baseline_pinned.connect(received.append)
        tab.set_baseline(None)
        self.assertEqual(tab.baseline_label.text(), "Baseline: —")
        self.assertEqual(received, [None])

    def test_baseline_run_id_round_trip(self):
        tab = ResultsTab()
        self.assertIsNone(tab.baseline_run_id())
        tab.set_baseline("xyz")
        self.assertEqual(tab.baseline_run_id(), "xyz")
        tab.set_baseline(None)
        self.assertIsNone(tab.baseline_run_id())

    def test_set_baseline_short_id_when_long(self):
        tab = ResultsTab()
        tab.set_baseline("aaaaaaaaXXXXX")
        # Should display first 8 chars after the star.
        self.assertIn("aaaaaaaa", tab.baseline_label.text())
        self.assertNotIn("XXXXX", tab.baseline_label.text())


class ResultsTabCompareButtonTests(unittest.TestCase):
    """Compare-to button: signal, enable rules, current_run_id tracking."""

    def test_compare_requested_signal_exists(self):
        tab = ResultsTab()
        self.assertIsInstance(tab.compare_requested, pyqtBoundSignal)

    def test_compare_button_disabled_until_set_run(self):
        tab = ResultsTab()
        self.assertFalse(tab.compare_button.isEnabled())

    def test_compare_button_emits_no_payload_on_click(self):
        tab = ResultsTab()
        # Manually enable; in production set_run() does it.
        tab.compare_button.setEnabled(True)
        received: list[None] = []
        tab.compare_requested.connect(lambda: received.append(None))
        tab.compare_button.click()
        self.assertEqual(received, [None])

    def test_set_run_enables_compare_and_remembers_id(self):
        tab = ResultsTab()
        con = _con_with_two_rows()
        try:
            tab.set_run("R1", con)
        finally:
            con.close()
        self.assertTrue(tab.compare_button.isEnabled())
        self.assertEqual(tab.current_run_id(), "R1")

    def test_current_run_id_default_none(self):
        tab = ResultsTab()
        self.assertIsNone(tab.current_run_id())

    def test_baseline_label_click_when_pinned_unpins(self):
        tab = ResultsTab()
        tab.set_baseline("aaa-bbb")
        received: list[object] = []
        tab.baseline_pinned.connect(received.append)
        # Simulate a click via the closure.
        tab._on_baseline_clicked(None)  # pylint: disable=protected-access
        self.assertIsNone(tab.baseline_run_id())
        self.assertEqual(received, [None])

    def test_baseline_label_click_when_unpinned_no_op(self):
        tab = ResultsTab()
        received: list[object] = []
        tab.baseline_pinned.connect(received.append)
        tab._on_baseline_clicked(None)  # pylint: disable=protected-access
        self.assertEqual(received, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


def test_set_review_path_not_runnable_keeps_run_disabled():
    """A review that fails to parse binds the path but must NOT enable
    the Run button — dispatching pvt run on it would just fail."""
    t = ResultsTab()
    t.set_review_path("/tmp/x.review.json", runnable=False)
    assert t.run_button.isEnabled() is False
    t.set_review_path("/tmp/x.review.json", runnable=True)
    assert t.run_button.isEnabled() is True


def test_show_review_summary_updates_header_and_clears_run():
    t = ResultsTab()
    t.show_review_summary("my_review", 4)
    assert "my_review" in t.header_label.text()
    assert t.current_run_id() is None


def test_show_review_summary_flags_parse_error():
    t = ResultsTab()
    t.show_review_summary("bad", 0, parse_error="invalid JSON")
    assert "解析失败" in t.header_label.text()


# --- G-1c: zero-spec hint strip ------------------------------------------


def _con_no_specs():
    """In-memory DuckDB with run 'R0' whose rows all carry NULL spec."""
    con = connect(":memory:")
    bootstrap(con)
    con.execute(
        """
        INSERT INTO runs(
          run_id, project_id, testbench_id, timestamp,
          author, history_name, schema_version, ingested_at
        ) VALUES
          ('R0', 'projA', 'tbA', TIMESTAMPTZ '2026-05-19 12:00:00+00',
           'tester', 'h0', 3, TIMESTAMPTZ '2026-05-19 12:00:00+00')
        """
    )
    con.execute(
        """
        INSERT INTO results(
          run_id, point, corner, test, output,
          value_num, value_str, status, sweep, corner_vars,
          test_note, spec, spec_status
        ) VALUES
          ('R0', 0, 'TT', 'pn', 'PN_1M',
           -125.0, NULL, 'pass', '{}', '{}', NULL, NULL, 'no_spec'),
          ('R0', 0, 'SS', 'pn', 'PN_1M',
           -95.0, NULL, 'pass', '{}', '{}', NULL, NULL, 'no_spec')
        """
    )
    return con


def test_no_spec_hint_shows_when_run_has_no_specs():
    t = ResultsTab()
    con = _con_no_specs()
    try:
        t.set_run("R0", con)
    finally:
        con.close()
    # isHidden() reflects the explicit flag regardless of an unshown parent.
    assert t.no_spec_hint.isHidden() is False


def test_no_spec_hint_hidden_when_run_has_specs():
    t = ResultsTab()
    con = _con_with_two_rows()
    try:
        t.set_run("R1", con)
    finally:
        con.close()
    assert t.no_spec_hint.isHidden() is True


def test_no_spec_hint_hidden_for_review_summary():
    t = ResultsTab()
    con = _con_no_specs()
    try:
        t.set_run("R0", con)
    finally:
        con.close()
    assert t.no_spec_hint.isHidden() is False
    # Selecting a review (no run) must drop the hint.
    t.show_review_summary("some_review", 2)
    assert t.no_spec_hint.isHidden() is True


# --- G-1b: set_spec_requested signal + SetSpecDialog ---------------------


def test_set_spec_requested_signal_exists():
    t = ResultsTab()
    received: list[tuple] = []
    t.set_spec_requested.connect(lambda o, s: received.append((o, s)))
    t.set_spec_requested.emit("PN_1M", ">= 20")
    assert received == [("PN_1M", ">= 20")]


def test_set_spec_dialog_accepts_valid_spec():
    d = SetSpecDialog("PN_1M", ">= 20")
    assert d.spec_text() == ">= 20"
    assert d._buttons.button(QDialogButtonBox.Ok).isEnabled() is True


def test_set_spec_dialog_blocks_ok_on_bad_spec():
    d = SetSpecDialog("PN_1M", "")
    d._edit.setText(">> garbage")
    assert d._buttons.button(QDialogButtonBox.Ok).isEnabled() is False


def test_set_spec_dialog_empty_is_allowed_as_clear():
    d = SetSpecDialog("PN_1M", ">= 20")
    d._edit.setText("")
    # Empty == "clear the spec"; OK must stay enabled.
    assert d._buttons.button(QDialogButtonBox.Ok).isEnabled() is True
    assert d.spec_text() == ""
