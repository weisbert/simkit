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
from simkit.failures import (
    FailedCorner,
    auto_retry_corners,
    find_failed_corners,
)
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
from simkit.strategies import get_builtin
from simkit.strategies.base import StrategyContext, StrategyOutcome
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
class StrategyAttempt:
    """One pass through a strategy's apply() — for the audit log.

    ``corners_remaining`` is what's still FAIL after this attempt's ingest
    + re-query; the orchestrator stops the chain when it reaches empty.
    """
    strategy_name: str
    attempt_number: int
    outcome: str                          # "recovered" | "unchanged" | "gave_up"
    history_name: str | None
    run_dir: Path | None
    corners_targeted: tuple[str, ...]
    corners_remaining: tuple[str, ...]
    notes: str = ""


@dataclass(frozen=True)
class ItemResult:
    item_name: str
    history_names: tuple[str, ...]  # primary + any strategy retries
    run_dirs: tuple[Path, ...]      # PvtSave output dirs
    completed: bool                 # axlRunAllTests returned cleanly
    notes: str = ""
    strategy_attempts: tuple[StrategyAttempt, ...] = ()
    final_failed_corners: tuple[str, ...] = ()


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


def _default_query_failed(
    pvtproject_path: Path, run_id: str,
) -> tuple[FailedCorner, ...]:
    """Default query_failed_cb: open the project DB read-only, query FAIL
    corners for one run_id.

    Passes ``include_eval_err=True`` so eval-only failures surface in the
    ItemResult report; ``auto_retry_corners`` filters them out of the
    retry chain (DECISIONS #62 D2).
    """
    from simkit.db import connect
    from simkit.project import _parse_pvtproject

    proj = _parse_pvtproject(Path(pvtproject_path).expanduser().resolve())
    db_path = proj.db_root / "simkit.duckdb"
    con = connect(db_path)
    try:
        return find_failed_corners(con, run_id, include_eval_err=True)
    finally:
        con.close()


def _load_run_id(run_path: Path) -> str:
    """Extract the run_id key from run.json.

    Accepts either the run.json file path (what ``bridge.pvt_save`` actually
    returns) or the enclosing run dir. Raises if the file is missing or
    malformed (caller treats as a chain abort).

    Envelope shape is ``{schema_version, run: {run_id, ...}, results, ...}``
    — same as what :func:`simkit.ingest.ingest_run_json` reads.
    """
    import json

    p = Path(run_path)
    if p.is_dir():
        p = p / "run.json"
    return json.loads(p.read_text())["run"]["run_id"]


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


def _resolve_one_corner_ic(
    ic_from: IcFromRef,
    source_history: str,
    corner_idx: int,
    test_name: str,
    results_root: Path,
):
    """Wrapper around ic_source.resolve_ic_path that returns None on any
    failure (caller treats that as "naked retry" per DECISIONS #57)."""
    try:
        return resolve_ic_path(
            results_root, source_history, corner_idx=corner_idx,
            test_name=test_name, file_kind=ic_from.file,
            explicit_subdir=ic_from.subdir if ic_from.subdir else None,
        )
    except IcSourceError:
        return None


def _execute_batch_item(
    item, idx, total, bridge, session, pvtproject_path,
    history_prefix, timestamp, run_kwargs, ingest_cb, notes,
):
    """Run all of an item's corners in one axlRunAllTests batch.

    The original v1 execution path — used when ``item.ic_from`` is None.
    Returns ``(histories, run_dirs, completed)``.
    """
    sanitized = _sanitize_history(item.name)
    history = f"{history_prefix}_{sanitized}_{timestamp}_{idx}"

    print(f"[orch {idx}/{total}] {item.name!r}: "
          f"running history={history} (this blocks until sim done)…")
    actual_history = history
    completed = False
    try:
        rv = bridge.pvt_runner_run(
            history, session=session, **(run_kwargs or {}),
        )
        if isinstance(rv, tuple) and len(rv) >= 3:
            actual_history = rv[2]
            if actual_history != history:
                notes.append(f"Maestro renamed history -> {actual_history}")
        completed = True
    except Exception as exc:
        notes.append(f"axlRunAllTests error: {exc}")
        print(f"           ! run errored: {exc}")
        return ([], [], False)

    histories = [actual_history]
    run_dirs: list[Path] = []
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
        return (histories, run_dirs, completed)

    try:
        if ingest_cb is None:
            _default_ingest(Path(run_dirs[0]), pvtproject_path)
        else:
            ingest_cb(str(run_dirs[0]))
        print(f"           ingested")
    except Exception as exc:
        notes.append(f"ingest error: {exc}")
        print(f"           ! ingest errored: {exc}")
    return (histories, run_dirs, completed)


def _pick_baseline_corner(
    live_union: Union,
    explicit_override: str | None,
) -> str:
    """Return the corner name to keep enabled as baseline (DECISIONS #59).

    Pure function. ``live_union`` is the current Maestro corner-table state
    pulled via ``pvt_corners_pull``; ``explicit_override`` is the user's
    sidecar ``baseline_corner:`` field if present.

    Selection rule (A4 policy):
      1. If ``explicit_override`` is non-None, it must match a row in
         ``live_union`` exactly (case-sensitive). Sweep-row overrides are
         allowed — user knows what they're doing — but warned in the caller.
      2. Else auto-pick the first scalar (non-sweep) row in declared
         order. A row is scalar iff it has neither sweep_var_keys nor
         sweep_model_indices populated.

    Raises ``OrchestratorError`` if no candidate can be found, so the
    caller does NOT silently let Maestro fall back to a ``nom`` baseline
    (which would re-introduce the 24-include cosmetic bug v1.4 closes).
    """
    table_names = [r.row_name for r in live_union.rows]
    if explicit_override is not None:
        if explicit_override not in table_names:
            raise OrchestratorError(
                f"baseline_corner={explicit_override!r} does not match any "
                f"corner in the live table (available: {table_names!r})"
            )
        return explicit_override
    for row in live_union.rows:
        if not row.sweep_var_keys and not row.sweep_model_indices:
            return row.row_name
    raise OrchestratorError(
        "ic_from item has no scalar (non-sweep) baseline corner in the "
        "live corner table; declare baseline_corner: \"<name>\" on the "
        "item's review entry, or add a scalar corner to the union"
    )


def _load_live_corner_table(
    bridge, *, session: str, pvtproject_path: Path,
) -> Union:
    """Pull the live ADE-XL corner table via bridge into a temp sidecar
    and parse it as a :class:`Union`. Used by ``_execute_ic_chained_item``
    to feed ``_pick_baseline_corner``.

    SKILL ``pvtCornersPull`` requires ``outPath`` end with ``.union.json``
    AND emits ``name = basename`` into the JSON. ``load_union`` requires
    ``name == basename``. Pull to a fixed ``baselinepick.union.json`` so
    all three constraints agree without a rename dance.
    """
    import tempfile
    tdir = Path(tempfile.mkdtemp(prefix="simkit_baseline_pick_"))
    target = tdir / "baselinepick.union.json"
    try:
        bridge.pvt_corners_pull(
            str(target),
            pvtproject_path=pvtproject_path,
            session=session,
        )
        return load_union(target)
    finally:
        shutil.rmtree(tdir, ignore_errors=True)


def _execute_ic_chained_item(
    item, idx, total, planned_union,
    bridge, session, pvtproject_path,
    history_prefix, timestamp, run_kwargs, ingest_cb,
    history_by_item, notes,
):
    """Phase 3A v1.3 (DECISIONS #57 stage-3): single-batch + Maestro pre-run script.

    Replaces stage-2's per-corner-submit pattern with one axlRunAllTests
    submission whose per-corner IC is delivered by an auto-generated
    SKILL pre-run script attached to each test in the item. Result:
    ONE Maestro history (consolidated GUI), per-corner IC injected
    just-in-time by the worker VM before each (test, corner) point is
    netlisted.

    Sequence:
      1. Resolve every sub-corner's IC path from the upstream's results
      2. Generate + write self-contained SKILL pre-run script with
         embedded (cornerName → +nodeset/+ic <path>) lookup table
      3. Snapshot each test's pre-existing pre-run script + the parent
         session's additionalArgs (so we can restore)
      4. Install pre-run script on each test (axlImportPreRunScript +
         Enabled=true)
      5. ONE axlRunAllTests submit (via pvt_runner_run = Submit + poll
         + Rename)
      6. PvtSave + ingest the single resulting history
      7. Cleanup: disable our pre-run on each test + reattach user's
         original if any + restore parent additionalArgs

    Falls back to the batch path (no IC) when:
      * upstream item has no recorded history (it crashed pre-PvtSave)
      * .pvtproject layout doesn't yield a results_root
      * the consumer's union didn't load (corner_count unknown)

    Per-corner missing IC = naked for THAT corner only (script's
    lookup just misses; other corners still get their IC).
    """
    from simkit.pre_run_script import (
        PreRunSpec, build_corner_arg_map, write_pre_run_script,
    )
    from simkit.union import explode

    ic_from: IcFromRef = item.ic_from
    sanitized = _sanitize_history(item.name)

    source_name = ic_from.item
    source_history = history_by_item.get(source_name)
    if source_history is None:
        notes.append(
            f"ic_from: source item {source_name!r} has no recorded history "
            f"(did it complete?). Running consumer in BATCH mode without IC."
        )
        print(f"           ! ic_from: no upstream history; falling back to batch")
        return _execute_batch_item(
            item, idx, total, bridge, session, pvtproject_path,
            history_prefix, timestamp, run_kwargs, ingest_cb, notes,
        )

    try:
        results_root = _resolve_results_root(pvtproject_path)
    except OrchestratorError as exc:
        notes.append(f"ic_from: {exc}")
        print(f"           ! ic_from: {exc}")
        return _execute_batch_item(
            item, idx, total, bridge, session, pvtproject_path,
            history_prefix, timestamp, run_kwargs, ingest_cb, notes,
        )

    if planned_union is None:
        notes.append(
            "ic_from: consumer union didn't load — cannot enumerate "
            "sub-corner names. Falling back to batch."
        )
        return _execute_batch_item(
            item, idx, total, bridge, session, pvtproject_path,
            history_prefix, timestamp, run_kwargs, ingest_cb, notes,
        )

    # Use the consumer item's first test name as the IC-resolution test
    # name (matches stage-2 behaviour). Multi-test items get the same
    # pre-run script attached to each test (corner-name-based lookup is
    # the same), but per-test IC mapping is a v1.4 follow-up.
    test_for_ic = item.tests[0]

    # 1. Enumerate sub-corner names + resolve each one's IC path
    sub_corners = list(explode(planned_union))
    sub_corner_names = [sc.sub_corner_name for sc in sub_corners]

    corner_to_ic_path: dict[str, str | None] = {}
    found = 0
    for sub_idx, sub_name in enumerate(sub_corner_names, start=1):
        resolved = _resolve_one_corner_ic(
            ic_from, source_history, sub_idx, test_for_ic, results_root,
        )
        if resolved is not None:
            corner_to_ic_path[sub_name] = str(resolved.abs_path)
            found += 1
        else:
            corner_to_ic_path[sub_name] = None

    arg_map = build_corner_arg_map(
        sub_corner_names, corner_to_ic_path, ic_from.mode,
    )

    print(f"[orch {idx}/{total}] {item.name!r}: ic_from chain (single-batch + pre-run, "
          f"{found}/{len(sub_corner_names)} corners mapped from {source_name!r})")

    if not arg_map:
        notes.append(
            f"ic_from: no corner IC files resolved under "
            f"{results_root}/{source_history}/. Running batch naked."
        )
        return _execute_batch_item(
            item, idx, total, bridge, session, pvtproject_path,
            history_prefix, timestamp, run_kwargs, ingest_cb, notes,
        )

    # 2. Generate + write pre-run script
    workdir = Path(pvtproject_path).expanduser().resolve().parent
    spec = PreRunSpec(
        item_name=item.name, mode=ic_from.mode, corner_to_arg=arg_map,
    )
    script_path = write_pre_run_script(spec, workdir)
    print(f"           pre-run script: {script_path}")

    # 3. Snapshot each test's prior pre-run script (so we can restore)
    prior_scripts: dict[str, str] = {}
    try:
        for tname in item.tests:
            prior_scripts[tname] = bridge.pvt_runner_get_pre_run_script(
                tname, session=session,
            )
    except Exception as exc:
        notes.append(f"ic_from: pre-run snapshot failed: {exc}")
        prior_scripts = {tname: "" for tname in item.tests}

    # 3b. Snapshot each test's prior additionalArgs (Phase 3A v1.9 #3, gap #1
    # closeout from DECISIONS #68). Use the v1.9 #2 primitive
    # ``pvt_runner_get_sim_option_val`` to capture each test's current
    # ``additionalArgs`` value so the finally block can restore it
    # byte-identically instead of unconditionally clearing to "".
    #
    # Sentinel: ``_NO_SNAPSHOT`` means the bridge wrapper isn't available
    # (mock bridges from pre-v1.9 #2 tests, or a SKILL surface that doesn't
    # expose the getter). Falls back to the old clear-to-"" behaviour for
    # those tests so partial bridge upgrades don't break existing pipelines.
    _NO_SNAPSHOT = object()
    prior_additional_args: dict[str, object] = {}
    probe = getattr(bridge, "pvt_runner_get_sim_option_val", None)
    if probe is None:
        for tname in item.tests:
            prior_additional_args[tname] = _NO_SNAPSHOT
        notes.append(
            "ic_from: bridge lacks pvt_runner_get_sim_option_val — "
            "using v1.3 clear-to-empty additionalArgs cleanup"
        )
    else:
        for tname in item.tests:
            try:
                prior_additional_args[tname] = probe(
                    tname, "additionalArgs", session=session,
                )
            except Exception as exc:
                prior_additional_args[tname] = _NO_SNAPSHOT
                notes.append(
                    f"ic_from: additionalArgs snapshot on {tname!r} "
                    f"failed: {exc} (clear-to-empty fallback)"
                )

    # 4. Install our pre-run script on every test in the item
    install_failures: list[str] = []
    for tname in item.tests:
        try:
            bridge.pvt_runner_install_pre_run_script(
                tname, str(script_path), session=session,
            )
        except Exception as exc:
            install_failures.append(f"{tname}: {exc}")
            notes.append(f"ic_from: install pre-run on {tname!r}: {exc}")

    if install_failures and len(install_failures) == len(item.tests):
        # All installs failed — bail out cleanly
        print(f"           ! all pre-run installs failed; batch fallback")
        return _execute_batch_item(
            item, idx, total, bridge, session, pvtproject_path,
            history_prefix, timestamp, run_kwargs, ingest_cb, notes,
        )

    # 5. Single batch submit
    history = f"{history_prefix}_{sanitized}_{timestamp}_{idx}"
    actual_history = history
    completed = False
    histories: list[str] = []
    run_dirs: list[Path] = []
    # Baseline-corner state (Phase 3A v1.4, DECISIONS #59). Initialised
    # here so the finally block can see them whether or not setup runs.
    corners_snap: list[tuple[str, bool]] = []
    baseline_applied = False
    try:
        # 4b. Baseline-corner preservation (Phase 3A v1.4, DECISIONS #59).
        # Snapshot current per-corner enable state, then enable one scalar
        # corner as baseline so Maestro doesn't auto-insert a `nom` subdir.
        # Restored in this try-finally's cleanup. Lives INSIDE the outer
        # try so OrchestratorError from the picker still triggers the
        # pre-run cleanup in finally.
        try:
            corners_snap = bridge.pvt_runner_snapshot_corners_enable(
                session=session,
            )
            live_union = _load_live_corner_table(
                bridge, session=session, pvtproject_path=pvtproject_path,
            )
            baseline_name = _pick_baseline_corner(
                live_union, item.baseline_corner,
            )
            if baseline_name not in {n for n, _ in corners_snap}:
                # picked from live_union but missing from enable-snapshot —
                # snapshot includes every corner the SKILL layer knows about,
                # so this can only happen on a transient name-mismatch.
                raise OrchestratorError(
                    f"baseline_corner={baseline_name!r} present in pulled "
                    f"union but absent from enable-state snapshot; corner "
                    f"table may have changed mid-flight"
                )
            target_snap = [
                (name, True if name == baseline_name else en)
                for name, en in corners_snap
            ]
            bridge.pvt_runner_restore_corners_enable(
                target_snap, session=session,
            )
            baseline_applied = True
            source = ("override" if item.baseline_corner else "auto")
            print(f"           baseline corner: {baseline_name!r} ({source})")
        except OrchestratorError:
            # Hard fail per DECISIONS #59 — let finally clean pre-run,
            # then propagate so the user sees the error and addresses it
            # rather than silently getting a `nom` baseline subdir.
            raise
        except Exception as exc:
            notes.append(f"baseline-corner setup failed: {exc}")
            print(f"           ! baseline-corner setup: {exc}")
            # Soft fail: continue with the run. Maestro will insert `nom`
            # as before; the cosmetic `/1/` subdir reappears but v1.3
            # functionality (per-corner readic/readns) is preserved.

        try:
            rv = bridge.pvt_runner_run(
                history, session=session, **(run_kwargs or {}),
            )
            if isinstance(rv, tuple) and len(rv) >= 3:
                actual_history = rv[2]
                if actual_history != history:
                    notes.append(f"Maestro renamed history -> {actual_history}")
            histories.append(actual_history)
            completed = True
        except Exception as exc:
            notes.append(f"axlRunAllTests error: {exc}")
            print(f"           ! run errored: {exc}")

        # 6. PvtSave + ingest the single batch history
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
            else:
                try:
                    if ingest_cb is None:
                        _default_ingest(Path(run_dirs[0]), pvtproject_path)
                    else:
                        ingest_cb(str(run_dirs[0]))
                    print(f"           ingested")
                except Exception as exc:
                    notes.append(f"ingest error: {exc}")
                    print(f"           ! ingest errored: {exc}")
    finally:
        # 7a. Restore per-corner enable state (Phase 3A v1.4, DECISIONS #59).
        # Mirror the pre-run script restore: undo our baseline mutation
        # so the user's corner-table state is byte-identical to entry.
        if baseline_applied and corners_snap:
            try:
                bridge.pvt_runner_restore_corners_enable(
                    corners_snap, session=session,
                )
            except Exception as exc:
                notes.append(f"baseline-corner restore failed: {exc}")
                print(f"           ! baseline restore: {exc}")

        # 7. Cleanup: disable our pre-run, reattach user's original if any.
        # If we wrote junk to additionalArgs via the pre-run script, that
        # value lives in the asi session — restore by reattaching the
        # user's prior script (which presumably knows its desired state)
        # OR by clearing if no prior script was set.
        for tname in item.tests:
            try:
                bridge.pvt_runner_disable_pre_run_script(tname, session=session)
            except Exception as exc:
                notes.append(f"pre-run disable {tname!r}: {exc}")
            prior = prior_scripts.get(tname, "")
            if prior and prior != str(script_path):
                try:
                    bridge.pvt_runner_install_pre_run_script(
                        tname, prior, session=session,
                    )
                except Exception as exc:
                    notes.append(
                        f"reattach user's pre-run {prior!r} on {tname!r}: {exc}"
                    )
        # additionalArgs cleanup (Phase 3A v1.9 #3, gap #1 closeout from
        # DECISIONS #68): restore each test's PRIOR additionalArgs from the
        # snapshot taken in step 3b instead of unconditionally clearing to
        # "". Pre-v1.9 behaviour (clear-to-"") is preserved for tests whose
        # snapshot returned the ``_NO_SNAPSHOT`` sentinel (probe wrapper
        # missing on bridge, or per-test snapshot raised).
        for tname in item.tests:
            prev = prior_additional_args.get(tname, _NO_SNAPSHOT)
            if prev is _NO_SNAPSHOT:
                restore_value = ""
            elif prev is None:
                # Option was unset before our pre-run write. Clear to "" so
                # the asi returns to "no value set" terminal state (the
                # SKILL helper normalises empty + nil to "unset").
                restore_value = ""
            else:
                restore_value = str(prev)
            try:
                bridge.pvt_runner_clear_ic_source(
                    tname, ic_from.mode, restore_value, session=session,
                )
            except Exception as exc:
                notes.append(
                    f"additionalArgs restore on {tname!r}: {exc}"
                )

    return (histories, run_dirs, completed)


# ---------------------------------------------------------------------------
# v1.6: per-corner FAIL detection + strategy chain dispatch (DECISIONS #62)


def _run_strategy_chain(
    item: ReviewItem,
    *,
    bridge,
    session: str,
    pvtproject_path: Path,
    primary_run_dir: Path,
    histories: list[str],
    run_dirs: list[Path],
    notes: list[str],
    ingest_cb,
    query_failed_cb,
    history_by_item: dict[str, str] | None = None,
    progress_cb=None,
    item_index: int = 0,
) -> tuple[tuple[StrategyAttempt, ...], tuple[str, ...]]:
    """Drive ``item.on_failure.strategies`` until FAIL set drains or chain
    exhausts. Mutates ``histories`` / ``run_dirs`` in place with each retry's
    artefacts so the caller's ItemResult sees the full audit trail.

    Returns ``(attempts, final_failed_corners)``. ``final_failed_corners``
    is the corner names still FAIL after the last attempt — empty iff fully
    recovered. If the initial query surfaced only eval_err corners (which
    auto_retry_corners filters out), the chain is skipped and the eval_err
    corner names show up in final_failed_corners untouched.
    """
    attempts: list[StrategyAttempt] = []

    def _record(att: StrategyAttempt) -> None:
        attempts.append(att)
        if progress_cb is not None:
            try:
                progress_cb({
                    "event": "strategy_attempt",
                    "item_index": item_index,
                    "strategy_name": att.strategy_name,
                    "attempt_number": att.attempt_number,
                    "outcome": att.outcome,
                    "targeted": list(att.corners_targeted),
                    "remaining": list(att.corners_remaining),
                })
            except Exception as exc:  # noqa: BLE001 — progress is best-effort
                notes.append(f"progress_cb(strategy_attempt) raised: {exc}")

    try:
        first_run_id = _load_run_id(primary_run_dir)
    except Exception as exc:
        notes.append(f"strategy chain: cannot read run_id from "
                     f"{primary_run_dir}: {exc}")
        return tuple(attempts), ()

    initial_failed = query_failed_cb(pvtproject_path, first_run_id)
    auto_targets = set(auto_retry_corners(initial_failed))

    # All FAIL corner names (including eval_err) — what we report at end.
    all_failed_names = {f.corner for f in initial_failed}

    if not auto_targets:
        # Nothing the chain can act on (clean run, or eval_err-only). Report
        # any eval_err corners; otherwise empty.
        return tuple(attempts), tuple(sorted(all_failed_names))

    remaining = set(auto_targets)

    for strat_entry in item.on_failure.strategies:
        if not remaining:
            break
        try:
            StratCls = get_builtin(strat_entry.name)
        except KeyError:
            notes.append(
                f"unknown strategy {strat_entry.name!r}; skipping"
            )
            continue
        strat = StratCls(
            max_attempts=strat_entry.max_attempts,
            params=dict(strat_entry.params),
        )

        for attempt in range(1, strat.max_attempts + 1):
            if not remaining:
                break

            ctx_failed = tuple(
                (f.corner, f.sample_test, "fail")
                for f in initial_failed
                if f.corner in remaining
            )
            ctx = StrategyContext(
                session=session,
                item_name=item.name,
                failed_corners=ctx_failed,
                attempt_number=attempt,
                bridge=bridge,
                params=strat.params,
                history_by_item=history_by_item,
                pvtproject_path=pvtproject_path,
            )

            print(f"           strategy {strat.name!r} attempt {attempt} "
                  f"targets {sorted(remaining)}")

            try:
                res = strat.apply(ctx)
            except Exception as exc:
                notes.append(
                    f"strategy {strat.name!r} attempt {attempt} raised: {exc}"
                )
                _record(StrategyAttempt(
                    strategy_name=strat.name, attempt_number=attempt,
                    outcome="gave_up", history_name=None, run_dir=None,
                    corners_targeted=tuple(sorted(remaining)),
                    corners_remaining=tuple(sorted(remaining)),
                    notes=f"exception: {exc}",
                ))
                break  # abandon this strategy; move to next in chain

            if (res.outcome == StrategyOutcome.GAVE_UP
                    or res.new_history_name is None):
                _record(StrategyAttempt(
                    strategy_name=strat.name, attempt_number=attempt,
                    outcome="gave_up",
                    history_name=res.new_history_name,
                    run_dir=None,
                    corners_targeted=tuple(sorted(remaining)),
                    corners_remaining=tuple(sorted(remaining)),
                    notes=res.notes,
                ))
                break  # next strategy in chain

            # Strategy did run a sim — PvtSave + ingest + re-query.
            try:
                retry_dir = bridge.pvt_save(
                    res.new_history_name,
                    pvtproject_path=pvtproject_path,
                    session=session,
                )
            except Exception as exc:
                notes.append(
                    f"PvtSave({res.new_history_name}) error: {exc}"
                )
                _record(StrategyAttempt(
                    strategy_name=strat.name, attempt_number=attempt,
                    outcome="gave_up",
                    history_name=res.new_history_name, run_dir=None,
                    corners_targeted=tuple(sorted(remaining)),
                    corners_remaining=tuple(sorted(remaining)),
                    notes=f"PvtSave failed: {exc}",
                ))
                break
            retry_dir_p = Path(retry_dir)
            histories.append(res.new_history_name)
            run_dirs.append(retry_dir_p)

            try:
                if ingest_cb is None:
                    _default_ingest(retry_dir_p, pvtproject_path)
                else:
                    ingest_cb(str(retry_dir_p))
            except Exception as exc:
                notes.append(
                    f"ingest({res.new_history_name}) error: {exc}"
                )
                _record(StrategyAttempt(
                    strategy_name=strat.name, attempt_number=attempt,
                    outcome="gave_up",
                    history_name=res.new_history_name,
                    run_dir=retry_dir_p,
                    corners_targeted=tuple(sorted(remaining)),
                    corners_remaining=tuple(sorted(remaining)),
                    notes=f"ingest failed: {exc}",
                ))
                break

            try:
                retry_run_id = _load_run_id(retry_dir_p)
                retry_failed = query_failed_cb(pvtproject_path, retry_run_id)
            except Exception as exc:
                notes.append(
                    f"re-query FAIL after {res.new_history_name}: {exc}"
                )
                # We can't tell what shrunk; mark as gave_up and surface
                # the current remaining set as-is.
                _record(StrategyAttempt(
                    strategy_name=strat.name, attempt_number=attempt,
                    outcome="gave_up",
                    history_name=res.new_history_name,
                    run_dir=retry_dir_p,
                    corners_targeted=tuple(sorted(remaining)),
                    corners_remaining=tuple(sorted(remaining)),
                    notes=f"re-query failed: {exc}",
                ))
                break

            still_failing = set(auto_retry_corners(retry_failed)) & remaining
            recovered = remaining - still_failing
            outcome = "recovered" if recovered else "unchanged"
            _record(StrategyAttempt(
                strategy_name=strat.name, attempt_number=attempt,
                outcome=outcome,
                history_name=res.new_history_name,
                run_dir=retry_dir_p,
                corners_targeted=tuple(sorted(remaining)),
                corners_remaining=tuple(sorted(still_failing)),
                notes=res.notes,
            ))
            print(f"           → {outcome}; remaining {sorted(still_failing)}")
            remaining = still_failing

    # Final report: corners still failing (auto-retry set) + any eval_err
    # that was excluded from retry from the very start.
    eval_only = all_failed_names - auto_targets
    final = sorted(remaining | eval_only)
    return tuple(attempts), tuple(final)


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
    query_failed_cb=None,
    progress_cb=None,
    cancel_check=None,
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
        query_failed_cb: Optional callable(pvtproject_path, run_id) ->
                tuple[FailedCorner, ...] used by the v1.6 strategy chain
                to look up FAIL corners after each ingest. Default opens
                the project DB read-only. Tests inject a stub when
                ingest_cb is mocked too (no real DB available).
        progress_cb: Optional callable(event_dict) — invoked at item
                start, item complete, and per strategy_attempt. Used by
                ``pvt run --gui-jsonl`` to stream JSONL progress to a
                QProcess parent (Phase 4 §9). Event dicts follow spec
                §9.2 shape; exceptions raised by the callback are
                swallowed + logged to notes (best-effort, never blocks
                the run).
        cancel_check: Optional callable() -> bool — polled BEFORE each
                item dispatch. When True, the orchestrator breaks the
                loop cleanly (no in-flight Maestro poll is interrupted)
                and lets the finally block restore the test snapshot.
                Used by the CLI's SIGTERM handler so the GUI's Cancel
                button cleanly stops the run at the next item boundary.
    """
    import time

    if pvtproject_path is None:
        raise OrchestratorError(
            "execute() requires pvtproject_path (needed by PvtSave even when "
            "push_union=False)"
        )

    if query_failed_cb is None:
        query_failed_cb = _default_query_failed

    timestamp = int(time.time())
    snap = bridge.pvt_runner_snapshot_test_state(session=session)
    snapshot_restored = False
    item_results: list[ItemResult] = []

    # Track each item's actual_history by name so a downstream consumer
    # with ic_from can find the upstream history dir on disk.
    history_by_item: dict[str, str] = {}

    def _emit(event: dict) -> None:
        if progress_cb is None:
            return
        try:
            progress_cb(event)
        except Exception as exc:  # noqa: BLE001 — progress is best-effort
            print(f"[orch] WARNING: progress_cb raised on "
                  f"{event.get('event')!r}: {exc}")

    try:
        for idx, planned in enumerate(plan.planned, start=1):
            if cancel_check is not None:
                try:
                    if cancel_check():
                        print(f"[orch] cancel requested; stopping before "
                              f"item {idx}/{len(plan.planned)}")
                        break
                except Exception as exc:  # noqa: BLE001
                    print(f"[orch] WARNING: cancel_check raised: {exc}")

            item = planned.item
            notes: list[str] = []

            _emit({
                "event": "item_started",
                "item_index": idx,
                "item_name": item.name,
                "total_items": len(plan.planned),
            })

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

            # 4. Branch on ic_from: batch path vs. per-corner path
            if item.ic_from is None:
                histories, run_dirs, completed = _execute_batch_item(
                    item, idx, len(plan.planned),
                    bridge, session, pvtproject_path,
                    history_prefix, timestamp, run_kwargs, ingest_cb,
                    notes,
                )
            else:
                # ic_from chain (Phase 3A v1.3 — DECISIONS #57 stage-3).
                # Single axlRunAllTests submit; per-corner IC is delivered
                # by an auto-generated SKILL pre-run script attached to
                # each test in the item. Maestro fires the script in the
                # worker virtuoso VM right before each (test, corner)
                # point is netlisted; the script looks up the embedded
                # corner→arg table and writes +nodeset/+ic into the
                # test's additionalArgs sim option. Result: ONE history
                # entry, consolidated GUI, per-corner IC delivered.
                histories, run_dirs, completed = _execute_ic_chained_item(
                    item, idx, len(plan.planned), planned.union,
                    bridge, session, pvtproject_path,
                    history_prefix, timestamp, run_kwargs, ingest_cb,
                    history_by_item, notes,
                )

            # Capture history for any downstream consumer's ic_from lookup.
            # For per-corner items, this is the FIRST history (each corner
            # has its own); resolve_ic_path will compute the right per-corner
            # subdir from any of them since they all share <results>/<hist>/.
            # For batch items, this is the single history covering all corners.
            if completed and histories:
                history_by_item[item.name] = histories[0]

            # v1.6: per-corner FAIL detection + strategy chain.
            strategy_attempts: tuple[StrategyAttempt, ...] = ()
            final_failed: tuple[str, ...] = ()
            if completed and run_dirs:
                if item.on_failure.strategies:
                    try:
                        strategy_attempts, final_failed = _run_strategy_chain(
                            item,
                            bridge=bridge,
                            session=session,
                            pvtproject_path=pvtproject_path,
                            primary_run_dir=run_dirs[0],
                            histories=histories,
                            run_dirs=run_dirs,
                            notes=notes,
                            ingest_cb=ingest_cb,
                            query_failed_cb=query_failed_cb,
                            history_by_item=history_by_item,
                            progress_cb=progress_cb,
                            item_index=idx,
                        )
                    except Exception as exc:
                        notes.append(f"strategy chain error: {exc}")
                else:
                    # No strategies declared — still surface FAIL corners
                    # so the report tells the user X corners went unhandled.
                    try:
                        rid = _load_run_id(run_dirs[0])
                        fails = query_failed_cb(pvtproject_path, rid)
                        final_failed = tuple(sorted({f.corner for f in fails}))
                    except Exception as exc:
                        notes.append(f"FAIL query error: {exc}")

            result = ItemResult(
                item_name=item.name,
                history_names=tuple(histories),
                run_dirs=tuple(run_dirs),
                completed=completed,
                notes=" | ".join(notes),
                strategy_attempts=strategy_attempts,
                final_failed_corners=final_failed,
            )
            item_results.append(result)
            if item_done_cb:
                item_done_cb(result)

            # Progress event — item_completed. run_id pulled from the
            # primary run.json if we got that far; "" otherwise.
            completed_run_id = ""
            if run_dirs:
                try:
                    completed_run_id = _load_run_id(run_dirs[0])
                except Exception:
                    completed_run_id = ""
            _emit({
                "event": "item_completed",
                "item_index": idx,
                "run_id": completed_run_id,
                "completed": 1 if completed else 0,
                "failed": len(final_failed),
                "history_name": histories[0] if histories else None,
            })

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
