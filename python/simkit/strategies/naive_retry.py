"""Phase 3A v1 built-in strategy: re-run only the failed corners.

No intervention beyond corner-set narrowing — useful for transient license /
disk / scheduler / queue hiccups; useless for real convergence failures
(those need ``gmin_bump`` / ``trans_pss_ic`` from v1.1, which add their own
session mutations on top).

v1.6 (DECISIONS #62): the apply() now snapshots the current corner-enable
state, narrows it to just the FAIL set, runs once, then restores the
pre-call state in a finally block — so a failed retry leaves Maestro in
the same enable state the caller handed in. (v1.5 was a no-filter re-run
of every enabled corner; that wasted Spectre time on PASS corners.)
"""

from __future__ import annotations

from simkit.strategies.base import (
    Strategy,
    StrategyContext,
    StrategyOutcome,
    StrategyResult,
)


class NaiveRetry(Strategy):
    name = "naive_retry"
    max_attempts = 1  # default: one retry. Sidecar can bump.

    def apply(self, ctx: StrategyContext) -> StrategyResult:
        fail_names = {c for (c, _t, _s) in ctx.failed_corners}
        if not fail_names:
            return StrategyResult(
                outcome=StrategyOutcome.GAVE_UP,
                notes="naive_retry: empty failed_corners set",
            )

        snap = ctx.bridge.pvt_runner_snapshot_corners_enable(
            session=ctx.session,
        )
        snap_names = {n for n, _ in snap}
        # FAIL names from the DB are sub-corner names ("TT_pvt_3") while
        # snapshot/restore_corners_enable operates on union ROW names
        # ("TT_pvt"). Map sub → row by longest-prefix match.
        kept, missing = _map_sub_to_rows(fail_names, snap_names)
        if not kept:
            return StrategyResult(
                outcome=StrategyOutcome.GAVE_UP,
                notes=(
                    f"naive_retry: none of the failed corners "
                    f"({sorted(fail_names)}) match the live corner table "
                    f"({sorted(snap_names)})"
                ),
            )

        target = [(name, name in kept) for name, _en in snap]
        history_name = _sanitize(
            f"{ctx.item_name}__retry{ctx.attempt_number}"
        )

        ctx.bridge.pvt_runner_restore_corners_enable(
            target, session=ctx.session,
        )
        try:
            ctx.bridge.pvt_runner_run(
                history_name, session=ctx.session,
            )
        finally:
            ctx.bridge.pvt_runner_restore_corners_enable(
                snap, session=ctx.session,
            )

        notes = f"naive_retry attempt #{ctx.attempt_number} on {sorted(kept)}"
        if missing:
            notes += f" (skipped — not in live table: {sorted(missing)})"
        return StrategyResult(
            outcome=StrategyOutcome.UNCHANGED,  # caller re-collects + decides
            notes=notes,
            new_history_name=history_name,
        )


def _sanitize(label: str) -> str:
    """Maestro history names: alphanumeric + underscore."""
    return "".join(c if (c.isalnum() or c == "_") else "_" for c in label)


def _map_sub_to_rows(
    fail_names: set[str], snap_names: set[str],
) -> tuple[set[str], set[str]]:
    """Map FAIL sub-corner names back to their union row names.

    DB stores sub-corner names like ``TT_pvt_3``; the bridge snapshot
    operates on row names like ``TT_pvt``. For each fail_name:
      * Exact match in snap_names wins.
      * Else the snap_name that is the LONGEST prefix of fail_name
        followed by ``_`` is selected (so ``TT_pvt_3`` maps to ``TT_pvt``,
        not ``TT``, even when both are in the snap table).

    Returns ``(matched_row_names, unmatched_fail_names)``. Re-running a
    sweep row at row granularity overshoots (re-runs every sub-corner
    in that row), but that's a known v1.6 limitation — per-sub-corner
    enable isn't an axlSKILL primitive at this layer.
    """
    matched: set[str] = set()
    unmatched: set[str] = set()
    for fname in fail_names:
        if fname in snap_names:
            matched.add(fname)
            continue
        candidates = [s for s in snap_names if fname.startswith(s + "_")]
        if not candidates:
            unmatched.add(fname)
            continue
        matched.add(max(candidates, key=len))
    return matched, unmatched
