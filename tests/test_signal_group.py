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
        self.assertEqual(sg.signals, ("/Vout",))

    def test_minimal_doc(self):
        path = self._write(_min_doc())
        sg = load_signal_group(path)
        self.assertEqual(sg.signal_group_schema_version, 1)
        self.assertEqual(sg.signals, ("/Vout",))

    def test_multi_signal_order_preserved(self):
        doc = _min_doc()
        doc["signals"] = ["/Vout", "/Vout2", "/buf/y"]
        path = self._write(doc)
        sg = load_signal_group(path)
        self.assertEqual(sg.signals, ("/Vout", "/Vout2", "/buf/y"))


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
