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
        # 3 corners (+ name + filter columns). Rows: Enable, Temperature
        # section + 1, Design Variables section + 2, Model Files section
        # + 1, Number of Corners (+ the filter row) = 10.
        self.assertEqual(self.model.columnCount(), 5)
        self.assertEqual(self.model.rowCount(), 10)

    def test_header_labels(self):
        names = [
            self.model.headerData(c, Qt.Horizontal, Qt.DisplayRole)
            for c in range(5)
        ]
        self.assertEqual(names, [
            "Variable", "Filter corner",
            "BT_2G_RX_TT", "BT_2G_RX_SS_1", "Foreign_TT",
        ])

    def test_row_groups_cadence_layout(self):
        kinds = [
            self.model.data_row_kind(r)
            for r in range(_R0, self.model.rowCount())
        ]
        # Cadence Corners-Setup layout: Enable, section-headed groups,
        # trailing Number of Corners. No Tests section — the fixture has
        # no pulled master test list.
        self.assertEqual(kinds, [
            "enable", "section", "temp", "section", "var", "var",
            "section", "model", "ncorners",
        ])
        temp_row = next(r for r in range(self.model.rowCount())
                        if self.model.data_row_kind(r) == "temp")
        self.assertEqual(self.model.var_at(temp_row), "temperature")
        model_row = next(r for r in range(self.model.rowCount())
                         if self.model.data_row_kind(r) == "model")
        self.assertEqual(self.model.model_at(model_row), "rf018.scs")
        design = {
            self.model.var_at(r) for r in range(self.model.rowCount())
            if self.model.data_row_kind(r) == "var"
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
        # (0,2+) per-corner value, (var-row,1) per-variable value.
        self.assertIsNotNone(self.model.matcher_at(0, 0))
        self.assertIsNotNone(self.model.matcher_at(0, 1))
        self.assertIsNotNone(self.model.matcher_at(0, _C0))
        var_row = self._row_of("d_en_dummy")
        self.assertIsNotNone(self.model.matcher_at(var_row, 1))
        # a data cell is not a filter cell
        self.assertIsNone(self.model.matcher_at(var_row, _C0))
        # the Filter-corner cell on a structural row is not a filter cell
        self.assertIsNone(self.model.matcher_at(_R0, 1))   # the Enable row

    def test_corner_name_filter_hides_columns(self):
        fired = []
        self.model.filtersChanged.connect(lambda: fired.append(True))
        self.model.setData(self.model.index(0, 1), "SS", Qt.EditRole)
        self.assertTrue(fired)
        vis = [self.model.is_data_col_visible(j) for j in range(3)]
        self.assertEqual(vis, [False, True, False])  # only BT_2G_RX_SS_1

    def test_variable_name_filter_hides_rows(self):
        self.model.setData(self.model.index(0, 0), "div", Qt.EditRole)
        # only Design Variable rows are filtered (2026 UX clarification).
        visible = set()
        for r in range(_R0, self.model.rowCount()):
            if self.model.data_row_kind(r) == "var" \
                    and self.model.is_data_row_visible(r - _R0):
                visible.add(self.model.var_at(r))
        self.assertEqual(visible, {"div_sel"})
        # temperature is not a Design Variable — never hidden by a filter.
        trow = self._row_of("temperature")
        self.assertTrue(self.model.is_data_row_visible(trow - _R0))

    def test_per_corner_value_filter_hides_rows(self):
        # filter under BT_2G_RX_TT (col 2): keep variable rows whose TT
        # value is 55 — only temperature.
        self.model.setData(self.model.index(0, _C0), "55", Qt.EditRole)
        temp_i = self._row_of("temperature") - _R0
        den_i = self._row_of("d_en_dummy") - _R0
        self.assertTrue(self.model.is_data_row_visible(temp_i))
        self.assertFalse(self.model.is_data_row_visible(den_i))

    def test_per_variable_value_filter_hides_columns(self):
        # filter beside d_en_dummy (a Design Variable): keep corners whose
        # d_en_dummy is 1 — only BT_2G_RX_TT (SS_1 overrides to 0, Foreign
        # has no d_en_dummy).
        drow = self._row_of("d_en_dummy")
        self.model.setData(self.model.index(drow, 1), "1", Qt.EditRole)
        vis = [self.model.is_data_col_visible(j) for j in range(3)]
        self.assertEqual(vis, [True, False, False])

    def test_numeric_value_filter(self):
        drow = self._row_of("d_en_dummy")
        self.model.setData(self.model.index(drow, 1), ">0", Qt.EditRole)
        self.model.set_filter_options(drow, 1, mode=FilterMode.NUMERIC)
        vis = [self.model.is_data_col_visible(j) for j in range(3)]
        self.assertEqual(vis, [True, False, False])  # only TT (d_en_dummy=1)

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


class TestsGridTest(unittest.TestCase):
    """The Cadence-style Tests grid — one row per test, a checkbox per
    corner — plus the trailing Number of Corners row (2026 UX)."""

    def _model_with_tests(self):
        from dataclasses import replace
        from simkit.corner_model import (
            Column, empty_cornermodel, add_mode, add_column,
        )
        m = empty_cornermodel("corners", "p", "tb")
        m = add_mode(m, "RX", {"d_en": "1"})
        m = add_column(m, Column(
            mode="RX", enabled=True, pvt_vars={"temperature": ("55",)},
            models=(), pvt_label="TT",
        ))
        m = replace(m, tests=("acdc", "tran"))
        return CornerModelTableModel(m)

    def test_per_test_rows_and_ncorners_row(self):
        model = self._model_with_tests()
        kinds = [
            model.data_row_kind(r) for r in range(_R0, model.rowCount())
        ]
        self.assertEqual(kinds.count("test"), 2)
        self.assertEqual(kinds.count("ncorners"), 1)
        self.assertEqual(kinds[-1], "ncorners")

    def test_test_checkbox_toggles_column_scope(self):
        model = self._model_with_tests()
        trow = next(
            r for r in range(model.rowCount())
            if model.test_at(r) == "acdc"
        )
        idx = model.index(trow, _C0)
        self.assertTrue(bool(model.flags(idx) & Qt.ItemIsUserCheckable))
        self.assertEqual(model.data(idx, Qt.CheckStateRole), Qt.Checked)
        model.setData(idx, Qt.Unchecked, Qt.CheckStateRole)
        self.assertEqual(model.cornermodel().columns[0].tests, ("tran",))


if __name__ == "__main__":
    unittest.main()
