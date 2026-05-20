# Handoff — 2026-05-20 (session 3)

For the next conversation window. This session was **GUI scenario
testing + bug fixing**. Read this first.

## Branch & state

`overnight-2026-05-19` (cut from `main`; nothing pushed, nothing merged).

**Everything is UNCOMMITTED.** The working tree now holds TWO layers of
uncommitted work, intermingled:
- **Prior (session 2)** items A–E — corner model-path round-trip,
  Sync Maestro History, `find_failed_corners` eval_err fix. Touched
  `failures.py`, `gui/loaders.py`, `skill_bridge.py`, `union.py`,
  `skill/pvtCorners.il`, `history_mirror.py` (+ its test), and parts of
  `gui/main_window.py` / `gui/views/corners_editor.py` / their tests.
- **This session (3)** — 8 GUI bug fixes (see below). Touched
  `gui/app.py`, `gui/main_window.py` (added to), `gui/diff_model.py`,
  `gui/views/results_tab.py`, `gui/views/run_progress.py`,
  `skill/pvtCollect.il`, and 4 test files.

Commit decision still pending — user has not split/bundled yet.

Tests: full suite **1583 passed, 93 subtests, 0 failed** (was 1571).

## What this session did

Ran GUI scenario testing: two role-play "user" agents drove the real
PyQt5 GUI through 14 scenarios (first launch → load → results → run →
diff → corner/measure edit → milestone → sync → bridge). They reported
10 issues. All 10 were addressed; a third agent re-tested the fixes
**8/8 PASS** (live, end-to-end).

### 8 real bugs — fixed & verified

| # | Bug | Fix |
|---|-----|-----|
| 1 | `File > Open Module…` menu always failed — `getExistingDirectory` returns a dir, `load_module` needs the `.pvtproject` file | `_on_open_module` resolves `<dir>/.pvtproject`; clear warning if absent |
| 4 | 2nd launch didn't restore the selected review | `_selected_review_path` tracked; `restore_session(last_review=)` rebinds + tree-selects; app.py persists it |
| 5 | Cancelling a run left the kanban header reading "Running:" | new `RunProgressWidget.mark_cancelled()` → header "CANCELLED:" |
| 6 | `prepFailed` Maestro histories re-failed every Sync (validator I12 rejects the raw status) | `pvtCollect.il` PASS 2 maps `prepFailed`/`aborted`/`killed` → sentinel `"failed"` |
| 7 | A parse-broken review could still be Run | `set_review_path(runnable=)` + right-click Run action disabled for broken reviews |
| 8 | Clicking a review node left Results blank / stale | new `ResultsTab.show_review_summary()` — header summary, clears stale run table |
| 9 | Cold-start showed only `[Module: -]` placeholder, no guidance | module label default → `未打开模块 — File ▸ Open Module… (Ctrl+O)` |
| 10 | diff cells showed `-0`; bridge button kept stale style | `_format_cell` collapses IEEE `-0.0`; GREEN clears button stylesheet |

### 2 reported bugs that were NOT real app bugs

- **Exit `core dump`** and **`corner push` "permanent hang"** were
  reported high-severity but did NOT reproduce. `app.main()`'s exit path
  ran 6× cleanly (incl. heartbeat active + a bridge op in flight at
  close); `pvt_corners_push` returned in 0.01s and the BridgeWorker
  delivered `op_complete` in 0.10s. Both were **artifacts of the scenario
  agent driving the GUI in-process via pytest-qt** — its harness teardown
  ≠ the real shutdown, and a bridge wedged by an earlier scenario step
  (`axlRunAllTests`) bled into a later one. See memory
  `reproduce-before-fix`. **Do not re-investigate these as app bugs.**
- The push investigation did surface a genuine adjacent gap: a wedged
  bridge makes `_dispatch` block forever with no user feedback (heartbeat
  skips while busy → dot stays GREEN). Fixed minimally: `_queue_op` arms
  a 60s stall-warning timer (`BRIDGE_OP_STALL_MS`) that logs a hint.

## Verification done

- Full pytest 1583/0; **+13 new regression tests** locking the 8 fixes
  (these bugs shipped originally because nothing tested them).
- SKILL #6: `pvtCollect.il` skillbridge-load-verified; collecting the
  real `Interactive.0/.1` prepFailed histories now yields
  `status='failed'` run.json (ingests cleanly).
- Re-test agent: 8/8 PASS via real UI interactions, incl. live V6
  (real `pvt run` + cancel) and V7 (live Sync on a scratch DB copy →
  `11 mirrored / 0 failed`).

## What's left / not touched

- **Commit decision** — 2 layers of uncommitted work to split or bundle.
- 8-cap Tier-1 **Cap#7 (copy-edit review)** and **Cap#8 (from-scratch
  wizard)** still entirely unimplemented (scenario I skipped — known gap).
- No live side effects left behind: Maestro corner set unchanged, real
  `simkit_1AXX/.db/simkit.duckdb` untouched (orphan collection dirs from
  verification were cleaned), `gui_state.json` restored.
