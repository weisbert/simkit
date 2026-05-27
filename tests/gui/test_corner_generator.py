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


def _set_pattern(dlg, index, *, name, process, voltage, temp, enabled=True):
    """Inject a PvtPattern at ``index`` into the dialog's library, creating
    new pattern slots as needed. Mode is NOT a per-row property — it's
    picked at the bottom of the dialog at Generate time."""
    from dataclasses import replace
    while index >= len(dlg._library._patterns):
        dlg._library._new_pattern()
    p = cg.PvtPattern(
        enabled=enabled, name=name,
        process_levels=tuple(t for t in (s.strip() for s in process.split(",")) if t),
        voltage_levels=tuple(t for t in (s.strip() for s in voltage.split(",")) if t),
        temperature_levels=tuple(t for t in (s.strip() for s in temp.split(",")) if t),
    )
    dlg._library._patterns[index] = p
    dlg._library._refresh_item(index)
    dlg._library._list.setCurrentRow(index)


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
        _set_pattern(
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
        _set_pattern(
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

    def test_grids_and_pattern_library_render_with_nonzero_height(self):
        # M2 — populated grid / library list must render at a visible
        # height, not merely exist in the widget's row count.
        _view_, dlg = self._dialog()
        dlg.resize(980, 660)
        dlg.show()
        _QAPP.processEvents()
        try:
            lst = dlg._library._list
            self.assertGreater(lst.count(), 0)
            # The list item must take real vertical space.
            rect = lst.visualItemRect(lst.item(0))
            self.assertGreater(rect.height(), 0)
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

    def test_blank_dialog_seeds_one_pattern_in_library(self):
        # A project with no saved patterns gets one blank library entry so
        # the user has somewhere to start editing.
        _view_, dlg = self._dialog()
        self.assertEqual(len(dlg._library.patterns()), 1)
        self.assertTrue(dlg._library.patterns()[0].enabled)

    def test_disabled_pattern_is_skipped_at_generate(self):
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
        _set_pattern(
            dlg, 0, name="ENABLED",
            process="TT", voltage="NV", temp="NT", enabled=True,
        )
        _set_pattern(
            dlg, 1, name="SKIPPED",
            process="TT", voltage="NV", temp="NT", enabled=False,
        )
        with mock.patch.object(cg.QMessageBox, "information"), \
                mock.patch.object(cg.QMessageBox, "warning"):
            dlg._on_generate()
        names = {effective_name(c) for c in view.cornermodel().columns}
        self.assertEqual(names, {"ENABLED"})

    def test_patterns_persist_into_cornermodel_after_close(self):
        # Close (reject) auto-snapshots the library into cm.patterns so the
        # next open re-hydrates them — no more "vanish on close".
        view, dlg = self._dialog()
        _set_pattern(
            dlg, 0, name="{mode}_PVT_45",
            process="TT, SS", voltage="NV", temp="NT, HT",
        )
        _set_pattern(
            dlg, 1, name="draft",
            process="TT", voltage="", temp="", enabled=False,
        )
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
        # Patterns saved into a cornermodel come back as library entries
        # next time the dialog is opened on that cornermodel.
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
        ps = dlg._library.patterns()
        self.assertEqual(len(ps), 2)
        self.assertEqual(ps[0].name, "loaded_1")
        self.assertEqual(ps[0].process_levels, ("TT",))
        self.assertTrue(ps[0].enabled)
        self.assertEqual(ps[1].name, "loaded_2")
        self.assertFalse(ps[1].enabled)
        # The list widget reflects the patterns.
        self.assertEqual(dlg._library._list.count(), 2)
        self.assertEqual(dlg._library._list.item(0).text(), "loaded_1")
        self.assertEqual(dlg._library._list.item(1).text(), "loaded_2")

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

    def test_library_new_appends_pattern_with_auto_name(self):
        _view_, dlg = self._dialog()
        before = len(dlg._library.patterns())
        dlg._library._new_pattern()
        dlg._library._new_pattern()
        ps = dlg._library.patterns()
        self.assertEqual(len(ps), before + 2)
        # Auto-name pattern must be unique and follow the Pattern_N scheme.
        names = [p.name for p in ps]
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(any(n.startswith("Pattern_") for n in names))

    def test_library_duplicate_clones_active_with_copy_suffix(self):
        _view_, dlg = self._dialog()
        _set_pattern(
            dlg, 0, name="my_corner",
            process="TT, SS", voltage="NV", temp="NT",
        )
        dlg._library._duplicate_current()
        ps = dlg._library.patterns()
        self.assertEqual(len(ps), 2)
        self.assertEqual(ps[1].name, "my_corner_copy")
        self.assertEqual(ps[1].process_levels, ("TT", "SS"))
        self.assertEqual(ps[1].voltage_levels, ("NV",))
        # Originals unchanged.
        self.assertEqual(ps[0].name, "my_corner")

    def test_library_delete_removes_selected_with_confirm(self):
        _view_, dlg = self._dialog()
        dlg._library._new_pattern()
        dlg._library._new_pattern()
        self.assertEqual(len(dlg._library.patterns()), 3)
        with mock.patch.object(
            cg.QMessageBox, "question",
            return_value=cg.QMessageBox.Yes,
        ):
            dlg._library._delete_current()
        self.assertEqual(len(dlg._library.patterns()), 2)

    def test_library_load_preset_appends_preset_patterns(self):
        _view_, dlg = self._dialog()
        before = len(dlg._library.patterns())
        preset_name = next(iter(cg._BUILTIN_PRESETS))
        with mock.patch.object(
            cg.QInputDialog, "getItem",
            return_value=(preset_name, True),
        ), mock.patch.object(cg.QMessageBox, "information"):
            dlg._library._load_preset()
        ps = dlg._library.patterns()
        expected = before + len(cg._BUILTIN_PRESETS[preset_name])
        self.assertEqual(len(ps), expected)
        # Last appended pattern's name comes from the preset definition.
        self.assertEqual(
            ps[-1].name,
            cg._BUILTIN_PRESETS[preset_name][-1].name,
        )

    def test_library_disabled_checkbox_round_trips_to_pattern(self):
        _view_, dlg = self._dialog()
        item = dlg._library._list.item(0)
        item.setCheckState(Qt.Unchecked)
        self.assertFalse(dlg._library.patterns()[0].enabled)
        item.setCheckState(Qt.Checked)
        self.assertTrue(dlg._library.patterns()[0].enabled)

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
