"""Strategy base class + context dataclasses (Phase 3A §4).

A Strategy is invoked by the orchestrator when one or more corners in an
item failed. It applies an intervention (e.g. raise gmin, inject IC),
re-runs the failed corners, then reverts the intervention. v1 ships only
``naive_retry`` (no intervention, just re-run); production strategies
(``gmin_bump``, ``trans_pss_ic``) arrive in v1.1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class StrategyOutcome(str, Enum):
    """High-level outcome after a strategy's attempt(s).

    Values:
        recovered  — at least one previously-failed corner now passes.
        unchanged  — no flip; corners still fail. Try next strategy.
        gave_up    — strategy decided not to try (e.g. failure shape
                     doesn't match what it can fix). Try next strategy.
    """
    RECOVERED = "recovered"
    UNCHANGED = "unchanged"
    GAVE_UP = "gave_up"


@dataclass(frozen=True)
class StrategyContext:
    """What a strategy receives. Frozen so strategies cannot mutate it.

    Attributes:
        session:           Maestro session name (string) — pass through to bridge.
        item_name:         Human label of the suite item being processed.
        failed_corners:    List of (corner_name, test_name, status) tuples that
                           failed on the previous attempt. ``status`` is a
                           free-form string from Phase 1 collector
                           (``sim_err`` / ``eval_err`` / ``unknown``).
        attempt_number:    1-based; first invocation is attempt 1, retry is 2, …
        bridge:            The Python skill_bridge module — strategies call
                           pvt_runner_run, pvt_corners_push, etc. from it.
        params:            User-supplied knobs from the sidecar strategy entry
                           (e.g. ``{"trans_duration": "5ns"}``).
        history_by_item:   Orchestrator-injected map of completed-item-name →
                           recorded history name. Strategies that need an
                           upstream artefact (e.g. trans_pss_ic looking up the
                           IC source's history dir) resolve via this map.
                           ``None`` when invoked outside the orchestrator (e.g.
                           ad-hoc tests of strategies that don't need it).
        pvtproject_path:   Orchestrator-injected absolute path to the
                           ``.pvtproject`` file. Strategies that need on-disk
                           layout (results-root, workdir for the pre-run
                           script) read it from here. ``None`` outside the
                           orchestrator.
    """
    session: str
    item_name: str
    failed_corners: tuple[tuple[str, str, str], ...]
    attempt_number: int
    bridge: Any  # the skill_bridge module
    params: Mapping[str, Any] = field(default_factory=dict)
    history_by_item: Mapping[str, str] | None = None
    pvtproject_path: Any = None  # Path | str | None — kept loose to avoid pathlib import here


@dataclass(frozen=True)
class StrategyResult:
    outcome: StrategyOutcome
    notes: str = ""                 # Human-readable summary for the log.
    new_history_name: str | None = None  # If strategy ran a sim, the history
                                         # name it used (for ingest).


class Strategy:
    """Base class for failure-recovery strategies.

    Subclasses MUST set ``name``. ``max_attempts`` is a class-level default;
    the sidecar can override per-entry.
    """
    name: str = ""
    max_attempts: int = 1

    def __init__(self, *, max_attempts: int | None = None,
                 params: Mapping[str, Any] | None = None):
        if max_attempts is not None:
            self.max_attempts = max_attempts
        self.params = dict(params or {})

    def apply(self, ctx: StrategyContext) -> StrategyResult:
        """Apply intervention + re-run failed corners. Override in subclass."""
        raise NotImplementedError

    def revert(self, ctx: StrategyContext) -> None:
        """Undo any session-level mutation. Override if apply() mutated
        anything; default is no-op (e.g. naive_retry doesn't mutate)."""
        return None
