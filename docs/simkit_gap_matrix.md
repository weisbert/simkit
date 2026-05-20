# simkit Gap Matrix — backtest of requirements vs. current capability

Backtest of two inputs against what simkit does **today**:
- `docs/rf_designer_review_scenarios.md` — 27 PDR/CDR/FDR scenarios (high-level requirements).
- `docs/simkit_new_user_friction.md` — 14 ranked new-user friction points.

Status legend: **OK** supported · **PARTIAL** mechanism exists but not reachable/complete · **MISSING** no mechanism · **BUG** defect confirmed in code.
Effort: **S** ≲1 day · **M** a few days · **L** structural.

---

## 1. Current capability inventory (verified this pass)

| Area | What exists today |
|---|---|
| Read history | DuckDB per project; `pvt list`; GUI History tree + Results tab (sortable `corner/test/output/value/status/spec/spec_status`). |
| Corners | `.union.json` corner sets; `pvt corners explode/list/diff/pull/push`; GUI Corners grid (add/dup/delete, dropdowns). |
| Measures | `.measure.json` bundles + templates + signal groups; `pvt measure render/apply/pull/diff`; GUI Measures editor w/ live preview. |
| Batch sim | `.review.json` (items = tests+union+bundle+on_failure); `pvt run`; GUI 4-step New Review wizard + copy-edit (just landed). |
| Specs | **Full pipeline**: bundle entry `spec` field → SKILL collector → `output_specs` in run.json (v2+) → `results.spec/spec_status` → `spec_eval.evaluate_spec` → `pvt diff --spec-changes`. |
| Regression | `pvt diff` two slices: matched rows + `only_a`/`only_b` (name-drift surfaced) + netlist diff + spec-verdict diff. GUI diff model. |
| Failure tracking | `results.status` enum; `failures.py` / `find_failed_corners`; schema v4 `runs.partial_run` flag. |
| Milestones | `runs.milestone` (schema v4); `pvt star`/`label`; GUI milestone tagging + tree group. |
| Bridge | Single SKILL socket, **single Maestro session** (one Session box). |
| Plotting | None. Results are tables only. |
| Monte Carlo | None. Union model is a PVT (process×voltage×temp) grid only. |

---

## 2. Confirmed bugs (fix first — small)

| ID | Bug | Evidence | Effort |
|---|---|---|---|
| **B-1** | CLI/GUI scan different bundle dirs. `pvt measure list-bundles`/`new-bundle` use `resolve_measurements_dir` → `measurementsDir` (default `measurements/`); GUI `loaders.py` hardcodes `bundles/`; 1AXX on disk uses `bundles/`. Same project, opposite answers. | friction #7; `cli/measure.py:803/878`, `gui/loaders.py:_BUNDLES_SUBDIR` | S |
| **B-2** | Corners "Send to Maestro" silently disabled. `validation_errors()` resolves a bare `model_file` (`rf018.scs`) against project root, fails, `_refresh_push_enabled()` disables the button — but the errors are never surfaced (no `setToolTip`, no error strip). | friction #1; `gui/views/corners_editor.py:350-411` | S |

---

## 3. Consolidated gap list (prioritised)

### P1 — core promise, blocks the most scenarios

| ID | Capability | Status | Gap | Serves | Effort |
|---|---|---|---|---|---|
| **G-1** | Specs are usable end-to-end | PARTIAL | Pipeline works, and the Measures edit dialog *has* a `spec` field — but nothing tells the user, the Results tab shows `no_spec` on every row with no path to fix it, and pre-spec data stays blank. Need: spec authoring visible in Measures, a "set spec" affordance from the Results table, and a hint when a run has zero specs. | friction #2; Part 0, PDR-5, CDR-6, FDR-5; pain-theme 1 | M |
| **G-2** | Plotting / curves | MISSING | Results tab is table-only. Every RF review artifact is a curve (gain/NF vs freq, compression). No waveform or XY plotting anywhere. | friction #4; PDR-1/2/4, CDR-4/5, FDR-2/4; nearly all | L |
| **G-3** | Margin / worst-case report | PARTIAL | Data is all in DuckDB; no canned "spec \| worst corner \| worst value \| margin \| verdict" rollup. Designers rebuild it by hand in Excel. | PDR-5, CDR-6, FDR-5; pain-theme 1 | M |

### P2 — high value, mechanism mostly present

| ID | Capability | Status | Gap | Serves | Effort |
|---|---|---|---|---|---|
| **G-4** | Convergence / partial-result surfacing | PARTIAL | `status` enum + `partial_run` exist; GUI shows a raw `status` cell (`eval_err` + `—`, no "why"). No run-level "N corners failed", no notification, no failed-only filter in the GUI tree. | friction #3-on-list; CDR-1/2, FDR-2, E-2/E-4; pain-theme 4 | M |
| **G-5** | Traceability of a result | PARTIAL | run.json copies the netlist (`input.scs`) + timestamp + `corner_vars`. Missing: model-file revision, PDK version, host. Can't prove a 6-week-old number's conditions. | FDR-5, E-3, E-5; pain-theme 3 | M |
| **G-6** | Cross-milestone trend | PARTIAL | `pvt diff` is pairwise. No 3-way PDR→CDR→FDR trend table/plot. | FDR-6 | S |
| **G-7** | Onboarding + vocabulary | MISSING | No glossary/tooltips for module/bridge/session/review/union/bundle/template/signal-group/raw/sweep; no cold-start landing. "Review" especially misreads as a meeting. | friction #3, #12; whole GUI | M |
| **G-8** | Wizard guardrails | PARTIAL | New Review wizard reaches Step 4 with an empty item (only blocked on Finish); confirmation is raw JSON; no live testbench-test picker (free-text test names, typos uncaught). | friction #6; Task-4 scenarios, spec §20.1 | S–M |
| **G-9** | Corner model coherence | PARTIAL | `vdd` column vs `extra_vars` free-text both hold supply; comma-list `tt,ss,ff` jammed in one process cell; exploded names (`TT_pvt_3`) shown with no definition. | friction #8, #9; PDR-3, CDR-1 | M |

### P3 — large or papercut

| ID | Capability | Status | Gap | Serves | Effort |
|---|---|---|---|---|---|
| **G-10** | Multi-Maestro / multi-Virtuoso | MISSING | Bridge is single-socket, single-session. No per-session status, no cross-process run aggregation, no batch-vs-interactive isolation, no crash-recovery resume (though `partial_run` is a seed for E-4). | Parts D & E (10 scenarios) | L |
| **G-11** | Monte Carlo | MISSING | Union is a PVT grid; no process+mismatch statistical sweep, no histogram/sigma/yield. | FDR-2 | L |
| **G-12** | Run naming | PARTIAL | Defaults to dev-junk (`orch_Test_basic_1779240708_1`); `--label` exists but new users won't know. Make the GUI prompt for a human label. | friction #11 | S |
| **G-13** | UI language consistency | BUG-ish | Mixed Chinese/English, sometimes one sentence. Pick one (or proper i18n). | friction #10 | S |
| **G-14** | Measures editor affordances | PARTIAL | Edit is double-click-only (no Edit button); raw OCEAN strings hand-editable with no guided "build a measurement"; cryptic `[raw]`/`[template]` glyphs. | friction in Task 3 | M |
| **G-15** | Bridge status legibility | PARTIAL | Amber dot + "Restart bridge" never explained; Pull/Run dead-end on a `缺少 session` modal. | friction #14; Task 2/4 tails | S |

---

## 4. Scenario coverage roll-up

| Part | Scenarios | Mostly-OK today | Blocked by gaps |
|---|---|---|---|
| 0 — review-ready TB | build spec | corner/measure/review authoring exists | G-1 (specs), G-9 (corner model) |
| A — PDR (5) | runnable end-to-end | batch run + history + diff | G-2 (plots), G-3 (margin), G-1 |
| B — CDR (6) | runnable | diff regression handles name-drift well | G-2, G-3, G-4, G-1 |
| C — FDR (6) | partly | milestones + diff | G-2, G-3, G-5 (traceability), G-6 (trend), G-11 (MC) |
| D — multi-Maestro/1-Virtuoso (5) | no | — | **G-10** (structural) |
| E — multi-Virtuoso (5) | no | per-project DB aggregates runs; `partial_run` seeds E-4 | **G-10** (structural) |

**Headline:** Parts A–C are *runnable today* — the gaps there are about turning raw tables into review-grade evidence (specs visible, margin rollup, plots). Parts D–E are *not addressed* — they need the single-session bridge to become multi-session aware (G-10), the single biggest structural item.

---

## 5. Suggested order

1. **B-1, B-2** — confirmed bugs, ~1 day total.
2. **G-1** — make specs visible/authorable; unlocks the auto pass/fail that is simkit's core promise.
3. **G-3 + G-4** — margin rollup + convergence surfacing; turns the Results table into review evidence.
4. **G-7, G-8, G-12, G-15** — the new-user cliff (vocabulary, wizard guardrails, naming, bridge legibility); cheap, high goodwill.
5. **G-2 (plots)** — large but unavoidable for RF; schedule deliberately.
6. **G-5, G-6, G-9, G-14** — traceability, trend, corner-model, measures polish.
7. **G-10 (multi-session), G-11 (Monte Carlo)** — structural; treat as their own mini-phases.
