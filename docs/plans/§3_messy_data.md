# Refactor plan: §3 collector testability + Python validator

_Returned by Plan agent on 2026-05-10. Verbatim. See `CONTRACT.md` for director-level reconciliation; tonight only Step 5 (validator) implements — SKILL refactor steps 1–4 deferred for live Tier-2 verification._

Working dir: `/home/yusheng/cadence_work/Test/workarea/simkit`. Plan only — no code edits.

The current `_pvtCollIterateResults` (`skill/pvtCollect.il` lines 533–771) interleaves three concerns: (1) opening the result DB and walking funobj-call slots, (2) maintaining caches keyed by live observations, and (3) shaping rows + sentinels for emit. Goal: cleave (1) from (2)+(3) so the entire shaping logic — including the two empirically-untested sentinel passes — becomes Tier-1-testable from synthetic tuples.

---

## 1. Refactor boundary spec

### 1.1 What lives where

| Layer | Function | Live-side calls? | Tier-1-testable? |
|---|---|---|---|
| I/O wrapper | `_pvtCollWalkRdb(sess histName hsdb)` | yes (axlrdb + maeOpenResults + axlGetNote) | no — needs live or skillbridge |
| Pure shaping | `_pvtCollRowsFromTuples(walkData)` | no | yes — the new test target |
| Public flow | `_pvtCollIterateResults(sess histName hsdb)` | indirectly (via Walk) | no — kept thin, just composes |

`_pvtCollIterateResults` becomes a 5-line composer: `walkR = _pvtCollWalkRdb(...)` → on err, return; on ok, `(pvtOk (_pvtCollRowsFromTuples (pvtUnwrap walkR)))`. The signature it presents to `PvtSave` (line 950) does not change.

### 1.2 `_pvtCollWalkRdb(sess histName hsdb)` contract

Returns `(pvtOk walkData)` where `walkData` is a flat assoc-list (one record built via `makeTable` with sidecar key list, so the unit-test driver can `setarray`-construct an isomorphic value with no live calls). The fields:

```
walkData:
  outputs        : list of outputTuple   ; pass-1 raw rows, pre-shaping
  tests          : list of testTuple     ; from (rdb->tests) for pass 2
  pidSweepCache  : ((pid . sweepAssoc) ...) ; raw, NOT yet PVT_JSON_EMPTY_OBJECT-folded
  cornerParamCache : ((cornerName . paramsList-or-nil) ...) ; raw axlrdbc->params output
  testNoteCache  : ((testName . string-or-nil) ...) ; "" → nil distinction preserved
  totalPoints    : int
```

Tuple shapes (each as a `list` so SKILL `nthelem` works in the pure side):

- `outputTuple = (pid cornerName testName outputName outputValue)` — exactly the 5 fields pass 1 reads via `out->cornerName`, `out->testName`, `out->name`, `out->value` plus the loop's `pid`. We also include the per-output `(funcall out->test)` and `(funcall testObj->corner)` resolution as **part of the corner cache** (not per-tuple) since they are corner-keyed not output-keyed.

- `testTuple = (pid cornerName testName statusSym)` — exactly what pass 2 reads from each `tst` via the four bare slots in Decision #16's table. `statusSym` stays a symbol (or nil/non-symbol) so the pure side can test the `_pvtSymToStr` fallback path.

- `pidSweepCache` entries hold the raw `(name value) ...` pair list returned by `(maeGetParamConditions pid)`. Pure side calls `_pvtCollAssocToTable` itself, so the pure-side test exercises that wrapper too.

- `cornerParamCache` entries hold whatever `(funcall cornerObj->params)` returned (raw POC-shape list of `'(group modelName ...)` records); pure side runs `_pvtCollParseCornerParams` over it. Cache is keyed by cornerName so a "_no_cache" / "_parse_error" marker is generated identically in the pure side.

- `testNoteCache` entries: store `nil` if `axlGetNote` returned nil, the literal string otherwise. The pure side does the same `(or note "")` flattening currently at lines 645/716.

**Why pre-resolve the test/corner objects on the live side rather than passing axlrdbo handles through.** Because the funobj-call rule (Decision #16) means slot access only works inside Virtuoso. If we leaked a `axlrdbo` into the tuple list, the pure side would have to do `(funcall x->corner)` to get cornerObj — that's exactly the boundary leak we're avoiding. So `_pvtCollWalkRdb` does the resolution; the pure side sees only base values + already-walked params lists.

**Failures in walk.** `_pvtCollWalkRdb` retains the existing `'pvt_collect_iter` `pvtErr` for "history not found" (current line 551–554). Other live throws stay swallowed-to-nil via `_pvtCollEvalThunk`, exactly as today — so the silent-failure surface does not grow.

### 1.3 `_pvtCollRowsFromTuples(walkData)` contract

Returns `(firstTestName . listOfRows)` — same shape `_pvtCollIterateResults` returns under `pvtOk` today (line 770). Pure SKILL: only uses arithmetic, string ops, `arrayref`/`setarray` on locally-constructed makeTables, and the existing helpers `_pvtCollAssocToTable`, `_pvtCollParseCornerParams`, `_pvtCollMakeRow`, `_pvtSymToStr`. **No** `funcall`, **no** `axl*`/`mae*` calls, **no** `errset` (errors here would be programming bugs that the test should surface, not silent-swallow).

### 1.4 Where do the 3 passes live?

**Confirmed: all three passes move into `_pvtCollRowsFromTuples`.** This is the right cut because:

- Pass 1 (ok rows): pure shaping over `walkData->outputs` plus cache consultation. Already takes only values today.
- Pass 2 (failed/running): pure shaping over `walkData->tests`. The `'done` filter, the `_pvtSymToStr` mapping, and the line-679 fallback string are all pure decisions on a `statusSym`.
- Pass 3 (no_convergence): walks `keysSeen` (built during pass 1) against `writtenSet` (also built during pass 1). Both data structures are pure-side artifacts; nothing to do with the live DB.

Cache lifetime stays per-`PvtSave` invocation as Decision in S3_DESIGN.md §3 specifies — the caches are now lexical to the pure function instead of the iterate proc, but the lifetime is identical (one walk → one pure call → both die together).

### 1.5 Pseudocode — old vs new shape

```
;; OLD:  _pvtCollIterateResults
;;   live-DB-open + count-points + (3 passes interleaved with caches and live calls)

;; NEW:  _pvtCollIterateResults
;;   walkR  = _pvtCollWalkRdb(sess histName hsdb)
;;   if pvtIsErr return walkR
;;   walk   = pvtUnwrap walkR
;;   return (pvtOk (_pvtCollRowsFromTuples walk))

;; NEW:  _pvtCollWalkRdb(sess histName hsdb)
;;   open rdb, count points, open results          [live]
;;   unwindProtect:
;;     for pid 1..totalPoints:
;;       sweepRaw[pid]  ← (maeGetParamConditions pid)         [live]
;;       for out in (funcall (pt->outputs) ?type 'expr):     [live, funcall rule]
;;         out tuple ← (pid cornerName testName outName outValue)
;;         if cornerName not in cornerParamCache:
;;           cornerParamCache[cname] ← (funcall cornerObj->params)
;;         if testName not in testNoteCache:
;;           testNoteCache[tname]    ← (axlGetNote hsdb "test" tname)
;;     for tst in (funcall (rdb->tests)):                     [live, funcall rule]
;;       testTuple ← (pid cornerName testName status)
;;       (also populate sweep / corner / note caches if not yet — pass-2-only paths)
;;   cleanup: maeCloseResults
;;   return walkData

;; NEW:  _pvtCollRowsFromTuples(walkData)
;;   pass 1: for each outputTuple:
;;     seenSet[cname|pid] ← (tname pid cname sweep)
;;     keysSeen ← cons key keysSeen
;;     if numberp(val) or (stringp and != "wave"):
;;       rows ← cons (_pvtCollMakeRow ... "ok" ...) rows
;;       writtenSet[key] ← t
;;       if firstTestName nil: firstTestName ← tname
;;   pass 2: for each testTuple where status != 'done:
;;     statusStr ← (cond ...) — see §3 below for fix
;;     rows ← cons (_pvtCollMakeRow ... statusStr ...) rows
;;   pass 3: for key in (reverse keysSeen) where (null writtenSet[key]):
;;     rows ← cons (_pvtCollMakeRow ... "no_convergence" ...) rows
;;   return (firstTestName . (reverse rows))
```

---

## 2. Synthetic test scenarios

For each TODO bullet, I give the synthetic `walkData` to feed `_pvtCollRowsFromTuples` and the assertion. Tuple notation: `(pid corner test outName outVal)` for outputs; `(pid corner test status)` for tests.

I assume a tiny "fixture builder" SKILL helper `_pvtCollTestMakeWalk` that takes the four cache lists + outputs + tests + totalPoints and assembles a `walkData` table — counterpart to the existing `_pvtCollTestSampleRunMeta` helper at `testPvtCollect.il:152`.

### Scenario A — TODO bullet #1: partial-convergence corner

```
outputs:
  (1 "TT"     "tran" "vout" 1.234)        ; converged
  (1 "TT"     "tran" "delay" 42e-12)      ; converged
  (1 "WC_VDD" "tran" "vout" "wave")       ; NEWTON failed: only "wave" emitted, no numeric
tests:
  (1 "TT"     "tran" 'done)
  (1 "WC_VDD" "tran" 'done)               ; status='done at the test level even though one corner didn't converge per output
totalPoints: 1
```
**Expected rows** (after reverse): two "ok" rows for TT + one `__sim_status__` row with `status="no_convergence"` for `(WC_VDD,1)`. Three rows total. Pass-3 fires because key `WC_VDD|1` is in `seenSet` (the "wave" output added it) but never in `writtenSet`.
**Tier-1 test name:** `collect/rows-partial-converge-emits-no-convergence`.

### Scenario B — TODO bullet #2: mid-flight `PvtSave`

```
outputs:
  (1 "TT" "tran" "vout" 1.0)              ; tran completed
tests:
  (1 "TT" "tran"   'done)
  (1 "TT" "noise"  'running)              ; noise still running, no outputs yet
totalPoints: 1
```
**Expected rows:** one "ok" row for `tran` + one `__sim_status__` row with `status="running"`, `output="__sim_status__"`, `value=PVT_JSON_NULL`, `test="noise"`. Two rows.
**Tier-1 test name:** `collect/rows-mid-flight-running-sentinel`.

### Scenario C — TODO bullet #3: a `'failed` test

```
outputs:
  (1 "TT" "tran" "vout" 1.0)
tests:
  (1 "TT" "tran"     'done)
  (1 "TT" "spectreX" 'failed)             ; crashed
totalPoints: 1
```
**Expected:** one "ok" + one `status="failed"`, `output="__sim_status__"` row.
**Tier-1 test name:** `collect/rows-failed-test-sentinel`.

### Scenario D — TODO bullet #4: unfamiliar status symbol

Two sub-cases; both check the cond-arm at line 678–679.

D1 — known-symbol passthrough via `_pvtSymToStr`:
```
tests: ((1 "TT" "tran" 'aborted))
expected: status="aborted"
```
D2 — non-symbol status (the line-679 bug):
```
tests: ((1 "TT" "tran" nil))
current behaviour: status="running"   ; SILENTLY MISCLASSIFIED — see §3
```
**Tier-1 test names:** `collect/rows-aborted-symbol-passthrough`, `collect/rows-non-symbol-status-is-unknown` (the second test asserts the *fixed* "unknown" mapping; it will fail until §3 fix lands).

### Scenario E — TODO bullet #5: gap in point-ID sequence

This one is the nasty case — the gap is detected during the *walk*, not the shaping. Two complementary tests:

E1 — Tier-1 (pure side) demonstrates that *if* the walker passes `walkData` with `totalPoints=4` and outputs at pids 1,2,4 (with the gap), shaping produces 3 rows correctly:
```
outputs: ((1 "TT" "t" "v" 1) (2 "TT" "t" "v" 2) (4 "TT" "t" "v" 4))
totalPoints: 4
expected: 3 ok rows, point fields 1/2/4
```
This proves the pure side is gap-tolerant. Tier-1 name: `collect/rows-gappy-pids-shape-correctly`.

E2 — The walker fix (§3 below) is verified at Tier-2 against a real session. Mark a TODO note in the test file: "walker-side gap detection requires Tier-2 verification."

### Scenario F — TODO bullet #6: per-output convergence inside a converged test

```
outputs:
  (1 "TT" "tran" "delay" 42e-12)         ; converged
  ; "vout" expression failed to evaluate — does pt->outputs even enumerate it?
tests:
  (1 "TT" "tran" 'done)
totalPoints: 1
```
**Expected:** This scenario *cannot be fully covered by Tier-1 alone* — the answer to "does `pt->outputs` enumerate the failed expression" is a live-side question. But Tier-1 *can* verify the contrapositive: if the walk *did* enumerate the failed expression with `value="wave"` or value=nil, pass-3 fires; if it didn't, the row is silently dropped.

Two synthetic variants:
F1 — failed expression returns `"wave"`:
```
outputs: ((1 "TT" "tran" "vout" "wave") (1 "TT" "tran" "delay" 1.5))
expected: 1 ok ("delay") + ZERO sentinels, because "wave" added (TT|1) to seenSet AND `delay` then writes it. So no_convergence is correctly suppressed.
```
F2 — failed expression returns nil and is filtered out before reaching the tuple list:
```
outputs: ((1 "TT" "tran" "delay" 1.5))   ; only the converged one
expected: 1 ok row, no sentinel — silent miss is the documented limitation.
```
**Tier-1 test names:** `collect/rows-mixed-converge-wave-suppresses-sentinel`, `collect/rows-pure-shaping-cannot-detect-dropped-output`. The second is documentation-as-test: it asserts the behaviour and links a TODO comment to the §6 risk inventory below.

---

## 3. Bugs noticed while reading

### Bug A (TODO-flagged): line 679 fallback is "running" — should be "unknown"

`pvtCollect.il:678–679`:
```
((symbolp tstStatus)        (_pvtSymToStr tstStatus))
(t                          "running")))
```
A nil status, an integer, or a list silently becomes `"running"`. **Fix:** change `"running"` to `"unknown"`. Cross-reference: schema.md §2.2 line 79 already says "Closed set: ok | failed | running | no_convergence. Any other value is a hard error at ingest." So `"unknown"` would itself be a schema violation — which is what we want, because the validator (part d) will catch it and raise. That's the right flow: collector emits a faithful sentinel, validator screams.

**Caveat:** if we want to keep the closed-set guarantee at collector time, the alternative is to map non-symbol → `"failed"` (treat as a known-bad case). I prefer `"unknown"` + validator-catches because it preserves the diagnostic information; if non-symbol status was actually our `axlrdbt->status` slot returning nil for a benign case (e.g. test never ran), conflating with `"failed"` lies about the run. Recommend: emit `"unknown"`, document in schema.md that the validator will reject it, fix the upstream cause.

### Bug B (TODO-flagged): gappy-pid count loop at line 558

```
(while (_pvtCollEvalThunk (lambda () (funcall (rdb->point) pid)))
  (setq totalPoints (plus totalPoints 1))
  (setq pid (plus pid 1)))
```
First nil exit ends the count. POC has the same bug. **Fix candidates:**

- **(B1) Probe ahead.** Walk `pid 1..N` for some safe upper bound `N` (e.g., 1024) and count non-nil. Cheap, but `N` is arbitrary.
- **(B2) Discover pids via `(rdb->tests)` then `tst->pointID`.** The test list is the canonical roster of (test, point) pairs. Build the pid set from `set(tst->pointID for tst in tests)`. No probing, no upper bound. **Recommend B2** — it's also robust against pid 0 vs pid 1 origin questions.
- **(B3) Loop until two consecutive nils.** Heuristic, fragile.

Pick (B2). Implementation note: it changes `totalPoints` from "number of sequential pids" to "number of distinct pids", which is what we actually want for pass 1's loop. The pass-1 loop becomes `foreach pid pidList` instead of `while leqp pid totalPoints`. This is a walk-side change; the pure side just consumes whatever pid numbers arrive in the output tuples.

### Bug C (new finding): pass-2 emits a sentinel even when an ok row exists for the same (test, corner, pid)

`_pvtCollIterateResults` pass 2 (lines 663–729) iterates `(rdb->tests)` and emits a sentinel for every test where `status != 'done`. There is **no check against `writtenSet`**. Consider:

```
outputs: ((1 "TT" "tran" "vout" 1.0))     ; pass 1 emits an ok row
tests:   ((1 "TT" "tran" 'running))       ; pass 2 ALSO emits a sentinel
```

Result: row count = 2 for the same (project, run_id, test, corner, point) — exactly the invariant the validator forbids ("≥1 ok row OR exactly one __sim_status__, never both").

This is plausible in a mid-flight `PvtSave`: the test's tran analysis finished and produced `vout`, but `tst->status` is still 'running because dc/noise haven't finished. We'd publish a "running" sentinel alongside an "ok" row.

**Fix:** in pass 2, build `seenKey = sprintf "%s|%d" cname pid` and `(cond ((arrayref writtenSet seenKey) ...skip...) ...emit...)`. Alternatively, only emit when *no* output for this (test, corner, pid) was written. Tier-1 test: `collect/rows-pass2-skips-when-pass1-wrote`.

### Bug D (new finding): pass 2 corner_vars cache fallback on cornerObj=nil writes "_parse_error"; pass 3 uses "_no_cache"

Lines 703–705 and 749–751 use **different** fallback markers. They should be a single marker (or two well-defined ones) so the validator and ingester have a consistent "this corner didn't get vars resolved" signal. Schema.md §2.2 declares `corner_vars: object`; a `{"_parse_error": "TT"}` is a legal-shape object but a magic key, and the validator (part d) should flag it.

**Fix:** unify on `_no_corner_vars` (single marker), and validator warns when present (not error — it's a degraded but valid state).

### Bug E (style, not bug): `errset (maeOpenResults ...) nil` at line 571 has its return value discarded

If `maeOpenResults` failed (e.g., results already open from a prior abandoned call) we silently continue — but maeCloseResults still fires in unwindProtect. Probably benign. Not in scope to fix here; flag for a Tier-2 verification cycle.

---

## 4. Validator design — `python/simkit/validate.py`

### 4.1 Invariants extracted from `docs/schema.md`

Headline (TODO §3):
- **I1 — Triple coverage:** every `(project_id, run_id, test, corner, point)` triple has either ≥1 row with `status="ok"` and `output != "__sim_status__"`, **or** exactly one row with `output == "__sim_status__"` and `status ∈ {failed, running, no_convergence}`. Never both, never neither.

From schema §2 (`run` object):
- **I2 — `run_id` is UUIDv4.** Same regex as the SKILL `_pvtCollIsUuidV4` at `pvtCollect.il:110`.
- **I3 — `project_id` matches `^[a-z0-9_-]+$`** (DECISIONS #6 / schema §1).
- **I4 — `testbench_id` matches `lib/cell/view`** (three slash-separated non-empty tokens).
- **I5 — `testbench_alias` is str or null.** When non-null, must be a non-empty string.
- **I6 — `timestamp` matches** `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$`. (Same regex `pvtCollect.il:167`.)
- **I7 — `author` is non-empty string.**
- **I8 — `label` is str or null.**
- **I9 — `note` is str or null.**
- **I10 — `netlist_path` is str (per schema "All fields are required unless marked | null") OR null.** Schema marks it without `| null`, but the collector emits `null` on soft-miss (line 869). Schema is currently inconsistent with the implementation. Validator should accept null and emit a *warning*, not an error, until schema.md is reconciled. Flag this as Decision-needed.
- **I11 — `history_name` is non-empty string.**

From schema §2.2 (`results[]`):
- **I12 — `status` ∈ closed set `{ok, failed, running, no_convergence}`.** Hard error otherwise (line 79).
- **I13 — `output` is non-empty string.** Sentinel `"__sim_status__"` is allowed.
- **I14 — `value` typing:** when `status == ok`, `value` is number OR str (and `output != "__sim_status__"`). When `status != ok` AND `output == "__sim_status__"`, `value == null`. Schema §2.2: "null when status != ok".
- **I15 — `point` is non-negative int.**
- **I16 — `corner`, `test` non-empty strings.**
- **I17 — `sweep`, `corner_vars` are objects (dicts).**
- **I18 — `test_note` is str or null.**

From schema §2.3 (`artifacts[]`) — empty in v1 collector but include for forward-compat:
- **I19 — `artifacts[].type` ∈ closed set** `{waveform, results_table, sim_log, schematic, netlist_diff, image, pdf, other}`.
- **I20 — `artifacts[].source` ∈ `{auto, manual}`.**
- **I21 — `artifacts[].relative_path` non-empty.**
- **I22 — `artifacts[].created_at` ISO 8601 (regex from I6).**

Top-level:
- **I23 — `schema_version == 1`** (integer, not string).
- **I24 — Top-level keys exactly `{schema_version, run, results, artifacts}`.** Order is irrelevant in JSON; presence is the assertion.

Soft warnings (do not fail):
- **W1 — Magic markers in `corner_vars`:** keys starting with `_` (e.g., `_no_corner_vars`, `_parse_error`, `_no_cache` from Bug D). Validator surfaces, ingester decides whether to reject.
- **W2 — `netlist_path == null`:** soft-miss path (schema says required, collector emits null on Spectre-detect failure). Schema/impl mismatch flagged.

### 4.2 Module structure

```
python/simkit/validate.py

  @dataclass(frozen=True)
  class Violation:
      code: str            # "I1", "I12", etc.
      severity: Literal["error", "warning"]
      path: str            # "results[42].status" / "run.run_id"
      message: str

  def validate_dump(dump: dict) -> list[Violation]:
      """Pure function: parsed JSON dict in, list of violations out.
         Empty list = valid."""

  def validate_dump_file(path: Path) -> list[Violation]:
      """Convenience wrapper: load + parse + validate."""

  # Per-invariant helpers (private), each takes (dump, violations) and appends:
  def _check_top_level(dump, violations): ...
  def _check_run_meta(dump, violations): ...
  def _check_results(dump, violations): ...
  def _check_triple_coverage(dump, violations): ...   # I1 — the headline
  def _check_artifacts(dump, violations): ...

  # Optional CLI:
  if __name__ == "__main__":
      sys.exit(main(sys.argv[1:]))
```

Pure-functional reasons: testability mirrors the SKILL refactor (TODO bullet — the symmetry is intentional). Returning a list of violations rather than raising lets the ingester decide policy.

### 4.3 Independent CLI vs ingester hook

**Both, with the validator as the single source of truth.** Proposal:

- **Library API:** `validate_dump(dump_dict) -> list[Violation]`. Pure. Always available.
- **Ingester contract** (for the §4 ingester planner being written concurrently): the ingester calls `validate_dump_file(path)`. If any violation has `severity=error`, the ingester rejects the dump and refuses to load the run; if only warnings, the ingester loads but logs each warning (with the existing logging infra). This makes the validator a hard quality gate at ingest time without coupling the validator to any DB.
- **Independent CLI:** `pvt validate <dump.json>` (added to the §5 `pvt` CLI later — and earlier as `python -m simkit.validate <path>` until then). Useful for:
  - SKILL-side smoke after a Tier-2 run, before driving ingest.
  - Bulk pre-check across an existing dump dir without DuckDB writes.
  - CI / regression fixtures (the 42-row 2026-05-10 real-run JSON becomes a "must-pass" fixture).
- **Exit codes:** 0 = clean, 1 = warnings only, 2 = errors. This lets shell pipelines (e.g., a future `pvt save && pvt ingest` chain) decide policy.

### 4.4 Cross-file invariants the validator does *not* check

- The DuckDB-side uniqueness pair `(run_id)` (only checkable at ingest).
- File existence of `netlist_path` / `artifacts[].relative_path` (would require resolving against `<dbRoot>/runs/<run_id>/`; validator stays JSON-only, ingester does the file-existence pass).
- Cross-run consistency (e.g., same `project_id` always pairs with same set of `corner` names) — out of scope; this is a per-dump invariant validator.

---

## 5. Tier-1 test additions

Currently 96 SKILL test registrations across the three test files (20 in testPvtCollect, 55 in testPvtJson, 23 in testPvtProject). Assertions fan out — reported "168" is assertion-level. New work adds ~22 SKILL test registrations in `testPvtCollect.il`, plus a new Python `tests/test_validate.py` module (~30 unittest cases).

### 5.1 SKILL Tier-1 (add to `skill/tests/testPvtCollect.il`)

`_pvtCollRowsFromTuples` shape & happy-path:
1. `collect/rows-empty-walk-returns-nil-firstTest-and-empty-rows`
2. `collect/rows-single-ok-output-emits-ok-row`
3. `collect/rows-multiple-points-multiple-corners-cross-product`
4. `collect/rows-string-value-non-wave-emits-ok` (e.g., `"PASS"`)
5. `collect/rows-string-value-wave-skipped`
6. `collect/rows-firstTestName-is-first-pass1-write-not-first-tuple`
7. `collect/rows-test-note-empty-string-becomes-null`
8. `collect/rows-test-note-non-empty-string-passes-through`
9. `collect/rows-corner-vars-cache-hit-second-corner-reuses`
10. `collect/rows-key-order-on-row-makeTable-matches-schema`

Sentinel paths (the 6 TODO scenarios):
11. `collect/rows-partial-converge-emits-no-convergence` (Scenario A)
12. `collect/rows-mid-flight-running-sentinel` (Scenario B)
13. `collect/rows-failed-test-sentinel` (Scenario C)
14. `collect/rows-aborted-symbol-passthrough` (Scenario D1)
15. `collect/rows-non-symbol-status-is-unknown` (Scenario D2 — fails until Bug A fix)
16. `collect/rows-gappy-pids-shape-correctly` (Scenario E1)
17. `collect/rows-mixed-converge-wave-suppresses-sentinel` (Scenario F1)
18. `collect/rows-pure-shaping-cannot-detect-dropped-output` (Scenario F2 — documents limitation)

Bug-fix verification:
19. `collect/rows-pass2-skips-when-pass1-wrote` (Bug C)
20. `collect/rows-pass3-fallback-marker-is-no-corner-vars` (Bug D — after fix)

Composer:
21. `collect/iterate-results-error-from-walk-propagates`
22. `collect/iterate-results-ok-path-composes-walk-and-rows`

A small fixture builder helper `_pvtCollTestMakeWalk(outputs tests pidSweepCache cornerParamCache testNoteCache totalPoints)` lives in the test file, mirroring `_pvtCollTestSampleRunMeta`.

**New count estimate:** 22 SKILL test registrations → ~50–80 new assertions. Cumulative total ~218–248.

### 5.2 Python validator tests — `tests/test_validate.py`

Mirror coverage at the invariant level. ~30 unittest cases:

- One pass case (the 2026-05-10 42-row real-run JSON — request and check it in as `tests/fixtures/run_full_converge.json`).
- One per error invariant I1–I24 (negative test: deliberately corrupt the dump in one place, assert the right violation code surfaces, and that no others spuriously fire).
- One per warning W1, W2.
- A composite "broken in 5 ways at once" test that verifies all 5 violations report (no fail-fast).

---

## 6. Risk inventory

### 6.1 Boundary leaks I'm worried about

- **`maeGetParamConditions(pid)` is a live call** but its **return value** (an assoc list of `(name value)` pairs) is data. The walk side stages it as `pidSweepCache`; the pure side consumes it. Clean cut. Risk: low.
- **`(funcall cornerObj->params)` returns POC-shape `(group modelName ...)` lists.** The walk side stages it as `cornerParamCache[cname]`; the pure side feeds it through the existing `_pvtCollParseCornerParams`. Clean cut. Risk: low.
- **`axlGetNote hsdb "test" tname` is a live call** producing a string-or-nil. Stage as `testNoteCache[tname]`. Clean cut. Risk: low.
- **`'done` symbol comparison in pass 2.** `equal tstStatus 'done` works in pure SKILL once `tstStatus` is staged as a symbol value. The test fixture must `setarray` the symbol literal `'done`, not the string `"done"`. **Document this in the fixture builder**, otherwise tests will spuriously pass/fail on type confusion. Risk: medium — landmine for the test author.
- **`firstTestName` selection.** Today, `firstTestName` is set on the *first ok-row write* (line 661). It's the test the netlist-copy uses. The pure side preserves this. But if pass 1 emits zero ok rows (e.g., all "wave" outputs), `firstTestName` is nil and the netlist-copy soft-misses — which is the existing behaviour. Add a test asserting this. Risk: low.

### 6.2 Hidden state dependencies

- **`unwindProtect` + `maeCloseResults`** is live-only and stays in `_pvtCollWalkRdb`. The pure side has no parallel cleanup obligation. No leak.
- **`_pvtCollEvalThunk` (line 523).** Stays in walk only. The pure side does not need it: any throw inside the pure side is a programming bug, not a "live API returned nil for benign reason." Forbid `errset` in `_pvtCollRowsFromTuples`. Risk: medium — convention has to be enforced by code review since SKILL has no module system to enforce it.
- **`(rdb->tests)` ordering is implementation-defined.** Pass 2's emit order depends on whether tests come back grouped by point or by name. The pure side reproduces whatever order the walk side observed. Tests should not depend on tst-order. Risk: low after explicit non-dependency.
- **`keysSeen` is built head-first then reversed at line 735.** Order of pass-3 sentinel rows therefore matches insertion order (pass-1 walk order). Preserved in the new design. Risk: low.

### 6.3 Things I'm uncertain about — flag for verification

- **Funobj closure stability across pid loops.** Decision #16 says `(rdb->point)` returns a funobj closure that takes `pid`. But within `_pvtCollWalkRdb`, do we need to re-fetch `(rdb->point)` each iteration, or is the funobj reusable? The current code (line 558, 579) re-fetches via `(_pvtCollEvalThunk (lambda () (funcall (rdb->point) pid)))` — implying re-fetch. Probe via skillbridge: `ws.eval_str("setq fn (rdb->point); funcall(fn, 1)")` then `funcall(fn, 2)` to confirm reuse-vs-refetch. If reusable, `_pvtCollWalkRdb` can hoist the funobj out of the pid loop (perf gain). If not, current pattern stays. Functions to verify: `rdb->point` reusability, `pt->outputs` reusability.
- **Bug B fix (B2 — pid set from tests).** Need to verify on the live session that `tst->pointID` is populated for every test (not just for `'done` ones). If it's nil for `'running` tests, B2 still works for already-completed tests but undercounts mid-flight. Probe: `mapcar (lambda (t) t->pointID) (funcall (rdb->tests))` on a running session. Functions to verify: `axlrdbt->pointID` for non-done tests.
- **Bug C fix interaction with Scenario B.** If pass 2 skips when pass 1 wrote, Scenario B's "ok row + running sentinel" expectation needs adjustment: when *all* outputs of a running test produced ok values, we suppress the sentinel and lose the "this test was running" signal. Two interpretations:
  - (a) The test had >0 outputs evaluate, so it's "ok enough" — suppress sentinel.
  - (b) The test status is canonical; even with partial ok, we want the sentinel.
  Pick (a) — the validator's I1 ("≥1 ok OR exactly one sentinel") already encodes the user-facing rule. Document this choice in DECISIONS.md (new entry).

### 6.4 The biggest uncertainty

Whether bullet F (per-output convergence inside a converged test) is even *detectable* from any inputs we can produce. If `pt->outputs` doesn't enumerate failed expressions at all, the row never enters the system and pass 3 has nothing to flag. The cure is upstream — call `axlGetExprList` (or whatever the current Cadence canonical is) to get the *intended* expression list, diff against the actually-emitted ones. This is **out of scope** for this plan but should be a TODO bullet.

---

## 7. Order of operations

The user reviews diffs, not code. Sequence the diffs to keep each one independently reviewable, and to keep the system runnable at every step.

**Step 1 — Walk extraction (no semantic change).** Add `_pvtCollWalkRdb` *alongside* the existing `_pvtCollIterateResults`. New function returns `walkData`. Do not call it yet. Adds Tier-1 test `collect/walk-shape-smoke` that asserts `walkData` has the documented keys (using a stub-rdb the test constructs — but only if we want zero-live coverage; otherwise this step has no Tier-1 test and ships pure documentation). No behaviour change.

*Diff size: ~120 lines added. Risk: low. Reviewable.*

**Step 2 — Rows-from-tuples extraction (no semantic change).** Add `_pvtCollRowsFromTuples(walkData)`. **Copy** (not move) the three passes' shaping logic out of `_pvtCollIterateResults` into the new pure function. The old function still owns the live walks; it now constructs a `walkData` value internally and calls the new pure function for shaping. Result: behaviour-identical to before, but the pure function now exists and is callable from tests. Add the 22 Tier-1 tests above.

*Diff size: ~250 lines (new function + tests). Risk: medium — the copy must preserve every cond-arm faithfully. Reviewable per-pass: 3 sub-commits if needed (pass-1 / pass-2 / pass-3).*

**Step 3 — Swap caller.** Rewrite `_pvtCollIterateResults` to the 5-line composer pseudocode in §1.5. Delete the inlined live walk *and* the inlined shaping in one diff. With Step 2 already in place, this is a pure reduction. Run full Tier-1 (expect green). Run Tier-2 against the 2026-05-10 verification history (expect 42 ok rows + correct envelope, byte-identical to the post-fix baseline).

*Diff size: −~150 lines. Risk: medium — Tier-2 verification is mandatory before merging this step (per DECISIONS #16). Reviewable.*

**Step 4 — Bug fixes A, B, C, D as separate commits.** Each lands with its specific Tier-1 test (15, B-test, 19, 20 from §5.1). Each is small (5–20 lines). User can revert any one in isolation if the live session disagrees.

- 4A: line 679 `"running"` → `"unknown"` + test 15.
- 4B: walker pid-set from `(rdb->tests) → tst->pointID` + verification probe + test 16 still passing.
- 4C: pass-2 `writtenSet` skip + test 19.
- 4D: unify fallback marker on `_no_corner_vars` + test 20.

Each commit is independent, but order matters slightly: do 4A and 4D before validator (because validator's I12 and W1 will reject the old behaviour); do 4B and 4C after Tier-2 re-verification, because they could shift live row counts on the next sim.

**Step 5 — Python validator.** New file `python/simkit/validate.py` + `tests/test_validate.py` + the 2026-05-10 real-run JSON checked in as a passing fixture. No changes to existing code. Independent of Steps 1–4 — could in principle land first, but its tests will exercise Bug A, C, D fixes (the 2026-05-10 fixture happens to contain only ok rows, so it passes regardless; but synthetic broken-fixtures will exercise the fixed paths).

*Diff size: ~400 lines (300 validator + 100 tests). Risk: low — pure-Python.*

**Step 6 — Wire validator into ingester.** *Deferred to the §4 ingester planning.* This plan defines only the contract (§4.3). The ingester planner reads `validate_dump_file` and decides where to call it (likely: right after JSON parse, before any DuckDB write). No work for this plan beyond writing the contract down.

**Step 7 — Documentation churn.** Append DECISIONS.md entry #17 — "Collector results-iteration split into walk + pure shaping; pure half is Tier-1 testable." Append the (a)+(d) decision rationale (paraphrasing TODO §3 reasoning). Update PROJECT_STATE.md timeline. Update TODO.md §3: tick the bullet, link to the new tests. Update schema.md if Bug A's "unknown" mapping needs to be explicitly forbidden (it will be — add a note to §2.2).

*Diff size: docs only.*

**Total project effort estimate:** half a session for Steps 1–3 (clean refactor, no behaviour change), half a session for Step 4 (four small fixes), half a session for Step 5 (validator), one Tier-2 verification at the end. Total ≈ 2 sessions, matching the TODO bullet's "half a session" claim per approach (the TODO underestimated by counting only (a); (d) adds another half).

---

### Critical Files for Implementation

- /home/yusheng/cadence_work/Test/workarea/simkit/skill/pvtCollect.il
- /home/yusheng/cadence_work/Test/workarea/simkit/skill/tests/testPvtCollect.il
- /home/yusheng/cadence_work/Test/workarea/simkit/python/simkit/validate.py (new)
- /home/yusheng/cadence_work/Test/workarea/simkit/tests/test_validate.py (new)
- /home/yusheng/cadence_work/Test/workarea/simkit/docs/schema.md
