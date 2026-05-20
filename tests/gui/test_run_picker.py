"""Unit tests for :class:`simkit.gui.views.run_picker.RunPickerDialog`."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "python"))

from PyQt5.QtWidgets import QApplication, QDialogButtonBox  # noqa: E402

from simkit.gui.views.run_picker import (  # noqa: E402
    MultiRunPickerDialog,
    RunPickerDialog,
)


_QAPP = QApplication.instance() or QApplication(sys.argv)


def _runs():
    """Three rows — exercises filter, ordering, and exclusion."""
    return [
        {"run_id": "aaaaaaaa-...", "short_id": "aaaaaaaa",
         "timestamp": "2026-05-19T12:00:00", "label": "golden"},
        {"run_id": "bbbbbbbb-...", "short_id": "bbbbbbbb",
         "timestamp": "2026-05-18T09:00:00", "label": None},
        {"run_id": "cccccccc-...", "short_id": "cccccccc",
         "timestamp": "2026-05-17T15:30:00", "label": "noisy_test"},
    ]


class RunPickerConstructionTests(unittest.TestCase):
    """Dialog builds + populates as expected."""

    def test_construction_populates_all_rows(self):
        dlg = RunPickerDialog(_runs())
        self.assertEqual(dlg.list_widget.count(), 3)
        self.assertFalse(dlg.list_widget.isHidden())

    def test_default_selected_run_id_is_none(self):
        dlg = RunPickerDialog(_runs())
        self.assertIsNone(dlg.selected_run_id)

    def test_title_default(self):
        dlg = RunPickerDialog(_runs())
        self.assertEqual(dlg.windowTitle(), "Compare against which run?")

    def test_title_override(self):
        dlg = RunPickerDialog(_runs(), title="Pick baseline")
        self.assertEqual(dlg.windowTitle(), "Pick baseline")

    def test_ok_button_disabled_by_default(self):
        dlg = RunPickerDialog(_runs())
        ok_btn = dlg.button_box.button(QDialogButtonBox.Ok)
        self.assertFalse(ok_btn.isEnabled())

    def test_empty_runs_shows_empty_label(self):
        dlg = RunPickerDialog([])
        self.assertEqual(dlg.list_widget.count(), 0)
        # The empty label should be visible (or at least active).
        self.assertFalse(dlg.empty_label.isHidden())
        self.assertIn("No other runs", dlg.empty_label.text())


class RunPickerExcludeCurrentTests(unittest.TestCase):
    """current_run_id is filtered out of the list."""

    def test_current_run_excluded(self):
        dlg = RunPickerDialog(_runs(), current_run_id="bbbbbbbb-...")
        # Only 2 of 3 rows remain.
        self.assertEqual(dlg.list_widget.count(), 2)
        texts = [
            dlg.list_widget.item(i).text() for i in range(dlg.list_widget.count())
        ]
        self.assertFalse(any("bbbbbbbb" in t for t in texts))

    def test_current_run_only_run_yields_empty(self):
        runs = [{"run_id": "X", "short_id": "X", "timestamp": "t", "label": None}]
        dlg = RunPickerDialog(runs, current_run_id="X")
        self.assertEqual(dlg.list_widget.count(), 0)
        self.assertFalse(dlg.empty_label.isHidden())


class RunPickerFilterTests(unittest.TestCase):
    """The QLineEdit filters the list case-insensitively."""

    def test_filter_by_short_id_substring(self):
        dlg = RunPickerDialog(_runs())
        dlg.filter_edit.setText("aaaa")
        self.assertEqual(dlg.list_widget.count(), 1)
        self.assertIn("aaaaaaaa", dlg.list_widget.item(0).text())

    def test_filter_by_label_substring(self):
        dlg = RunPickerDialog(_runs())
        dlg.filter_edit.setText("golden")
        self.assertEqual(dlg.list_widget.count(), 1)
        self.assertIn("golden", dlg.list_widget.item(0).text())

    def test_filter_case_insensitive(self):
        dlg = RunPickerDialog(_runs())
        dlg.filter_edit.setText("GOLDEN")
        self.assertEqual(dlg.list_widget.count(), 1)

    def test_filter_no_matches_shows_empty_label(self):
        dlg = RunPickerDialog(_runs())
        dlg.filter_edit.setText("zzzzzzzz")
        self.assertEqual(dlg.list_widget.count(), 0)
        self.assertFalse(dlg.empty_label.isHidden())
        self.assertIn("No runs match", dlg.empty_label.text())

    def test_filter_cleared_restores_all(self):
        dlg = RunPickerDialog(_runs())
        dlg.filter_edit.setText("aaaa")
        self.assertEqual(dlg.list_widget.count(), 1)
        dlg.filter_edit.setText("")
        self.assertEqual(dlg.list_widget.count(), 3)


class RunPickerSelectionTests(unittest.TestCase):
    """Selection enables OK + selected_run_id round-trips on accept."""

    def test_selecting_a_row_enables_ok(self):
        dlg = RunPickerDialog(_runs())
        dlg.list_widget.setCurrentRow(0)
        ok_btn = dlg.button_box.button(QDialogButtonBox.Ok)
        self.assertTrue(ok_btn.isEnabled())

    def test_accept_sets_selected_run_id(self):
        dlg = RunPickerDialog(_runs())
        dlg.list_widget.setCurrentRow(0)
        dlg._on_accept()  # pylint: disable=protected-access
        self.assertEqual(dlg.selected_run_id, "aaaaaaaa-...")

    def test_accept_with_no_selection_does_nothing(self):
        dlg = RunPickerDialog(_runs())
        # No row picked.
        dlg._on_accept()  # pylint: disable=protected-access
        self.assertIsNone(dlg.selected_run_id)

    def test_double_click_accepts_with_selected_row(self):
        dlg = RunPickerDialog(_runs())
        item = dlg.list_widget.item(1)
        dlg.list_widget.setCurrentRow(1)
        dlg._on_item_double_clicked(item)  # pylint: disable=protected-access
        self.assertEqual(dlg.selected_run_id, "bbbbbbbb-...")


class RunPickerFormatTests(unittest.TestCase):
    """Display text shows short_id, ts, and label when present."""

    def test_row_with_label_shows_three_parts(self):
        dlg = RunPickerDialog(_runs())
        text = dlg.list_widget.item(0).text()
        self.assertIn("aaaaaaaa", text)
        self.assertIn("2026-05-19", text)
        self.assertIn("golden", text)

    def test_row_without_label_shows_two_parts(self):
        dlg = RunPickerDialog(_runs())
        # Find the "bbbbbbbb" row.
        for i in range(dlg.list_widget.count()):
            text = dlg.list_widget.item(i).text()
            if "bbbbbbbb" in text:
                self.assertIn("2026-05-18", text)
                # No label suffix.
                self.assertNotIn("None", text)
                break
        else:
            self.fail("row not found")


class MultiRunPickerTests(unittest.TestCase):
    """MultiRunPickerDialog — the G-6 trend chooser."""

    def test_construction_populates_all_rows(self):
        dlg = MultiRunPickerDialog(_runs())
        self.assertEqual(dlg.list_widget.count(), 3)

    def test_ok_disabled_until_two_selected(self):
        dlg = MultiRunPickerDialog(_runs())
        ok = dlg.button_box.button(QDialogButtonBox.Ok)
        self.assertFalse(ok.isEnabled())
        dlg.list_widget.item(0).setSelected(True)
        dlg._update_ok_enabled()
        self.assertFalse(ok.isEnabled())
        dlg.list_widget.item(1).setSelected(True)
        dlg._update_ok_enabled()
        self.assertTrue(ok.isEnabled())

    def test_accept_returns_selection_in_list_order(self):
        dlg = MultiRunPickerDialog(_runs())
        # Select rows 2 then 0 — result must follow list order, not click order.
        dlg.list_widget.item(2).setSelected(True)
        dlg.list_widget.item(0).setSelected(True)
        dlg._on_accept()
        self.assertEqual(
            dlg.selected_run_ids, ["aaaaaaaa-...", "cccccccc-..."],
        )

    def test_milestone_shown_in_row_text(self):
        runs = [
            {"run_id": "x", "short_id": "xxxx", "timestamp": "2026-05-01",
             "label": None, "milestone": "PDR"},
            {"run_id": "y", "short_id": "yyyy", "timestamp": "2026-05-02",
             "label": None, "milestone": None},
        ]
        dlg = MultiRunPickerDialog(runs)
        self.assertIn("[PDR]", dlg.list_widget.item(0).text())

    def test_filter_matches_milestone(self):
        runs = [
            {"run_id": "x", "short_id": "xxxx", "timestamp": "t",
             "label": None, "milestone": "PDR"},
            {"run_id": "y", "short_id": "yyyy", "timestamp": "t",
             "label": None, "milestone": "CDR"},
        ]
        dlg = MultiRunPickerDialog(runs)
        dlg.filter_edit.setText("pdr")
        self.assertEqual(dlg.list_widget.count(), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
