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

from PyQt5.QtCore import Qt  # noqa: E402
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


def _fill_pattern_row(dlg, r, *, name, process, voltage, temp):
    """Set a pattern row's editable cells without disturbing the Enabled
    checkbox item. Mode is NOT a per-row property — it's picked at the
    bottom of the dialog at Generate time."""
    pt = dlg._patterns
    pt.item(r, cg._PAT_COL_NAME).setText(name)
    pt.item(r, cg._PAT_COL_PROCESS).setText(process)
    pt.item(r, cg._PAT_COL_VOLTAGE).setText(voltage)
    pt.item(r, cg._PAT_COL_TEMP).setText(temp)


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
        _fill_pattern_row(
            dlg, 0, name="VCO_PVT",
            process="TT, SS", voltage="NV, HV", temp="NT, LT",
        )

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
        _fill_pattern_row(
            dlg, 0, name="Beacon_PVT",
            process="TT, SS", voltage="NV, HV", temp="NT, LT",
        )

        with mock.patch.object(cg.QMessageBox, "information"), \
                mock.patch.object(cg.QMessageBox, "warning"):
            dlg._on_generate()

        cm = view.cornermodel()
        self.assertEqual(
            {effective_name(c) for c in cm.columns}, {"Beacon_PVT"}
        )
        self.assertEqual(column_point_count(cm, cm.columns[0]), 8)

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

    # --- new-UX tests ----------------------------------------------------

    def test_blank_dialog_seeds_one_enabled_row(self):
        # A project with no saved patterns gets one blank, ticked row so
        # the user has somewhere to type.
        _view_, dlg = self._dialog()
        pt = dlg._patterns
        self.assertEqual(pt.rowCount(), 1)
        self.assertEqual(
            pt.item(0, cg._PAT_COL_ENABLED).checkState(), Qt.Checked
        )
        self.assertEqual(pt.item(0, cg._PAT_COL_NAME).text(), "")

    def test_disabled_row_is_skipped_at_generate(self):
        view, dlg = self._dialog()
        _fill_grid(
            dlg._grids["Process"], model_file="/pdk/m.scs", var_headers=[],
            rows=[("TT", "tt")],
        )
        _fill_grid(
            dlg._grids["Voltage"], model_file=None, var_headers=["vdd"],
            rows=[("NV", "0.8")],
        )
        _fill_grid(
            dlg._grids["Temperature"], model_file=None,
            var_headers=["temperature"], rows=[("NT", "27")],
        )
        # Two rows, second one disabled by un-ticking the checkbox.
        _fill_pattern_row(
            dlg, 0, name="ENABLED",
            process="TT", voltage="NV", temp="NT",
        )
        dlg._add_pattern_row()
        _fill_pattern_row(
            dlg, 1, name="SKIPPED",
            process="TT", voltage="NV", temp="NT",
        )
        dlg._set_rows_enabled([1], False)
        with mock.patch.object(cg.QMessageBox, "information"), \
                mock.patch.object(cg.QMessageBox, "warning"):
            dlg._on_generate()
        names = {effective_name(c) for c in view.cornermodel().columns}
        self.assertEqual(names, {"ENABLED"})

    def test_patterns_persist_into_cornermodel_after_close(self):
        # Close (reject) auto-snapshots the table into cm.patterns so the
        # next open re-hydrates them — no more "vanish on close".
        view, dlg = self._dialog()
        _fill_pattern_row(
            dlg, 0, name="{mode}_PVT_45",
            process="TT, SS", voltage="NV", temp="NT, HT",
        )
        dlg._add_pattern_row()
        _fill_pattern_row(
            dlg, 1, name="draft",
            process="TT", voltage="", temp="",
        )
        dlg._set_rows_enabled([1], False)
        dlg.reject()
        saved = view.cornermodel().patterns
        self.assertEqual(len(saved), 2)
        self.assertTrue(saved[0].enabled)
        self.assertEqual(saved[0].name, "{mode}_PVT_45")
        self.assertEqual(saved[0].process_levels, ("TT", "SS"))
        self.assertEqual(saved[0].voltage_levels, ("NV",))
        self.assertEqual(saved[0].temperature_levels, ("NT", "HT"))
        self.assertFalse(saved[1].enabled)
        self.assertEqual(saved[1].name, "draft")
        self.assertEqual(saved[1].process_levels, ("TT",))

    def test_dialog_rehydrates_saved_patterns_on_open(self):
        # Patterns saved into a cornermodel come back as table rows next
        # time the dialog is opened on that cornermodel.
        from dataclasses import replace
        view = _view()
        cm = view.cornermodel()
        cm = replace(cm, patterns=(
            cg.PvtPattern(
                enabled=True, name="loaded_1",
                process_levels=("TT",), voltage_levels=("NV", "HV"),
                temperature_levels=("NT",),
            ),
            cg.PvtPattern(
                enabled=False, name="loaded_2",
                process_levels=("SS",), voltage_levels=(),
                temperature_levels=(),
            ),
        ))
        view._apply(cm)
        dlg = CornerGeneratorDialog(view)
        pt = dlg._patterns
        self.assertEqual(pt.rowCount(), 2)
        self.assertEqual(pt.item(0, cg._PAT_COL_NAME).text(), "loaded_1")
        self.assertEqual(
            pt.item(0, cg._PAT_COL_PROCESS).text(), "TT"
        )
        self.assertEqual(
            pt.item(0, cg._PAT_COL_VOLTAGE).text(), "NV, HV"
        )
        self.assertEqual(pt.item(0, cg._PAT_COL_ENABLED).checkState(), Qt.Checked)
        self.assertEqual(pt.item(1, cg._PAT_COL_NAME).text(), "loaded_2")
        self.assertEqual(pt.item(1, cg._PAT_COL_ENABLED).checkState(), Qt.Unchecked)

    def test_name_template_tokens_are_substituted(self):
        self.assertEqual(
            cg._resolve_pattern_name(
                "{mode}_{process}_{voltage}_{temp}",
                mode="RX_BT_2G",
                selections={
                    "Process": ["TT", "SS"], "Voltage": ["NV"],
                    "Temperature": ["NT", "HT"],
                },
            ),
            "RX_BT_2G_TT_SS_NV_NT_HT",
        )

    def test_empty_name_falls_back_to_mode(self):
        # Empty user-typed name → defaults to {mode} → resolved to mode name.
        self.assertEqual(
            cg._resolve_pattern_name(
                "", mode="RX_BT_2G", selections={"Process": ["TT"]},
            ),
            "RX_BT_2G",
        )

    def test_context_menu_helper_toggles_enabled(self):
        _view_, dlg = self._dialog()
        dlg._add_pattern_row()
        dlg._add_pattern_row()
        self.assertEqual(dlg._patterns.rowCount(), 3)
        dlg._set_rows_enabled([0, 2], False)
        self.assertFalse(dlg._row_is_enabled(0))
        self.assertTrue(dlg._row_is_enabled(1))
        self.assertFalse(dlg._row_is_enabled(2))
        dlg._set_rows_enabled([0], True)
        self.assertTrue(dlg._row_is_enabled(0))

    def test_remove_pattern_row_deletes_every_selected_row(self):
        _view_, dlg = self._dialog()
        dlg._add_pattern_row()
        dlg._add_pattern_row()
        self.assertEqual(dlg._patterns.rowCount(), 3)
        dlg._patterns.selectRow(0)
        sm = dlg._patterns.selectionModel()
        # Add row 2 to the selection by toggling its row.
        from PyQt5.QtCore import QItemSelection, QItemSelectionModel
        idx_top = dlg._patterns.model().index(2, 0)
        idx_end = dlg._patterns.model().index(2, dlg._patterns.columnCount() - 1)
        sm.select(
            QItemSelection(idx_top, idx_end),
            QItemSelectionModel.Select | QItemSelectionModel.Rows,
        )
        dlg._remove_pattern_row()
        self.assertEqual(dlg._patterns.rowCount(), 1)

    def test_checkable_combobox_commits_checked_items(self):
        cb = cg._CheckableComboBox(["TT", "SS", "FF"], ["TT", "FF"])
        self.assertEqual(cb.checked_labels(), ["TT", "FF"])
        # Free-typed text wins when it diverges from the checked set, so the
        # cell can carry whatever the user typed verbatim.
        cb.lineEdit().setText("TT, FF, custom_lbl")
        self.assertEqual(cb.committed_value(), "TT, FF, custom_lbl")
        cb.lineEdit().setText("")
        self.assertEqual(cb.committed_value(), "TT, FF")


if __name__ == "__main__":
    unittest.main()
