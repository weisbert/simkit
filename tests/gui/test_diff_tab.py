"""Unit tests for :class:`simkit.gui.views.diff_tab.DiffTab`."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "python"))

from PyQt5.QtCore import pyqtBoundSignal  # noqa: E402
from PyQt5.QtWidgets import QApplication, QPlainTextEdit, QTableView  # noqa: E402

from simkit.diff import DiffResult, DiffRow, NetlistDiff  # noqa: E402
from simkit.gui.views.diff_tab import DiffTab  # noqa: E402


_QAPP = QApplication.instance() or QApplication(sys.argv)


def _drow(**kw):
    base = dict(
        test="pn", corner="TT", point=0, output="PN_1M",
        value_a=-125.0, value_b=-125.0,
        status_a="pass", status_b="pass",
        abs_delta=0.0, rel_delta=0.0,
        kind="match", is_sentinel=False,
        spec_a=None, spec_b=None,
        spec_status_a=None, spec_status_b=None,
    )
    base.update(kw)
    return DiffRow(**base)


def _result(rows=None, netlist=None):
    rows = rows if rows is not None else [_drow()]
    netlist = netlist if netlist is not None else NetlistDiff(
        a_path="input.scs", b_path="input.scs",
        diff_text="", note=None,
    )
    return DiffResult(
        slice_a_run_id="aaaaaaaa-1234-1234-1234-123456789012",
        slice_b_run_id="bbbbbbbb-1234-1234-1234-123456789012",
        slice_a_identifier="aaaaaaaa",
        slice_b_identifier="bbbbbbbb",
        rows=rows,
        netlist=netlist,
    )


class DiffTabConstructionTests(unittest.TestCase):
    """DiffTab builds with the three sub-tabs and the right title."""

    def test_construction_does_not_raise(self):
        tab = DiffTab(_result())
        self.assertIsNotNone(tab)

    def test_title_uses_short_ids(self):
        tab = DiffTab(_result())
        self.assertEqual(tab.title, "Diff: aaaaaaaa vs bbbbbbbb")

    def test_three_sub_tabs(self):
        tab = DiffTab(_result())
        self.assertEqual(tab.tabs.count(), 3)
        labels = [tab.tabs.tabText(i) for i in range(tab.tabs.count())]
        self.assertEqual(labels, ["Spec delta", "Netlist delta", "Spec-string delta"])

    def test_closed_signal_exists(self):
        tab = DiffTab(_result())
        self.assertIsInstance(tab.closed, pyqtBoundSignal)

    def test_close_button_emits_closed(self):
        tab = DiffTab(_result())
        seen: list[bool] = []
        tab.closed.connect(lambda: seen.append(True))
        tab.close_button.click()
        self.assertEqual(seen, [True])

    def test_diff_result_property(self):
        r = _result()
        tab = DiffTab(r)
        self.assertIs(tab.diff_result, r)


class DiffTabSpecDeltaTests(unittest.TestCase):
    """Spec delta sub-tab: model + proxy + filter combo."""

    def test_results_proxy_starts_with_all_rows(self):
        tab = DiffTab(_result([_drow(), _drow(value_a=1.0, value_b=2.0)]))
        self.assertEqual(tab.results_proxy.rowCount(), 2)

    def test_filter_changed_hides_unchanged(self):
        tab = DiffTab(_result([
            _drow(),  # unchanged
            _drow(value_a=1.0, value_b=2.0),  # changed
        ]))
        tab.filter_combo.setCurrentText("Show only changed")
        self.assertEqual(tab.results_proxy.rowCount(), 1)

    def test_filter_verdict_flipped_only_shows_spec_flips(self):
        tab = DiffTab(_result([
            _drow(value_a=1.0, value_b=2.0),  # value only, no spec
            _drow(spec_status_a="pass", spec_status_b="fail"),  # flip
        ]))
        tab.filter_combo.setCurrentText("Show only verdict-flipped")
        self.assertEqual(tab.results_proxy.rowCount(), 1)

    def test_filter_all_restores_full_count(self):
        tab = DiffTab(_result([_drow(), _drow(value_a=1.0, value_b=2.0)]))
        tab.filter_combo.setCurrentText("Show only changed")
        self.assertEqual(tab.results_proxy.rowCount(), 1)
        tab.filter_combo.setCurrentText("All rows")
        self.assertEqual(tab.results_proxy.rowCount(), 2)

    def test_results_view_is_a_qtableview(self):
        tab = DiffTab(_result())
        self.assertIsInstance(tab.results_view, QTableView)

    def test_filter_combo_options(self):
        tab = DiffTab(_result())
        items = [tab.filter_combo.itemText(i)
                 for i in range(tab.filter_combo.count())]
        self.assertEqual(items, [
            "All rows", "Show only changed", "Show only verdict-flipped",
        ])


class DiffTabNetlistTests(unittest.TestCase):
    """Netlist sub-tab: monospace + note label + identical fallback."""

    def test_netlist_view_is_plaintextedit_readonly(self):
        tab = DiffTab(_result())
        self.assertIsInstance(tab.netlist_view, QPlainTextEdit)
        self.assertTrue(tab.netlist_view.isReadOnly())

    def test_empty_diff_text_shows_identical_message(self):
        tab = DiffTab(_result(netlist=NetlistDiff(
            a_path="x.scs", b_path="x.scs", diff_text="", note=None,
        )))
        self.assertIn("identical", tab.netlist_view.toPlainText())

    def test_diff_text_rendered_verbatim(self):
        tab = DiffTab(_result(netlist=NetlistDiff(
            a_path="x.scs", b_path="x.scs",
            diff_text="--- a\n+++ b\n@@ -1 +1 @@\n-foo\n+bar\n",
            note=None,
        )))
        self.assertIn("@@ -1 +1 @@", tab.netlist_view.toPlainText())

    def test_none_diff_text_blank_view(self):
        tab = DiffTab(_result(netlist=NetlistDiff(
            a_path=None, b_path=None, diff_text=None,
            note="both null",
        )))
        self.assertEqual(tab.netlist_view.toPlainText(), "")

    def test_note_label_shown_when_present(self):
        tab = DiffTab(_result(netlist=NetlistDiff(
            a_path=None, b_path="x.scs", diff_text=None,
            note="slice_a has null netlist_path",
        )))
        # isHidden() is False when show() has been called and never hidden;
        # isVisibleTo(parent) requires the parent chain to be visible too,
        # which doesn't happen in offscreen tests without an exec_().
        self.assertFalse(tab.netlist_note_label.isHidden())
        self.assertEqual(
            tab.netlist_note_label.text(),
            "slice_a has null netlist_path",
        )

    def test_note_label_hidden_when_absent(self):
        tab = DiffTab(_result())
        self.assertTrue(tab.netlist_note_label.isHidden())


class DiffTabSpecStringTests(unittest.TestCase):
    """Spec-string sub-tab — empty case + populated case."""

    def test_no_spec_changes_renders_empty_label(self):
        tab = DiffTab(_result([_drow()]))
        # spec_string_view stays None when there are no changes
        self.assertIsNone(tab.spec_string_view)
        self.assertEqual(tab.spec_string_model.rowCount(), 0)

    def test_spec_string_only_changed_rows_included(self):
        rows = [
            _drow(spec_a="< -100", spec_b="< -100"),   # unchanged
            _drow(spec_a="< -100", spec_b="< -90"),    # changed
            _drow(spec_a=None,      spec_b="< -90"),   # added
        ]
        tab = DiffTab(_result(rows))
        # Two rows where spec_a != spec_b.
        self.assertEqual(tab.spec_string_model.rowCount(), 2)
        self.assertIsInstance(tab.spec_string_view, QTableView)

    def test_spec_string_model_columns(self):
        rows = [_drow(spec_a="< -100", spec_b="< -90")]
        tab = DiffTab(_result(rows))
        self.assertEqual(
            tab.spec_string_model.COLUMNS,
            ("test", "output", "spec_a", "spec_b"),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
