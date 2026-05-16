# Phase Plan

Future phases and parked ideas. Intentionally vague — details decided when each phase begins.

This file is a **scratch pad for ideas that are NOT Phase 1**. When an idea pops up mid-work that doesn't belong in the current phase, drop it here in a sentence or two. It keeps `TODO.md` honest (current phase only) and guarantees ideas don't get lost.

---

## Phase 2: PVT-Union Builder — DONE 2026-05-13

Picked 2026-05-12 from the original Phase 3 candidate list; closed across §1–§6 in `TODO.md` with all four §6 gates offline-pinned. See `docs/phase2_pvt_union_spec.md` for the locked §1 spec. Motivating case (VCO LO 2026-05-11, 21 columns × 3 points = 63 corners) became the §6 Gate U2 acceptance fixture.

## Phase 3B: Formula-Template Authoring — DONE 2026-05-16 (skeleton + v1.1 builtins + v1.2 expressiveness + v1.3 specs + v1.4 spec-capture round-trip)

Picked from the candidate list below; see `TODO.md` for the breakdown and `docs/phase3b_measure_template_spec.md` for the locked §1 spec. **Goal achieved:** completed the **Define** layer of the system architecture by giving the user a way to declare "what to measure" with the same authoring economics Phase 2 gave them for "what conditions to measure under" (PVT unions). Skeleton landed 2026-05-14; v1.1 same-day added a 17-template builtins library reverse-engineered from sim_DCOBUF + `pvt measure install-builtins` CLI + walkthrough fixture. v1.2 (2026-05-15) closed 6 friction items surfaced by the live fnxSession0 dogfood: output_name override, raw_expression entry kind, single-axis param_sweep, 4 `_full` rise/fall builtins (naming precedent over CLIP parameter), implicit signal_group:null, list-bundles error display. v1.3 (2026-05-15) closed the silent pass/fail spec gap — bundle apply entries now carry an optional Cadence-native spec string; SKILL push parses + dispatches to `axlAddSpecToOutput`. Library 17 → 21. Schema v1 → v2 (v1 still loads). 678/678 Python tests + 376/1 SKILL Tier-1. Dogfood proof: live fnxSession0 11-row Outputs round-trip in one bundle, now with pass/fail specs.

Rationale for going B before A (sim orchestrator): Phase 3A explicitly waits on a stable Define layer per this file's earlier note; running the orchestrator with measurements still hand-edited in Maestro Calculator just batches the wrong configuration. See DECISIONS #38 for the longer rationale.

**Candidates NOT picked this phase** (kept here for the next phase boundary):

- **Sim orchestrator** (pain: manually clicking N corner × M test combinations in Maestro) — was the original "Phase 3 (tentative)" candidate. Promote next, once P3B is in daily use.
- **Design-ref bulk update** (pain 1.f) — when bumping Maestro copy/version, update all tests' `design` pointers in one shot.
- **Report generator** — auto-PDF (number tables + waveform PNGs + netlist diff vs prior slice) over a slice from Phase 1's data layer.
- **Auto-hook on Maestro sim completion** — eliminate the manual `PvtSave` call.
- **Standard TB generator** — parameterized symbol gen + standard skeleton + heavy/lite variant switching.

**Principle:** one helper, end-to-end, with a real user (you) validating. Resist doing all three at once.

---

## Phase 3A: simulation orchestrator (IN PROGRESS — §1 spec DONE 2026-05-16)

**§1 spec frozen** at `docs/phase3a_orchestrator_spec.md`. DECISIONS #50-53. Open questions below resolved during the §1 spec push (see DECISIONS); leaving the original list here for historical context:

- **Driver style:** skillbridge (existing infra, no subprocess) vs. `virtuoso -nograph -replay` (fresh VM per run, isolation but slow startup). The bridge has been the Phase 1 / 2 / 3B-skeleton workhorse and just works; subprocess style is an option not a default.
- **Review-suite sidecar shape:** YAML or JSON? Project convention is JSON sidecars (`.pvtproject`, `.union.json`, `.measure.json`, `.siggroup.json`, `.template.json`). YAML would be the first divergence — needs a reason or default to JSON for consistency.
- **Trigger surface:** does the orchestrator iterate test×corner×sim combinations itself, or hand each combination to Maestro via the existing axlSKILL run-control API? Live skillbridge probe needed: `axlRun` / `axlSubmitJobs` / `axlSetMaestroRun` etc. — which exist, which actually drive runs.
- **Failure semantics:** non-convergence retry policy (skip / retry-N / abort?); whether a single corner failure halts the suite or just marks that corner failed and continues.
- **Auto-ingest hookup:** Phase 1 `pvt ingest` happens against a dump dir. Does the orchestrator dump per-run JSON between Maestro calls (current `PvtSave` style) or batch-dump at suite end?

**Pre-existing assets that Phase 3A can lean on:**

- Phase 1 data pillar — full ingest + query + diff + label CLI surface.
- Phase 2 PVT-union loader + explode — already produces the exact (var, model, sub-corner) tuples a suite would iterate.
- Phase 3B measure-bundle render — produces the exact Outputs table the suite needs to push pre-run.
- Skillbridge wrapper (`python/simkit/skill_bridge.py`) — `pvt_corners_pull/push`, `pvt_measure_push/pull/restore` are the existing drive-Maestro surface; orchestrator probably extends it with a `pvt_run_test` helper.

**v1.4 cleared the Phase 3A hard prerequisite**: pass/fail capture is live end-to-end. Phase 3A can start clean.

**Bite-sized v1 cut (the seed; spec will refine):**

- A sidecar-driven "review suite" definition (which tests, which unions, which bundles).
- Python runner that iterates the suite, drives Maestro per (test, corner), monitors progress, handles non-convergence per policy.
- Auto-ingests each completed run via the existing `pvt ingest` path.

## Phase 3B v1.5 (in-place, remaining candidates)

Items deferred from v1.4 or surfaced during it (top is highest priority):

1. **Spec weight pull + push round-trip** — v1.4 #3 scope-shrunk after live probe (DECISIONS #46): weight IS readable via `axlGetSpecWeight(int_handle)`, info is NOT. User confirmed they don't touch weights in practice (all default to 1.0). Promote when a real "this spec is more important" workflow surfaces.
2. ~~**`?min`/`?max` vs `>=`/`<=` semantic alignment**~~ — **DONE 2026-05-16** (DECISIONS #47). Push mapping changed to `?range X 1e30` / `?range -1e30 X` with `_PVT_MEASURE_SPEC_HUGE = 1e30` sentinel; parser tags renamed `min`/`max` → `ge`/`le` to match Python convention. Cadence stores as `"range X 1e+30"` / `"range -1e+30 X"`; spec_eval evaluates correctly.
3. ~~**Per-iteration spec on sweep entries**~~ — **DONE 2026-05-16** (DECISIONS #48). Parallel `specs: [...]` array on swept entries; mutex with uniform `spec`; null entries == no spec on that row. Renderer plumbs through `RenderedRow.spec`.
4. ~~**Per-signal alias map**~~ — **DONE 2026-05-16** (DECISIONS #49). `signal_group_schema_version: 2` accepts `{net, alias}` objects in `signals[]`; alias replaces basename in rendered output names. Solves the dco2g_supplies /VDD×4 collision case. v1 bare-string form unaffected.
5. **Multi-axis param_sweep** — v1.2 enforces single-axis; promote when a real "freq × temperature" 2-D case appears.
6. **`tolerance` spec eval** — spec_eval currently marks `tolerance X ()` as unsupported because the target lives in axlSKILL side metadata with no read accessor. If we accept a `tolerance X T` form (with explicit target), the verdict becomes computable.
7. **Spec orphan cleanup API** — `axlDelSpecFromOutput` doesn't exist; live probes leave orphan spec records that persist until Cadence restart. Workaround: build the cleanup via output-delete + re-add. Useful for test harnesses.

---

## Parked ideas (raw — flesh out when they're picked up)

### Standard TB generator
User-proposed 2026-04-22. Scope:
- Parameterized symbol generator (dreg ctrl, PKG-with-trace, LDO, …)
- Standard TB skeleton assembly from components
- Heavy/lite TB variant switching (couples into `testbench_id` in the data layer)

Not Phase 2 by default; promote only if it wins the priority call. If built: symbol gen → skeleton → variant switching. Also recorded in `~/.claude/.../memory/project_standard_tb_generator.md`.

### Auto-hook on Maestro sim completion
Phase 1 uses manual `PvtSave`. An auto-hook would remove the manual step — investigate which Maestro/ADE-XL events expose a post-sim callback.

### Report generator
Auto-produce a review PDF from a slice: number tables + waveform PNGs + netlist diff vs. prior slice. Belongs near end of Phase 1 or start of Phase 2.

### Multi-user collaboration
Not near-term. Would require shared DB, conflict handling, user identity beyond the simple `author` field. Ignore until someone asks.

### Main-bench / sub-bench sync (user pain 2.a)
Schematic problem: when a main TB updates, spin-off minor TBs don't sync. No good solution yet. Parking this to revisit when Phase 2 clarifies the authoring layer.

### Waveform auto-annotation
Plot rise_time / dutyCycle / VDD / VSS automatically on saved waveforms. Likely a small SKILL utility built on top of the artifacts system.

---

## How to use this file

Three rules:
1. **Half-baked is fine.** A one-sentence idea is better than a missing one.
2. **Don't promote casually.** Moving an item to TODO.md means committing it to the current phase's scope — requires deliberate decision.
3. **Revisit before each phase boundary.** When Phase 1 nears completion, re-read this file and decide what Phase 2 actually is.
