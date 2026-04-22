# Architectural Decision Log

Append-mostly. Each entry captures a decision and its rationale. Edit old entries only to correct errors. If a decision is reversed, append a new entry that supersedes it — never delete.

Format:
```
## #N — Short title
_Date: YYYY-MM-DD_
**Decision:** ...
**Why:** ...
**Alternatives considered:** ... (optional)
**Supersedes / superseded by:** ... (optional)
```

---

## #1 — Three-pillar architecture; build data pillar first
_Date: 2026-04-21_

**Decision:** Project is organized as three weakly-coupled pillars:
1. TB authoring helpers (SKILL-heavy, Python as template engine)
2. Simulation orchestrator (Python drives Maestro/OCEAN)
3. Data layer (structured results, queryable, versionable)

Build order: **data first**, then one isolated authoring helper, then orchestrator.

**Why:** The data layer is the only pillar with no manual workaround; it's the foundation that makes compliance tables and cross-version comparison possible. Authoring and orchestration can be done by hand if needed.

**Alternatives considered:** Monolithic "simulator assistant" — rejected as high-risk and all-or-nothing.

---

## #2 — Data layer: JSON exchange + DuckDB/SQLite query layer
_Date: 2026-04-21_

**Decision:** Simulation dumps produce JSON per run (human-readable, git-friendly archive). A Python ingester loads JSON into DuckDB for cross-run queries.

**Why:** JSON alone makes "TT worst-case across runs" and cross-version delta queries tedious. DuckDB gives SQL for free. Files + DB = both an archive and a query surface.

---

## #3 — Mixed Python↔SKILL bridges; do not force one style
_Date: 2026-04-21_

**Decision:** Different bridge styles for different concerns:
- **File exchange** (JSON out, `load()` in) → data dump path
- **Socket bridge** (skillBridge / CIW socket) → interactive authoring helpers
- **CLI subprocess** (`virtuoso -nograph -replay`) → batch orchestration

**Why:** Each fits a different scenario. A unified bridge adds complexity without benefit.

---

## #4 — No early GUI
_Date: 2026-04-21_

**Decision:** Early phases are CLI + config files + SKILL scripts only. GUI (likely PySide6) only after the useful action set stabilizes.

**Why:** Premature GUI work drains time from feature validation. The real shape of the tool isn't visible until the CLI is in daily use.

---

## #5 — Two ingest triggers, one data pipeline
_Date: 2026-04-21_

**Decision:**
- Interactive sims (user clicking in Maestro, possibly bad/iterative results) → user explicitly marks "save to DB" per run.
- Batch sims (Python orchestrator driven) → auto-ingested.

Both feed the same JSON → DB pipeline. The collector SKILL doesn't know which is which — the trigger decision lives one layer up.

**Why:** Interactive iteration produces noise; batch runs are deliberate. Conflating them pollutes the slice history.

---

## #6 — Project identity: `.pvtproject` file with layered auto-detect
_Date: 2026-04-22_

**Decision:** Identify the project of a Virtuoso session via layered lookup:
1. Env var `PVT_PROJECT`
2. `.pvtproject` YAML file found by walking up from cwd
3. Fallback: interactive → first-save dialog; batch → hard error

`PvtInit(?project ...)` retained as a manual override for rare cases.

**Why:** Every-session declaration is ceremony. Layered auto-detect mirrors git's `.git/` pattern — set up once per project tree, never think about it again.

**Alternatives considered:** Auto-inferring project from cellView path — rejected; unreliable when libs cross projects.

---

## #7 — Three-tier run identity
_Date: 2026-04-22_

**Decision:** Every run is identified at three levels:
- `project_id` — from `.pvtproject`
- `testbench_id` — auto-captured as the active Maestro setup's cellView path (`lib/cell/view`); readable aliases allowed in `.pvtproject`
- `run_id` — auto-generated per dump

**Why:** One project often has multiple testbenches (e.g. heavy vs. lite) open in parallel Maestro windows of the same Virtuoso session. Without `testbench_id`, their data would collapse together in reports and queries.

---

## #8 — Circuit slice = simulated netlist + optional schematic screenshot
_Date: 2026-04-22_

**Decision:** Capture the simulated netlist (`input.scs` or equivalent) as the canonical record of "what circuit was simulated." Schematic screenshots are optional supplementary artifacts.

**Why:** The netlist is what Spectre actually ran — textual, diffable, deterministic, reproducible. It beats editor-state snapshots (can be dirty) and full OA library copies (too heavy, binary, poor diff).

**Alternatives considered:** Full library snapshots (overkill), cellView path + mtime only (breaks when schematic later changes).

---

## #9 — Evidence artifacts as first-class schema citizen; post-hoc attach allowed
_Date: 2026-04-22_

**Decision:** Each run can carry attached non-structured files (waveform PNGs, table screenshots, sim logs, user-uploaded images, PDFs). Stored in a proper `artifacts` table (`run_id`, `type`, `relative_path`, `description`, `source`). Files live on filesystem; DB stores paths, not blobs. Users can attach artifacts to a run days or weeks after the dump.

**Why:** Solves concrete pain — waveform plot/annotation (user pain 3.d), screenshot→OCR→Excel loop (3.f), cross-version visual diff (3.g). First-class schema enables automated report generation.

---

## #10 — Dual-source run notes
_Date: 2026-04-22_

**Decision:** A run carries notes from two sources:
- Maestro's per-test note (semantic — "what this test measures"), pulled automatically by the collector.
- User-written dump-time note (run-level — "what changed vs. last run").

Both stored side-by-side.

**Why:** Different temporalities. Test notes describe stable intent; dump notes describe session-specific context.

---

## #11 — Run vs. slice: label upgrades a run to a slice
_Date: 2026-04-21_

**Decision:** Every dump produces a `run` (auto `run_id`, timestamp, full data). A user-applied `label` upgrades the run to a `slice` — a stable anchor for cross-version comparison. Unlabeled runs are drafts, GC-eligible; slices are retained permanently.

**Why:** Not every run is review-worthy. Labels give the user explicit control over the permanent history, while all runs remain queryable in the short term.

---

## #12 — `PvtDumpToJson.il` is a throwaway POC
_Date: 2026-04-21_

**Decision:** `../MyRunner/PvtDumpToJson.il` proved the dump path is feasible but is not the foundation. Phase 1 writes a new collector from scratch.

**Why:** The POC doesn't match the final schema (three-tier IDs, artifacts, netlist capture, `.pvtproject` identity, etc.). Extending it would cost more than rewriting.
