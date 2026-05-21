"""Tests for CornerModelTableModel — Phase 5 table model (spec §7).

Model coordinates: column 0 = variable name, column 1 = the Filter-corner
strip, columns 2+ = corners; row 0 = the filter row, rows 1+ = data.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "python"))

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from simkit.corner_model import load_cornermodel, set_mode_var  # noqa: E402
from simkit.gui.corner_filter import FilterMode  # noqa: E402
from simkit.gui.corner_model_table import (  # noqa: E402
    _BRUSH_MANAGED,
    _BRUSH_RED,
    CornerModelTableModel,
)

_QAPP = QApplication.instance() or QApplication(sys.argv)

# First corner column / first data row in model coordinates.
_C0 = 2
_R0 = 1


def _cm(tmp_path: Path):
    data = {
        "cornermodel_schema_version": 1,
        "name": "lo_corners",
        "project": "1AXX",
        "testbench_id": "sim_yusheng/Test/maestro",
        "modes": {"BT_2G_RX": {"vars": {"d_en_dummy": "1", "div_sel": "2"}}},
        "columns": [
            {"mode": "BT_2G_RX", "pvt_label": "TT", "enabled": True,
             "pvt_vars": {"temperature": "55"},
             "models": [{"file": "rf018.scs", "section": "tt"}]},
            {"mode": "BT_2G_RX", "pvt_label": "SS_1", "enabled": True,
             "pvt_vars": {"temperature": "125"},
             "overrides": {"d_en_dummy": "0"},
             "models": [{"file": "rf018.scs", "section": "ss"}]},
            {"mode": None, "name": "Foreign_TT", "enabled": True,
             "pvt_vars": {"temperature": "55"},
             "models": [{"file": "rf018.scs", "section": "tt"}]},
        ],
    }
    p = tmp_path / "lo_corners.cornermodel.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return load_cornermodel(p)


class CornerModelTableModelTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = Path(tempfile.mkdtemp())
        self.cm = _cm(self._tmp)
        self.model = CornerModelTableModel(self.cm)

    def test_dimensions(self):
        # 3 corners (+ name + filter columns); temperature + 1 model file +
        # d_en_dummy + div_sel data rows (+ the filter row).
        self.assertEqual(self.model.columnCount(), 5)
        self.assertEqual(self.model.rowCount(), 5)

    def test_header_labels(self):
        names = [
            self.model.headerData(c, Qt.Horizontal, Qt.DisplayRole)
            for c in range(5)
        ]
        self.assertEqual(names, [
            "Variable", "Filter corner",
            "BT_2G_RX_TT", "BT_2G_RX_SS_1", "Foreign_TT",
        ])

    def test_row_groups_temperature_models_then_design(self):
        self.assertEqual(self.model.data_row_kind(_R0), "var")
        self.assertEqual(self.model.var_at(_R0), "temperature")
        self.assertEqual(self.model.data_row_kind(_R0 + 1), "model")
        self.assertEqual(self.model.model_at(_R0 + 1), "rf018.scs")
        design = {
            self.model.var_at(r) for r in range(_R0 + 2, self.model.rowCount())
        }
        self.assertEqual(design, {"d_en_dummy", "div_sel"})

    def test_variable_name_in_column_zero(self):
        row = self._row_of("d_en_dummy")
        self.assertEqual(
            self.model.data(self.model.index(row, 0), Qt.DisplayRole),
            "d_en_dummy",
        )

    def test_model_file_row_shows_section_per_column(self):
        row = self._model_row("rf018.scs")
        self.assertEqual(
            self.model.data(self.model.index(row, _C0), Qt.DisplayRole), "tt"
        )
        self.assertEqual(
            self.model.data(self.model.index(row, _C0 + 1), Qt.DisplayRole),
            "ss",
        )

    def test_model_file_cell_retargets_section(self):
        row = self._model_row("rf018.scs")
        idx = self.model.index(row, _C0)
        self.assertTrue(bool(self.model.flags(idx) & Qt.ItemIsEditable))
        self.assertTrue(self.model.setData(idx, "ff", Qt.EditRole))
        self.assertEqual(
            self.model.data(self.model.index(row, _C0), Qt.DisplayRole), "ff"
        )

    def test_edit_role_prefills_current_value(self):
        row = self._row_of("temperature")
        self.assertEqual(
            self.model.data(self.model.index(row, _C0), Qt.EditRole), "55"
        )

    def test_managed_cell_value_and_brush(self):
        row = self._row_of("d_en_dummy")
        idx = self.model.index(row, _C0)            # BT_2G_RX_TT
        self.assertEqual(self.model.data(idx, Qt.DisplayRole), "1")
        self.assertEqual(self.model.data(idx, Qt.BackgroundRole), _BRUSH_MANAGED)

    def test_diverging_override_is_red(self):
        row = self._row_of("d_en_dummy")
        idx = self.model.index(row, _C0 + 1)        # BT_2G_RX_SS_1, override 0
        self.assertEqual(self.model.data(idx, Qt.DisplayRole), "0")
        self.assertEqual(self.model.data(idx, Qt.BackgroundRole), _BRUSH_RED)

    def test_missing_cell_renders_dash(self):
        row = self._row_of("d_en_dummy")            # Foreign_TT has no d_en
        idx = self.model.index(row, _C0 + 2)
        self.assertEqual(self.model.data(idx, Qt.DisplayRole), "—")

    def test_is_managed_cell(self):
        reg_row = self._row_of("d_en_dummy")
        pvt_row = self._row_of("temperature")
        self.assertTrue(self.model.is_managed_cell(reg_row, _C0))
        self.assertFalse(self.model.is_managed_cell(pvt_row, _C0))
        self.assertFalse(self.model.is_managed_cell(reg_row, _C0 + 2))

    def test_global_edit_reset_syncs(self):
        cm2 = set_mode_var(self.cm, "BT_2G_RX", "d_en_dummy", "0")
        self.model.set_cornermodel(cm2)
        row = self._row_of("d_en_dummy")
        self.assertEqual(
            self.model.data(self.model.index(row, _C0), Qt.DisplayRole), "0"
        )
        self.assertEqual(
            self.model.data(self.model.index(row, _C0 + 1), Qt.BackgroundRole),
            _BRUSH_MANAGED,
        )

    def test_edit_managed_cell_creates_override(self):
        row = self._row_of("d_en_dummy")
        idx = self.model.index(row, _C0)            # BT_2G_RX_TT, base "1"
        emitted = []
        self.model.cornermodelChanged.connect(emitted.append)
        self.assertTrue(self.model.setData(idx, "0", Qt.EditRole))
        self.assertEqual(len(emitted), 1)
        self.assertEqual(
            self.model.data(self.model.index(row, _C0), Qt.BackgroundRole),
            _BRUSH_RED,
        )
        self.assertEqual(self.model.column_at(_C0).overrides["d_en_dummy"], "0")

    def test_edit_pvt_cell_updates_value(self):
        row = self._row_of("temperature")
        idx = self.model.index(row, _C0)
        self.assertTrue(self.model.setData(idx, "-40", Qt.EditRole))
        self.assertEqual(
            self.model.data(self.model.index(row, _C0), Qt.DisplayRole), "-40"
        )

    def test_managed_cells_are_editable(self):
        row = self._row_of("d_en_dummy")
        flags = self.model.flags(self.model.index(row, _C0))
        self.assertTrue(bool(flags & Qt.ItemIsEditable))

    # --- the embedded filter frame --------------------------------------

    def test_filter_cells_are_matcher_backed(self):
        # the four filter slots: (0,0) var-name, (0,1) corner-name,
        # (0,2+) per-corner value, (r,1) per-variable value.
        self.assertIsNotNone(self.model.matcher_at(0, 0))
        self.assertIsNotNone(self.model.matcher_at(0, 1))
        self.assertIsNotNone(self.model.matcher_at(0, _C0))
        self.assertIsNotNone(self.model.matcher_at(_R0, 1))
        # a data cell is not a filter cell
        self.assertIsNone(self.model.matcher_at(_R0, _C0))

    def test_corner_name_filter_hides_columns(self):
        fired = []
        self.model.filtersChanged.connect(lambda: fired.append(True))
        self.model.setData(self.model.index(0, 1), "SS", Qt.EditRole)
        self.assertTrue(fired)
        vis = [self.model.is_data_col_visible(j) for j in range(3)]
        self.assertEqual(vis, [False, True, False])  # only BT_2G_RX_SS_1

    def test_variable_name_filter_hides_rows(self):
        self.model.setData(self.model.index(0, 0), "div", Qt.EditRole)
        visible = {
            self.model.var_at(_R0 + i)
            for i in range(self.model.rowCount() - 1)
            if self.model.is_data_row_visible(i)
        }
        self.assertEqual(visible, {"div_sel"})

    def test_per_corner_value_filter_hides_rows(self):
        # filter under BT_2G_RX_TT (col 2): keep variable rows whose TT
        # value is 55 — only temperature.
        self.model.setData(self.model.index(0, _C0), "55", Qt.EditRole)
        temp_i = self._row_of("temperature") - _R0
        den_i = self._row_of("d_en_dummy") - _R0
        self.assertTrue(self.model.is_data_row_visible(temp_i))
        self.assertFalse(self.model.is_data_row_visible(den_i))

    def test_per_variable_value_filter_hides_columns(self):
        # filter beside temperature (col 1): keep corners whose temperature
        # is 55 — BT_2G_RX_TT and Foreign_TT, not BT_2G_RX_SS_1 (125).
        trow = self._row_of("temperature")
        self.model.setData(self.model.index(trow, 1), "55", Qt.EditRole)
        vis = [self.model.is_data_col_visible(j) for j in range(3)]
        self.assertEqual(vis, [True, False, True])

    def test_numeric_value_filter(self):
        trow = self._row_of("temperature")
        self.model.setData(self.model.index(trow, 1), ">100", Qt.EditRole)
        self.model.set_filter_options(trow, 1, mode=FilterMode.NUMERIC)
        vis = [self.model.is_data_col_visible(j) for j in range(3)]
        self.assertEqual(vis, [False, True, False])  # only SS_1 (125)

    def test_clear_all_filters(self):
        self.model.setData(self.model.index(0, 1), "SS", Qt.EditRole)
        self.assertTrue(self.model.has_active_filters())
        self.model.clear_all_filters()
        self.assertFalse(self.model.has_active_filters())
        self.assertTrue(all(
            self.model.is_data_col_visible(j) for j in range(3)
        ))

    def test_filters_survive_a_cornermodel_rebuild(self):
        self.model.setData(self.model.index(0, 1), "SS", Qt.EditRole)
        cm2 = set_mode_var(self.cm, "BT_2G_RX", "div_sel", "9")
        self.model.set_cornermodel(cm2)
        self.assertEqual(self.model.matcher_at(0, 1).pattern, "SS")

    def _row_of(self, var: str) -> int:
        for r in range(self.model.rowCount()):
            if self.model.var_at(r) == var:
                return r
        raise AssertionError(f"var {var!r} not in model")

    def _model_row(self, file: str) -> int:
        for r in range(self.model.rowCount()):
            if self.model.model_at(r) == file:
                return r
        raise AssertionError(f"model file {file!r} not in model")


if __name__ == "__main__":
    unittest.main()
