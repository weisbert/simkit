"""Tests for :mod:`simkit.gui.tree_model` (Phase 4 Stage 3)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Headless Qt — must be set BEFORE PyQt5 import.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt5")

from PyQt5.QtWidgets import QApplication  # noqa: E402

from simkit.gui.loaders import (  # noqa: E402
    LoadedHistoryRun,
    LoadedModule,
    LoadedReview,
)
from simkit.gui.tree_model import ProjectTreeModel  # noqa: E402


_QAPP = QApplication.instance() or QApplication(sys.argv)


def _sample_module() -> LoadedModule:
    return LoadedModule(
        project_path=Path("/proj/.pvtproject"),
        project_root=Path("/proj"),
        project_name="demo",
        db_path=Path("/proj/simkit.duckdb"),
        reviews=(
            LoadedReview(
                review_path=Path("/proj/reviews/pn.review.json"),
                review_name="pn",
                item_count=3,
            ),
            LoadedReview(
                review_path=Path("/proj/reviews/max_freq.review.json"),
                review_name="max_freq",
                item_count=1,
            ),
        ),
        history=(
            LoadedHistoryRun(
                run_id="bbbbbbbbbbbb-2222",
                short_id="bbbbbbbb",
                timestamp="2026-05-12 09:00:00+00",
                label=None,
                starred=True,
                milestone="CDR",
                history_name="pn__1",
            ),
            LoadedHistoryRun(
                run_id="aaaaaaaaaaaa-1111",
                short_id="aaaaaaaa",
                timestamp="2026-05-10 09:00:00+00",
                label="before-fix",
                starred=False,
                milestone=None,
                history_name="pn__2",
            ),
        ),
        milestones=("CDR",),
        union_default=None,
        bundle_default=None,
    )


def test_populate_creates_three_groups():
    model = ProjectTreeModel()
    model.populate(_sample_module())
    # Three top-level rows.
    assert model.rowCount() == 3
    # Group labels include the counts.
    assert "Reviews" in model.item(0).text()
    assert "Milestones" in model.item(1).text()
    assert "History" in model.item(2).text()


def test_node_kind_group_for_top_level():
    model = ProjectTreeModel()
    model.populate(_sample_module())
    idx = model.index(0, 0)
    assert model.node_kind(idx) == ProjectTreeModel.NODE_KIND_GROUP


def test_reviews_group_has_review_children():
    model = ProjectTreeModel()
    model.populate(_sample_module())
    reviews_group = model.item(0)
    assert reviews_group.rowCount() == 2
    child = reviews_group.child(0)
    child_idx = model.indexFromItem(child)
    assert model.node_kind(child_idx) == ProjectTreeModel.NODE_KIND_REVIEW
    payload = model.node_payload(child_idx)
    assert isinstance(payload, LoadedReview)
    # Render contains the item count.
    assert "items" in child.text()


def test_history_group_renders_short_id_and_label_and_star():
    model = ProjectTreeModel()
    model.populate(_sample_module())
    history_group = model.item(2)
    assert history_group.rowCount() == 2
    first = history_group.child(0)
    text = first.text()
    # Star prefix for starred runs.
    assert text.startswith("★")
    assert "bbbbbbbb" in text
    # Label fallback to history_name when no label.
    assert "pn__1" in text
    # Second entry has a label.
    second = history_group.child(1)
    assert "before-fix" in second.text()


def test_milestone_group_lists_milestones_with_counts():
    model = ProjectTreeModel()
    model.populate(_sample_module())
    milestones_group = model.item(1)
    assert milestones_group.rowCount() == 1
    child = milestones_group.child(0)
    assert "CDR" in child.text()
    assert "1 runs" in child.text()
    idx = model.indexFromItem(child)
    assert model.node_kind(idx) == ProjectTreeModel.NODE_KIND_MILESTONE
    assert model.node_payload(idx) == "CDR"


def test_history_node_payload_is_loaded_history_run():
    model = ProjectTreeModel()
    model.populate(_sample_module())
    history_group = model.item(2)
    idx = model.indexFromItem(history_group.child(0))
    payload = model.node_payload(idx)
    assert isinstance(payload, LoadedHistoryRun)
    assert payload.starred is True


def test_populate_is_idempotent_replaces_contents():
    model = ProjectTreeModel()
    model.populate(_sample_module())
    model.populate(_sample_module())
    assert model.rowCount() == 3


def test_node_kind_returns_none_for_invalid_index():
    model = ProjectTreeModel()
    model.populate(_sample_module())
    from PyQt5.QtCore import QModelIndex
    assert model.node_kind(QModelIndex()) is None
    assert model.node_payload(QModelIndex()) is None


def test_empty_module_still_has_three_groups():
    model = ProjectTreeModel()
    module = LoadedModule(
        project_path=Path("/proj/.pvtproject"),
        project_root=Path("/proj"),
        project_name="demo",
        db_path=Path("/proj/simkit.duckdb"),
        reviews=(),
        history=(),
        milestones=(),
        union_default=None,
        bundle_default=None,
    )
    model.populate(module)
    assert model.rowCount() == 3
    # Each group reports 0.
    for r in range(3):
        assert "(0)" in model.item(r).text()
