# S3 Design — `skill/pvtCollect.il`

**Status:** design landed 2026-05-08, awaiting implementation. This is a working document — once S2b lands the code, S4 may fold relevant parts into PROJECT_STATE / DECISIONS and remove the rest.

**Reader audience:** the implementer of S2b (Phase 1 §3 collector).

---

## Module map (call graph)

```
PvtSave(?histName ?label ?note ?captureScreenshot)
   │
   ├── pvtLoadPvtProject()                       [external — pvtProject.il]
   │       └─> proj  (pvtProject defstruct: project, dbRoot, author, testbenchAliases)
   │
   ├── _pvtCollResolveSession(?histName)         [helper — wraps S3-probe §1+§4 + group detect]
   │       └─> (sess hsdb histID histName)        OR pvtErr 'pvt_collect_session
   │
   ├── _pvtCollAutoCapture(proj, sess, histID, histName, note)
   │       ├── _pvtCollNewRunId()                 [§1]
   │       └── _pvtCollIsoTimestamp()             [helper for design Q-G]
   │       └─> runMeta table  (schema §2.1 keys, ordered via sidecar)
   │
   ├── _pvtCollMakeRunDir(dbRoot, runId)         [§6]
   │       └─> absRunDir   OR pvtErr 'pvt_collect_io
   │
   ├── _pvtCollIterateResults(sess, histName, hsdb)   [§3]
   │       └─> (firstTestName . listOfRowTables) OR pvtErr 'pvt_collect_iter
   │
   ├── _pvtCollCopyNetlist(sess, histID, firstTestName, absRunDir)   [§4]
   │       └─> "input.scs" | nil   (warn-soft, never errors)
   │
   ├── _pvtCollCaptureScreenshot(absRunDir, requested?)   [§5 — v1 stub]
   │       └─> always nil; warn-once first time invoked with requested=t
   │
   ├── _pvtCollBuildEnvelope(runMeta, results, netlistPath, artifacts)   [§7]
   │       └─> envelope table (4 keys: schema_version, run, results, artifacts)
   │
   └── _pvtCollWriteRunJson(envelope, absRunDir)         [§8]
           └─> absJsonPath   OR pvtErr 'pvt_collect_io
```

## Public API

```
PvtSave(?histName        ; t_string  | absent => current history
        ?label            ; t_string  | absent => null in JSON
        ?note             ; t_string  | absent => null in JSON
        ?captureScreenshot)  ; t/nil  | default nil
   -> (pvtOk t_absJsonPath) | pvtErr
```

`author` is **not** a parameter — it comes from `proj->author` if set, else `(getShellEnvVar "USER")`, else literal `"unknown"`. Matches schema §2.1 "captured at dump time" intent and the C0.3 director decision.

`PvtSave` returns the wrapped form `(pvtOk path)` on success to stay consistent with `pvtLoadPvtProject` and the rest of the public surface; CLI shim layers can unwrap via `pvtRaise`.

---

## Internal modules

### 1. `_pvtCollNewRunId`

- **Inputs:** none.
- **Output:** `(pvtOk t_uuidString)` — RFC 4122 v4 string, 36 chars `xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx`. **Never** trailing `\n`.
- **Side effects:** spawns one `uuidgen` process via `ipcBeginProcess`, blocks via `ipcWait`, reads stdout via `ipcReadProcess`. On failure of either step, falls through to the `random()` bit-twiddle fallback (no error returned).
- **Errors raised:** none in v1.
- **Depends on:** nothing.
- **Test points for S3:**
  - Returns a string of length exactly 36.
  - Matches `^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$` (verify via `pcreMatchp`).
  - Two consecutive calls return different values.
  - Trailing `\n` is stripped (regression for the `ipcReadProcess` raw-output trap in S3_PROBE_RESULTS.md §5).

### 2. `_pvtCollAutoCapture(proj, sess, histID, histName, note)`

- **Inputs:**
  - `proj` — pvtProject defstruct (immutable).
  - `sess` — t_sessionName (string from `axlGetWindowSession`).
  - `histID`, `histName` — already resolved by `_pvtCollResolveSession`.
  - `note` — t_string | nil.
- **Output:** `(pvtOk t_runMetaTable)`. The table has schema §2.1 keys in emit-order, plus an internal `history_id` slot used downstream. Sidecar `_PVT_JSON_KEYS_PROP` populated. **`label` slot is set to `PVT_JSON_NULL`** (schema mandates null at dump time per Decision #11). `netlist_path` slot is pre-populated with `PVT_JSON_NULL` so build-envelope can overwrite without re-keying the sidecar.
- **Side effects:** ADE-XL reads only — `axlGetSessionLibName/CellName/ViewName`; `pvtProjectAliasFor` (in-memory).
- **Errors raised:**
  - `'pvt_collect_session` "ADE-XL session has no active library/cell/view (lib=%L cell=%L view=%L)" if any of the three L/C/V calls returns nil.
- **Depends on:** `_pvtCollNewRunId`, `_pvtCollIsoTimestamp`, `pvtProjectAliasFor`.
- **Test points for S3:**
  - `testbench_id` is exactly `<lib>/<cell>/<view>` joined by literal `/`.
  - `testbench_alias` is the matching alias from `proj->testbenchAliases`, OR `PVT_JSON_NULL` (note: NOT SKILL `nil`) if no entry.
  - `project_id` equals `proj->project`.
  - `author` precedence: `proj->author` > `$USER` > `"unknown"`.
  - `label` is always `PVT_JSON_NULL` at dump time.
  - `note` propagates verbatim (or `PVT_JSON_NULL` if caller passed nil).
  - `history_name` is the string actually used (not the handle).

### 3. `_pvtCollIterateResults(sess, histName, hsdb)`

- **Inputs:** session string, history name string, hsdb handle.
- **Output:** `(pvtOk (firstTestName . listOfRowTables))`:
  - `firstTestName` is needed by `_pvtCollCopyNetlist`. Empty result-set ⇒ `firstTestName = nil` (caller skips netlist copy).
  - `listOfRowTables` — proper list of makeTable rows, each with sidecar key list `[point, corner, test, output, value, status, sweep, corner_vars, test_note]` per schema §2.2 order.
- **Side effects:**
  - `maeReadResDB(?historyName histName ?session sess)` — reads result DB.
  - `maeOpenResults(?history histName ?session sess)` and `maeCloseResults()` (wrap in `unwindProtect`; close on every exit path).
  - `axlGetNote(hsdb, "test", testName)` once per distinct test (cached in lexical `makeTable`).
- **Errors raised:**
  - `'pvt_collect_iter` "history %L not found in session %L" if `maeReadResDB` returns nil.
- **Depends on:** internal helpers `_pvtCollParseCornerParams`, `_pvtCollAssocToTable`, `_pvtCollMakeRow`.
- **Loop structure** (port from POC `MyRunner/PvtDumpToJson.il`, with NEW additions):
  1. Compute `totalPoints` by walking `rdb->point(pid)` until nil.
  2. **OK loop** — `for pid 1..totalPoints`, `pt = rdb->point(pid)`, foreach `out` in `pt->outputs(?type 'expr)` whose value is `numberp` or (`stringp` AND `!= "wave"`):
     - cornerName, testName, point, output, value (numeric kept numeric — do NOT pre-format; emit-side handles `%g`).
     - sweep ← `maeGetParamConditions(pid)` cached per `pid` (NEW: POC re-fetched every output).
     - corner_vars ← `_pvtCollCornerVarsCache[cname]`, populating once per corner.
     - test_note ← `_pvtCollTestNoteCache[testName]`, populating once per test name.
     - `seenSet[cname|pid] = testName` for sentinel pass.
  3. **failed/running loop** — foreach `tst` in `rdb->tests()` where `tst->status != 'done`. Emit a sentinel row `output = "__sim_status__"`, `value = PVT_JSON_NULL`, `status` mapped from symbol.
  4. **no_convergence loop** — foreach key in `seenSet` where `writtenSet[key]` is nil, emit a sentinel row with `status = "no_convergence"`.
- **NEW vs POC:**
  - `test` field is included on every row (POC dropped it for OK rows; schema §2.2 requires it).
  - `test_note` field is included on every row, populated from cached `axlGetNote`.
  - `value` is kept as a SKILL number/string and serialized by pvtJson — no manual `%g` preformatting (POC's `sprintf "%.6g"` introduced precision loss; the new emitter respects float vs int natively).
  - `sweep` and `corner_vars` are makeTable values with sidecar key list set to insertion order, so emit produces stable JSON object output. POC built strings; we build structured tables.
  - Cache lifecycle: caches are `makeTable`s constructed at the top of `_pvtCollIterateResults` and live for the duration of one `PvtSave` invocation — they go out of scope after the proc returns. (Answers design Q-E: lexical, not module-global.)
- **Test points for S3:**
  - With a fixture history of 2 points × 2 corners × 1 test × 1 output, returns 4 rows, all `status=ok`.
  - Inserting a known `failed` test at point 1, corner WC produces exactly one extra `__sim_status__` row with `status="failed"`.
  - Inserting a `no_convergence` (test seen in some output but not all expected `(test, point)` pairs written) produces the sentinel row with `status="no_convergence"`.
  - `test_note` for a test with note `"rise time"` appears verbatim on every row of that test; nil notes serialize to JSON `null`.
  - Caches are not leaked into a second `PvtSave` call (negative test — corner var changes between runs are visible).

### 4. `_pvtCollCopyNetlist(sess, histID, firstTestName, absRunDir)`

- **Inputs:** session string, history handle (integer), first test name (string|nil), absolute run-directory path.
- **Output:** `t_relativePath` (`"input.scs"`) on success, `nil` on any soft miss. **Never returns pvtErr** — netlist copy is best-effort per S3_PROBE_RESULTS.md director-decision #3.
- **Side effects:**
  - `asiGetAnalogSimulator(sess)` — string check.
  - `axlGetPointNetlistDir(histID, firstTestName)` — read-only.
  - On Spectre + non-nil dir: `ipcBeginProcess "cp -- <dir>/input.scs <absRunDir>/input.scs"` + `ipcWait`.
- **Errors raised:** none (warn-soft only).
- **Depends on:** nothing.
- **Soft-miss conditions** (each emits exactly one `warn(...)` and returns nil):
  - `firstTestName` is nil (empty result set).
  - `asiGetAnalogSimulator` returns anything other than `"spectre"`.
  - `axlGetPointNetlistDir` returns nil.
  - The constructed path `<dir>/input.scs` does not `isFile`.
  - `cp` exits non-zero.
- **Test points for S3:**
  - With a Spectre history pre-populated with `input.scs`, the file lands at `<runDir>/input.scs` byte-identical to source.
  - Returned path is the literal string `"input.scs"` (relative, no leading slash).
  - When simulator reports non-Spectre, returns nil and emits one warn (verify via captured stderr in skillbridge tests).
  - When source `input.scs` missing, returns nil; no partial file at destination.

### 5. `_pvtCollCaptureScreenshot(absRunDir, requested)`

- **Inputs:** absolute run-dir path, boolean `requested` (caller-supplied `?captureScreenshot`).
- **Output:** always `nil`. Documented as v1.1 deferred per C0.2 / DECISIONS #16 (TODO).
- **Side effects:** if `requested` is `t` and the warn has not yet fired in this Cadence session, emit one
  `warn("PvtSave ?captureScreenshot t: screenshot capture deferred to v1.1 — see TODO.md §3.5\n")`.
  Use a module-level `defvar _pvtCollScreenshotWarned nil` flipped to `t` after first emission.
- **Errors raised:** none.
- **Depends on:** nothing.
- **Implementer note (NOT to be coded):** S3_PROBE_RESULTS.md §6 documents the v1.1 path: `hiWindowSaveImage` for ViVA / ADE-XL setup / schematic windows; `hiExportImage` for graphics windows. **Do NOT call** `hiScreenShot` or `awvSaveAsImage` — they don't exist on this Cadence.
- **Test points for S3:**
  - With `?captureScreenshot t`: returns nil; first call emits warn (assert via stderr capture); second call emits no additional warn (one-shot semantics).
  - With `?captureScreenshot nil`: returns nil; no warn.
  - `artifacts` array in envelope remains `PVT_JSON_EMPTY_ARRAY` regardless.

### 6. `_pvtCollMakeRunDir(dbRoot, runId)`

- **Inputs:** `dbRoot` (absolute path string from `proj->dbRoot` — already simplified by pvtProject.il), `runId` (uuid string).
- **Output:** `(pvtOk t_absRunDir)` — absolute path, no trailing slash. Both `<dbRoot>/runs/<runId>/` and `<dbRoot>/runs/<runId>/artifacts/` exist on success.
- **Side effects:** creates 2 directories (and `<dbRoot>/runs/` if missing — recursive). Strategy per design Q-D: shell out via `ipcBeginProcess "mkdir -p -- <runDir>/artifacts"` + `ipcWait`. One shell call creates all four nested levels atomically per directory.
- **Errors raised:**
  - `'pvt_collect_io` "dbRoot %L does not exist (check .pvtproject)" if `isDir(dbRoot)` is nil. (Defensive guard — prevents silent phantom-tree creation when user typoed the dbRoot path.)
  - `'pvt_collect_io` "could not create run directory %L (mkdir exit=%d)" if `ipcWait` returns non-zero.
- **Depends on:** nothing.
- **Test points for S3:**
  - Returns absolute path, no trailing `/`.
  - Both `<runDir>` and `<runDir>/artifacts` exist (`isDir` returns t).
  - When `<dbRoot>/runs` does not exist, it is created as part of the mkdir.
  - When `<dbRoot>` itself does not exist: pvtErr 'pvt_collect_io with the "does not exist" message before mkdir runs.

### 7. `_pvtCollBuildEnvelope(runMeta, results, netlistPath, artifacts)`

- **Inputs:**
  - `runMeta` — table from `_pvtCollAutoCapture` (already has insertion-order sidecar with `netlist_path` slot pre-populated as `PVT_JSON_NULL`).
  - `results` — list of row tables.
  - `netlistPath` — string | nil.
  - `artifacts` — list (always empty in v1; parameter exists for v1.1).
- **Output:** `(pvtOk t_envelopeTable)`. Table has exactly four keys in order: `schema_version`, `run`, `results`, `artifacts`. Sidecar set to `'("schema_version" "run" "results" "artifacts")`.
- **Side effects:** none.
- **Errors raised:** none — pure construction.
- **Critical details:**
  - `schema_version` is **integer** `1`, not string. Drives ingester dispatch.
  - `run` table inherits `runMeta` directly. If `netlistPath` is a string, overwrite the pre-populated slot: `setarray runMeta "netlist_path" netlistPath`. Otherwise leave as `PVT_JSON_NULL`.
  - `results` is a SKILL list (proper). Empty list emits as `[]` (use `PVT_JSON_EMPTY_ARRAY` if list is `nil`).
  - `artifacts` MUST emit as `[]` not `null`. Use `(if artifacts artifacts PVT_JSON_EMPTY_ARRAY)`.
- **Test points for S3:**
  - Envelope has exactly 4 top-level keys, in the exact order [schema_version, run, results, artifacts].
  - `schema_version` round-trips through pvtJson as integer 1 (not "1").
  - Empty artifacts emit as `[]`, parseable by `python -m json.tool`.
  - `run.netlist_path` is JSON null when collector skipped the copy.

### 8. `_pvtCollWriteRunJson(envelope, absRunDir)`

- **Inputs:** envelope table, absolute run-dir path.
- **Output:** `(pvtOk t_absJsonPath)` — `<absRunDir>/run.json`.
- **Side effects:** creates / truncates `<absRunDir>/run.json`. Format: **compact** (no pretty-printing). Caller can pipe through `python -m json.tool` for review.
- **Errors raised:**
  - `'pvt_collect_io` "could not open %L for writing" if `outfile` returns nil.
  - Other I/O failures inside `pvtJsonEmitToPort` propagate as SKILL errors, caught by an `errset` localized to the writer body (not blanket-wrapping `PvtSave`).
- **Depends on:** `pvtJsonEmitToPort`, `outfile`, `close`, `unwindProtect`.
- **Design Q-B answer:** the port is created INSIDE this proc (not in `PvtSave`). The writer owns the open/close pair, wrapped in `unwindProtect`.
- **Test points for S3:**
  - File at returned path exists, is non-empty, is valid JSON (parseable by `python -m json.tool`).
  - Top-level object has exactly 4 keys [schema_version, run, results, artifacts], in that order.

### 9. `PvtSave(?histName ?label ?note ?captureScreenshot)` — public entry

- **Inputs:** all four optional keyword args.
- **Output:** `(pvtOk t_absJsonPath)` on success, or `pvtErr` on any failure.
- **Body sketch (NO production code; pseudocode only):**
  ```
  prog (projR proj sess hsdb histID histName runMeta runDir
        iterR firstTest results netlistPath envelope writeR)
    ; 1. resolve project
    projR = pvtLoadPvtProject()
    if pvtIsErr(projR) -> return projR
    proj = pvtUnwrap(projR)

    ; 2. resolve session + history
    sessR = _pvtCollResolveSession(histName)
    if pvtIsErr(sessR) -> return sessR
    [sess hsdb histID histName] = pvtUnwrap(sessR)

    ; 3. capture metadata
    captR = _pvtCollAutoCapture(proj sess histID histName note)
    if pvtIsErr(captR) -> return captR
    runMeta = pvtUnwrap(captR)
    setarray(runMeta "label" (or label PVT_JSON_NULL))

    ; 4. iterate results
    iterR = _pvtCollIterateResults(sess histName hsdb)
    if pvtIsErr(iterR) -> return iterR
    [firstTest results] = pvtUnwrap(iterR)

    ; 5. mkdir, copy netlist, screenshot
    runDirR = _pvtCollMakeRunDir(proj->dbRoot runMeta["run_id"])
    if pvtIsErr(runDirR) -> return runDirR
    runDir = pvtUnwrap(runDirR)

    netlistPath = _pvtCollCopyNetlist(sess histID firstTest runDir)
    _pvtCollCaptureScreenshot(runDir captureScreenshot)  ; always nil; v1 stub

    ; 6. assemble envelope
    envR = _pvtCollBuildEnvelope(runMeta results netlistPath nil)
    if pvtIsErr(envR) -> return envR
    envelope = pvtUnwrap(envR)

    ; 7. write
    writeR = _pvtCollWriteRunJson(envelope runDir)
    if pvtIsErr(writeR) -> return writeR

    return writeR  ; (pvtOk absJsonPath)
  ```
- **Errors raised (own):** none directly — every error path comes from a sub-module returning pvtErr, which is short-circuit-returned.
- **Depends on:** all 8 internal modules + `pvtLoadPvtProject`, `pvtJsonEmitToPort`, `setarray`, `arrayref`.
- **Test points for S3:**
  - End-to-end with a real session: produces a valid `run.json` whose every field passes the schema check.
  - With `pvtLoadPvtProject` returning notFound: returns the original pvtErr; the message contains both `"PVT_PROJECT"` and `".pvtproject"` substrings (verifies design Q-F).
  - With `axlGetWindowSession` returning nil: returns pvtErr 'pvt_collect_session.
  - With `histName` referring to a group history: returns pvtErr 'pvt_collect_session "v1 does not support group histories — pick a child run".

### Helper: `_pvtCollResolveSession(histName)`

Encapsulates S3-probe §1 + §4 + group-history detection.

- **Inputs:** `histName` (string | nil).
- **Output:** `(pvtOk (sess hsdb histID histName))` 4-element list, or pvtErr.
- **Errors:**
  - `'pvt_collect_session` "no ADE-XL session in current window" — `axlGetWindowSession` returns nil.
  - `'pvt_collect_session` "history %L not found in session" — `axlGetHistoryEntry` returns 0 (NOT nil — see S3_PROBE_RESULTS.md §4 gotcha).
  - `'pvt_collect_session` "no current history (run a sim first)" — `axlGetCurrentHistory` returns nil and caller did not pass `?histName`.
  - `'pvt_collect_session` "v1 does not support group histories — pick a child run" — group/parametric/Monte-Carlo parent detected.

### Helper: `_pvtCollIsoTimestamp()`

Resolves design Q-G.

- **Inputs:** none.
- **Output:** ISO-8601 string with offset, e.g. `"2026-04-22T14:32:15+08:00"`.
- **Implementation:** `ipcBeginProcess "date +%Y-%m-%dT%H:%M:%S%:z"` + `ipcWait` + `ipcReadProcess`, strip trailing `\n`. The `%:z` extension is GNU coreutils ≥ 2009.
- **Errors raised:** none. Fallback: `getCurrentTime()` wrapped with literal `+00:00` offset and a one-time warn `"could not determine local timezone offset, defaulting to UTC"`.

---

## Design decisions

### A. Error propagation strategy — option (i): result-passing

Every internal proc returns `(pvtOk val)` on success or `(pvtErr ...)` on failure. `PvtSave` body checks `pvtIsErr` after each call and short-circuits. Matches `pvtJson.il` and `pvtProject.il` exactly.

The only place where SKILL-level `errset` is still useful is around `outfile`/`fprintf` calls inside `_pvtCollWriteRunJson` (OS-level errors with no native pvtErr counterpart). There we wrap with `errset` and convert the caught error message into pvtErr 'pvt_collect_io. **Localized errset is fine; do not blanket-wrap PvtSave.**

### B. Port lifecycle — created inside `_pvtCollWriteRunJson`

The writer owns the open/close pair, wrapped in `unwindProtect`. `PvtSave` does not see ports. Reasoning: (a) write target is an implementation detail of the writer; (b) `unwindProtect` on the close call must be co-located with the open; (c) caller passes a destination dir, callee writes a deterministic filename inside.

### C. `testbench_alias` resolution — inside `_pvtCollAutoCapture`

Lookup happens at the moment `testbench_id` is computed, so the alias travels with `runMeta` from there onward. Build-envelope step is a pure data-shaping pass with no business logic. Schema §2.1's "resolved at dump time" colocates naturally with testbench_id capture.

### D. `runs/` directory creation — `mkdir -p` via shell

`ipcBeginProcess "mkdir -p -- <runDir>/artifacts"` + `ipcWait`. One shell call creates all four nested levels atomically; race-safe by design. Defensive guard: probe `isDir(proj->dbRoot)` before mkdir to fail clearly on a typoed dbRoot.

### E. Per-test note caching — lexical, scoped to one `PvtSave`

Cache is a `makeTable` constructed inside `_pvtCollIterateResults`, populated on miss, read on hit. Dies when proc returns. Not module-level — per-test notes can change between Maestro runs (user editing notes), so cross-`PvtSave` caching would silently serve stale data. Same lifetime applies to corner-vars cache.

### F. Missing project-root error message

`pvtLoadPvtProject` already produces `"no .pvtproject found walking up from <cwd> and PVT_PROJECT is not set"`. **No additional wrapping needed in `PvtSave`** — the existing message is sufficient. Test point: error message contains both `"PVT_PROJECT"` and `".pvtproject"` substrings.

### G. ISO-8601 timestamp — shell out to `date +"%Y-%m-%dT%H:%M:%S%:z"`

See `_pvtCollIsoTimestamp` helper. Other options (parsing `getCurrentTime`, no-offset timestamp) are inferior — manual parsing requires localtime/gmtime SKILL primitives that aren't documented; no-offset violates schema §2.1 ISO-8601 contract.

---

## Test plan handoff for S3

| Surface | Determinism | S3 strategy |
|---|---|---|
| Envelope key order [schema_version, run, results, artifacts] | deterministic | Snapshot test: parse output, assert exact key list. |
| Schema version is integer 1 | deterministic | `assert json.load(...)["schema_version"] == 1` (NOT `"1"`). |
| `artifacts: []` (empty array sentinel correctness) | deterministic | `assert json.load(...)["artifacts"] == []`. |
| `results` array length for fixture history | deterministic | Property test: count rows per (point × test × corner × output) cross-product. |
| `results[i]` field set [point, corner, test, output, value, status, sweep, corner_vars, test_note] | deterministic | Snapshot test on first row's keys. |
| Sentinel rows have `output == "__sim_status__"` and `value` is JSON null | deterministic | Property test on rows with status != "ok". |
| `run_id` shape (UUIDv4 regex) | shape deterministic, value not | Regex match; do not snapshot literal value. |
| `timestamp` shape (ISO 8601 with offset) | shape deterministic, value not | Regex match `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$`. |
| `author` value | non-deterministic ($USER varies, project author varies) | Read `proj.author` || `$USER` and assert equality with file contents. |
| Absolute path of run.json | non-deterministic (dbRoot varies) | Assert returned path starts with `proj.dbRoot`, ends with `/run.json`, intermediate component is a UUID. |
| `netlist_path == "input.scs"` when source present | deterministic | Live-session test; compare file hash with source. |
| `netlist_path == null` when source absent or non-Spectre | deterministic given the failure mode | Live-session test with fixture lacking netlist. |
| `test_note` populated from `axlGetNote` | deterministic given fixture | Live-session test; set a note via `axlSetNote`, dump, assert. |
| `corner_vars` parsed from corner params | deterministic given fixture | Live-session test on a known corner. Edge case: parse-error fallback emits `_parse_error` synthetic key. |
| Screenshot capture (v1) | deterministic — always nil | Unit test: invoke with `?captureScreenshot t`, assert envelope `artifacts == []`, assert one warn emitted on first call only. |
| Behavior when no ADE-XL session | deterministic — pvtErr 'pvt_collect_session | Unit test via skillbridge with no session. |
| Behavior when group history selected | deterministic — pvtErr 'pvt_collect_session | Live-session test. |
| Behavior when no `.pvtproject` and no `$PVT_PROJECT` | deterministic — pvtErr 'pvt_notFound | Unit test (no live session needed). |
| Cache lifetime (corner-vars / test-note) | deterministic | Two consecutive PvtSave calls with mutated values between them; assert second dump has new values. |

**S3 test infrastructure tiers:**
- **Tier 1 (offline, no Cadence):** unit tests for `_pvtCollNewRunId` (UUID shape), `_pvtCollMakeRunDir` (mkdir -p semantics on a tmpdir), envelope shape. Driven by skillbridge against a Virtuoso instance with NO active ADE-XL session — these don't require a real history.
- **Tier 2 (live session, manual fixture):** end-to-end `PvtSave` against a checked-in tiny `.cdsenv` + minimal Spectre testbench in `skill/tests/fixtures/livesession/`. Run by hand or in a CI shim that opens Virtuoso.
- **Tier 3 (golden-file diff):** pin the JSON output of a known fixture run; diff against committed golden file with non-deterministic fields (run_id, timestamp, author, paths) regex-redacted before compare.

---

## Open questions for implementer

1. **DECISIONS #16 placeholder.** S2b's screenshot warn message points to `TODO.md §3.5` until S4 lands the formal `DECISIONS #16` entry. Do not add #16 yourself — that's S4's job. Just keep the warn message string as `"PvtSave ?captureScreenshot t: screenshot capture deferred to v1.1 — see TODO.md §3.5\n"`.

2. **`asiGetAnalogSimulator` exact return value.** Likely `"spectre"` (lowercase); could be `"Spectre"`. Defensive: `(member (asiGetAnalogSimulator sess) '("spectre" "Spectre"))`. Probe live during integration and tighten the comparison if confident.

3. **`axlGetCurrentRunMode` exact signature for group-history detection.** S3_PROBE_RESULTS.md §4 mentions it without pinning args. Probe live during integration; if the API doesn't directly answer "is this a group history," fall back to `axlGetHistoryGroupChildren(histID)` returning a non-empty list.

4. **Corner-vars edge case: `corModelSpec` nested form.** Port the POC's `PvtParseCornerParams` logic verbatim (line-for-line); do not rewrite without a reproducer. Cadence-version-dependent shape.
