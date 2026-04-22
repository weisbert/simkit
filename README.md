# simkit

Unified simulation data collection and automation for Cadence Virtuoso / Maestro.

## What this is

A long-running, incremental project to build an "efficiency amplifier + data backbone" layer on top of Cadence Maestro — not replacing it. TB construction stays in Maestro/ADE-XL where circuit engineers expect it.

## Three pillars (weak coupling)

1. **Authoring helpers** — reduce repetitive Maestro clicks (corners, formulas, batch edits, design-ref updates)
2. **Simulation orchestrator** — batch runs, non-convergence retry, standard review suites
3. **Data layer** — structured, queryable, versionable simulation results  ← **Phase 1 focuses here**

Each pillar is built and validated before the next. No grand unification.

## Where to start reading

- **Fresh Claude session**: read `docs/session_bootstrap.md` first.
- **Human collaborator**: read `PROJECT_STATE.md` for current status, then `DECISIONS.md` for the "why."

## Repo map

| File/dir | Purpose |
|---|---|
| `PROJECT_STATE.md` | Current phase, recent decisions, next steps — updated often |
| `DECISIONS.md` | Architectural decision log (append-mostly) |
| `TODO.md` | Current-phase task list with checkboxes |
| `PHASE_PLAN.md` | Future phases and parked ideas |
| `docs/` | Schema spec, conventions, session bootstrap |
| `skill/` | SKILL code (runs inside Virtuoso) |
| `python/` | Python code (CLI, ingester) |
| `config/` | Example config files (`.pvtproject` etc.) |
| `tests/` | Unit tests + end-to-end validation |
| `examples/` | Minimal working examples |

## Hard constraints

- **Offline-deployable**: no runtime internet, no pip-on-demand. Dependencies must be vendored for offline install into the red zone.
- **Target**: Cadence ICADVM18.1-64b, Python 3.11.4.
- Works in isolated-network environments where Claude Code is unavailable.
