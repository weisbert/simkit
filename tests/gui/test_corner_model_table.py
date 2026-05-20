"""Tests for CornerModelTableModel — Phase 5 Stage 1 table model (spec §7)."""

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
from simkit.gui.corner_model_table import (  # noqa: E402
    _BRUSH_MANAGED,
    _BRUSH_RED,
    CornerModelTableModel,
)

_QAPP = QApplication.instance() or QApplication(sys.argv)


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
        # 3 columns; vars = d_en_dummy, div_sel, temperature
        self.assertEqual(self.model.columnCount(), 3)
        self.assertEqual(self.model.rowCount(), 3)

    def test_register_vars_sort_first(self):
        rows = [self.model.var_at(r) for r in range(self.model.rowCount())]
        # register vars (d_en_dummy, div_sel) precede the PVT var temperature
        self.assertEqual(rows.index("temperature"), 2)

    def test_header_is_effective_name(self):
        names = [
            self.model.headerData(c, Qt.Horizontal, Qt.DisplayRole)
            for c in range(3)
        ]
        self.assertEqual(
            names, ["BT_2G_RX_TT", "BT_2G_RX_SS_1", "Foreign_TT"]
        )

    def test_managed_cell_value_and_brush(self):
        row = self._row_of("d_en_dummy")
        idx = self.model.index(row, 0)            # BT_2G_RX_TT
        self.assertEqual(self.model.data(idx, Qt.DisplayRole), "1")
        self.assertEqual(self.model.data(idx, Qt.BackgroundRole), _BRUSH_MANAGED)

    def test_diverging_override_is_red(self):
        row = self._row_of("d_en_dummy")
        idx = self.model.index(row, 1)            # BT_2G_RX_SS_1, override 0
        self.assertEqual(self.model.data(idx, Qt.DisplayRole), "0")
        self.assertEqual(self.model.data(idx, Qt.BackgroundRole), _BRUSH_RED)

    def test_missing_cell_renders_dash(self):
        # Foreign_TT has no d_en_dummy var.
        row = self._row_of("d_en_dummy")
        idx = self.model.index(row, 2)
        self.assertEqual(self.model.data(idx, Qt.DisplayRole), "—")

    def test_is_managed_cell(self):
        reg_row = self._row_of("d_en_dummy")
        pvt_row = self._row_of("temperature")
        self.assertTrue(self.model.is_managed_cell(reg_row, 0))
        self.assertFalse(self.model.is_managed_cell(pvt_row, 0))   # PVT var
        self.assertFalse(self.model.is_managed_cell(reg_row, 2))   # foreign

    def test_global_edit_reset_syncs(self):
        cm2 = set_mode_var(self.cm, "BT_2G_RX", "d_en_dummy", "0")
        self.model.set_cornermodel(cm2)
        row = self._row_of("d_en_dummy")
        # TT now reads 0 (synced)
        self.assertEqual(
            self.model.data(self.model.index(row, 0), Qt.DisplayRole), "0"
        )
        # SS_1 override 0 now equals base -> no longer red, just managed tint
        self.assertEqual(
            self.model.data(self.model.index(row, 1), Qt.BackgroundRole),
            _BRUSH_MANAGED,
        )

    def test_edit_managed_cell_creates_override(self):
        row = self._row_of("d_en_dummy")
        idx = self.model.index(row, 0)            # BT_2G_RX_TT, base "1"
        emitted = []
        self.model.cornermodelChanged.connect(emitted.append)
        self.assertTrue(self.model.setData(idx, "0", Qt.EditRole))
        self.assertEqual(len(emitted), 1)
        # the edit becomes a diverging override -> red
        self.assertEqual(
            self.model.data(self.model.index(row, 0), Qt.BackgroundRole),
            _BRUSH_RED,
        )
        col = self.model.column_at(0)
        self.assertEqual(col.overrides["d_en_dummy"], "0")

    def test_edit_pvt_cell_updates_value(self):
        row = self._row_of("temperature")
        idx = self.model.index(row, 0)
        self.assertTrue(self.model.setData(idx, "-40", Qt.EditRole))
        self.assertEqual(
            self.model.data(self.model.index(row, 0), Qt.DisplayRole), "-40"
        )

    def test_managed_cells_are_editable(self):
        row = self._row_of("d_en_dummy")
        flags = self.model.flags(self.model.index(row, 0))
        self.assertTrue(bool(flags & Qt.ItemIsEditable))

    def _row_of(self, var: str) -> int:
        for r in range(self.model.rowCount()):
            if self.model.var_at(r) == var:
                return r
        raise AssertionError(f"var {var!r} not in model")


if __name__ == "__main__":
    unittest.main()
