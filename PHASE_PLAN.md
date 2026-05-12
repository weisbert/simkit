# Phase Plan

Future phases and parked ideas. Intentionally vague — details decided when each phase begins.

This file is a **scratch pad for ideas that are NOT Phase 1**. When an idea pops up mid-work that doesn't belong in the current phase, drop it here in a sentence or two. It keeps `TODO.md` honest (current phase only) and guarantees ideas don't get lost.

---

## Phase 2: PVT-Union Builder — PROMOTED to active phase 2026-05-12

Picked from the candidate list below; see `TODO.md` for current task state and `docs/phase2_pvt_union_spec.md` for the locked §1 spec. Motivating case (VCO LO 2026-05-11, 21 columns × 3 points = 63 corners) is the §6 Gate U2 acceptance target. The 21× explosion is purely an authoring-layer problem — the tool lets the user *describe* the union once (sidecar JSON) and emit the 63 corners mechanically.

**Candidates NOT picked this phase** (kept here for the next phase boundary):

- **Formula templates** (pains 1.b, 1.c) — library of reusable measurement formulas (rise time, dutyCycle, etc.), apply across many signals at once.
- **Design-ref bulk update** (pain 1.f) — when bumping Maestro copy/version, update all tests' `design` pointers in one shot.

**Principle:** one helper, end-to-end, with a real user (you) validating. Resist doing all three at once.

---

## Phase 3 (tentative): simulation orchestrator (bite-sized first cut)

Not "batch everything." First cut:

- A YAML-driven "review suite" definition (which tests, which corners, which sims to run)
- Python runner that invokes Maestro via socket/CLI, monitors progress, merges non-convergence retries
- Auto-ingests results to data layer (leverages Phase 1)

Only after Phase 2's helper is in daily use.

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
