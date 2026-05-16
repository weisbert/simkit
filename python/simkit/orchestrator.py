"""Phase 3A §5 — Simulation-orchestrator runtime.

v1 execute() now wired against the Phase 3A §3 bridge + §4 strategy framework
(2026-05-16). Real Maestro runs work end-to-end when invoked with a live
``skill_bridge`` module + an open Maestro session.


Implements docs/phase3a_orchestrator_spec.md §3 (orchestrator loop) and
§4 (failure semantics). DECISIONS #50-#52.

v1 ships:
  * ``plan_review(review)`` — pure, side-effect-free; resolves each item to a
    ``PlannedItem`` carrying the sub-corner count + path-existence verdict.
  * ``dry_run(plan)`` — formats the plan to stdout, no Maestro touched.
  * ``synthesize_adhoc_review(...)`` — builds a one-item ``Review`` in memory
    for the ad-hoc CLI escape hatch (``pvt run --tests ... --union ...``).
  * Skeleton ``execute(plan, bridge, *, ingest_cb)`` — fills in once §3 SKILL
    bridge + §4 strategy framework land. Currently raises ``NotImplementedError``
    when called with a live bridge, so the dry-run path is fully usable today.

The split between "plan" and "execute" mirrors what Phase 2's ``pvt corners
build`` does for unions: the plan is a side-effect-free artefact you can
inspect, dry-run, diff, or ship to a teammate; execute is a separate verb
that needs the live Maestro session.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

from simkit.errors import SimkitError
from simkit.ic_source import IcSourceError, resolve_ic_path
from simkit.review import (
    IcFromRef,
    OnFailurePolicy,
    PathIssue,
    Review,
    ReviewItem,
    StrategyEntry,
    load_review,
    validate_paths_exist,
)
from simkit.union import Union, load_union


class OrchestratorError(SimkitError):
    """Base class for orchestrator-level errors."""


class NotImplementedYetError(OrchestratorError):
    """Raised when the user asks for live execution before §3/§4 land."""


# ---------------------------------------------------------------------------
# Plan shape


@dataclass(frozen=True)
class PlannedItem:
    """A ``ReviewItem`` resolved against on-disk sidecars + counted corners.

    ``corner_count`` is the number of sub-corners that would be submitted to
    Maestro for this item (= explode count of the union). ``None`` when the
    union sidecar is missing or unreadable; the orchestrator can still log
    + dry-run the plan, but execute() will refuse to proceed.
    """

    item: ReviewItem
    corner_count: Optional[int]
    union: Optional[Union]
    bundle_resolved: bool   # True if bundle is None (omitted) OR file exists
    path_issues: tuple[PathIssue, ...]


@dataclass(frozen=True)
class RunPlan:
    review: Review
    planned: tuple[PlannedItem, ...]

    @property
    def total_corners(self) -> int:
        return sum((p.corner_count or 0) for p in self.planned)

    @property
    def has_blocking_issues(self) -> bool:
        return any(p.path_issues for p in self.planned)


# ---------------------------------------------------------------------------
# Build the plan


def plan_review(
    review: Review,
    *,
    items_filter: Optional[Sequence[str]] = None,
) -> RunPlan:
    """Resolve a ``Review`` to a ``RunPlan``.

    Reads each item's union sidecar to count the sub-corners; checks each
    bundle path exists if non-null. Items disabled in the sidecar
    (``enabled=false``) are dropped from the plan — they will not appear in
    dry-run output or execution. ``items_filter`` further narrows to a
    user-specified subset; missing names raise ``OrchestratorError``.
    """
    path_issues_by_name: dict[str, list[PathIssue]] = {}
    for issue in validate_paths_exist(review):
        path_issues_by_name.setdefault(issue.item_name, []).append(issue)

    if items_filter is not None:
        wanted = set(items_filter)
        all_names = {it.name for it in review.items}
        missing = wanted - all_names
        if missing:
            raise OrchestratorError(
                f"--items: unknown item name(s) {sorted(missing)}; "
                f"available: {sorted(all_names)}"
            )

    planned: list[PlannedItem] = []
    for item in review.items:
        if items_filter is not None and item.name not in items_filter:
            continue
        if not item.enabled:
            continue

        # Try to load + explode the union to count corners.
        union_obj: Optional[Union] = None
        corner_count: Optional[int] = None
        item_path_issues = list(path_issues_by_name.get(item.name, ()))
        if item.union.exists():
            try:
                union_obj = load_union(item.union)
                from simkit.union import explode  # local import to avoid cycle
                corner_count = len(list(explode(union_obj)))
            except Exception as exc:
                # Surface as a path issue (degrades the dry-run line but
                # doesn't break the loop). Keep going so we collect ALL
                # issues across items in one pass.
                item_path_issues.append(
                    PathIssue(item.name, "union", item.union,
                              f"unloadable: {exc}")
                )

        bundle_resolved = (item.bundle is None) or item.bundle.exists()

        planned.append(PlannedItem(
            item=item,
            corner_count=corner_count,
            union=union_obj,
            bundle_resolved=bundle_resolved,
            path_issues=tuple(item_path_issues),
        ))

    return RunPlan(review=review, planned=tuple(planned))


# ---------------------------------------------------------------------------
# Dry-run formatting


def dry_run(plan: RunPlan, *, stream=None) -> None:
    """Format the plan to ``stream`` (default stdout). No side effects.

    Output shape (aligned table):

        REVIEW review_example  project=example_block  items=5  corners=...
        ---
        [1/5] BT2GRX trans PVT
              tests:    sim_BT2GRX, sim_BT2GTX
              union:    unions/bt2grx_trans.union.json  (21 corners)
              bundle:   bundles/bt2grx_trans.measure.json
              on_fail:  corner=skip item=skip strategies=[naive_retry]
        ...
        ---
        SUMMARY  5 items, 84 corners, 0 disabled, 0 issue(s)
    """
    out = stream if stream is not None else sys.stdout
    review = plan.review
    print(f"REVIEW {review.name}  project={review.project}  "
          f"schema_v={review.review_schema_version}  "
          f"items={len(review.items)}  "
          f"planned={len(plan.planned)}  "
          f"corners={plan.total_corners}", file=out)
    print("---", file=out)

    skipped_disabled = [it for it in review.items if not it.enabled]
    total_planned = len(plan.planned)

    for idx, p in enumerate(plan.planned, start=1):
        item = p.item
        print(f"[{idx}/{total_planned}] {item.name}", file=out)
        print(f"      tests:    {', '.join(item.tests)}", file=out)
        cc = f"{p.corner_count} corners" if p.corner_count is not None else "? corners (union not loaded)"
        union_rel = _relpath(item.union, review.source_path.parent)
        print(f"      union:    {union_rel}  ({cc})", file=out)
        if item.bundle is None:
            print(f"      bundle:   (none — keep current Outputs table)", file=out)
        else:
            bundle_rel = _relpath(item.bundle, review.source_path.parent)
            marker = "" if p.bundle_resolved else "  [MISSING]"
            print(f"      bundle:   {bundle_rel}{marker}", file=out)
        strat_names = [s.name for s in item.on_failure.strategies] or ["(none)"]
        print(f"      on_fail:  corner={item.on_failure.corner_policy} "
              f"item={item.on_failure.item_policy} "
              f"strategies={strat_names}", file=out)
        if p.path_issues:
            for iss in p.path_issues:
                print(f"      ISSUE:    {iss.kind} {iss.path} [{iss.reason}]",
                      file=out)
        print(file=out)

    print("---", file=out)
    issue_count = sum(len(p.path_issues) for p in plan.planned)
    print(f"SUMMARY  {total_planned} items planned, "
          f"{plan.total_corners} corners total, "
          f"{len(skipped_disabled)} disabled (skipped), "
          f"{issue_count} issue(s)", file=out)
    if issue_count > 0:
        print("         (re-run with --strict-paths to exit non-zero on issues)",
              file=out)


def _relpath(p: Path, base: Path) -> str:
    try:
        return str(p.relative_to(base))
    except ValueError:
        return str(p)


# ---------------------------------------------------------------------------
# Ad-hoc one-item review synthesis


def synthesize_adhoc_review(
    *,
    project: str,
    tests: Sequence[str],
    union: Path,
    bundle: Optional[Path] = None,
    item_name: str = "ad-hoc",
    suite_name: str = "adhoc",
    on_failure_dict: Optional[dict] = None,
) -> Review:
    """Build a one-item ``Review`` for the ``pvt run --tests/--union/...`` path.

    The synthesized review never touches disk; it bypasses the basename-
    equals-name check and goes straight to the orchestrator. DECISIONS #50 §4
    covers the ad-hoc escape hatch.
    """
    from simkit.review import (
        _merge_on_failure,
        _strategy_entry_from_dict,
        _validate_on_failure,  # private but stable
    )

    # Build the item-level OnFailurePolicy directly so we don't have to
    # round-trip through the JSON validator's filename rules.
    suite_dict = on_failure_dict or {}
    item_policy = _merge_on_failure(suite_dict, {})

    item = ReviewItem(
        name=item_name,
        tests=tuple(tests),
        union=Path(union).resolve(),
        bundle=(Path(bundle).resolve() if bundle else None),
        enabled=True,
        on_failure=item_policy,
    )
    return Review(
        review_schema_version=1,
        name=suite_name,
        project=project,
        items=(item,),
        # source_path used by relative-path resolution; for synth, point
        # at the union's parent dir as a reasonable base.
        source_path=Path(union).resolve().parent / f"{suite_name}.review.json",
    )


# ---------------------------------------------------------------------------
# Live execution — skeleton, fills in when §3 + §4 land


@dataclass(frozen=True)
class ItemResult:
    item_name: str
    history_names: tuple[str, ...]  # primary + any strategy retries
    run_dirs: tuple[Path, ...]      # PvtSave output dirs
    completed: bool                 # axlRunAllTests returned cleanly
    notes: str = ""


@dataclass(frozen=True)
class ExecuteReport:
    items: tuple[ItemResult, ...]
    snapshot_restored: bool


def _default_ingest(run_json_path: Path, pvtproject_path: Path) -> None:
    """Default ingest_cb: open the project DB, ingest one run.json file.

    Imports are deferred so the orchestrator stays usable without DuckDB
    (e.g. dry-run path doesn't touch DB).
    """
    from simkit.db import bootstrap, connect
    from simkit.ingest import ingest_run_json
    from simkit.project import _parse_pvtproject

    proj = _parse_pvtproject(Path(pvtproject_path).expanduser().resolve())
    db_path = proj.db_root / "simkit.duckdb"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = connect(db_path)
    try:
        bootstrap(con)
        ingest_run_json(con, run_json_path)
    finally:
        con.close()


def _sanitize_history(label: str) -> str:
    """Maestro history names must be alphanumeric + underscore."""
    return "".join(c if (c.isalnum() or c == "_") else "_" for c in label)


def _resolve_results_root(pvtproject_path: Path) -> Path:
    """Compute the Maestro results root that ``axlGetResultsLocation`` returns.

    Per DECISIONS #57 probe: ``axlGetResultsLocation(sdb)`` returns
    ``<sessionDir>/results/maestro`` for a maestro-mode setup. v1.2
    derives ``<sessionDir>`` from the .pvtproject's containing dir on
    the assumption that the project lives in the same dir as
    ``maestro.sdb`` — same convention every v1 dogfood uses. If a
    project ever spans a different layout, this fallback errors loudly
    so the dev path is "add an explicit results_root override field" not
    "silently look in the wrong dir."
    """
    session_dir = Path(pvtproject_path).expanduser().resolve().parent
    candidate = session_dir / "results" / "maestro"
    if not candidate.is_dir():
        raise OrchestratorError(
            f"results root not found at expected location {candidate}. "
            f"v1.2 derives this from the .pvtproject parent; add a session "
            f"override if the layout differs."
        )
    return candidate


def _set_ic_for_item(
    item: ReviewItem,
    bridge,
    session: str,
    pvtproject_path: Path,
    history_by_item: dict[str, str],
    notes: list[str],
) -> Optional[str]:
    """Resolve corner-1's IC for this item's source + set it on every test.

    v1.2 v1 limitation (DECISIONS #57): the consumer item runs all
    corners as one batch with corner-1's IC. v1.2.1 will iterate corners
    one-by-one with per-corner IC. Until then, this helper takes the
    first available corner dir of the source history's results.

    Returns the previous option value (so the orchestrator can restore
    it via :func:`pvt_runner_clear_ic_source` after the run), or
    ``None`` if no IC was set (in which case the orchestrator should
    skip the clear step too).
    """
    ic_from: IcFromRef = item.ic_from  # caller has already null-guarded
    source_name = ic_from.item
    source_history = history_by_item.get(source_name)
    if source_history is None:
        notes.append(
            f"ic_from: source item {source_name!r} has no recorded history "
            f"(did it complete?). Running this item without IC."
        )
        print(f"           ! ic_from: no upstream history; naked retry")
        return None

    try:
        results_root = _resolve_results_root(pvtproject_path)
    except OrchestratorError as exc:
        notes.append(f"ic_from: {exc}")
        print(f"           ! ic_from: {exc}")
        return None

    # v1 simplification: use corner-1 (first non-default corner dir);
    # all corners of this batch share the same IC path. Test name is the
    # consumer's first test (in practice the typical PSS item names ONE
    # test). Multi-test items would need finer-grained handling.
    test_for_ic = item.tests[0]
    explicit_subdir = ic_from.subdir if ic_from.subdir else None
    try:
        resolved = resolve_ic_path(
            results_root, source_history, corner_idx=1,
            test_name=test_for_ic, file_kind=ic_from.file,
            explicit_subdir=explicit_subdir,
        )
    except IcSourceError as exc:
        notes.append(f"ic_from path resolve failed: {exc}")
        print(f"           ! ic_from resolve: {exc}")
        return None

    if resolved is None:
        notes.append(
            f"ic_from: corner 1's spectre.{ic_from.file} not found under "
            f"{results_root}/{source_history}/1/{test_for_ic}/. Naked retry."
        )
        print(f"           ! ic_from: corner-1 IC missing; naked retry")
        return None

    # Set the IC on every test of this consumer item. Capture the
    # FIRST test's prev value as the restore baseline — all tests in
    # the consumer item share the same simulator session in practice
    # (same cell/view), so a single restore covers them.
    prev_value: Optional[str] = None
    for tname in item.tests:
        try:
            pv = bridge.pvt_runner_set_ic_source(
                tname, str(resolved.abs_path), ic_from.mode,
                session=session,
            )
            if prev_value is None:
                prev_value = pv
        except Exception as exc:
            notes.append(
                f"ic_from: pvt_runner_set_ic_source({tname!r}) failed: {exc}. "
                f"Bridge writes to the test's 'additionalArgs' sim option — "
                f"check it's not in a read-only / locked state."
            )
            print(f"           ! ic_from set on {tname!r}: {exc}")
            # If we partially set on earlier tests, attempt to clear them
            # before bailing — best-effort.
            return prev_value if prev_value is not None else ""

    print(
        f"           ic_from: set {ic_from.mode}={resolved.abs_path} "
        f"(from {source_name}, corner=1, subdir={resolved.subdir}) "
        f"[v1.2 v1: all corners share this IC]"
    )
    notes.append(
        f"ic_from: {ic_from.mode}=corner1's {ic_from.file} from {source_name}"
    )
    return prev_value if prev_value is not None else ""


def execute(
    plan: RunPlan,
    bridge,
    *,
    session: str,
    pvtproject_path: Optional[Path] = None,
    history_prefix: str = "orch",
    push_union: bool = True,
    ingest_cb=None,
    item_done_cb=None,
    run_kwargs: Optional[dict] = None,
) -> ExecuteReport:
    """Drive Maestro through the plan via ``bridge``. v1 sync-blocking path.

    Args:
        plan: Output of ``plan_review``.
        bridge: Python module exposing the runner / collector / corners /
                ingest entry points. In production this is
                ``simkit.skill_bridge``; tests pass a mock module of the
                same shape.
        session: Maestro session name (e.g. ``fnxSession0``).
        pvtproject_path: Required only when ``push_union=True``. Path to the
                project's .pvtproject file (the bridge needs it).
        history_prefix: All run histories named ``<prefix>_<item>_<ts>``,
                so post-run cleanup can grep them.
        push_union: When True, push each item's union sidecar before running.
                When False, the orchestrator uses whatever corners are
                currently enabled in the session. Useful for the first
                end-to-end dogfood where the user has already set up
                corners by hand.
        ingest_cb: Optional callable(run_dir_path) — invoked after each
                item's PvtSave. Defaults to running ``pvt ingest`` via
                ``simkit.ingest.ingest_run_dir``.
        item_done_cb: Optional callable(ItemResult) — invoked after each
                item completes (for progress UI).
        run_kwargs: Optional dict forwarded to ``bridge.pvt_runner_run``
                per item. Use to tune ``poll_interval`` /
                ``timeout_sec`` / ``idle_confirm_reads`` /
                ``dispatch_grace_reads`` for unusually short or long
                sims. None = bridge defaults.
    """
    import time

    if pvtproject_path is None:
        raise OrchestratorError(
            "execute() requires pvtproject_path (needed by PvtSave even when "
            "push_union=False)"
        )

    timestamp = int(time.time())
    snap = bridge.pvt_runner_snapshot_test_state(session=session)
    snapshot_restored = False
    item_results: list[ItemResult] = []

    # Track each item's actual_history by name so a downstream consumer
    # with ic_from can find the upstream history dir on disk.
    history_by_item: dict[str, str] = {}

    try:
        for idx, planned in enumerate(plan.planned, start=1):
            item = planned.item
            histories: list[str] = []
            run_dirs: list[Path] = []
            notes: list[str] = []
            completed = False

            # 1. enable only this item's tests
            bridge.pvt_runner_enable_only(list(item.tests), session=session)

            # 2. push union if requested + file exists
            if push_union and item.union.exists():
                bridge.pvt_corners_push(
                    str(item.union),
                    pvtproject_path=pvtproject_path,
                    session=session,
                )
            elif push_union:
                notes.append(f"WARNING: union {item.union} not found, "
                             f"skipping push (using current Maestro state)")

            # 3. push bundle — DEFERRED (orchestrator-side bundle push lands
            #    when pvt_measure_push wiring is needed; for S1 dogfood we
            #    rely on whatever Outputs table the session already has).

            # 3b. ic_from (Phase 3A v1.2, DECISIONS #57) — point Spectre at
            # the upstream item's per-corner IC. v1.2 v1 limitation: we
            # set ONE shared IC path (corner-1's) for the whole batch
            # because per-corner enable-and-run iteration needs a SKILL
            # helper that doesn't exist yet (tracked as v1.2.1). True
            # per-corner-distinct IC will land then. For now this still
            # delivers value: the orchestrator handles the cross-item
            # plumbing + path resolution + restore-on-exit, the user just
            # gets a less-than-ideal "all corners share corner-1's IC"
            # behavior until v1.2.1.
            ic_prev_value: Optional[str] = None
            if item.ic_from is not None:
                ic_prev_value = _set_ic_for_item(
                    item, bridge, session, pvtproject_path,
                    history_by_item, notes,
                )

            # 4. run synchronously
            sanitized = _sanitize_history(item.name)
            history = f"{history_prefix}_{sanitized}_{timestamp}_{idx}"

            print(f"[orch {idx}/{len(plan.planned)}] {item.name!r}: "
                  f"running history={history} (this blocks until sim done)…")
            actual_history = history
            try:
                rv = bridge.pvt_runner_run(
                    history, session=session, **(run_kwargs or {}),
                )
                # rv may be (status, sub) for legacy mocks or
                # (status, sub, actual_name) for the live bridge.
                if isinstance(rv, tuple) and len(rv) >= 3:
                    actual_history = rv[2]
                    if actual_history != history:
                        notes.append(f"Maestro renamed history -> {actual_history}")
                histories.append(actual_history)
                completed = True
            except Exception as exc:
                notes.append(f"axlRunAllTests error: {exc}")
                print(f"           ! run errored: {exc}")

            # 4b. clear the IC we set in 3b — independent of run success
            # so the next item starts clean even if this one errored.
            if item.ic_from is not None and ic_prev_value is not None:
                try:
                    for test_name in item.tests:
                        bridge.pvt_runner_clear_ic_source(
                            test_name, item.ic_from.mode, ic_prev_value,
                            session=session,
                        )
                except Exception as exc:
                    notes.append(f"ic_from clear error: {exc}")
                    print(f"           ! ic clear errored: {exc}")

            # 5. dump via PvtSave
            run_dir = None
            if completed:
                try:
                    run_dir = bridge.pvt_save(
                        actual_history,
                        pvtproject_path=pvtproject_path,
                        session=session,
                    )
                    run_dirs.append(Path(run_dir))
                    print(f"           dumped to {run_dir}")
                except Exception as exc:
                    notes.append(f"PvtSave error: {exc}")
                    print(f"           ! PvtSave errored: {exc}")

            # 6. ingest (default: real ingest into project DB)
            if run_dir:
                try:
                    if ingest_cb is None:
                        _default_ingest(Path(run_dir), pvtproject_path)
                    else:
                        ingest_cb(run_dir)
                    print(f"           ingested")
                except Exception as exc:
                    notes.append(f"ingest error: {exc}")
                    print(f"           ! ingest errored: {exc}")

            # 7. TODO (v1.x): per-corner failure detection + strategy chain.
            #    For the initial dogfood we mark the item completed and
            #    leave per-corner verdict reading to `pvt list` post-run.

            # Capture history for any downstream consumer's ic_from lookup.
            if completed and histories:
                history_by_item[item.name] = histories[-1]

            result = ItemResult(
                item_name=item.name,
                history_names=tuple(histories),
                run_dirs=tuple(run_dirs),
                completed=completed,
                notes=" | ".join(notes),
            )
            item_results.append(result)
            if item_done_cb:
                item_done_cb(result)

    finally:
        try:
            bridge.pvt_runner_restore_test_state(snap, session=session)
            snapshot_restored = True
            print(f"[orch] restored test enable state")
        except Exception as exc:
            print(f"[orch] WARNING: could not restore test state: {exc}")

    return ExecuteReport(
        items=tuple(item_results),
        snapshot_restored=snapshot_restored,
    )
