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
- §3 messy-data Tier-2 against real failing-sim histories — **DONE 2026-05-12 via user-pre-staged histories**:
  - `simkit_simerr` (all sim err) — pass-2 produces 7 `failed`-status `__sim_status__` sentinels, one per active sub-corner. Clean.
  - `simkit_Rtime_err` (one corner's Rtime_clkout eval err) — pre-fix the row was silently dropped; **DECISIONS #35 introduced per-output `eval_err` sentinel** as the fix. Post-fix: 42 rows = 41 ok + 1 eval_err for `(TT_2p5G, Rtime_clkout)`. Both histories serve as standing Tier-2 references for the messy-data paths.

---

## Phase 2 — PVT-Union Builder

**Goal:** end-to-end loop from "describe one semantic PVT in a sidecar" → "tool emits the exploded Maestro corner table" → "round-trip is bit-identical."

Driven by the VCO LO 2026-05-11 motivating case (21 columns × 3 points = 63 corners that morally describe one PVT). Spec frozen at `docs/phase2_pvt_union_spec.md` (DECISIONS #29-31). Acceptance gates are §6 (Gates U1-U4).

### §1. Specification (no code yet — pure documentation)

- [x] `docs/phase2_pvt_union_spec.md` — pain, data model (vars + models axes), sidecar format, round-trip surface, CLI preview, acceptance gates, versioning, open decisions (8.1-8.6).
- [x] `config/pvt_union_example.union.json` — worked example matching the live `simkit_verify` corner-table (2 rows → 7 sub-corners).
- [x] `docs/schema.md` §1 additive update — `unionsDir` field added (no version bump, additive per unknown-key policy).

### §2. Python loader + validator

- [ ] `python/simkit/union.py` — JSON → typed union object; validate every §3.2 / §3.3 invariant.
- [ ] `python/simkit/union.py:explode()` — return sub-corner list per §3.4 (alphabetic key + lex-sorted values).
- [ ] `tests/test_union.py` — every load-error invariant; the simkit_verify example must explode to the exact 7-row table from spec §9; length-1 array round-trips without collapse.
- [ ] **Verification gate (per PM-mode rule):** `pytest tests/test_union.py` 100% green; `python -m simkit.union explode config/pvt_union_example.union.json` prints the spec §9 table verbatim.

**Open decisions blocking §2 start:** 8.1 (multiple unions per bench), 8.2 (unionsDir default), 8.4 (axlSetParameter in v1?), 8.6 (explode order on VCO LO). Pick defaults from spec §8 unless a domain reason surfaces.

### §3. SKILL bridge (pull + push)

- [x] `skill/pvtCorners.il` — `pvtCornersPull(?sess ?outPath ?unionName)` per spec §4.3. Vars via `axlGetVars`/`axlGetVar`/`axlGetVarValue`; models via `axlGetModels`/`axlGetModel`/`axlGetModel{File,Section,Block,Test}`. Sidecar JSON via Phase 1 `pvtJson` emitter. **VERIFIED via Tier-1 + Tier-2 live probe 2026-05-12; see DECISIONS #32 #33.**
- [x] `pvtCornersPush(?sess ?unionJsonPath ?dryRun)` — vars via `axlPutVar`; models via `axlPutModel` + `axlSetModel{Section,Block,Test}`. **VERIFIED 2026-05-12 against fnxSession0** (3 corners incl. vars+models sweeps): pull → push → pull round-trip is byte-identical modulo per-call `name` field. Tier-1: 256 → 300 / 0 (1 baseline FAIL flipped to PASS after Cadence restart; +13 push-side helper cases).
- [x] `skill/tests/testPvtCorners.il` — Tier-1 cases for pure helpers (30 cases registered; suite 256 → 286 / 1 / 0).
- [x] **§3.V Verification gate** — CLEARED 2026-05-12 after user reloaded sbStart.il. SKILL Tier-1 256 → 286 / 1 / 0 (1 baseline FAIL is Maestro-open no-session test, unchanged). Tier-2 live pull from `fnxSession0` reproduces spec §9 7-sub-corner table; Python `load_union` + `explode` round-trip is byte-clean. Four SKILL bugs caught during verification (1 arg-order, 4 operator-shorthand) and fixed; DECISIONS #32 records the named-function-vs-operator-shorthand rule, #33 records the verification.

### §4. (no separate §4 — Phase 2 has no analogue of Phase 1's ingester since the data is config, not run output)

### §5. `pvt corners` CLI

- [x] `pvt corners build <union>.union.json [--out <path>]` — emit Maestro-importable corners-CSV (recovery path independent of skillbridge). Open Decision 8.3 resolved 2026-05-13 via reverse-engineering of a real `fnxSession0` GUI export.
- [x] `pvt corners explode <union>.union.json [--json]` — print sub-corner table.
- [x] `pvt corners list [--project P]` — enumerate unions in `<unionsDir>/`.
- [x] `pvt corners diff <a> <b>` — row-by-row axis-by-axis comparison.
- [x] `pvt corners push <union>.union.json [--project P] [--session S] [--dry-run]` — skillbridge → `pvtCornersPush`.
- [x] `pvt corners pull <output>.union.json [--project P] [--session S] [--union-name N]` — skillbridge → `pvtCornersPull`. Pull now also captures per-row `enabled` and per-model `_file_abs` (2026-05-13 extension).
- [x] `pvt corners restore <csv> [--testbench-id ID] [--session S] [--dry-run]` — parse CSV → Union → push via skillbridge. Convenience companion to `build`; **the truly skillbridge-independent recovery path is still Maestro GUI `Tools → Corners → Import`**.
- [x] **Verification gate (per PM-mode rule):** pytest covers each subcommand (29 in `test_corners_cli.py`; 18 in `test_skill_bridge.py` for the wrapper layer; 18 in `test_corners_csv.py` for the emitter). Live runtime-verified 2026-05-13 against `fnxSession0`: pull → push → pull → diff is 3/3 identical, dry-run does not perturb live state, `build` produces a CSV byte-identical to GUI export.

### §6. End-to-end acceptance gates

- [x] **Gate U1** — Round-trip fidelity on `fnxSession0` (live Maestro). Manually verified 2026-05-12 via `/tmp/probe_push.py` and offline-pinned 2026-05-13 (commit `8ae37bf`) via captured baseline → edited → post_edit_pull triple in `tests/fixtures/unions/u1_*` + 6-case `TestGateU1RoundTrip`. The edit-persists-and-pulls-back invariant: TT.temperature 55→85 push survives and re-pulls byte-identical; non-TT rows unaffected; baseline pushed back restores `fnxSession0` to its original 3-row state.
- [x] **Gate U2** — VCO LO acceptance. 2026-05-13: user didn't have VCO LO loaded; I synthesised the 21-row × 3-pt shape from the PHASE_PLAN.md / DECISIONS #29 description and pushed it into the live `fnxSession0`. Session went 3 → 24 rows; all 21 pushed rows pull back byte-identical (vars + models). Offline pinned at `tests/fixtures/unions/vco_lo_21x3.union.json` with 5 pytest cases in `TestGateU2VCOLoAcceptance` (load, row-count, ind-temp × process matrix, temperature-sweep shape, explode → 63 sub-corners, section-per-process). Open Decision 8.6: per-row sweep is only 1 axis × 3 values, so this case doesn't stress alphabetic-key explode order — that's still pending a multi-axis-per-row real case.
- [x] **Gate U3** — Explode arithmetic on a synthetic 2 × 3 × 5 = 30 union (`tests/test_acceptance_phase2.py::TestGateU3ExplodeArithmetic`, 6 tests).
- [x] **Gate U4** — Sidecar → CSV bit-identical against Maestro GUI export, pinned 2026-05-13 in `TestGateU4SidecarToCSV` (5 cases). Round-trip live verification (Import the emitted CSV via Maestro GUI on a crash-recovery sandbox) **still owed to user** — see PROJECT_STATE.md "Owed" section.

### §7. Maintenance (do alongside, not at the end)

- [ ] Update `PROJECT_STATE.md` after each substantial chunk
- [ ] Append new decisions to `DECISIONS.md` as they happen
- [ ] Park any non-Phase-2 ideas into `PHASE_PLAN.md`
- [ ] Keep README usage section current

---

## Phase 3B — Formula-Template Authoring (SKELETON DONE 2026-05-14)

**Goal:** Close the Define layer. Skeleton that lets the user author + persist their own formula templates and apply them to a live Maestro session. NO pre-baked template library in v1.

Spec at `docs/phase3b_measure_template_spec.md`. Eight design decisions in `DECISIONS.md` #38–#41, restore safety fix in #42. Final tally: Python 607/607, SKILL Tier-1 347/5. M1/M3/M4 offline-pinned; M2 live-verified on `fnxSession0`.

### §1. Specification

- [x] `docs/phase3b_measure_template_spec.md` — problem, three-sidecar data model, Maestro round-trip surface, CLI preview, acceptance gates, versioning, open decisions.
- [x] `config/template_example.template.json` — worked example reverse-engineered from `fnxSession0`'s Rtime_clkout (real composite expression).
- [x] `config/signal_group_example.siggroup.json` — minimal example.
- [x] `config/measure_bundle_example.measure.json` — minimal example tying the above together.
- [x] `docs/schema.md` §1 additive update — `templatesDir`, `signalGroupsDir`, `measurementsDir` fields added.

### §2. Python loader + validator + paste-importer — DONE

- [x] `python/simkit/template.py` (425 LOC, 29 cases in `test_template.py`).
- [x] `python/simkit/signal_group.py` (165 LOC, 20 cases).
- [x] `python/simkit/measure_bundle.py` (412 LOC, 24 cases).
- [x] `python/simkit/template_render.py` (163 LOC, 15 cases).
- [x] `python/simkit/template_paste.py` (258 LOC, 18 cases). Quote-preservation fix landed mid-flight (replaces `"/path"` → `"$SIG"` keeping outer quotes so render reconstitutes byte-equal to source).

### §3. SKILL bridge — DONE

- [x] `skill/pvtMeasure.il` (685 LOC) — `pvtMeasurePush` + `pvtMeasurePull` per spec §4.3.
- [x] `skill/tests/testPvtMeasure.il` (244 LOC, +47 Tier-1 cases; SKILL Tier-1 300/5 → 347/5).
- [x] **§3.V Verification gate** — `/tmp/probe_p3b_skill.py` ran the push → export-verify → pull-verify → cleanup cycle in 0.04 s wall on `fnxSession0`; baseline restored. Probe transcript recorded in Subagent B's report.

### §5. `pvt measure` CLI — DONE

- [x] All 12 subcommands wired in `python/simkit/cli/measure.py` (1374 LOC, 53 cases in `test_cli_measure.py`):
      `new-template`, `list-templates`, `show-template`, `new-signal-group`, `list-signal-groups`,
      `new-bundle`, `list-bundles`, `render`, `apply`, `pull`, `diff`, `restore`.
- [x] `python/simkit/skill_bridge.py` extended +272 LOC: `pvt_measure_push` / `pvt_measure_pull` / `pvt_measure_restore` wrappers (27 cases in `test_skill_bridge_measure.py`).
- [x] **Verification gate** — pytest covers each subcommand; live runtime verification of `apply` + `pull` + `restore` against `fnxSession0` clean (see Gates M2 + M3 below).

### §6. End-to-end acceptance gates

- [x] **Gate M1** — Paste-import faithfulness. Pinned as 3-case `GateM1PasteRoundTripTests` in `tests/test_template_paste.py`. Paste `fnxSession0`'s real `Rtime_clkout` → render with V_LOW=10, V_HIGH=90, signal=`/Vout` → byte-equal to source.
- [x] **Gate M2** — Apply round-trip. **Live-verified 2026-05-14** against `fnxSession0` via `/tmp/verify_m2_m3_live.py`: `pvt measure apply config/voltage_outs_rise.measure.json` lands `Rtime_Vout` with the exact `_pasted_from` expression; existing 11 rows untouched. Offline pinning deferred (live capture suffices; M1 covers the offline contract).
- [x] **Gate M3** — Snapshot bit-identical. **Live-verified 2026-05-14**: pull → restore (merge mode, post-DECISIONS #42) → pull → bit-identical (modulo `_`-prefixed keys and key order). Offline pinning deferred.
- [x] **Gate M4** — Python-side validation. 8/8 negative cases pinned across `test_template.py` / `test_measure_bundle.py` / `test_template_render.py` (unbalanced parens, undeclared $PARAM, unreferenced param, quote imbalance, signal-group/template mismatch in both directions, missing required override, output-name collision).

### §7. Maintenance — kept up alongside

- [x] PROJECT_STATE.md updated.
- [x] DECISIONS.md #38–#42 appended.
- [x] PHASE_PLAN.md marks P3B done; A (sim orchestrator) flagged as next candidate.
- [ ] README usage section — pending.

### Phase 3B v1.1 — Builtins library (DONE 2026-05-14)

Same-day extension after the skeleton stabilised. 17-template library + install CLI + walkthrough fixture, all derived from one of the user's real production Outputs CSVs (sim_DCOBUF, 130 rows). DECISIONS #43 captures the inventory + the three shape choices (ANALYSIS-as-param, signal+string edge_delay, collision-as-loud-failure).

- [x] 17 builtins authored under `config/builtins/*.template.json`: `i_avg_window`, `i_avg_full`, `freq_window`, `duty_cycle_window`, `rise_time_auto`, `fall_time_auto`, `rise_time_fixed`, `fall_time_fixed`, `dft_window`, `dft_mag_at_freq`, `dft_phase_at_freq`, `db20_ratio`, `edge_delay_avg`, `edge_delay_wave`, `cycle_wrap_positive`, `phase_diff_wrap`, `value_at`.
- [x] `pvt measure install-builtins [--force] [--names …] [--list]` CLI in `python/simkit/cli/measure.py`. Default refuses to overwrite existing templates; `--force` overwrites; `--names` installs a subset; `--list` dry-runs.
- [x] `tests/test_builtins.py` (5 cases) — load + render every builtin, byte-for-byte against 17 reverse-engineered DCOBUF formulas.
- [x] `tests/test_cli_measure.py::InstallBuiltinsCliTests` (8 cases) — empty-install / dry-run / `--names` subset / unknown-name reject / collision-refuse / `--force` overwrite / missing-project / post-install listable.
- [x] `tests/fixtures/builtins_walkthrough/` + `tests/test_builtins_walkthrough.py` (4 cases) — 4-entry bundle collapses 20 hand-written DCOBUF rows; signal-basename collision pinned as a deliberate `RenderError`.
- [x] Python suite 598 → 602 / 0.

### Phase 3B v1.2 — Expressiveness pass (DONE 2026-05-15)

Dogfood-driven follow-on. fnxSession0's 11-row baseline (4 nets + 7 expr) couldn't be expressed in a single v1.1 bundle. Six friction items closed:

- [x] (c) implicit `signal_group: null` when template has no signal-kind param
- [x] (d) `list-bundles` STATUS column shows `ERR: <reason>` with bundle-path prefix stripped
- [x] (a) apply-entry `output_name` field fully shadows the concat scheme; supports `${SIG}` placeholder
- [x] (b) 4 new `_full` builtins (rise/fall × auto/fixed) — drop the `clip(t_1, t_2)` wrap; follow `i_avg_window`/`i_avg_full` naming precedent rather than adding a CLIP parameter
- [x] (f) `raw_expression` apply-entry kind — peer to template entries, schema enforces exactly-one-of
- [x] (e) `param_sweep: {KEY: [...]}` + parallel `output_names: [...]` — single-axis sweep expansion
- [x] `measure_schema_version` bumped to 2; v1 bundles still load; v2-only fields rejected in v1 with a "bump to 2" error
- [x] **Dogfood proof** — `~/cadence_work/simkit_p3b_dogfood/measurements/dogfood_v2.measure.json` (3 entries: 1 raw + 1 sweep + 1 template-with-override) describes all 7 fnxSession0 expr rows; apply --replace → pull → diff vs. baseline.snapshot.json is 11/11 byte-identical
- [x] Python suite 602 → 662 / 0 (+60 cases across measure_bundle / template_render / builtins / cli_measure)
- [x] DECISIONS #44

### Phase 3B v1.3 — Spec passthrough (DONE 2026-05-15)

User pointed out v1.2 silently discarded the Maestro Spec column on apply, even though pull captured it. v1.3 closes the gap:

- [x] `MeasureApply.spec: Optional[str]` field accepted on template / raw / sweep entries
- [x] Loader prefix sanity check (`<`, `>`, `<=`, `>=`, `range`, `tol`, `[`, digit/sign/dot)
- [x] `RenderedRow.spec` propagated through all three entry kinds; uniform across signal-group + sweep expansion
- [x] `rendered.csv` gains a trailing `spec` column
- [x] JSON envelope passes `spec` field down to SKILL
- [x] `skill/pvtMeasure.il` — `_pvtMeasureSafeEvalNumber` (char-set guard before evalstring because errset CANNOT catch unbound-var errors from evalstring) + `_pvtMeasureParseSpec` (Cadence-native strings to tagged op + value tuples) + `_pvtMeasureApplyParsedSpec` (dispatch to `axlAddSpecToOutput` with the exclusive `?lt`/`?gt`/`?min`/`?max`/`?range`/`?tol` keyword) + sdb plumbed through `_pvtMeasurePushOne`
- [x] Per-row `spec_status` field on the push report (separate from the primary `added`/`replaced` signal — spec failure does NOT abort the batch)
- [x] SKILL Tier-1 +15 cases (`measure/parseSpec/*` + `measure/safeEval/*`); Tier-1 347 → 376 / 1 (baseline FAIL unchanged)
- [x] Python +16 cases across `test_measure_bundle.py` (load + v1-schema gate), `test_template_render.py` (3 entry kinds × signal + sweep paths), `test_cli_measure.py` (rendered.csv spec column). Suite 662 → 678 / 0
- [x] **Live dogfood** `measurements/dogfood_v3.measure.json` — 5×PN_* with `<-100` + Rtime_clkout with `<100p`. Cadence normalises on store: `<100p` → `< 1e-10`, `<-100` → `< -100`. Round-trip is semantic, not byte-identical
- [x] DECISIONS #45

### Deferred from Phase 3B v1.3 → v1.4 (do NOT block next phase):

- **#1 PASS/FAIL CAPTURE IN COLLECTOR** (surfaced 2026-05-16 by live spec sim dogfood). The Phase 1 collector predates specs. After v1.3 specs land in Maestro, a sim runs, PvtSave dumps results — but `result.status` only encodes "computed without eval-err". Spec verdict is silently dropped: `pvt list` / `pvt diff` cannot answer "which corners failed spec?" The fix touches several layers:
    1. `skill/pvtCollect.il` — capture `axlGetSpecData` / per-result pass/fail at iterate time (probably `axlGetResultSpecVal` or similar — needs live SKILL probe).
    2. `docs/schema.md` + run.json schema version bump — add `spec` (string) + `spec_status` (pass/fail/no_spec) per-result column.
    3. `python/simkit/ingest.py` + DuckDB DDL — new columns; `pvt list --failed-only`; `pvt diff --spec-changes`.
    4. Acceptance: dogfood replay against `simkit_check_spec` history should reproduce the 6 PASS verdicts the user saw in the GUI.
   This is the highest-priority v1.4 because it unblocks "orchestrator runs 100 corners → tell me what's red" which is the natural Phase 3A entry condition.

- **Per-iteration spec on sweep entries** — currently single spec applies uniformly to N swept rows. Per-row spec (e.g. PN @ 1MHz < -100, PN @ 100MHz < -140) needs a parallel `specs: [...]` array alongside `output_names`.
- ~~**`axlGetSpecData` / `axlGetSpecWeight` on pull**~~ — **SCOPED DOWN 2026-05-16, DECISIONS #46.** Probe found weight IS readable (`axlGetSpecWeight(spec_int_handle)`) but info is write-only (no `axlGetSpecInfo`). User confirmed they don't manually touch weights (all default to 1.0). Pull-only capture without matching `MeasureApply.spec_weight` would be a misleading half-feature. Deferred to v1.5; full round-trip when a real weight workflow surfaces.
- ~~**Dotted `X..Y` range form in spec parser**~~ — **DONE 2026-05-16, DECISIONS #46.** `_pvtMeasureParseSpec` accepts X..Y via `index(s "..")`-based split (avoids parseString single-char delimiter issue). Pre-checks: rejects 3+ consecutive dots ("1...2") and multiple `..` occurrences ("1.5..2.5..3.5", which would crash the SKILL reader uncatchably via evalstring). SKILL Tier-1 376 → 385 (+9 cases); Python +2 cases on bundle load.
- ~~**Spec status in apply CLI summary**~~ — **DONE 2026-05-16, DECISIONS #46.** `_decode_push_row` captures `spec_status` into `PvtMeasurePushRow`; apply summary formatter adds an inline `spec: ok` / `spec: failed: <reason>` per row + an aggregate `spec totals: N ok, M failed` tail when any row carries a spec. Python suite +5 cases.
- **Per-signal alias map** — v1.1 walkthrough pinned the collision (`/VDD` × 4 supplies share basename). Signal group needs to declare `aliases: {"path": "label"}`.
- **Multi-axis param_sweep** — v1.2 enforces single-axis. Real `freq × temperature` 2-D matrix case would justify lifting it.
- Multi-signal templates (v1 enforces ≤1 `signal`-kind param per template; edge_delay uses 1 signal + 1 string ref as the workaround).
- Cross-project template sharing (user-home `~/.simkit/templates/`).
- Snapshot template match-back (reverse-engineer a pulled snapshot into bundle + parameters).
- Offline acceptance gates M2 + M3 (currently live-verified; would need captured fixture pair like Phase 2 Gate U1).

---

## Phase 3A — Simulation Orchestrator (IN PROGRESS — kicked off 2026-05-16)

**Goal:** Close the **execution** pillar. Engineer writes one `*.review.json` describing 5-15 named items (each = tests + union + bundle); `pvt run` drives Maestro through the whole battery, applies retry strategies on convergence failures, auto-ingests results.

Spec at `docs/phase3a_orchestrator_spec.md`. Four design decisions in `DECISIONS.md` #50-53.

### §1. Specification — DONE

- [x] `docs/phase3a_orchestrator_spec.md` — problem, sidecar shape, item shape, orchestrator loop, failure semantics, strategy plugin contract, SKILL bridge surface, CLI surface, out-of-scope list, open decisions.
- [x] `config/review_example.review.json` — 5-item BT2GRX-style example (trans PVT + PSS PN for two modes + interference sim).
- [x] DECISIONS #50 (sidecar shape), #51 (per-corner skip + sequential items), #52 (strategy plugin + v1 scope = framework + naive_retry only; gmin/PSS-IC → v1.1), #53 (SKILL API map).
- [ ] `docs/schema.md` §1 additive update — add `reviewsDir` field (default `./reviews`); additive per unknown-key policy (no version bump). **TODO during §2.**

### §2. Python sidecar loader + validator

- [ ] `python/simkit/review.py` — JSON → typed `Review` + `ReviewItem` dataclasses; validate every §2 invariant from spec.
- [ ] `on_failure` deep-merge logic (item overrides suite).
- [ ] Cross-reference validation: union/bundle paths resolve relative to review file; filename basename equals `name` field; `project` matches enclosing `.pvtproject`.
- [ ] `python/simkit/project.py` — add `reviewsDir` field (default `./reviews`).
- [ ] `tests/test_review.py` — every load-error invariant + the example file loads + happy-path merge + deep-merge edge cases.
- [ ] **Verification gate:** `pytest tests/test_review.py` 100% green; `python -m simkit.review validate config/review_example.review.json` reports no errors (will warn on missing union/bundle paths since the example doesn't ship those — that's expected).

### §3. SKILL run-control bridge

- [ ] `skill/pvtRunner.il` — production-side helpers per DECISIONS #53 + spec §6:
    - `pvtRunnerEnableOnly` / `pvtRunnerSnapshotTestState` / `pvtRunnerRestoreTestState`
    - `pvtRunnerSubmit` (wraps `axlRunAllTestsWithCallback`)
    - `pvtRunnerWait` / `pvtRunnerGetStatus`
    - `pvtRunnerCollectHistory` (reuses Phase 1 `pvtCollIterateResults` per envelope shape)
- [ ] `python/simkit/skill_bridge.py` — Python wrappers: `pvt_runner_enable_only`, `pvt_runner_submit`, `pvt_runner_wait`, `pvt_runner_collect_history`, etc.
- [ ] `skill/tests/testPvtRunner.il` — Tier-1 mock-free cases for the pure helpers.
- [ ] **§3.V Verification gate (live)**: against an open Maestro session, dry-fire `pvtRunnerSubmit` on a 1-test 1-corner setup, observe `axlGetRunStatus` transitions, capture the actual status code map → resolves open decisions 10.1 + 10.2 + 10.3.

### §4. Failure-strategy plugin framework + 1 placeholder built-in

- [ ] `python/simkit/strategies/__init__.py` — discovery (both built-in and `<project>/strategies/*.py`).
- [ ] `python/simkit/strategies/base.py` — `Strategy` base class + `StrategyContext` + `StrategyResult` + Bridge facade.
- [ ] `python/simkit/strategies/naive_retry.py` — built-in placeholder (no intervention, re-run up to N times).
- [ ] `tests/test_strategies.py` — framework happy path + naive_retry + user-plugin discovery + revert-on-failure.
- [ ] `docs/phase3a_writing_strategies.md` — one worked example showing how to write a custom strategy (anticipates the v1.1 gmin_bump shape).

### §5. Orchestrator runtime + CLI

- [ ] `python/simkit/orchestrator.py` — main loop per spec §3 pseudocode; session-state snapshot+restore wrapper; per-item result aggregator.
- [ ] `python/simkit/cli/run.py` — `pvt run`, `pvt run --dry-run`, `pvt run --items`, `pvt run --tests/--union/--bundle` ad-hoc, `pvt run --list-strategies`.
- [ ] `python/simkit/cli/review.py` — `pvt review init`, `pvt review validate`.
- [ ] `tests/test_orchestrator.py` — pure-Python orchestration with mocked SKILL bridge: per-corner-fail skip behaviour, strategy chain invocation, dry-run no-side-effect, ad-hoc mode parity.
- [ ] `tests/test_cli_run.py` — CLI smoke + arg parsing + exit codes.

### §6. End-to-end acceptance + live dogfood

- [ ] **Gate R1** — two-item review runs end-to-end on a synthetic captured session; orchestrator restores session state on suite exit (including mid-item kill via SIGINT).
- [ ] **Gate R2** — `naive_retry` strategy: synthetic `sim_err` first attempt, verify re-run marks pass on second attempt.
- [ ] **Gate R3** — `--dry-run` mode: produces the same plan a real run would execute, with zero side effects (verified by before/after snapshot diff).
- [ ] **Gate R4** — Ad-hoc mode parity: `pvt run --tests T1 --union U.union.json` produces identical DB result rows to a single-item review with the same content.
- [ ] Live dogfood on user's real BT2GRX-style or fnxSession0 review (whichever session the user picks). Observe friction, capture as DECISIONS / v1.1 backlog.

### Phase 3A v1.1 — DONE 2026-05-16

- [x] **#1 (architectural): split SKILL `pvtRunnerRun` → `pvtRunnerSubmit` + `pvtRunnerRename`** so Python can insert poll-to-idle between dispatch and rename (DECISIONS #55). Legacy `pvtRunnerRun` kept as a thin wrapper for direct CIW callers.
- [x] **#1 (Python state machine):** `pvt_runner_run` rewritten — Submit → poll loop on `pvt_runner_get_status` with `saw_non_idle` + `idle_streak` + `dispatch_grace` exit branches → Rename. New kwargs: `poll_interval`, `timeout_sec`, `idle_confirm_reads`, `dispatch_grace_reads`, `initial_wait_sec`, `post_idle_quiesce_sec`.
- [x] **#1 (handle-0 translation):** `axlGetRunStatus` throws an *uncatchable* C-level error when there's no in-flight run; Python wrapper catches the RuntimeError and translates the "handle 0" message to synthetic `(0, 0)` (errset can't trap this; probed 2026-05-16).
- [x] **#1 (Tier-1):** new `skill/tests/testPvtRunner.il` covers validation paths (empty/nil/non-string `historyName` rejected by `pvtRunnerRename` and legacy `pvtRunnerRun`; `_pvtRunnerResolveSession` string-passthrough; existence of all 8 production procs). SKILL Tier-1 394/1 → 407/1.
- [x] **#1 (Python tests):** 9 new state-machine cases in `tests/test_skill_bridge.py` (cached path, real-run path, handle-0 translation, timeout, idle_confirm_requires_consecutive, empty-name pre-submit rejection, get_status translation isolation). Python 835 → 868.
- [x] **#1 (orchestrator):** `execute()` gains optional `run_kwargs` dict forwarded to `pvt_runner_run` per item, so callers can tune poll/timeout/grace without rewiring.
- [x] **#1 (live verify):** cached path on `fnxSession0` → 3.4s, no ASSEMBLER-2423 on subsequent delete. Real-Spectre path on `fnxSession0` (TT_v11verify, temp=56) → exposed that `axlGetRunStatus` is BLIND on this installation; state machine exits via dispatch_grace too early; rename works mid-sim but post-run destructive ops still hit the modal. Documented as the v1.2 #1 follow-up.

### Phase 3A v1.3 — DONE 2026-05-17 PM

- [x] **Cross-item IC piping (`ic_from` schema + orchestrator + SKILL pre-run script generator)** — DECISIONS #57 / 5 commits 503fa0b → efb4cac landed 2026-05-16/17 AM.
- [x] **`readic="<path>"` syntax fix** (efb4cac) verified end-to-end on `fnxSession0` 2026-05-17 PM. 6 sub-points of TT_pvt each received the correct per-corner IC path in their `simulatorOptions options` block.
- [x] **Diagnostic confirmation** that `axlGetCornerNameForCurrentPointInRun` emits exploded sub-point names (`TT_pvt_0..5`) for sweep-row corners — original v1.3 cornerMap keys were always correct; earlier "failed" retry had been mis-scoped to single-point TT corner.
- [x] **sdb-handle pass-through infrastructure** (DECISIONS #58) — `skill_bridge.get_sdb()` + polymorphic `sess` in `pvtRunner.il`. Bypasses Cadence's window-focus-keyed session-name resolution after `axlRunAllTests`. Wedge detection translates 3 known failure patterns into actionable `SkillBridgeError`.
- [x] **A/B test cleared serial-dispatch misattribution** — `~1s per sub-point` is Maestro's local sim default, not pre-run-script overhead. Same TT_pvt 6-sub-point batch with vs without pre-run script showed identical timing.
- [x] **All 7 commits (5 stage-2/3 + 1 AM handoff doc + 1 closeout) pushed to `origin/main` as of `f8e5f69`.**

### Phase 3A v1.4 — DONE 2026-05-18 AM

- [x] Baseline-corner preservation (`ReviewItem.baseline_corner` + `_pick_baseline_corner`). Commit `9b5328f` / DECISIONS #59.
- [x] Worker-VM socket-steal root cause + sibling skill_tools fix. Commit `a69bf12` (doc) + sibling `e8c76e9` / DECISIONS #60.

### Phase 3A v1.5 — DONE 2026-05-18 PM

- [x] **§5 close — `pvt run` CLI ↔ `execute()` wiring.** Removed the exit-5 "not yet implemented" gate; threaded `--session` (or PVT_SESSION env), `--no-push-union`, `--history-prefix` flags; pvtproject_path auto-walked from review.json's parent dir. Exit codes 0=ok / 6=partial / 7=bridge-import-fail. 6 new live-mode tests with mocked execute() (15/15 in test_cli_run.py).
- [x] **v1.5 F1: validator allows `pending` status.** SKILL collector legitimately emits `pending` for sub-corners that haven't started yet (PvtSave fired before axlRunAllTests queued them). I12 + I1 sentinel-status whitelists both extended. Caught + fixed during 1st real `pvt run` dogfood when both run.jsons were rejected on ingest.
- [x] **v1.5 F2: `pvtRunnerCountRunning` rdb-walker discriminator (closes v1.2 #1).** SKILL helper walks current-history rdb counting non-terminal `tst->status` rows; Python state machine AND-s it with `axlGetRunStatus` for the idle-confirm gate. New opt-in `min_running_observed` kwarg. Live-verified twice on fnxSession0: 0 pending/running rows in either dump (vs pre-F2: 6 pending sentinels in item 1). DECISIONS #61.

### Phase 3A v1.6 — DONE 2026-05-18 PM (DECISIONS #62)

- [x] **v1.2 #3 closed: per-corner FAIL detection + strategy chain dispatch in `execute()`** — `simkit.failures.find_failed_corners` (DB read) + `naive_retry` rewritten with per-corner enable narrowing + `_run_strategy_chain` in orchestrator + `ItemResult` extended with `strategy_attempts` / `final_failed_corners` + CLI exit code 6 honours `final_failed_corners`. Sub→row mapping via longest-prefix match handles sweep rows (`TT_pvt_3` → enable row `TT_pvt`). Live-verified twice on fnxSession0: phase 1 = clean (eval_err surfaced, naive_retry correctly skipped); phase 2 = forced retry (eval_err relabelled to spec_fail) drove the full snapshot/restore/run/ingest/re-query path end-to-end. Python 963 → 996 (+33). DECISIONS #62 records D1-D4 design + the two production bugs caught (path shape + envelope shape) + alternatives rejected.

### Phase 3A v1.7 — DONE 2026-05-18 EOD (DECISIONS #63)

- [x] **`gmin_bump` strategy implementation** — `simkit.strategies.gmin_bump.GminBump` uses the v1.3 pre-run-script mechanism to inject per-corner `asiSetSimOptionVal asi "gmin" <bump>` inside the worker VM. Default ramp `[1e-11, 1e-10, 1e-9]`, max_attempts=3. Sidecar params: `ramp`, `option_name`, `baseline_value`, `max_attempts`. Registered in `_BUILTINS`.
- [x] **A5 live-verify caught state-leak bug** — worker-VM asi session is shared across sub-corners in the same sweep row, NOT per-sub-corner-fresh. Initial probe targeting only TT_pvt_3 with 9.99e-10 left TT_pvt_4 + TT_pvt_5 carrying the bumped value.
- [x] **A6 fix**: `PreRunSpec.baseline_value` field — when set, renderer emits a baseline-write-first body that resolves asi unconditionally then conditionally overlays the per-corner override. ic_from's existing shape (`baseline_value=None`) unchanged.
- [x] **A7 re-verify**: only TT_pvt_3 carries 9.99e-10; TT_pvt_4/5 back to 1e-12 on both `Test` and `Test_trans` netlists. Leak closed. Bridge clean throughout.
- [x] Python 996 → 1028 (+32: 30 gmin_bump + 4 pre_run_script extensions). SKILL Tier-1 unchanged (pure-Python feature + safer renderer).

### Phase 3A v1.8 — DONE 2026-05-18 EOD (DECISIONS #66)

- [x] **`trans_pss_ic` strategy implementation** — landed as on-failure variant of v1.3 `ic_from` (DECISIONS #57's pre-run-script mechanism reused verbatim). The deferred `asiChangeAnalysis` probe (DECISIONS #52) is now moot: `additionalArgs`-based path covers the IC injection need without any new asi* surface. `StrategyContext` extended with optional `history_by_item` + `pvtproject_path` fields; `_run_strategy_chain` plumbs them through. Safe-write shape (`baseline_value=""`) inherits v1.7 A6 fix. Live-verified on fnxSession0 (TT_pvt_3 → simkit_verify/5/Test/netlist/spectre.ic, script + restoration clean). +28 tests. DECISIONS #66 records D1-D6 design + alternatives rejected + two probe-environment learnings.

### Phase 3A v1.9 — IN PROGRESS

- [x] **v1.2 #2: `pvt corners push --replace`** — DONE 2026-05-18 EOD (DECISIONS #67). Opt-in flag, default kept as ADD for back-compat. SKILL `pvtCornersPush` gains `?replace nil`; enumerates live corners, drops names not in sidecar via `axlGetCorner` + `axlRemoveElement`, then pushes. Wipe-protection guard catches 0-valid-rows accident. Live-verified on fnxSession0 (7-step probe end-to-end clean: ADD ✓ / REPLACE ✓ / empty rows refusal ✓ / no-row_name refusal at new gate ✓ / dry-run+replace 0-mutation ✓ / baseline restored ✓). +6 Python tests + 6 SKILL Tier-1 cases. Python 1097 → 1103.
- [x] **Auto-probe `baseline_value` from asi at strategy startup** — DONE 2026-05-18 EOD (DECISIONS #68). Closes v1.7 D4 deferral. New SKILL `pvtRunnerGetSimOptionVal(sess, testName, optionKey)` + Python `pvt_runner_get_sim_option_val(...) -> Optional[str]` wrapper. gmin_bump auto-probes the live asi when sidecar doesn't pass `baseline_value`; resolution order explicit > probe > default, source tag in notes. Back-compat with mock bridges via `getattr` lookup. Live-verified on fnxSession0. +12 Python tests + 8 SKILL Tier-1 cases. Python 1103 → 1115. Bonus: side finding confirms v1.3 gap #1 is real (stale additionalArgs on Test/Test_trans).

### Phase 3A v1.9 candidates (remaining):
- [x] ~~**run.json `history_name: None` fix**~~ — **PHANTOM, closed via display fix 2026-05-18 EOD (DECISIONS #64).** SKILL write side was already correct since v1.5 PM (`d["run"]["history_name"]` populated for every run); the "None" report was a `dict.get()` misread against the top-level envelope (5 keys: schema_version/run/results/artifacts/output_specs — no top-level history_name). DB column was also already correct. Real gap was the `pvt list` table not showing a `history` column at all; added in `cli/list_runs.py`, 2 new tests. Python 1028 → 1030.
- [x] ~~**v1.8 #4: `pvt star` — sync DB starred runs to Maestro history locks (DECISIONS #65)**~~ — DONE 2026-05-18 EOD. Schema v2→v3 adds `runs.starred BOOLEAN DEFAULT FALSE`. New verbs: `pvt star <run_id>` / `pvt unstar <run_id>` / `pvt sync-stars push|pull [--dry-run]`. `pvt list` gets a `★` column and `--starred-only` filter. Bridge wrappers `pvt_runner_set_history_lock` (mae*) + `pvt_runner_get_history_lock_map` (axl*). Live-verified end-to-end on `fnxSession0`: star → bridge read-back lock=T → unstar → bridge read-back lock=nil; 3 pre-existing user locks (simkit_verify / simkit_simerr / simkit_Rtime_err) untouched throughout. Python 1030 → 1069 (+39). SKILL Tier-1 unchanged. Future hook for the day `pvt forget` lands: default-block deletion of starred runs.

- [x] ~~**v1.3 known-gap #1: capture + restore prior `additionalArgs` simoption**~~ — DONE 2026-05-19 (DECISIONS #69 Gap #1). Both `_execute_ic_chained_item` and `TransPssIc.apply` snapshot via v1.9 #2's `pvt_runner_get_sim_option_val(test, "additionalArgs", session=session)` BEFORE installing the pre-run script; `finally` writes each captured value back per-test via `pvt_runner_clear_ic_source(tname, mode, prev, session=session)` instead of the v1.3 unconditional clear-to-`""` on a single `test_for_ic`. `_NO_SNAPSHOT` sentinel + `None` branches both fall back to clear-to-`""` for safety; `getattr` back-compat for pre-v1.9 mock bridges. Live-verified on `fnxSession0`: pre-probe → snapshot → sentinel write → restore → post-probe byte-identical for both `Test` and `Test_trans`. +6 orchestrator + 6 strategy tests.
- [x] ~~**v1.3 known-gap #2: per-test pre-run scripts**~~ — DONE 2026-05-19 (DECISIONS #69 Gap #2, **latent infra only**). New `PreRunSpec.per_test_corner_to_arg: Mapping[str, Mapping[str, str]] | None` field + `write_per_test_pre_run_scripts(spec, tests, workdir) -> dict[str, Path]` helper. Per-test maps override top-level fallback; tagged `item_name` as `<item>__<test>` when maps diverge so content-hashed filenames differ. Orchestrator stays on shared-map shape — opting it in is a future PR when a real multi-test consumer with divergent ICs appears. +13 tests.
- [x] ~~**v1.3 known-gap #3: 3-item chain dogfood**~~ — DONE 2026-05-19 (DECISIONS #69 Gap #3, **offline pinned**). `tests/test_orchestrator_ic_chain_3item.py` (322 LOC, 7 cases across 3 classes): mock bridge + `tmp_path` fixture mimics `<dbRoot>/<history>/<idx>/<test>/netlist/spectre.ic` for A → B (ic_from A) → C (ic_from B). Validates `history_by_item` propagation, per-corner IC resolution at each link, batch fallback when upstream history missing. Live 3-item dogfood still owed when a real review case appears.
- [x] ~~**runTests.il atomic loader fix**~~ — DONE 2026-05-19 (DECISIONS #69 item 6). Replaced single-fallback `defvar` block with 4-layer resolution chain: (a) `_PVT_FORCE_ROOT` SKILL global override, (b) `PVT_{TEST,SKILL}_ROOT` env vars (now empty-string-safe via `_pvtEnvOrNil` — fixed latent `(or "" X)` bug), (c) upward walk from cwd for marker `skill/tests/runTests.il` + `getSkillPath()` scan, (d) original cwd-based default. Plus load-time printf + actionable warning when `pvtError.il` not found. Tier-1 460/2 from both `cwd=simkit` and `cwd=/tmp` — load-path independence verified. SKILL probe confirmed `getCurrentLoadingFile` does NOT exist in IC6.1.8 (5 candidates checked); `getAllLoadedFiles` only records post-load, can't help during in-flight load.
- [x] ~~**Audit other tests for `mock.patch.dict(sys.modules, {...: None})` anti-pattern**~~ — DONE 2026-05-19 (DECISIONS #69 item 5). 5 greps over `tests/` (`mock.patch.dict`, `sys.modules[`, `patch.dict(sys.modules`, `monkeypatch.setitem(sys.modules`, `sys.modules`) all return **zero hits**. Audit-clean.
- [x] ~~**Per-attempt corner-enable tracer log**~~ — DONE 2026-05-19 (DECISIONS #69 item 4, env-gated). `_trace()` helper in `naive_retry.py`, imported by `gmin_bump.py` + `trans_pss_ic.py`. `SIMKIT_TRACE=1` (literal `"1"` only) emits one stdout line per `.apply()`: `[trace] <strategy> attempt #N: targeted=[…] remaining_before=[…]`. Default OFF, zero production noise. +6 tests.

### Phase 3A v1.9 #4 candidates (newly surfaced):
- [x] ~~**2 pre-existing SKILL Tier-1 FAILs**~~ — RESOLVED 2026-05-19 (DECISIONS #70). Not pre-existing: the count was a `LiteralRemoteFunction` readback proxy mis-read (`ws[name]` doesn't read SKILL var values; must `ws['evalstring'](name)`). After correct readback: (a) `corners/collectRowNames/skips-missing-row_name` was a real v1.9 #1 fixture regression (`'unbound` symbol collides with SKILL "variable unbound" sentinel); fixed 7× → `PVT_JSON_ABSENT` in `testPvtCorners.il`. (b) `dialog/fresh path under existing dir accepted` is a `/tmp/dialog_*` cleanup race, not a code bug; pre-run `rm -rf /tmp/dialog_*` clears it. Clean baseline now **462/0/0**.
- [x] ~~**runTests.il simplification via `get_filename(piport)`**~~ — DONE 2026-05-19 (DECISIONS #71, Phase 3A closeout). Replaced Agent B's 4-layer fallback with 1 primary path (canonical SKILL `get_filename(piport)` — same idiom user's own `skill_tools.il:102` uses) + 2 fallbacks (env vars, cwd default). Removed: `_pvtFindRepoRoot` upward walk helper, `_PVT_FORCE_ROOT` global, `getSkillPath` scan. Net `-37 LOC` (80 → 43). Probe-verified: `get_filename(piport)` returns the absolute path of the file being loaded via skillbridge `ws['load']`; cwd-independent. Live-verified 462/0/0 from both `cwd=simkit` and `cwd=/tmp`.

### Phase 3A deferred items (with explicit "wait for X" triggers):
- [ ] **Probe oddity from Gap #1 live verify** — `Test`'s sentinel write via `pvt_runner_set_ic_source` didn't visibly land on read-back while `Test_trans`'s did. Restore round-trip was still byte-identical for both (Gap #1 contract held). Suspected: `Set` short-circuits on similar values, or asi caching on main session interacts with per-test setter. **Trigger**: another mysterious sentinel-not-landing case OR free time on a SKILL day.
- [ ] **Orchestrator opt-in to per-test pre-run scripts** — Gap #2 landed the data shape + helper (`PreRunSpec.per_test_corner_to_arg` + `write_per_test_pre_run_scripts`). Switch `_execute_ic_chained_item` to use them when set. **Trigger**: a real multi-test consumer item with divergent per-test ICs.
- [ ] **Dialog test fixture race-proof** — `dialog/*` tests should clean their own `/tmp/dialog_*` in setUp/tearDown. **Trigger**: a second occurrence of the cleanup race biting somebody.

---

## 🎯 Phase 3A — CLOSED 2026-05-19

All three architectural pillars (Data/Define/Execute) shipped and dogfooded. No active phase — observing real usage to surface true next pain points before picking the next phase. See `PHASE_PLAN.md` for parked candidates and `PROJECT_STATE.md` for the full closeout summary.

### Deferred to Phase 3A v1.3 (do NOT block v1.2):

- **`gmin_bump` strategy** — needs targeted `asi*` probe: `asiAddSimOption` signature + how to scope to single-corner / single-test override + how to revert.
- **`trans_pss_ic` strategy** — needs `asiChangeAnalysis` / PSS-analysis-form probe: where IC field lives, how to point at a `spectre.fc` from a precursor trans, parameterizable trans duration + IC mapping.
- **Per-test bundle dict** — `bundle: {"test_a": "a.measure.json", "test_b": "b.measure.json"}` form. Promote when a real case appears.
- **Item dependency graph** — `depends_on: [item_name]`. Promote when "干扰仿真 must wait for PSS pass" type workflow surfaces.
- **Multi-Maestro orchestration** — one review driving N parallel Maestro sessions.
- **Report generator** — PDF/HTML with waveform PNG attachments. Its own phase per `PHASE_PLAN.md`.

---

## Phase 4 — GUI / Adoption Layer (KICKED OFF 2026-05-19)

**Goal:** PyQt5 desktop GUI as the **first real user entry point** for simkit. User (analog IC designer) has never used the `pvt` CLI; Tier-1 must enable one complete signoff cycle entirely inside the GUI. Spec at `docs/phase4_gui_spec.md` (DECISIONS #73).

### §1. Specification (no code yet — pure documentation)

- [x] `docs/phase4_gui_spec.md` — 22-section design contract (problem statement, locked decisions, Tier-1/Tier-2 scope, layout, module session lifecycle, bridge layer, subprocess layer, diff workflow, corner editor, measure editor, run progress, wizard, milestone tagging, status strip, CLI entry, tests, deployment, open questions, impl order). 558 lines. DECISIONS #73.
- [x] User workflow learnings captured in DECISIONS #73 (never-used-CLI, multi-module DR cycle, PDR/CDR/FDR gates, diff-is-core, partial cross-module restore).
- [x] Two parallel design-review agents (architecture + UX) ran before spec writing; all 10 recommendations absorbed into §2.1 mandates (A1-A5, B1-B5).
- [x] 6 ASCII-mockup open decisions + 2 deeper workflow questions all answered before spec writing.

### §2. Deployment pipeline integration (PyQt5 deps) — DONE 2026-05-19 PM (DECISIONS #74)

- [x] Add `PyQt5==5.15.9` + `pytest-qt==4.5.0` + `QtAwesome==1.4.2` to `requirements.txt`.
- [x] Re-freeze `requirements.lock.txt` via `pip freeze --all` in clean venv. `duckdb` pinned 1.5.2 → 1.2.2 (glibc 2.17 baseline forever-rule).
- [x] `download_wheels.py` `DEFAULT_PLATFORMS` tightened to manylinux2014/2_17 only (drops 2_28 which would silently break red).
- [x] CRLF defense (3 layers): `.gitattributes` + `make_payload.py` pack-time normalize + `unpack_payload.sh` post-extract sed-strip.
- [x] `--no-wheels` code-only payload mode (`make_payload.py --no-wheels` → `unpack_payload.sh` auto-copies wheels from `<deploys>/current/vendor/wheels/`).
- [x] `deploy_venv.sh` patches `.venv/bin/activate` + `activate.csh` with `LD_LIBRARY_PATH` prepend for the wheel's bundled Qt5 (Cadence's `/software/public/qt/5.15.3_xcb/lib` shadows it otherwise).
- [x] `scripts/README.md` updated with all of the above; `scripts/PHASE4_DEPS_HANDOFF.md` is now redundant — pending deletion.
- [x] Live-verified on red zone: `bash scripts/deploy_venv.sh` clean with 5 smoke tests (duckdb / skillbridge / simkit / `pvt` / PyQt5).

### §3. App skeleton (architecture proves out) — DONE 2026-05-19 PM (DECISIONS #74)

- [x] `python/simkit/gui/` package created with `__init__.py` + `app.py` (`main()` entry).
- [x] `pvt gui` subcommand in `python/simkit/cli/gui.py` → calls `simkit.gui.app.main()`.
- [x] `MainWindow` with top bar (module selector placeholder), status strip placeholder, left tree placeholder, right panel placeholder, bottom log.
- [x] `BridgeWorker` singleton on dedicated QThread per spec §8 (A1 + A5). Heartbeat every 10s; status dot wired to top bar.
- [x] `ModuleSession` dataclass + serializer per spec §7 (A4).
- [x] `~/.simkit/gui_app.json` global state + `.simkit/gui_state.json` per-module state read/write.
- [x] Unit tests for ModuleSession + BridgeWorker (mock bridge) + state persistence round-trip — **37 / 37 green**.
- [x] `app.py` `except ImportError` split: `ModuleNotFoundError` → exit 4 with install hint; `ImportError` → exit 5 with real error + LD_LIBRARY_PATH guidance.
- [x] **Verification gate (effectively met 2026-05-19 PM):** `pvt gui --safe-mode` launched an empty window with bridge status dot on red zone; user closed it. Director-mode signal = pass. Other §3 acceptance items (bridge dot color matches Virtuoso state / `~/.simkit/gui_app.json` writes on close / restart restores module) NOT explicitly validated by user — moved on.

**Outstanding bug — DONE 2026-05-19 PM late (DECISIONS #76):**

- [x] **BridgeWorker timer cross-thread shutdown warning** — Root cause turned out deeper than the warning suggested: `worker.run` (blocking `Queue.get()` loop) and `worker.start_heartbeat` were BOTH connected to `thread.started`, but the first slot blocked the worker thread's event loop forever — so the heartbeat timer NEVER actually fired during runtime (spec §8.2 silently unfulfilled). The "timer destroyed on wrong thread" warning was just the visible symptom on shutdown, when `start_heartbeat` finally fired during the queue-drain. Refactored to Qt-native signal-based dispatch: `_op_queued` signal (queue_op → _dispatch via QueuedConnection) replaces `queue.Queue`; `_stop_requested` signal (stop → _cleanup via QueuedConnection) replaces sentinel + `_stopped` flag; `initialize` slot (connected to `thread.started`) creates the heartbeat QTimer on the worker thread; `_cleanup` slot stops the timer on the worker thread before destruction. Plus: added module-level `simkit.skill_bridge.evalstring()` helper so the heartbeat probe can actually call SKILL `t`. Live-verified on home Linux `.venv` with running Virtuoso: heartbeat ticks fire on the worker thread, status transitions to GREEN immediately on first successful tick, zero `QObject::killTimer` warnings on clean shutdown. Unit tests refactored: 13 → 16 (3 new `CleanupTests` cases). Full suite 1190 → 1193 green.

### §4. View results path — Stage 2 DONE 2026-05-19 PM late (agent A; DECISIONS #77)

- [ ] Left tree: Reviews / Milestones / History groups with proper data binding. **— Stage 3 (needs module loading + project context)**
- [x] Results tab: `ResultsModel(QAbstractTableModel)` + `QSortFilterProxyModel` per spec §10/A3 (`python/simkit/gui/results_model.py` + `gui/views/results_tab.py`).
- [x] Review header bar with "Run this review" button (B2) per spec §6.1 (`run_requested = pyqtSignal(str)`; MainWindow handler is log-only stub for Stage 2, BridgeWorker wire happens in Stage 3 alongside §5).
- [x] Results table: corner × test × measure × pass/fail/spec/spec_status columns (7-col model with `value` merging via `simkit.from_db._merge_value`).
- [x] Failed-corner highlight via `BackgroundRole` (`QBrush(QColor(255,220,220))` when `status==fail` OR `spec_status in {fail, eval_err}`).
- [x] Widget tests (pytest-qt) for table rendering (16 model tests + 10 tab tests; `:memory:` DuckDB fixtures).

### §5. Run path (subprocess + JSONL progress)

- [ ] `pvt run --gui-jsonl` CLI flag (additive) — emits structured progress events per spec §9.2.
- [ ] `GuiEventEmitter` hooked into `_run_strategy_chain` + post-PvtSave.
- [ ] `RunController` (Python class) spawns QProcess, parses JSONL events, emits Qt signals.
- [ ] Run progress UI per spec §13 (items kanban + text log streaming to bottom panel).
- [ ] Cancel semantics per spec §9.3 (SIGTERM + 5s grace + SIGKILL + partial_run DB flag).
- [ ] DuckDB schema migration: `runs.partial_run BOOLEAN DEFAULT FALSE`.
- [ ] Live-verified end-to-end on fnxSession0: pick review → click Run → progress UI updates → results populate when done → cancel mid-run leaves partial state correctly tagged.

### §6. Diff path

- [ ] `DiffResultsModel(QAbstractTableModel)` wraps two run_ids per spec §10.3.
- [ ] "Compare" button on every run row in History + Results header (B3).
- [ ] Run-picker dialog (filterable list) for compare-against selection.
- [ ] Baseline pin in Results header; per-module sticky.
- [ ] Diff view = new right-panel tab with sub-tabs (Spec delta / Netlist delta / Spec-string delta).
- [ ] Color coding: pass→fail red, fail→pass green, value-change-only yellow, unchanged grey.
- [ ] "Show only changed" filter via QSortFilterProxyModel.

### §7. Corner editor — Stage 2 DONE 2026-05-19 PM late (agent B; DECISIONS #77)

- [x] Corners tab content: table editor with add-row / duplicate-row / per-row enable checkbox / per-cell dropdown (B4) (`gui/views/corners_editor.py` + 19 pytest-qt tests).
- [x] "Pull from Maestro" / "Send to Maestro" buttons with last-sync timestamp.
- [x] Live-vs-sidecar divergence yellow strip (shown via `set_divergence(live_count, sidecar_count)`; 3 follow-up signals: `show_diff` / `pull_overrides_sidecar` / `keep_sidecar`).
- [x] Live validation (cell highlight on invalid; `validation_errors()` returns list, gates Send-button).
- [ ] Push via BridgeWorker → `pvt_corners_push --replace` **— Stage 3 (currently log-only stub)**.
- [ ] union↔flat row-shape adapter **— Stage 3 (editor uses flat dicts; real `UnionRow` has `vars: dict[str, tuple]` + `models`)**.
- [ ] model-file path existence validation **— Stage 3 (needs project root context)**.

### §8. Measure bundle editor — Stage 2 DONE 2026-05-19 PM late (agent C; DECISIONS #77)

- [x] Measures tab content: split pane (edit / live render preview right sidebar) (`gui/views/measures_editor.py` + 18 pytest-qt tests).
- [x] Template picker + signal-group picker + param entry per spec §12 (`set_available_templates(list[str] | dict[str, Template])` dual-form contract).
- [x] Live render preview re-renders on edit; show render errors inline (in-memory `MeasureBundle` construction + `render_bundle()`; bypasses file round-trip for keystroke-speed feedback).
- [x] Apply to Maestro disabled while render shows errors (`apply_requested = pyqtSignal(object)` carries `list[RenderedRow]`, fired only when render is clean).
- [ ] Real BridgeWorker `pvt_measure_apply` dispatch **— Stage 3 (currently log-only stub)**.

### §9. Milestone + status strip

- [x] DuckDB schema migration: `runs.milestone VARCHAR DEFAULT NULL`. Schema v3 → v4. **— DONE 2026-05-19 PM late (agent D; DECISIONS #77; also adds `runs.partial_run BOOLEAN DEFAULT FALSE` for §5 cancel semantics)**.
- [ ] Right-click run → "Set milestone…" with autocomplete from existing milestone strings.
- [ ] Milestone-set triggers star+lock round-trip via BridgeWorker.
- [ ] Left-tree Milestones group with counters + filter behavior.
- [ ] Top-bar status strip per spec §16: 30s DuckDB query, FAIL chips with click-to-jump.

### §10. Review wizard + copy-edit

- [ ] "Copy as…" right-click action: form editor pre-populated from existing review.
- [ ] Wizard: step 1 project/name, step 2 items (multi-select tests via bridge, file pickers for union/bundle), step 3 failure handling, step 4 review/save.
- [ ] `simkit.review.validate` run before write; errors inline.

### §11. Error translation + polish

- [ ] `simkit/gui/error_translation.py` with curated known-error table per spec §8.3.
- [ ] "Details" disclosure for raw error text.
- [ ] Unknown errors fall through with "Report this" link.

### §12. Dogfood acceptance gate

- [ ] User completes one real signoff cycle (e.g. NDIV PDR or CDR pass) entirely inside the GUI on red zone.
- [ ] Until this gate clears, Tier-2 work (cross-module dashboard, charts, etc.) is locked.

### Phase 4 open decisions (spec §20 parking lot — pick up when they bite):

- [ ] Wizard testbench-list source for offline drafting (current Tier-1: live bridge fetch).
- [ ] Multi-monitor / theme / i18n (locale files for translation table).
- [ ] Keyboard shortcuts default set + help dialog.
- [ ] Offline-Virtuoso read-only mode details.
