"""Unit tests for simkit.union (`.union.json` loader + explode).

Run with stdlib unittest or pytest:

    PYTHONPATH=python python3.11 -m unittest tests.test_union -v
    python3.11 -m pytest tests/test_union.py -v
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

from simkit.union import (  # noqa: E402
    FrozenModelEntry,
    ModelEntry,
    SubCorner,
    Union,
    UnionMalformedError,
    UnionRow,
    UnionSchemaVersionError,
    UnionValidationError,
    explode,
    load_union,
)


_EXAMPLE_FILE = _REPO_ROOT / "config" / "pvt_union_example.union.json"


def _min_doc(name: str = "pvt_extended") -> dict:
    return {
        "union_schema_version": 1,
        "name": name,
        "project": "my_ldo",
        "testbench_id": "MY_LIB/ldo_top_tb/schematic",
        "rows": [
            {
                "row_name": "TT",
                "vars": {"temperature": "55"},
            }
        ],
    }


class TempDirMixin:
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_union_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, doc, *, name: str = "pvt_extended") -> Path:
        path = self.tmp / f"{name}.union.json"
        if isinstance(doc, str):
            path.write_text(doc, encoding="utf-8")
        else:
            path.write_text(json.dumps(doc), encoding="utf-8")
        return path


class HappyPathTests(unittest.TestCase):

    def test_example_loads(self):
        u = load_union(_EXAMPLE_FILE)
        self.assertIsInstance(u, Union)
        self.assertEqual(u.union_schema_version, 1)
        self.assertEqual(u.name, "pvt_union_example")
        self.assertEqual(u.project, "my_ldo")
        self.assertEqual(u.testbench_id, "sim_yusheng/Test/maestro")

    def test_example_row_count(self):
        u = load_union(_EXAMPLE_FILE)
        self.assertEqual(len(u.rows), 2)

    def test_example_tt_row_shape(self):
        u = load_union(_EXAMPLE_FILE)
        tt = u.rows[0]
        self.assertEqual(tt.row_name, "TT")
        self.assertEqual(tt.vars, {"temperature": ("55",)})
        self.assertEqual(len(tt.models), 1)

    def test_example_tt_model_shape(self):
        u = load_union(_EXAMPLE_FILE)
        m = u.rows[0].models[0]
        self.assertEqual(m.file, "rf018.scs")
        self.assertEqual(m.block, "Global")
        self.assertEqual(m.test, "All")
        self.assertEqual(m.section, ("tt",))

    def test_example_tt_pvt_row_shape(self):
        u = load_union(_EXAMPLE_FILE)
        r = u.rows[1]
        self.assertEqual(r.row_name, "TT_pvt")
        self.assertEqual(r.vars, {"temperature": ("55",), "VDD": ("3", "2.8")})
        self.assertEqual(len(r.models), 1)
        self.assertEqual(r.models[0].section, ("tt", "ss", "ff"))


class SchemaVersionTests(TempDirMixin, unittest.TestCase):

    def test_missing_schema_version(self):
        doc = _min_doc()
        del doc["union_schema_version"]
        path = self._write(doc)
        with self.assertRaises(UnionSchemaVersionError):
            load_union(path)

    def test_schema_version_wrong_type(self):
        doc = {**_min_doc(), "union_schema_version": "1"}
        path = self._write(doc)
        with self.assertRaises(UnionSchemaVersionError):
            load_union(path)

    def test_schema_version_bool_rejected(self):
        doc = {**_min_doc(), "union_schema_version": True}
        path = self._write(doc)
        with self.assertRaises(UnionSchemaVersionError):
            load_union(path)

    def test_schema_version_unsupported_value(self):
        doc = {**_min_doc(), "union_schema_version": 2}
        path = self._write(doc)
        with self.assertRaises(UnionSchemaVersionError):
            load_union(path)


class RequiredFieldsTests(TempDirMixin, unittest.TestCase):

    def test_missing_name(self):
        doc = _min_doc()
        del doc["name"]
        path = self._write(doc)
        with self.assertRaises(UnionValidationError):
            load_union(path)

    def test_missing_project(self):
        doc = _min_doc()
        del doc["project"]
        path = self._write(doc)
        with self.assertRaises(UnionValidationError):
            load_union(path)

    def test_missing_testbench_id(self):
        doc = _min_doc()
        del doc["testbench_id"]
        path = self._write(doc)
        with self.assertRaises(UnionValidationError):
            load_union(path)

    def test_missing_rows(self):
        doc = _min_doc()
        del doc["rows"]
        path = self._write(doc)
        with self.assertRaises(UnionValidationError):
            load_union(path)


class TopLevelShapeTests(TempDirMixin, unittest.TestCase):

    def test_malformed_json(self):
        path = self.tmp / "pvt_extended.union.json"
        path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(UnionMalformedError):
            load_union(path)

    def test_top_level_not_object(self):
        path = self.tmp / "pvt_extended.union.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        with self.assertRaises(UnionMalformedError):
            load_union(path)

    def test_name_mismatch_basename(self):
        doc = _min_doc(name="something_else")
        path = self._write(doc, name="pvt_extended")
        with self.assertRaises(UnionValidationError) as cm:
            load_union(path)
        self.assertIn("name", str(cm.exception))

    def test_rows_empty_array(self):
        doc = {**_min_doc(), "rows": []}
        path = self._write(doc)
        with self.assertRaises(UnionValidationError):
            load_union(path)


class RowValidationTests(TempDirMixin, unittest.TestCase):

    def test_row_empty_vars_and_models(self):
        doc = {**_min_doc(), "rows": [{"row_name": "TT", "vars": {}, "models": []}]}
        path = self._write(doc)
        with self.assertRaises(UnionValidationError) as cm:
            load_union(path)
        self.assertIn("at least one", str(cm.exception))

    def test_row_missing_row_name(self):
        doc = {**_min_doc(), "rows": [{"vars": {"VDD": "3"}}]}
        path = self._write(doc)
        with self.assertRaises(UnionValidationError):
            load_union(path)

    def test_row_name_regex_uppercase_start_ok(self):
        doc = {**_min_doc(), "rows": [{"row_name": "TT_pvt_1", "vars": {"VDD": "3"}}]}
        path = self._write(doc)
        u = load_union(path)
        self.assertEqual(u.rows[0].row_name, "TT_pvt_1")

    def test_row_name_starts_with_digit_rejected(self):
        doc = {**_min_doc(), "rows": [{"row_name": "1TT", "vars": {"VDD": "3"}}]}
        path = self._write(doc)
        with self.assertRaises(UnionValidationError):
            load_union(path)

    def test_row_name_has_hyphen_rejected(self):
        doc = {**_min_doc(), "rows": [{"row_name": "TT-1", "vars": {"VDD": "3"}}]}
        path = self._write(doc)
        with self.assertRaises(UnionValidationError):
            load_union(path)

    def test_row_name_duplicate(self):
        doc = {
            **_min_doc(),
            "rows": [
                {"row_name": "TT", "vars": {"VDD": "3"}},
                {"row_name": "TT", "vars": {"VDD": "5"}},
            ],
        }
        path = self._write(doc)
        with self.assertRaises(UnionValidationError) as cm:
            load_union(path)
        self.assertIn("duplicate", str(cm.exception))


class VarValueTests(TempDirMixin, unittest.TestCase):

    def test_scalar_becomes_one_tuple(self):
        doc = {**_min_doc(), "rows": [{"row_name": "TT", "vars": {"VDD": "3"}}]}
        path = self._write(doc)
        u = load_union(path)
        self.assertEqual(u.rows[0].vars["VDD"], ("3",))

    def test_length_one_array_stays_one_tuple_not_collapsed(self):
        doc = {**_min_doc(), "rows": [{"row_name": "TT", "vars": {"VDD": ["3"]}}]}
        path = self._write(doc)
        u = load_union(path)
        self.assertEqual(u.rows[0].vars["VDD"], ("3",))
        self.assertIn("VDD", u.rows[0].sweep_var_keys)

    def test_empty_array_rejected(self):
        doc = {**_min_doc(), "rows": [{"row_name": "TT", "vars": {"VDD": []}}]}
        path = self._write(doc)
        with self.assertRaises(UnionValidationError):
            load_union(path)

    def test_mixed_type_array_rejected(self):
        doc = {**_min_doc(), "rows": [{"row_name": "TT", "vars": {"VDD": [3, "2.8"]}}]}
        path = self._write(doc)
        with self.assertRaises(UnionValidationError):
            load_union(path)

    def test_numeric_value_rejected(self):
        doc = {**_min_doc(), "rows": [{"row_name": "TT", "vars": {"VDD": 3}}]}
        path = self._write(doc)
        with self.assertRaises(UnionValidationError):
            load_union(path)

    def test_var_name_regex_rejects_leading_digit(self):
        doc = {**_min_doc(), "rows": [{"row_name": "TT", "vars": {"1VDD": "3"}}]}
        path = self._write(doc)
        with self.assertRaises(UnionValidationError):
            load_union(path)


class ModelEntryTests(TempDirMixin, unittest.TestCase):

    def test_model_file_required(self):
        doc = {
            **_min_doc(),
            "rows": [{"row_name": "TT", "models": [{"section": "tt"}]}],
        }
        path = self._write(doc)
        with self.assertRaises(UnionValidationError) as cm:
            load_union(path)
        self.assertIn("file", str(cm.exception))

    def test_model_section_required(self):
        doc = {
            **_min_doc(),
            "rows": [{"row_name": "TT", "models": [{"file": "rf018.scs"}]}],
        }
        path = self._write(doc)
        with self.assertRaises(UnionValidationError) as cm:
            load_union(path)
        self.assertIn("section", str(cm.exception))

    def test_model_block_test_defaults(self):
        doc = {
            **_min_doc(),
            "rows": [
                {
                    "row_name": "TT",
                    "models": [{"file": "rf018.scs", "section": "tt"}],
                }
            ],
        }
        path = self._write(doc)
        u = load_union(path)
        m = u.rows[0].models[0]
        self.assertEqual(m.block, "Global")
        self.assertEqual(m.test, "All")

    def test_model_section_length_one_array_stays_one_tuple(self):
        doc = {
            **_min_doc(),
            "rows": [
                {
                    "row_name": "TT",
                    "models": [{"file": "rf018.scs", "section": ["tt"]}],
                }
            ],
        }
        path = self._write(doc)
        u = load_union(path)
        self.assertEqual(u.rows[0].models[0].section, ("tt",))
        self.assertIn(0, u.rows[0].sweep_model_indices)

    def test_model_section_empty_array_rejected(self):
        doc = {
            **_min_doc(),
            "rows": [
                {
                    "row_name": "TT",
                    "models": [{"file": "rf018.scs", "section": []}],
                }
            ],
        }
        path = self._write(doc)
        with self.assertRaises(UnionValidationError):
            load_union(path)


class ExplodeExampleTests(unittest.TestCase):
    """The crucial spec §9 fixture — explode the example, assert exact 7 rows."""

    def test_explode_example_matches_spec_section_9(self):
        u = load_union(_EXAMPLE_FILE)
        got = explode(u)
        expected = [
            SubCorner(
                row_name="TT",
                sub_corner_name="TT",
                vars={"temperature": "55"},
                models=(
                    FrozenModelEntry(
                        file="rf018.scs", block="Global", test="All", section="tt"
                    ),
                ),
            ),
            SubCorner(
                row_name="TT_pvt",
                sub_corner_name="TT_pvt_0",
                vars={"temperature": "55", "VDD": "2.8"},
                models=(
                    FrozenModelEntry(
                        file="rf018.scs", block="Global", test="All", section="ff"
                    ),
                ),
            ),
            SubCorner(
                row_name="TT_pvt",
                sub_corner_name="TT_pvt_1",
                vars={"temperature": "55", "VDD": "3"},
                models=(
                    FrozenModelEntry(
                        file="rf018.scs", block="Global", test="All", section="ff"
                    ),
                ),
            ),
            SubCorner(
                row_name="TT_pvt",
                sub_corner_name="TT_pvt_2",
                vars={"temperature": "55", "VDD": "2.8"},
                models=(
                    FrozenModelEntry(
                        file="rf018.scs", block="Global", test="All", section="ss"
                    ),
                ),
            ),
            SubCorner(
                row_name="TT_pvt",
                sub_corner_name="TT_pvt_3",
                vars={"temperature": "55", "VDD": "3"},
                models=(
                    FrozenModelEntry(
                        file="rf018.scs", block="Global", test="All", section="ss"
                    ),
                ),
            ),
            SubCorner(
                row_name="TT_pvt",
                sub_corner_name="TT_pvt_4",
                vars={"temperature": "55", "VDD": "2.8"},
                models=(
                    FrozenModelEntry(
                        file="rf018.scs", block="Global", test="All", section="tt"
                    ),
                ),
            ),
            SubCorner(
                row_name="TT_pvt",
                sub_corner_name="TT_pvt_5",
                vars={"temperature": "55", "VDD": "3"},
                models=(
                    FrozenModelEntry(
                        file="rf018.scs", block="Global", test="All", section="tt"
                    ),
                ),
            ),
        ]
        self.assertEqual(got, expected)


class ExplodeArithmeticTests(TempDirMixin, unittest.TestCase):

    def _build_union(self, *, varA, varB, sections) -> Union:
        return Union(
            union_schema_version=1,
            name="probe",
            project="p",
            testbench_id="LIB/c/v",
            rows=(
                UnionRow(
                    row_name="R",
                    vars={"A": tuple(varA), "B": tuple(varB)},
                    models=(
                        ModelEntry(
                            file="m.scs",
                            block="Global",
                            test="All",
                            section=tuple(sections),
                        ),
                    ),
                    sweep_var_keys=frozenset({"A", "B"}),
                    sweep_model_indices=frozenset({0}),
                ),
            ),
        )

    def test_2x3x5_count(self):
        u = self._build_union(
            varA=["1", "2"],
            varB=["3", "4", "5"],
            sections=["X", "Y", "Z", "W", "V"],
        )
        sc = explode(u)
        self.assertEqual(len(sc), 30)

    def test_2x3x5_innermost_is_A(self):
        u = self._build_union(
            varA=["1", "2"],
            varB=["3", "4", "5"],
            sections=["X", "Y", "Z", "W", "V"],
        )
        sc = explode(u)
        self.assertEqual(sc[0].vars["A"], "1")
        self.assertEqual(sc[1].vars["A"], "2")
        self.assertEqual(sc[2].vars["A"], "1")

    def test_2x3x5_middle_is_B(self):
        u = self._build_union(
            varA=["1", "2"],
            varB=["3", "4", "5"],
            sections=["X", "Y", "Z", "W", "V"],
        )
        sc = explode(u)
        self.assertEqual(sc[0].vars["B"], "3")
        self.assertEqual(sc[1].vars["B"], "3")
        self.assertEqual(sc[2].vars["B"], "4")
        self.assertEqual(sc[6].vars["B"], "3")

    def test_2x3x5_outermost_is_section_lex_sorted(self):
        u = self._build_union(
            varA=["1", "2"],
            varB=["3", "4", "5"],
            sections=["X", "Y", "Z", "W", "V"],
        )
        sc = explode(u)
        # Lex order: V, W, X, Y, Z. Outermost = section changes every 6 entries.
        self.assertEqual(sc[0].models[0].section, "V")
        self.assertEqual(sc[6].models[0].section, "W")
        self.assertEqual(sc[12].models[0].section, "X")
        self.assertEqual(sc[18].models[0].section, "Y")
        self.assertEqual(sc[24].models[0].section, "Z")
        self.assertEqual(sc[29].models[0].section, "Z")

    def test_2x3x5_names_indexed(self):
        u = self._build_union(
            varA=["1", "2"],
            varB=["3", "4", "5"],
            sections=["X", "Y", "Z", "W", "V"],
        )
        sc = explode(u)
        self.assertEqual([s.sub_corner_name for s in sc[:3]], ["R_0", "R_1", "R_2"])
        self.assertEqual(sc[29].sub_corner_name, "R_29")


class ExplodeLexSortGotchaTests(unittest.TestCase):

    def test_numeric_strings_sort_lex_not_numeric(self):
        u = Union(
            union_schema_version=1,
            name="probe",
            project="p",
            testbench_id="LIB/c/v",
            rows=(
                UnionRow(
                    row_name="R",
                    vars={"X": ("10", "2", "3")},
                    models=(),
                    sweep_var_keys=frozenset({"X"}),
                    sweep_model_indices=frozenset(),
                ),
            ),
        )
        sc = explode(u)
        self.assertEqual(
            [s.vars["X"] for s in sc],
            ["10", "2", "3"],
            "lex-sort on strings places '10' before '2' — spec §3.4 caveat",
        )


# ----------------------------------------------------------------------------
# Schema extensions for Phase 2 CSV-build path (2026-05-13).
# Pull now captures `enabled` per row and `_file_abs` per model. Old
# sidecars must still load with sane defaults: enabled=True, file_abs=None.
# ----------------------------------------------------------------------------


def _doc_with_model(name: str = "for_extension") -> dict:
    return {
        "union_schema_version": 1,
        "name": name,
        "project": "my_ldo",
        "testbench_id": "MY_LIB/ldo_top_tb/schematic",
        "rows": [
            {
                "row_name": "TT",
                "vars": {"temperature": "55"},
                "models": [
                    {"file": "rf018.scs", "section": "tt"},
                ],
            },
            {
                "row_name": "TT_alt",
                "vars": {"temperature": "85"},
                "models": [
                    {"file": "rf018.scs", "section": "ss"},
                ],
            },
        ],
    }


class EnabledFieldTests(TempDirMixin, unittest.TestCase):

    def test_missing_enabled_defaults_to_true(self):
        doc = _doc_with_model()
        path = self.tmp / f"{doc['name']}.union.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        u = load_union(path)
        for row in u.rows:
            self.assertTrue(row.enabled, f"row {row.row_name!r} should default-enable")

    def test_enabled_false_round_trips(self):
        doc = _doc_with_model()
        doc["rows"][0]["enabled"] = False
        path = self.tmp / f"{doc['name']}.union.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        u = load_union(path)
        self.assertFalse(u.rows[0].enabled)
        self.assertTrue(u.rows[1].enabled)

    def test_non_bool_enabled_rejected(self):
        doc = _doc_with_model()
        doc["rows"][0]["enabled"] = "yes"
        path = self.tmp / f"{doc['name']}.union.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        with self.assertRaises(UnionValidationError) as ctx:
            load_union(path)
        self.assertIn("'enabled' must be a JSON boolean", str(ctx.exception))


class FileAbsFieldTests(TempDirMixin, unittest.TestCase):

    def test_missing_file_abs_defaults_to_none(self):
        doc = _doc_with_model()
        path = self.tmp / f"{doc['name']}.union.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        u = load_union(path)
        for row in u.rows:
            for m in row.models:
                self.assertIsNone(m.file_abs)

    def test_file_abs_loads(self):
        doc = _doc_with_model()
        for r in doc["rows"]:
            r["models"][0]["_file_abs"] = "/opt/pdk/models/rf018.scs"
        path = self.tmp / f"{doc['name']}.union.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        u = load_union(path)
        for row in u.rows:
            self.assertEqual(row.models[0].file_abs, "/opt/pdk/models/rf018.scs")

    def test_empty_file_abs_treated_as_unresolved(self):
        # Phase 4 1AXX dogfood: SKILL pull leaves _file_abs="" for multi-section
        # rows (`axlGetModelFile` can't disambiguate across sections). Empty is
        # informational — load_union should treat it as None (= "not resolved"),
        # not block the entire load. See DECISIONS #79 follow-up.
        doc = _doc_with_model()
        doc["rows"][0]["models"][0]["_file_abs"] = ""
        path = self.tmp / f"{doc['name']}.union.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        u = load_union(path)
        self.assertIsNone(u.rows[0].models[0].file_abs)

    def test_wrong_type_file_abs_rejected(self):
        # Non-string is still rejected — empty-string is a special semantic
        # case ("not resolved"), but a number or list is a real schema bug.
        doc = _doc_with_model()
        doc["rows"][0]["models"][0]["_file_abs"] = 42
        path = self.tmp / f"{doc['name']}.union.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        with self.assertRaises(UnionValidationError) as ctx:
            load_union(path)
        self.assertIn("must be a string if present", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
