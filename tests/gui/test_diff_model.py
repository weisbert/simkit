"""Unit tests for :mod:`simkit.gui.diff_model`."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "python"))

from PyQt5.QtCore import QModelIndex, Qt  # noqa: E402
from PyQt5.QtGui import QBrush, QColor  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from simkit.diff import DiffRow  # noqa: E402
from simkit.gui.diff_model import DiffResultsModel  # noqa: E402


_QAPP = QApplication.instance() or QApplication(sys.argv)


def _row(**kw):
    """Construct a DiffRow with sane defaults for tests."""
    base = dict(
        test="pn",
        corner="TT",
        point=0,
        output="PN_1M",
        value_a=-125.0,
        value_b=-125.0,
        status_a="pass",
        status_b="pass",
        abs_delta=0.0,
        rel_delta=0.0,
        kind="match",
        is_sentinel=False,
        spec_a=None,
        spec_b=None,
        spec_status_a=None,
        spec_status_b=None,
    )
    base.update(kw)
    return DiffRow(**base)


class DiffResultsModelShapeTests(unittest.TestCase):
    """rowCount / columnCount / headerData / contract column list."""

    def test_empty_model_row_count_zero(self):
        m = DiffResultsModel([])
        self.assertEqual(m.rowCount(), 0)

    def test_none_rows_same_as_empty(self):
        m = DiffResultsModel(None)
        self.assertEqual(m.rowCount(), 0)

    def test_column_count_matches_columns_tuple(self):
        m = DiffResultsModel([])
        self.assertEqual(m.columnCount(), len(DiffResultsModel.COLUMNS))

    def test_columns_contract_order(self):
        # Pinned by spec — if this changes, callers/headerData/tests update.
        self.assertEqual(
            DiffResultsModel.COLUMNS,
            ("test", "corner", "output", "value_a", "value_b",
             "status_a", "status_b", "abs_delta", "rel_delta",
             "spec_a", "spec_b"),
        )

    def test_row_count_with_three_rows(self):
        m = DiffResultsModel([_row(), _row(corner="SS"), _row(corner="FF")])
        self.assertEqual(m.rowCount(), 3)

    def test_row_count_under_invalid_parent_is_zero(self):
        # Flat tables report 0 children for any non-root parent.
        m = DiffResultsModel([_row()])
        self.assertEqual(m.rowCount(m.index(0, 0)), 0)

    def test_column_count_under_invalid_parent_is_zero(self):
        m = DiffResultsModel([_row()])
        self.assertEqual(m.columnCount(m.index(0, 0)), 0)

    def test_header_data_horizontal(self):
        m = DiffResultsModel([])
        for i, name in enumerate(DiffResultsModel.COLUMNS):
            self.assertEqual(
                m.headerData(i, Qt.Horizontal, Qt.DisplayRole), name,
            )

    def test_header_data_vertical_is_one_based(self):
        m = DiffResultsModel([_row(), _row()])
        self.assertEqual(m.headerData(0, Qt.Vertical, Qt.DisplayRole), 1)
        self.assertEqual(m.headerData(1, Qt.Vertical, Qt.DisplayRole), 2)

    def test_header_data_wrong_role_returns_none(self):
        m = DiffResultsModel([])
        self.assertIsNone(m.headerData(0, Qt.Horizontal, Qt.EditRole))

    def test_header_data_out_of_range_returns_none(self):
        m = DiffResultsModel([])
        self.assertIsNone(
            m.headerData(99, Qt.Horizontal, Qt.DisplayRole),
        )


class DiffResultsModelDisplayTests(unittest.TestCase):
    """DisplayRole cell formatting."""

    def test_display_string_cell(self):
        m = DiffResultsModel([_row(test="my_test")])
        # column 0 = "test"
        self.assertEqual(
            m.data(m.index(0, 0), Qt.DisplayRole), "my_test",
        )

    def test_display_float_value_uses_g_format(self):
        m = DiffResultsModel([_row(value_a=-125.0)])
        # column 3 = "value_a"
        self.assertEqual(
            m.data(m.index(0, 3), Qt.DisplayRole), "-125",
        )

    def test_display_none_cell_renders_em_dash(self):
        m = DiffResultsModel([_row(spec_a=None, spec_b=None)])
        # column 9 = "spec_a"
        self.assertEqual(
            m.data(m.index(0, 9), Qt.DisplayRole), "—",
        )

    def test_display_int_point_field_omitted_from_columns(self):
        # Ensure "point" is not surfaced as a column (it's part of the
        # row key but not the GUI table contract).
        self.assertNotIn("point", DiffResultsModel.COLUMNS)

    def test_invalid_index_returns_none(self):
        m = DiffResultsModel([_row()])
        self.assertIsNone(m.data(QModelIndex(), Qt.DisplayRole))

    def test_out_of_range_row_returns_none(self):
        m = DiffResultsModel([_row()])
        # Build a fake-looking index by going off the end via index().
        self.assertIsNone(m.data(m.index(99, 0), Qt.DisplayRole))

    def test_out_of_range_col_returns_none(self):
        m = DiffResultsModel([_row()])
        self.assertIsNone(m.data(m.index(0, 99), Qt.DisplayRole))

    def test_unknown_role_returns_none(self):
        m = DiffResultsModel([_row()])
        self.assertIsNone(m.data(m.index(0, 0), Qt.ToolTipRole))


class DiffResultsModelBackgroundTests(unittest.TestCase):
    """BackgroundRole brushes — regression/recovery/value/unchanged."""

    def _bg(self, m, row=0, col=0):
        return m.data(m.index(row, col), Qt.BackgroundRole)

    def test_unchanged_row_has_no_brush(self):
        m = DiffResultsModel([_row()])
        self.assertIsNone(self._bg(m))

    def test_status_regression_pass_to_fail_red(self):
        m = DiffResultsModel([_row(status_a="pass", status_b="fail")])
        brush = self._bg(m)
        self.assertIsInstance(brush, QBrush)
        self.assertEqual(brush.color(), QColor(0xFF, 0xD0, 0xD0))

    def test_status_recovery_fail_to_pass_green(self):
        m = DiffResultsModel([_row(status_a="fail", status_b="pass")])
        brush = self._bg(m)
        self.assertIsInstance(brush, QBrush)
        self.assertEqual(brush.color(), QColor(0xD0, 0xFF, 0xD0))

    def test_value_only_change_yellow(self):
        m = DiffResultsModel([_row(value_a=1.0, value_b=2.0)])
        brush = self._bg(m)
        self.assertIsInstance(brush, QBrush)
        self.assertEqual(brush.color(), QColor(0xFF, 0xF5, 0xB3))

    def test_spec_verdict_flip_pass_to_fail_red(self):
        m = DiffResultsModel([_row(
            spec_status_a="pass", spec_status_b="fail",
        )])
        brush = self._bg(m)
        self.assertIsInstance(brush, QBrush)
        self.assertEqual(brush.color(), QColor(0xFF, 0xD0, 0xD0))

    def test_spec_verdict_flip_fail_to_pass_green(self):
        m = DiffResultsModel([_row(
            spec_status_a="fail", spec_status_b="pass",
        )])
        brush = self._bg(m)
        self.assertIsInstance(brush, QBrush)
        self.assertEqual(brush.color(), QColor(0xD0, 0xFF, 0xD0))

    def test_only_a_row_is_yellow(self):
        m = DiffResultsModel([_row(kind="only_a", value_b=None)])
        brush = self._bg(m)
        self.assertEqual(brush.color(), QColor(0xFF, 0xF5, 0xB3))

    def test_only_b_row_is_yellow(self):
        m = DiffResultsModel([_row(kind="only_b", value_a=None)])
        brush = self._bg(m)
        self.assertEqual(brush.color(), QColor(0xFF, 0xF5, 0xB3))

    def test_eval_err_status_treated_as_fail(self):
        # Going from pass to eval_err on the spec verdict = regression.
        m = DiffResultsModel([_row(
            spec_status_a="pass", spec_status_b="eval_err",
        )])
        brush = self._bg(m)
        self.assertEqual(brush.color(), QColor(0xFF, 0xD0, 0xD0))


class DiffResultsModelFilterPredicateTests(unittest.TestCase):
    """changed_only_filter / verdict_flipped_filter."""

    def test_changed_only_matches_changed_rows(self):
        m = DiffResultsModel([
            _row(),  # 0: identical
            _row(value_a=1.0, value_b=2.0),  # 1: value change
            _row(status_a="pass", status_b="fail"),  # 2: status flip
            _row(kind="only_a", value_b=None),  # 3: only-a
        ])
        self.assertFalse(m.changed_only_filter(0))
        self.assertTrue(m.changed_only_filter(1))
        self.assertTrue(m.changed_only_filter(2))
        self.assertTrue(m.changed_only_filter(3))

    def test_changed_only_handles_spec_string_change(self):
        m = DiffResultsModel([
            _row(spec_a="< -100", spec_b="< -90"),
        ])
        self.assertTrue(m.changed_only_filter(0))

    def test_changed_only_out_of_range_false(self):
        m = DiffResultsModel([_row()])
        self.assertFalse(m.changed_only_filter(99))

    def test_verdict_flipped_only_when_both_present_and_differ(self):
        m = DiffResultsModel([
            _row(),  # both None → False
            _row(spec_status_a="pass", spec_status_b="pass"),  # equal
            _row(spec_status_a="pass", spec_status_b="fail"),  # flip!
            _row(spec_status_a=None, spec_status_b="fail"),    # one None
        ])
        self.assertFalse(m.verdict_flipped_filter(0))
        self.assertFalse(m.verdict_flipped_filter(1))
        self.assertTrue(m.verdict_flipped_filter(2))
        self.assertFalse(m.verdict_flipped_filter(3))


class DiffResultsModelHelperTests(unittest.TestCase):
    """rows() copy + diff_row_at."""

    def test_rows_returns_a_defensive_copy(self):
        original = [_row()]
        m = DiffResultsModel(original)
        copied = m.rows()
        copied.append(_row(corner="ZZ"))
        self.assertEqual(m.rowCount(), 1)

    def test_diff_row_at_returns_row_object(self):
        r0 = _row(corner="SS")
        m = DiffResultsModel([r0])
        self.assertIs(m.diff_row_at(0), r0)

    def test_diff_row_at_out_of_range_returns_none(self):
        m = DiffResultsModel([_row()])
        self.assertIsNone(m.diff_row_at(99))
        self.assertIsNone(m.diff_row_at(-1))


def test_format_cell_collapses_negative_zero():
    """A zero-magnitude delta must render '0', never IEEE '-0'."""
    from simkit.gui.diff_model import _format_cell

    assert _format_cell(-0.0) == "0"
    assert _format_cell(0.0) == "0"


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
