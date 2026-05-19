"""Run milestone tagging — write side of the spec §15.2 milestone column.

Mirrors :mod:`simkit.label` (slice promotion) but for the freer-form
design-review milestone tag (``PDR`` / ``CDR`` / ``FDR`` / free text).
The ``runs.milestone`` column was added by the v3→v4 DuckDB migration
in :mod:`simkit.schema_sql`; until this module landed the only way to
populate it was a manual SQL UPDATE.

Semantics:

* ``milestone != None`` and ``runs.milestone`` was ``NULL`` → set.
* ``milestone != None`` and ``runs.milestone`` was non-null and
  ``force=False`` → raise :class:`MilestoneConflictError`.
* ``milestone != None`` and ``force=True`` → overwrite.
* ``milestone is None`` → clear unconditionally.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import duckdb

from simkit.db import transaction
from simkit.errors import RunNotFoundError, SimkitError


class MilestoneConflictError(SimkitError):
    """Raised when set_run_milestone would overwrite without ``force=True``."""


_MilestoneAction = Literal["set", "overwritten", "cleared", "noop"]


@dataclass(frozen=True)
class MilestoneResult:
    run_id: str
    previous: Optional[str]
    current: Optional[str]
    action: _MilestoneAction


def set_run_milestone(
    con: duckdb.DuckDBPyConnection,
    *,
    run_id: str,
    milestone: Optional[str],
    force: bool = False,
) -> MilestoneResult:
    """Set or clear ``runs.milestone`` for ``run_id``.

    Pass ``milestone=None`` to clear. Clearing does not require
    ``force=True``.
    """
    if milestone is not None:
        milestone = _validate_milestone(milestone)

    row = con.execute(
        "SELECT milestone FROM runs WHERE run_id = ?", [run_id]
    ).fetchone()
    if row is None:
        raise RunNotFoundError(f"run_id {run_id!r} not found in DB")
    previous: Optional[str] = row[0]

    if milestone is None:
        if previous is None:
            return MilestoneResult(
                run_id=run_id, previous=None, current=None, action="noop",
            )
        with transaction(con):
            con.execute(
                "UPDATE runs SET milestone = NULL WHERE run_id = ?",
                [run_id],
            )
        return MilestoneResult(
            run_id=run_id, previous=previous, current=None, action="cleared",
        )

    # milestone is not None
    if previous is not None and previous != milestone and not force:
        raise MilestoneConflictError(
            f"run_id {run_id!r} already tagged milestone={previous!r}; "
            "pass force=True to overwrite"
        )
    if previous == milestone:
        return MilestoneResult(
            run_id=run_id, previous=previous, current=milestone, action="noop",
        )
    action: _MilestoneAction = (
        "overwritten" if previous is not None else "set"
    )
    with transaction(con):
        con.execute(
            "UPDATE runs SET milestone = ? WHERE run_id = ?",
            [milestone, run_id],
        )
    return MilestoneResult(
        run_id=run_id, previous=previous, current=milestone, action=action,
    )


_MAX_LEN = 64


def _validate_milestone(milestone: str) -> str:
    if not isinstance(milestone, str):  # pragma: no cover - GUI passes str
        raise SimkitError(
            f"milestone must be a string, got {type(milestone).__name__}"
        )
    stripped = milestone.strip()
    if not stripped:
        raise SimkitError("milestone must be a non-empty string")
    if len(stripped) > _MAX_LEN:
        raise SimkitError(
            f"milestone too long (max {_MAX_LEN} chars): {stripped!r}"
        )
    # Reject control characters; otherwise free text is fine — design
    # reviews use idiosyncratic names ("PDR", "CDR-rev2", "tape-out check").
    if any(ord(c) < 0x20 for c in stripped):
        raise SimkitError(
            f"milestone may not contain control characters: {stripped!r}"
        )
    return stripped
