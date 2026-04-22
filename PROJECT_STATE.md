# Project State

_Last updated: 2026-04-22_ (late)

## Current phase

**Phase 1: Data Pillar MVP** — specification complete; implementation beginning.

## Goal of Phase 1 (one sentence)

End-to-end loop: "Maestro sim finishes" → "one command saves it" → "Python can query it and diff two slices."

## Recent timeline

- **2026-04-21 → 2026-04-22**: initial design conversation closed all architectural questions. See `DECISIONS.md` entries #1–#12.
- **2026-04-22**: project scaffold and git repo created; Phase 1 scope locked in `TODO.md`.
- **2026-04-22**: Phase 1 §1 (Specification) complete — `docs/schema.md` fleshed out from skeleton; `config/pvtproject.example.yaml` created.
- **2026-04-22**: Decided JSON over YAML/TOML for `.pvtproject` (see Decision #13); spec + example migrated; §2 loader unblocked.
- **2026-04-22**: Phase 1 §2 item 1 done — Python loader `python/simkit/project.py` + 30 stdlib-`unittest` tests. Walker, env `PVT_PROJECT`, schema validation all in place.

## What's DONE

- All architectural decisions for the data pillar (see `DECISIONS.md`)
- Phase 1 scope defined (see `TODO.md`)
- Project scaffold and git repo
- Phase 1 §1 Specification: schema spec + example `.pvtproject`
- Phase 1 §2 item 1 — Python `.pvtproject` loader (`python/simkit/project.py` + tests)

## What's IN PROGRESS

_(nothing — between tasks)_

## What's NEXT (next 1–2 sessions)

Phase 1 §2 item 2 — **SKILL-side `.pvtproject` loader**. Same walker + a minimal strict-JSON parser in SKILL, reading the same file the Python loader reads (per Decision #13). Before coding, consult `SKILL_file/` PDFs on file I/O and string parsing. Validation rules should mirror the Python loader so the two agree on accept/reject.

After §2 is fully done: move to §3 (collector SKILL from scratch, per Decision #12 — do NOT extend the POC).

## Open questions / blockers

_(none)_

## Context cheatsheet for fresh sessions

- **User**: analog circuit designer; Cadence Virtuoso ICADVM18.1-64b; Python 3.11.4.
- **Environments**: home = dev (Claude Code OK, mirrored Cadence); work = red zone (offline only, no Claude Code). Deploy constraint: fully offline-installable.
- **POC file**: `../MyRunner/PvtDumpToJson.il` — proved the dump path works. **Do NOT extend it**; Phase 1 writes a clean collector from scratch (see Decision #12).
- **SKILL reference docs**: `../SKILL_file/` — 44 Cadence PDFs organized by topic. Consult before writing SKILL.
