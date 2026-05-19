"""Project-tree QStandardItemModel for the left panel (Phase 4 Stage 3).

Three top-level groups: Reviews, Milestones, History. The model is fed by
:meth:`populate` with a :class:`simkit.gui.loaders.LoadedModule` snapshot.
MainWindow listens for clicks and asks :meth:`node_kind` + :meth:`node_payload`
to decide what panel to show on the right.
"""

from __future__ import annotations

from typing import Any

from PyQt5.QtCore import QModelIndex, Qt
from PyQt5.QtGui import QStandardItem, QStandardItemModel

from simkit.gui.loaders import (
    LoadedHistoryRun,
    LoadedModule,
    LoadedReview,
)


# Stash (kind, payload) on each item under this role so the view can ask
# the model which node was clicked without sniffing display text.
_NODE_DATA_ROLE = Qt.UserRole + 1


class ProjectTreeModel(QStandardItemModel):
    """Tree model for the left-panel module browser."""

    NODE_KIND_REVIEW = "review"
    NODE_KIND_MILESTONE = "milestone"
    NODE_KIND_HISTORY = "history"
    NODE_KIND_GROUP = "group"

    GROUP_REVIEWS = "Reviews"
    GROUP_MILESTONES = "Milestones"
    GROUP_HISTORY = "History"

    def __init__(self, parent: Any = None):
        super().__init__(parent)
        self.setHorizontalHeaderLabels([""])

    def populate(self, module: LoadedModule) -> None:
        """Replace contents with one module's snapshot."""
        self.removeRows(0, self.rowCount())

        self.appendRow(self._build_reviews_group(module.reviews))
        self.appendRow(
            self._build_milestones_group(module.milestones, module.history)
        )
        self.appendRow(self._build_history_group(module.history))

    # ---- introspection helpers used by MainWindow -----------------------

    def node_kind(self, index: QModelIndex) -> str | None:
        if not index.isValid():
            return None
        payload = self.data(index, _NODE_DATA_ROLE)
        if not isinstance(payload, tuple) or len(payload) != 2:
            return None
        return payload[0]

    def node_payload(self, index: QModelIndex) -> Any:
        if not index.isValid():
            return None
        payload = self.data(index, _NODE_DATA_ROLE)
        if not isinstance(payload, tuple) or len(payload) != 2:
            return None
        return payload[1]

    # ---- group builders -------------------------------------------------

    def _build_reviews_group(
        self, reviews: tuple[LoadedReview, ...]
    ) -> QStandardItem:
        group = _group_item(self.GROUP_REVIEWS, len(reviews))
        group.setData(
            (self.NODE_KIND_GROUP, self.GROUP_REVIEWS), _NODE_DATA_ROLE
        )
        for review in reviews:
            child = QStandardItem(
                f"{review.review_name}  ({review.item_count} items)"
            )
            child.setEditable(False)
            child.setData(
                (self.NODE_KIND_REVIEW, review), _NODE_DATA_ROLE
            )
            group.appendRow(child)
        return group

    def _build_milestones_group(
        self,
        milestones: tuple[str, ...],
        history: tuple[LoadedHistoryRun, ...],
    ) -> QStandardItem:
        group = _group_item(self.GROUP_MILESTONES, len(milestones))
        group.setData(
            (self.NODE_KIND_GROUP, self.GROUP_MILESTONES), _NODE_DATA_ROLE
        )
        # Per-milestone run counts come from the history list so we don't
        # round-trip to DuckDB a second time.
        counts: dict[str, int] = {m: 0 for m in milestones}
        for run in history:
            if run.milestone and run.milestone in counts:
                counts[run.milestone] += 1
        for ms in milestones:
            child = QStandardItem(f"★ {ms} ({counts[ms]} runs)")
            child.setEditable(False)
            child.setData(
                (self.NODE_KIND_MILESTONE, ms), _NODE_DATA_ROLE
            )
            group.appendRow(child)
        return group

    def _build_history_group(
        self, history: tuple[LoadedHistoryRun, ...]
    ) -> QStandardItem:
        group = _group_item(self.GROUP_HISTORY, len(history))
        group.setData(
            (self.NODE_KIND_GROUP, self.GROUP_HISTORY), _NODE_DATA_ROLE
        )
        for run in history:
            child = QStandardItem(_history_label(run))
            child.setEditable(False)
            child.setData(
                (self.NODE_KIND_HISTORY, run), _NODE_DATA_ROLE
            )
            group.appendRow(child)
        return group


def _group_item(label: str, n: int) -> QStandardItem:
    item = QStandardItem(f"{label} ({n})")
    item.setEditable(False)
    # Bold-ish via Qt's font-weight signal is tempting but unneeded for
    # Tier-1; the parens count carries the meaning.
    return item


def _history_label(run: LoadedHistoryRun) -> str:
    star = "★ " if run.starred else ""
    tail = run.label if run.label else (run.history_name or "")
    return f"{star}{run.short_id}  {run.timestamp}  {tail}".rstrip()
