# Phase 5 Corner Manager — dogfood results (2026-05-21)

Overnight autonomous run. Covers the Corner / Corner-Manager **unification**
(one "Corners" tab, present at GUI startup, no load step), GUI **English-ization**,
the **RFIC-designer walkthrough**, the **fixes** it surfaced, and a **live
Maestro dogfood** of the corner pull/push data path.

## What shipped

| commit | content |
|---|---|
| `1b4dee5` | Phase 5 Corner Manager — 6-stage model layer + CLI + GUI (build) |
| `ae4e3b5` | unify Corners into one tab + English-ize all GUI strings |
| `bb3465e` | corner manager — template/axis authoring, cornerModelsDir, fixes |

- Single `"Corners"` tab always hosts the Corner Manager; present + usable at
  GUI startup. `load_module` auto-discovers the project's `.cornermodel.json`
  (honouring `cornerModelsDir`), else seeds one from the default union, else a
  blank model. `CornersEditor` deleted. `File ▸ Open Corner Model` demoted to
  optional. In-GUI edits persist to the `.cornermodel.json`.
- All user-visible GUI strings are English.
- New Template / New Axis GUI authoring added (was the walkthrough blocker —
  templates and correlated axes previously needed hand-edited JSON). New Run Set
  uses a checkable column picker. Check status bar now reports `missing_file`.
  Column filter shares the row filter's and/or/* grammar.

## Offline verification

`1868 passed, 93 subtests` — full suite green. `tests/gui/test_view_coverage.py`
(M2 hard gate) green.

## Live Maestro dogfood — corner pull/push data path

Run against the live session **`fnxSession0`** (`sim_yusheng/Test/maestro`,
project `1AXX`), Virtuoso online.

| step | result |
|---|---|
| `pvt_corners_pull` | ✅ pulled 3 live corners (TT, TT_pvt, TT_2p5G) with real shapes — `model.section` arrays (`tt/ss/ff`), VDD sweep `(3, 2.8)` |
| `cornermodel_from_union` (unify auto-build path) | ✅ built a 3-column cornermodel from the live pull |
| `classify_pull` vs `corner_models/baseline.cornermodel.json` | ✅ matched all 3, 0 foreign, 0 missing |
| `materialize` | ✅ baseline → 3 pushable rows |
| `pvt_corners_push` (replace=True) round-trip | ✅ pushed the snapshot back, re-pulled — **live corner table unchanged** |

Live fixture captured: `tests/fixtures/live/fnxsession0_corners.union.json`
(Mandate M1).

**Observation (not a bug):** the live Maestro corner table carries a `temp`
variable (= 55) in addition to `temperature`; the on-disk `baseline.cornermodel.json`
only has `temperature`. `classify_pull` correctly flags this as a per-corner
diff. When the user pulls into the corner manager they will see a `temp` row.

## M4 Definition-of-Done

```
[x] M1  Live I/O fixture captured — tests/fixtures/live/fnxsession0_corners.union.json
[x] M2  Corner-manager view has a render test; test_view_coverage green
[x] M3  No new Qt-signal controller (N/A — additions are within an existing view)
[~] V   Live-verified: the corner pull → build → classify → materialize → push
        data path round-trips against live Maestro (fnxSession0). NOT yet run:
        the literal 6-stage GUI checklist below (clicking each stage through the
        running MainWindow with the bridge worker). The GUI layer is offline-
        tested; the bridge functions it calls are the ones live-verified here.
[x] R   Agent walkthrough findings independently re-checked before fixing — one
        finding (checklist JSON "missing fields") was a misread and skipped.
```

## Left for the user — hands-on 6-stage acceptance

`docs/phase5_dogfood_checklist.md` Stages 1–6 are the per-stage GUI walkthrough:
open the GUI on a real project, drive the modes panel / templates / variants /
run sets / Push, and confirm each stage's gate visually. The data path underneath
is live-verified; this is the visual / interaction acceptance.
