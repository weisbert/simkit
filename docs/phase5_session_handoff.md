# Phase 5 Corner Manager — session handoff (2026-05-21)

## Where things stand

Phase 5 Corner Manager is built, unified, English, agent-reviewed, and
**deployed to the red zone** — the user launched `b8844ba` there and the GUI
came up. Everything is on `main` and `overnight-2026-05-19`, pushed to origin.

Commit trail (newest first):
- `b8844ba` — File ▸ New Project (create a `.pvtproject` from the GUI;
  `Open Module` → `Open Project`)
- `0eeb909` — smart Push confirmation (dialog only on deletions) + rolling
  snapshot retention (keep 20)
- `be4b1c3` — corner Push safety gate (snapshot + confirm before a destructive
  replace=True push)
- `53ed5b7` — live dogfood results + live corner fixture
- `bb3465e` — template/axis GUI authoring, cornerModelsDir, agent-fix bundle
- `ae4e3b5` — unify into one Corners tab + English-ize all GUI strings
- `1b4dee5` — Phase 5 6-stage model layer + CLI + GUI

Offline: `1876 passed`. Live: the corner pull → build → classify → materialize
→ push round-trip is verified against live Maestro `fnxSession0`
(`docs/phase5_dogfood_results.md`).

## What the next conversation picks up

**The user will report what they are dissatisfied with after using the
software.** Expect GUI / UX change requests against the Corner Manager. Be
ready to act on them — reproduce in the offscreen GUI, fix, test, and (since
the user runs this in the red zone) note that changes need a redeploy.

## Still open

- The literal 6-stage GUI dogfood checklist (`docs/phase5_dogfood_checklist.md`)
  is the user's per-stage hands-on acceptance — not yet walked stage by stage.
- Deferred: corner-model GUI pull interactive reconciliation backfill;
  `pvt corner-model push/pull` CLI.

## Red-zone usage note

Deploys live under `<DEPLOYS>/current` (a symlink). A terminal already
activated against an old deploy must **re-`cd <DEPLOYS>/current`** (not just
`deactivate`/re-`source`) to pick up a new deploy — or open a fresh terminal.
