# Project State

_Last updated: 2026-04-22_

## Current phase

**Phase 1: Data Pillar MVP** — entering implementation. Design closed, scaffold created.

## Goal of Phase 1 (one sentence)

End-to-end loop: "Maestro sim finishes" → "one command saves it" → "Python can query it and diff two slices."

## Recent timeline

- **2026-04-21 → 2026-04-22**: initial design conversation closed all architectural questions. See `DECISIONS.md` entries #1–#12.
- **2026-04-22**: project scaffold and git repo created; Phase 1 scope locked in `TODO.md`.

## What's DONE

- All architectural decisions for the data pillar (see `DECISIONS.md`)
- Phase 1 scope defined (see `TODO.md`)
- Project scaffold and git repo

## What's IN PROGRESS

_(nothing — waiting to start the first Phase 1 task)_

## What's NEXT (next 1–2 sessions)

Pick the first task from `TODO.md`. Suggested start: **Phase 1 section 1 (Specification)** — write `docs/schema.md` defining `.pvtproject` YAML, JSON dump format, and DuckDB tables. Pure documentation task, no Cadence needed, fast feedback.

Then Phase 1 section 2 (`.pvtproject` loader in Python) — also Cadence-free, unit-testable.

These two build the habit of updating `PROJECT_STATE.md` / `DECISIONS.md` before hitting SKILL-debugging friction.

## Open questions / blockers

None architectural. Implementation-level questions will surface as we build — log them here as they appear.

## Context cheatsheet for fresh sessions

- **User**: analog circuit designer; Cadence Virtuoso ICADVM18.1-64b; Python 3.11.4.
- **Environments**: home = dev (Claude Code OK, mirrored Cadence); work = red zone (offline only, no Claude Code). Deploy constraint: fully offline-installable.
- **POC file**: `../MyRunner/PvtDumpToJson.il` — proved the dump path works. **Do NOT extend it**; Phase 1 writes a clean collector from scratch (see Decision #12).
- **SKILL reference docs**: `../SKILL_file/` — 44 Cadence PDFs organized by topic. Consult before writing SKILL.
