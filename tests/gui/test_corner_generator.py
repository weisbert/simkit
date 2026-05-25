"""Runtime verification for the PVT Corner Generator dialog.

Drives the dialog the way a user would — fill the level grids, fill a
pattern row, hit Generate — and checks the corner columns land in the
view's cornermodel with the composite-axis expansion + naming rule.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "python"))

from PyQt5.QtWidgets import QApplication, QTableWidgetItem  # noqa: E402

from simkit.corner_model import (  # noqa: E402
    add_mode,
    column_point_count,
    effective_name,
    empty_cornermodel,
)
from simkit.gui.views import corner_generator as cg  # noqa: E402
from simkit.gui.views.corner_generator import CornerGeneratorDialog  # noqa: E402
from simkit.gui.views.corner_manager import CornerManagerView  # noqa: E402

_QAPP = QApplication.instance() or QApplication(sys.argv)


def _view():
    cm = add_mode(empty_cornermodel(name="vco", project="P"), "Beacon",
                  {"d_en_dummy": "1"})
    return CornerManagerView(cm)


def _set(table, r, c, text):
    table.setItem(r, c, QTableWidgetItem(text))


def _fill_grid(grid, *, model_file, var_headers, rows):
    """rows: list of cell-tuples in column order (Level, [section], vars...)."""
    if model_file is not None:
        grid._model_file_edit.setText(model_file)  # adds the section column
    # the grid is seeded with one variable ("var1") for V/T — reuse / rename
    existing = grid._member_names()
    for i, name in enumerate(var_headers):
        if i < len(existing):
            col = grid._member_start() + i
            grid._table.setHorizontalHeaderItem(col, QTableWidgetItem(name))
        else:
            grid._add_variable(initial=name)
    while grid._table.rowCount() < len(rows):
        grid._add_level()
    while grid._table.rowCount() > len(rows):
        grid._table.removeRow(grid._table.rowCount() - 1)
    for r, cells in enumerate(rows):
        for c, text in enumerate(cells):
            _set(grid._table, r, c, text)


class CornerGeneratorDialogTest(unittest.TestCase):

    def _dialog(self):
        view = _view()
        dlg = CornerGeneratorDialog(view)
        return view, dlg

    def test_dialog_builds_with_three_level_grids(self):
        _view_, dlg = self._dialog()
        self.assertEqual(set(dlg._grids), {"Process", "Voltage", "Temperature"})
        self.assertEqual(dlg._mode_combo.currentText(), "Beacon")

    def test_generate_expands_composite_axes_and_imports(self):
        view, dlg = self._dialog()
        # Process — composite (section + CT).
        _fill_grid(
            dlg._grids["Process"], model_file="/pdk/m.scs",
            var_headers=["CT"],
            rows=[("TT", "tt", "100"), ("SS", "ss", "120")],
        )
        # Voltage — simple (vdd only).
        _fill_grid(
            dlg._grids["Voltage"], model_file=None, var_headers=["vdd"],
            rows=[("NV", "0.80"), ("HV", "0.85")],
        )
        # Temperature — composite (temperature + indfile).
        _fill_grid(
            dlg._grids["Temperature"], model_file=None,
            var_headers=["temperature", "indfile"],
            rows=[("NT", "55", "L55.s5p"), ("LT", "-40", "Ln40.s5p")],
        )
        # one pattern row crossing all three
        pt = dlg._patterns
        for c, text in enumerate(("VCO_PVT", "TT, SS", "NV, HV", "NT, LT")):
            _set(pt, 0, c, text)

        with mock.patch.object(cg.QMessageBox, "information"), \
                mock.patch.object(cg.QMessageBox, "warning"):
            dlg._on_generate()

        cm = view.cornermodel()
        names = {effective_name(c) for c in cm.columns}
        # Process (2, composite) x Temperature (2, composite) = 4 columns;
        # Voltage is simple -> stays multi-valued, never in a name.
        self.assertEqual(
            names,
            {"VCO_PVT_TT_NT", "VCO_PVT_TT_LT",
             "VCO_PVT_SS_NT", "VCO_PVT_SS_LT"},
        )
        # the three axes were written into the model
        self.assertEqual(
            set(cm.correlated_axes), {"Process", "Voltage", "Temperature"}
        )
        # each generated column still sweeps the 2 voltages
        for col in cm.columns:
            self.assertEqual(column_point_count(cm, col), 2)

    def test_generate_all_simple_makes_one_column(self):
        view, dlg = self._dialog()
        # Process — simple here (section only, no extra variable).
        _fill_grid(
            dlg._grids["Process"], model_file="/pdk/m.scs", var_headers=[],
            rows=[("TT", "tt"), ("SS", "ss")],
        )
        _fill_grid(
            dlg._grids["Voltage"], model_file=None, var_headers=["vdd"],
            rows=[("NV", "0.80"), ("HV", "0.85")],
        )
        _fill_grid(
            dlg._grids["Temperature"], model_file=None,
            var_headers=["temperature"],
            rows=[("NT", "55"), ("LT", "-40")],
        )
        pt = dlg._patterns
        for c, text in enumerate(("Beacon_PVT", "TT, SS", "NV, HV", "NT, LT")):
            _set(pt, 0, c, text)

        with mock.patch.object(cg.QMessageBox, "information"), \
                mock.patch.object(cg.QMessageBox, "warning"):
            dlg._on_generate()

        cm = view.cornermodel()
        self.assertEqual(
            {effective_name(c) for c in cm.columns}, {"Beacon_PVT"}
        )
        self.assertEqual(column_point_count(cm, cm.columns[0]), 8)

    def test_level_pick_dialog_round_trips_selection(self):
        levels = ["NT", "LT", "HT"]
        dlg = cg._LevelPickDialog("Temperature", levels, ["LT"])
        self.assertEqual(dlg.selected(), ["LT"])

    def test_read_from_cadence_fills_model_file_and_seeds_sections(self):
        import simkit.skill_bridge as sb
        _view_, dlg = self._dialog()
        grid = dlg._grids["Process"]
        # Stand-in for a loaded project so the early-out guard does not
        # fire (the bare CornerManagerView used in tests has no main
        # window with current_project_path).
        _view_.current_project_path = lambda: Path("/tmp/fake.pvtproject")
        with mock.patch.object(
            sb, "read_model_files",
            return_value={"rf018.scs": {
                "file_abs": "/pdk/rf018.scs",
                "sections": ["tt", "ss", "ff"]}},
        ), mock.patch.object(
            cg.QMessageBox, "question",
            return_value=cg.QMessageBox.Yes,
        ), mock.patch.object(cg.QMessageBox, "information"), \
                mock.patch.object(cg.QMessageBox, "warning"):
            grid._read_from_cadence()
        self.assertEqual(grid._model_file_edit.text(), "rf018.scs")
        self.assertEqual(grid.level_labels(), ["tt", "ss", "ff"])

    def test_read_from_cadence_warns_when_no_project_loaded(self):
        # Brand-new machine path: no .pvtproject loaded yet. Show an
        # actionable hint instead of letting the bridge fail with
        # "no .pvtproject found walking up from <cwd>".
        import simkit.skill_bridge as sb
        _view_, dlg = self._dialog()
        grid = dlg._grids["Process"]
        # _view_ has no current_project_path → guard fires.
        called = []
        with mock.patch.object(
            sb, "read_model_files", side_effect=AssertionError("should not call"),
        ), mock.patch.object(
            cg.QMessageBox, "information",
            side_effect=lambda *a, **kw: called.append(a),
        ):
            grid._read_from_cadence()
        self.assertEqual(len(called), 1)
        msg = called[0][2]
        self.assertIn("File ▸ New Project", msg)

    def test_grids_and_pattern_table_render_with_nonzero_height(self):
        # M2 — a populated grid / pattern row must render at a visible
        # height, not merely exist in the widget's row count.
        _view_, dlg = self._dialog()
        dlg.resize(980, 660)
        dlg.show()
        _QAPP.processEvents()
        try:
            patterns = dlg._patterns
            self.assertGreater(patterns.rowCount(), 0)
            self.assertGreater(patterns.rowHeight(0), 0)
            self.assertGreater(
                patterns.horizontalHeader().sectionSize(0), 0
            )
            grid = dlg._grids["Voltage"]._table
            self.assertGreater(grid.rowCount(), 0)
            self.assertGreater(grid.rowHeight(0), 0)
        finally:
            dlg.hide()


    def test_parse_model_sections_spectre(self):
        import tempfile
        with tempfile.NamedTemporaryFile(
            "w", suffix=".scs", delete=False, encoding="utf-8",
        ) as f:
            f.write(
                "// rf018 PDK model\n"
                "section tt\n"
                "  include \"rf018_tt.scs\"\n"
                "endsection\n"
                "section ss\n"
                "  include \"rf018_ss.scs\"\n"
                "endsection\n"
                "section ff\n"
                "endsection\n"
            )
            path = f.name
        self.assertEqual(cg._parse_model_sections(path), ["tt", "ss", "ff"])

    def test_parse_model_sections_hspice_lib_blocks(self):
        import tempfile
        with tempfile.NamedTemporaryFile(
            "w", suffix=".sp", delete=False, encoding="utf-8",
        ) as f:
            f.write(
                "* HSPICE-style sections\n"
                ".lib tt\n"
                ".param vth=0.42\n"
                ".endl\n"
                ".lib ss\n"
                ".endl\n"
            )
            path = f.name
        self.assertEqual(cg._parse_model_sections(path), ["tt", "ss"])

    def test_parse_model_sections_returns_empty_on_missing_file(self):
        self.assertEqual(cg._parse_model_sections("/no/such/file.scs"), [])

    def test_section_column_delegate_pulls_parsed_sections(self):
        import tempfile
        _view_, dlg = self._dialog()
        grid = dlg._grids["Process"]
        with tempfile.NamedTemporaryFile(
            "w", suffix=".scs", delete=False, encoding="utf-8",
        ) as f:
            f.write("section tt\nendsection\nsection ss\nendsection\n")
            path = f.name
        grid._model_file_edit.setText(path)
        self.assertEqual(grid._available_sections, ["tt", "ss"])
        # The section column should have the combobox delegate installed.
        self.assertIsInstance(
            grid._table.itemDelegateForColumn(1), cg._SectionDelegate
        )


if __name__ == "__main__":
    unittest.main()
