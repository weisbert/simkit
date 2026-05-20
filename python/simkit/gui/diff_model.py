"""Diff results model — table backing for :class:`DiffTab` (spec §10).

Wraps a ``list[DiffRow]`` from :mod:`simkit.diff` in a
``QAbstractTableModel`` so the view can drive a ``QSortFilterProxyModel``
on top (mandate A3). Column order is intentionally narrower than the CLI
text renderer's: the GUI table omits ``point``, ``status_a``,
``status_b`` are folded into the row colouring, etc. The full DiffRow
remains available via :meth:`diff_row_at` for cell-level drill-downs.

Why three colours not two: the spec §10.2 vocabulary distinguishes a
*regression* (pass→fail, red) from a *recovery* (fail→pass, green) so
the user can sweep the table for the first kind without sorting; a
"value-only delta with no verdict change" is the third visually-distinct
case (yellow). Unchanged rows stay default — they make up the bulk and
the user filters them out via the filter combo.
"""

from __future__ import annotations

from typing import Any, List, Optional

from PyQt5.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt
from PyQt5.QtGui import QBrush, QColor

from simkit.diff import DiffRow


# Cell brushes per spec §10.2. Pastel tones so the text stays legible
# under the default Qt palette (same family as ResultsModel's
# 255/220/220 pastel red).
_BRUSH_REGRESSION = QBrush(QColor(0xFF, 0xD0, 0xD0))   # pass → fail
_BRUSH_RECOVERY   = QBrush(QColor(0xD0, 0xFF, 0xD0))   # fail → pass
_BRUSH_VALUE      = QBrush(QColor(0xFF, 0xF5, 0xB3))   # value changed, verdict same

_MISSING = "—"


# Columns published to the view. The order is contract; tests pin it.
_COLUMNS: tuple[str, ...] = (
    "test",
    "corner",
    "output",
    "value_a",
    "value_b",
    "status_a",
    "status_b",
    "abs_delta",
    "rel_delta",
    "spec_a",
    "spec_b",
)


# Statuses that count as "fail-like" for the regression/recovery
# classifier. ``eval_err`` is treated as a failure for the spec verdict
# axis (matches ResultsModel's _FAIL_SPEC_STATUSES).
_FAIL_LIKE = frozenset({"fail", "eval_err"})


class DiffResultsModel(QAbstractTableModel):
    """Read-only table model over a list of :class:`DiffRow`."""

    COLUMNS: tuple[str, ...] = _COLUMNS

    def __init__(
        self,
        rows: Optional[List[DiffRow]] = None,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self._rows: List[DiffRow] = list(rows) if rows is not None else []

    # --- Qt model surface ------------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self.COLUMNS)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.DisplayRole,
    ) -> Any:
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            if 0 <= section < len(self.COLUMNS):
                return self.COLUMNS[section]
            return None
        return section + 1

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row = index.row()
        col = index.column()
        if not (0 <= row < len(self._rows)):
            return None
        if not (0 <= col < len(self.COLUMNS)):
            return None

        diff_row = self._rows[row]

        if role == Qt.DisplayRole:
            return _format_cell(getattr(diff_row, self.COLUMNS[col], None))

        if role == Qt.BackgroundRole:
            return _row_brush(diff_row)

        return None

    # --- non-Qt helpers --------------------------------------------------

    def rows(self) -> List[DiffRow]:
        """Defensive copy of the backing rows — for inspection in tests."""
        return list(self._rows)

    def diff_row_at(self, row: int) -> Optional[DiffRow]:
        """Return the underlying :class:`DiffRow` at ``row`` (or None)."""
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    # --- proxy-filter predicates ----------------------------------------
    #
    # Wrapped by a QSortFilterProxyModel in the view layer. Both predicates
    # take a model-row index (not a proxy index); the view's proxy passes
    # ``source_row`` straight through.

    def changed_only_filter(self, row: int) -> bool:
        """True iff row has any delta: value or status changed, or
        sentinel transitions between the two slices."""
        dr = self.diff_row_at(row)
        if dr is None:
            return False
        if dr.kind != "match":
            # only_a / only_b / status_mismatch are all "changed"
            return True
        if dr.status_a != dr.status_b:
            return True
        if dr.value_a != dr.value_b:
            return True
        if dr.spec_a != dr.spec_b:
            return True
        if dr.spec_status_a != dr.spec_status_b:
            return True
        return False

    def verdict_flipped_filter(self, row: int) -> bool:
        """True iff the spec verdict flipped (both sides captured a
        non-None ``spec_status`` and they differ). Mirrors
        :pyattr:`DiffRow.spec_changed`."""
        dr = self.diff_row_at(row)
        if dr is None:
            return False
        return dr.spec_changed


def _format_cell(value: Any) -> str:
    """Render one cell value as display text. ``None`` → em dash."""
    if value is None:
        return _MISSING
    if isinstance(value, float):
        # Compact float rendering — full precision lives in the DiffRow
        # object, accessible via ``diff_row_at()`` for tooltip overlays.
        if value == 0.0:
            # Collapse IEEE -0.0 so an unchanged delta renders "0", not "-0".
            value = 0.0
        return f"{value:g}"
    return str(value)


def _row_brush(dr: DiffRow) -> Optional[QBrush]:
    """Return the BackgroundRole brush for ``dr``, or None for unchanged.

    Priority: regression (pass→fail) beats recovery (fail→pass) beats
    "value-only changed". An unchanged row returns None so Qt uses the
    default alternating-row palette.
    """
    sa, sb = dr.status_a, dr.status_b
    a_fail = sa in _FAIL_LIKE
    b_fail = sb in _FAIL_LIKE

    # Verdict flip first — spec verdict is the user's primary signal.
    if dr.spec_status_a is not None and dr.spec_status_b is not None:
        a_spec_fail = dr.spec_status_a in _FAIL_LIKE
        b_spec_fail = dr.spec_status_b in _FAIL_LIKE
        if (not a_spec_fail) and b_spec_fail:
            return _BRUSH_REGRESSION
        if a_spec_fail and (not b_spec_fail):
            return _BRUSH_RECOVERY

    # Raw simulator-status flip as a fallback signal when no spec was
    # captured (v1 dumps) or the spec verdict didn't move.
    if (not a_fail) and b_fail:
        return _BRUSH_REGRESSION
    if a_fail and (not b_fail):
        return _BRUSH_RECOVERY

    # only_a / only_b — yellow, since they're "changed" but not a verdict
    # flip on a stable key.
    if dr.kind in ("only_a", "only_b"):
        return _BRUSH_VALUE

    if dr.value_a != dr.value_b:
        return _BRUSH_VALUE

    return None
