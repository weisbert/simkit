"""Unit tests for simkit.template_render (`render_bundle`).

Run with stdlib unittest or pytest:

    PYTHONPATH=python python3 -m unittest tests.test_template_render -v
    python3 -m pytest tests/test_template_render.py -v
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

from simkit.measure_bundle import load_measure_bundle  # noqa: E402
from simkit.project import load_pvtproject  # noqa: E402
from simkit.template_render import (  # noqa: E402
    RenderError,
    RenderedRow,
    render_bundle,
)


_EXAMPLE_TMPL = _REPO_ROOT / "config" / "rise_time_threshold.template.json"
_EXAMPLE_SG = _REPO_ROOT / "config" / "voltage_outs.siggroup.json"
_EXAMPLE_BUNDLE = _REPO_ROOT / "config" / "voltage_outs_rise.measure.json"


def _rise_template_doc(name: str = "rise_time_threshold") -> dict:
    return {
        "template_schema_version": 1,
        "name": name,
        "short_alias": "Rtime",
        "expression": "average(riseTime(vtime('tran $SIG) 0 nil VAR(\"VDD\") nil $V_LOW $V_HIGH t \"time\"))",
        "params": [
            {"key": "SIG",    "kind": "signal"},
            {"key": "V_LOW",  "kind": "number", "default": "10"},
            {"key": "V_HIGH", "kind": "number", "default": "90"},
        ],
        "eval_type": "point",
        "plot": True,
        "save": False,
    }


def _no_signal_template_doc(name: str = "pn_at_freq") -> dict:
    return {
        "template_schema_version": 1,
        "name": name,
        "short_alias": "PN",
        "expression": "value(VAR($OUT_NAME) $FREQ)",
        "params": [
            {"key": "OUT_NAME", "kind": "string"},
            {"key": "FREQ",     "kind": "number", "default": "1000000"},
        ],
    }


def _voltage_outs_doc(name: str = "voltage_outs", signals=None) -> dict:
    return {
        "signal_group_schema_version": 1,
        "name": name,
        "signals": signals if signals is not None else ["/Vout"],
    }


def _bundle_doc(name: str = "voltage_outs_rise", apply=None) -> dict:
    return {
        "measure_schema_version": 1,
        "name": name,
        "project": "my_block",
        "testbench_id": "MY_LIB/my_block_tb/schematic",
        "test_name": "Test",
        "apply": apply or [{
            "template": "rise_time_threshold",
            "signal_group": "voltage_outs",
        }],
    }


class ProjectFixtureMixin:

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_render_"))
        self.proj_dir = self.tmp / "proj"
        self.proj_dir.mkdir()
        self.templates_dir = self.proj_dir / "templates"
        self.signal_groups_dir = self.proj_dir / "signal_groups"
        self.measurements_dir = self.proj_dir / "measurements"
        for d in (self.templates_dir, self.signal_groups_dir, self.measurements_dir):
            d.mkdir()
        self.pvt_path = self.proj_dir / ".pvtproject"
        self.pvt_path.write_text(
            json.dumps({"project": "my_block", "dbRoot": "db"}),
            encoding="utf-8",
        )
        self.project = load_pvtproject(start=self.proj_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_template(self, doc: dict, *, name: str) -> Path:
        p = self.templates_dir / f"{name}.template.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        return p

    def _write_signal_group(self, doc: dict, *, name: str) -> Path:
        p = self.signal_groups_dir / f"{name}.siggroup.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        return p

    def _write_bundle(self, doc: dict, *, name: str) -> Path:
        p = self.measurements_dir / f"{name}.measure.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        return p


class HappyPathTests(ProjectFixtureMixin, unittest.TestCase):

    def test_minimal_one_signal_one_template(self):
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        bundle_path = self._write_bundle(_bundle_doc(), name="voltage_outs_rise")
        bundle = load_measure_bundle(bundle_path, project=self.project)
        rows = render_bundle(bundle)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].output_name, "Rtime_Vout")
        self.assertEqual(
            rows[0].expression,
            "average(riseTime(vtime('tran /Vout) 0 nil VAR(\"VDD\") nil 10 90 t \"time\"))",
        )
        self.assertEqual(rows[0].eval_type, "point")
        self.assertTrue(rows[0].plot)
        self.assertFalse(rows[0].save)

    def test_example_bundle_matches_pasted_from(self):
        """Gate-M1-aligned: the on-disk example template authors
        `vtime('tran "$SIG")` (quoted form, post-paste-importer-fix), so
        rendering with signal=/Vout + V_LOW=10 + V_HIGH=90 (template defaults)
        reconstitutes the original Rtime_clkout expression byte-for-byte
        against the template's `_pasted_from` field."""
        tmpl_data = json.loads(_EXAMPLE_TMPL.read_text(encoding="utf-8"))
        sg_data = json.loads(_EXAMPLE_SG.read_text(encoding="utf-8"))
        bundle_data = json.loads(_EXAMPLE_BUNDLE.read_text(encoding="utf-8"))
        self._write_template(tmpl_data, name=tmpl_data["name"])
        self._write_signal_group(sg_data, name=sg_data["name"])
        path = self._write_bundle(bundle_data, name=bundle_data["name"])
        bundle = load_measure_bundle(path, project=self.project)
        rows = render_bundle(bundle)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].output_name, "Rtime_Vout")
        self.assertEqual(rows[0].expression, tmpl_data["_pasted_from"])

    def test_quoted_signal_form_round_trips_to_pasted_from(self):
        """When the template author writes `vtime('tran "$SIG")` (quoted),
        the rendered form re-acquires the quotes — matching the typical
        fnxSession0 _pasted_from shape `vtime('tran "/Vout")`."""
        doc = _rise_template_doc()
        doc["expression"] = (
            'average(riseTime(vtime(\'tran "$SIG") 0 nil VAR("VDD") '
            'nil $V_LOW $V_HIGH t "time"))'
        )
        self._write_template(doc, name="rise_time_threshold")
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        bundle_path = self._write_bundle(_bundle_doc(), name="voltage_outs_rise")
        bundle = load_measure_bundle(bundle_path, project=self.project)
        rows = render_bundle(bundle)
        self.assertEqual(
            rows[0].expression,
            'average(riseTime(vtime(\'tran "/Vout") 0 nil VAR("VDD") '
            'nil 10 90 t "time"))',
        )

    def test_no_signal_template_renders_once(self):
        self._write_template(_no_signal_template_doc(), name="pn_at_freq")
        doc = _bundle_doc(apply=[{
            "template": "pn_at_freq",
            "signal_group": None,
            "param_overrides": {"OUT_NAME": "PN_wave"},
        }])
        bundle_path = self._write_bundle(doc, name="voltage_outs_rise")
        bundle = load_measure_bundle(bundle_path, project=self.project)
        rows = render_bundle(bundle)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].output_name, "PN")
        self.assertEqual(rows[0].expression, "value(VAR(PN_wave) 1000000)")


class MultiSignalGroupTests(ProjectFixtureMixin, unittest.TestCase):

    def test_three_signals_render_in_group_order(self):
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        self._write_signal_group(
            _voltage_outs_doc(signals=["/Vout", "/Vout2", "/buf/y"]),
            name="voltage_outs",
        )
        bundle_path = self._write_bundle(_bundle_doc(), name="voltage_outs_rise")
        bundle = load_measure_bundle(bundle_path, project=self.project)
        rows = render_bundle(bundle)
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            [r.output_name for r in rows],
            ["Rtime_Vout", "Rtime_Vout2", "Rtime_y"],
        )

    def test_nested_signal_basename(self):
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        self._write_signal_group(
            _voltage_outs_doc(signals=["/I0/I5/out"]),
            name="voltage_outs",
        )
        bundle_path = self._write_bundle(_bundle_doc(), name="voltage_outs_rise")
        bundle = load_measure_bundle(bundle_path, project=self.project)
        rows = render_bundle(bundle)
        self.assertEqual(rows[0].output_name, "Rtime_out")


class AliasSuffixTests(ProjectFixtureMixin, unittest.TestCase):

    def test_alias_suffix_in_output_name(self):
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        doc = _bundle_doc(apply=[{
            "template": "rise_time_threshold",
            "signal_group": "voltage_outs",
            "param_overrides": {"V_LOW": "20", "V_HIGH": "80"},
            "alias_suffix": "_20_80",
        }])
        bundle_path = self._write_bundle(doc, name="voltage_outs_rise")
        bundle = load_measure_bundle(bundle_path, project=self.project)
        rows = render_bundle(bundle)
        self.assertEqual(rows[0].output_name, "Rtime_20_80_Vout")
        self.assertIn(" 20 80 ", rows[0].expression)


class OverridePriorityTests(ProjectFixtureMixin, unittest.TestCase):

    def test_override_beats_default(self):
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        doc = _bundle_doc(apply=[{
            "template": "rise_time_threshold",
            "signal_group": "voltage_outs",
            "param_overrides": {"V_LOW": "30"},
        }])
        bundle_path = self._write_bundle(doc, name="voltage_outs_rise")
        bundle = load_measure_bundle(bundle_path, project=self.project)
        rows = render_bundle(bundle)
        # V_LOW=30 (override), V_HIGH=90 (default).
        self.assertIn(" 30 90 ", rows[0].expression)

    def test_default_used_when_no_override(self):
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        bundle_path = self._write_bundle(_bundle_doc(), name="voltage_outs_rise")
        bundle = load_measure_bundle(bundle_path, project=self.project)
        rows = render_bundle(bundle)
        self.assertIn(" 10 90 ", rows[0].expression)


class TwoEntriesSameTemplateTests(ProjectFixtureMixin, unittest.TestCase):

    def test_two_entries_disambiguated_by_alias_suffix(self):
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        doc = _bundle_doc(apply=[
            {
                "template": "rise_time_threshold",
                "signal_group": "voltage_outs",
            },
            {
                "template": "rise_time_threshold",
                "signal_group": "voltage_outs",
                "param_overrides": {"V_LOW": "20", "V_HIGH": "80"},
                "alias_suffix": "_20_80",
            },
        ])
        bundle_path = self._write_bundle(doc, name="voltage_outs_rise")
        bundle = load_measure_bundle(bundle_path, project=self.project)
        rows = render_bundle(bundle)
        self.assertEqual(
            [r.output_name for r in rows],
            ["Rtime_Vout", "Rtime_20_80_Vout"],
        )


class GateM4CollisionTests(ProjectFixtureMixin, unittest.TestCase):
    """Gate M4 (h): output_name collisions across the bundle render."""

    def test_m4_h_same_template_same_group_twice_collides(self):
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        doc = _bundle_doc(apply=[
            {
                "template": "rise_time_threshold",
                "signal_group": "voltage_outs",
            },
            {
                "template": "rise_time_threshold",
                "signal_group": "voltage_outs",
            },
        ])
        bundle_path = self._write_bundle(doc, name="voltage_outs_rise")
        bundle = load_measure_bundle(bundle_path, project=self.project)
        with self.assertRaises(RenderError):
            render_bundle(bundle)

    def test_m4_h_two_paths_share_basename(self):
        # /buf/y and /I0/y both have basename `y`.
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        self._write_signal_group(
            _voltage_outs_doc(signals=["/buf/y", "/I0/y"]),
            name="voltage_outs",
        )
        bundle_path = self._write_bundle(_bundle_doc(), name="voltage_outs_rise")
        bundle = load_measure_bundle(bundle_path, project=self.project)
        with self.assertRaises(RenderError):
            render_bundle(bundle)


class SubstitutionEdgeCaseTests(ProjectFixtureMixin, unittest.TestCase):

    def test_dollar_followed_by_lowercase_is_literal(self):
        # `$lower` is not a valid param token; should pass through verbatim.
        doc = _no_signal_template_doc(name="literal_dollar")
        doc["expression"] = "concat(\"$lowercase\" $OUT_NAME $FREQ)"
        self._write_template(doc, name="literal_dollar")
        bundle_doc = _bundle_doc(apply=[{
            "template": "literal_dollar",
            "signal_group": None,
            "param_overrides": {"OUT_NAME": "X"},
        }])
        bundle_path = self._write_bundle(bundle_doc, name="voltage_outs_rise")
        bundle = load_measure_bundle(bundle_path, project=self.project)
        rows = render_bundle(bundle)
        self.assertEqual(rows[0].expression, 'concat("$lowercase" X 1000000)')

    def test_param_appears_twice_substituted_twice(self):
        doc = _no_signal_template_doc(name="dup_ref")
        doc["expression"] = "concat($OUT_NAME $OUT_NAME $FREQ)"
        self._write_template(doc, name="dup_ref")
        bundle_doc = _bundle_doc(apply=[{
            "template": "dup_ref",
            "signal_group": None,
            "param_overrides": {"OUT_NAME": "X"},
        }])
        bundle_path = self._write_bundle(bundle_doc, name="voltage_outs_rise")
        bundle = load_measure_bundle(bundle_path, project=self.project)
        rows = render_bundle(bundle)
        self.assertEqual(rows[0].expression, "concat(X X 1000000)")


class RenderedRowTypeTests(unittest.TestCase):

    def test_is_dataclass(self):
        # Sanity check that the dataclass is shaped as documented.
        r = RenderedRow(
            output_name="X", expression="e", eval_type="point",
            plot=True, save=False,
        )
        self.assertEqual(r.output_name, "X")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
