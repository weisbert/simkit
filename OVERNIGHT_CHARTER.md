# Overnight Charter — 2026-05-19

User went to sleep. Granted full autonomy. Will only look at GUI effect in the morning.
This file is my own discipline guard so I don't drift.

## Deliverable

A working GUI on branch `overnight-2026-05-19` such that user can:
1. Launch the GUI
2. Run through phase 4 spec's MUST mandates (A1-A5, B1-B5, 8-cap Tier-1) and have them all work
3. See the 3 reported bugs fixed (pending-stuck / Interactive.0 / real-env compat)

NOT a 500-line report. A working tool.

## Scope (locked)

IN:
- `docs/phase4_gui_spec.md` MUST mandates: A1-A5, B1-B5, 8-cap Tier-1
- 3 reported bugs from this session
- Persona dogfood loop for verification

OUT:
- Nice-to-haves in the spec
- Refactors not required by a fix
- CLI surface
- New features not in spec

## Rules

| Rule | Action |
|------|--------|
| Branch | `overnight-2026-05-19` only |
| Push/merge | NEVER. User decides in morning. |
| Force push / rebase / rm -rf / git reset --hard | BANNED |
| Spec conflict | Follow spec, log to `DECISIONS.md`, flag in report |
| Calendar bug | 1-line fix → fix. Else → log, move on |
| Maestro dies | Do NOT restart it. Switch to non-GUI work. Tell user in report. |
| skillbridge wedge | Use `reference_skillbridge_recovery` dance, max 2 retries, else degrade to mock |
| Done = | (a) skillbridge probe (b) python runtime smoke (c) persona dogfood — all 3 pass |
| User intervention | NEVER request. Only `git commit` to branch + write to `OVERNIGHT_REPORT.md` |

## Phases

### Phase 0 — Now (running)
- [x] Charter written
- [x] Branch created
- [ ] 4 parallel audit/diagnose agents launched

### Phase 1 — Audit & diagnose (parallel, ~45 min)
- GUI gap audit (Explore)
- Bug #1 diagnosis
- Bug #2 diagnosis
- Bug #3 investigation

### Phase 2 — Fix bugs first (sequential, ~2 hr)
- Bug #2 (naming) → likely 1-file SKILL call change
- Bug #1 (pending) → polling fix
- Bug #3 (real-env) → either remove override or confirm pass-through
- After each: persona dogfood + commit on branch

### Phase 3 — Fill GUI gaps (sequential, ~4 hr)
- Iterate gap matrix in spec priority order
- Each feature: implement → 3-layer verify → commit

### Phase 4 — Mid-night dogfood sweep (~1 hr)
- Run all 11 mandates as persona, log to `logs_yusheng/overnight_dogfood/`
- This is the morning evidence

### Phase 5 — Report (15 min)
- `OVERNIGHT_REPORT.md` at repo root
- Line 1: GUI launch command
- Mandate table with checkmark / fail per mandate
- List of known issues
- List of self-made decisions (informational only — user said won't review)

## Failure handling

| Failure | Action |
|---------|--------|
| Agent disagrees with another agent | I (PM) decide; log to DECISIONS.md |
| skillbridge returns wrong shape | Recovery dance (pyKillServer + pyStartServer), max 2x |
| Maestro hangs | Stop GUI work, switch to unit-test work; report cause |
| Test fails | Fix root cause; never `--no-verify` |
| Out of time | Commit what works; mark unfinished items in report |

## Anti-drift checklist (re-read this every ~hour)

- Am I still on `overnight-2026-05-19`? ← `git rev-parse --abbrev-ref HEAD`
- Am I still building toward the 11 mandates, or did I sneak in a refactor?
- Did I run dogfood on the last change before committing?
- Am I about to ask user a question? STOP — make a default decision and document.
