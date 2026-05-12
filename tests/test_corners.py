"""Unit tests for simkit.corners (pure logic behind ``pvt corners`` CLI)."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.corners import (  # noqa: E402
    UnionDiff,
    UnionDiffChange,
    UnionListing,
    diff_unions,
    list_unions,
    resolve_unions_dir,
)
from simkit.union import load_union  # noqa: E402


_EXAMPLE_FILE = _REPO_ROOT / "config" / "pvt_union_example.union.json"


def _min_doc(name: str, *, vdd_sweep=None, section_sweep=None) -> dict:
    vars_block = {"temperature": "55"}
    if vdd_sweep is not None:
        vars_block["VDD"] = vdd_sweep
    section = section_sweep if section_sweep is not None else "tt"
    return {
        "union_schema_version": 1,
        "name": name,
        "project": "my_ldo",
        "testbench_id": "MY_LIB/ldo_top_tb/schematic",
        "rows": [
            {
                "row_name": "TT",
                "vars": vars_block,
                "models": [
                    {
                        "file": "rf018.scs",
                        "block": "Global",
                        "test": "All",
                        "section": section,
                    }
                ],
            }
        ],
    }


class TempDirMixin:
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_corners_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_union(self, doc, name: str) -> Path:
        path = self.tmp / f"{name}.union.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        return path


class ListUnionsTests(TempDirMixin, unittest.TestCase):

    def test_empty_dir_returns_empty_list(self):
        self.assertEqual(list_unions(self.tmp), [])

    def test_nonexistent_dir_returns_empty_list(self):
        self.assertEqual(list_unions(self.tmp / "nope"), [])

    def test_single_valid_union(self):
        self._write_union(_min_doc("u_one"), "u_one")
        listings = list_unions(self.tmp)
        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0].name, "u_one")
        self.assertEqual(listings[0].project, "my_ldo")
        self.assertEqual(listings[0].row_count, 1)
        self.assertEqual(listings[0].sub_corner_count, 1)
        self.assertIsNone(listings[0].error)

    def test_malformed_json_yields_error_listing(self):
        bad = self.tmp / "broken.union.json"
        bad.write_text("{not json", encoding="utf-8")
        listings = list_unions(self.tmp)
        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0].name, "broken")
        self.assertIsNone(listings[0].row_count)
        self.assertIsNotNone(listings[0].error)

    def test_name_basename_mismatch_yields_error_listing(self):
        # File on disk: foo.union.json, but doc declares name "bar".
        doc = _min_doc("bar")
        path = self.tmp / "foo.union.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        listings = list_unions(self.tmp)
        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0].name, "foo")
        self.assertIsNotNone(listings[0].error)

    def test_multi_union_sorted_by_filename(self):
        self._write_union(_min_doc("zeta"), "zeta")
        self._write_union(_min_doc("alpha"), "alpha")
        self._write_union(_min_doc("m_id"), "m_id")
        listings = list_unions(self.tmp)
        self.assertEqual([l.name for l in listings], ["alpha", "m_id", "zeta"])

    def test_explode_count_for_sweeps(self):
        # 2 VDDs x 3 sections = 6.
        doc = _min_doc(
            "u_sweep",
            vdd_sweep=["3", "2.8"],
            section_sweep=["tt", "ss", "ff"],
        )
        self._write_union(doc, "u_sweep")
        listings = list_unions(self.tmp)
        self.assertEqual(listings[0].sub_corner_count, 6)


class ResolveUnionsDirTests(TempDirMixin, unittest.TestCase):

    def _write_pvtproject(self, payload: dict) -> Path:
        p = self.tmp / ".pvtproject"
        p.write_text(json.dumps(payload), encoding="utf-8")
        return p

    def test_default_is_unions_subdir(self):
        path = self._write_pvtproject({
            "project": "my_ldo",
            "dbRoot": "./db",
        })
        resolved = resolve_unions_dir(path)
        self.assertEqual(resolved, (self.tmp / "unions").resolve())

    def test_explicit_relative_unionsDir(self):
        path = self._write_pvtproject({
            "project": "my_ldo",
            "dbRoot": "./db",
            "unionsDir": "configs/u",
        })
        resolved = resolve_unions_dir(path)
        self.assertEqual(resolved, (self.tmp / "configs/u").resolve())

    def test_explicit_absolute_unionsDir(self):
        abspath = self.tmp / "abs_unions"
        path = self._write_pvtproject({
            "project": "my_ldo",
            "dbRoot": "./db",
            "unionsDir": str(abspath),
        })
        resolved = resolve_unions_dir(path)
        self.assertEqual(resolved, abspath.resolve())


class DiffUnionsTests(unittest.TestCase):

    def _load_example(self):
        return load_union(_EXAMPLE_FILE)

    def test_identical_unions(self):
        a = self._load_example()
        b = self._load_example()
        d = diff_unions(a, b)
        self.assertFalse(d.has_differences())
        self.assertEqual(d.added, ())
        self.assertEqual(d.removed, ())
        self.assertEqual(d.changed, ())
        self.assertEqual(d.identical_count, 2)

    def _make_pair(self, doc_a: dict, doc_b: dict):
        from simkit.union import load_union  # local import to avoid cycle
        # We need files on disk because the loader validates filename match.
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="simkit_diffpair_"))
        try:
            pa = tmp / f"{doc_a['name']}.union.json"
            pb = tmp / f"{doc_b['name']}.union.json"
            pa.write_text(json.dumps(doc_a), encoding="utf-8")
            pb.write_text(json.dumps(doc_b), encoding="utf-8")
            return load_union(pa), load_union(pb), tmp
        except Exception:
            shutil.rmtree(tmp, ignore_errors=True)
            raise

    def test_row_added_in_b(self):
        a_doc = _min_doc("u_a")
        b_doc = _min_doc("u_b")
        b_doc["rows"].append({
            "row_name": "FF",
            "vars": {"temperature": "85"},
        })
        a, b, tmp = self._make_pair(a_doc, b_doc)
        try:
            d = diff_unions(a, b)
            self.assertEqual(d.added, ("FF",))
            self.assertEqual(d.removed, ())
            self.assertEqual(d.changed, ())
            self.assertEqual(d.identical_count, 1)
            self.assertTrue(d.has_differences())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_row_removed_from_b(self):
        a_doc = _min_doc("u_a")
        a_doc["rows"].append({
            "row_name": "FF",
            "vars": {"temperature": "85"},
        })
        b_doc = _min_doc("u_b")
        a, b, tmp = self._make_pair(a_doc, b_doc)
        try:
            d = diff_unions(a, b)
            self.assertEqual(d.added, ())
            self.assertEqual(d.removed, ("FF",))
            self.assertEqual(d.changed, ())
            self.assertEqual(d.identical_count, 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_var_changed_scalar_to_sweep(self):
        a_doc = _min_doc("u_a")
        b_doc = _min_doc("u_b", vdd_sweep=["3", "2.8", "3.3"])
        a, b, tmp = self._make_pair(a_doc, b_doc)
        try:
            d = diff_unions(a, b)
            self.assertEqual(d.added, ())
            self.assertEqual(d.removed, ())
            self.assertEqual(d.identical_count, 0)
            self.assertEqual(len(d.changed), 1)
            c = d.changed[0]
            self.assertEqual(c.row_name, "TT")
            self.assertEqual(c.field, "vars.VDD")
            self.assertIsNone(c.a)
            self.assertEqual(c.b, ["3", "2.8", "3.3"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_model_section_changed(self):
        a_doc = _min_doc("u_a", section_sweep=["tt"])
        b_doc = _min_doc("u_b", section_sweep=["tt", "ss"])
        a, b, tmp = self._make_pair(a_doc, b_doc)
        try:
            d = diff_unions(a, b)
            self.assertEqual(len(d.changed), 1)
            c = d.changed[0]
            self.assertEqual(c.row_name, "TT")
            self.assertEqual(c.field, "models[0].section")
            self.assertEqual(c.a, ["tt"])
            self.assertEqual(c.b, ["tt", "ss"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_model_added(self):
        a_doc = _min_doc("u_a")
        b_doc = _min_doc("u_b")
        b_doc["rows"][0]["models"].append({
            "file": "extra.scs",
            "section": "tt",
        })
        a, b, tmp = self._make_pair(a_doc, b_doc)
        try:
            d = diff_unions(a, b)
            self.assertEqual(len(d.changed), 1)
            c = d.changed[0]
            self.assertEqual(c.field, "models[1]")
            self.assertIsNone(c.a)
            self.assertEqual(c.b["file"], "extra.scs")
            self.assertEqual(c.b["section"], ["tt"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
