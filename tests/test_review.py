"""Unit tests for simkit.review (`.review.json` loader + validator).

Phase 3A §1 spec (docs/phase3a_orchestrator_spec.md), DECISIONS #50-#52.

Run with stdlib unittest or pytest:

    PYTHONPATH=python python3.11 -m unittest tests.test_review -v
    python3.11 -m pytest tests/test_review.py -v
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

from simkit.review import (  # noqa: E402
    IcFromRef,
    OnFailurePolicy,
    PathIssue,
    Review,
    ReviewItem,
    ReviewMalformedError,
    ReviewSchemaVersionError,
    ReviewValidationError,
    StrategyEntry,
    check_project_match,
    load_review,
    validate_paths_exist,
)


_EXAMPLE_FILE = _REPO_ROOT / "config" / "review_example.review.json"


def _min_doc(name: str = "myreview") -> dict:
    return {
        "review_schema_version": 1,
        "name": name,
        "project": "myproj",
        "items": [
            {
                "name": "item one",
                "tests": ["sim_a"],
                "union": "unions/a.union.json",
            }
        ],
    }


class TempDirMixin:
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_review_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, doc, *, name: str = "myreview") -> Path:
        path = self.tmp / f"{name}.review.json"
        if isinstance(doc, str):
            path.write_text(doc, encoding="utf-8")
        else:
            path.write_text(json.dumps(doc), encoding="utf-8")
        return path


# ---------------------------------------------------------------------------
# Happy path


class HappyPathTests(unittest.TestCase):
    def test_example_file_loads(self):
        """The shipped example file must always load cleanly."""
        review = load_review(_EXAMPLE_FILE)
        self.assertEqual(review.name, "review_example")
        self.assertEqual(review.project, "example_block")
        self.assertEqual(review.review_schema_version, 1)
        self.assertEqual(len(review.items), 5)

    def test_example_file_items_carry_expected_shape(self):
        review = load_review(_EXAMPLE_FILE)
        item0 = review.items[0]
        self.assertEqual(item0.name, "BT2GRX trans PVT")
        self.assertEqual(list(item0.tests), ["sim_BT2GRX", "sim_BT2GTX"])
        self.assertTrue(item0.enabled)
        # The CJK-named item must load — _ITEM_NAME_RE uses \w (unicode).
        cjk_item = review.items[4]
        self.assertEqual(cjk_item.name, "干扰仿真")
        # Its on_failure overrides suite-level: item_policy=halt.
        self.assertEqual(cjk_item.on_failure.item_policy, "halt")
        # But suite-level strategies (naive_retry) are inherited.
        self.assertEqual([s.name for s in cjk_item.on_failure.strategies],
                         ["naive_retry"])

    def test_example_file_paths_resolve_to_review_dir(self):
        review = load_review(_EXAMPLE_FILE)
        for item in review.items:
            self.assertTrue(item.union.is_absolute())
            self.assertTrue(str(item.union).endswith(".union.json"))


# ---------------------------------------------------------------------------
# schema version


class SchemaVersionTests(TempDirMixin, unittest.TestCase):
    def test_missing_version_errors(self):
        doc = _min_doc()
        del doc["review_schema_version"]
        with self.assertRaises(ReviewSchemaVersionError):
            load_review(self._write(doc))

    def test_unsupported_version_errors(self):
        doc = _min_doc()
        doc["review_schema_version"] = 99
        with self.assertRaises(ReviewSchemaVersionError) as cm:
            load_review(self._write(doc))
        self.assertIn("99", str(cm.exception))

    def test_bool_rejected_as_version(self):
        doc = _min_doc()
        doc["review_schema_version"] = True  # bool is-a-int in Python
        with self.assertRaises(ReviewSchemaVersionError):
            load_review(self._write(doc))


# ---------------------------------------------------------------------------
# top-level required fields


class TopLevelTests(TempDirMixin, unittest.TestCase):
    def test_missing_name_errors(self):
        doc = _min_doc()
        del doc["name"]
        with self.assertRaises(ReviewValidationError):
            load_review(self._write(doc))

    def test_name_regex_lowercase_only(self):
        doc = _min_doc(name="MyReview")
        with self.assertRaises(ReviewValidationError):
            load_review(self._write(doc, name="MyReview"))

    def test_filename_basename_must_equal_name(self):
        doc = _min_doc(name="actual_name")
        # write under a different basename
        path = self.tmp / "wrong_name.review.json"
        path.write_text(json.dumps(doc))
        with self.assertRaises(ReviewValidationError) as cm:
            load_review(path)
        self.assertIn("filename basename", str(cm.exception))

    def test_missing_project_errors(self):
        doc = _min_doc()
        del doc["project"]
        with self.assertRaises(ReviewValidationError):
            load_review(self._write(doc))

    def test_missing_items_errors(self):
        doc = _min_doc()
        del doc["items"]
        with self.assertRaises(ReviewValidationError):
            load_review(self._write(doc))

    def test_empty_items_errors(self):
        doc = _min_doc()
        doc["items"] = []
        with self.assertRaises(ReviewValidationError):
            load_review(self._write(doc))

    def test_non_object_top_level_errors(self):
        path = self._write("[]")
        with self.assertRaises(ReviewMalformedError):
            load_review(path)

    def test_malformed_json_errors(self):
        path = self._write("{not json")
        with self.assertRaises(ReviewMalformedError):
            load_review(path)


# ---------------------------------------------------------------------------
# item shape


class ItemShapeTests(TempDirMixin, unittest.TestCase):
    def test_item_missing_name(self):
        doc = _min_doc()
        del doc["items"][0]["name"]
        with self.assertRaises(ReviewValidationError):
            load_review(self._write(doc))

    def test_item_missing_tests(self):
        doc = _min_doc()
        del doc["items"][0]["tests"]
        with self.assertRaises(ReviewValidationError):
            load_review(self._write(doc))

    def test_item_empty_tests(self):
        doc = _min_doc()
        doc["items"][0]["tests"] = []
        with self.assertRaises(ReviewValidationError):
            load_review(self._write(doc))

    def test_item_missing_union(self):
        doc = _min_doc()
        del doc["items"][0]["union"]
        with self.assertRaises(ReviewValidationError):
            load_review(self._write(doc))

    def test_item_bundle_null_is_ok(self):
        doc = _min_doc()
        doc["items"][0]["bundle"] = None
        review = load_review(self._write(doc))
        self.assertIsNone(review.items[0].bundle)

    def test_item_bundle_resolves_relative(self):
        doc = _min_doc()
        doc["items"][0]["bundle"] = "b.measure.json"
        review = load_review(self._write(doc))
        self.assertEqual(
            review.items[0].bundle,
            (self.tmp / "b.measure.json").resolve(),
        )

    def test_item_enabled_default_true(self):
        review = load_review(self._write(_min_doc()))
        self.assertTrue(review.items[0].enabled)

    def test_item_enabled_false_honored(self):
        doc = _min_doc()
        doc["items"][0]["enabled"] = False
        review = load_review(self._write(doc))
        self.assertFalse(review.items[0].enabled)

    def test_item_enabled_must_be_bool(self):
        doc = _min_doc()
        doc["items"][0]["enabled"] = "yes"
        with self.assertRaises(ReviewValidationError):
            load_review(self._write(doc))

    def test_unknown_item_key_errors(self):
        doc = _min_doc()
        doc["items"][0]["foo"] = "bar"
        with self.assertRaises(ReviewValidationError) as cm:
            load_review(self._write(doc))
        self.assertIn("unknown keys", str(cm.exception))

    def test_duplicate_item_names_error(self):
        doc = _min_doc()
        doc["items"].append({
            "name": "item one",
            "tests": ["sim_b"],
            "union": "unions/b.union.json",
        })
        with self.assertRaises(ReviewValidationError) as cm:
            load_review(self._write(doc))
        self.assertIn("duplicates", str(cm.exception))

    def test_test_name_must_be_identifier(self):
        doc = _min_doc()
        doc["items"][0]["tests"] = ["bad/name"]
        with self.assertRaises(ReviewValidationError):
            load_review(self._write(doc))

    def test_duplicate_test_in_one_item(self):
        doc = _min_doc()
        doc["items"][0]["tests"] = ["sim_a", "sim_a"]
        with self.assertRaises(ReviewValidationError):
            load_review(self._write(doc))

    def test_cjk_item_name_accepted(self):
        doc = _min_doc()
        doc["items"][0]["name"] = "干扰仿真"
        review = load_review(self._write(doc))
        self.assertEqual(review.items[0].name, "干扰仿真")


# ---------------------------------------------------------------------------
# on_failure


class OnFailureTests(TempDirMixin, unittest.TestCase):
    def test_default_skip_when_omitted(self):
        review = load_review(self._write(_min_doc()))
        self.assertEqual(review.items[0].on_failure.default, "skip")
        self.assertEqual(review.items[0].on_failure.corner_policy, "skip")
        self.assertEqual(review.items[0].on_failure.item_policy, "skip")
        self.assertEqual(review.items[0].on_failure.strategies, ())

    def test_suite_level_strategies_inherited(self):
        doc = _min_doc()
        doc["on_failure"] = {
            "default": "skip",
            "strategies": [{"name": "naive_retry", "max_attempts": 2}],
        }
        review = load_review(self._write(doc))
        item = review.items[0]
        self.assertEqual(len(item.on_failure.strategies), 1)
        self.assertEqual(item.on_failure.strategies[0].name, "naive_retry")
        self.assertEqual(item.on_failure.strategies[0].max_attempts, 2)

    def test_item_level_overrides_suite(self):
        doc = _min_doc()
        doc["on_failure"] = {"default": "skip"}
        doc["items"][0]["on_failure"] = {"item_policy": "halt"}
        review = load_review(self._write(doc))
        item = review.items[0]
        # Item override wins
        self.assertEqual(item.on_failure.item_policy, "halt")
        # corner_policy still inherits default
        self.assertEqual(item.on_failure.corner_policy, "skip")

    def test_item_level_strategies_replace_wholesale(self):
        """Arrays replace; objects merge — per DECISIONS #50."""
        doc = _min_doc()
        doc["on_failure"] = {
            "strategies": [{"name": "naive_retry"}],
        }
        doc["items"][0]["on_failure"] = {
            "strategies": [{"name": "custom_strategy"}],
        }
        review = load_review(self._write(doc))
        item = review.items[0]
        self.assertEqual([s.name for s in item.on_failure.strategies],
                         ["custom_strategy"])

    def test_bad_policy_value_errors(self):
        doc = _min_doc()
        doc["on_failure"] = {"default": "explode"}
        with self.assertRaises(ReviewValidationError):
            load_review(self._write(doc))

    def test_unknown_on_failure_key_errors(self):
        doc = _min_doc()
        doc["on_failure"] = {"weird_key": "x"}
        with self.assertRaises(ReviewValidationError):
            load_review(self._write(doc))

    def test_strategy_entry_missing_name(self):
        doc = _min_doc()
        doc["on_failure"] = {"strategies": [{"max_attempts": 2}]}
        with self.assertRaises(ReviewValidationError):
            load_review(self._write(doc))

    def test_strategy_entry_bad_max_attempts(self):
        doc = _min_doc()
        doc["on_failure"] = {"strategies": [{"name": "x", "max_attempts": 0}]}
        with self.assertRaises(ReviewValidationError):
            load_review(self._write(doc))

    def test_strategy_params_passthrough(self):
        doc = _min_doc()
        doc["on_failure"] = {
            "strategies": [
                {"name": "trans_pss_ic", "max_attempts": 1,
                 "trans_duration": "5ns", "ic_mode": "spectre.fc"},
            ],
        }
        review = load_review(self._write(doc))
        s = review.items[0].on_failure.strategies[0]
        self.assertEqual(s.params,
                         {"trans_duration": "5ns", "ic_mode": "spectre.fc"})


# ---------------------------------------------------------------------------
# path-exists side checks


class PathsExistTests(TempDirMixin, unittest.TestCase):
    def test_missing_union_reported(self):
        review = load_review(self._write(_min_doc()))
        issues = validate_paths_exist(review)
        # Example points at unions/a.union.json — not created in tmp dir
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].kind, "union")
        self.assertEqual(issues[0].reason, "missing")

    def test_existing_union_clean(self):
        # create a stub union file
        (self.tmp / "unions").mkdir()
        (self.tmp / "unions" / "a.union.json").write_text("{}")
        review = load_review(self._write(_min_doc()))
        issues = validate_paths_exist(review)
        self.assertEqual(issues, [])

    def test_bundle_wrong_suffix_flagged(self):
        doc = _min_doc()
        doc["items"][0]["bundle"] = "b.json"  # wrong suffix
        # bundle file exists but suffix wrong
        (self.tmp / "b.json").write_text("{}")
        # union missing too
        review = load_review(self._write(doc))
        issues = validate_paths_exist(review)
        kinds = [(i.kind, i.reason) for i in issues]
        self.assertIn(("bundle", "wrong_suffix (expected .measure.json)"), kinds)


# ---------------------------------------------------------------------------
# project-match check


class ProjectMatchTests(TempDirMixin, unittest.TestCase):
    def test_match_ok(self):
        review = load_review(self._write(_min_doc()))
        check_project_match(review, "myproj")  # no raise

    def test_mismatch_raises(self):
        review = load_review(self._write(_min_doc()))
        with self.assertRaises(ReviewValidationError):
            check_project_match(review, "other_project")


# ---------------------------------------------------------------------------
# ic_from (schema v2, DECISIONS #57) — Phase 3A v1.2 trans→PSS IC piping


def _ic_doc(consumer_ic_from: dict, *, schema_version: int = 2) -> dict:
    """Build a 2-item review where item 'pss' consumes IC from item 'trans'.

    Both items reference the same union by default. Override the
    ``ic_from`` block via ``consumer_ic_from``; pass ``schema_version=1``
    to exercise the v1-rejects-ic_from path.
    """
    return {
        "review_schema_version": schema_version,
        "name": "myreview",
        "project": "myproj",
        "items": [
            {
                "name": "trans",
                "tests": ["sim_trans"],
                "union": "unions/full.union.json",
            },
            {
                "name": "pss",
                "tests": ["sim_pss"],
                "union": "unions/full.union.json",
                "ic_from": consumer_ic_from,
            },
        ],
    }


class IcFromShapeTests(TempDirMixin, unittest.TestCase):
    """Per-item shape validation: required keys, allowed values, unknown keys."""

    def test_happy_path_loads(self):
        doc = _ic_doc({"item": "trans", "file": "fc", "mode": "readns"})
        review = load_review(self._write(doc))
        self.assertEqual(review.review_schema_version, 2)
        self.assertIsNone(review.items[0].ic_from)
        ref = review.items[1].ic_from
        self.assertIsNotNone(ref)
        self.assertEqual(ref.item, "trans")
        self.assertEqual(ref.file, "fc")
        self.assertEqual(ref.mode, "readns")
        self.assertIsNone(ref.subdir)

    def test_v1_sidecar_rejects_ic_from(self):
        # Even shape-valid ic_from must be rejected on v1 (per DECISIONS #57:
        # silently ignoring would mean PSS runs cold).
        doc = _ic_doc({"item": "trans", "file": "fc", "mode": "readns"},
                      schema_version=1)
        with self.assertRaises(ReviewSchemaVersionError) as cm:
            load_review(self._write(doc))
        self.assertIn("review_schema_version >= 2", str(cm.exception))

    def test_missing_required_key(self):
        for omit in ("item", "file", "mode"):
            full = {"item": "trans", "file": "fc", "mode": "readns"}
            full.pop(omit)
            with self.subTest(omit=omit):
                with self.assertRaises(ReviewValidationError) as cm:
                    load_review(self._write(_ic_doc(full)))
                self.assertIn("missing required keys", str(cm.exception))

    def test_unknown_key_rejected(self):
        doc = _ic_doc({
            "item": "trans", "file": "fc", "mode": "readns", "bogus": True,
        })
        with self.assertRaises(ReviewValidationError) as cm:
            load_review(self._write(doc))
        self.assertIn("bogus", str(cm.exception))

    def test_invalid_file_kind(self):
        doc = _ic_doc({"item": "trans", "file": "xyz", "mode": "readns"})
        with self.assertRaises(ReviewValidationError) as cm:
            load_review(self._write(doc))
        self.assertIn("file must be one of", str(cm.exception))

    def test_invalid_mode(self):
        doc = _ic_doc({"item": "trans", "file": "fc", "mode": "loadme"})
        with self.assertRaises(ReviewValidationError) as cm:
            load_review(self._write(doc))
        self.assertIn("mode must be one of", str(cm.exception))

    def test_subdir_override_accepted(self):
        doc = _ic_doc({
            "item": "trans", "file": "fc", "mode": "readns", "subdir": "psf",
        })
        review = load_review(self._write(doc))
        self.assertEqual(review.items[1].ic_from.subdir, "psf")

    def test_subdir_empty_string_rejected(self):
        doc = _ic_doc({
            "item": "trans", "file": "fc", "mode": "readns", "subdir": "",
        })
        with self.assertRaises(ReviewValidationError):
            load_review(self._write(doc))

    def test_each_file_kind_accepted(self):
        for kind in ("fc", "ic", "dc"):
            with self.subTest(kind=kind):
                doc = _ic_doc({"item": "trans", "file": kind, "mode": "readns"})
                review = load_review(self._write(doc))
                self.assertEqual(review.items[1].ic_from.file, kind)

    def test_both_modes_accepted(self):
        for mode in ("readns", "readic"):
            with self.subTest(mode=mode):
                doc = _ic_doc({"item": "trans", "file": "ic", "mode": mode})
                review = load_review(self._write(doc))
                self.assertEqual(review.items[1].ic_from.mode, mode)

    def test_null_ic_from_treated_as_omitted(self):
        doc = _ic_doc({"item": "trans", "file": "fc", "mode": "readns"})
        doc["items"][1]["ic_from"] = None
        review = load_review(self._write(doc))
        self.assertIsNone(review.items[1].ic_from)


class BaselineCornerFieldTests(TempDirMixin, unittest.TestCase):
    """baseline_corner field validation (DECISIONS #59, Phase 3A v1.4)."""

    def test_absent_field_yields_none(self):
        doc = _ic_doc({"item": "trans", "file": "fc", "mode": "readns"})
        review = load_review(self._write(doc))
        self.assertIsNone(review.items[1].baseline_corner)

    def test_explicit_name_loads(self):
        doc = _ic_doc({"item": "trans", "file": "fc", "mode": "readns"})
        doc["items"][1]["baseline_corner"] = "TT"
        review = load_review(self._write(doc))
        self.assertEqual(review.items[1].baseline_corner, "TT")

    def test_null_treated_as_absent(self):
        doc = _ic_doc({"item": "trans", "file": "fc", "mode": "readns"})
        doc["items"][1]["baseline_corner"] = None
        review = load_review(self._write(doc))
        self.assertIsNone(review.items[1].baseline_corner)

    def test_empty_string_rejected(self):
        doc = _ic_doc({"item": "trans", "file": "fc", "mode": "readns"})
        doc["items"][1]["baseline_corner"] = ""
        with self.assertRaises(ReviewValidationError) as cm:
            load_review(self._write(doc))
        self.assertIn("non-empty string", str(cm.exception))

    def test_non_string_rejected(self):
        doc = _ic_doc({"item": "trans", "file": "fc", "mode": "readns"})
        doc["items"][1]["baseline_corner"] = 42
        with self.assertRaises(ReviewValidationError):
            load_review(self._write(doc))

    def test_v1_sidecar_rejects_baseline_corner(self):
        # v1.4 schema bump enforced — same pattern as ic_from.
        doc = _ic_doc({"item": "trans", "file": "fc", "mode": "readns"},
                      schema_version=1)
        # First strip ic_from since v1 already rejects it (would error first)
        doc["items"][1].pop("ic_from")
        doc["items"][1]["baseline_corner"] = "TT"
        with self.assertRaises(ReviewSchemaVersionError) as cm:
            load_review(self._write(doc))
        self.assertIn("review_schema_version >= 2", str(cm.exception))

    def test_baseline_corner_without_ic_from_rejected(self):
        # Per DECISIONS #59 scope: v1.4 only honors baseline_corner on
        # ic_from items. Setting it on a batch item is meaningless +
        # would silently do nothing; reject with a clear error.
        doc = _ic_doc({"item": "trans", "file": "fc", "mode": "readns"})
        doc["items"][0]["baseline_corner"] = "TT"  # trans is batch (no ic_from)
        with self.assertRaises(ReviewValidationError) as cm:
            load_review(self._write(doc))
        self.assertIn("only honored on items with ic_from", str(cm.exception))


class IcFromCrossRefTests(TempDirMixin, unittest.TestCase):
    """Post-loop cross-item refs: existence, ordering, same-union, no self-ref."""

    def test_self_reference_rejected(self):
        doc = _ic_doc({"item": "pss", "file": "fc", "mode": "readns"})
        with self.assertRaises(ReviewValidationError) as cm:
            load_review(self._write(doc))
        self.assertIn("references itself", str(cm.exception))

    def test_unknown_source_item_rejected(self):
        doc = _ic_doc({"item": "does_not_exist", "file": "fc", "mode": "readns"})
        with self.assertRaises(ReviewValidationError) as cm:
            load_review(self._write(doc))
        self.assertIn("does not match any item name", str(cm.exception))

    def test_forward_reference_rejected(self):
        # Swap order: consumer comes BEFORE source → should fail because the
        # source can't have produced IC files yet when the consumer needs them.
        doc = _ic_doc({"item": "trans", "file": "fc", "mode": "readns"})
        doc["items"] = list(reversed(doc["items"]))
        with self.assertRaises(ReviewValidationError) as cm:
            load_review(self._write(doc))
        self.assertIn("must appear earlier", str(cm.exception))

    def test_different_union_rejected(self):
        doc = _ic_doc({"item": "trans", "file": "fc", "mode": "readns"})
        doc["items"][1]["union"] = "unions/different.union.json"
        with self.assertRaises(ReviewValidationError) as cm:
            load_review(self._write(doc))
        self.assertIn("share the same union", str(cm.exception))

    def test_same_union_passes(self):
        # Already exercised by happy_path but pin explicitly.
        doc = _ic_doc({"item": "trans", "file": "fc", "mode": "readns"})
        review = load_review(self._write(doc))
        self.assertEqual(
            review.items[0].union, review.items[1].union,
            "test fixture must keep both items on the same union",
        )

    def test_chain_of_three_items_valid(self):
        # trans → trans2 (reads trans.fc) → pss (reads trans2.fc).
        doc = {
            "review_schema_version": 2, "name": "myreview", "project": "myproj",
            "items": [
                {"name": "trans", "tests": ["sim_trans"],
                 "union": "unions/full.union.json"},
                {"name": "trans2", "tests": ["sim_trans2"],
                 "union": "unions/full.union.json",
                 "ic_from": {"item": "trans", "file": "ic", "mode": "readic"}},
                {"name": "pss", "tests": ["sim_pss"],
                 "union": "unions/full.union.json",
                 "ic_from": {"item": "trans2", "file": "fc", "mode": "readns"}},
            ],
        }
        review = load_review(self._write(doc))
        self.assertIsNone(review.items[0].ic_from)
        self.assertEqual(review.items[1].ic_from.item, "trans")
        self.assertEqual(review.items[2].ic_from.item, "trans2")


if __name__ == "__main__":
    unittest.main()
