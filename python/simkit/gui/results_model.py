"""Results model for the GUI Results tab (spec §4 cap #1, §10.3, A3).

Spec mandate A3: every result table is a :class:`QAbstractTableModel` +
:class:`QSortFilterProxyModel`. ``QTableWidget`` is banned codebase-wide
(anticipates 240→1000-row sweeps).

This module ships two things:

* :class:`ResultsModel` — the read-only table model backing a *single*
  run's results pane. Columns match spec §6.1 / §10.2 vocabulary:
  ``corner``, ``test``, ``output``, ``value``, ``status``, ``spec``,
  ``spec_status``. ``BackgroundRole`` paints a light-red brush on any
  row where ``status == 'fail'`` or ``spec_status`` is one of
  ``{'fail', 'eval_err'}`` — the two ways the per-row verdict surfaces
  (raw simulator status vs spec-evaluated verdict; see DECISIONS #47).

* :func:`load_rows_for_run` — pure DuckDB read for ``run_id``. Returns
  list-of-dicts keyed identically to ``ResultsModel.COLUMNS`` so the
  caller can hand the rows directly to the model.

Mock-safety: ``BackgroundRole`` returns a ``QBrush`` of a fixed
``QColor(255, 220, 220)``; the colour value is the only design decision
left to impl in the §4 stage-2 spec note. Stage-2 tests construct a
:class:`QApplication` (offscreen) before instantiating the model so the
``QBrush`` / ``QColor`` allocations work without a display server.
"""

from __future__ import annotations

from typing import Any, Iterable, List, Optional, Sequence

import duckdb

from PyQt5.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt5.QtGui import QBrush, QColor


# --- column order is part of the contract -------------------------------
#
# Tests + the view both reference :attr:`ResultsModel.COLUMNS` so any
# rename / reorder has a single source of truth.
_COLUMNS: tuple[str, ...] = (
    "corner",
    "test",
    "output",
    "value",
    "status",
    "spec",
    "spec_status",
)


# Light-red fill for FAIL / eval_err rows. Picked at impl time (spec
# §4 stage-2 explicitly leaves this to the implementer); a soft red so
# the text stays legible under the default Qt palette. The 255/220/220
# tuple matches the "pastel red" common in spreadsheet UIs.
_FAIL_BRUSH = QBrush(QColor(255, 220, 220))


# spec_status values that should highlight a row as failed in addition
# to the raw ``status == 'fail'`` check.
_FAIL_SPEC_STATUSES = frozenset({"fail", "eval_err"})


# Display placeholder for missing/None cell values. Matches the spec
# §4 stage-2 contract.
_MISSING = "—"  # em dash "—"


class ResultsModel(QAbstractTableModel):
    """Table model for ONE run's results.

    Construct with a pre-fetched list of row dicts. The model is
    intentionally dumb — it does not query DuckDB itself; that's
    :func:`load_rows_for_run`'s job. This split keeps the model pure
    in-memory (testable without a DB) and the query trivially mockable.
    """

    COLUMNS: tuple[str, ...] = _COLUMNS

    def __init__(
        self,
        rows: Optional[Iterable[dict]] = None,
        parent: Any = None,
    ):
        super().__init__(parent)
        self._rows: List[dict] = list(rows) if rows is not None else []

    # --- Qt model surface ------------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        # Children of a non-root index don't exist in a flat table.
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
        # Vertical: 1-based row numbers, useful when sorting reorders rows.
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

        record = self._rows[row]

        if role == Qt.DisplayRole:
            value = record.get(self.COLUMNS[col])
            return _format_cell(value)

        if role == Qt.BackgroundRole:
            if _is_fail_row(record):
                return _FAIL_BRUSH
            return None

        return None

    # --- non-Qt helpers --------------------------------------------------

    def rows(self) -> List[dict]:
        """Defensive copy of the backing rows — for inspection in tests."""
        return list(self._rows)


def _format_cell(value: Any) -> str:
    """Coerce a single cell value to display text. ``None`` → em dash."""
    if value is None:
        return _MISSING
    if isinstance(value, str):
        # Treat an empty string as missing — DuckDB sometimes hands back
        # "" for optional VARCHAR columns and "—" reads cleaner.
        if value == "":
            return _MISSING
        return value
    return str(value)


def _is_fail_row(record: dict) -> bool:
    """True if this row should be highlighted as failing."""
    status = record.get("status")
    if isinstance(status, str) and status == "fail":
        return True
    spec_status = record.get("spec_status")
    if isinstance(spec_status, str) and spec_status in _FAIL_SPEC_STATUSES:
        return True
    return False


# --- DB read helper -----------------------------------------------------


def load_rows_for_run(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
) -> List[dict]:
    """Fetch the result rows for ``run_id`` from the DuckDB ``results`` table.

    Returns a list of dicts keyed by :attr:`ResultsModel.COLUMNS`. The
    ``value`` cell is merged from ``value_num`` / ``value_str`` — same
    rule as :func:`simkit.from_db._merge_value` (numeric beats string;
    both null → ``None`` which the model renders as em dash).

    Ordering: ``corner, test, output`` — stable, matches existing
    list-runs / from_db ordering minus the leading ``point`` column
    (Tier-1 Results tab does not surface sweep points; left to Tier-2).

    The caller owns the connection lifetime.
    """
    rows: Sequence[Sequence[Any]] = con.execute(
        """
        SELECT
          corner, test, output,
          value_num, value_str,
          status, spec, spec_status
        FROM results
        WHERE run_id = ?
        ORDER BY corner, test, output
        """,
        [run_id],
    ).fetchall()
    out: List[dict] = []
    for r in rows:
        corner, test, output, value_num, value_str, status, spec, spec_status = r
        out.append({
            "corner": corner,
            "test": test,
            "output": output,
            "value": _merge_value(value_num, value_str),
            "status": status,
            "spec": spec,
            "spec_status": spec_status,
        })
    return out


def _merge_value(value_num: Any, value_str: Any) -> Any:
    """Same rule as :func:`simkit.from_db._merge_value`.

    Numeric wins (more precision than the formatted string), then string,
    then None.
    """
    if value_num is not None:
        return float(value_num)
    if value_str is not None:
        return value_str
    return None
