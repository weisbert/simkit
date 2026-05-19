"""Unit tests for simkit.project (`.pvtproject` loader).

Run with stdlib unittest (no pytest; red-zone target is offline):

    PYTHONPATH=python python3.11 -m unittest tests.test_project_loader -v
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
import warnings
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.project import (  # noqa: E402
    ENV_VAR,
    PVTPROJECT_FILENAME,
    PvtProjectNotFoundError,
    PvtProjectValidationError,
    find_pvtproject,
    load_pvtproject,
)


MIN_VALID = {
    "project": "my_ldo",
    "dbRoot": "./simkit_data",
}


def _write(dir_: Path, content) -> Path:
    path = dir_ / PVTPROJECT_FILENAME
    if isinstance(content, (dict, list)):
        path.write_text(json.dumps(content), encoding="utf-8")
    else:
        path.write_text(content, encoding="utf-8")
    return path


class TempDirMixin:
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_test_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class FindPvtProjectTests(TempDirMixin, unittest.TestCase):

    def test_walks_up_from_cwd(self):
        _write(self.tmp, MIN_VALID)
        deep = self.tmp / "a" / "b" / "c"
        deep.mkdir(parents=True)
        found = find_pvtproject(start=deep)
        self.assertEqual(found, (self.tmp / PVTPROJECT_FILENAME).resolve())

    def test_returns_none_when_not_found(self):
        # Use an isolated tmp dir that has no ancestor .pvtproject
        # (we can't truly prevent a .pvtproject existing somewhere on the host,
        # but the walker only finds the *nearest*; start deep under self.tmp
        # and assert that if any ancestor had one it would be outside self.tmp).
        deep = self.tmp / "a" / "b"
        deep.mkdir(parents=True)
        found = find_pvtproject(start=deep)
        # Either None (clean) or something strictly above self.tmp (host junk).
        if found is not None:
            self.assertNotIn(str(self.tmp.resolve()), str(found))

    def test_start_is_a_file(self):
        _write(self.tmp, MIN_VALID)
        a_file = self.tmp / "some_file.txt"
        a_file.write_text("", encoding="utf-8")
        found = find_pvtproject(start=a_file)
        self.assertEqual(found, (self.tmp / PVTPROJECT_FILENAME).resolve())


class LoaderFallbackTests(TempDirMixin, unittest.TestCase):

    def test_env_var_wins_over_walker(self):
        # Two files; env points to the far one, walker would pick the near one.
        far = self.tmp / "far"
        far.mkdir()
        _write(far, {**MIN_VALID, "project": "far_proj"})

        near = self.tmp / "near" / "deep"
        near.mkdir(parents=True)
        _write(near.parent, {**MIN_VALID, "project": "near_proj"})

        env = {ENV_VAR: str(far / PVTPROJECT_FILENAME)}
        p = load_pvtproject(start=near, env=env)
        self.assertEqual(p.project, "far_proj")

    def test_env_var_missing_file_is_hard_error(self):
        env = {ENV_VAR: "/nonexistent/.pvtproject"}
        with self.assertRaises(PvtProjectNotFoundError):
            load_pvtproject(start=self.tmp, env=env)

    def test_walker_found(self):
        _write(self.tmp, MIN_VALID)
        deep = self.tmp / "x" / "y"
        deep.mkdir(parents=True)
        p = load_pvtproject(start=deep, env={})
        self.assertEqual(p.project, "my_ldo")

    def test_no_env_no_file_raises(self):
        deep = self.tmp / "x" / "y"
        deep.mkdir(parents=True)
        # Use a chroot-style guard: only fails cleanly if no ancestor has one.
        try:
            load_pvtproject(start=deep, env={})
        except PvtProjectNotFoundError:
            return
        # If it didn't raise, a host-level .pvtproject exists above us — skip.
        self.skipTest("host filesystem has an ancestor .pvtproject")


class ValidationTests(TempDirMixin, unittest.TestCase):

    def _load(self, doc_or_text):
        _write(self.tmp, doc_or_text)
        return load_pvtproject(start=self.tmp, env={})

    def test_minimum_valid(self):
        p = self._load(MIN_VALID)
        self.assertEqual(p.project, "my_ldo")
        self.assertEqual(p.db_root, (self.tmp / "simkit_data").resolve())
        self.assertIsNone(p.author)
        self.assertEqual(dict(p.testbench_aliases), {})
        self.assertEqual(p.schema_version, 1)

    def test_malformed_json(self):
        with self.assertRaises(PvtProjectValidationError):
            self._load("{not json")

    def test_top_level_not_object(self):
        with self.assertRaises(PvtProjectValidationError):
            self._load([1, 2, 3])

    def test_missing_project(self):
        doc = {k: v for k, v in MIN_VALID.items() if k != "project"}
        with self.assertRaises(PvtProjectValidationError) as cm:
            self._load(doc)
        self.assertIn("project", str(cm.exception))

    def test_missing_dbroot(self):
        doc = {k: v for k, v in MIN_VALID.items() if k != "dbRoot"}
        with self.assertRaises(PvtProjectValidationError) as cm:
            self._load(doc)
        self.assertIn("dbRoot", str(cm.exception))

    def test_project_regex(self):
        # Spaces / punctuation still rejected.
        with self.assertRaises(PvtProjectValidationError):
            self._load({**MIN_VALID, "project": "Has Spaces"})
        with self.assertRaises(PvtProjectValidationError):
            self._load({**MIN_VALID, "project": "with.dots"})
        with self.assertRaises(PvtProjectValidationError):
            self._load({**MIN_VALID, "project": ""})
        # Uppercase IS allowed (Phase 4 production names like "1AXX",
        # "NDIV_pre_FDR" — engineers should not be forced to lowercase).
        self.assertEqual(self._load({**MIN_VALID, "project": "1AXX"}).project, "1AXX")
        self.assertEqual(
            self._load({**MIN_VALID, "project": "UpperCase"}).project, "UpperCase"
        )
        # Other valid edge cases
        self.assertEqual(self._load({**MIN_VALID, "project": "a"}).project, "a")
        self.assertEqual(
            self._load({**MIN_VALID, "project": "my-proj_01"}).project,
            "my-proj_01",
        )

    def test_project_wrong_type(self):
        with self.assertRaises(PvtProjectValidationError):
            self._load({**MIN_VALID, "project": 123})

    def test_dbroot_absolute(self):
        abs_dir = self.tmp / "abs_db"
        p = self._load({**MIN_VALID, "dbRoot": str(abs_dir)})
        self.assertEqual(p.db_root, abs_dir.resolve())

    def test_dbroot_empty_string(self):
        with self.assertRaises(PvtProjectValidationError):
            self._load({**MIN_VALID, "dbRoot": ""})

    def test_dbroot_wrong_type(self):
        with self.assertRaises(PvtProjectValidationError):
            self._load({**MIN_VALID, "dbRoot": 42})

    def test_author_string(self):
        p = self._load({**MIN_VALID, "author": "yusheng"})
        self.assertEqual(p.author, "yusheng")

    def test_author_null_is_none(self):
        p = self._load({**MIN_VALID, "author": None})
        self.assertIsNone(p.author)

    def test_author_wrong_type(self):
        with self.assertRaises(PvtProjectValidationError):
            self._load({**MIN_VALID, "author": 7})

    def test_aliases_ok(self):
        aliases = {"LIB/cell1/schematic": "one", "LIB/cell2/schematic": "two"}
        p = self._load({**MIN_VALID, "testbench_aliases": aliases})
        self.assertEqual(dict(p.testbench_aliases), aliases)

    def test_aliases_duplicate_value(self):
        aliases = {"LIB/a/schematic": "same", "LIB/b/schematic": "same"}
        with self.assertRaises(PvtProjectValidationError) as cm:
            self._load({**MIN_VALID, "testbench_aliases": aliases})
        self.assertIn("duplicate alias", str(cm.exception))

    def test_aliases_wrong_type(self):
        with self.assertRaises(PvtProjectValidationError):
            self._load({**MIN_VALID, "testbench_aliases": ["nope"]})
        with self.assertRaises(PvtProjectValidationError):
            self._load({**MIN_VALID, "testbench_aliases": {"k": 1}})

    def test_schema_version_supported(self):
        p = self._load({**MIN_VALID, "schema_version": 1})
        self.assertEqual(p.schema_version, 1)

    def test_schema_version_unsupported(self):
        with self.assertRaises(PvtProjectValidationError):
            self._load({**MIN_VALID, "schema_version": 999})

    def test_schema_version_bool_rejected(self):
        with self.assertRaises(PvtProjectValidationError):
            self._load({**MIN_VALID, "schema_version": True})

    def test_schema_version_wrong_type(self):
        with self.assertRaises(PvtProjectValidationError):
            self._load({**MIN_VALID, "schema_version": "1"})

    def test_unknown_key_warns_but_loads(self):
        doc = {**MIN_VALID, "totally_new_field": "hi"}
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            p = self._load(doc)
        self.assertEqual(p.project, "my_ldo")
        self.assertTrue(
            any("totally_new_field" in str(w.message) for w in caught),
            f"expected warning about unknown key, got: {[str(w.message) for w in caught]}",
        )

    def test_underscore_key_silent(self):
        doc = {**MIN_VALID, "_doc": "this is a comment", "_note": {"k": "v"}}
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            p = self._load(doc)
        self.assertEqual(p.project, "my_ldo")
        self.assertFalse(
            any("_doc" in str(w.message) or "_note" in str(w.message) for w in caught),
            "underscore keys should not warn",
        )


class ExampleFileTests(unittest.TestCase):
    """The bundled example file must load cleanly under the current schema."""

    def test_bundled_example_loads(self):
        tmp = Path(tempfile.mkdtemp(prefix="simkit_example_"))
        try:
            example = _REPO_ROOT / "config" / "pvtproject.example.json"
            shutil.copy(example, tmp / PVTPROJECT_FILENAME)
            with warnings.catch_warnings():
                warnings.simplefilter("error")  # example must not warn
                p = load_pvtproject(start=tmp, env={})
            self.assertEqual(p.project, "my_ldo")
            self.assertEqual(p.author, "yusheng")
            self.assertEqual(
                p.alias_for("MY_LIB/ldo_top_tb/schematic"), "ldo_heavy_tb"
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
