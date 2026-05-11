# TODO

Tasks for the current phase. Check off as completed. At the start of each session, scan this together with `PROJECT_STATE.md`.

Durable source of truth for tasks. Claude's in-session `TaskCreate` may be used to break down an active item into sub-steps during implementation — but the checkboxes here are what persist.

---

## Phase 1 — Data Pillar MVP

**Goal:** end-to-end loop from "Maestro sim finishes" → "one command saves it" → "Python can query and diff two slices."

### 1. Specification (no code yet — pure documentation)

- [x] `docs/schema.md`: define `.pvtproject` fields (JSON — see Decision #13)
- [x] `docs/schema.md`: define JSON dump format (per run)
- [x] `docs/schema.md`: define DuckDB tables (`runs` / `results` / `artifacts`) with types
- [x] `config/pvtproject.example.json`: minimal working example

### 2. `.pvtproject` loader

- [x] Python: walker + JSON parser + fallback order (env → file → error). Pure-Python module; unit-testable. — `python/simkit/project.py`, 30 tests passing.
- [x] SKILL: equivalent walker + minimal strict-JSON parser, reading the same file. — `skill/pvtError.il` + `pvtJson.il` + `pvtProject.il`, 76 tests passing via skillbridge (commit `a3c8651`).
- [ ] SKILL-only first-save dialog (`skill/pvtProjectDialog.il`) — separable from §3, can interleave.

### 3. Collector SKILL (new, from scratch — do NOT extend POC)

- [x] Entry point `PvtSave(?histName ?label ?note ?captureScreenshot)`
- [x] Auto-capture: `project_id`, `testbench_id` (Maestro cellView path), timestamp, author
- [x] Pull Maestro per-test notes (via `axlGetNote hsdb "test" name`; null when no note)
- [x] Iterate history results → structured records (`ok` / `failed` / `running` / `no_convergence`)
  - 9 funobj-call sites fixed during Tier-2 verification — see Decision #16. Tier-1 (76 + 41 = 168 tests) passed without exposing them; live skillbridge run produced 42 ok rows on a real 7-test/49-output history after the fix.
- [ ] **Verify non-`done` and no_convergence sentinel paths against real "messy" data.** The 2026-05-10 verification ran a fully-converged sim — every test was `status='done`, every (corner,point,output) triple landed in pass 1, so passes 2 (failed/running) and 3 (no_convergence) ran zero iterations and are **architecturally present but empirically untested**. POC `../MyRunner/PvtDumpToJson.il` covers the same three passes (lines 84–230); cross-reading shows our code matches its `'failed` / `'running` / `symbolToString(unknown)` mapping but the path is unproven against:
  - **partial-convergence corner**: Newton failed for 2 of 6 corners → pass 1 emits 4 rows, pass 3 should emit 2 `__sim_status__` no_convergence rows. Untested.
  - **mid-flight `PvtSave`**: caller invokes while a sim is still running → some tests `'running`, some `'done`. Our pass 2 should emit `__sim_status__` rows with `status:"running"`. Untested.
  - **`'failed` test**: a test that crashed (e.g., spectre `*Error*` mid-tran). Pass 2 path. Untested.
  - **unfamiliar status symbol**: `'aborted` / `'sim_err` / version-specific symbols. Our line 678 (`symbolp` → `_pvtSymToStr`) falls through correctly; line 679 fallback to `"running"` for non-symbol status is **probably wrong** (a nil status would silently become "running" — should be "unknown" to surface the gap).
  - **gap in point ID sequence**: count loop at line 558 exits on the first `(rdb->point pid)` nil. If pid 3 is missing but pid 4 exists, totalPoints undercounts and pid 4 is never visited (POC has same bug).
  - **per-output convergence inside a converged test**: one expression converges, another doesn't, but `tst->status='done`. Pass 1 emits the converged ones; pass 3 logic ("(test, corner, pid) seen but not written") may or may not flag the missing one — depends on whether `pt->outputs` enumerates the failed expression at all. Untested.

  **Proposed approach (pick one or stack):**
  - **(a) Refactor for testability** — split `_pvtCollIterateResults` into `_pvtCollWalkRdb` (live side, returns raw tuples) + `_pvtCollRowsFromTuples` (pure, takes tuples + caches → row list). Then Tier-1 can exhaustively test `RowsFromTuples` against synthetic mixed-status / gappy-pid / weird-symbol inputs. Mock-free but covers the row-shaping logic. Estimated cost: half a session; reduces Tier-2 scope.
  - **(b) Capture-and-replay rdb fixture** — write a one-shot SKILL utility that serialises `(rdb->tests)` + per-point `pt->outputs` results into a SKILL-readable list file; build a stub-rdb that replays from those files. Each "weird" sim becomes a permanent regression fixture. Cleanest long-term, but ~1 full session of infrastructure.
  - **(c) Tier-2 manual-case checklist** — run intentionally-broken sims (kill spectre mid-sweep; constrain so a corner won't converge; invoke PvtSave during run; etc.), eyeball the JSON, log expected vs actual in `skill/tests/tier2/scenarios.md`. Cheap, no auto-regression.
  - **(d) Python-side schema validator** — `python/simkit/validate.py`: ingester invariants — every (project, run_id, test, corner, point) triple has **either** ≥1 ok row **or** exactly one `__sim_status__` row, never both, never neither. Catches collector misclassifications even when the SKILL side passes its own tests. Recommended regardless of (a/b/c).

  **Recommendation:** **(a) + (d)** as the main pair. (a) gives static coverage of the row-shaping logic (the part most likely to have bugs); (d) gives a Python-side safety net the ingester runs on every dump. (c) becomes documentation-only ("here are the scenarios I want covered"); (b) deferred unless (a)+(d) prove insufficient.

  **Status (2026-05-11 overnight — §3 Step 4 landed):**
  - (d) **DONE** — `python/simkit/validate.py` + `tests/test_validate.py` (50 tests). 24 invariants + 2 warnings. Wired inline in the ingester per DECISIONS #17. Independently invocable: `pvt validate <path>`.
  - (a) **DONE through Step 4** — `_pvtCollIterateResults` is the 5-line composer; `_pvtCollWalkRdb` (live walk) + `_pvtCollRowsFromTuples` (pure shaper). Step 4 fixed all four bugs in separate commits:
    - Bug A: pass-2 fallback for non-symbol status → `"unknown"` (was silently `"running"`); validator I12 now flags the gap.
    - Bug B: walker pidList built from `tst->pointID` across `(rdb->tests)` instead of `(while (rdb->point pid) ...)` count-up — gappy pid sequences no longer truncated. **Tier-1 coverage gap flagged**; see DECISIONS #23 (verified by Tier-2 happy-path regression, awaits real gappy-pid sim).
    - Bug C: pass-2 added per-`(cname,pid,tname)` `writtenByTest` skip — validator I1 no longer at risk of ok+sentinel coexistence for the same triple.
    - Bug D: unified marker `_no_corner_vars` across all three passes (scope expanded from TODO's "just pass-3" per DECISIONS #22).
    - Tier-1: 215/1/0 maintained (the 1 baseline FAIL is the Maestro-open no-session test).
    - Tier-2: 42/42 data rows byte-identical to 2026-05-10 reference fixture on `simkit_verify`.
  - (c) — documentation-only; remains pending (Tier-2 scenarios doc not yet written).
  - (b) — still deferred.
  - **Owed Bug B follow-up** (post-Step 4): walker-level Tier-1 test for gappy pidList. Either build a synthetic-rdb harness (DECISIONS #23) or capture a real gappy-pid sim and pin it as a fixture.
- [ ] Copy simulated netlist to run dir — soft-miss path works (`netlist_path: null` when collector can't determine simulator); needs follow-up: detect Spectre via `axlGetMainSetupDB`-driven simulator probe rather than current heuristic, which warned `simulator nil is not Spectre` on a real spectre run.
- [ ] Optional screenshot (waveform, results table) via `awvSaveAsImage` / `hiScreenShot` — explicitly deferred to v1.1; current behaviour is one-shot warn + return nil (Decision in S3_DESIGN §3.5).
- [x] Write JSON dump using the spec from task 1 — round-trip verified via `python3 -m json.tool` and via the SKILL parser; `testbench_alias` resolution working.

### 4. Python ingester

- [x] Scan dump dir → load to DuckDB — `python/simkit/ingest.py` + `db.py` + `schema_sql.py`; 38 tests in `tests/test_ingest.py`.
- [x] Handle schema evolution gracefully (JSON carries a `schema_version` field) — strict `== 1` for v1; unknown major → `SchemaVersionError`, non-int / zero / negative → `MalformedDumpError`.
- [x] Idempotent: re-ingesting the same `run_id` is a no-op (or explicit error) — default `on_conflict="error"` raises `DuplicateRunError`; `"replace"` / `--force` deletes then re-inserts; `"skip"` returns `action="skipped"`. Per-run transactions (DECISIONS #20).

### 5. `pvt` CLI (minimal)

- [x] `pvt ingest <path>` — manual ingest trigger (`python -m simkit.cli ingest` — 7 CLI tests).
- [x] `pvt validate <path>` — invariant audit. `--from-db <run_id>` now wired (DECISIONS #26 covers the DuckDB ↔ ISO normalisation).
- [x] `pvt attach <run_id> <file> --type ... --desc ...` — post-hoc artifact attach (`simkit.attach`; 21 tests covering copy + dup + invalid-type + missing-src + `--as` rename).
- [x] `pvt label <run_id> <label> [--force|--clear]` — promote run → slice (DECISIONS #25; `simkit.label`; 22 tests).
- [x] `pvt list [--project ...] [--slice-only] [--json] [--limit N]` — table or JSON listing (`simkit.list_runs`; 17 tests; opens DB read-only).
- [x] `pvt diff <slice_a> <slice_b> [--threshold REL] [--include-status] [--json]` — aligned table + unified netlist diff (DECISIONS #24 covers slice resolution; `simkit.diff`; 34 tests).

### 6. End-to-end validation

- [ ] Run one real Maestro PVT sim → `PvtSave` → `pvt ingest` → query from Python
- [ ] Validate: TT worst-case query across corners
- [ ] Validate: netlist diff between two slices with a known manual change
- [ ] Validate: attach a screenshot post-hoc and retrieve it

### 7. Maintenance (do these as part of the work, not at the end)

- [ ] Update `PROJECT_STATE.md` after each substantial chunk
- [ ] Append new decisions to `DECISIONS.md` as they happen
- [ ] Drop Phase-2-worthy ideas into `PHASE_PLAN.md` (don't let them contaminate Phase 1)
- [ ] Keep README usage section current

---

## Suggested start

`1. Specification` → `2. .pvtproject loader (Python)`. Both are offline-testable with zero Cadence dependency; fast feedback loop. Builds the habit of updating DECISIONS.md / PROJECT_STATE.md before we hit SKILL-debugging friction.
