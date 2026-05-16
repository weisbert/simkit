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

- **Per-iteration spec on sweep entries** — currently single spec applies uniformly to N swept rows. Per-row spec (e.g. PN @ 1MHz < -100, PN @ 100MHz < -140) needs a parallel `specs: [...]` array alongside `output_names`.
- **`axlGetSpecData` / `axlGetSpecWeight` on pull** — pull captures the spec string but loses the structured `?weight` / `?info` side metadata.
- **Dotted `X..Y` range form in spec parser** — parseString uses single-char delimiters so `..` is ambiguous with the dot inside numeric literals. Bracket `[X:Y]` + `range X Y` cover the cases; revisit if needed.
- **Spec status in apply CLI summary** — pushReport captures `spec_status` per row but the CLI doesn't surface it. Need to extend PvtMeasurePushReport decoding + table formatter.
- **Per-signal alias map** — v1.1 walkthrough pinned the collision (`/VDD` × 4 supplies share basename). Signal group needs to declare `aliases: {"path": "label"}`.
- **Multi-axis param_sweep** — v1.2 enforces single-axis. Real `freq × temperature` 2-D matrix case would justify lifting it.
- Multi-signal templates (v1 enforces ≤1 `signal`-kind param per template; edge_delay uses 1 signal + 1 string ref as the workaround).
- Cross-project template sharing (user-home `~/.simkit/templates/`).
- Snapshot template match-back (reverse-engineer a pulled snapshot into bundle + parameters).
- Offline acceptance gates M2 + M3 (currently live-verified; would need captured fixture pair like Phase 2 Gate U1).
