"""Run labelling: promote a run to a slice (or demote back to a draft).

Implements the DB-layer side of ``pvt label`` (TODO §5). A non-null
``runs.label`` means the run has been promoted to a slice (DECISIONS #11)
and is retained permanently; a null label is a draft run, eligible for
future garbage collection.

The set/clear/force semantics are:

* ``label != None`` and ``runs.label`` was ``NULL`` → set; ``action='set'``.
* ``label != None`` and ``runs.label`` was non-null and ``force=False`` →
  raise :class:`LabelConflictError`. Use ``force=True`` to overwrite.
* ``label != None`` and ``runs.label`` was non-null and ``force=True`` →
  ``action='overwritten'`` (carrying the previous value).
* ``label is None`` → clear unconditionally (``action='cleared'`` or
  ``action='noop'`` if already null). No ``--force`` required.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import duckdb

from simkit.db import transaction
from simkit.errors import LabelConflictError, RunNotFoundError, SimkitError


_LabelAction = Literal["set", "overwritten", "cleared", "noop"]


@dataclass(frozen=True)
class LabelResult:
    run_id: str
    previous: Optional[str]
    current: Optional[str]
    action: _LabelAction


def set_run_label(
    con: duckdb.DuckDBPyConnection,
    *,
    run_id: str,
    label: Optional[str],
    force: bool = False,
) -> LabelResult:
    """Set or clear ``runs.label`` for ``run_id``.

    Pass ``label=None`` to clear (demote slice → draft run). Clearing
    does not require ``force=True``.
    """
    if label is not None:
        label = _validate_label(label)

    row = con.execute(
        "SELECT label FROM runs WHERE run_id = ?", [run_id]
    ).fetchone()
    if row is None:
        raise RunNotFoundError(f"run_id {run_id!r} not found in DB")
    previous: Optional[str] = row[0]

    if label is None:
        if previous is None:
            return LabelResult(
                run_id=run_id, previous=None, current=None,
                action="noop",
            )
        with transaction(con):
            con.execute(
                "UPDATE runs SET label = NULL WHERE run_id = ?", [run_id]
            )
        return LabelResult(
            run_id=run_id, previous=previous, current=None,
            action="cleared",
        )

    # label is not None
    if previous is not None and not force:
        raise LabelConflictError(
            f"run_id {run_id!r} already has label {previous!r}; "
            "pass --force to overwrite"
        )
    action: _LabelAction = "overwritten" if previous is not None else "set"
    with transaction(con):
        con.execute(
            "UPDATE runs SET label = ? WHERE run_id = ?",
            [label, run_id],
        )
    return LabelResult(
        run_id=run_id, previous=previous, current=label, action=action,
    )


def _validate_label(label: str) -> str:
    if not isinstance(label, str):  # pragma: no cover - argparse passes str
        raise SimkitError(f"label must be a string, got {type(label).__name__}")
    stripped = label.strip()
    if not stripped:
        raise SimkitError("label must be a non-empty string")
    if "\n" in stripped or "\r" in stripped:
        raise SimkitError("label must not contain newline characters")
    if len(stripped) > 200:
        raise SimkitError(
            f"label too long ({len(stripped)} chars; max 200)"
        )
    return stripped
