# Overnight Report — 2026-05-19

**Launch:** `git checkout overnight-2026-05-19 && .venv/bin/python -m simkit.cli gui`

(or `pvt gui` if you have the simkit entry on `$PATH`).

> **CORRECTION (2026-05-20) — read this first.**
> The "Bug #1" claim in this report is **WRONG**. The overnight `-u` change
> addressed a mis-diagnosed stdout-buffering hypothesis that was never the
> actual cause of "stuck pending". The real cause — `pvt_runner_run`'s idle
> detection requiring `axlGetRunStatus == (0,0)`, which this Maestro never
> returns — was reproduced live and fixed the next day in commit `1be3f71`.
> "Bug #2" (Interactive.N naming) is real but its fix only takes effect once
> completion is detected, i.e. it also depended on `1be3f71`. See `HANDOFF.md`
> for the full, corrected picture. The GUI-gap work below (A5 / B1 / Cap#6)
> stands and is unaffected.

## TL;DR

Three GUI gaps closed (A5 / B1 / Cap#6) and two bug *attempts* made — one
(`-u`) later found to be a mis-diagnosis, see the correction above. Branch
`overnight-2026-05-19`; tests went from 1512 → 1549.
Out of scope: 8-cap Tier-1 caps #7 (Copy-edit) and #8 (Wizard) — these
are wholly absent and not part of "大差不差".

## Bugs — corrected status (see correction banner above)

| # | Symptom | Actual root cause | Real fix |
|---|---------|-------------------|----------|
| 1 | GUI stuck on "pending" while sim already ran | **NOT stdout buffering.** `pvt_runner_run`'s poll-to-idle loop required `axlGetRunStatus == (0,0)`; this Maestro returns `(24,24)`/`(18,18)`/`(0,14)` even when idle, so a 10s run polled to the 1800s timeout. | Commit `1be3f71` (2026-05-20): idle detection now trusts `count_running` (rdb content). The overnight `-u` change is harmless but did not fix this. |
| 2 | Maestro showed "Interactive.N" not the GUI's chosen name | The rename was correct but only fires *after* completion is detected — which never happened because of bug #1. The `errset/nil` swallow was a real latent issue but not the user-visible cause. | The errset-removal (commit `f5b6a3e`) is kept; the user-visible symptom is resolved by `1be3f71`. Verified e2e: history renamed `orch_Test_basic_*`. |
| 3 | Real-env (公司 Command + alps) compat | n/a — audit verdict | **A: equivalent to manual click.** No `axlSetMainSimulator`/`axlPutRunMode`/host/queue overrides anywhere in the dispatch. One narrow caveat: `ic_from:` items use Spectre-flavored `+nodeset/+ic` in `additionalArgs` — alps compat with those flags unknown. Common batch path is engine-agnostic. |

## GUI gaps closed

| ID | What changed |
|----|-------------|
| A5 | Visible "Restart bridge" button next to the heartbeat dot. Hidden when GREEN, plain on AMBER, red+bold on RED. Tooltip points at the CIW recovery dance for the case where the pyServer itself is down. |
| B1 | Status strip now populated: queries every recent module's DuckDB for last-24h ingests, renders `Last 24h: X done / Y running / Z FAIL` with up to 8 clickable FAIL pills. Refreshes on 30s timer + run_finished + GREEN recovery. |
| Cap#6 | Right-click on a History row → "Set milestone… (current)" opens an editable combo (PDR / CDR / FDR + free text) and writes `runs.milestone`. "Clear milestone" appears when something is set. Tree refreshes immediately. |

## What you'll see (3 screenshots in `logs_yusheng/overnight_dogfood/`)

1. `main_window_green.png` — bridge healthy, restart button hidden, status strip shows `Last 24h: 17 done / 1 running / 3 FAIL` with 3 chips
2. `main_window_amber.png` — bridge stale, plain restart button visible
3. `main_window_red.png` — bridge broken, red+bold restart button

## Try these in order (12 minute morning check)

1. Launch GUI. **Verify** top bar shows status strip with real data (or 0/0/0 if your DB has no recent runs).
2. Open your usual module (or load via `--module`). **Verify** left tree populates.
3. Click "Run this review" on any small review. **Verify** the kanban row flips off "pending" within seconds (NOT at subprocess exit).
4. While the run is in flight, check Maestro's History panel. **Verify** the entry eventually carries your GUI-supplied name (not "Interactive.N").
5. Right-click any History row. **Verify** "Set milestone…" is enabled (was greyed out yesterday); pick PDR.
6. **Verify** the left tree's Milestones group now contains a PDR node and the row's tooltip shows `milestone: PDR`.
7. (Optional, requires breaking the bridge) Stop the Cadence pyServer in CIW. **Verify** within 30s the status dot goes RED and the "Restart bridge" button becomes red+bold. Restart pyServer in CIW. Click "Restart bridge". **Verify** the dot recovers to GREEN.

## Out of scope (intentionally not touched)

- 8-cap Tier-1 cap #7 (**Copy-edit**) and cap #8 (**Wizard**) — entire features absent. Per audit, these are Stage 5+ scope and not part of the overnight "大差不差" cut.
- B3 medium-risk gap: Compare exists in Results header but not on right-click of each History row. Functional, just one extra click for now.

## Decisions I made (you said you won't review — listed for completeness)

1. **Bug #2 fix path:** kept post-run rename rather than trying to pre-name a session — Cadence has no `axlSetSessionName` / `axlNewSession` API and `axlRunAllTests`'s 2nd arg is ignored (probed and confirmed). The errset removal + readback is the minimal correct fix.
2. **Restart bridge UX:** chose to keep the button hidden in GREEN to avoid clutter. Alternative was to show it always greyed out — but a hidden button is more honest about "everything's fine, no action available".
3. **Status strip data freshness:** 30s polling, NOT live event-driven. Alternative was per-run-finished only — but cross-module activity from other simkit processes wouldn't be picked up. 30s is a reasonable trade-off.
4. **Milestone validation:** capped at 64 chars; rejects control characters; otherwise free text. PDR/CDR/FDR are presets in the combo but the user can type anything.
5. **FAIL chip click:** logs + switches to Results tab when the click is on a run in the currently-loaded module. Full cross-module navigation (auto-switch module + open review) deferred to a Phase 5 follow-up — wasn't blocking and the design call deserves your input.

## Files changed

- `skill/pvtRunner.il` — surface rename failures (bug #2)
- `python/simkit/gui/controllers/run.py` — `-u` argv (bug #1)
- `python/simkit/gui/bridge_worker.py` — `restart()` + `_restart_local()` (A5)
- `python/simkit/gui/main_window.py` — restart button + status strip wiring + milestone dialog (A5, B1, Cap#6)
- `python/simkit/gui/status_strip.py` — new module (B1)
- `python/simkit/gui/app.py` — paths provider wiring (B1)
- `python/simkit/milestone.py` — new module (Cap#6)
- `tests/...` — +37 new tests across 4 test files

`git log overnight-2026-05-19 --oneline ^main` shows the 6 commits in build order.

## Regression

`pytest tests/` → **1549 passed, 93 subtests passed** (was 1512 at session start).
No skips, no flakes, no warnings I introduced.

## Known gaps / what I didn't touch

- **Live end-to-end dogfood was skipped overnight — and that is exactly why
  bug #1 was mis-diagnosed.** It was done properly on 2026-05-20: a real
  `pvt run` of `sanity_check.review.json` reproduced the hang, root-caused it
  to idle detection, and confirmed the `1be3f71` fix (run completes in 16s).
  Lesson recorded in `HANDOFF.md` and memory.
- **B3 right-click-Compare on History rows** — not added; existing Compare button in Results header still works.
- **Caps #7-8** — out of scope.
- **Real-env (alps) `ic_from:` codepath** — needs a one-time probe on the real-env host. Common batch path is safe.
- **Corner pull/push** for multi-section corners loses the model file path
  (caused a separate SFE-73 the next day; user hand-fixed the live corners).
  Tracked as item A in `HANDOFF.md`.
