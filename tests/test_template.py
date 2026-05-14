"""Unit tests for simkit.template (`.template.json` loader).

Run with stdlib unittest or pytest:

    PYTHONPATH=python python3 -m unittest tests.test_template -v
    python3 -m pytest tests/test_template.py -v
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

from simkit.template import (  # noqa: E402
    Template,
    TemplateLoadError,
    TemplateMalformedError,
    TemplateParam,
    TemplateSchemaVersionError,
    load_template,
)


_EXAMPLE_FILE = _REPO_ROOT / "config" / "rise_time_threshold.template.json"


def _min_doc(name: str = "rise_time") -> dict:
    return {
        "template_schema_version": 1,
        "name": name,
        "expression": "average(riseTime(vtime('tran $SIG) 0 nil VAR(\"VDD\") nil $V_LOW $V_HIGH t \"time\"))",
        "params": [
            {"key": "SIG",    "kind": "signal"},
            {"key": "V_LOW",  "kind": "number", "default": "10"},
            {"key": "V_HIGH", "kind": "number", "default": "90"},
        ],
    }


class TempDirMixin:
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_tmpl_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, doc, *, name: str = "rise_time") -> Path:
        path = self.tmp / f"{name}.template.json"
        if isinstance(doc, str):
            path.write_text(doc, encoding="utf-8")
        else:
            path.write_text(json.dumps(doc), encoding="utf-8")
        return path


class HappyPathTests(TempDirMixin, unittest.TestCase):

    def test_example_loads(self):
        t = load_template(_EXAMPLE_FILE)
        self.assertIsInstance(t, Template)
        self.assertEqual(t.template_schema_version, 1)
        self.assertEqual(t.name, "rise_time_threshold")
        self.assertEqual(t.short_alias, "Rtime")
        self.assertEqual(t.eval_type, "point")
        self.assertTrue(t.plot)
        self.assertFalse(t.save)
        self.assertEqual(t.unit, "s")
        self.assertIsNotNone(t.pasted_from)

    def test_example_param_shape(self):
        t = load_template(_EXAMPLE_FILE)
        keys = [p.key for p in t.params]
        self.assertEqual(keys, ["SIG", "V_LOW", "V_HIGH"])
        sig = t.signal_param()
        self.assertIsNotNone(sig)
        self.assertEqual(sig.key, "SIG")

    def test_minimal_doc(self):
        path = self._write(_min_doc())
        t = load_template(path)
        self.assertEqual(t.short_alias, "rise_time")  # defaults to name
        self.assertEqual(t.eval_type, "point")
        self.assertTrue(t.plot)
        self.assertFalse(t.save)
        self.assertIsNone(t.unit)

    def test_short_alias_override(self):
        doc = _min_doc()
        doc["short_alias"] = "Rtime"
        path = self._write(doc)
        t = load_template(path)
        self.assertEqual(t.short_alias, "Rtime")

    def test_params_by_key(self):
        path = self._write(_min_doc())
        t = load_template(path)
        d = t.params_by_key()
        self.assertEqual(set(d), {"SIG", "V_LOW", "V_HIGH"})
        self.assertIsInstance(d["SIG"], TemplateParam)


class SchemaVersionTests(TempDirMixin, unittest.TestCase):

    def test_missing(self):
        doc = _min_doc()
        del doc["template_schema_version"]
        path = self._write(doc)
        with self.assertRaises(TemplateSchemaVersionError):
            load_template(path)

    def test_unsupported(self):
        doc = _min_doc()
        doc["template_schema_version"] = 2
        path = self._write(doc)
        with self.assertRaises(TemplateSchemaVersionError):
            load_template(path)

    def test_non_int(self):
        doc = _min_doc()
        doc["template_schema_version"] = "1"
        path = self._write(doc)
        with self.assertRaises(TemplateSchemaVersionError):
            load_template(path)


class NameAndFilenameTests(TempDirMixin, unittest.TestCase):

    def test_bad_name_regex(self):
        doc = _min_doc(name="BadName")
        path = self._write(doc, name="BadName")
        with self.assertRaises(TemplateLoadError):
            load_template(path)

    def test_basename_mismatch(self):
        doc = _min_doc()  # name = rise_time
        path = self._write(doc, name="other_name")
        with self.assertRaises(TemplateLoadError):
            load_template(path)

    def test_bad_suffix(self):
        doc = _min_doc()
        path = self.tmp / "rise_time.json"  # missing .template
        path.write_text(json.dumps(doc), encoding="utf-8")
        with self.assertRaises(TemplateLoadError):
            load_template(path)


class MalformedTests(TempDirMixin, unittest.TestCase):

    def test_not_json(self):
        path = self.tmp / "rise_time.template.json"
        path.write_text("not { valid", encoding="utf-8")
        with self.assertRaises(TemplateMalformedError):
            load_template(path)

    def test_not_object(self):
        path = self._write("[]")
        with self.assertRaises(TemplateMalformedError):
            load_template(path)

    def test_missing_file(self):
        with self.assertRaises(TemplateMalformedError):
            load_template(self.tmp / "does_not_exist.template.json")


class GateM4StructuralTests(TempDirMixin, unittest.TestCase):
    """Gate M4 (a, d): structural balance — parens, double-quotes, braces."""

    def test_m4_a_unbalanced_parens_extra_close(self):
        doc = _min_doc()
        doc["expression"] = "average(riseTime($SIG))) $V_LOW $V_HIGH"
        path = self._write(doc)
        with self.assertRaises(TemplateLoadError):
            load_template(path)

    def test_m4_a_unbalanced_parens_extra_open(self):
        doc = _min_doc()
        doc["expression"] = "average((riseTime($SIG) $V_LOW $V_HIGH)"
        path = self._write(doc)
        with self.assertRaises(TemplateLoadError):
            load_template(path)

    def test_m4_d_unbalanced_double_quote(self):
        doc = _min_doc()
        doc["expression"] = 'average(riseTime($SIG) "time $V_LOW $V_HIGH)'
        path = self._write(doc)
        with self.assertRaises(TemplateLoadError):
            load_template(path)

    def test_m4_a_unbalanced_brace(self):
        doc = _min_doc()
        doc["expression"] = "average(riseTime($SIG)) {$V_LOW $V_HIGH"
        path = self._write(doc)
        with self.assertRaises(TemplateLoadError):
            load_template(path)


class GateM4PlaceholderTests(TempDirMixin, unittest.TestCase):
    """Gate M4 (b, c): placeholder ↔ params bidirectional consistency."""

    def test_m4_b_undeclared_param_in_expression(self):
        doc = _min_doc()
        # Add a $V_MID reference but no V_MID param.
        doc["expression"] = (
            "average(riseTime(vtime('tran $SIG) 0 nil VAR(\"VDD\") nil "
            "$V_LOW $V_HIGH $V_MID t \"time\"))"
        )
        path = self._write(doc)
        with self.assertRaises(TemplateLoadError) as cm:
            load_template(path)
        self.assertIn("$V_MID", str(cm.exception))

    def test_m4_c_param_never_referenced(self):
        doc = _min_doc()
        doc["params"].append(
            {"key": "UNUSED", "kind": "number", "default": "0"}
        )
        path = self._write(doc)
        with self.assertRaises(TemplateLoadError) as cm:
            load_template(path)
        self.assertIn("UNUSED", str(cm.exception))


class SignalParamCountTests(TempDirMixin, unittest.TestCase):

    def test_two_signal_params_rejected(self):
        doc = _min_doc()
        # Replace expression to have $SIG2 too.
        doc["expression"] = (
            "average(riseTime(vtime('tran $SIG) 0 nil VAR(\"VDD\") nil "
            "$V_LOW $V_HIGH t \"time\") + vtime('tran $SIG2))"
        )
        doc["params"].append({"key": "SIG2", "kind": "signal"})
        path = self._write(doc)
        with self.assertRaises(TemplateLoadError) as cm:
            load_template(path)
        self.assertIn("signal", str(cm.exception).lower())

    def test_zero_signal_params_allowed(self):
        doc = {
            "template_schema_version": 1,
            "name": "pn_at_freq",
            "expression": "value(VAR($OUT_NAME) $FREQ)",
            "params": [
                {"key": "OUT_NAME", "kind": "string"},
                {"key": "FREQ", "kind": "number", "default": "1000000"},
            ],
        }
        path = self._write(doc, name="pn_at_freq")
        t = load_template(path)
        self.assertIsNone(t.signal_param())


class ParamShapeTests(TempDirMixin, unittest.TestCase):

    def test_bad_param_key(self):
        doc = _min_doc()
        doc["params"][0]["key"] = "lower"  # not [A-Z][A-Z0-9_]*
        path = self._write(doc)
        with self.assertRaises(TemplateLoadError):
            load_template(path)

    def test_unknown_kind(self):
        doc = _min_doc()
        doc["params"][1]["kind"] = "vector"
        path = self._write(doc)
        with self.assertRaises(TemplateLoadError):
            load_template(path)

    def test_duplicate_param_key(self):
        doc = _min_doc()
        doc["params"].append(
            {"key": "V_LOW", "kind": "number", "default": "0"}
        )
        path = self._write(doc)
        with self.assertRaises(TemplateLoadError):
            load_template(path)

    def test_default_must_be_string(self):
        doc = _min_doc()
        doc["params"][1]["default"] = 10  # int, not str
        path = self._write(doc)
        with self.assertRaises(TemplateLoadError):
            load_template(path)


class EvalTypeTests(TempDirMixin, unittest.TestCase):

    def test_unknown_eval_type(self):
        doc = _min_doc()
        doc["eval_type"] = "histogram"
        path = self._write(doc)
        with self.assertRaises(TemplateLoadError):
            load_template(path)

    def test_known_eval_types_pass(self):
        for et in ("point", "corners", "sweeps", "maa"):
            with self.subTest(eval_type=et):
                doc = _min_doc()
                doc["eval_type"] = et
                path = self._write(doc)
                t = load_template(path)
                self.assertEqual(t.eval_type, et)


class OptionalFlagsTests(TempDirMixin, unittest.TestCase):

    def test_plot_save_non_bool_rejected(self):
        doc = _min_doc()
        doc["plot"] = "true"  # string, not bool
        path = self._write(doc)
        with self.assertRaises(TemplateLoadError):
            load_template(path)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
