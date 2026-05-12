# TODO

Tasks for the current phase. Check off as completed. At the start of each session, scan this together with `PROJECT_STATE.md`.

Durable source of truth for tasks. Claude's in-session `TaskCreate` may be used to break down an active item into sub-steps during implementation — but the checkboxes here are what persist.

---

## Phase 1 — Data Pillar MVP (COMPLETE)

All six sections shipped (§1 spec, §2 loaders + first-save dialog, §3 collector SKILL with messy-data refactor + Bug A/B/C/D fixes + netlist Spectre fix, §4 ingester, §5 full `pvt` CLI surface, §6 acceptance gates pinned as regression tests). Final tally: 254 / 254 Python tests green, 256 / 1 / 0 SKILL Tier-1 (1 baseline FAIL is the unchanged Maestro-open no-session test). Last commits: `41675e9` (dialog) through `b0c13da` (Phase 2 sharpening).

**Deferred from Phase 1 (do NOT block Phase 2):**
- §2.2 dialog Tier-2 manual UI verification — 5 scenarios documented at `skill/tests/tier2/scenarios.md`, sandbox at `/home/yusheng/cadence_work/dialog_sandbox/`. Pick up alongside any future UI-affecting change.
- §3 walker mock-rdb harness — DECISIONS #23; awaits real gappy-pid sim or budget for the `maeReadResDB` refactor.
- §3 screenshot v1.1 — S3_DESIGN §3.5; current one-shot warn + return nil suffices until a use case shows up.

---

## Phase 2 — PVT-Union Builder

**Goal:** end-to-end loop from "describe one semantic PVT in a sidecar" → "tool emits the exploded Maestro corner table" → "round-trip is bit-identical."

Driven by the VCO LO 2026-05-11 motivating case (21 columns × 3 points = 63 corners that morally describe one PVT). Spec frozen at `docs/phase2_pvt_union_spec.md` (DECISIONS #29-31). Acceptance gates are §6 (Gates U1-U4).

### §1. Specification (no code yet — pure documentation)

- [x] `docs/phase2_pvt_union_spec.md` — pain, data model (vars + models axes), sidecar format, round-trip surface, CLI preview, acceptance gates, versioning, open decisions (8.1-8.6).
- [x] `config/pvt_union.example.json` — worked example matching the live `simkit_verify` corner-table (2 rows → 7 sub-corners).
- [x] `docs/schema.md` §1 additive update — `unionsDir` field added (no version bump, additive per unknown-key policy).

### §2. Python loader + validator

- [ ] `python/simkit/union.py` — JSON → typed union object; validate every §3.2 / §3.3 invariant.
- [ ] `python/simkit/union.py:explode()` — return sub-corner list per §3.4 (alphabetic key + lex-sorted values).
- [ ] `tests/test_union.py` — every load-error invariant; the simkit_verify example must explode to the exact 7-row table from spec §9; length-1 array round-trips without collapse.
- [ ] **Verification gate (per PM-mode rule):** `pytest tests/test_union.py` 100% green; `python -m simkit.union explode config/pvt_union.example.json` prints the spec §9 table verbatim.

**Open decisions blocking §2 start:** 8.1 (multiple unions per bench), 8.2 (unionsDir default), 8.4 (axlSetParameter in v1?), 8.6 (explode order on VCO LO). Pick defaults from spec §8 unless a domain reason surfaces.

### §3. SKILL bridge (pull + push)

- [ ] `skill/pvtCorners.il` — `pvtCornersPull(sess outPath)` per spec §4.3. Vars via `axlGetVars`/`axlGetVar`/`axlGetVarValue`; models via `axlGetModels`/`axlGetModel`/`axlGetModel{File,Section,Block,Test}`. Sidecar JSON via Phase 1 `pvtJson` emitter.
- [ ] `pvtCornersPush(sess unionJsonPath)` — symmetric. Vars via `axlPutVar`; models via `axlPutModel` + `axlSetModel{Section,Block,Test}`.
- [ ] `skill/tests/testPvtCorners.il` — Tier-1 (pure helpers; no live session).
- [ ] **Verification gate (per PM-mode rule):** Tier-2 short non-blocking skillbridge probe — pull from live `fnxSession0` produces a sidecar that diffs cleanly against `config/pvt_union.example.json` (modulo §4.2). Push must NOT run against the live working session; use a sandbox session.

### §4. (no separate §4 — Phase 2 has no analogue of Phase 1's ingester since the data is config, not run output)

### §5. `pvt corners` CLI

- [ ] `pvt corners build <union>.union.json [--out <path>]` — validate + emit Maestro corners-CSV.
- [ ] `pvt corners explode <union>.union.json [--json]` — print sub-corner table.
- [ ] `pvt corners list [--project P]` — enumerate unions in `<unionsDir>/`.
- [ ] `pvt corners diff <a> <b>` — row-by-row axis-by-axis comparison.
- [ ] `pvt corners push <union>.union.json` — delegate to skillbridge.
- [ ] `pvt corners pull <output>.union.json` — delegate to skillbridge.
- [ ] **Verification gate (per PM-mode rule):** pytest covers each subcommand against the example file; manual smoke on `pvt corners explode config/pvt_union.example.json` matches spec §9.

### §6. End-to-end acceptance gates

- [ ] **Gate U1** — Round-trip fidelity on `simkit_verify` (push → pull, bit-identical).
- [ ] **Gate U2** — VCO LO acceptance (real 21-col × 3-pt setup; deferred until VCO LO is loaded in Maestro). DECISIONS may add #32 if probe reveals new constraints.
- [ ] **Gate U3** — Explode arithmetic on a synthetic 2 × 3 × 5 = 30 union.
- [ ] **Gate U4** — Sidecar → CSV → Sidecar bit-identical (modulo §4.2).

### §7. Maintenance (do alongside, not at the end)

- [ ] Update `PROJECT_STATE.md` after each substantial chunk
- [ ] Append new decisions to `DECISIONS.md` as they happen
- [ ] Park any non-Phase-2 ideas into `PHASE_PLAN.md`
- [ ] Keep README usage section current
