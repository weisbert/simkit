# Handoff — 2026-05-22 (corner & dimension unified)

For the next conversation. Read this first, then read
`docs/corner_manager_user_story.md` (痛点 a + h).

## Branch & state

`main`, all committed and clean. Recent commits:

- `523e90a` retire Corner Sets, unify on multi-mode Axes.
- `2547c80` **unify corner & dimension authoring** — the work below.

Offline tests green: full suite **1922 passed**.

## What changed (commit 2547c80)

A corner and an axis were two concepts; to the user they are one — a
corner is always a crossing of **dimensions**. The authoring is now one
flow.

Data layer (`corner_model.py`):
- A level (`CorrelatedTuple`) can carry a model-file `section`; a
  dimension (`CorrelatedAxis`) an optional `model_file` — the
  process-corner case (TT → section `tt`) is now expressible.
- A corner (`Column`) records, per crossed dimension, the subset of
  level labels it uses (`selected_levels`), and may carry inline
  dimensions (`inline_axes`) not in the project library.
- `materialize` crosses the selected subset and folds each chosen
  level's section into the row's model file.
- `assign_mode_to_column` folds a raw / pulled column into a mode.

GUI (`corner_manager.py`):
- `New Column` + `Axes…` merged. **`Dimensions…`** manages reusable
  dimensions (a level grid; a "section" column appears when a Model file
  is filled in). **`New Corner`** crosses dimensions in a tree, ticking
  the levels each corner uses, and stamps onto every ticked mode.
- Right-click a raw / foreign column → "Add to a mode".

Runtime-verified: authoring `VCO_PN_PVT` (process × temp × voltage,
5 × 2 × 3) yields a 30-point corner with per-level sections applied.

## NEXT — user acceptance

The acceptance gate is unchanged: a real signoff cycle inside the GUI on
the live Maestro session. The user dogfoods; restart `pvt gui` to pick
up new code. `.pvtproject` for live probes:
`workarea/simkit_1AXX/.pvtproject`; session `fnxSession0`. NOT yet
deployed to the red zone — needs the yellow→red deploy.

## Known gaps / polish

- The New Mode dialog's process preview (`column_models(col)`, 1-arg)
  does not resolve a dimension-built column's sections — cosmetic.
- A section dimension's `model_file` is a single path; if a corner
  already has its own model entry for that file the section overwrites
  it (intended). Multiple section dimensions on one corner each target
  their own file.
