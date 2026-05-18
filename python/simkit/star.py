"""Run-starring + Maestro history-lock sync (Phase 3A v1.8 #4 / DECISIONS #65).

Two layers:

* ``set_run_starred`` — pure DB UPDATE on ``runs.starred``. Idempotent,
  raises :class:`RunNotFoundError` if ``run_id`` is missing.
* ``compute_sync_plan`` / ``apply_sync_plan`` — bulk reconciliation
  between the DB's starred set and Maestro's per-history lock state.
  Pure plan computation is separable from execution so the CLI can dry-run.

The sync model is **one-way per invocation, user-selected direction**:

* ``push``: DB is authoritative. For each starred run whose history is
  present in Maestro, ensure the Maestro lock matches. Warn (don't fail)
  when a starred run's history is missing from the session.
* ``pull``: Maestro is authoritative. For each Maestro history whose name
  matches a DB run's history_name, copy the Maestro lock state into
  ``runs.starred``. Maestro histories with no matching DB row are skipped.

Multiple runs CAN share the same history_name (a `pvt run` retry creates
two run.json envelopes both naming the same Maestro history). That's fine
for push (collapse to OR — any starred run forces lock=T) and for pull
(broadcast — all rows sharing the name copy the lock bit).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Literal, Mapping, Optional, Tuple

import duckdb

from simkit.db import transaction
from simkit.errors import RunNotFoundError, SimkitError


# --- single-run DB mutation ---------------------------------------------


_StarAction = Literal["set", "cleared", "noop"]


@dataclass(frozen=True)
class StarResult:
    run_id: str
    history_name: str
    previous: bool
    current: bool
    action: _StarAction


def set_run_starred(
    con: duckdb.DuckDBPyConnection, *,
    run_id: str, starred: bool,
) -> StarResult:
    """UPDATE ``runs.starred`` for ``run_id``.

    Idempotent: a no-op write returns ``action='noop'`` without touching
    the DB. Raises :class:`RunNotFoundError` if ``run_id`` is absent.
    """
    row = con.execute(
        "SELECT starred, history_name FROM runs WHERE run_id = ?", [run_id]
    ).fetchone()
    if row is None:
        raise RunNotFoundError(f"run_id {run_id!r} not found in DB")
    previous = bool(row[0])
    history_name = row[1]
    if previous == starred:
        return StarResult(
            run_id=run_id, history_name=history_name,
            previous=previous, current=previous, action="noop",
        )
    with transaction(con):
        con.execute(
            "UPDATE runs SET starred = ? WHERE run_id = ?",
            [starred, run_id],
        )
    return StarResult(
        run_id=run_id, history_name=history_name,
        previous=previous, current=starred,
        action="set" if starred else "cleared",
    )


# --- sync-plan computation ----------------------------------------------


SyncDirection = Literal["push", "pull"]


@dataclass(frozen=True)
class SyncAction:
    """One unit of work in a sync plan.

    ``kind``:
      * ``maestro_lock``   — call ``set_history_lock(name, True)``
      * ``maestro_unlock`` — call ``set_history_lock(name, False)``
      * ``db_star``        — UPDATE runs SET starred=TRUE WHERE history_name=?
      * ``db_unstar``      — UPDATE runs SET starred=FALSE WHERE history_name=?
    """
    kind: Literal["maestro_lock", "maestro_unlock", "db_star", "db_unstar"]
    history_name: str
    affected_run_ids: Tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SyncPlan:
    direction: SyncDirection
    actions: Tuple[SyncAction, ...]
    warnings: Tuple[str, ...]


def compute_sync_plan(
    *,
    direction: SyncDirection,
    db_rows: Mapping[str, Tuple[bool, Tuple[str, ...]]],
    maestro_lock_map: Mapping[str, bool],
) -> SyncPlan:
    """Diff DB starred-by-history vs Maestro lock-by-history.

    ``db_rows`` maps ``history_name -> (any_starred, (run_id, run_id, ...))``.
    ``any_starred`` is the OR across all DB rows that share this history
    name (a retry creates two runs naming the same history).
    """
    actions: List[SyncAction] = []
    warnings: List[str] = []

    if direction == "push":
        for hist_name, (db_starred, run_ids) in db_rows.items():
            in_maestro = hist_name in maestro_lock_map
            if not in_maestro:
                if db_starred:
                    warnings.append(
                        f"starred run(s) {list(run_ids)} reference history "
                        f"{hist_name!r} which is not present in this Maestro "
                        f"session — Maestro lock skipped"
                    )
                continue
            mae_locked = maestro_lock_map[hist_name]
            if db_starred and not mae_locked:
                actions.append(SyncAction(
                    kind="maestro_lock",
                    history_name=hist_name,
                    affected_run_ids=run_ids,
                ))
            elif (not db_starred) and mae_locked:
                actions.append(SyncAction(
                    kind="maestro_unlock",
                    history_name=hist_name,
                    affected_run_ids=run_ids,
                ))
        return SyncPlan(direction=direction, actions=tuple(actions),
                        warnings=tuple(warnings))

    if direction == "pull":
        for hist_name, mae_locked in maestro_lock_map.items():
            entry = db_rows.get(hist_name)
            if entry is None:
                # Maestro history with no matching DB run — nothing to do.
                continue
            db_starred, run_ids = entry
            if mae_locked and not db_starred:
                actions.append(SyncAction(
                    kind="db_star",
                    history_name=hist_name,
                    affected_run_ids=run_ids,
                ))
            elif (not mae_locked) and db_starred:
                actions.append(SyncAction(
                    kind="db_unstar",
                    history_name=hist_name,
                    affected_run_ids=run_ids,
                ))
        return SyncPlan(direction=direction, actions=tuple(actions),
                        warnings=tuple(warnings))

    raise SimkitError(f"unknown sync direction: {direction!r}")


def load_db_rows(
    con: duckdb.DuckDBPyConnection,
) -> Dict[str, Tuple[bool, Tuple[str, ...]]]:
    """Build the ``{history_name -> (any_starred, run_ids)}`` map for the DB."""
    rows = con.execute(
        "SELECT history_name, starred, run_id FROM runs "
        "ORDER BY history_name, ingested_at"
    ).fetchall()
    grouped: Dict[str, List[Tuple[bool, str]]] = {}
    for hn, starred, rid in rows:
        grouped.setdefault(hn, []).append((bool(starred), rid))
    return {
        hn: (any(s for s, _ in pairs), tuple(rid for _, rid in pairs))
        for hn, pairs in grouped.items()
    }


def apply_sync_plan(
    plan: SyncPlan,
    *,
    con: duckdb.DuckDBPyConnection,
    set_history_lock: Callable[[str, bool], None],
) -> None:
    """Execute a plan against DB + Maestro.

    ``set_history_lock`` should be ``functools.partial`` of
    :func:`simkit.skill_bridge.pvt_runner_set_history_lock` bound to a
    specific session. Caller owns the DB connection lifecycle.
    """
    for act in plan.actions:
        if act.kind == "maestro_lock":
            set_history_lock(act.history_name, True)
        elif act.kind == "maestro_unlock":
            set_history_lock(act.history_name, False)
        elif act.kind == "db_star":
            with transaction(con):
                con.execute(
                    "UPDATE runs SET starred = TRUE WHERE history_name = ?",
                    [act.history_name],
                )
        elif act.kind == "db_unstar":
            with transaction(con):
                con.execute(
                    "UPDATE runs SET starred = FALSE WHERE history_name = ?",
                    [act.history_name],
                )
        else:  # pragma: no cover — guarded by Literal at type-check time
            raise SimkitError(f"unknown sync action kind: {act.kind!r}")
