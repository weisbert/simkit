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

- **Config file format — pyyaml missing.** `/usr/bin/python3.11` has no `yaml` module, and the red-zone target is offline-only (README hard constraints). §2 loader can't start until this is resolved. Three options on the table:
  - **(a) Vendor pyyaml** into the project tree (copy wheel + `sys.path` shim or bundled sdist). Keeps `.pvtproject` as YAML. Cost: one offline-install dance per env.
  - **(b) Switch to TOML.** Python 3.11 has `tomllib` built-in, zero deps. Cost: schema.md §1 needs a rewrite (YAML → TOML), example file rename, DECISIONS entry explaining the switch.
  - **(c) Switch to JSON.** Zero deps, but hand-writing config gets ugly (no comments, quote noise).
  - Resolve this first thing next session; if we switch format, the spec change must be made before writing the loader.

## Context cheatsheet for fresh sessions

- **User**: analog circuit designer; Cadence Virtuoso ICADVM18.1-64b; Python 3.11.4.
- **Environments**: home = dev (Claude Code OK, mirrored Cadence); work = red zone (offline only, no Claude Code). Deploy constraint: fully offline-installable.
- **POC file**: `../MyRunner/PvtDumpToJson.il` — proved the dump path works. **Do NOT extend it**; Phase 1 writes a clean collector from scratch (see Decision #12).
- **SKILL reference docs**: `../SKILL_file/` — 44 Cadence PDFs organized by topic. Consult before writing SKILL.
