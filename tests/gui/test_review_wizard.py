"""Tests for the from-scratch review wizard (spec §14.2, capability #8).

Covers the four-step page structure, per-step validation, and the
end-to-end flow that writes a loadable ``.review.json``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "python"))

pytest.importorskip("PyQt5")

from simkit.gui.views.review_wizard import ReviewWizard  # noqa: E402
from simkit.review import load_review  # noqa: E402


def _make_project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "reviews").mkdir(parents=True)
    (root / "unions").mkdir()
    (root / "unions" / "baseline.union.json").write_text("{}", encoding="utf-8")
    return root


def _fill_first_item(wizard: ReviewWizard) -> None:
    table = wizard.items_table.table
    table.item(0, 0).setText("item1")
    table.item(0, 1).setText("Test")
    table.cellWidget(0, 2).setCurrentText("../unions/baseline.union.json")


def test_wizard_has_four_pages(qtbot, tmp_path):
    w = ReviewWizard(_make_project(tmp_path), "proj")
    qtbot.addWidget(w)
    assert len(w.pageIds()) == 4


def test_name_page_completeness_tracks_validity(qtbot, tmp_path):
    w = ReviewWizard(_make_project(tmp_path), "proj")
    qtbot.addWidget(w)
    w.restart()
    name_page = w.page(0)
    assert not name_page.isComplete()
    name_page.name_edit.setText("has space")
    assert not name_page.isComplete()
    name_page.name_edit.setText("brand_new")
    assert name_page.isComplete()


def test_name_page_rejects_existing_review(qtbot, tmp_path):
    root = _make_project(tmp_path)
    (root / "reviews" / "taken.review.json").write_text("{}", encoding="utf-8")
    w = ReviewWizard(root, "proj")
    qtbot.addWidget(w)
    w.restart()
    name_page = w.page(0)
    name_page.name_edit.setText("taken")
    assert name_page.validatePage() is False
    assert "already exists" in name_page.error_label.text()


def test_items_page_seeds_a_row_and_gates_on_it(qtbot, tmp_path):
    w = ReviewWizard(_make_project(tmp_path), "proj")
    qtbot.addWidget(w)
    w.restart()
    w.page(0).name_edit.setText("brand_new")
    w.next()
    assert w.currentId() == 1
    items_page = w.page(1)
    assert w.items_table.row_count() == 1  # initializePage seeded one
    # G-8: a seeded but empty row is NOT enough to advance.
    assert not items_page.isComplete()
    assert items_page.hint_label.text() != ""
    # Filling name + tests + union completes it.
    _fill_first_item(w)
    assert items_page.isComplete()
    assert items_page.hint_label.text() == ""
    # Removing the only row drops back to incomplete.
    w.items_table.table.selectRow(0)
    w.items_table.remove_selected()
    assert not items_page.isComplete()


def test_wizard_end_to_end_writes_loadable_review(qtbot, tmp_path):
    root = _make_project(tmp_path)
    w = ReviewWizard(root, "proj")
    qtbot.addWidget(w)
    w.restart()

    w.page(0).name_edit.setText("brand_new")
    w.next()
    assert w.currentId() == 1

    _fill_first_item(w)
    w.next()
    assert w.currentId() == 2

    w.suite_controls.default_combo.setCurrentText("halt")
    w.next()
    assert w.currentId() == 3

    preview = w.page(3).preview.toPlainText()
    assert "brand_new" in preview
    assert "item1" in preview
    # G-8: Step 4 also carries a plain-language recap, not just JSON.
    summary = w.page(3).summary_label.text()
    assert "评审「brand_new」" in summary
    assert "item1" in summary
    assert "1 个 item" in summary

    assert w.page(3).validatePage() is True
    assert w.saved_path == root / "reviews" / "brand_new.review.json"
    review = load_review(w.saved_path)
    assert review.name == "brand_new"
    assert review.items[0].name == "item1"
    assert review.items[0].tests == ("Test",)


def test_wizard_review_page_surfaces_validation_error(qtbot, tmp_path):
    root = _make_project(tmp_path)
    w = ReviewWizard(root, "proj")
    qtbot.addWidget(w)
    w.restart()
    w.page(0).name_edit.setText("brand_new")
    w.next()
    # Leave the seeded row empty (no name/tests/union) → invalid.
    w.next()
    w.next()
    assert w.currentId() == 3
    assert w.page(3).validatePage() is False
    assert w.page(3).error_label.text() != ""
    assert w.saved_path is None
