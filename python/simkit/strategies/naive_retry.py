"""Phase 3A v1 built-in strategy: re-run failed corners up to N times.

No intervention — just retries. Useful for transient license / disk / scheduler
hiccups; useless for real convergence failures (those need ``gmin_bump`` /
``trans_pss_ic`` from v1.1).
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
        """Trigger a re-run of the (currently still-enabled) tests.

        Note v1 caveat: this re-runs ALL enabled tests + corners, not just
        the failed ones. Maestro's per-corner re-run filter lives in
        ``axlRunAllTestsWithCallback`` plus selective enable manipulation —
        that's a v1.1 refinement. For now the orchestrator can leverage
        Maestro's "use cached results for unchanged corners" behaviour
        (axlSetReuseNetlistOption) to make the re-run cheap on passing
        corners.
        """
        history_name = f"{ctx.item_name}__retry{ctx.attempt_number}"
        # Sanitise — Maestro history names are alphanumeric + underscore.
        history_name = "".join(
            c if c.isalnum() or c == "_" else "_" for c in history_name
        )
        ctx.bridge.pvt_runner_run(history_name, session=ctx.session)
        return StrategyResult(
            outcome=StrategyOutcome.UNCHANGED,  # caller re-collects + decides
            notes=f"naive_retry attempt #{ctx.attempt_number}",
            new_history_name=history_name,
        )
