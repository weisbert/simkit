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
            child.setToolTip(_history_tooltip(run))
            child.setData(
                (self.NODE_KIND_HISTORY, run), _NODE_DATA_ROLE
            )
            group.appendRow(child)
        return group


def _group_item(label: str, n: int) -> QStandardItem:
    item = QStandardItem(f"{label} ({n})")
    item.setEditable(False)
    return item


def _history_label(run: LoadedHistoryRun) -> str:
    """Lead with the most-meaningful name; timestamp goes to the right.

    Priority: label (user-set) → history_name (Maestro / pvt run) → short_id.
    """
    star = "★ " if run.starred else ""
    primary = run.label or run.history_name or run.short_id
    when = _relative_ts(run.timestamp)
    return f"{star}{primary}  ·  {when}"


def _history_tooltip(run: LoadedHistoryRun) -> str:
    """Power-user disclosure: full timestamp + ids on hover."""
    lines = [f"id: {run.short_id}"]
    if run.history_name:
        lines.append(f"history: {run.history_name}")
    if run.label:
        lines.append(f"label: {run.label}")
    if run.milestone:
        lines.append(f"milestone: {run.milestone}")
    lines.append(f"timestamp: {run.timestamp}")
    return "\n".join(lines)


def _relative_ts(iso_ts: str | None, *, now=None) -> str:
    """Format an ISO timestamp like '2026-05-18 14:36:42+08' as '3h ago' /
    'yesterday' / 'May 18'. Falls back to the raw string on parse failure.

    The ``now`` kwarg is for tests; production calls leave it None and let
    the function read wall clock.
    """
    if not iso_ts:
        return ""
    from datetime import datetime, timezone

    try:
        # DuckDB renders TIMESTAMPTZ as 'YYYY-MM-DD HH:MM:SS+TZ'. Python's
        # fromisoformat needs T separator and full ±HH:MM offset; massage
        # the string before parsing.
        s = iso_ts.strip().replace(" ", "T", 1)
        # '+08' → '+08:00' (Python is strict about offset width)
        if len(s) >= 3 and s[-3] in ("+", "-") and ":" not in s[-3:]:
            s = s + ":00"
        ts = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return iso_ts  # fallback: better to show something than nothing

    if now is None:
        now = datetime.now(ts.tzinfo if ts.tzinfo else timezone.utc)
    delta = now - ts
    secs = int(delta.total_seconds())
    if secs < 0:
        return ts.strftime("%b %-d")
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    if secs < 86400 * 2:
        return "yesterday"
    if secs < 86400 * 7:
        return f"{secs // 86400}d ago"
    if ts.year == now.year:
        return ts.strftime("%b %-d")
    return ts.strftime("%b %-d %Y")
