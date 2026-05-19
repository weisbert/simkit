"""Phase 3A v1.8 built-in strategy: inject upstream trans IC on PSS failure.

The on-failure variant of v1.3 ``ic_from`` (DECISIONS #57). Where ``ic_from``
is an *always-on* item-level field that injects IC into a consumer PSS/HB
item BEFORE its first attempt, ``trans_pss_ic`` injects IC only on a RETRY,
after corners failed on the primary run.

Use it when most corners converge naked but a few stragglers need IC to
push them across, OR when you want to leave a PSS item un-chained for
day-to-day reruns and only fall back to trans-IC injection when the
strategy chain trips.

Mechanism: identical to ``ic_from`` — render a self-contained SKILL
pre-run script that writes ``readns/readic="<path>"`` into each failing
sub-corner's ``additionalArgs`` simulator option, install on the item's
tests, narrow corner-enable to the FAIL row set, fire one
``axlRunAllTests``, restore in finally. The source IC's per-corner path
is resolved via ``ic_source.resolve_ic_path`` against the upstream
item's recorded history (orchestrator-injected ``ctx.history_by_item``).

Sidecar params (all under the strategy entry's ``params:`` block):

    source_item:   (required) name of the upstream item whose results
                   directory holds the per-corner spectre.{ic,fc,dc}.
                   Must precede this item in ``review.items[]``.
    file:          (default ``"ic"``) one of ``"ic"`` / ``"fc"`` / ``"dc"``.
    mode:          (default ``"readic"``) one of ``"readic"`` / ``"readns"``.
                   readic = hard initial condition; readns = nodeset hint.
    subdir:        (optional) explicit per-simulator subdir override.
                   Default auto-detects ``netlist`` (Spectre) → ``psf`` (Alps).
    test_for_ic:   (optional) test name to use when locating the upstream
                   IC files. Default: the first test name appearing in
                   ``failed_corners`` (whose test ran in the upstream too,
                   typically — Phase 3A items share their test set with
                   their precursor).

Failure modes (all return ``GAVE_UP`` with a clear note):

  * ``source_item`` missing from params.
  * ``ctx.history_by_item`` is None or doesn't contain ``source_item``.
  * ``ctx.pvtproject_path`` is None (results_root unknown).
  * Results-root doesn't exist under the expected layout.
  * No IC files resolve for any failed sub-corner.

State-leak safety: ``PreRunSpec.baseline_value=""`` is set on every
script so partial-coverage retries (FAIL=1 of 6 sub-corners in a sweep
row, common case) don't leak the previous sub-corner's
``additionalArgs`` across the shared worker-VM asi session. Same fix
shape as gmin_bump (DECISIONS #63 A6).
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Iterable

from simkit.ic_source import IcSourceError, resolve_ic_path
from simkit.pre_run_script import (
    PreRunSpec,
    build_corner_arg_map,
    write_pre_run_script,
)
from simkit.strategies.base import (
    Strategy,
    StrategyContext,
    StrategyOutcome,
    StrategyResult,
)
from simkit.strategies.naive_retry import _map_sub_to_rows, _sanitize, _trace


DEFAULT_FILE_KIND = "ic"
DEFAULT_MODE = "readic"
_VALID_FILE_KINDS = ("ic", "fc", "dc")
_VALID_MODES = ("readic", "readns")


class TransPssIc(Strategy):
    name = "trans_pss_ic"
    max_attempts = 1  # one IC-injection retry is the typical recipe

    def apply(self, ctx: StrategyContext) -> StrategyResult:
        fail_names = {c for (c, _t, _s) in ctx.failed_corners}
        if not fail_names:
            return StrategyResult(
                outcome=StrategyOutcome.GAVE_UP,
                notes="trans_pss_ic: empty failed_corners set",
            )

        source_item = ctx.params.get("source_item")
        if not source_item:
            return StrategyResult(
                outcome=StrategyOutcome.GAVE_UP,
                notes=(
                    "trans_pss_ic: required param 'source_item' missing — "
                    "set it to the upstream item whose history holds the IC"
                ),
            )

        file_kind = str(ctx.params.get("file", DEFAULT_FILE_KIND))
        mode = str(ctx.params.get("mode", DEFAULT_MODE))
        if file_kind not in _VALID_FILE_KINDS:
            return StrategyResult(
                outcome=StrategyOutcome.GAVE_UP,
                notes=(
                    f"trans_pss_ic: invalid file={file_kind!r}; "
                    f"must be one of {_VALID_FILE_KINDS}"
                ),
            )
        if mode not in _VALID_MODES:
            return StrategyResult(
                outcome=StrategyOutcome.GAVE_UP,
                notes=(
                    f"trans_pss_ic: invalid mode={mode!r}; "
                    f"must be one of {_VALID_MODES}"
                ),
            )
        explicit_subdir = ctx.params.get("subdir") or None

        history_by_item = ctx.history_by_item or {}
        source_history = history_by_item.get(source_item)
        if not source_history:
            return StrategyResult(
                outcome=StrategyOutcome.GAVE_UP,
                notes=(
                    f"trans_pss_ic: source_item={source_item!r} has no "
                    f"recorded history (did it complete? "
                    f"known items: {sorted(history_by_item)})"
                ),
            )

        if ctx.pvtproject_path is None:
            return StrategyResult(
                outcome=StrategyOutcome.GAVE_UP,
                notes=(
                    "trans_pss_ic: pvtproject_path is None — "
                    "strategy cannot locate the results root"
                ),
            )

        pvtproject_path = Path(ctx.pvtproject_path).expanduser().resolve()
        results_root = (
            pvtproject_path.parent / "results" / "maestro"
        )
        if not results_root.is_dir():
            return StrategyResult(
                outcome=StrategyOutcome.GAVE_UP,
                notes=(
                    f"trans_pss_ic: results root {results_root} not found "
                    f"(.pvtproject parent layout differs from convention)"
                ),
            )

        tests = sorted({t for (_c, t, _s) in ctx.failed_corners if t})
        if not tests:
            return StrategyResult(
                outcome=StrategyOutcome.GAVE_UP,
                notes=(
                    "trans_pss_ic: failed_corners has no test names — "
                    "cannot install per-test pre-run hook"
                ),
            )
        test_for_ic = str(ctx.params.get("test_for_ic") or tests[0])

        sub_corner_names, sub_idx_by_name = _pull_live_sub_corner_index(
            ctx.bridge, session=ctx.session,
            pvtproject_path=pvtproject_path,
        )
        if not sub_corner_names:
            return StrategyResult(
                outcome=StrategyOutcome.GAVE_UP,
                notes=(
                    "trans_pss_ic: live corner-table pull returned 0 "
                    "sub-corners — nothing to map"
                ),
            )

        corner_to_ic_path: dict[str, str | None] = {}
        unresolved: list[str] = []
        for fname in sorted(fail_names):
            idx = sub_idx_by_name.get(fname)
            if idx is None:
                unresolved.append(fname)
                continue
            try:
                resolved = resolve_ic_path(
                    results_root, source_history,
                    corner_idx=idx, test_name=test_for_ic,
                    file_kind=file_kind,
                    explicit_subdir=explicit_subdir,
                )
            except IcSourceError as exc:
                unresolved.append(f"{fname} ({exc})")
                continue
            if resolved is None:
                unresolved.append(fname)
                continue
            corner_to_ic_path[fname] = str(resolved.abs_path)

        if not corner_to_ic_path:
            return StrategyResult(
                outcome=StrategyOutcome.GAVE_UP,
                notes=(
                    f"trans_pss_ic: no IC files resolved under "
                    f"{results_root}/{source_history}/ for any failed "
                    f"corner (unresolved: {unresolved})"
                ),
            )

        arg_map = build_corner_arg_map(
            list(corner_to_ic_path.keys()),
            corner_to_ic_path,
            mode,
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
                    f"trans_pss_ic: none of the failed corners "
                    f"({sorted(fail_names)}) match the live corner table "
                    f"({sorted(snap_names)})"
                ),
            )

        spec = PreRunSpec(
            item_name=f"{ctx.item_name}_transic{ctx.attempt_number}",
            mode=mode,
            corner_to_arg=arg_map,
            option_key="additionalArgs",
            # Inherit gmin_bump's A6 safe-write shape (DECISIONS #63):
            # restore additionalArgs="" before overlay so a partial-row
            # FAIL set doesn't leak the previous sub-corner's IC arg
            # across the shared worker-VM asi session.
            baseline_value="",
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

        # Snapshot each test's prior additionalArgs value (Phase 3A v1.9 #3,
        # gap #1 closeout). Sentinel = bridge lacks the v1.9 #2 getter or
        # the per-test probe raised; in that case fall back to v1.3-era
        # clear-to-empty cleanup. Otherwise the finally block will restore
        # each test's pre-strategy value byte-identically.
        _NO_SNAPSHOT = object()
        prior_additional_args: dict[str, object] = {}
        probe = getattr(ctx.bridge, "pvt_runner_get_sim_option_val", None)
        added_args_snapshot_notes: list[str] = []
        if probe is None:
            for tname in tests:
                prior_additional_args[tname] = _NO_SNAPSHOT
            added_args_snapshot_notes.append(
                "bridge lacks pvt_runner_get_sim_option_val "
                "(clear-to-empty fallback)"
            )
        else:
            for tname in tests:
                try:
                    prior_additional_args[tname] = probe(
                        tname, "additionalArgs", session=ctx.session,
                    )
                except Exception as exc:
                    prior_additional_args[tname] = _NO_SNAPSHOT
                    added_args_snapshot_notes.append(
                        f"additionalArgs snapshot on {tname!r} failed: {exc}"
                    )

        installed: list[str] = []
        for tname in tests:
            ctx.bridge.pvt_runner_install_pre_run_script(
                tname, str(script_path), session=ctx.session,
            )
            installed.append(tname)

        target = [(name, name in kept) for name, _en in snap]
        history_name = _sanitize(
            f"{ctx.item_name}__transic{ctx.attempt_number}"
        )
        _trace("trans_pss_ic", ctx, kept, fail_names)

        ctx.bridge.pvt_runner_restore_corners_enable(
            target, session=ctx.session,
        )
        try:
            ctx.bridge.pvt_runner_run(history_name, session=ctx.session)
        finally:
            # Corners first — same shape as gmin_bump: even if pre-run
            # teardown raises, leave enable state matching what the
            # caller handed in.
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
            # additionalArgs restore (Phase 3A v1.9 #3, gap #1 closeout).
            # Per-test write-back of the snapshot taken pre-install. Mode
            # plumbed through to keep clear_ic_source's validation happy;
            # the SKILL helper writes the provided value verbatim into
            # additionalArgs regardless of mode tag.
            for tname in installed:
                prev = prior_additional_args.get(tname, _NO_SNAPSHOT)
                if prev is _NO_SNAPSHOT or prev is None:
                    restore_value = ""
                else:
                    restore_value = str(prev)
                try:
                    ctx.bridge.pvt_runner_clear_ic_source(
                        tname, mode, restore_value, session=ctx.session,
                    )
                except Exception:
                    pass

        mapped = sorted(corner_to_ic_path)
        notes = (
            f"trans_pss_ic attempt #{ctx.attempt_number} on {mapped} "
            f"via {mode} from {source_item!r}/{source_history!r}"
        )
        if added_args_snapshot_notes:
            notes += (
                " [additionalArgs: " + "; ".join(added_args_snapshot_notes) + "]"
            )
        if unresolved:
            notes += f" (no IC for: {unresolved})"
        if missing:
            notes += f" (skipped — not in live table: {sorted(missing)})"
        return StrategyResult(
            outcome=StrategyOutcome.UNCHANGED,  # caller re-collects + decides
            notes=notes,
            new_history_name=history_name,
        )


def _pull_live_sub_corner_index(
    bridge, *, session: str, pvtproject_path: Path,
) -> tuple[list[str], dict[str, int]]:
    """Pull live Maestro corner table → ordered sub-corner names + 1-based index map.

    Mirrors the orchestrator's ``_load_live_corner_table`` + ``explode``
    sequence but kept local to the strategy so it stays self-contained.
    Returns ``([], {})`` on any failure (caller treats as GAVE_UP).
    """
    from simkit.union import explode, load_union

    tdir = Path(tempfile.mkdtemp(prefix="simkit_transic_"))
    target = tdir / "transic_pull.union.json"
    try:
        bridge.pvt_corners_pull(
            str(target),
            pvtproject_path=Path(pvtproject_path),
            session=session,
        )
        u = load_union(target)
    except Exception:
        return [], {}
    finally:
        shutil.rmtree(tdir, ignore_errors=True)

    names = [sc.sub_corner_name for sc in explode(u)]
    idx_by_name = {n: i for i, n in enumerate(names, start=1)}
    return names, idx_by_name


__all__ = ["TransPssIc"]
