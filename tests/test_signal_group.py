"""Unit tests for simkit.signal_group (`.siggroup.json` loader).

Run with stdlib unittest or pytest:

    PYTHONPATH=python python3 -m unittest tests.test_signal_group -v
    python3 -m pytest tests/test_signal_group.py -v
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

from simkit.signal_group import (  # noqa: E402
    Signal,
    SignalGroup,
    SignalGroupLoadError,
    SignalGroupMalformedError,
    SignalGroupSchemaVersionError,
    load_signal_group,
    signal_basename,
)


_EXAMPLE_FILE = _REPO_ROOT / "config" / "voltage_outs.siggroup.json"


def _min_doc(name: str = "outs") -> dict:
    return {
        "signal_group_schema_version": 1,
        "name": name,
        "signals": ["/Vout"],
    }


class TempDirMixin:
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_sg_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, doc, *, name: str = "outs") -> Path:
        path = self.tmp / f"{name}.siggroup.json"
        if isinstance(doc, str):
            path.write_text(doc, encoding="utf-8")
        else:
            path.write_text(json.dumps(doc), encoding="utf-8")
        return path


class HappyPathTests(TempDirMixin, unittest.TestCase):

    def test_example_loads(self):
        sg = load_signal_group(_EXAMPLE_FILE)
        self.assertIsInstance(sg, SignalGroup)
        self.assertEqual(sg.name, "voltage_outs")
        self.assertEqual([s.net for s in sg.signals], ["/Vout"])
        self.assertEqual([s.alias for s in sg.signals], [None])

    def test_minimal_doc(self):
        path = self._write(_min_doc())
        sg = load_signal_group(path)
        self.assertEqual(sg.signal_group_schema_version, 1)
        self.assertEqual([s.net for s in sg.signals], ["/Vout"])

    def test_multi_signal_order_preserved(self):
        doc = _min_doc()
        doc["signals"] = ["/Vout", "/Vout2", "/buf/y"]
        path = self._write(doc)
        sg = load_signal_group(path)
        self.assertEqual(
            [s.net for s in sg.signals], ["/Vout", "/Vout2", "/buf/y"]
        )


class SchemaVersionTests(TempDirMixin, unittest.TestCase):

    def test_missing(self):
        doc = _min_doc()
        del doc["signal_group_schema_version"]
        path = self._write(doc)
        with self.assertRaises(SignalGroupSchemaVersionError):
            load_signal_group(path)

    def test_unsupported(self):
        doc = _min_doc()
        doc["signal_group_schema_version"] = 99
        path = self._write(doc)
        with self.assertRaises(SignalGroupSchemaVersionError):
            load_signal_group(path)


class NameAndFilenameTests(TempDirMixin, unittest.TestCase):

    def test_bad_name(self):
        doc = _min_doc(name="Bad-Name")
        path = self._write(doc, name="Bad-Name")
        with self.assertRaises(SignalGroupLoadError):
            load_signal_group(path)

    def test_basename_mismatch(self):
        doc = _min_doc()
        path = self._write(doc, name="not_outs")
        with self.assertRaises(SignalGroupLoadError):
            load_signal_group(path)

    def test_wrong_suffix(self):
        doc = _min_doc()
        path = self.tmp / "outs.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        with self.assertRaises(SignalGroupLoadError):
            load_signal_group(path)


class SignalsTests(TempDirMixin, unittest.TestCase):

    def test_missing_signals(self):
        doc = _min_doc()
        del doc["signals"]
        path = self._write(doc)
        with self.assertRaises(SignalGroupLoadError):
            load_signal_group(path)

    def test_empty_signals_rejected(self):
        doc = _min_doc()
        doc["signals"] = []
        path = self._write(doc)
        with self.assertRaises(SignalGroupLoadError):
            load_signal_group(path)

    def test_path_without_leading_slash_rejected(self):
        doc = _min_doc()
        doc["signals"] = ["Vout"]
        path = self._write(doc)
        with self.assertRaises(SignalGroupLoadError):
            load_signal_group(path)

    def test_duplicates_rejected(self):
        doc = _min_doc()
        doc["signals"] = ["/Vout", "/Vout"]
        path = self._write(doc)
        with self.assertRaises(SignalGroupLoadError):
            load_signal_group(path)

    def test_non_string_element_rejected(self):
        doc = _min_doc()
        doc["signals"] = ["/Vout", 42]
        path = self._write(doc)
        with self.assertRaises(SignalGroupLoadError):
            load_signal_group(path)

    def test_signals_not_array_rejected(self):
        doc = _min_doc()
        doc["signals"] = "/Vout"
        path = self._write(doc)
        with self.assertRaises(SignalGroupLoadError):
            load_signal_group(path)


class MalformedTests(TempDirMixin, unittest.TestCase):

    def test_not_json(self):
        path = self.tmp / "outs.siggroup.json"
        path.write_text("not json", encoding="utf-8")
        with self.assertRaises(SignalGroupMalformedError):
            load_signal_group(path)

    def test_not_object(self):
        path = self._write("[1, 2, 3]")
        with self.assertRaises(SignalGroupMalformedError):
            load_signal_group(path)


class AliasFormTests(TempDirMixin, unittest.TestCase):
    """v2 — items may be {net, alias} objects (DECISIONS #49)."""

    def _v2_doc(self, signals: list) -> dict:
        return {
            "signal_group_schema_version": 2,
            "name": "outs",
            "signals": signals,
        }

    def test_alias_form_loads(self):
        path = self._write(self._v2_doc([
            {"net": "/I_A/VDD", "alias": "vdd_a"},
            {"net": "/I_B/VDD", "alias": "vdd_b"},
        ]))
        sg = load_signal_group(path)
        self.assertEqual(sg.signal_group_schema_version, 2)
        self.assertEqual(
            [(s.net, s.alias) for s in sg.signals],
            [("/I_A/VDD", "vdd_a"), ("/I_B/VDD", "vdd_b")],
        )
        self.assertEqual(sg.signals[0].output_basename, "vdd_a")

    def test_alias_form_mix_with_bare_strings(self):
        path = self._write(self._v2_doc([
            "/Vout",
            {"net": "/I_A/VDD", "alias": "vdd_a"},
        ]))
        sg = load_signal_group(path)
        self.assertIsNone(sg.signals[0].alias)
        self.assertEqual(sg.signals[0].output_basename, "Vout")
        self.assertEqual(sg.signals[1].alias, "vdd_a")

    def test_alias_form_rejected_in_v1(self):
        doc = self._v2_doc([{"net": "/X", "alias": "x"}])
        doc["signal_group_schema_version"] = 1
        path = self._write(doc)
        with self.assertRaisesRegex(
            SignalGroupLoadError, r"requires 'signal_group_schema_version': 2"
        ):
            load_signal_group(path)

    def test_alias_optional_in_dict_form(self):
        # {net: ...} with no alias is allowed; falls back to basename.
        path = self._write(self._v2_doc([{"net": "/Vout"}]))
        sg = load_signal_group(path)
        self.assertIsNone(sg.signals[0].alias)
        self.assertEqual(sg.signals[0].output_basename, "Vout")

    def test_alias_null_treated_as_missing(self):
        path = self._write(self._v2_doc([{"net": "/Vout", "alias": None}]))
        sg = load_signal_group(path)
        self.assertIsNone(sg.signals[0].alias)

    def test_alias_bad_identifier_rejected(self):
        path = self._write(self._v2_doc(
            [{"net": "/X", "alias": "1bad"}]
        ))
        with self.assertRaisesRegex(SignalGroupLoadError, r"alias.*must match"):
            load_signal_group(path)

    def test_alias_with_slash_rejected(self):
        path = self._write(self._v2_doc(
            [{"net": "/X", "alias": "a/b"}]
        ))
        with self.assertRaisesRegex(SignalGroupLoadError, r"alias.*must match"):
            load_signal_group(path)

    def test_alias_duplicate_rejected(self):
        path = self._write(self._v2_doc([
            {"net": "/I_A/VDD", "alias": "vdd"},
            {"net": "/I_B/VDD", "alias": "vdd"},
        ]))
        with self.assertRaisesRegex(SignalGroupLoadError, r"duplicates earlier alias"):
            load_signal_group(path)

    def test_dict_form_missing_net_rejected(self):
        path = self._write(self._v2_doc([{"alias": "x"}]))
        with self.assertRaisesRegex(SignalGroupLoadError, r"'net' must be a string"):
            load_signal_group(path)

    def test_dict_form_unknown_key_rejected(self):
        path = self._write(self._v2_doc(
            [{"net": "/X", "alias": "x", "extra": "junk"}]
        ))
        with self.assertRaisesRegex(SignalGroupLoadError, r"unknown keys"):
            load_signal_group(path)

    def test_net_duplicate_across_forms_rejected(self):
        path = self._write(self._v2_doc([
            "/VDD",
            {"net": "/VDD", "alias": "vdd2"},
        ]))
        with self.assertRaisesRegex(SignalGroupLoadError, r"duplicates earlier net"):
            load_signal_group(path)


class SignalBasenameTests(unittest.TestCase):

    def test_simple(self):
        self.assertEqual(signal_basename("/Vout"), "Vout")

    def test_nested(self):
        self.assertEqual(signal_basename("/buf/y"), "y")

    def test_deep_nested(self):
        self.assertEqual(signal_basename("/X/Y/Z/clkout"), "clkout")

    def test_no_leading_slash_raises(self):
        with self.assertRaises(ValueError):
            signal_basename("Vout")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
