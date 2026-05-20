# Handoff — 2026-05-20 session

For the next conversation window. Read this first.

## Branch

`overnight-2026-05-19` (cut from `main`; nothing pushed, nothing merged).
Commits on it, oldest → newest:

```
f5b6a3e gui: fix stuck-pending + surface rename failures   ← see "WRONG claims" below
2154513 gui: add visible "Restart bridge" button (A5)
ffb4392 gui: populate cross-module 24h status strip (B1)
76e6b2f gui: enable milestone tagging (cap #6)
7a16249 docs: overnight report + dogfood logs + screenshots ← see "WRONG claims"
a70245d test: streaming test for the -u change
1be3f71 fix: pvt_runner_run idle detection ← THE real fix this session
```

## The user's 4 reported problems — current status

| # | Problem | Status |
|---|---------|--------|
| 4 | Run stuck at "pending" forever | ✅ FIXED — `1be3f71`. Root cause: `pvt_runner_run` idle detection required `axlGetRunStatus==(0,0)`, but this Maestro (`fnxSession0`) returns `(24,24)`/`(18,18)`/`(0,14)` even when idle. Now trusts `count_running` (rdb content). Verified e2e: run completes in 16s. |
| 3 | Maestro history named "Interactive.N" not our name | ✅ FIXED — downstream of #4. Rename only fires post-completion; once completion is detected the rename works. Verified: history became `orch_Test_basic_1779240708_1`. |
| 2 | simkit history empty vs full Maestro history | ⚠️ PARTIAL — a *completed* run now ingests into DuckDB and shows (the `runs` table went 0→1 rows after the e2e run). But simkit still does NOT mirror *pre-existing* Maestro history. Item D below. |
| 1 | "No open-module menu" | ❌ NOT FIXED — the GUI has no menu bar at all; module opens only via `--module` CLI arg or last-visited restore. Item C below. |

Plus a 5th issue found mid-session: **corner empty model file** (Spectre SFE-73 `include ""`). The user MANUALLY fixed the 3 live corners in Maestro. The underlying pull/push code bug is NOT fixed — item A below.

## WRONG claims to correct

`OVERNIGHT_CHARTER.md` and `OVERNIGHT_REPORT.md` (+ commit `f5b6a3e` message) claim "Bug #1 fixed via `-u`". **This is wrong.** The `-u` change addressed a mis-diagnosed stdout-buffering hypothesis that was never the bug. The real bug #1 fix is `1be3f71`. The `-u` flag is harmless and left in place. The next session should correct OVERNIGHT_REPORT.md's Bug #1 section.

## Remaining work (user to prioritize; A recommended first)

| # | Task | Why it matters |
|---|------|----------------|
| A | Corner pull/push: preserve the model file PATH for multi-section corners. Today `pvtCorners.il` push does `axlPutModel ch <basename>` and never calls `axlSetModelFile`; the pull blanks `_file_abs` for multi-section rows (`union.py:303` comment admits it). | Corners only work now because the user hand-fixed the live Maestro corners. A GUI edit + push, or a fresh multi-section corner, re-breaks them → SFE-73. |
| B | GUI corner editor: carry the model path on add-row / duplicate-row. | Likely the original source of the empty `_file_abs` in `baseline.union.json`. |
| C | GUI: add an "open module" affordance (menu bar or button). | Problem 1. Small, independent. |
| D | Import/mirror existing Maestro history into simkit's DuckDB. | Problem 2 deep fix. |
| E | `find_failed_corners` counts `eval_err` from inapplicable measurements as a corner failure → a clean PSS-only run reports "6 corners failed" (the `Rtime_clkout` transient measure eval-errs under PSS). Cosmetic noise; user said acceptable. | Low priority. |

## Key environment facts

- User's project: `/home/yusheng/cadence_work/Test/workarea/simkit_1AXX/` — project `1AXX`, DB at `.db/simkit.duckdb`, live Maestro session `fnxSession0`.
- Reviews: `reviews/sanity_check.review.json` (test `Test`, PSS) — this is the known-good e2e test review.
- skillbridge socket `/tmp/skill-server-default.sock`; bridge tool at `../skill_tools/skillbridge/`.
- venv: `.venv/` — use `.venv/bin/python` (system python3 lacks PyQt5).
- `axlGetRunStatus` on this Maestro is unreliable — never (0,0); `count_running` (rdb walk) is the authority.
- The 3 live corners (`TT`, `TT_pvt`, `TT_2p5G`) currently have correct model file paths (user fixed them). Don't assume they survive a fresh GUI corner-edit until item A is done.

## How to verify a run e2e (the real dogfood — do NOT substitute unit tests)

```
cd .../simkit_1AXX
.venv/bin/python -u -m simkit.cli run reviews/sanity_check.review.json \
    --session fnxSession0 --gui-jsonl
```
Expect: `item_started` → ~16s → `item_completed` → `review_done`. History renamed
`orch_Test_basic_*`. `runs` table gets a row (query with `CAST(timestamp AS VARCHAR)`
— raw TIMESTAMPTZ triggers a missing-pytz import error).

## Process discipline (the hard lesson of this session)

Two rounds of "fixes" shipped for bugs that were never reproduced — diagnoses from
sub-agents were treated as findings. RULE for next session: **reproduce the bug live
first, fix, then confirm on the same live reproduction.** A unit test or a synthetic
subprocess is necessary but NOT a substitute for the real end-to-end run. If the
environment blocks real verification, the task is BLOCKED, not done.

## Open tasks in the tracker

All current tasks (#9–#14) are completed. Items A–E above are not yet tracked.
