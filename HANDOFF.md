# Handoff — 2026-05-20 (session 4)

For the next conversation window. This session: **finished the 8-cap
Tier-1 (Cap#7 + Cap#8), then backtested simkit against fresh
requirements**. Read this first.

## Branch & state

`overnight-2026-05-19` (cut from `main`; nothing pushed, nothing merged).
Last commit: **479b01b**.

**Uncommitted work** (two groups, intermingled in the working tree):

1. **Cap#7 / Cap#8 implementation** — 1 modified + 4 new files:
   - `python/simkit/gui/main_window.py` (M) — MVP `_new_review_dialog`
     replaced by `_new_review_wizard` / `_copy_edit_review` /
     `_after_review_written`; "Copy as…" right-click action added;
     Reviews-group menu now opens the wizard. Module-level
     `_is_valid_review_name` removed (unused after the swap).
   - `python/simkit/gui/views/review_editor.py` (new) — Cap#7 copy-edit
     `ReviewEditorDialog` + shared blocks (`ReviewItemsTable`,
     `SuiteFailureControls`, `build_review_dict`, `validate_review_dict`).
   - `python/simkit/gui/views/review_wizard.py` (new) — Cap#8 4-step
     `ReviewWizard`.
   - `tests/gui/test_review_editor.py` + `tests/gui/test_review_wizard.py`
     (new) — 20 widget tests.
2. **Three backtest docs** (untracked):
   - `docs/rf_designer_review_scenarios.md`
   - `docs/simkit_new_user_friction.md`
   - `docs/simkit_gap_matrix.md`

`.cadence/` and `logs_yusheng/logs0/` are untracked but **not ours** —
leave them out of any commit.

Commit decision still pending — user reviewed the work but did not
commit groups 1 or 2.

Tests: full suite **1603 passed, 93 subtests, 0 failed** (was 1583;
+20 from the new editor/wizard tests).

## What this session did

1. **Verified + committed the prior two layers** (session 2 corner/sync
   work + session 3's 8 GUI bug fixes) as a single bundled commit
   **479b01b** — user chose to bundle rather than split.
2. **Implemented Cap#7 + Cap#8** — the last two 8-cap Tier-1 capabilities.
   UI shape is spec-faithful per `docs/phase4_gui_spec.md §14`: Cap#7 is
   a form dialog (`ReviewEditorDialog`), Cap#8 is a 4-step `QWizard`.
   **The 8-cap Tier-1 is now complete.**
3. **Backtested simkit against fresh requirements.** Spawned two
   role-play agents:
   - an RF IC designer → `docs/rf_designer_review_scenarios.md`, a
     27-scenario PDR/CDR/FDR requirements catalog (Parts 0/A/B/C +
     D=multi-Maestro-in-one-Virtuoso, E=multi-Virtuoso).
   - a first-time simkit user → `docs/simkit_new_user_friction.md`,
     14 ranked usability friction points.
   Then wrote **`docs/simkit_gap_matrix.md`** — the backtest result.

## Verification done

- Full pytest **1603/0**; +20 widget tests locking the editor + wizard.
- In-process end-to-end: drove the real `MainWindow` + `ReviewWizard` +
  `ReviewEditorDialog` against a scratch copy of the `1AXX` module —
  wizard writes a loadable `.review.json`, copy-edit pre-fills + renames
  + writes, `_after_review_written` rescan picks both up in the tree.
- `pvt gui` boots clean with the new code.
- Confirmed two agent-reported bugs against the source (see B-1/B-2 in
  the gap matrix) — they are real.

## What's next — work the gap matrix

`docs/simkit_gap_matrix.md` is the work queue. Key framing: many gaps are
**PARTIAL not MISSING** — simkit already has the spec pipeline, diff
regression (handles name-drift), and the `partial_run` flag; the gap is
GUI surfacing, which is cheap.

Suggested order (from the matrix §5):
1. **B-1, B-2** — confirmed bugs, ~1 day. B-1: CLI `measure list-bundles`
   scans `measurements/`, GUI scans `bundles/` — directory mismatch.
   B-2: Corners "Send to Maestro" silently disabled — surface
   `validation_errors()` as a tooltip / error strip.
2. **G-1** — make specs visible/authorable (core promise: auto pass/fail).
3. **G-3 + G-4** — margin rollup + convergence surfacing.
4. **G-7, G-8, G-12, G-15** — the new-user cliff (cheap, high goodwill).
5. **G-2 (plots)** — large; schedule deliberately.
6. **G-10 (multi-session), G-11 (Monte Carlo)** — structural mini-phases;
   Parts D/E of the scenario catalog are entirely unaddressed today.

User has not yet picked which item to start. Confirm before coding.

## Memory updated this session

New: `project_rf_designer_requirements.md` — points at the scenario
catalog as simkit's requirements basis + the 4 recurring pain themes
(hand-transcribed margin tables / name-drift regression / no
traceability / silent partial results).
