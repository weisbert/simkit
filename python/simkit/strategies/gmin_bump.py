"""Phase 3A v1.7 built-in strategy: bump Spectre ``gmin`` for FAILed corners.

Lifts the v1.3 pre-run-script mechanism (DECISIONS #57) to inject a
per-corner ``gmin`` override into the worker virtuoso VM. For each FAILed
corner this strategy passes the bumped value to
``asiSetSimOptionVal asi "gmin" <bump>`` inside the per-(test, corner)
pre-run hook, so the override lands in that corner's netlist only and the
user's main-session GUI Spectre Options form stays untouched.

Each ``apply()`` call walks one step of the ``ramp`` (default
``[1e-11, 1e-10, 1e-9]`` — 10×/100×/1000× looser than the Spectre baseline
``1e-12`` measured on fnxSession0 2026-05-18):

    attempt 1 → ramp[0]
    attempt 2 → ramp[1]
    ...
    attempt N → ramp[min(N-1, len(ramp)-1)]   ; ramp re-uses last step

Sidecar params (all optional):
    ramp:        list of numeric or string gmin values. Default
                 ``[1e-11, 1e-10, 1e-9]``.
    option_name: SKILL sim-option name. Default ``"gmin"``; override per
                 simulator (UltraSim / AFS / etc.) if needed.

Per-corner scoping is the same proven mechanism v1.3 ic_from uses —
empirically validated on fnxSession0 for ``additionalArgs``. The asi
``gmin`` probe in DECISIONS #62 follow-up confirmed asiSetSimOptionVal
round-trips on the same per-test asi sessions and the schema entry is
present by default on Spectre; per-corner isolation inherits from the
worker VM lifecycle (one fresh asi per point).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from simkit.pre_run_script import PreRunSpec, write_pre_run_script
from simkit.strategies.base import (
    Strategy,
    StrategyContext,
    StrategyOutcome,
    StrategyResult,
)
from simkit.strategies.naive_retry import _map_sub_to_rows, _sanitize, _trace


DEFAULT_RAMP: tuple[float, ...] = (1e-11, 1e-10, 1e-9)
DEFAULT_OPTION_NAME = "gmin"
# Spectre's documented gmin floor default. Used as the ultimate fallback
# when (a) sidecar doesn't pass ``baseline_value`` AND (b) the live
# auto-probe via ``pvt_runner_get_sim_option_val`` can't reach the test's
# asi (e.g. bridge not loaded, test name not in setupDB). Matches the
# value live-probed on fnxSession0 2026-05-18.
DEFAULT_BASELINE = "1e-12"


class GminBump(Strategy):
    name = "gmin_bump"
    max_attempts = 3  # default; sidecar can override

    def apply(self, ctx: StrategyContext) -> StrategyResult:
        fail_names = {c for (c, _t, _s) in ctx.failed_corners}
        if not fail_names:
            return StrategyResult(
                outcome=StrategyOutcome.GAVE_UP,
                notes="gmin_bump: empty failed_corners set",
            )

        ramp = list(ctx.params.get("ramp", DEFAULT_RAMP))
        if not ramp:
            return StrategyResult(
                outcome=StrategyOutcome.GAVE_UP,
                notes="gmin_bump: empty ramp param — nothing to try",
            )
        ramp_idx = min(ctx.attempt_number - 1, len(ramp) - 1)
        gmin_value = _format_value(ramp[ramp_idx])
        option_name = str(ctx.params.get("option_name", DEFAULT_OPTION_NAME))
        baseline_value, baseline_source = _resolve_baseline_value(
            ctx, option_name,
        )

        snap = ctx.bridge.pvt_runner_snapshot_corners_enable(
            session=ctx.session,
        )
        snap_names = {n for n, _ in snap}
        kept, missing = _map_sub_to_rows(fail_names, snap_names)
        if not kept:
            return StrategyResult(
                outcome=StrategyOutcome.GAVE_UP,
                notes=(
                    f"gmin_bump: none of the failed corners "
                    f"({sorted(fail_names)}) match the live corner table "
                    f"({sorted(snap_names)})"
                ),
            )

        tests = sorted({t for (_c, t, _s) in ctx.failed_corners if t})
        if not tests:
            return StrategyResult(
                outcome=StrategyOutcome.GAVE_UP,
                notes=(
                    "gmin_bump: failed_corners has no test names — "
                    "cannot install per-test pre-run hook"
                ),
            )

        # Embed BOTH the FAIL set names (could be sub-corner names like
        # TT_pvt_3) and their parent row names (TT_pvt) — the worker hook
        # sees the sub-corner name during a sweep row's per-point firing
        # and the bare row name for scalar corners. Cover both cases.
        targeted = sorted(fail_names | kept)
        corner_to_value = {c: gmin_value for c in targeted}

        spec = PreRunSpec(
            item_name=f"{ctx.item_name}_gmin{ctx.attempt_number}",
            mode="gmin_bump",
            corner_to_arg=corner_to_value,
            option_key=option_name,
            baseline_value=baseline_value,
        )
        workdir = Path(
            ctx.params.get("workdir") or tempfile.gettempdir()
        )
        script_path = write_pre_run_script(spec, workdir)

        prior_scripts: dict[str, str] = {}
        for tname in tests:
            try:
                prior_scripts[tname] = ctx.bridge.pvt_runner_get_pre_run_script(
                    tname, session=ctx.session,
                )
            except Exception:
                prior_scripts[tname] = ""

        installed: list[str] = []
        for tname in tests:
            ctx.bridge.pvt_runner_install_pre_run_script(
                tname, str(script_path), session=ctx.session,
            )
            installed.append(tname)

        target = [(name, name in kept) for name, _en in snap]
        history_name = _sanitize(
            f"{ctx.item_name}__gmin{ctx.attempt_number}"
        )
        _trace("gmin_bump", ctx, kept, fail_names)

        ctx.bridge.pvt_runner_restore_corners_enable(
            target, session=ctx.session,
        )
        try:
            ctx.bridge.pvt_runner_run(history_name, session=ctx.session)
        finally:
            # Corners first — even if pre-run teardown raises, leave the
            # enable state matching what the caller handed in.
            try:
                ctx.bridge.pvt_runner_restore_corners_enable(
                    snap, session=ctx.session,
                )
            except Exception:
                pass
            for tname in installed:
                prior = prior_scripts.get(tname, "")
                try:
                    if prior:
                        ctx.bridge.pvt_runner_install_pre_run_script(
                            tname, prior, session=ctx.session,
                        )
                    else:
                        ctx.bridge.pvt_runner_disable_pre_run_script(
                            tname, session=ctx.session,
                        )
                except Exception:
                    pass

        notes = (
            f"gmin_bump attempt #{ctx.attempt_number} on {sorted(kept)} "
            f"with {option_name}={gmin_value} (baseline={baseline_value} "
            f"from {baseline_source})"
        )
        if missing:
            notes += f" (skipped — not in live table: {sorted(missing)})"
        return StrategyResult(
            outcome=StrategyOutcome.UNCHANGED,  # caller re-collects + decides
            notes=notes,
            new_history_name=history_name,
        )


def _format_value(v) -> str:
    """Stringify a ramp value. Strings pass through verbatim; floats use
    ``g`` format so ``1e-10`` round-trips as ``"1e-10"``, not ``"1.0e-10"``."""
    if isinstance(v, str):
        return v
    return f"{v:g}"


def _resolve_baseline_value(ctx, option_name: str) -> tuple[str, str]:
    """Resolve the baseline option value with explicit > probe > default.

    Returns ``(value, source)`` where ``source`` is a short tag for the
    apply-summary log line: ``"sidecar"`` / ``"probe"`` / ``"default"``.

    Phase 3A v1.9 #2 (DECISIONS #68): when the sidecar doesn't pass
    ``baseline_value``, this function reaches into the failing test's
    asi via ``bridge.pvt_runner_get_sim_option_val`` to read whatever
    value Spectre would have used (project may have manually loosened
    gmin to 1e-11 in the Options form; we should restore THAT value
    between per-corner overrides, not the hardcoded "1e-12"). Falls back
    to the DEFAULT_BASELINE constant when probe is unreachable
    (bridge wrapper missing, test name not in setupDB, etc.).
    """
    if "baseline_value" in ctx.params:
        return _format_value(ctx.params["baseline_value"]), "sidecar"

    probe_test = None
    for (_c, t, _s) in ctx.failed_corners:
        if t:
            probe_test = t
            break
    if probe_test is None:
        return DEFAULT_BASELINE, "default"

    bridge = ctx.bridge
    getter = getattr(bridge, "pvt_runner_get_sim_option_val", None)
    if getter is None:
        return DEFAULT_BASELINE, "default"
    try:
        live = getter(probe_test, option_name, session=ctx.session)
    except Exception:
        return DEFAULT_BASELINE, "default"
    if live is None or live == "":
        return DEFAULT_BASELINE, "default"
    return _format_value(live), "probe"
