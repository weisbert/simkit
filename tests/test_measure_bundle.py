"""Unit tests for simkit.measure_bundle (`.measure.json` loader).

Run with stdlib unittest or pytest:

    PYTHONPATH=python python3 -m unittest tests.test_measure_bundle -v
    python3 -m pytest tests/test_measure_bundle.py -v
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
    MeasureBundle,
    MeasureBundleLoadError,
    MeasureBundleMalformedError,
    MeasureBundleSchemaVersionError,
    load_measure_bundle,
    resolve_measurements_dir,
    resolve_signal_groups_dir,
    resolve_templates_dir,
)
from simkit.project import load_pvtproject  # noqa: E402


_EXAMPLE_TMPL = _REPO_ROOT / "config" / "rise_time_threshold.template.json"
_EXAMPLE_SG = _REPO_ROOT / "config" / "voltage_outs.siggroup.json"
_EXAMPLE_BUNDLE = _REPO_ROOT / "config" / "voltage_outs_rise.measure.json"


class ProjectFixtureMixin:
    """Stage a fresh on-disk project tree under self.tmp."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_mb_"))
        self.proj_dir = self.tmp / "proj"
        self.proj_dir.mkdir()
        self.templates_dir = self.proj_dir / "templates"
        self.signal_groups_dir = self.proj_dir / "signal_groups"
        self.measurements_dir = self.proj_dir / "measurements"
        for d in (self.templates_dir, self.signal_groups_dir, self.measurements_dir):
            d.mkdir()
        # Minimal `.pvtproject`.
        self.pvt_path = self.proj_dir / ".pvtproject"
        self.pvt_path.write_text(
            json.dumps({
                "project": "my_block",
                "dbRoot": "db",
            }),
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
        if isinstance(doc, str):
            p.write_text(doc, encoding="utf-8")
        else:
            p.write_text(json.dumps(doc), encoding="utf-8")
        return p


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


def _voltage_outs_doc() -> dict:
    return {
        "signal_group_schema_version": 1,
        "name": "voltage_outs",
        "signals": ["/Vout"],
    }


def _min_bundle(
    name: str = "voltage_outs_rise", *, schema_version: int = 2,
) -> dict:
    return {
        "measure_schema_version": schema_version,
        "name": name,
        "project": "my_block",
        "testbench_id": "MY_LIB/my_block_tb/schematic",
        "test_name": "Test",
        "apply": [
            {"template": "rise_time_threshold", "signal_group": "voltage_outs"}
        ],
    }


class ProjectDirResolverTests(ProjectFixtureMixin, unittest.TestCase):

    def test_defaults(self):
        self.assertEqual(
            resolve_templates_dir(self.project),
            self.proj_dir / "templates",
        )
        self.assertEqual(
            resolve_signal_groups_dir(self.project),
            self.proj_dir / "signal_groups",
        )
        self.assertEqual(
            resolve_measurements_dir(self.project),
            self.proj_dir / "measurements",
        )

    def test_overrides(self):
        # Re-write .pvtproject with overrides.
        self.pvt_path.write_text(
            json.dumps({
                "project": "my_block",
                "dbRoot": "db",
                "templatesDir": "custom_tmpls",
                "signalGroupsDir": "custom_sgs",
                "measurementsDir": "custom_meas",
            }),
            encoding="utf-8",
        )
        proj = load_pvtproject(start=self.proj_dir)
        self.assertEqual(
            resolve_templates_dir(proj),
            self.proj_dir / "custom_tmpls",
        )
        self.assertEqual(
            resolve_signal_groups_dir(proj),
            self.proj_dir / "custom_sgs",
        )
        self.assertEqual(
            resolve_measurements_dir(proj),
            self.proj_dir / "custom_meas",
        )


class HappyPathTests(ProjectFixtureMixin, unittest.TestCase):

    def test_minimal_bundle_loads(self):
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        path = self._write_bundle(_min_bundle(), name="voltage_outs_rise")
        b = load_measure_bundle(path, project=self.project)
        self.assertIsInstance(b, MeasureBundle)
        self.assertEqual(b.name, "voltage_outs_rise")
        self.assertEqual(b.project, "my_block")
        self.assertEqual(b.test_name, "Test")
        self.assertEqual(len(b.apply), 1)
        self.assertEqual(b.apply[0].template.name, "rise_time_threshold")
        self.assertEqual(b.apply[0].signal_group.name, "voltage_outs")
        self.assertEqual(b.apply[0].param_overrides, {})
        self.assertEqual(b.apply[0].alias_suffix, "")

    def test_example_bundle_loads(self):
        # Stage the example sidecars (each already `<name>.<ext>`) into a
        # tmp proj tree so the loader resolves them relative to the right
        # `.pvtproject` (the real `config/` is not a project directory).
        tmpl_data = json.loads(_EXAMPLE_TMPL.read_text(encoding="utf-8"))
        sg_data = json.loads(_EXAMPLE_SG.read_text(encoding="utf-8"))
        bundle_data = json.loads(_EXAMPLE_BUNDLE.read_text(encoding="utf-8"))
        self._write_template(tmpl_data, name=tmpl_data["name"])
        self._write_signal_group(sg_data, name=sg_data["name"])
        path = self._write_bundle(bundle_data, name=bundle_data["name"])
        b = load_measure_bundle(path, project=self.project)
        self.assertEqual(b.name, "voltage_outs_rise")
        self.assertEqual(b.apply[0].template.short_alias, "Rtime")

    def test_param_overrides_and_alias_suffix(self):
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        doc = _min_bundle()
        doc["apply"][0]["param_overrides"] = {"V_LOW": "20", "V_HIGH": "80"}
        doc["apply"][0]["alias_suffix"] = "_20_80"
        path = self._write_bundle(doc, name="voltage_outs_rise")
        b = load_measure_bundle(path, project=self.project)
        self.assertEqual(b.apply[0].param_overrides, {"V_LOW": "20", "V_HIGH": "80"})
        self.assertEqual(b.apply[0].alias_suffix, "_20_80")

    def test_no_signal_apply_with_null_group(self):
        self._write_template(_no_signal_template_doc(), name="pn_at_freq")
        doc = _min_bundle()
        doc["apply"] = [{
            "template": "pn_at_freq",
            "signal_group": None,
            "param_overrides": {"OUT_NAME": "PN_wave"},
        }]
        path = self._write_bundle(doc, name="voltage_outs_rise")
        b = load_measure_bundle(path, project=self.project)
        self.assertIsNone(b.apply[0].signal_group)

    def test_no_signal_apply_omits_signal_group_implicitly(self):
        # v1.2 (c): when template has no signal-kind param, omitting
        # 'signal_group' is equivalent to explicit null.
        self._write_template(_no_signal_template_doc(), name="pn_at_freq")
        doc = _min_bundle()
        doc["apply"] = [{
            "template": "pn_at_freq",
            "param_overrides": {"OUT_NAME": "PN_wave"},
        }]
        path = self._write_bundle(doc, name="voltage_outs_rise")
        b = load_measure_bundle(path, project=self.project)
        self.assertIsNone(b.apply[0].signal_group)

    def test_signal_template_omitting_signal_group_still_errors(self):
        # v1.2 (c) inverse: template with signal-kind param + omitted
        # signal_group must still surface a load error.
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        doc = _min_bundle()
        del doc["apply"][0]["signal_group"]
        path = self._write_bundle(doc, name="voltage_outs_rise")
        with self.assertRaisesRegex(
            MeasureBundleLoadError, r"missing 'signal_group'"
        ):
            load_measure_bundle(path, project=self.project)

    # v1.2 (a) — output_name override load-time validation -----------------

    def test_output_name_override_accepted(self):
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        doc = _min_bundle()
        doc["apply"][0]["output_name"] = "MyRtime_${SIG}"
        path = self._write_bundle(doc, name="voltage_outs_rise")
        b = load_measure_bundle(path, project=self.project)
        self.assertEqual(b.apply[0].output_name, "MyRtime_${SIG}")

    def test_output_name_default_none_when_omitted(self):
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        path = self._write_bundle(_min_bundle(), name="voltage_outs_rise")
        b = load_measure_bundle(path, project=self.project)
        self.assertIsNone(b.apply[0].output_name)

    def test_output_name_empty_string_rejected(self):
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        doc = _min_bundle()
        doc["apply"][0]["output_name"] = ""
        path = self._write_bundle(doc, name="voltage_outs_rise")
        with self.assertRaisesRegex(
            MeasureBundleLoadError, r"non-empty string"
        ):
            load_measure_bundle(path, project=self.project)

    def test_output_name_sig_placeholder_without_signal_template_rejected(self):
        self._write_template(_no_signal_template_doc(), name="pn_at_freq")
        doc = _min_bundle()
        doc["apply"] = [{
            "template": "pn_at_freq",
            "signal_group": None,
            "param_overrides": {"OUT_NAME": "PN_wave"},
            "output_name": "PN_${SIG}",
        }]
        path = self._write_bundle(doc, name="voltage_outs_rise")
        with self.assertRaisesRegex(
            MeasureBundleLoadError, r"no signal-kind param"
        ):
            load_measure_bundle(path, project=self.project)

    def test_output_name_bad_chars_rejected(self):
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        doc = _min_bundle()
        doc["apply"][0]["output_name"] = "has space"
        path = self._write_bundle(doc, name="voltage_outs_rise")
        with self.assertRaisesRegex(MeasureBundleLoadError, r"must match"):
            load_measure_bundle(path, project=self.project)


class ParamSweepApplyTests(ProjectFixtureMixin, unittest.TestCase):
    """v1.2 (e) — single-axis param_sweep with parallel output_names."""

    def _make_value_at_bundle(self, sweep_doc: dict) -> Path:
        self._write_template(
            _no_signal_template_doc(name="value_at"), name="value_at"
        )
        doc = _min_bundle()
        doc["apply"] = [sweep_doc]
        return self._write_bundle(doc, name="voltage_outs_rise")

    def test_sweep_loads(self):
        path = self._make_value_at_bundle({
            "template": "value_at",
            "signal_group": None,
            "param_overrides": {"OUT_NAME": "PN_wave"},
            "param_sweep": {"FREQ": ["1000000", "3000000", "10000000"]},
            "output_names": ["PN_1M", "PN_3M", "PN_10M"],
        })
        b = load_measure_bundle(path, project=self.project)
        e = b.apply[0]
        self.assertEqual(
            e.param_sweep, {"FREQ": ("1000000", "3000000", "10000000")}
        )
        self.assertEqual(e.output_names, ("PN_1M", "PN_3M", "PN_10M"))

    def test_sweep_without_names_rejected(self):
        path = self._make_value_at_bundle({
            "template": "value_at",
            "signal_group": None,
            "param_overrides": {"OUT_NAME": "PN_wave"},
            "param_sweep": {"FREQ": ["1e6"]},
        })
        with self.assertRaisesRegex(
            MeasureBundleLoadError, r"must appear together"
        ):
            load_measure_bundle(path, project=self.project)

    def test_names_without_sweep_rejected(self):
        path = self._make_value_at_bundle({
            "template": "value_at",
            "signal_group": None,
            "param_overrides": {"OUT_NAME": "PN_wave", "FREQ": "1e6"},
            "output_names": ["X"],
        })
        with self.assertRaisesRegex(
            MeasureBundleLoadError, r"must appear together"
        ):
            load_measure_bundle(path, project=self.project)

    def test_length_mismatch_rejected(self):
        path = self._make_value_at_bundle({
            "template": "value_at",
            "signal_group": None,
            "param_overrides": {"OUT_NAME": "PN_wave"},
            "param_sweep": {"FREQ": ["1e6", "3e6", "10e6"]},
            "output_names": ["PN_1M", "PN_3M"],
        })
        with self.assertRaisesRegex(
            MeasureBundleLoadError, r"parallel arrays must match length"
        ):
            load_measure_bundle(path, project=self.project)

    def test_multi_axis_sweep_rejected(self):
        path = self._make_value_at_bundle({
            "template": "value_at",
            "signal_group": None,
            "param_overrides": {"OUT_NAME": "PN_wave"},
            "param_sweep": {
                "FREQ": ["1e6"],
                "OUT_NAME": ["X"],
            },
            "output_names": ["X"],
        })
        with self.assertRaisesRegex(
            MeasureBundleLoadError, r"exactly one axis"
        ):
            load_measure_bundle(path, project=self.project)

    def test_unknown_sweep_key_rejected(self):
        path = self._make_value_at_bundle({
            "template": "value_at",
            "signal_group": None,
            "param_overrides": {"OUT_NAME": "PN_wave", "FREQ": "1e6"},
            "param_sweep": {"NO_SUCH": ["1"]},
            "output_names": ["X"],
        })
        with self.assertRaisesRegex(
            MeasureBundleLoadError, r"not declared in template"
        ):
            load_measure_bundle(path, project=self.project)

    def test_sweep_signal_kind_rejected(self):
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        doc = _min_bundle()
        doc["apply"] = [{
            "template": "rise_time_threshold",
            "signal_group": "voltage_outs",
            "param_sweep": {"SIG": ["/Vout", "/AVDD"]},
            "output_names": ["X", "Y"],
        }]
        path = self._write_bundle(doc, name="voltage_outs_rise")
        with self.assertRaisesRegex(
            MeasureBundleLoadError, r"cannot sweep a signal-kind param"
        ):
            load_measure_bundle(path, project=self.project)

    def test_sweep_key_collision_with_overrides_rejected(self):
        path = self._make_value_at_bundle({
            "template": "value_at",
            "signal_group": None,
            "param_overrides": {"OUT_NAME": "PN_wave", "FREQ": "1e6"},
            "param_sweep": {"FREQ": ["1e6", "3e6"]},
            "output_names": ["A", "B"],
        })
        with self.assertRaisesRegex(
            MeasureBundleLoadError, r"also listed in 'param_overrides'"
        ):
            load_measure_bundle(path, project=self.project)

    def test_sweep_with_output_name_rejected(self):
        path = self._make_value_at_bundle({
            "template": "value_at",
            "signal_group": None,
            "param_overrides": {"OUT_NAME": "PN_wave"},
            "param_sweep": {"FREQ": ["1e6"]},
            "output_names": ["A"],
            "output_name": "Z",
        })
        with self.assertRaisesRegex(
            MeasureBundleLoadError, r"mutually exclusive"
        ):
            load_measure_bundle(path, project=self.project)


class SpecPassthroughTests(ProjectFixtureMixin, unittest.TestCase):
    """v1.3 — Cadence-native spec string on bundle apply entries."""

    def _bundle_with_spec(self, *, spec: object, raw_entry: bool = False) -> Path:
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        doc = _min_bundle()
        if raw_entry:
            doc["apply"] = [{
                "raw_expression": "rfEdgePhaseNoise(?result \"pn\")",
                "output_name": "PN_wave",
                "spec": spec,
            }]
        else:
            doc["apply"][0]["spec"] = spec
        return self._write_bundle(doc, name="voltage_outs_rise")

    def test_spec_loads_lt(self):
        path = self._bundle_with_spec(spec="<100p")
        b = load_measure_bundle(path, project=self.project)
        self.assertEqual(b.apply[0].spec, "<100p")

    def test_spec_loads_range_bracket(self):
        path = self._bundle_with_spec(spec="[2.4G:2.6G]")
        b = load_measure_bundle(path, project=self.project)
        self.assertEqual(b.apply[0].spec, "[2.4G:2.6G]")

    def test_spec_loads_range_keyword(self):
        path = self._bundle_with_spec(spec="range -150 -100")
        b = load_measure_bundle(path, project=self.project)
        self.assertEqual(b.apply[0].spec, "range -150 -100")

    def test_spec_loads_range_dotted(self):
        # v1.4 — Cadence-style "X..Y" dotted range form. Python validation
        # already passed it through (prefix is numeric); the change is on
        # the SKILL parser side (DECISIONS #46). Pin the bundle-load contract
        # here so a future tightening of _SPEC_PREFIX_RE doesn't regress it.
        path = self._bundle_with_spec(spec="1.5..2.5")
        b = load_measure_bundle(path, project=self.project)
        self.assertEqual(b.apply[0].spec, "1.5..2.5")

    def test_spec_loads_range_dotted_negative(self):
        path = self._bundle_with_spec(spec="-150..-100")
        b = load_measure_bundle(path, project=self.project)
        self.assertEqual(b.apply[0].spec, "-150..-100")

    def test_spec_null_means_none(self):
        path = self._bundle_with_spec(spec=None)
        b = load_measure_bundle(path, project=self.project)
        self.assertIsNone(b.apply[0].spec)

    def test_spec_empty_string_rejected(self):
        path = self._bundle_with_spec(spec="")
        with self.assertRaisesRegex(MeasureBundleLoadError, "non-empty"):
            load_measure_bundle(path, project=self.project)

    def test_spec_whitespace_only_rejected(self):
        path = self._bundle_with_spec(spec="   ")
        with self.assertRaisesRegex(MeasureBundleLoadError, "non-empty"):
            load_measure_bundle(path, project=self.project)

    def test_spec_bad_prefix_rejected(self):
        path = self._bundle_with_spec(spec="probably_not_a_spec")
        with self.assertRaisesRegex(MeasureBundleLoadError, "does not look"):
            load_measure_bundle(path, project=self.project)

    def test_spec_non_string_rejected(self):
        path = self._bundle_with_spec(spec=42)
        with self.assertRaisesRegex(MeasureBundleLoadError, "string"):
            load_measure_bundle(path, project=self.project)

    def test_spec_on_raw_entry(self):
        path = self._bundle_with_spec(spec=">-140", raw_entry=True)
        b = load_measure_bundle(path, project=self.project)
        self.assertEqual(b.apply[0].spec, ">-140")
        self.assertIsNone(b.apply[0].template)

    def test_spec_v1_bundle_rejected(self):
        # v1.3 (1) gated by measure_schema_version: 2 like other v2-only fields.
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        doc = _min_bundle(schema_version=1)
        doc["apply"][0]["spec"] = "<100p"
        path = self._write_bundle(doc, name="voltage_outs_rise")
        with self.assertRaisesRegex(
            MeasureBundleLoadError, r"require 'measure_schema_version': 2"
        ):
            load_measure_bundle(path, project=self.project)


class PerIterationSpecsTests(ProjectFixtureMixin, unittest.TestCase):
    """v1.5 #3 — `specs` parallel array on sweep entries."""

    def _make_pn_sweep_bundle(self, sweep_extra: dict) -> Path:
        self._write_template(
            _no_signal_template_doc(name="pn_at_freq"), name="pn_at_freq"
        )
        entry = {
            "template": "pn_at_freq",
            "signal_group": None,
            "param_overrides": {"OUT_NAME": "PN_wave"},
            "param_sweep": {"FREQ": ["1000000", "100000000"]},
            "output_names": ["PN_1M", "PN_100M"],
        }
        entry.update(sweep_extra)
        doc = _min_bundle()
        doc["apply"] = [entry]
        return self._write_bundle(doc, name="voltage_outs_rise")

    def test_specs_parallel_loads(self):
        path = self._make_pn_sweep_bundle({"specs": ["<-100", "<-140"]})
        b = load_measure_bundle(path, project=self.project)
        self.assertEqual(b.apply[0].specs, ("<-100", "<-140"))
        self.assertIsNone(b.apply[0].spec)

    def test_specs_with_nulls_preserved(self):
        path = self._make_pn_sweep_bundle({"specs": ["<-100", None]})
        b = load_measure_bundle(path, project=self.project)
        self.assertEqual(b.apply[0].specs, ("<-100", None))

    def test_specs_mutex_with_spec(self):
        path = self._make_pn_sweep_bundle(
            {"spec": "<-100", "specs": ["<-100", "<-140"]}
        )
        with self.assertRaisesRegex(
            MeasureBundleLoadError, r"mutually exclusive"
        ):
            load_measure_bundle(path, project=self.project)

    def test_specs_length_mismatch_rejected(self):
        path = self._make_pn_sweep_bundle({"specs": ["<-100"]})
        with self.assertRaisesRegex(
            MeasureBundleLoadError, r"specs.*has 1 entries.*has 2"
        ):
            load_measure_bundle(path, project=self.project)

    def test_specs_bad_entry_rejected(self):
        path = self._make_pn_sweep_bundle({"specs": ["<-100", "garbage"]})
        with self.assertRaisesRegex(
            MeasureBundleLoadError, r"specs\[1\].*does not look"
        ):
            load_measure_bundle(path, project=self.project)

    def test_specs_non_string_entry_rejected(self):
        path = self._make_pn_sweep_bundle({"specs": ["<-100", 42]})
        with self.assertRaisesRegex(
            MeasureBundleLoadError, r"specs\[1\].*string or null"
        ):
            load_measure_bundle(path, project=self.project)

    def test_specs_empty_string_rejected(self):
        path = self._make_pn_sweep_bundle({"specs": ["<-100", "   "]})
        with self.assertRaisesRegex(
            MeasureBundleLoadError, r"specs\[1\].*non-empty"
        ):
            load_measure_bundle(path, project=self.project)

    def test_specs_only_on_sweep_entries(self):
        # No param_sweep + has 'specs' → rejected
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        doc = _min_bundle()
        doc["apply"][0]["specs"] = ["<100p"]
        path = self._write_bundle(doc, name="voltage_outs_rise")
        with self.assertRaisesRegex(
            MeasureBundleLoadError, r"only applies to swept entries"
        ):
            load_measure_bundle(path, project=self.project)

    def test_specs_must_be_array(self):
        path = self._make_pn_sweep_bundle({"specs": "<-100"})
        with self.assertRaisesRegex(
            MeasureBundleLoadError, r"'specs' must be a JSON array"
        ):
            load_measure_bundle(path, project=self.project)


class RawExpressionApplyTests(ProjectFixtureMixin, unittest.TestCase):
    """v1.2 (f) — raw_expression apply entries bypass templates."""

    def _bundle_with_raw(self, raw_entry: dict) -> Path:
        # No template needed — but the bundle still validates against the
        # project, so seed one signal_group so unrelated cases stay simple.
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        doc = _min_bundle()
        doc["apply"] = [raw_entry]
        return self._write_bundle(doc, name="voltage_outs_rise")

    def test_raw_entry_loads(self):
        path = self._bundle_with_raw({
            "raw_expression": "rfEdgePhaseNoise(?result \"pnoise_sample_pm0\")",
            "output_name": "PN_wave",
            "plot": True,
            "save": False,
        })
        b = load_measure_bundle(path, project=self.project)
        self.assertEqual(len(b.apply), 1)
        self.assertIsNone(b.apply[0].template)
        self.assertEqual(
            b.apply[0].raw_expression,
            "rfEdgePhaseNoise(?result \"pnoise_sample_pm0\")",
        )
        self.assertEqual(b.apply[0].output_name, "PN_wave")
        self.assertTrue(b.apply[0].raw_plot)
        self.assertFalse(b.apply[0].raw_save)

    def test_raw_entry_plot_save_defaults(self):
        path = self._bundle_with_raw({
            "raw_expression": "noise(\"dB10\" 1e6)",
            "output_name": "Noise_1M",
        })
        b = load_measure_bundle(path, project=self.project)
        self.assertTrue(b.apply[0].raw_plot)
        self.assertFalse(b.apply[0].raw_save)

    def test_raw_entry_requires_output_name(self):
        path = self._bundle_with_raw({
            "raw_expression": "x",
        })
        with self.assertRaisesRegex(MeasureBundleLoadError, r"output_name"):
            load_measure_bundle(path, project=self.project)

    def test_raw_entry_rejects_empty_expression(self):
        path = self._bundle_with_raw({
            "raw_expression": "",
            "output_name": "X",
        })
        with self.assertRaisesRegex(
            MeasureBundleLoadError, r"non-empty string"
        ):
            load_measure_bundle(path, project=self.project)

    def test_raw_entry_rejects_sig_placeholder(self):
        path = self._bundle_with_raw({
            "raw_expression": "x",
            "output_name": "PN_${SIG}",
        })
        with self.assertRaisesRegex(
            MeasureBundleLoadError, r"\$\{SIG\}.*no signal context"
        ):
            load_measure_bundle(path, project=self.project)

    def test_raw_entry_rejects_unknown_keys(self):
        path = self._bundle_with_raw({
            "raw_expression": "x",
            "output_name": "X",
            "signal_group": "voltage_outs",
        })
        with self.assertRaisesRegex(MeasureBundleLoadError, r"unknown keys"):
            load_measure_bundle(path, project=self.project)

    def test_raw_entry_and_template_mutually_exclusive(self):
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        doc = _min_bundle()
        doc["apply"] = [{
            "template": "rise_time_threshold",
            "signal_group": "voltage_outs",
            "raw_expression": "x",
            "output_name": "X",
        }]
        path = self._write_bundle(doc, name="voltage_outs_rise")
        with self.assertRaisesRegex(
            MeasureBundleLoadError, r"both 'template' and 'raw_expression'"
        ):
            load_measure_bundle(path, project=self.project)

    def test_apply_entry_with_neither_kind_rejected(self):
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        doc = _min_bundle()
        doc["apply"] = [{"output_name": "X"}]
        path = self._write_bundle(doc, name="voltage_outs_rise")
        with self.assertRaisesRegex(
            MeasureBundleLoadError, r"'template' or 'raw_expression'"
        ):
            load_measure_bundle(path, project=self.project)


class SchemaVersionTests(ProjectFixtureMixin, unittest.TestCase):

    def test_missing(self):
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        doc = _min_bundle()
        del doc["measure_schema_version"]
        path = self._write_bundle(doc, name="voltage_outs_rise")
        with self.assertRaises(MeasureBundleSchemaVersionError):
            load_measure_bundle(path, project=self.project)

    def test_unsupported(self):
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        doc = _min_bundle()
        doc["measure_schema_version"] = 99
        path = self._write_bundle(doc, name="voltage_outs_rise")
        with self.assertRaises(MeasureBundleSchemaVersionError):
            load_measure_bundle(path, project=self.project)

    def test_v1_bundle_loads_without_v2_fields(self):
        # A vanilla v1 bundle that doesn't touch any v1.2 feature still
        # loads cleanly under the bumped schema range.
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        doc = _min_bundle(schema_version=1)
        path = self._write_bundle(doc, name="voltage_outs_rise")
        b = load_measure_bundle(path, project=self.project)
        self.assertEqual(b.measure_schema_version, 1)

    def test_v1_bundle_with_output_name_rejected(self):
        # v1.2 (a) output_name is a v2-only field; v1 bundles using it
        # must surface a "bump to 2" error rather than silently accepting.
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        doc = _min_bundle(schema_version=1)
        doc["apply"][0]["output_name"] = "X"
        path = self._write_bundle(doc, name="voltage_outs_rise")
        with self.assertRaisesRegex(
            MeasureBundleLoadError, r"require 'measure_schema_version': 2"
        ):
            load_measure_bundle(path, project=self.project)

    def test_v1_bundle_with_raw_expression_rejected(self):
        doc = _min_bundle(schema_version=1)
        doc["apply"] = [
            {"raw_expression": "x", "output_name": "X"},
        ]
        path = self._write_bundle(doc, name="voltage_outs_rise")
        with self.assertRaisesRegex(
            MeasureBundleLoadError, r"require 'measure_schema_version': 2"
        ):
            load_measure_bundle(path, project=self.project)

    def test_v1_bundle_with_param_sweep_rejected(self):
        self._write_template(
            _no_signal_template_doc(name="value_at"), name="value_at"
        )
        doc = _min_bundle(schema_version=1)
        doc["apply"] = [{
            "template": "value_at",
            "signal_group": None,
            "param_overrides": {"OUT_NAME": "PN_wave"},
            "param_sweep": {"FREQ": ["1e6"]},
            "output_names": ["X"],
        }]
        path = self._write_bundle(doc, name="voltage_outs_rise")
        with self.assertRaisesRegex(
            MeasureBundleLoadError, r"require 'measure_schema_version': 2"
        ):
            load_measure_bundle(path, project=self.project)


class NameAndProjectTests(ProjectFixtureMixin, unittest.TestCase):

    def test_basename_mismatch(self):
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        doc = _min_bundle()
        path = self._write_bundle(doc, name="wrong_name")
        with self.assertRaises(MeasureBundleLoadError):
            load_measure_bundle(path, project=self.project)

    def test_project_mismatch(self):
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        doc = _min_bundle()
        doc["project"] = "some_other_project"
        path = self._write_bundle(doc, name="voltage_outs_rise")
        with self.assertRaises(MeasureBundleLoadError):
            load_measure_bundle(path, project=self.project)


class CrossResolveTests(ProjectFixtureMixin, unittest.TestCase):

    def test_unknown_template(self):
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        doc = _min_bundle()
        path = self._write_bundle(doc, name="voltage_outs_rise")
        with self.assertRaises(MeasureBundleLoadError) as cm:
            load_measure_bundle(path, project=self.project)
        self.assertIn("rise_time_threshold", str(cm.exception))

    def test_unknown_signal_group(self):
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        doc = _min_bundle()
        path = self._write_bundle(doc, name="voltage_outs_rise")
        with self.assertRaises(MeasureBundleLoadError) as cm:
            load_measure_bundle(path, project=self.project)
        self.assertIn("voltage_outs", str(cm.exception))


class GateM4ApplySignalGroupTests(ProjectFixtureMixin, unittest.TestCase):
    """Gate M4 (e, f): signal_group/template signal-param consistency."""

    def test_m4_e_signal_group_given_but_template_has_no_signal_param(self):
        self._write_template(_no_signal_template_doc(), name="pn_at_freq")
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        doc = _min_bundle()
        doc["apply"] = [{
            "template": "pn_at_freq",
            "signal_group": "voltage_outs",
            "param_overrides": {"OUT_NAME": "PN_wave"},
        }]
        path = self._write_bundle(doc, name="voltage_outs_rise")
        with self.assertRaises(MeasureBundleLoadError) as cm:
            load_measure_bundle(path, project=self.project)
        self.assertIn("no signal-kind param", str(cm.exception))

    def test_m4_f_signal_group_null_but_template_has_signal_param(self):
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        doc = _min_bundle()
        doc["apply"][0]["signal_group"] = None
        path = self._write_bundle(doc, name="voltage_outs_rise")
        with self.assertRaises(MeasureBundleLoadError) as cm:
            load_measure_bundle(path, project=self.project)
        self.assertIn("signal-kind param", str(cm.exception))


class GateM4ParamOverridesTests(ProjectFixtureMixin, unittest.TestCase):
    """Gate M4 (g): missing param_overrides for params with no default."""

    def test_m4_g_missing_required_override(self):
        # OUT_NAME has no default; bundle must override.
        self._write_template(_no_signal_template_doc(), name="pn_at_freq")
        doc = _min_bundle()
        doc["apply"] = [{
            "template": "pn_at_freq",
            "signal_group": None,
            # OUT_NAME is missing.
            "param_overrides": {"FREQ": "1e6"},
        }]
        path = self._write_bundle(doc, name="voltage_outs_rise")
        with self.assertRaises(MeasureBundleLoadError) as cm:
            load_measure_bundle(path, project=self.project)
        self.assertIn("OUT_NAME", str(cm.exception))

    def test_override_for_unknown_key_rejected(self):
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        doc = _min_bundle()
        doc["apply"][0]["param_overrides"] = {"NOT_A_KEY": "0"}
        path = self._write_bundle(doc, name="voltage_outs_rise")
        with self.assertRaises(MeasureBundleLoadError) as cm:
            load_measure_bundle(path, project=self.project)
        self.assertIn("NOT_A_KEY", str(cm.exception))

    def test_override_value_must_be_string(self):
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        doc = _min_bundle()
        doc["apply"][0]["param_overrides"] = {"V_LOW": 20}  # int, not str
        path = self._write_bundle(doc, name="voltage_outs_rise")
        with self.assertRaises(MeasureBundleLoadError):
            load_measure_bundle(path, project=self.project)


class AliasSuffixTests(ProjectFixtureMixin, unittest.TestCase):

    def test_alias_suffix_bad_chars_rejected(self):
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        doc = _min_bundle()
        doc["apply"][0]["alias_suffix"] = "-20-80"  # dashes not allowed
        path = self._write_bundle(doc, name="voltage_outs_rise")
        with self.assertRaises(MeasureBundleLoadError):
            load_measure_bundle(path, project=self.project)

    def test_alias_suffix_empty_allowed(self):
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        doc = _min_bundle()
        doc["apply"][0]["alias_suffix"] = ""
        path = self._write_bundle(doc, name="voltage_outs_rise")
        b = load_measure_bundle(path, project=self.project)
        self.assertEqual(b.apply[0].alias_suffix, "")


class StructuralTests(ProjectFixtureMixin, unittest.TestCase):

    def test_apply_must_be_non_empty(self):
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        doc = _min_bundle()
        doc["apply"] = []
        path = self._write_bundle(doc, name="voltage_outs_rise")
        with self.assertRaises(MeasureBundleLoadError):
            load_measure_bundle(path, project=self.project)

    def test_apply_must_be_array(self):
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        doc = _min_bundle()
        doc["apply"] = {}
        path = self._write_bundle(doc, name="voltage_outs_rise")
        with self.assertRaises(MeasureBundleLoadError):
            load_measure_bundle(path, project=self.project)

    def test_apply_missing_signal_group_field(self):
        self._write_template(_rise_template_doc(), name="rise_time_threshold")
        self._write_signal_group(_voltage_outs_doc(), name="voltage_outs")
        doc = _min_bundle()
        del doc["apply"][0]["signal_group"]
        path = self._write_bundle(doc, name="voltage_outs_rise")
        with self.assertRaises(MeasureBundleLoadError):
            load_measure_bundle(path, project=self.project)


class MalformedTests(ProjectFixtureMixin, unittest.TestCase):

    def test_not_json(self):
        path = self._write_bundle("not { json", name="voltage_outs_rise")
        with self.assertRaises(MeasureBundleMalformedError):
            load_measure_bundle(path, project=self.project)

    def test_not_object(self):
        path = self._write_bundle("[]", name="voltage_outs_rise")
        with self.assertRaises(MeasureBundleMalformedError):
            load_measure_bundle(path, project=self.project)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
