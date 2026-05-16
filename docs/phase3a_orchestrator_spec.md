# Phase 3A §1 — Simulation Orchestrator Spec

**Schema version: 1** (Phase 3A v1). Frozen surface for Phase 3A. Any breaking change requires bumping `review_schema_version` and appending a migration note to `DECISIONS.md`.

Phase 3A builds the **third (execution) pillar** of simkit: a Python orchestrator that drives Maestro through a user-authored "review suite" sidecar, ingests results, and applies retry strategies on convergence failures.

This spec is informed by (a) the user's stated workflow — a typical signoff "review" is a list of 5-10 named items, each pairing **its own set of tests** with **its own corner set** and **its own measure bundle**; (b) the 2026-05-16 live skillbridge probe that mapped `axl*` run-control and discovered the `asi*` simulator-interface namespace (298 fns) where Spectre options + analysis fields live.

---

## 1. Problem statement (one paragraph)

A signoff-grade review of a mixed-signal block (e.g. BT2GRX) is a list of 5-15 items like "BT2GRX trans PVT", "BT2GRX PSS PN", "LE mode trans PVT", "LE PSS PN", "干扰仿真", … Each item names its own tests (one or several from the Maestro session), its own PVT-union corner set (different analyses want different corners — trans wants full PVT, PSS often wants typical/slow/fast only), and its own measure bundle (the formulas that define pass/fail). Today the engineer clicks the right test boxes + enables the right corner rows + applies the right output formulas in Maestro UI, one item at a time, and watches each run finish. Phase 3A turns this into a one-command operation against a `*.review.json` sidecar.

---

## 2. Sidecar file format

### 2.1 Location and naming

| Item | Value | Notes |
|---|---|---|
| Format | strict JSON | Same rationale as Phase 1/2/3B sidecars (DECISIONS #13). |
| Extension | `.review.json` | Two-part extension, `find -name '*.review.json'` works. |
| Project-level directory | `<reviewsDir>/` | New optional `.pvtproject` field, default `./reviews` relative to the `.pvtproject`. Additive to project schema (no `schema_version` bump). |
| Conflict policy | Filename basename (sans `.review.json`) must equal the file's `name` field. | Catches "renamed file but stale internal name". |

### 2.2 Top-level structure

```json
{
  "_doc": "...",
  "review_schema_version": 1,
  "name": "bt2grx_signoff",
  "project": "bt2grx",
  "items": [
    { ... item 1 ... },
    { ... item 2 ... }
  ],
  "on_failure": { ... suite-level defaults ... }
}
```

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `review_schema_version` | int | yes | — | `1` = v1 base shape. `2` = v1.2 adds `ic_from` on items (§2.5). v1 sidecars still load unchanged. |
| `name` | str | yes | — | Must match `^[a-z0-9_-]+$` and equal filename basename. |
| `project` | str | yes | — | Must match enclosing `.pvtproject:project`. |
| `items` | array | yes | — | One or more items. Empty = load error. |
| `on_failure` | object | no | `{"default": "skip"}` | Suite-level failure defaults; items can override. See §4. |

### 2.3 Item shape

```json
{
  "name": "BT2GRX trans PVT",
  "tests": ["sim_BT2GRX", "sim_BT2GTX"],
  "union": "unions/bt2grx_trans.union.json",
  "bundle": "bundles/bt2grx_trans.measure.json",
  "enabled": true,
  "on_failure": { ... item-level override ... }
}
```

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `name` | str | yes | — | Human-readable label for log + report. Must match `^[A-Za-z0-9_\-\s]+$`. Unique within a suite. |
| `tests` | array of str | yes | — | One or more test names. Each must resolve to a test in the live session. Other session-level tests are temporarily disabled for this item. |
| `union` | str | yes | — | Path (relative to review file) to a `.union.json`. Phase 2 union sidecar applies as-is — per-test corner enable expressed by the union's `rows[*].test` field. |
| `bundle` | str | no | null | Path to a `.measure.json`. If null, Outputs table is not touched (uses whatever's already in the session). |
| `enabled` | bool | no | `true` | When `false`, item is logged-skipped without running. Cheap way to park an item without deleting it. |
| `on_failure` | object | no | inherits suite-level | Per-item override; merges on top of suite-level (item keys win). |
| `ic_from` | object | no | null | **Schema v2.** When set, before each corner of this item runs, the orchestrator points the test's analysis at the named upstream item's per-corner Spectre IC file (`spectre.fc` / `.ic` / `.dc`). See §2.5. |

### 2.4 What a "review" is in this spec

A **review** is a named, ordered list of items that together describe one full signoff cycle for one project. Multiple reviews can coexist (e.g. `bt2grx_signoff.review.json`, `bt2grx_smoke.review.json`, `bt2grx_tapeout.review.json`). A run invokes exactly one review.

### 2.5 Cross-item IC piping (`ic_from`, schema v2)

Workflow: PSS / HB convergence is hard from cold start. Engineer wants to run a trans precursor across the same PVT corner set, take each corner's Spectre `spectre.fc` (final condition) — or `.ic` / `.dc` — and feed it into the PSS / HB analysis as `readns` (nodeset hint, soft) or `readic` (initial condition, hard) **per corner**.

`ic_from` is an object on the consumer item:

```json
{
  "name": "BT2GRX PSS PN",
  "tests": ["sim_BT2GRX_pss"],
  "union": "unions/bt2grx_full.union.json",
  "bundle": "bundles/bt2grx_pss_pn.measure.json",
  "ic_from": {
    "item": "BT2GRX trans precursor",
    "file": "fc",
    "mode": "readns"
  }
}
```

| Field | Type | Required | Allowed | Notes |
|---|---|---|---|---|
| `item` | str | yes | name of an **earlier** item in the same review | Source item must precede the consumer in `items[]` order. |
| `file` | str | yes | `"fc"`, `"ic"`, `"dc"` | Maps to `spectre.fc` / `spectre.ic` / `spectre.dc` in the trans result dir. |
| `mode` | str | yes | `"readns"`, `"readic"` | `readns` = nodeset hint (Spectre soft guess, typical for PSS). `readic` = hard IC (constrains values, typical for trans→trans handoff). |

**Corner mapping rule (v2):** consumer and source items MUST reference the **same `.union.json` path** (resolved-equal). Per-corner pairing is positional under that union's explode order. This guarantees zero-ambiguity matching with no name-string heuristics. Loader rejects mismatch at validate time.

**Orchestrator behaviour (v1.3):** ic_from triggers a **single-batch + Maestro pre-run script** path. The consumer item runs ONCE via `axlRunAllTests`; per-corner IC is delivered just-in-time by a SKILL pre-run script attached to each test via `axlImportPreRunScript`. The script fires in Maestro's worker virtuoso VM right before each (test, sub-corner) point is netlisted, reads the current sub-corner via `axlGetCornerNameForCurrentPointInRun`, looks up the matching `+nodeset` / `+ic <path>` in an embedded SKILL `assoc` table, and writes it into the test's `additionalArgs` simulator option via `asiSetSimOptionVal`. Result: ONE Maestro history with all N sub-corners consolidated in the GUI / ViVA / results table, and each sub-corner saw its own IC at Spectre invocation time. The orchestrator snapshots the user's prior pre-run script (if any) and re-attaches it on cleanup.

**Source item failure handling:** if a corner failed in the source item (no `.fc` produced for that corner_idx), the orchestrator runs the corresponding consumer corner **without** the IC (naked retry) and logs a warning. PSS / HB without IC usually still finishes but may fail to converge — the user sees a normal per-corner FAIL row, not an orchestrator error. If the entire source item has no recorded history (it crashed pre-PvtSave), the consumer falls back to **batch mode without IC** (one axlRunAllTests for all corners).

**Simulator subdir auto-detection:** Spectre stores IC files under `<corner_dir>/<test>/netlist/spectre.{fc,ic,dc}`. Alps (国产 simulator) stores them under `<corner_dir>/<test>/psf/spectre.{fc,ic,dc}` (TBC at first work-env dogfood). The resolver tries known subdirs in order, picks the first that has the file; user can pin via `ic_from.subdir` override if a new simulator surfaces.

**Per-corner result dir layout** (empirically derived from `simkit_verify`, 2026-05-16):

```
<axlGetResultsLocation(sdb)>/<history_name>/<corner_idx_1based>/<test_name>/<sim_subdir>/spectre.{fc,ic,dc}
```

`corner_idx_1based` matches the order corners appear in `axlGetCorners(sdb)` at the time the source item ran. The orchestrator captures this ordering when it pushes the source item's union, so the consumer item's per-corner lookup is deterministic.

---

## 3. Orchestrator loop (pseudocode)

```python
review = load_review(path)
for item in review.items:
    if not item.enabled:
        log.skip(item, "enabled=false")
        continue
    with _save_restore_session_state():
        _enable_only(item.tests)               # disable all others
        push_union(item.union)                  # Phase 2 pvtCornersPush
        if item.bundle:
            push_bundle(item.bundle)            # Phase 3B pvtMeasurePush
        history_name = f"review_{review.name}_{item.name}_{ts}"
        if item.ic_from:                        # schema v2 (§2.5)
            # v1.3: single-batch + Maestro pre-run script
            corner_to_arg = {}
            for sub_corner in explode(item.union):
                ic_path = resolve_ic_path(item.ic_from, sub_corner.idx)
                if ic_path:
                    flag = "+nodeset" if mode == "readns" else "+ic"
                    corner_to_arg[sub_corner.name] = f"{flag} {ic_path}"
            script_path = write_pre_run_script(item.name, corner_to_arg)
            prior = {t: bridge.get_pre_run_script(t) for t in item.tests}
            try:
                for t in item.tests:
                    bridge.install_pre_run_script(t, script_path)
                run_batch_submit(history_name)      # ONE axlRunAllTests
                pvt_save(history_name)
                ingest(history_name)
            finally:
                for t in item.tests:
                    bridge.disable_pre_run_script(t)
                    if prior[t]:
                        bridge.install_pre_run_script(t, prior[t])
                bridge.clear_ic_source(item.tests[0], mode, "")
        else:
            await _run_with_strategies(history_name, item.on_failure)
        per_corner_status = collect_results()   # Phase 1 PvtSave path
        ingest(per_corner_status)               # Phase 1 pvt ingest
        log.summary(item, per_corner_status)
report = build_report(review)                   # printed table + DB pointer
```

Items execute **sequentially in list order**. Concurrency within one item (parallel corners) is delegated to Maestro/LSF — orchestrator submits one `axlRunAllTestsWithCallback` per item and waits for the completion callback. No orchestrator-side process pool.

`_save_restore_session_state()` captures the live session's pre-item state (which tests are enabled, current union, current bundle) and restores it on `__exit__` regardless of pass/fail/exception — same hygiene Phase 3B's `pvt measure restore` follows (DECISIONS #42).

---

## 4. Failure semantics

| Layer | Default behaviour | Override |
|---|---|---|
| **Per-corner sim failure** (non-convergence, sim_err, eval_err) | Mark that corner FAIL in DB; continue with remaining corners in the same item. | Strategy chain (§5) can re-run failed corners with intervention before marking FAIL. |
| **Per-item failure** (e.g. union push errored, bundle render errored, all corners failed) | Mark item FAIL; continue with next item. | `on_failure: {item_policy: "halt"}` stops the whole review. |
| **Suite-level** | Always produce a per-item summary + per-(item, corner) status table at the end. Return non-zero exit code if any corner failed (CI-friendly). | — |

`on_failure` object schema:

```json
{
  "default": "skip",                  // "skip" | "halt"; default for both corner and item levels
  "corner_policy": "skip",            // overrides "default" for per-corner
  "item_policy": "skip",              // overrides "default" for per-item
  "strategies": [                     // ordered chain; empty = no retries
    {"name": "naive_retry", "max_attempts": 1}
  ]
}
```

Per-item `on_failure` deep-merges over suite-level (object keys merged; arrays replaced wholesale).

---

## 5. Strategy plugin contract

A **strategy** is a Python class that, given a failing (corner, test, sim_engine_state), attempts one or more interventions and re-runs the corner. v1 ships the contract + 1 placeholder strategy (`naive_retry`); known production strategies (`gmin_bump`, `trans_pss_ic`) are deferred to v1.1, where the `asi*` API surface for Spectre options + analysis IC fields gets its own probe phase.

### 5.1 Class interface

```python
class Strategy:
    name: str
    max_attempts: int  # default 1
    params: dict       # user-passed knobs from the sidecar entry

    def apply(self, ctx: StrategyContext) -> StrategyResult:
        """Mutate ctx (sim options, analysis fields, etc.), return what was done.
        Idempotent within one attempt; orchestrator handles re-run."""

    def revert(self, ctx: StrategyContext) -> None:
        """Undo the mutation. Called after the retry attempt regardless of outcome."""
```

`StrategyContext` carries: `session`, `sdb`, `test_handle`, `corner_id`, `attempt_number`, plus a Bridge facade for the `asi*` / `axl*` writes the strategy needs.

### 5.2 Built-in strategies (v1)

| Name | Behaviour | Status |
|---|---|---|
| `naive_retry` | No intervention; just re-runs the failing corner up to `max_attempts` times. Covers transient license / disk / scheduler hiccups. | v1 ✅ |
| `gmin_bump` | Escalating Spectre `gmin` (1e-12 → 1e-11 → 1e-10). | v1.1 (asi* probe) |
| `trans_pss_ic` | Run an N-ns trans precursor, take `spectre.fc`, set as the PSS analysis IC. `N` and IC mapping parameterizable. | v1.1 (asi* probe) |

### 5.3 User-defined strategies

The orchestrator discovers user strategies from `<project>/strategies/*.py` (sibling to the `.pvtproject`). Each file exports one or more `Strategy` subclasses. User strategies can reference any of the `asi*` / `axl*` SKILL APIs through the same bridge facade the built-ins use. v1 ships docs (`docs/phase3a_writing_strategies.md`) with one worked example.

---

## 6. SKILL bridge surface

New file: `skill/pvtRunner.il`. Production-side helpers (all return `(pvt_ok value)` / `(pvt_err category msg)`):

| Verb | Args | Returns |
|---|---|---|
| `pvtRunnerEnableOnly` | session, list-of-test-names | per-test enable diff (before/after) |
| `pvtRunnerSnapshotTestState` | session | opaque snapshot used by restore |
| `pvtRunnerRestoreTestState` | session, snapshot | (echoes snapshot on success) |
| `pvtRunnerSubmit` | session, history-name | dispatches `axlRunAllTestsWithCallback`; returns completion token |
| `pvtRunnerWait` | session, token, timeout-sec | blocks until callback fires or timeout |
| `pvtRunnerGetStatus` | session | `[runStatusCode, subCode, latestHistoryName]` |
| `pvtRunnerCollectHistory` | session, history-name | JSON-shaped per-(corner, test) status table — feeds Phase 1 ingester directly |
| `pvtRunnerSetIcSource` | session, testName, icPath, mode | Writes `+nodeset <path>` (readns) or `+ic <path>` (readic) into the test's `additionalArgs` sim option via `asiSetSimOptionVal`. Returns prev value for restore. v1.2 stage-1. |
| `pvtRunnerClearIcSource` | session, testName, mode, prevValue | Restores `additionalArgs` to prevValue. v1.2 stage-1. |
| `pvtRunnerSnapshotCornersEnable` | session | List of `(name, bool)` per-corner enable state. v1.2 stage-2 (kept; not on ic_from critical path in v1.3). |
| `pvtRunnerEnableCornerByIndex` | session, idx | Disable all corners except the 1-based idx (in `axlGetCorners` order). v1.2 stage-2 (kept). |
| `pvtRunnerRestoreCornersEnable` | session, snap | Apply snapshot to restore corner enable mask. v1.2 stage-2 (kept). |
| `pvtRunnerInstallPreRunScript` | session, testName, scriptPath | Attach + enable a pre-run SKILL script (`axlImportPreRunScript` + `axlSetPreRunScriptEnabled t`). v1.3 — the ic_from injection hook. |
| `pvtRunnerDisablePreRunScript` | session, testName | Disable the pre-run script (`axlSetPreRunScriptEnabled nil`). v1.3. |
| `pvtRunnerGetPreRunScript` | session, testName | Returns the attached script path (`""` if none). For snapshot/restore of user's prior script. v1.3. |

`pvtRunnerCollectHistory` reuses `pvtCollIterateResults` from Phase 1 — same row classification (`ok` / `sim_err` / `eval_err` / `unknown`), same JSON envelope shape, so the orchestrator can pipe its output straight into `pvt ingest` with no schema work.

---

## 7. Python CLI surface

New file: `python/simkit/cli/run.py`.

| Command | Purpose |
|---|---|
| `pvt run <review.json>` | Primary path. Runs all items end-to-end. Exits non-zero if any corner failed. |
| `pvt run <review.json> --items <name1>,<name2>` | Run a subset of items by name. |
| `pvt run <review.json> --dry-run` | Print the plan (items × resolved corner counts × bundle pointers) without driving Maestro. |
| `pvt run --tests T1,T2 --union U.union.json [--bundle B.measure.json]` | Ad-hoc escape hatch — synthesises a one-item review in memory. |
| `pvt run --list-strategies` | Lists all discovered strategies (built-in + user) with their signatures. |
| `pvt review init <name>` | Scaffolds an empty `.review.json` with comments showing the expected fields. |
| `pvt review validate <review.json>` | Schema + cross-reference check (does each `tests` entry exist in the session? does each `union` / `bundle` path resolve?). |

Live-Maestro commands (`pvt run`) require `--session` (or `PVT_SESSION` env var) to identify which Maestro session to drive. `pvt review validate` is offline (no skillbridge).

---

## 8. Acceptance gates (§6)

| Gate | What it pins | When closed |
|---|---|---|
| **R1** | Two-item review runs end-to-end on a synthetic session; orchestrator restores session state on suite exit (even if killed mid-item). | §6 dogfood |
| **R2** | `naive_retry` strategy: inject a synthetic `sim_err` on first attempt, verify the orchestrator re-runs and marks pass on second attempt. | §4 strategy framework |
| **R3** | Dry-run mode: produces the same plan a real run would execute, with zero side effects on the live session (verified by before/after snapshot diff). | §5 CLI |
| **R4** | Ad-hoc mode: `pvt run --tests T1 --union U.union.json` produces identical DB result rows to a single-item review with the same content. | §5 CLI |

All four gates pinned as pytest cases (using captured snapshots; no live Maestro at test time).

---

## 9. Out of scope for v1

- `gmin_bump` strategy — deferred to v1.3 (separate `asi*` probe phase needed).
- ~~`trans_pss_ic` strategy~~ — **superseded by `ic_from` (§2.5, v1.2)**: this need turned out to be a workflow concern (always-on prerequisite), not a failure-recovery concern, so it lives on the item, not in the Strategy chain. DECISIONS #57.
- Per-test bundle (different bundles for tests in one item) — v1 ships single shared bundle; per-test dict form added when a real case appears.
- Item dependency graph (item B waits for item A pass) — v1 is flat sequential; promote when a real workflow needs it.
- Multi-Maestro orchestration (one review driving N parallel Maestro sessions) — v1 is single-session.
- Report generation (PDF / HTML / waveform PNG attachments) — v1 prints a per-item summary table; the report pillar gets its own phase per `PHASE_PLAN.md`.
- Auto-hook on Maestro sim completion (eliminate manual `pvt run` invocation) — separate phase per `PHASE_PLAN.md`.

---

## 10. Open decisions

| # | Question | Resolved during |
|---|---|---|
| 10.1 | What does `axlGetRunStatus` return during/after a live run? Probe gave `[0,0]` for idle; need to observe run → done → failed transitions before locking the status-decode table in `pvtRunnerGetStatus`. | §3 dogfood |
| 10.2 | Is `axlRunAllTestsWithCallback`'s third argument a SKILL function symbol or a string of the function name? Need to verify against the doc. | §3 first implementation |
| 10.3 | Does Maestro need to be the "current window" for `axlRunAllTestsWithCallback` to dispatch, or can the bridge drive it from CIW context? (Recall the session-detection issue from this probe.) | §3 first dry-fire |
| 10.4 | Exact `asiChangeAnalysis` signature for setting `readns` / `readic` on a PSS / HB analysis — keyword arg name, value shape (raw path string vs. quoted), idempotency. 2026-05-16 probe confirmed the function exists but full args unverified (fnxSession0 has only trans analyses). | v1.2 §4 SKILL implementation, with user-side help loading a PSS analysis. |
