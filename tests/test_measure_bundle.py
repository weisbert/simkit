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


def _min_bundle(name: str = "voltage_outs_rise") -> dict:
    return {
        "measure_schema_version": 1,
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
