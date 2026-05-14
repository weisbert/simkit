"""DCOBUF walkthrough — end-to-end demonstration that the builtins library
plus signal groups plus a measure bundle reproduces a slice of the user's
real DCOBUF Outputs CSV byte-for-byte.

The walkthrough collapses 20 hand-written CSV expr rows down to a 4-entry
measure-bundle JSON (5 clock nets × 4 templates). Source rows used as the
byte-equality oracle are commented inline below.

A separate ``CollisionExpectedOnSupplyGroup`` test exercises the deliberate
naming collision when a signal group's nets share basenames (e.g. four
distinct paths all ending in /VDD): the render must surface this as a
``RenderError`` rather than silently overwriting one another. The user's
source CSV disambiguates this by hand-numbering rows (Iavg_1, Iavg_2, ...)
— absorbed natively only via a v2 per-signal alias map.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.measure_bundle import (  # noqa: E402
    load_measure_bundle,
    resolve_signal_groups_dir,
    resolve_templates_dir,
)
from simkit.project import load_pvtproject  # noqa: E402
from simkit.template_render import render_bundle  # noqa: E402

_FIXTURE_DIR = _REPO_ROOT / "tests" / "fixtures" / "builtins_walkthrough"
_BUILTINS_DIR = _REPO_ROOT / "config" / "builtins"


def _stage_project(tmp: Path) -> Path:
    """Lay out a real .pvtproject around the fixtures so the bundle loader
    resolves templates + signal_groups exactly as it would in production."""
    pvtproject = tmp / ".pvtproject"
    pvtproject.write_text(
        json.dumps({
            "project": "dcobuf_demo",
            "dbRoot": "./db",
            "schema_version": 1,
        }),
        encoding="utf-8",
    )
    (tmp / "db").mkdir()
    templates_dir = tmp / "templates"
    sg_dir = tmp / "signal_groups"
    meas_dir = tmp / "measurements"
    templates_dir.mkdir()
    sg_dir.mkdir()
    meas_dir.mkdir()
    # Copy the templates the bundle references (plus i_avg_window which the
    # collision-demo test needs separately).
    for name in (
        "i_avg_window",
        "freq_window",
        "duty_cycle_window",
        "rise_time_auto",
        "fall_time_auto",
    ):
        shutil.copy(
            _BUILTINS_DIR / f"{name}.template.json",
            templates_dir / f"{name}.template.json",
        )
    # Copy the two signal-group fixtures
    shutil.copy(
        _FIXTURE_DIR / "dco2g_clocks.siggroup.json",
        sg_dir / "dco2g_clocks.siggroup.json",
    )
    shutil.copy(
        _FIXTURE_DIR / "dco2g_supplies.siggroup.json",
        sg_dir / "dco2g_supplies.siggroup.json",
    )
    # And the bundle
    shutil.copy(
        _FIXTURE_DIR / "dco2g_review.measure.json",
        meas_dir / "dco2g_review.measure.json",
    )
    return pvtproject


_EXPECTED: dict[str, str] = {
    # freq_window × 5 clocks (lifted verbatim from DCOBUF Freq_* rows)
    # Freq_bufp_2g,expr,frequency(clip(vtime('tran "/bufp") VAR("t_1") VAR("t_2")))
    "Freq_bufp": 'frequency(clip(vtime(\'tran "/bufp") VAR("t_1") VAR("t_2")))',
    "Freq_bufn": 'frequency(clip(vtime(\'tran "/bufn") VAR("t_1") VAR("t_2")))',
    "Freq_BT2GTX_P": 'frequency(clip(vtime(\'tran "/BT2GTX_P") VAR("t_1") VAR("t_2")))',
    "Freq_BT2GTX_N": 'frequency(clip(vtime(\'tran "/BT2GTX_N") VAR("t_1") VAR("t_2")))',
    "Freq_wb2grx_inp": 'frequency(clip(vtime(\'tran "/wb2grx_inp") VAR("t_1") VAR("t_2")))',

    # duty_cycle_window × 5 clocks
    # DutyC_dco2g_bufp,expr,dutyCycle(clip(vtime('tran "/bufp") VAR("t_1") VAR("t_2")) ?mode "auto" ?xName "cycle" ?outputType "average")
    "DutyC_bufp": 'dutyCycle(clip(vtime(\'tran "/bufp") VAR("t_1") VAR("t_2")) ?mode "auto" ?xName "cycle" ?outputType "average")',
    "DutyC_bufn": 'dutyCycle(clip(vtime(\'tran "/bufn") VAR("t_1") VAR("t_2")) ?mode "auto" ?xName "cycle" ?outputType "average")',
    "DutyC_BT2GTX_P": 'dutyCycle(clip(vtime(\'tran "/BT2GTX_P") VAR("t_1") VAR("t_2")) ?mode "auto" ?xName "cycle" ?outputType "average")',
    "DutyC_BT2GTX_N": 'dutyCycle(clip(vtime(\'tran "/BT2GTX_N") VAR("t_1") VAR("t_2")) ?mode "auto" ?xName "cycle" ?outputType "average")',
    "DutyC_wb2grx_inp": 'dutyCycle(clip(vtime(\'tran "/wb2grx_inp") VAR("t_1") VAR("t_2")) ?mode "auto" ?xName "cycle" ?outputType "average")',

    # rise_time_auto × 5 clocks (lifted from Rtime_dco2g_* rows)
    "Rtime_bufp": (
        'average(riseTime(clip(vtime(\'tran "/bufp") VAR("t_1") VAR("t_2")) '
        'ymin(clip(vtime(\'tran "/bufp") VAR("t_1") VAR("t_2"))) nil '
        'ymax(clip(vtime(\'tran "/bufp") VAR("t_1") VAR("t_2"))) nil '
        '10 90 t "time"))'
    ),
    "Rtime_BT2GTX_P": (
        'average(riseTime(clip(vtime(\'tran "/BT2GTX_P") VAR("t_1") VAR("t_2")) '
        'ymin(clip(vtime(\'tran "/BT2GTX_P") VAR("t_1") VAR("t_2"))) nil '
        'ymax(clip(vtime(\'tran "/BT2GTX_P") VAR("t_1") VAR("t_2"))) nil '
        '10 90 t "time"))'
    ),
    "Rtime_wb2grx_inp": (
        'average(riseTime(clip(vtime(\'tran "/wb2grx_inp") VAR("t_1") VAR("t_2")) '
        'ymin(clip(vtime(\'tran "/wb2grx_inp") VAR("t_1") VAR("t_2"))) nil '
        'ymax(clip(vtime(\'tran "/wb2grx_inp") VAR("t_1") VAR("t_2"))) nil '
        '10 90 t "time"))'
    ),

    # fall_time_auto × 5 clocks (lifted from Ftime_dco2g_* rows)
    "Ftime_bufp": (
        'average(riseTime(clip(vtime(\'tran "/bufp") VAR("t_1") VAR("t_2")) '
        'ymin(clip(vtime(\'tran "/bufp") VAR("t_1") VAR("t_2"))) nil '
        'ymax(clip(vtime(\'tran "/bufp") VAR("t_1") VAR("t_2"))) nil '
        '90 10 t "time"))'
    ),
    "Ftime_BT2GTX_P": (
        'average(riseTime(clip(vtime(\'tran "/BT2GTX_P") VAR("t_1") VAR("t_2")) '
        'ymin(clip(vtime(\'tran "/BT2GTX_P") VAR("t_1") VAR("t_2"))) nil '
        'ymax(clip(vtime(\'tran "/BT2GTX_P") VAR("t_1") VAR("t_2"))) nil '
        '90 10 t "time"))'
    ),
}


class DcobufWalkthroughTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="simkit_walkthrough_"))
        cls.pvtproject_path = _stage_project(cls.tmp)
        cls.project = load_pvtproject(cls.pvtproject_path)
        cls.bundle_path = cls.tmp / "measurements" / "dco2g_review.measure.json"
        cls.bundle = load_measure_bundle(cls.bundle_path, project=cls.project)
        cls.rows = render_bundle(cls.bundle)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_bundle_collapses_20_hand_written_rows(self):
        # 4 apply entries each iterate the 5-clock group → 4*5=20 outputs.
        self.assertEqual(len(self.bundle.apply), 4)
        self.assertEqual(len(self.rows), 20)

    def test_no_output_name_collisions(self):
        names = [r.output_name for r in self.rows]
        self.assertEqual(len(names), len(set(names)))

    def test_byte_equal_to_dcobuf_source_rows(self):
        rendered = {r.output_name: r.expression for r in self.rows}
        for name, expected in _EXPECTED.items():
            with self.subTest(output=name):
                self.assertIn(name, rendered, f"missing output: {name}")
                self.assertEqual(rendered[name], expected)

    def test_supply_group_collides_under_v1_naming(self):
        """Pin the known v1 limitation: a signal group whose paths share
        basenames will surface as a RenderError when applied. The user's
        source CSV works around this by hand-numbering rows (1, 2, ...);
        a per-signal alias map would absorb the idiom natively (v2)."""
        from simkit.measure_bundle import MeasureApply, MeasureBundle
        from simkit.signal_group import load_signal_group
        from simkit.template import load_template
        from simkit.template_render import RenderError, render_bundle

        templates_dir = self.tmp / "templates"
        sg_dir = self.tmp / "signal_groups"

        t = load_template(templates_dir / "i_avg_window.template.json")
        sg = load_signal_group(sg_dir / "dco2g_supplies.siggroup.json")
        apply = MeasureApply(
            template=t, signal_group=sg,
            param_overrides={}, alias_suffix="",
        )
        bundle = MeasureBundle(
            measure_schema_version=1, name="collide_demo",
            project="dcobuf_demo",
            testbench_id="DCOBUF_LIB/sim_DCOBUF/schematic",
            test_name="sim_DCOBUF", apply=(apply,),
            source_path=Path("-"),
        )
        with self.assertRaises(RenderError) as ctx:
            render_bundle(bundle)
        self.assertIn("appears twice", str(ctx.exception))
        self.assertIn("Iavg_VDD", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
