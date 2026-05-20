"""Cross-milestone trend: align N slices side-by-side (G-6 / FDR-6).

``simkit.diff`` answers "what changed between *two* runs". A design
review asks a different question: "how did this number move across
PDR → CDR → FDR?". That is an N-way alignment, not a pairwise delta.

This module is the data layer behind ``pvt trend`` and the GUI Trend
tab. Three pieces:

* :func:`resolve_trend_column` — turn one identifier (label, run_id
  prefix, *or milestone tag*) into a :class:`TrendColumn`. Unlike
  :func:`simkit.diff.resolve_slice`, a milestone that matches several
  runs is **not** ambiguous — the most recent run wins, because that is
  the run a designer means by "the CDR result".
* :func:`compute_trend` — resolve every slice, align their result rows
  on ``(test, corner, point, output)``, emit :class:`TrendRow` records
  each carrying one :class:`TrendCell` per column.
* :class:`TrendRow.direction` — a cheap monotonicity verdict over the
  numeric cells so the renderer can flag a number that is drifting.

The column order is the order the user passed the slices — the caller
is expected to pass them oldest-milestone-first (PDR, CDR, FDR).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

import duckdb

from simkit.diff import _load_results_keyed, resolve_slice
from simkit.errors import SliceNotFoundError

_Key = Tuple[str, str, int, str]  # (test, corner, point, output)


@dataclass(frozen=True)
class TrendColumn:
    """One resolved slice — a column in the trend table."""

    identifier: str  # what the user typed
    run_id: str
    label: Optional[str]
    milestone: Optional[str]
    timestamp: str  # ISO-ish string, already CAST to VARCHAR by the query

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def display(self) -> str:
        """Short human label for a column header."""
        if self.milestone:
            return self.milestone
        if self.label:
            return self.label
        return self.run_id[:8]


@dataclass(frozen=True)
class TrendCell:
    """One run's value for one (test, corner, point, output) key."""

    present: bool
    value: Any  # float | str | None
    status: Optional[str]
    spec_status: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TrendRow:
    """One result key, with a cell per column (same order as columns)."""

    test: str
    corner: str
    point: int
    output: str
    cells: Tuple[TrendCell, ...]
    is_sentinel: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "test": self.test,
            "corner": self.corner,
            "point": self.point,
            "output": self.output,
            "cells": [c.to_dict() for c in self.cells],
            "is_sentinel": self.is_sentinel,
            "direction": self.direction,
            "varies": self.varies,
        }

    @property
    def _numeric_values(self) -> List[float]:
        out: List[float] = []
        for c in self.cells:
            if not c.present:
                continue
            v = c.value
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                out.append(float(v))
        return out

    @property
    def direction(self) -> Optional[str]:
        """Monotonicity of the numeric cells, left-to-right.

        ``"up"`` / ``"down"`` — every step moves the same way (at least
        one strict step); ``"flat"`` — two or more equal values;
        ``"mixed"`` — steps disagree; ``None`` — fewer than two numeric
        cells, so no trend can be claimed.
        """
        nums = self._numeric_values
        if len(nums) < 2:
            return None
        ups = downs = 0
        for a, b in zip(nums, nums[1:]):
            if b > a:
                ups += 1
            elif b < a:
                downs += 1
        if ups and downs:
            return "mixed"
        if ups:
            return "up"
        if downs:
            return "down"
        return "flat"

    @property
    def varies(self) -> bool:
        """True iff the present cells do not all hold the same value."""
        vals = [c.value for c in self.cells if c.present]
        if len(vals) < 2:
            return False
        first = vals[0]
        return any(v != first for v in vals[1:])


@dataclass(frozen=True)
class TrendResult:
    columns: Tuple[TrendColumn, ...]
    rows: Tuple[TrendRow, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "columns": [c.to_dict() for c in self.columns],
            "rows": [r.to_dict() for r in self.rows],
        }


# ---------------------------------------------------------------------------
# Slice resolution
# ---------------------------------------------------------------------------

def _resolve_milestone_latest(
    con: duckdb.DuckDBPyConnection, milestone: str,
) -> Optional[str]:
    """Return the most-recent run tagged ``milestone``, or None."""
    row = con.execute(
        """
        SELECT run_id FROM runs
        WHERE milestone = ?
        ORDER BY timestamp DESC, run_id
        LIMIT 1
        """,
        [milestone],
    ).fetchone()
    return None if row is None else row[0]


def _column_for_run(
    con: duckdb.DuckDBPyConnection, identifier: str, run_id: str,
) -> TrendColumn:
    row = con.execute(
        "SELECT label, milestone, CAST(timestamp AS VARCHAR) "
        "FROM runs WHERE run_id = ?",
        [run_id],
    ).fetchone()
    label, milestone, ts = (None, None, "") if row is None else row
    return TrendColumn(
        identifier=identifier,
        run_id=run_id,
        label=label,
        milestone=milestone,
        timestamp="" if ts is None else str(ts),
    )


def resolve_trend_column(
    con: duckdb.DuckDBPyConnection, identifier: str,
) -> TrendColumn:
    """Resolve ``identifier`` to a :class:`TrendColumn`.

    Resolution order: label exact match, then run_id prefix (both via
    :func:`simkit.diff.resolve_slice`), then **milestone tag** — and a
    milestone matching several runs resolves to the newest one rather
    than raising :class:`AmbiguousSliceError`.
    """
    if not identifier:
        raise SliceNotFoundError("slice identifier must be a non-empty string")
    try:
        run_id = resolve_slice(con, identifier)
    except SliceNotFoundError:
        run_id = _resolve_milestone_latest(con, identifier)
        if run_id is None:
            raise SliceNotFoundError(
                f"no run matches identifier {identifier!r} "
                "(tried label exact match, run_id prefix, milestone tag)"
            )
    return _column_for_run(con, identifier, run_id)


# ---------------------------------------------------------------------------
# Trend computation
# ---------------------------------------------------------------------------

def compute_trend(
    con: duckdb.DuckDBPyConnection,
    *,
    slices: List[str],
) -> TrendResult:
    """Align ``slices`` side-by-side into a :class:`TrendResult`.

    ``slices`` must hold at least two identifiers; the column order is
    preserved (pass oldest milestone first). The same run may appear
    twice — no de-duplication, the caller's column count is honoured.
    """
    if len(slices) < 2:
        raise ValueError("trend needs at least two slices")

    columns = tuple(resolve_trend_column(con, s) for s in slices)

    # Load each distinct run once; reuse diff's keyed loader so trend and
    # diff cannot drift on how a result row maps to a value.
    per_run: Dict[str, Dict[_Key, Tuple[Any, str, Optional[str], Optional[str]]]] = {}
    for col in columns:
        if col.run_id not in per_run:
            per_run[col.run_id] = _load_results_keyed(con, col.run_id)

    all_keys = set()
    for keyed in per_run.values():
        all_keys |= keyed.keys()

    rows: List[TrendRow] = []
    for key in sorted(all_keys):
        test, corner, point, output = key
        cells: List[TrendCell] = []
        for col in columns:
            keyed = per_run[col.run_id]
            if key in keyed:
                value, status, _spec, spec_status = keyed[key]
                cells.append(TrendCell(
                    present=True, value=value,
                    status=status, spec_status=spec_status,
                ))
            else:
                cells.append(TrendCell(
                    present=False, value=None,
                    status=None, spec_status=None,
                ))
        rows.append(TrendRow(
            test=test, corner=corner, point=point, output=output,
            cells=tuple(cells),
            is_sentinel=(output == "__sim_status__"),
        ))

    return TrendResult(columns=columns, rows=tuple(rows))
