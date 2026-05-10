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
- [ ] Copy simulated netlist to run dir — soft-miss path works (`netlist_path: null` when collector can't determine simulator); needs follow-up: detect Spectre via `axlGetMainSetupDB`-driven simulator probe rather than current heuristic, which warned `simulator nil is not Spectre` on a real spectre run.
- [ ] Optional screenshot (waveform, results table) via `awvSaveAsImage` / `hiScreenShot` — explicitly deferred to v1.1; current behaviour is one-shot warn + return nil (Decision in S3_DESIGN §3.5).
- [x] Write JSON dump using the spec from task 1 — round-trip verified via `python3 -m json.tool` and via the SKILL parser; `testbench_alias` resolution working.

### 4. Python ingester

- [ ] Scan dump dir → load to DuckDB
- [ ] Handle schema evolution gracefully (JSON carries a `schema_version` field)
- [ ] Idempotent: re-ingesting the same `run_id` is a no-op (or explicit error)

### 5. `pvt` CLI (minimal)

- [ ] `pvt ingest <path>` — manual ingest trigger
- [ ] `pvt attach <run_id> <file> --type ... --desc ...` — post-hoc artifact attach
- [ ] `pvt label <run_id> <label>` — promote run → slice
- [ ] `pvt list [--project ...] [--slice-only]`
- [ ] `pvt diff <slice_a> <slice_b>` — result-table diff + netlist diff

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
