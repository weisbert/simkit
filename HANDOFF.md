# Handoff — 2026-05-21 (corner-editor polish + deploy rework)

For the next conversation. Read this first.

## Branch & state

`main`. After the push at the end of this session, `origin/main` is at
**`f178204`**. Working tree clean. **Nothing deployed to the red zone
yet** — the user still needs to run the yellow→red deploy.

Offline tests: **1925 passed, 93 subtests** — run with
`QT_QPA_PLATFORM=offscreen PYTHONPATH=python .venv/bin/python3 -m pytest`
(use `timeout` + `-p no:cacheprovider`; a real modal hangs offscreen Qt).

## What this session did (8 commits)

This was a long corner-editor + deploy session, driven by red-zone UX
feedback from the RFIC-designer user.

1. **`3d5e556` / `5b9470b` / `7c97166`** — corner-editor 12-item polish:
   Cadence row grouping, Enable row, per-corner Tests scope (pull/push
   SKILL, live-verified), context menus, row/column reorder, Reorder
   dialog, header drag, Ctrl+C/V copy-paste, inline rename, point-count
   header, pull adopts Maestro variable order.
2. **`e385a5d` / `7786cc3`** — deploy rework ("plan B"): the venv is now
   shared at `<DEPLOYS>/venv`, keyed to a `requirements.lock.txt` hash.
   A code-only deploy just flips the `current` symlink — no pip, no
   reinstall. simkit is imported via a static `.pth`, not pip-installed.
   See `scripts/README.md` "The shared-venv model".
3. **`91b9aa0`** — `pvt gui` Qt-load-failure hint now derives the venv
   path from `sys.prefix` (was a hard-coded `.venv`).
4. **`9c408e2`** — corner-manager concept simplification: the user found
   six co-equal dialogs overwhelming. Profile removed from the GUI;
   Variant merged into Mode ("New Mode ▸ Derived from an existing mode");
   new "Edit Mode…" re-classifies registers vs PVT (`reclassify_mode`);
   New Mode surfaces Process rows; Templates renamed "Corner Sets".
5. **`f178204`** — corner table now mirrors Cadence's Corners Setup form:
   a real Tests grid (one row per test + a checkbox per corner), a
   trailing "Number of Corners" row, and section-header rows. The SKILL
   pull now emits a top-level `tests` master list (live-verified).

## First red-zone deploy after this session

The deploy scripts themselves changed (plan B). The first deploy:
- Yellow: `git pull` then `python scripts/make_payload.py --no-wheels`.
- Red: `unpack_payload.sh` copies wheels from the old `current`, then
  `deploy_venv.sh` **builds `<DEPLOYS>/venv` once** (this run still
  installs packages). Every code-only deploy after that is seconds.
- Activate (csh): `source <DEPLOYS>/venv/bin/activate.csh`.

## Deferred / known gaps

- **Axis right-click reframe** — the standalone Axes dialog was removed
  (it confused the user); the planned right-click "Link variables" flow
  was *not* built. Attaching a correlated axis to columns + migrating
  member vars off `pvt_vars` is a real sub-feature. Data-layer
  `CorrelatedAxis` machinery is intact; axes still work via JSON / the
  template `+axis` syntax. VCO-binding-only — low priority.
- **Tests grid needs a pull** — `CornerModel.tests` (the master test
  list) is populated only by a Tests-aware pull. Before any pull the
  Tests section does not appear.
- **No Nominal column** — Cadence's corner table has a dedicated
  "Nominal" column; simkit treats all corners uniformly. User was told;
  not requested.

## Verification notes

- SKILL pull/push of per-corner Tests + the master `tests` list were
  live-verified against Maestro `fnxSession0` this session.
- `.pvtproject` for live probes: `workarea/simkit_1AXX/.pvtproject`.
- The acceptance gate is unchanged: Phase 4/5 is done only when the user
  completes a real signoff cycle inside the GUI on the red zone.
