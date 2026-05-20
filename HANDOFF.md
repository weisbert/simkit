# Handoff — 2026-05-20 (session 5)

For the next conversation window. This session: **worked the gap matrix
— shipped B-1/B-2 + G-1 + G-3/G-4 + G-7/G-8/G-15, all committed**. Read
this first.

## Branch & state

`overnight-2026-05-19` (cut from `main`; nothing pushed, nothing merged).
Last commit: **00dee0e**. Working tree is **clean** — everything below
is committed.

`.cadence/` and `logs_yusheng/logs0/` are untracked but **not ours** —
leave them out of any commit.

Tests: full suite **1660 passed, 93 subtests, 0 failed** (was 1603 at
the start of the session; +57 across the new work).

## What this session did

Worked `docs/simkit_gap_matrix.md` §5 in order. Each item was
reproduced/verified live against the `1AXX` module (or a scratch copy)
before being called done.

1. **B-1 + B-2** — commit `04a239f`. B-1: the GUI loader hardcoded a
   `bundles/` subdir while the CLI scans the project's `measurementsDir`
   — same project, opposite answers. The loader / pulled-bundle write /
   fs-watcher now all resolve via `resolve_measurements_dir`. Also added
   `"measurementsDir": "./bundles"` to `simkit_1AXX/.pvtproject` (that
   file is **outside this repo** — a one-line edit so 1AXX's existing
   `bundles/` data stays visible). B-2: Corners "Send to Maestro" was
   gated on `validation_errors()` with nothing shown — added a red error
   strip + button tooltip.
2. **G-1** — commit `29b5a2a`. Specs usable end-to-end: Measures spec
   field gets a syntax hint + live validation; Results tab shows a
   zero-spec hint strip; right-click a Results row → "Set spec…" which
   re-evaluates `spec_status` in place against the recorded values and
   writes the spec back into the matching bundle entry.
3. **G-3 + G-4** — commit `2fd62e1`. New **Summary tab** (right panel,
   next to Results): a health line (status counts + sim failures +
   `partial_run` flag, amber when unhealthy) and a per-output margin
   rollup table (`spec_eval.spec_margin` + `gui/run_summary.py`).
   Results tab gains a "只看失败行" filter. Filter + red row tint now
   treat `status` of `eval_err`/`failed`/`no_convergence` as a problem
   (previously a calc error greyed by silently).
4. **G-7 + G-8 + G-15** — commit `00dee0e`. G-7: Help ▸ 术语表 glossary
   dialog + vocabulary tooltips on the Session input and left-tree group
   nodes. G-8: New Review wizard Step 2 gates Next on every item being
   complete; Step 4 gets a plain-language recap. G-15: per-state bridge
   status-dot tooltips. **G-12 was found already implemented** —
   `_on_run_requested` already prompts for a run name + passes `--label`
   — so the gap matrix's G-12 entry is stale; no work needed.

## New files this session

- `python/simkit/gui/run_summary.py` — `run_health` + `margin_rollup`
  (pure, no Qt).
- `python/simkit/gui/views/summary_tab.py` — the Summary tab + its
  `MarginRollupModel`.
- `python/simkit/gui/views/glossary_dialog.py` — G-7 glossary.
- 4 new test files (`test_run_summary`, `test_summary_tab`,
  `test_glossary_dialog`, plus additions to many existing ones).

## What's next — gap matrix §5, step 5

`docs/simkit_gap_matrix.md` §5 remaining order:

5. **G-2 (plotting / curves)** — the next item. Results is table-only;
   every RF review artifact is a curve (gain/NF vs freq, compression).
   This is the matrix's only **L** (structural) GUI item — "schedule
   deliberately". Recommended: start with a design-research pass before
   cutting scope. **User explicitly deferred this to a future
   conversation** — do not start it without checking in.
6. **G-5, G-6, G-9, G-14** — traceability, cross-milestone trend,
   corner-model coherence, Measures-editor affordances.
7. **G-10 (multi-session), G-11 (Monte Carlo)** — structural mini-phases;
   Parts D/E of the scenario catalog are entirely unaddressed.

## Verification notes for next session

- `1AXX`'s latest run (`7a85bb53…`) is a **partial run** with 30 ok / 6
  `eval_err` rows, all `no_spec` — a good live dogfood case for the
  Summary tab + failed-filter + set-spec.
- Pattern that worked well: copy `simkit_1AXX` to `/tmp/<scratch>` and
  drive the real `MainWindow` headless (`QT_QPA_PLATFORM=offscreen`)
  rather than mutating production data.
