"""Tests for the copy-edit review dialog + its shared building blocks.

Covers Tier-1 capability #7 (spec §14.1): discovery helpers, the items
table round-trip (including preservation of GUI-unsurfaced keys),
validation, and the :class:`ReviewEditorDialog` save path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "python"))

pytest.importorskip("PyQt5")

from simkit.gui.views.review_editor import (  # noqa: E402
    ReviewEditorDialog,
    ReviewItemsTable,
    build_review_dict,
    discover_bundles,
    discover_unions,
    is_valid_review_name,
    validate_review_dict,
)
from simkit.review import load_review  # noqa: E402


def _make_project(tmp_path: Path) -> Path:
    """A minimal module layout: reviews/ + a union + a bundle on disk."""
    root = tmp_path / "proj"
    (root / "reviews").mkdir(parents=True)
    (root / "unions").mkdir()
    (root / "bundles").mkdir()
    (root / "unions" / "baseline.union.json").write_text("{}", encoding="utf-8")
    (root / "bundles" / "b.measure.json").write_text("{}", encoding="utf-8")
    return root


# --- discovery -------------------------------------------------------------


def test_discover_unions_returns_review_relative_paths(tmp_path):
    root = _make_project(tmp_path)
    assert discover_unions(root) == ["../unions/baseline.union.json"]
    assert discover_bundles(root) == ["../bundles/b.measure.json"]


def test_discover_handles_missing_dirs(tmp_path):
    assert discover_unions(tmp_path / "nope") == []


def test_is_valid_review_name():
    assert is_valid_review_name("sanity_check-2")
    assert not is_valid_review_name("has space")
    assert not is_valid_review_name("")


# --- items table -----------------------------------------------------------


def test_items_table_roundtrip_preserves_unsurfaced_keys(qtbot):
    table = ReviewItemsTable(["../unions/baseline.union.json"], [])
    qtbot.addWidget(table)
    # ic_from is a schema-v2 key the GUI never shows — it must survive.
    table.load_items([
        {
            "name": "consumer",
            "tests": ["Test"],
            "union": "../unions/baseline.union.json",
            "ic_from": {"item": "src", "file": "ic", "mode": "readic"},
        }
    ])
    assert table.row_count() == 1
    out = table.to_items()
    assert out[0]["ic_from"] == {"item": "src", "file": "ic", "mode": "readic"}


def test_items_table_edit_overlays_onto_source(qtbot):
    table = ReviewItemsTable(["../unions/baseline.union.json"], [])
    qtbot.addWidget(table)
    table.add_item({"name": "old", "tests": ["A"],
                    "union": "../unions/baseline.union.json"})
    table.table.item(0, 0).setText("renamed")
    table.table.item(0, 1).setText("A, B , C")
    out = table.to_items()
    assert out[0]["name"] == "renamed"
    assert out[0]["tests"] == ["A", "B", "C"]


def test_items_table_item_policy_combo(qtbot):
    table = ReviewItemsTable(["../unions/baseline.union.json"], [])
    qtbot.addWidget(table)
    table.add_item({"name": "x", "tests": ["Test"],
                    "union": "../unions/baseline.union.json"})
    table.table.cellWidget(0, 4).setCurrentText("halt")
    assert table.to_items()[0]["on_failure"] == {"item_policy": "halt"}


# --- validation ------------------------------------------------------------


def test_validate_accepts_a_well_formed_review(tmp_path):
    root = _make_project(tmp_path)
    doc = build_review_dict(
        "ok", "proj",
        [{"name": "i1", "tests": ["Test"],
          "union": "../unions/baseline.union.json"}],
    )
    error, warnings = validate_review_dict(doc, root / "reviews")
    assert error is None
    assert warnings == []


def test_validate_rejects_a_malformed_review(tmp_path):
    root = _make_project(tmp_path)
    doc = build_review_dict("bad", "proj", [{"name": "i1"}])  # no tests/union
    error, _warnings = validate_review_dict(doc, root / "reviews")
    assert error is not None


def test_validate_warns_on_missing_union(tmp_path):
    root = _make_project(tmp_path)
    doc = build_review_dict(
        "ok", "proj",
        [{"name": "i1", "tests": ["Test"],
          "union": "../unions/does_not_exist.union.json"}],
    )
    error, warnings = validate_review_dict(doc, root / "reviews")
    assert error is None
    assert any("missing" in w for w in warnings)


# --- dialog ----------------------------------------------------------------


def test_dialog_prefills_from_source_review(qtbot, tmp_path):
    root = _make_project(tmp_path)
    source = {
        "review_schema_version": 1,
        "name": "src",
        "project": "proj",
        "items": [
            {"name": "a", "tests": ["Test"],
             "union": "../unions/baseline.union.json"},
            {"name": "b", "tests": ["Test2"],
             "union": "../unions/baseline.union.json"},
        ],
    }
    dlg = ReviewEditorDialog(root, "proj", source_review=source,
                             default_name="src_copy")
    qtbot.addWidget(dlg)
    assert dlg.name_edit.text() == "src_copy"
    assert dlg.items_table.row_count() == 2


def test_dialog_save_writes_a_loadable_review(qtbot, tmp_path):
    root = _make_project(tmp_path)
    source = {
        "review_schema_version": 1, "name": "src", "project": "proj",
        "items": [{"name": "a", "tests": ["Test"],
                   "union": "../unions/baseline.union.json"}],
    }
    dlg = ReviewEditorDialog(root, "proj", source_review=source,
                             default_name="src_copy")
    qtbot.addWidget(dlg)
    dlg._on_save()
    assert dlg.saved_path is not None
    assert dlg.saved_path == root / "reviews" / "src_copy.review.json"
    review = load_review(dlg.saved_path)  # must parse cleanly
    assert review.name == "src_copy"
    assert len(review.items) == 1


def test_dialog_blocks_invalid_name(qtbot, tmp_path):
    root = _make_project(tmp_path)
    dlg = ReviewEditorDialog(root, "proj", default_name="bad name")
    qtbot.addWidget(dlg)
    dlg._on_save()
    assert dlg.saved_path is None
    assert "invalid" in dlg.error_label.text().lower()


def test_dialog_blocks_duplicate_name(qtbot, tmp_path):
    root = _make_project(tmp_path)
    (root / "reviews" / "taken.review.json").write_text("{}", encoding="utf-8")
    dlg = ReviewEditorDialog(root, "proj", default_name="taken")
    qtbot.addWidget(dlg)
    dlg.items_table.add_item({"name": "i", "tests": ["Test"],
                              "union": "../unions/baseline.union.json"})
    dlg._on_save()
    assert dlg.saved_path is None
    assert "already exists" in dlg.error_label.text()


def test_dialog_emits_validation_error_inline(qtbot, tmp_path):
    root = _make_project(tmp_path)
    dlg = ReviewEditorDialog(root, "proj", default_name="empty_tests")
    qtbot.addWidget(dlg)
    # Default row has no name/tests — save must surface review.py's error.
    dlg._on_save()
    assert dlg.saved_path is None
    assert dlg.error_label.text() != ""
