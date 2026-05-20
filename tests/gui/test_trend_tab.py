"""Unit tests for the G-6 trend GUI: TrendTableModel + TrendTab."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "python"))

from PyQt5.QtCore import Qt, pyqtBoundSignal  # noqa: E402
from PyQt5.QtGui import QBrush  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from simkit.gui.trend_model import TrendTableModel  # noqa: E402
from simkit.gui.views.trend_tab import TrendTab  # noqa: E402
from simkit.trend import (  # noqa: E402
    TrendCell,
    TrendColumn,
    TrendResult,
    TrendRow,
)

_QAPP = QApplication.instance() or QApplication(sys.argv)


def _col(display, run_id="rrrrrrrr-0000-0000-0000-000000000000",
         milestone=None, provenance=None):
    # label defaults to display so TrendColumn.display resolves to it
    # when no milestone is given (mirrors a label-resolved slice).
    return TrendColumn(
        identifier=display, run_id=run_id, label=display,
        milestone=milestone, timestamp="2026-05-01 00:00:00+08",
        provenance=provenance,
    )


def _cell(value, present=True, spec_status=None):
    return TrendCell(
        present=present, value=value, status="ok", spec_status=spec_status,
    )


def _row(output, values, *, sentinel=False, spec=None):
    spec = spec or [None] * len(values)
    cells = tuple(
        _cell(v, present=(v is not None), spec_status=s)
        for v, s in zip(values, spec)
    )
    return TrendRow(
        test="T", corner="TT", point=1, output=output,
        cells=cells, is_sentinel=sentinel,
    )


def _result(columns, rows):
    return TrendResult(columns=tuple(columns), rows=tuple(rows))


class TrendModelTests(unittest.TestCase):

    def test_column_count_is_keys_plus_slices_plus_dir(self):
        res = _result([_col("PDR"), _col("CDR"), _col("FDR")],
                       [_row("gain", [1.0, 2.0, 3.0])])
        m = TrendTableModel(res)
        # 4 key columns + 3 slices + 1 dir column.
        self.assertEqual(m.columnCount(), 8)
        self.assertEqual(m.rowCount(), 1)

    def test_headers_use_column_display_names(self):
        res = _result([_col("PDR", milestone="PDR"), _col("CDR", milestone="CDR")],
                      [_row("gain", [1.0, 2.0])])
        m = TrendTableModel(res)
        headers = [m.headerData(c, Qt.Horizontal) for c in range(m.columnCount())]
        self.assertEqual(headers, ["test", "corner", "point", "output",
                                   "PDR", "CDR", "dir"])

    def test_direction_glyph_in_last_column(self):
        res = _result([_col("a"), _col("b"), _col("c")],
                      [_row("gain", [1.0, 2.0, 3.0])])
        m = TrendTableModel(res)
        dir_col = m.columnCount() - 1
        self.assertEqual(m.data(m.index(0, dir_col)), "▲")

    def test_fail_spec_status_tints_cell(self):
        res = _result([_col("a"), _col("b")],
                      [_row("gain", [1.0, 2.0], spec=["pass", "fail"])])
        m = TrendTableModel(res)
        # value column for slice b (index 5) carries the fail brush.
        brush = m.data(m.index(0, 5), Qt.BackgroundRole)
        self.assertIsInstance(brush, QBrush)

    def test_absent_cell_renders_em_dash(self):
        res = _result([_col("a"), _col("b")],
                      [_row("gain", [1.0, None])])
        m = TrendTableModel(res)
        self.assertEqual(m.data(m.index(0, 5)), "—")

    def test_changed_only_filter_predicate(self):
        res = _result([_col("a"), _col("b")],
                      [_row("flat", [1.0, 1.0]), _row("moved", [1.0, 2.0])])
        m = TrendTableModel(res)
        self.assertFalse(m.changed_only_filter(0))
        self.assertTrue(m.changed_only_filter(1))


class TrendTabTests(unittest.TestCase):

    def test_title_joins_column_displays(self):
        res = _result([_col("PDR"), _col("CDR"), _col("FDR")],
                      [_row("gain", [1.0, 2.0, 3.0])])
        tab = TrendTab(res)
        self.assertEqual(tab.title, "Trend: PDR → CDR → FDR")

    def test_closed_signal_exists(self):
        tab = TrendTab(_result([_col("a"), _col("b")], [_row("g", [1.0, 2.0])]))
        self.assertIsInstance(tab.closed, pyqtBoundSignal)

    def test_sentinel_rows_hidden_by_default(self):
        res = _result(
            [_col("a"), _col("b")],
            [_row("gain", [1.0, 2.0]),
             _row("__sim_status__", [None, None], sentinel=True)],
        )
        tab = TrendTab(res)
        # Source model has both rows; proxy hides the sentinel.
        self.assertEqual(tab.model.rowCount(), 2)
        self.assertEqual(tab.proxy.rowCount(), 1)

    def test_sentinel_toggle_reveals_row(self):
        res = _result(
            [_col("a"), _col("b")],
            [_row("gain", [1.0, 2.0]),
             _row("__sim_status__", [None, None], sentinel=True)],
        )
        tab = TrendTab(res)
        tab.sentinel_check.setChecked(True)
        self.assertEqual(tab.proxy.rowCount(), 2)

    def test_changed_only_hides_flat_rows(self):
        res = _result(
            [_col("a"), _col("b")],
            [_row("flat", [1.0, 1.0]), _row("moved", [1.0, 9.0])],
        )
        tab = TrendTab(res)
        self.assertEqual(tab.proxy.rowCount(), 2)
        tab.changed_only_check.setChecked(True)
        self.assertEqual(tab.proxy.rowCount(), 1)

    def test_rows_render_with_nonzero_height(self):
        # Guard against the blockSignals/bulk-insert 0-px-row trap.
        res = _result([_col("a"), _col("b")],
                      [_row("gain", [1.0, 2.0])])
        tab = TrendTab(res)
        tab.resize(600, 400)
        tab.show()
        _QAPP.processEvents()
        self.assertGreater(tab.table.rowHeight(0), 0)
        tab.hide()

    def test_empty_result_shows_hint(self):
        tab = TrendTab(_result([_col("a"), _col("b")], []))
        self.assertTrue(tab.empty_label.isVisible() or tab.model.rowCount() == 0)
        self.assertEqual(tab.model.rowCount(), 0)


class TrendTabConsistencyStripTests(unittest.TestCase):
    """G-5 — the cross-run condition-consistency strip."""

    def test_strip_hidden_when_provenance_matches(self):
        prov = {"host": "h", "pdk_version": "v1", "model_files": []}
        res = _result(
            [_col("PDR", provenance=dict(prov)),
             _col("CDR", provenance=dict(prov))],
            [_row("gain", [1.0, 2.0])],
        )
        tab = TrendTab(res)
        self.assertTrue(tab.consistency_label.isHidden())

    def test_strip_shown_on_host_mismatch(self):
        res = _result(
            [_col("PDR", provenance={"host": "deskA", "model_files": []}),
             _col("CDR", provenance={"host": "farmB", "model_files": []})],
            [_row("gain", [1.0, 2.0])],
        )
        tab = TrendTab(res)
        self.assertFalse(tab.consistency_label.isHidden())
        self.assertIn("条件不一致", tab.consistency_label.text())

    def test_strip_shown_when_a_run_lacks_provenance(self):
        res = _result(
            [_col("PDR", provenance={"host": "h", "model_files": []}),
             _col("CDR", provenance=None)],
            [_row("gain", [1.0, 2.0])],
        )
        tab = TrendTab(res)
        self.assertFalse(tab.consistency_label.isHidden())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
