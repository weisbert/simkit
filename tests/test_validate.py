"""Unit tests for simkit.validate (invariant checker).

Coverage strategy: one positive test (the 42-row real-run fixture passes),
one negative test per error invariant I1–I24, one per warning W1/W2, and a
"broken in many ways" composite that asserts the validator surfaces every
violation in one pass (no fail-fast).

Run with stdlib unittest:

    PYTHONPATH=python python3.11 -m unittest tests.test_validate -v
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.validate import (  # noqa: E402
    Violation,
    validate_dump,
    validate_dump_file,
)


_FIXTURES = _REPO_ROOT / "tests" / "fixtures" / "runs"
_REAL_RUN = _FIXTURES / "bdc13f17-d39b-4a13-b58e-846435996a29" / "run.json"
_SYN_MIN = _FIXTURES / "synthetic_minimal" / "run.json"
_SYN_MESSY = _FIXTURES / "synthetic_messy" / "run.json"
_BAD_VERSION = _FIXTURES / "bad_version" / "run.json"
_BAD_STATUS = _FIXTURES / "bad_status" / "run.json"
_BAD_VALUE = _FIXTURES / "bad_value_when_failed" / "run.json"
_MALFORMED_JSON = _FIXTURES / "malformed_json" / "run.json"


def _good_dump() -> dict:
    """A valid v1 dump used as the starting template for negative tests.

    Each test mutates a fresh deepcopy and asserts the right violation.
    """
    with _SYN_MIN.open("r", encoding="utf-8") as f:
        return json.load(f)


def _codes(violations) -> list:
    return [v.code for v in violations]


def _errors(violations) -> list:
    return [v for v in violations if v.severity == "error"]


def _warnings(violations) -> list:
    return [v for v in violations if v.severity == "warning"]


class RealFixtureTests(unittest.TestCase):
    """The 42-row real-run JSON must surface only the W2 netlist warning."""

    def test_real_run_has_no_errors(self):
        violations = validate_dump_file(_REAL_RUN)
        errs = _errors(violations)
        self.assertEqual(
            errs, [],
            f"real fixture should have zero errors; got: "
            f"{[(v.code, v.path, v.message) for v in errs]}",
        )

    def test_real_run_emits_w2_for_null_netlist(self):
        violations = validate_dump_file(_REAL_RUN)
        warns = _warnings(violations)
        self.assertTrue(
            any(v.code == "W2" for v in warns),
            f"expected a W2 warning for null netlist_path; got: "
            f"{[(v.code, v.path) for v in warns]}",
        )

    def test_synthetic_minimal_is_clean(self):
        violations = validate_dump_file(_SYN_MIN)
        self.assertEqual(violations, [])

    def test_synthetic_messy_no_errors(self):
        violations = validate_dump_file(_SYN_MESSY)
        errs = _errors(violations)
        self.assertEqual(
            errs, [],
            f"synthetic_messy should be clean of errors; got: "
            f"{[(v.code, v.path, v.message) for v in errs]}",
        )


class TripleCoverageI1Tests(unittest.TestCase):
    """I1 — every (point, corner, test) has either ≥1 ok row or 1 sentinel."""

    def setUp(self):
        self.dump = _good_dump()

    def test_both_ok_and_sentinel_rejected(self):
        # Add a sentinel row for the same (point, corner, test) that
        # already has an ok row.
        sentinel = {
            "point": 1, "corner": "TT", "test": "Test",
            "output": "__sim_status__", "value": None,
            "status": "running", "sweep": {},
            "corner_vars": {"temperature": "27"}, "test_note": None,
        }
        self.dump["results"].append(sentinel)
        v = validate_dump(self.dump)
        self.assertIn("I1", _codes(v))

    def test_neither_ok_nor_sentinel_rejected(self):
        # Replace the ok row with a row that's neither 'ok' nor a sentinel
        # (status='ok' but rename to status='running' with a regular output
        # would be I14). Easier: drop everything and assert I1 fires when a
        # triple appears via a non-ok non-sentinel row would be malformed.
        # Instead: orphan a triple by giving it a bad status that we then
        # rewrite to make it parse but neither ok nor sentinel.
        # Simplest: a triple with a non-ok status on a regular output —
        # this fires I1 (no sentinel covering it) AND I14 (value mismatch).
        # We just assert I1 surfaces.
        self.dump["results"] = [
            {
                "point": 1, "corner": "TT", "test": "Test",
                "output": "vout",
                "value": None,
                "status": "failed",
                "sweep": {},
                "corner_vars": {"temperature": "27"},
                "test_note": None,
            }
        ]
        v = validate_dump(self.dump)
        self.assertIn("I1", _codes(v))

    def test_two_sentinels_per_triple_rejected(self):
        self.dump["results"] = [
            {
                "point": 1, "corner": "TT", "test": "Test",
                "output": "__sim_status__", "value": None,
                "status": "failed", "sweep": {},
                "corner_vars": {"temperature": "27"}, "test_note": None,
            },
            {
                "point": 1, "corner": "TT", "test": "Test",
                "output": "__sim_status__", "value": None,
                "status": "running", "sweep": {},
                "corner_vars": {"temperature": "27"}, "test_note": None,
            },
        ]
        v = validate_dump(self.dump)
        self.assertIn("I1", _codes(v))

    def test_sentinel_with_status_ok_rejected(self):
        # Sentinel + status='ok' is itself an I14 (ok cannot be sentinel)
        # AND I1 (sentinel must have a non-ok status).
        self.dump["results"] = [
            {
                "point": 1, "corner": "TT", "test": "Test",
                "output": "__sim_status__", "value": None,
                "status": "ok", "sweep": {},
                "corner_vars": {"temperature": "27"}, "test_note": None,
            }
        ]
        v = validate_dump(self.dump)
        codes = _codes(v)
        self.assertIn("I14", codes)
        # I1 also fires because there is no ok-row covering the triple
        # (the only row is a sentinel-shaped row with status=ok which we
        # don't count as an ok row given output==sentinel).
        self.assertIn("I1", codes)


class RunMetaTests(unittest.TestCase):
    """I2–I11 negative tests."""

    def setUp(self):
        self.dump = _good_dump()

    def test_i2_run_id_not_uuid(self):
        self.dump["run"]["run_id"] = "not-a-uuid"
        v = validate_dump(self.dump)
        self.assertIn("I2", _codes(v))

    def test_i3_project_id_bad_chars(self):
        self.dump["run"]["project_id"] = "Has Spaces"
        v = validate_dump(self.dump)
        self.assertIn("I3", _codes(v))

    def test_i4_testbench_id_not_lib_cell_view(self):
        self.dump["run"]["testbench_id"] = "only_two/parts"
        v = validate_dump(self.dump)
        self.assertIn("I4", _codes(v))

    def test_i4_testbench_id_empty_token(self):
        self.dump["run"]["testbench_id"] = "lib//view"
        v = validate_dump(self.dump)
        self.assertIn("I4", _codes(v))

    def test_i5_testbench_alias_empty_string(self):
        self.dump["run"]["testbench_alias"] = ""
        v = validate_dump(self.dump)
        self.assertIn("I5", _codes(v))

    def test_i5_testbench_alias_null_ok(self):
        self.dump["run"]["testbench_alias"] = None
        v = validate_dump(self.dump)
        self.assertNotIn("I5", _codes(v))

    def test_i6_timestamp_no_offset(self):
        self.dump["run"]["timestamp"] = "2026-05-10T12:00:00"
        v = validate_dump(self.dump)
        self.assertIn("I6", _codes(v))

    def test_i7_author_empty_string(self):
        self.dump["run"]["author"] = ""
        v = validate_dump(self.dump)
        self.assertIn("I7", _codes(v))

    def test_i8_label_wrong_type(self):
        self.dump["run"]["label"] = 7
        v = validate_dump(self.dump)
        self.assertIn("I8", _codes(v))

    def test_i9_note_wrong_type(self):
        self.dump["run"]["note"] = ["x"]
        v = validate_dump(self.dump)
        self.assertIn("I9", _codes(v))

    def test_i10_netlist_path_empty_string(self):
        self.dump["run"]["netlist_path"] = ""
        v = validate_dump(self.dump)
        self.assertIn("I10", _codes(v))

    def test_i11_history_name_empty_string(self):
        self.dump["run"]["history_name"] = ""
        v = validate_dump(self.dump)
        self.assertIn("I11", _codes(v))


class ResultsRowTests(unittest.TestCase):
    """I12–I18 negative tests."""

    def setUp(self):
        self.dump = _good_dump()

    def test_i12_status_outside_enum(self):
        self.dump["results"][0]["status"] = "potato"
        v = validate_dump(self.dump)
        self.assertIn("I12", _codes(v))

    def test_i13_output_empty(self):
        self.dump["results"][0]["output"] = ""
        v = validate_dump(self.dump)
        self.assertIn("I13", _codes(v))

    def test_i14_value_null_when_status_ok(self):
        self.dump["results"][0]["value"] = None
        v = validate_dump(self.dump)
        self.assertIn("I14", _codes(v))

    def test_i14_value_set_when_status_failed(self):
        self.dump["results"][0]["status"] = "failed"
        self.dump["results"][0]["value"] = 1.0
        # convert to sentinel form so I1 doesn't fire on its own
        self.dump["results"][0]["output"] = "__sim_status__"
        v = validate_dump(self.dump)
        self.assertIn("I14", _codes(v))

    def test_i15_point_negative(self):
        self.dump["results"][0]["point"] = -1
        v = validate_dump(self.dump)
        self.assertIn("I15", _codes(v))

    def test_i15_point_wrong_type(self):
        self.dump["results"][0]["point"] = "1"
        v = validate_dump(self.dump)
        self.assertIn("I15", _codes(v))

    def test_i16_corner_empty(self):
        self.dump["results"][0]["corner"] = ""
        v = validate_dump(self.dump)
        self.assertIn("I16", _codes(v))

    def test_i16_test_empty(self):
        self.dump["results"][0]["test"] = ""
        v = validate_dump(self.dump)
        self.assertIn("I16", _codes(v))

    def test_i17_sweep_not_dict(self):
        self.dump["results"][0]["sweep"] = ["x", "y"]
        v = validate_dump(self.dump)
        self.assertIn("I17", _codes(v))

    def test_i17_corner_vars_not_dict(self):
        self.dump["results"][0]["corner_vars"] = "T=27"
        v = validate_dump(self.dump)
        self.assertIn("I17", _codes(v))

    def test_i18_test_note_wrong_type(self):
        self.dump["results"][0]["test_note"] = 5
        v = validate_dump(self.dump)
        self.assertIn("I18", _codes(v))


class ArtifactsTests(unittest.TestCase):
    """I19–I22 negative tests."""

    def setUp(self):
        self.dump = _good_dump()
        self.dump["artifacts"].append({
            "type": "waveform",
            "relative_path": "artifacts/x.psf",
            "description": "ok",
            "source": "auto",
            "created_at": "2026-05-10T12:00:00+08:00",
        })

    def test_i19_artifact_type_outside_enum(self):
        self.dump["artifacts"][0]["type"] = "potato"
        v = validate_dump(self.dump)
        self.assertIn("I19", _codes(v))

    def test_i20_artifact_source_outside_enum(self):
        self.dump["artifacts"][0]["source"] = "magic"
        v = validate_dump(self.dump)
        self.assertIn("I20", _codes(v))

    def test_i21_artifact_relative_path_empty(self):
        self.dump["artifacts"][0]["relative_path"] = ""
        v = validate_dump(self.dump)
        self.assertIn("I21", _codes(v))

    def test_i22_artifact_created_at_bad_format(self):
        self.dump["artifacts"][0]["created_at"] = "yesterday"
        v = validate_dump(self.dump)
        self.assertIn("I22", _codes(v))


class TopLevelTests(unittest.TestCase):
    """I23–I24 negative tests."""

    def setUp(self):
        self.dump = _good_dump()

    def test_i23_schema_version_2(self):
        self.dump["schema_version"] = 2
        v = validate_dump(self.dump)
        self.assertIn("I23", _codes(v))

    def test_i23_schema_version_string(self):
        self.dump["schema_version"] = "1"
        v = validate_dump(self.dump)
        self.assertIn("I23", _codes(v))

    def test_i23_schema_version_missing(self):
        del self.dump["schema_version"]
        v = validate_dump(self.dump)
        codes = _codes(v)
        # Either I23 (missing schema_version) or I24 (top-level keys
        # incomplete) — accept either, but the validator currently emits
        # both for clarity.
        self.assertTrue("I23" in codes or "I24" in codes)

    def test_i24_extra_top_level_key(self):
        self.dump["bogus"] = "x"
        v = validate_dump(self.dump)
        self.assertIn("I24", _codes(v))

    def test_i24_results_not_list(self):
        self.dump["results"] = {"oops": "dict"}
        v = validate_dump(self.dump)
        self.assertIn("I24", _codes(v))

    def test_i24_top_level_not_object(self):
        v = validate_dump([1, 2, 3])
        self.assertIn("I24", _codes(v))


class WarningTests(unittest.TestCase):

    def test_w1_corner_vars_magic_marker(self):
        dump = _good_dump()
        dump["results"][0]["corner_vars"] = {"_no_corner_vars": "TT"}
        v = validate_dump(dump)
        codes_warn = [x.code for x in v if x.severity == "warning"]
        self.assertIn("W1", codes_warn)

    def test_w2_netlist_path_null(self):
        dump = _good_dump()
        dump["run"]["netlist_path"] = None
        v = validate_dump(dump)
        codes_warn = [x.code for x in v if x.severity == "warning"]
        self.assertIn("W2", codes_warn)


class CompositeTests(unittest.TestCase):
    """No fail-fast: a dump broken in many ways surfaces every code at once."""

    def test_broken_in_many_ways(self):
        dump = _good_dump()
        # 1. Bad schema_version (I23)
        dump["schema_version"] = 99
        # 2. Bad project_id (I3)
        dump["run"]["project_id"] = "WHOA"
        # 3. Bad timestamp (I6)
        dump["run"]["timestamp"] = "yesterday"
        # 4. Status outside enum (I12) — also breaks I1 indirectly
        dump["results"][0]["status"] = "potato"
        # 5. Add an artifact with a bad type (I19)
        dump["artifacts"].append({
            "type": "blob",
            "relative_path": "x",
            "description": "",
            "source": "auto",
            "created_at": "2026-05-10T12:00:00+08:00",
        })
        v = validate_dump(dump)
        codes = set(_codes(v))
        for expected in {"I23", "I3", "I6", "I12", "I19"}:
            self.assertIn(expected, codes, f"missing {expected} in {codes}")

    def test_validator_does_not_raise(self):
        # Even completely garbage input must return a list, not raise.
        v = validate_dump({"random": "garbage", "no": ["sense"]})
        self.assertIsInstance(v, list)
        self.assertGreater(len(v), 0)
        self.assertIsInstance(v[0], Violation)


class FileLoaderTests(unittest.TestCase):

    def test_validate_dump_file_real_fixture(self):
        violations = validate_dump_file(_REAL_RUN)
        self.assertEqual(_errors(violations), [])

    def test_validate_dump_file_bad_version_fixture(self):
        violations = validate_dump_file(_BAD_VERSION)
        self.assertIn("I23", _codes(violations))

    def test_validate_dump_file_bad_status_fixture(self):
        violations = validate_dump_file(_BAD_STATUS)
        self.assertIn("I12", _codes(violations))

    def test_validate_dump_file_bad_value_fixture(self):
        violations = validate_dump_file(_BAD_VALUE)
        self.assertIn("I14", _codes(violations))

    def test_validate_dump_file_malformed_json_fixture(self):
        violations = validate_dump_file(_MALFORMED_JSON)
        # Malformed JSON is reported as a single I24 with the file path.
        codes = _codes(violations)
        self.assertIn("I24", codes)
        # No other invariants run because we couldn't even parse.
        self.assertEqual(len(violations), 1)


if __name__ == "__main__":
    unittest.main()
