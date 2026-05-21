"""Tests for CornerManagerView — Phase 5 Stage 1 view (spec §7).

Includes the M2-mandated view-layer render test
(``test_rows_render_with_nonzero_height``): a non-grandfathered view must
prove its rows render at a non-zero height, not just that the model has rows.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "python"))

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from simkit.corner_model import load_cornermodel  # noqa: E402
from simkit.gui.views import corner_manager as cm_mod  # noqa: E402
from simkit.gui.views.corner_manager import CornerManagerView  # noqa: E402

_QAPP = QApplication.instance() or QApplication(sys.argv)


def _make_cm() -> "object":
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
             "models": [{"file": "rf018.scs", "section": "ss"}]},
        ],
    }
    tmp = Path(tempfile.mkdtemp())
    p = tmp / "lo_corners.cornermodel.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return load_cornermodel(p)


class CornerManagerViewTest(unittest.TestCase):
    def setUp(self):
        self._guard_modals()
        self.view = CornerManagerView(_make_cm())

    def tearDown(self):
        self.view.hide()
        self.view.deleteLater()

    def _guard_modals(self):
        # A real QMessageBox blocks forever under offscreen Qt (DECISIONS #78
        # D5). Stub it so an unexpected modal fails loud instead of hanging.
        patcher = mock.patch.object(
            cm_mod.QMessageBox, "warning", return_value=None
        )
        self._warning_mock = patcher.start()
        self.addCleanup(patcher.stop)

    def test_displays_modes_and_columns(self):
        self.assertEqual(self.view.modes_list.count(), 1)
        # 2 corners + the name column + the Filter-corner column
        self.assertEqual(self.view.table_model.columnCount(), 4)
        # modes panel shows the selected mode's register vars
        self.assertEqual(self.view.mode_vars.rowCount(), 2)

    def test_rows_render_with_nonzero_height(self):
        # M2 mandate — guard against the blockSignals/bulk-insert 0-px trap.
        self.view.resize(800, 400)
        self.view.show()
        _QAPP.processEvents()
        self.assertGreater(self.view.table.rowHeight(0), 0)
        # the Modes pop-up renders its register table at a real height too
        self.view._modes_dialog.show()
        _QAPP.processEvents()
        self.assertGreater(self.view.mode_vars.rowHeight(0), 0)
        self.view._modes_dialog.hide()

    def test_global_edit_syncs_all_columns(self):
        edited = []
        self.view.cornermodel_edited.connect(edited.append)
        self.view.modes_list.setCurrentRow(0)
        # edit d_en_dummy from "1" to "0" in the modes panel
        for r in range(self.view.mode_vars.rowCount()):
            if self.view.mode_vars.item(r, 0).text() == "d_en_dummy":
                self.view.mode_vars.item(r, 1).setText("0")
                break
        self.assertEqual(len(edited), 1)
        # both TT and SS_1 columns now materialise d_en_dummy = 0
        model = self.view.table_model
        row = next(
            i for i in range(model.rowCount())
            if model.var_at(i) == "d_en_dummy"
        )
        # corner data columns start at model column 2
        self.assertEqual(
            model.data(model.index(row, 2), Qt.DisplayRole), "0"
        )
        self.assertEqual(
            model.data(model.index(row, 3), Qt.DisplayRole), "0"
        )

    def test_new_mode_from_column(self):
        # _make_cm has columns, so New Mode derives a mode from a column.
        with mock.patch.object(
            cm_mod.QInputDialog, "getItem",
            return_value=("From a corner column", True),
        ), mock.patch.object(
            cm_mod._NewModeDialog, "exec_",
            return_value=cm_mod.QDialog.Accepted,
        ), mock.patch.object(
            cm_mod._NewModeDialog, "selected_column_index", return_value=0,
        ), mock.patch.object(
            cm_mod._NewModeDialog, "mode_name", return_value="BT_2G_TX",
        ), mock.patch.object(
            cm_mod._NewModeDialog, "register_vars",
            return_value={"temperature": "55"},
        ), mock.patch.object(
            cm_mod._NewModeDialog, "pvt_label", return_value="TT",
        ):
            self.view._on_new_mode()
        self.assertEqual(self.view.modes_list.count(), 2)
        self.assertIn("BT_2G_TX", self.view.cornermodel().modes)

    def test_new_mode_manual_fallback_when_no_columns(self):
        from simkit.corner_model import empty_cornermodel
        blank = empty_cornermodel(
            name="x", project="1AXX", testbench_id="l/c/v",
        )
        view = CornerManagerView(blank)
        self.addCleanup(view.deleteLater)
        with mock.patch.object(
            cm_mod.QInputDialog, "getText", return_value=("M1", True),
        ), mock.patch.object(
            cm_mod.QInputDialog, "getMultiLineText",
            return_value=("d_en=1", True),
        ):
            view._on_new_mode()
        self.assertIn("M1", view.cornermodel().modes)

    def test_new_column_adds_a_column(self):
        with mock.patch.object(
            cm_mod.QInputDialog, "getItem",
            return_value=("BT_2G_RX", True),
        ), mock.patch.object(
            cm_mod.QInputDialog, "getText",
            return_value=("FF_1", True),
        ), mock.patch.object(
            cm_mod.QInputDialog, "getMultiLineText",
            side_effect=[("temperature=-40", True), ("rf018.scs: ff", True)],
        ):
            self.view._on_new_column()
        self.assertEqual(self.view.table_model.columnCount(), 5)
        new_col = self.view.cornermodel().columns[-1]
        self.assertEqual(new_col.models[0].file, "rf018.scs")
        self.assertEqual(new_col.models[0].section, ("ff",))

    def test_push_signal_carries_cornermodel(self):
        captured = []
        self.view.push_requested.connect(captured.append)
        self.view.btn_push.click()
        self.assertEqual(len(captured), 1)
        self.assertIs(captured[0], self.view.cornermodel())

    def test_pull_signal_emitted(self):
        fired = []
        self.view.pull_requested.connect(lambda: fired.append(True))
        self.view.btn_pull.click()
        self.assertEqual(fired, [True])


def _make_stage2_cm() -> "object":
    data = {
        "cornermodel_schema_version": 1,
        "name": "vco_corners",
        "project": "1AXX",
        "testbench_id": "sim_yusheng/Test/maestro",
        "modes": {"VCO": {"vars": {"d_en_dummy": "1"}}},
        "correlated_axes": {
            "proc_ct": {
                "members": ["process", "CT"],
                "tuples": [
                    {"label": "tt", "values": {"process": "tt", "CT": "100"}},
                    {"label": "ff", "values": {"process": "ff", "CT": "88"}},
                ],
            }
        },
        "pvt_templates": {
            "vco_full": {
                "columns": [
                    {"pvt_label": "TT", "pvt_vars": {"temperature": "55"}},
                    {"pvt_label": "PVT", "pvt_vars": {"VDD": ["0.9", "1.0"]},
                     "correlated_axes": ["proc_ct"]},
                ]
            }
        },
        "columns": [
            {"mode": "VCO", "pvt_label": "seed", "enabled": True,
             "pvt_vars": {"temperature": "55"}},
        ],
    }
    tmp = Path(tempfile.mkdtemp())
    p = tmp / "vco_corners.cornermodel.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return load_cornermodel(p)


class CornerManagerStage2Test(unittest.TestCase):
    def setUp(self):
        self._guard_modals()
        self.view = CornerManagerView(_make_stage2_cm())

    def tearDown(self):
        self.view.hide()
        self.view.deleteLater()

    def _guard_modals(self):
        # A real QMessageBox blocks forever under offscreen Qt (DECISIONS #78
        # D5). Stub it so an unexpected modal fails loud instead of hanging.
        patcher = mock.patch.object(
            cm_mod.QMessageBox, "warning", return_value=None
        )
        self._warning_mock = patcher.start()
        self.addCleanup(patcher.stop)

    def test_template_panel_populated(self):
        self.assertEqual(self.view.templates_list.count(), 1)

    def test_apply_template_generates_columns(self):
        self.view.templates_list.setCurrentRow(0)
        with mock.patch.object(
            cm_mod.QInputDialog, "getItem",
            return_value=("VCO", True),
        ):
            self.view._on_apply_template()
        # seed + VCO_TT + VCO_PVT
        self.assertEqual(self.view.table_model.columnCount(), 5)
        self.assertTrue(
            any(b.template == "vco_full"
                for b in self.view.cornermodel().template_bindings)
        )

    def test_aggregation_column_point_count_in_ncorners_row(self):
        self.view.templates_list.setCurrentRow(0)
        with mock.patch.object(
            cm_mod.QInputDialog, "getItem", return_value=("VCO", True),
        ):
            self.view._on_apply_template()
        model = self.view.table_model
        col = next(
            c for c in range(model.columnCount())
            if model.headerData(c, Qt.Horizontal, Qt.DisplayRole) == "VCO_PVT"
        )
        nrow = next(
            r for r in range(model.rowCount())
            if model.data_row_kind(r) == "ncorners"
        )
        # VCO_PVT = 2 proc_ct tuples × 2 VDD = 4 points
        self.assertEqual(
            model.data(model.index(nrow, col), Qt.DisplayRole), "4"
        )

    def test_unbind_template_after_apply(self):
        self.view.templates_list.setCurrentRow(0)
        with mock.patch.object(
            cm_mod.QInputDialog, "getItem", return_value=("VCO", True),
        ):
            self.view._on_apply_template()
            self.view._on_unbind_template()
        self.assertEqual(self.view.cornermodel().template_bindings, ())
        # columns kept (D3 freeze)
        self.assertEqual(self.view.table_model.columnCount(), 5)


class CornerManagerNewModeFromModeTest(unittest.TestCase):
    """2026 UX — a 'variant' is now just a mode derived from another."""

    def setUp(self):
        patcher = mock.patch.object(
            cm_mod.QMessageBox, "warning", return_value=None
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.view = CornerManagerView(_make_cm())

    def tearDown(self):
        self.view.hide()
        self.view.deleteLater()

    def test_new_mode_derived_from_existing_mode(self):
        # BT_2G_RX exists; derive BT_2G_RX_PN by copying + tweaking registers.
        with mock.patch.object(
            cm_mod.QInputDialog, "getItem",
            side_effect=[
                ("Derived from an existing mode", True),
                ("BT_2G_RX", True),
            ],
        ), mock.patch.object(
            cm_mod.QInputDialog, "getText",
            return_value=("BT_2G_RX_PN", True),
        ), mock.patch.object(
            cm_mod.QInputDialog, "getMultiLineText",
            return_value=("d_en_dummy=1\ndiv_sel=0", True),
        ):
            self.view._on_new_mode()
        modes = self.view.cornermodel().modes
        self.assertIn("BT_2G_RX_PN", modes)
        self.assertEqual(modes["BT_2G_RX_PN"].vars["div_sel"], "0")

    def test_edit_mode_reclassifies_registers(self):
        # _make_cm's mode BT_2G_RX has registers d_en_dummy + div_sel.
        self.view.modes_list.setCurrentRow(0)
        with mock.patch.object(
            cm_mod._EditModeDialog, "exec_",
            return_value=cm_mod.QDialog.Accepted,
        ), mock.patch.object(
            cm_mod._EditModeDialog, "register_vars",
            return_value={"d_en_dummy": "1"},
        ):
            self.view._on_edit_mode()
        regs = self.view.cornermodel().modes["BT_2G_RX"].vars
        self.assertEqual(set(regs), {"d_en_dummy"})

    def test_new_mode_dialog_surfaces_process(self):
        # _make_cm columns carry a model file — the New Mode dialog must
        # show Process so the user sees the P of PVT (2026 UX #2).
        dialog = cm_mod._NewModeDialog(self.view.cornermodel())
        names = [
            dialog._table.item(r, 0).text()
            for r in range(dialog._table.rowCount())
        ]
        self.assertTrue(any(n.startswith("Process") for n in names))


def _make_stage4_cm() -> "object":
    data = {
        "cornermodel_schema_version": 1,
        "name": "lo_corners",
        "project": "1AXX",
        "testbench_id": "sim_yusheng/Test/maestro",
        "modes": {
            "BT_2G_RX": {"vars": {"d_en": "1"}},
            "BT_2G_TX": {"vars": {"d_en": "1"}},
        },
        "run_sets": {
            "RX_only": {"columns": ["BT_2G_RX_TT"]},
        },
        "columns": [
            {"mode": "BT_2G_RX", "pvt_label": "TT", "enabled": True,
             "pvt_vars": {"temperature": "55"}},
            {"mode": "BT_2G_TX", "pvt_label": "TT", "enabled": True,
             "pvt_vars": {"temperature": "55"}},
        ],
    }
    tmp = Path(tempfile.mkdtemp())
    p = tmp / "lo_corners.cornermodel.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return load_cornermodel(p)


class CornerManagerStage4Test(unittest.TestCase):
    def setUp(self):
        self._guard_modals()
        self.view = CornerManagerView(_make_stage4_cm())

    def tearDown(self):
        self.view.hide()
        self.view.deleteLater()

    def _guard_modals(self):
        patcher = mock.patch.object(
            cm_mod.QMessageBox, "warning", return_value=None
        )
        self._warning_mock = patcher.start()
        self.addCleanup(patcher.stop)

    def test_run_sets_panel_populated(self):
        self.assertEqual(self.view.run_sets_list.count(), 1)
        self.assertIn("RX_only", self.view.run_sets_list.item(0).text())

    def test_apply_run_set_changes_enabled(self):
        self.view.run_sets_list.setCurrentRow(0)
        self.view._on_apply_run_set()
        by_name = {
            c.pvt_label: c for c in self.view.cornermodel().columns
        }
        rx = next(c for c in self.view.cornermodel().columns
                  if c.mode == "BT_2G_RX")
        tx = next(c for c in self.view.cornermodel().columns
                  if c.mode == "BT_2G_TX")
        self.assertTrue(rx.enabled)
        self.assertFalse(tx.enabled)

    def test_corner_name_filter_hides_columns(self):
        self.view.resize(800, 400)
        self.view.show()
        _QAPP.processEvents()
        model = self.view.table_model
        # the corner-name filter cell is (0, 1)
        model.setData(model.index(0, 1), "BT_2G_RX", Qt.EditRole)
        _QAPP.processEvents()
        hidden = [
            self.view.table.isColumnHidden(c)
            for c in range(2, model.columnCount())
        ]
        self.assertEqual(hidden.count(True), 1)   # BT_2G_TX_TT hidden
        self.view.hide()

    def test_filter_to_run_set(self):
        self.view.show()
        _QAPP.processEvents()
        self.view.run_sets_list.setCurrentRow(0)
        self.view._on_filter_set()
        model = self.view.table_model
        visible = [
            model.column_at(c).mode
            for c in range(2, model.columnCount())
            if not self.view.table.isColumnHidden(c)
        ]
        # only BT_2G_RX_TT (in RX_only) visible
        self.assertEqual(visible, ["BT_2G_RX"])
        self.view.hide()

    def test_new_run_set_via_dialog(self):
        with mock.patch.object(
            cm_mod.QInputDialog, "getText",
            return_value=("TX_only", True),
        ), mock.patch.object(
            cm_mod._ColumnPickerDialog, "exec_",
            return_value=cm_mod.QDialog.Accepted,
        ), mock.patch.object(
            cm_mod._ColumnPickerDialog, "checked_columns",
            return_value=("BT_2G_TX_TT",),
        ):
            self.view._on_new_run_set()
        self.assertIn("TX_only", self.view.cornermodel().run_sets)


def _make_stage5_cm() -> "object":
    data = {
        "cornermodel_schema_version": 1,
        "name": "lo_corners",
        "project": "1AXX",
        "testbench_id": "sim_yusheng/Test/maestro",
        "modes": {"M": {"vars": {"d_en": "1", "ldo_vset": "3", "div12": "1"}}},
        "pvt_templates": {"lib_tmpl": {"columns": [{"pvt_label": "TT"}]}},
        "columns": [
            {"mode": "M", "pvt_label": "seed", "enabled": True,
             "pvt_vars": {"temperature": "55"}},
        ],
    }
    tmp = Path(tempfile.mkdtemp())
    p = tmp / "lo_corners.cornermodel.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return load_cornermodel(p)


class CornerManagerStage5Test(unittest.TestCase):
    def setUp(self):
        self._guard_modals()
        self.tmp = Path(tempfile.mkdtemp())
        self.view = CornerManagerView(_make_stage5_cm())

    def tearDown(self):
        self.view.hide()
        self.view.deleteLater()

    def _guard_modals(self):
        patcher = mock.patch.object(
            cm_mod.QMessageBox, "warning", return_value=None
        )
        self._warning_mock = patcher.start()
        self.addCleanup(patcher.stop)

    def test_variable_name_filter_hides_rows(self):
        from simkit.gui.corner_filter import FilterMode
        self.view.resize(800, 400)
        self.view.show()
        _QAPP.processEvents()
        model = self.view.table_model
        # the variable-name filter cell is (0, 0)
        model.setData(model.index(0, 0), "ldo*", Qt.EditRole)
        model.set_filter_options(0, 0, mode=FilterMode.WILDCARD)
        _QAPP.processEvents()
        visible = [
            model.var_at(r) for r in range(1, model.rowCount())
            if model.data_row_kind(r) == "var"
            and not self.view.table.isRowHidden(r)
        ]
        self.assertEqual(visible, ["ldo_vset"])
        self.view.hide()

    def test_variable_name_filter_any_words(self):
        from simkit.gui.corner_filter import FilterMode
        self.view.show()
        _QAPP.processEvents()
        model = self.view.table_model
        model.setData(model.index(0, 0), "ldo div", Qt.EditRole)
        model.set_filter_options(0, 0, mode=FilterMode.ANY_WORDS)
        _QAPP.processEvents()
        visible = {
            model.var_at(r) for r in range(1, model.rowCount())
            if model.data_row_kind(r) == "var"
            and not self.view.table.isRowHidden(r)
        }
        self.assertEqual(visible, {"ldo_vset", "div12"})
        self.view.hide()

    def test_check_status_label_present(self):
        self.assertIn("Check", self.view.check_label.text())

    def test_clear_filters_button_resets_visibility(self):
        self.view.show()
        _QAPP.processEvents()
        model = self.view.table_model
        model.setData(model.index(0, 0), "nomatch_xyz", Qt.EditRole)
        _QAPP.processEvents()
        self.view.btn_clear_filters.click()
        _QAPP.processEvents()
        self.assertFalse(model.has_active_filters())
        self.assertFalse(self.view.table.isRowHidden(1))
        self.view.hide()

    def test_export_then_import_library(self):
        lib_path = self.tmp / "std.cornerlib.json"
        with mock.patch.object(
            cm_mod.QInputDialog, "getText",
            return_value=(str(lib_path), True),
        ):
            self.view._on_export_library()
        self.assertTrue(lib_path.is_file())

        # import into a fresh view (no templates) — should merge in lib_tmpl
        fresh_data = {
            "cornermodel_schema_version": 1, "name": "lo_corners",
            "project": "1AXX", "testbench_id": "sim_yusheng/Test/maestro",
            "modes": {"M": {"vars": {"d_en": "1"}}},
            "columns": [{"mode": "M", "pvt_label": "seed", "enabled": True,
                         "pvt_vars": {"temperature": "55"}}],
        }
        fp = self.tmp / "lo_corners.cornermodel.json"
        fp.write_text(json.dumps(fresh_data), encoding="utf-8")
        fresh_view = CornerManagerView(load_cornermodel(fp))
        with mock.patch.object(
            cm_mod.QInputDialog, "getText",
            return_value=(str(lib_path), True),
        ):
            fresh_view._on_import_library()
        self.assertIn("lib_tmpl", fresh_view.cornermodel().pvt_templates)
        fresh_view.deleteLater()


def _make_stage6() -> tuple:
    from simkit.corner_model import load_pvtprofile

    tmp = Path(tempfile.mkdtemp())
    prof_data = {
        "pvtprofile_schema_version": 1, "name": "rf018", "project": "1AXX",
        "axes": {
            "voltage": {"levels": {
                "nominal": {"vars": {"LDO_VSET": "20"}},
                "low": {"vars": {"LDO_VSET": "15"}},
            }},
            "temperature": {"levels": {
                "nominal": {"vars": {"temperature": "55"}},
            }},
        },
    }
    pp = tmp / "rf018.pvtprofile.json"
    pp.write_text(json.dumps(prof_data), encoding="utf-8")
    profile = load_pvtprofile(pp)

    cm_data = {
        "cornermodel_schema_version": 1, "name": "lo_corners",
        "project": "1AXX", "testbench_id": "sim_yusheng/Test/maestro",
        "pvt_profile": "rf018",
        "modes": {"M": {"vars": {"d_en": "1"}}},
        "columns": [{"mode": "M", "pvt_label": "TT", "enabled": True,
                     "axis_levels": {"voltage": "nominal",
                                     "temperature": "nominal"}}],
    }
    cp = tmp / "lo_corners.cornermodel.json"
    cp.write_text(json.dumps(cm_data), encoding="utf-8")
    return load_cornermodel(cp), profile


class CornerManagerStage6Test(unittest.TestCase):
    def setUp(self):
        self._guard_modals()
        self.cm, self.profile = _make_stage6()

    def _guard_modals(self):
        patcher = mock.patch.object(
            cm_mod.QMessageBox, "warning", return_value=None
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_table_resolves_axis_levels_with_profile(self):
        # The PVT profile is still a data-layer concept (the table resolves
        # axis_levels through it) — only the standalone Profile GUI panel
        # was removed in the 2026 simplification.
        view = CornerManagerView(self.cm, self.profile)
        self.addCleanup(view.deleteLater)
        model = view.table_model
        rows = {model.var_at(r) for r in range(model.rowCount())}
        # voltage:nominal resolves LDO_VSET into the displayed table
        self.assertIn("LDO_VSET", rows)


def _make_basic_cm() -> "object":
    """A cornermodel with one mode + two columns — bare authoring start."""
    data = {
        "cornermodel_schema_version": 1,
        "name": "lo_corners",
        "project": "1AXX",
        "testbench_id": "sim_yusheng/Test/maestro",
        "modes": {"BT_2G_RX": {"vars": {"d_en": "1"}}},
        "columns": [
            {"mode": "BT_2G_RX", "pvt_label": "TT", "enabled": True,
             "pvt_vars": {"temperature": "55"}},
            {"mode": "BT_2G_RX", "pvt_label": "SS", "enabled": True,
             "pvt_vars": {"temperature": "125"}},
        ],
    }
    tmp = Path(tempfile.mkdtemp())
    p = tmp / "lo_corners.cornermodel.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return load_cornermodel(p)


class CornerManagerAuthoringTest(unittest.TestCase):
    """GUI authoring of corner sets / run sets."""

    def setUp(self):
        patcher = mock.patch.object(
            cm_mod.QMessageBox, "warning", return_value=None
        )
        self._warning_mock = patcher.start()
        self.addCleanup(patcher.stop)
        self.view = CornerManagerView(_make_basic_cm())
        self.addCleanup(self.view.deleteLater)

    def test_new_template_button_creates_a_pvt_template(self):
        with mock.patch.object(
            cm_mod.QInputDialog, "getText", return_value=("rx_full", True),
        ), mock.patch.object(
            cm_mod.QInputDialog, "getMultiLineText",
            return_value=("TT: temperature=55, VDD=0.9\n"
                          "SS_1: temperature=125, VDD=0.85", True),
        ):
            self.view._on_new_template()
        tmpl = self.view.cornermodel().pvt_templates.get("rx_full")
        self.assertIsNotNone(tmpl)
        self.assertEqual([c.pvt_label for c in tmpl.columns], ["TT", "SS_1"])

    def test_new_template_then_apply_generates_columns(self):
        with mock.patch.object(
            cm_mod.QInputDialog, "getText", return_value=("rx_full", True),
        ), mock.patch.object(
            cm_mod.QInputDialog, "getMultiLineText",
            return_value=("LP: temperature=55, VDD=0.7", True),
        ):
            self.view._on_new_template()
        self.view.templates_list.setCurrentRow(0)
        with mock.patch.object(
            cm_mod.QInputDialog, "getItem", return_value=("BT_2G_RX", True),
        ):
            self.view._on_apply_template()
        names = {
            str(self.view.table_model.headerData(
                c, Qt.Horizontal, Qt.DisplayRole)).split(" ·")[0]
            for c in range(self.view.table_model.columnCount())
        }
        self.assertIn("BT_2G_RX_LP", names)

    def test_new_run_set_uses_column_picker(self):
        with mock.patch.object(
            cm_mod.QInputDialog, "getText", return_value=("All_TT", True),
        ), mock.patch.object(
            cm_mod._ColumnPickerDialog, "exec_",
            return_value=cm_mod.QDialog.Accepted,
        ), mock.patch.object(
            cm_mod._ColumnPickerDialog, "checked_columns",
            return_value=("BT_2G_RX_TT",),
        ):
            self.view._on_new_run_set()
        run_sets = self.view.cornermodel().run_sets
        self.assertIn("All_TT", run_sets)
        self.assertEqual(run_sets["All_TT"].columns, ("BT_2G_RX_TT",))

    def test_corner_name_filter_modes(self):
        from simkit.gui.corner_filter import FilterMode
        model = self.view.table_model
        # _make_basic_cm has BT_2G_RX_TT and BT_2G_RX_SS
        model.setData(model.index(0, 1), "TT SS", Qt.EditRole)
        model.set_filter_options(0, 1, mode=FilterMode.ANY_WORDS)
        hidden = [
            self.view.table.isColumnHidden(c)
            for c in range(2, model.columnCount())
        ]
        self.assertEqual(hidden, [False, False])
        model.set_filter_options(0, 1, mode=FilterMode.CONTAINS)
        model.setData(model.index(0, 1), "TT", Qt.EditRole)
        hidden = [
            self.view.table.isColumnHidden(c)
            for c in range(2, model.columnCount())
        ]
        # only the BT_2G_RX_TT column stays visible
        self.assertEqual(hidden.count(False), 1)


class CornerManagerInteractionTest(unittest.TestCase):
    """2026 UX — corner / variable context-menu actions, row + column
    reordering, and Excel-style copy / paste."""

    def setUp(self):
        patcher = mock.patch.object(
            cm_mod.QMessageBox, "warning", return_value=None
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.view = CornerManagerView(_make_cm())

    def tearDown(self):
        self.view.hide()
        self.view.deleteLater()

    def _corner_names(self):
        m = self.view.table_model
        return [
            str(m.headerData(c, Qt.Horizontal, Qt.DisplayRole)).split(" ·")[0]
            for c in range(2, m.columnCount())
        ]

    def test_duplicate_column_places_copy_after_source(self):
        self.view._duplicate_column(0)
        self.assertEqual(
            self._corner_names(),
            ["BT_2G_RX_TT", "BT_2G_RX_TT_copy", "BT_2G_RX_SS_1"],
        )

    def test_delete_column(self):
        with mock.patch.object(
            cm_mod.QMessageBox, "question",
            return_value=cm_mod.QMessageBox.Yes,
        ):
            self.view._delete_column(0)
        self.assertEqual(self._corner_names(), ["BT_2G_RX_SS_1"])

    def test_toggle_column_enabled(self):
        self.assertTrue(self.view.cornermodel().columns[0].enabled)
        self.view._toggle_column_enabled(0)
        self.assertFalse(self.view.cornermodel().columns[0].enabled)

    def test_shift_column(self):
        self.view._shift_column(0, 1)
        self.assertEqual(
            self._corner_names(), ["BT_2G_RX_SS_1", "BT_2G_RX_TT"]
        )

    def test_reorder_corners_dialog(self):
        with mock.patch.object(
            cm_mod._ReorderDialog, "exec_",
            return_value=cm_mod.QDialog.Accepted,
        ), mock.patch.object(
            cm_mod._ReorderDialog, "new_order", return_value=(1, 0),
        ):
            self.view._on_reorder_corners()
        self.assertEqual(
            self._corner_names(), ["BT_2G_RX_SS_1", "BT_2G_RX_TT"]
        )

    def test_rename_column(self):
        with mock.patch.object(
            cm_mod.QInputDialog, "getText", return_value=("RX_FAST", True),
        ):
            self.view._rename_column(0)
        self.assertIn("RX_FAST", self._corner_names())

    def test_rename_variable(self):
        with mock.patch.object(
            cm_mod.QInputDialog, "getText", return_value=("DIV_SEL", True),
        ):
            self.view._rename_variable("div_sel")
        _temp, design = self.view.table_model.variable_order()
        self.assertIn("DIV_SEL", design)
        self.assertNotIn("div_sel", design)

    def test_remove_variable(self):
        with mock.patch.object(
            cm_mod.QMessageBox, "question",
            return_value=cm_mod.QMessageBox.Yes,
        ):
            self.view._remove_variable("div_sel")
        _temp, design = self.view.table_model.variable_order()
        self.assertNotIn("div_sel", design)

    def test_move_design_variable_row(self):
        before = self.view.table_model.variable_order()[1]
        self.view._move_var(before[0], 1)
        after = self.view.table_model.variable_order()[1]
        self.assertEqual(after[0], before[1])
        self.assertEqual(after[1], before[0])

    def test_copy_then_paste_overwrites_cells(self):
        model = self.view.table_model
        trow = next(
            r for r in range(model.rowCount())
            if model.var_at(r) == "temperature"
        )
        # copy the temperature value of corner column 2
        self.view.table.selectionModel().select(
            model.index(trow, 2),
            self.view.table.selectionModel().ClearAndSelect,
        )
        self.view._copy_selection()
        self.assertEqual(QApplication.clipboard().text(), "55")
        # paste it over corner column 3 (was 125)
        self.view.table.setCurrentIndex(model.index(trow, 3))
        self.view._paste_selection()
        self.assertEqual(
            model.data(model.index(trow, 3), Qt.DisplayRole), "55"
        )

    def test_header_drag_reorders_model(self):
        header = self.view.table.horizontalHeader()
        header.moveSection(2, 3)   # drag the first corner past the second
        self.assertEqual(
            self._corner_names(), ["BT_2G_RX_SS_1", "BT_2G_RX_TT"]
        )
        # the header itself is restored to identity visual order
        self.assertEqual(
            [header.visualIndex(i) for i in range(4)], [0, 1, 2, 3]
        )


if __name__ == "__main__":
    unittest.main()
