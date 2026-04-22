# Project State

_Last updated: 2026-04-22_

## Current phase

**Phase 1: Data Pillar MVP** — specification complete; implementation beginning.

## Goal of Phase 1 (one sentence)

End-to-end loop: "Maestro sim finishes" → "one command saves it" → "Python can query it and diff two slices."

## Recent timeline

- **2026-04-21 → 2026-04-22**: initial design conversation closed all architectural questions. See `DECISIONS.md` entries #1–#12.
- **2026-04-22**: project scaffold and git repo created; Phase 1 scope locked in `TODO.md`.
- **2026-04-22**: Phase 1 §1 (Specification) complete — `docs/schema.md` fleshed out from skeleton; `config/pvtproject.example.yaml` created.

## What's DONE

- All architectural decisions for the data pillar (see `DECISIONS.md`)
- Phase 1 scope defined (see `TODO.md`)
- Project scaffold and git repo
- Phase 1 §1 Specification: schema spec + example `.pvtproject`

## What's IN PROGRESS

_(nothing — between tasks)_

## What's NEXT (next 1–2 sessions)

Phase 1 §2 — **`.pvtproject` loader (Python)**. Walker (cwd → up), YAML parser, fallback order (env `PVT_PROJECT` → file → error). Pure-Python module, unit-testable, no Cadence dependency.

Then the SKILL-side equivalent (§2 item 2): same walker/parser logic in SKILL, using JSON or a restricted YAML subset (SKILL has no YAML libs).

## Open questions / blockers

- **pyyaml not available** on dev machine's system Python (`/usr/bin/python3.11`). Phase 1 §2 requires a YAML parser. Decide: vendor pyyaml into the project tree for the offline-deploy constraint (DECISIONS #1 principle, README hard constraints), or pick a zero-dep alternative (restricted-YAML subset, TOML, plain JSON config). Resolve before starting §2.

## Context cheatsheet for fresh sessions

- **User**: analog circuit designer; Cadence Virtuoso ICADVM18.1-64b; Python 3.11.4.
- **Environments**: home = dev (Claude Code OK, mirrored Cadence); work = red zone (offline only, no Claude Code). Deploy constraint: fully offline-installable.
- **POC file**: `../MyRunner/PvtDumpToJson.il` — proved the dump path works. **Do NOT extend it**; Phase 1 writes a clean collector from scratch (see Decision #12).
- **SKILL reference docs**: `../SKILL_file/` — 44 Cadence PDFs organized by topic. Consult before writing SKILL.
