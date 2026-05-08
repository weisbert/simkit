# S3 Probe Results — Cadence APIs needed by `pvtCollect.il`

**Probed:** 2026-05-08, via skillbridge socket `/tmp/skill-server-default.sock`.
**Cadence:** SKILL37.00, ICADVM18.1-64b.
**Reader audience:** the implementer of S2b (Phase 1 §3 collector SKILL).

**Environment caveat:** at probe time, no ADE-XL window was open on the dev host (`axlNoSession() => t`). Session-bound calls were verified `isCallable` true and signatures cross-checked against the IC6.1.8 reference PDFs (`adexlSKILLref.pdf`, `skuiref.pdf`, `sklangref.pdf`, `skartistref.pdf`); end-to-end live verification must happen during S2b implementation against a real Maestro session.

---

## 1. testbench_id

- **API:**
  - `axlGetWindowSession()` → `t_sessionName | nil` (string like `"session0"`; `nil` if no ADE-XL in current window)
  - `axlGetSessionLibName(t_sessionName)` → `t_libName | nil`
  - `axlGetSessionCellName(t_sessionName)` → `t_cellName | nil`
  - `axlGetSessionViewName(t_sessionName)` → `t_viewName | nil`
  - Compose: `(strcat lib "/" cell "/" view)` per schema §2.1.
- **Verified via skillbridge:**
  ```
  (isCallable 'axlGetWindowSession)   => True
  (isCallable 'axlGetSessionLibName)  => True
  (isCallable 'axlGetSessionCellName) => True
  (isCallable 'axlGetSessionViewName) => True
  (axlGetWindowSession)               => None        ;; no session open right now
  (axlNoSession)                      => True
  ```
- **Important shape note:** `axlGetWindowSession()` returns a **string** (the session name), not a struct. The L/C/V accessors take that name string. The POC's `sess = axlGetWindowSession()` confirms this.
- **Gotchas:**
  - Returns `nil` when no ADE-XL is in the current window. Guard with `axlNoSession` or null-check before the L/C/V calls.
  - In headless / `-nograph` mode there's no window; `axlCreateSession` + an explicit setup is required (out of scope for §3 v1).
- **Trap class (DECISIONS #14):** none directly. Compose with `strcat`, not `cons`/dotted-pair.

## 2. netlist path

- **API (chosen v1 strategy):** `axlGetPointNetlistDir(x_historyID t_testName [?cornerName ...] [?designPointId ...])` → `t_pointNetlistDir | nil` (`adexlSKILLref.pdf` p.102-103).
  - With NO `?cornerName`/`?designPointId`: returns the top-level (PSF) netlist dir for the `(test, history)` pair — i.e. the dir holding the netlist generated for that test.
  - With both `?cornerName` AND `?designPointId`: returns the per-point netlist dir.
  - Pair with: `axlGetHistoryEntry(x_hsdb, t_historyName)` to obtain `x_historyID` from a name; `x_hsdb = axlGetMainSetupDB(sessionName)`.
  - The simulated netlist file in that dir is conventionally `input.scs` (Spectre); per schema §4 the collector copies it to `<run_dir>/input.scs`.
- **Verified via skillbridge:**
  ```
  (isCallable 'axlGetPointNetlistDir) => True
  (isCallable 'axlGetMainSetupDB)     => True
  (isCallable 'axlGetHistoryEntry)    => True
  ```
- **Multi-test, multi-corner reality.** `axlGetPointNetlistDir` is keyed by `(history, test, corner, point)`. Schema §2.1 has a single `netlist_path` slot. Defensible v1 strategies:
  1. **First-test, top-level netlist (recommended v1):** call with `(historyID, firstTestName)` only. Document the limitation: heterogeneous tests with materially different netlists lose the non-first ones. Per-corner deltas remain queryable via `corner_vars` on every result row.
  2. **Per-test fan-out (v2):** walk `axlGetTests(x_mainSDB)` and emit one `artifacts[]` entry per test. Requires schema bump (`netlist_path` → list); explicitly out of scope per Decision #8.
- **Gotchas:**
  - Returns `nil` if the test's netlist hasn't been generated yet (corner-first failure). Handle: emit `null` for `netlist_path` and log a warning. **Schema docs amendment needed**: §2.1 currently types `netlist_path` as `str` non-null; widen to `str | null` (S4 will land this).
  - `?cornerName` and `?designPointId` are coupled — pass both or neither (PDF "Important" callout).
  - Returned path is a directory; the actual file is `input.scs` (Spectre) or `input.cir` / etc. for other simulators. v1 hardcodes `input.scs`; if `asiGetAnalogSimulator` reports non-Spectre, log a warning and skip the copy (see §3 below for context).
  - Use a shell `cp` (via `ipcBeginProcess "cp ..."` or `csh "cp ..."`) — SKILL has no built-in file-copy primitive worth relying on.
- **Trap class (DECISIONS #14):** trap #6 (don't build kw args with `cons`-on-non-list; pass positionally).

## 3. Per-test note text

- **API:** `axlGetNote(x_hsdb t_item t_name)` → `t_note | nil` (`adexlSKILLref.pdf` p.101).
  - `t_item` MUST be a literal string: `"test"`, `"history"`, `"corner"`, or `"globalvar"`.
  - For schema §2.2 `test_note`: `axlGetNote(hsdb, "test", testName)`.
- **Verified via skillbridge:**
  ```
  (isCallable 'axlGetNote) => True
  ```
- **Gotchas:**
  - Pass-through verbatim; do NOT parse the prefix string the example in the PDF shows (`"Notes -- name : ..."`). Note text is opaque per Decision #10.
  - Returns `nil` when no note set → schema §2.2 declares `test_note` as `str | null`, so the collector emits JSON `null`.
  - The literal-string `"test"` argument is REQUIRED — not a symbol `'test`. Easy mistake.
- **Trap class (DECISIONS #14):** none directly.

## 4. History enumeration & default selection

- **API:**
  - **Default (active) history when caller omits `?histName`:**
    `axlGetCurrentHistory(t_sessionName)` → `x_historyHandle | nil` (PDF p.417).
    Get name via `axlGetHistoryName(x_historyHandle)` → `t_historyName | nil` (p.427).
  - **Caller-supplied `?histName` → handle:**
    `axlGetHistoryEntry(x_hsdb t_historyName)` → `x_historyID | 0` (p.423; **`0`, not `nil`, on miss**).
  - **Listing all histories (diagnostics):**
    `axlGetHistory(x_hsdb)` → `(handle (n1 n2 ...))` list-of-list. Note: NOT `axlGetHistoryNames` — that name does **not** exist (`isCallable` => `nil`).
- **Verified via skillbridge:**
  ```
  (isCallable 'axlGetCurrentHistory)   => True
  (isCallable 'axlGetHistoryName)      => True
  (isCallable 'axlGetHistoryEntry)     => True
  (isCallable 'axlGetHistory)          => True
  (isCallable 'axlGetHistoryNames)     => None    ;; DOES NOT EXIST
  ```
- **Recommended `PvtSave` flow:**
  ```
  sess = axlGetWindowSession()
  hsdb = axlGetMainSetupDB(sess)
  if histName supplied:
      histID = axlGetHistoryEntry(hsdb, histName)   ;; 0 on miss -> error
  else:
      histID = axlGetCurrentHistory(sess)           ;; nil on no-history -> error
      histName = axlGetHistoryName(histID)
  ```
- **Gotchas:**
  - `axlGetHistoryEntry` returns integer `0` (not `nil`) on miss. Test with `equal v 0`, not `null v`.
  - Group runs (Monte Carlo, parametric sweeps) wrap multiple sub-histories. `axlGetHistoryGroupChildren` walks them. **v1 explicitly does not support group histories** — detect and error out clearly.

## 5. UUIDv4 in classic SKILL

- **APIs probed:**
  - `gensym([s_arg])` → sequential symbol like `G275`, `G276`. **Adequate for in-process; NOT for cross-process collision avoidance.** Two Virtuoso instances would collide.
  - `random([n])` → integer; with no arg, returns 32-bit positive int. Useful for entropy.
  - `mt_random` → **NOT bound** on this Cadence (`isCallable => nil`). Do not use.
  - `getCurrentTime()` → string `"May 8 15:21:14 2026"`, **NOT an int** (DECISIONS #14 trap #8).
  - `ipcBeginProcess "uuidgen"` → spawn shell uuidgen and read stdout. **Verified live.**
  - `csh "uuidgen"` → returns `t` but **does NOT capture stdout**. Cannot use.
- **Verified via skillbridge:**
  ```
  (let ((p (ipcBeginProcess "uuidgen")) buf)
    (ipcWait p)
    (setq buf (ipcReadProcess p))
    buf)
  => "a7e217d8-db7d-4b00-a0cc-de25b045654f\n"   ;; trailing \n; trim
  ```
- **Recommended strategy:**
  - **Primary:** `ipcBeginProcess "uuidgen" / ipcWait / ipcReadProcess`, strip trailing `\n` with `(buildString (parseString s "\n") "")`. RFC 4122 v4 compliant. `uuidgen` ships with util-linux on every modern Linux.
  - **Fallback (if uuidgen missing):** synthesize from `random()`:
    ```
    (sprintf nil "%04x%04x-%04x-4%03x-%04x-%04x%04x%04x"
             (random 65536) (random 65536) (random 65536)
             (logand (random 65536) 4095)
             (logior 32768 (logand (random 65536) 16383))
             (random 65536) (random 65536) (random 65536))
    ```
    Schema §2.1 says "uniqueness matters; monotonicity does not" — fallback acceptable.
- **Trap class (DECISIONS #14):** trap #9 (`%X` is rejected; only `%x`).

## 6. Screenshot APIs (signatures only — v1 NOT implementing)

- **`hiWindowSaveImage`** (`skuiref.pdf` p.1058-1060). **Verified callable.**
  Targets: any Qt window (schematic, ADE-XL setup, ViVA waveform, dialogs, full screens). This is the right v1.1 API for capturing schematic + ADE-XL setup.
- **`hiExportImage`** (`skuiref.pdf` p.1062). **Verified callable.** Restricted to graphics windows (schematic/layout). Higher fidelity than `hiWindowSaveImage` for those.
- **`hiScreenShot`** — **NOT bound** on this Cadence. Does not exist on ICADVM18.1.
- **`awvSaveAsImage`** — **NOT bound.** Does not exist. Use `hiWindowSaveImage` for ViVA windows instead.

For v1, `PvtSave ?captureScreenshot t` will warn-once `"screenshot capture deferred to v1.1; see DECISIONS #16"` and emit no artifact rows.

## Summary table

| Need | Recommended API | Confidence |
|---|---|---|
| testbench_id | `axlGetWindowSession` + `axlGetSessionLibName/CellName/ViewName` joined with `/` | high |
| netlist path | `axlGetPointNetlistDir(historyID, firstTestName)` → top-level dir; copy `<dir>/input.scs` | high for API; med for "first-test wins" v1 policy |
| test_note | `axlGetNote(hsdb, "test", testName)` (literal string `"test"`) | high |
| history default | `axlGetCurrentHistory(session)` + `axlGetHistoryName(handle)`; named lookup via `axlGetHistoryEntry(hsdb, name)` returns **`0`** on miss | high |
| UUIDv4 | Primary: `ipcBeginProcess "uuidgen"` + `ipcWait` + `ipcReadProcess` (strip `\n`). Fallback: `random()` + bit-twiddle | high |
| Screenshots (v1.1 only) | `hiWindowSaveImage` for any window; `hiExportImage` for graphics windows. **`hiScreenShot` and `awvSaveAsImage` do not exist.** | high |

## Director's decisions on open questions

1. **Live ADE-XL session probe deferred** — accepted. S2b implementer round-trips each `axl*`/`mae*` call with a real session before considering it settled. Not a blocker for code structure.
2. **Non-Spectre simulator detection** — use `asiGetAnalogSimulator(sess)`. If non-Spectre, log warn and skip the netlist copy; emit `null` for `netlist_path`.
3. **`netlist_path` schema slot when capture fails** — widen schema §2.1 type from `str` to `str | null`. Soft-warn on miss, do not fail the dump. **S4 will amend `docs/schema.md` accordingly.**
4. **Group histories** — detect via `axlGetCurrentRunMode(checkpoint)` and emit a clear `"v1 does not support group histories — pick a child run"` error. Do not silently dump the parent.
5. **`csh "uuidgen"` no stdout capture** — confirmed live; flagged. Use `ipcBeginProcess` only.
