;; ============================================================================
;; pvtCollect.il
;;
;; Phase 1 §3 collector for simkit. Auto-captures one ADE-XL history into a
;; <dbRoot>/runs/<run_id>/run.json envelope per docs/schema.md §2.
;;
;; Public API:
;;   PvtSave(?histName ?label ?note ?captureScreenshot)
;;     -> (pvtOk t_absJsonPath) | pvtErr
;;
;; SKILL note: every procedure that uses `(return ...)` for early exit MUST
;; have its body wrapped in `(prog () ...)`, because in classic SKILL `return`
;; only escapes the enclosing `prog` form (sklangref.pdf p.379), not the
;; surrounding `procedure`. We follow the convention "every procedure body
;; is a prog block" throughout this file, matching pvtJson.il / pvtProject.il.
;;
;; Reuse:
;;   - pvtError.il: pvtOk / pvtErr / pvtIsOk / pvtIsErr / pvtUnwrap / pvtErrMessage
;;   - pvtJson.il:  pvtJsonEmitToPort / pvtJsonParseFile / PVT_JSON_NULL /
;;                  PVT_JSON_EMPTY_ARRAY / PVT_JSON_EMPTY_OBJECT /
;;                  _PVT_JSON_KEYS_PROP
;;   - pvtProject.il: pvtLoadPvtProject / pvtProjectAliasFor / proj-> accessors
;;
;; Module map (see skill/S3_DESIGN.md for the authoritative call graph):
;;   PvtSave
;;     ├── pvtLoadPvtProject
;;     ├── _pvtCollResolveSession
;;     ├── _pvtCollAutoCapture
;;     │     ├── _pvtCollNewRunId
;;     │     └── _pvtCollIsoTimestamp
;;     ├── _pvtCollIterateResults
;;     │     ├── _pvtCollParseCornerParams (ported from POC)
;;     │     ├── _pvtCollAssocToTable
;;     │     └── _pvtCollMakeRow
;;     │   (refactor in progress per docs/plans/§3_messy_data.md §1:
;;     │    _pvtCollWalkRdb is added alongside in Step 1; pure shaper
;;     │    _pvtCollRowsFromTuples + caller swap land in Steps 2 + 3.)
;;     ├── _pvtCollMakeRunDir
;;     ├── _pvtCollCopyNetlist
;;     ├── _pvtCollCaptureScreenshot
;;     ├── _pvtCollBuildEnvelope
;;     └── _pvtCollWriteRunJson
;; ============================================================================


;; --- module-level state -----------------------------------------------------

;; One-shot warn for the v1 stub of ?captureScreenshot t. Flipped to t after
;; the first emission within a Cadence session. Defvar overwrites already-
;; bound vars in classic SKILL (DECISIONS #14 trap #5), but reloading this
;; file is intentionally idempotent at the language level — the cost is a
;; second warn after a re-load, which matches the user-visible expectation
;; that "the file was reloaded, behaviour resets".
(defvar _pvtCollScreenshotWarned nil)


;; ============================================================================
;; Helper: _pvtCollNewRunId  (S3_DESIGN §1)
;;
;; Returns (pvtOk t_uuidString). Strict 36-char RFC 4122 v4 form. Primary path
;; spawns `uuidgen`; fallback synthesizes from random() + bit-twiddle when the
;; shell call fails (not when uuidgen is missing — we don't probe for that
;; ahead of time, we just notice the empty / malformed read and fall back).
;; Trailing `\n` from `ipcReadProcess` is always stripped (S3_PROBE_RESULTS §5).
;; ============================================================================

(procedure (_pvtCollNewRunId)
  (prog (cid raw stripped)
    ;; Primary: shell out to uuidgen.
    (setq cid (errset (ipcBeginProcess "uuidgen") nil))
    (cond
      ;; errset returns a list on success — (cid) — or nil on signal. Unwrap.
      ((and cid (listp cid) (car cid))
        (setq cid (car cid))
        ;; ipcWait returns t. We don't probe exit status here — if uuidgen
        ;; ran at all we expect 36 chars + '\n' on stdout. If it didn't,
        ;; ipcReadProcess returns nil and we fall through to the fallback.
        (errset (ipcWait cid) nil)
        (setq raw (errset (ipcReadProcess cid) nil))
        (cond
          ((and raw (listp raw) (car raw) (stringp (car raw)))
            (setq stripped (_pvtCollStripTrailingNewline (car raw)))
            ;; Sanity: must be exactly 36 chars and v4-shaped. If not, fall
            ;; through to the random fallback.
            (cond
              ((_pvtCollIsUuidV4 stripped)
                (return (pvtOk stripped))))))))
    ;; Fallback: synthesise from random() per S3_PROBE_RESULTS §5.
    (return (pvtOk (_pvtCollSynthUuidV4))))
)

(procedure (_pvtCollStripTrailingNewline s)
  ;; Strip a single trailing \n if present, then a trailing \r if present
  ;; (covers the "uuidgen | tr -d \r" portability angle on cygwin-style hosts).
  (prog (n last)
    (cond
      ((not (stringp s)) (return s)))
    (setq n (strlen s))
    (cond ((zerop n) (return s)))
    (setq last (substring s n 1))
    (cond
      ((equal last "\n")
        (setq s (substring s 1 (difference n 1)))
        (setq n (difference n 1))
        (cond ((zerop n) (return s)))
        (setq last (substring s n 1))))
    (cond
      ((equal last "\r")
        (setq s (substring s 1 (difference n 1)))))
    (return s))
)

(procedure (_pvtCollIsUuidV4 s)
  ;; ^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$
  (and (stringp s)
       (equal (strlen s) 36)
       (pcreMatchp
         "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
         s))
)

(procedure (_pvtCollSynthUuidV4)
  ;; RFC 4122 v4 fallback: bytes are random except the version nibble (set to
  ;; 4) and the high two bits of the clock_seq_hi byte (set to 10b).
  ;; sprintf format:
  ;;   xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx
  ;; where x is any hex and y is one of [89ab].
  ;; SKILL random() with no arg returns a 32-bit positive int; we draw 16-bit
  ;; chunks via (random 65536) for predictable formatting.
  (let ((a (random 65536))   ; first 16 bits
        (b (random 65536))   ; next 16 bits
        (c (random 65536))   ; group 2: 16 bits
        (d (random 4096))    ; group 3 minus the leading "4": 12 bits
        (e (random 16384))   ; group 4 lower 14 bits (high 2 bits set below)
        (f (random 65536))   ; group 5 high 16 bits
        (g (random 65536))   ; group 5 mid  16 bits
        (h (random 65536)))  ; group 5 low  16 bits
    ;; Set high two bits of `e` to 10b so the printed nibble lands in [89ab]:
    ;;   logior with 0x8000 (32768), and clear top bit via the (random 16384)
    ;;   draw — already < 0x4000 so the result is in [0x8000, 0xBFFF].
    (sprintf nil "%04x%04x-%04x-4%03x-%04x-%04x%04x%04x"
             a b c d
             (logior 32768 e)
             f g h))
)


;; ============================================================================
;; Helper: _pvtCollIsoTimestamp  (S3_DESIGN §G)
;;
;; Returns ISO 8601 with offset, e.g. "2026-04-22T14:32:15+08:00". Implements
;; via shell-out to `date`. Fallback (rare) is `getCurrentTime` + literal
;; "+00:00" + a one-time warn.
;; ============================================================================

(procedure (_pvtCollIsoTimestamp)
  (prog (cid raw stripped)
    (setq cid (errset (ipcBeginProcess "date +%Y-%m-%dT%H:%M:%S%:z") nil))
    (cond
      ((and cid (listp cid) (car cid))
        (setq cid (car cid))
        (errset (ipcWait cid) nil)
        (setq raw (errset (ipcReadProcess cid) nil))
        (cond
          ((and raw (listp raw) (car raw) (stringp (car raw)))
            (setq stripped (_pvtCollStripTrailingNewline (car raw)))
            ;; Sanity-check shape: ^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$
            (cond
              ((pcreMatchp
                 "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[+-][0-9]{2}:[0-9]{2}$"
                 stripped)
                (return stripped)))))))
    ;; Fallback: getCurrentTime() returns a string like "May 8 14:37:37 2026"
    ;; which is NOT ISO-shaped. We can't safely reformat without localtime
    ;; primitives. Best we can do is concatenate getCurrentTime + "+00:00",
    ;; warn once, and let the ingester reject if it's strict about shape.
    (warn "could not determine local timezone offset, defaulting to UTC\n")
    (return (strcat (getCurrentTime) "+00:00")))
)


;; ============================================================================
;; Helper: _pvtCollMakeRunDir  (S3_DESIGN §6)
;;
;; Inputs:
;;   dbRoot — absolute path string from proj->dbRoot.
;;   runId  — uuid string.
;; Output:
;;   (pvtOk t_absRunDir) on success — both <runDir> and <runDir>/artifacts
;;   exist; no trailing slash.
;;   pvtErr 'pvt_collect_io on dbRoot-missing or mkdir failure.
;; ============================================================================

(procedure (_pvtCollMakeRunDir dbRoot runId)
  (prog (runDir artifactsDir cmd cid status)
    ;; Defensive guard: bail before touching mkdir if dbRoot is bogus.
    (cond
      ((or (null dbRoot) (not (stringp dbRoot)))
        (return (pvtErr 'pvt_collect_io
                        (sprintf nil "dbRoot %L does not exist (check .pvtproject)"
                                 dbRoot)
                        nil)))
      ((not (isDir dbRoot))
        (return (pvtErr 'pvt_collect_io
                        (sprintf nil "dbRoot %L does not exist (check .pvtproject)"
                                 dbRoot)
                        nil))))
    (setq runDir       (strcat dbRoot "/runs/" runId))
    (setq artifactsDir (strcat runDir "/artifacts"))
    ;; mkdir -p creates everything atomically (S3_DESIGN §D). The `--` guard
    ;; protects against a runDir that begins with `-` (random + uuidgen never
    ;; produces such a string, but it's defensive against future runId
    ;; schemes).
    (setq cmd (sprintf nil "mkdir -p -- %L" artifactsDir))
    (setq cid (errset (ipcBeginProcess cmd) nil))
    (cond
      ((or (null cid) (not (listp cid)) (null (car cid)))
        (return (pvtErr 'pvt_collect_io
                        (sprintf nil "could not spawn mkdir process for %L"
                                 runDir)
                        nil))))
    (setq cid (car cid))
    (errset (ipcWait cid) nil)
    (setq status (errset (ipcGetExitStatus cid) nil))
    ;; status is a list (exit-int) on success, nil on errset failure. Treat
    ;; either non-zero exit or a nil errset as a hard error.
    (cond
      ((or (null status) (not (listp status)))
        (return (pvtErr 'pvt_collect_io
                        (sprintf nil "could not create run directory %L (mkdir status unavailable)"
                                 runDir)
                        nil)))
      ((not (zerop (car status)))
        (return (pvtErr 'pvt_collect_io
                        (sprintf nil "could not create run directory %L (mkdir exit=%d)"
                                 runDir (car status))
                        nil))))
    ;; Verify both directories materialised — paranoid but cheap.
    (cond
      ((or (not (isDir runDir)) (not (isDir artifactsDir)))
        (return (pvtErr 'pvt_collect_io
                        (sprintf nil "could not create run directory %L (mkdir reported success but tree missing)"
                                 runDir)
                        nil))))
    (return (pvtOk runDir)))
)


;; ============================================================================
;; Helper: _pvtCollResolveSession(?histName)  (S3_DESIGN helper after §9)
;;
;; Encapsulates S3-probe §1 + §4 + group-history detection. Returns
;;   (pvtOk (sess hsdb histID histName))
;; or pvtErr 'pvt_collect_session.
;; ============================================================================

(procedure (_pvtCollResolveSession histName)
  (prog (sess hsdb histID resolvedName checkpoint runMode)
    ;; --- 1. session in current window
    (setq sess (errset (axlGetWindowSession) nil))
    (setq sess (cond ((listp sess) (car sess)) (t nil)))
    (cond
      ((null sess)
        (return (pvtErr 'pvt_collect_session
                        "no ADE-XL session in current window"
                        nil))))
    ;; --- 2. main setup db
    (setq hsdb (errset (axlGetMainSetupDB sess) nil))
    (setq hsdb (cond ((listp hsdb) (car hsdb)) (t nil)))
    (cond
      ((null hsdb)
        (return (pvtErr 'pvt_collect_session
                        (sprintf nil "could not get main setup DB for session %L"
                                 sess)
                        nil))))
    ;; --- 3. resolve histID, histName
    (cond
      ;; Caller-supplied histName: look up the entry. axlGetHistoryEntry
      ;; returns 0 (NOT nil) on miss — S3_PROBE_RESULTS §4.
      ((and histName (stringp histName) (greaterp (strlen histName) 0))
        (setq histID (errset (axlGetHistoryEntry hsdb histName) nil))
        (setq histID (cond ((listp histID) (car histID)) (t nil)))
        (cond
          ((or (null histID) (equal histID 0))
            (return (pvtErr 'pvt_collect_session
                            (sprintf nil "history %L not found in session"
                                     histName)
                            nil))))
        (setq resolvedName histName))
      ;; No histName: use current history. axlGetCurrentHistory returns
      ;; integer handle | nil.
      (t
        (setq histID (errset (axlGetCurrentHistory sess) nil))
        (setq histID (cond ((listp histID) (car histID)) (t nil)))
        (cond
          ((null histID)
            (return (pvtErr 'pvt_collect_session
                            "no current history (run a sim first)"
                            nil))))
        (setq resolvedName (errset (axlGetHistoryName histID) nil))
        (setq resolvedName
              (cond ((listp resolvedName) (car resolvedName)) (t nil)))
        (cond
          ((or (null resolvedName) (not (stringp resolvedName)))
            (return (pvtErr 'pvt_collect_session
                            "axlGetHistoryName returned nil for current history"
                            nil))))))
    ;; --- 4. group-history detect (S3_PROBE_RESULTS §4 director-decision #4)
    ;; Strategy: get the checkpoint for this history, then ask for its run
    ;; mode. "Single Run, Sweeps and Corners" is the only non-group mode.
    ;; If the calls fail (older Cadence / unusable handle), fall back to
    ;; axlGetHistoryGroupChildren — non-empty list = group parent.
    (setq checkpoint (errset (axlGetHistoryCheckpoint histID) nil))
    (setq checkpoint
          (cond ((listp checkpoint) (car checkpoint)) (t nil)))
    (cond
      (checkpoint
        (setq runMode (errset (axlGetCurrentRunMode checkpoint) nil))
        (setq runMode (cond ((listp runMode) (car runMode)) (t nil)))
        (cond
          ((and (stringp runMode)
                (not (equal runMode "Single Run, Sweeps and Corners")))
            (return (pvtErr 'pvt_collect_session
                            "v1 does not support group histories — pick a child run"
                            nil))))))
    ;; Belt-and-braces fallback: explicit group-children probe.
    (let ((kids (errset (axlGetHistoryGroupChildren histID) nil)))
      (setq kids (cond ((listp kids) (car kids)) (t nil)))
      ;; kids is itself "(handle (n1 n2 ...))" or nil — treat any non-nil
      ;; truthy reply as evidence of a group.
      (cond
        ((and kids (listp kids) (cadr kids) (listp (cadr kids))
              (not (null (cadr kids))))
          (return (pvtErr 'pvt_collect_session
                          "v1 does not support group histories — pick a child run"
                          nil)))))
    (return (pvtOk (list sess hsdb histID resolvedName))))
)


;; ============================================================================
;; Helper: _pvtCollAutoCapture(proj sess histID histName note)  (S3_DESIGN §2)
;;
;; Builds the runMeta table with schema §2.1 keys in emit-order. The sidecar
;; _PVT_JSON_KEYS_PROP is populated so the pvtJson emitter walks keys in the
;; right order. label is set to PVT_JSON_NULL (D #11). netlist_path is
;; pre-populated with PVT_JSON_NULL so the build-envelope step can overwrite
;; without re-keying the sidecar.
;; ============================================================================

(procedure (_pvtCollAutoCapture proj sess histID histName note)
  (prog (lib cell view tbId alias author tab keys runId timestamp)
    ;; --- 1. lib/cell/view from session ----
    (setq lib  (errset (axlGetSessionLibName  sess) nil))
    (setq lib  (cond ((listp lib)  (car lib))  (t nil)))
    (setq cell (errset (axlGetSessionCellName sess) nil))
    (setq cell (cond ((listp cell) (car cell)) (t nil)))
    (setq view (errset (axlGetSessionViewName sess) nil))
    (setq view (cond ((listp view) (car view)) (t nil)))
    (cond
      ((or (null lib) (null cell) (null view))
        (return (pvtErr 'pvt_collect_session
                        (sprintf nil
                                 "ADE-XL session has no active library/cell/view (lib=%L cell=%L view=%L)"
                                 lib cell view)
                        nil))))
    (setq tbId (strcat lib "/" cell "/" view))
    ;; --- 2. testbench_alias (string | PVT_JSON_NULL)
    (setq alias (pvtProjectAliasFor proj tbId))
    (cond
      ((not (stringp alias)) (setq alias PVT_JSON_NULL)))
    ;; --- 3. author precedence: proj > $USER > "unknown"
    (setq author proj->author)
    (cond
      ((null author)
        (setq author (getShellEnvVar "USER")))
      ((not (stringp author))
        (setq author (getShellEnvVar "USER"))))
    (cond
      ((or (null author) (not (stringp author)) (zerop (strlen author)))
        (setq author "unknown")))
    ;; --- 4. ids
    (setq runId (pvtUnwrap (_pvtCollNewRunId)))
    (setq timestamp (_pvtCollIsoTimestamp))
    ;; --- 5. assemble table in emit-order
    (setq tab (makeTable 'pvtRunMeta PVT_JSON_NULL))
    (setq keys (list "run_id" "project_id" "testbench_id" "testbench_alias"
                     "timestamp" "author" "label" "note" "netlist_path"
                     "history_name"))
    (setarray tab "run_id"          runId)
    (setarray tab "project_id"      proj->project)
    (setarray tab "testbench_id"    tbId)
    (setarray tab "testbench_alias" alias)
    (setarray tab "timestamp"       timestamp)
    (setarray tab "author"          author)
    (setarray tab "label"           PVT_JSON_NULL)
    (setarray tab "note"
              (cond ((and note (stringp note)) note) (t PVT_JSON_NULL)))
    ;; netlist_path begins as null; envelope-builder overwrites if a copy
    ;; succeeded. Pre-populating here keeps the sidecar-key list stable.
    (setarray tab "netlist_path"    PVT_JSON_NULL)
    (setarray tab "history_name"    histName)
    (setarray tab _PVT_JSON_KEYS_PROP keys)
    ;; Stash an internal slot for downstream (history_id is not in schema
    ;; §2.1 but iterate-results needs the handle). The emitter only walks
    ;; sidecar keys, so this slot does NOT leak into the JSON.
    (setarray tab "_history_id"     histID)
    (return (pvtOk tab)))
)


;; ============================================================================
;; Helper: _pvtCollParseCornerParams  (port from POC, verbatim semantics)
;;
;; Cadence-version-dependent shape for `params` is documented in MyRunner/
;; PvtDumpToJson.il. Returns an assoc-style list of (name value) pairs:
;;   - corModelSpec (a list of (group modelName ...)) flattens into a
;;     single ("model" "modelA|modelB|...") pair using "|" as separator.
;;   - numeric values are coerced to %g-formatted strings (matches POC).
;;   - string values pass through.
;;   - everything else is dropped.
;; ============================================================================

(procedure (_pvtCollParseCornerParams params)
  (prog (varMap modelStr pname pval)
    (setq varMap nil)
    (setq modelStr "")
    (foreach p params
      (setq pname p->name)
      (setq pval  p->value)
      (cond
        ((equal pname "corModelSpec")
          (setq modelStr "")
          (foreach entry pval
            (cond
              ((and (listp entry) (cadr entry) (stringp (cadr entry)))
                (setq modelStr
                      (strcat modelStr
                              (cond ((equal modelStr "") "") (t "|"))
                              (cadr entry))))))
          (setq varMap (append varMap (list (list "model" modelStr)))))
        ((numberp pval)
          (setq varMap
                (append varMap
                        (list (list pname (sprintf nil "%g" (times pval 1.0)))))))
        ((stringp pval)
          (setq varMap (append varMap (list (list pname pval)))))
        (t nil)))
    (return varMap))
)


;; ============================================================================
;; Helper: _pvtCollAssocToTable
;;
;; Convert an assoc list ((k1 v1) (k2 v2) ...) into a makeTable with sidecar
;; keys set to the insertion order. Used to build `sweep` and `corner_vars`
;; objects whose keys must serialize stably (S3_DESIGN §3 NEW vs POC).
;;
;; Empty list returns PVT_JSON_EMPTY_OBJECT so emit produces "{}", not "null".
;; ============================================================================

(procedure (_pvtCollAssocToTable assoc tableName)
  (prog (tab keyOrder)
    (cond
      ((null assoc) (return PVT_JSON_EMPTY_OBJECT)))
    (setq tab (makeTable tableName PVT_JSON_NULL))
    (setq keyOrder nil)
    (foreach pair assoc
      (cond
        ((and (listp pair) (stringp (car pair)))
          (setarray tab (car pair) (cadr pair))
          (setq keyOrder (cons (car pair) keyOrder)))))
    (setarray tab _PVT_JSON_KEYS_PROP (reverse keyOrder))
    (return tab))
)


;; ============================================================================
;; Helper: _pvtCollMakeRow
;;
;; Build a single result row table with schema §2.2 keys in emit-order:
;;   point, corner, test, output, value, status, sweep, corner_vars, test_note
;; ============================================================================

(procedure (_pvtCollMakeRow point corner testName outputName value status
                            sweepObj cornerVarsObj testNote)
  (prog (tab keys)
    (setq tab (makeTable 'pvtRow PVT_JSON_NULL))
    (setq keys (list "point" "corner" "test" "output" "value" "status"
                     "sweep" "corner_vars" "test_note"))
    (setarray tab "point"        point)
    (setarray tab "corner"       corner)
    (setarray tab "test"         testName)
    (setarray tab "output"       outputName)
    (setarray tab "value"        value)
    (setarray tab "status"       status)
    (setarray tab "sweep"        sweepObj)
    (setarray tab "corner_vars"  cornerVarsObj)
    (setarray tab "test_note"
              (cond ((and testNote (stringp testNote)) testNote)
                    (t PVT_JSON_NULL)))
    (setarray tab _PVT_JSON_KEYS_PROP keys)
    (return tab))
)


;; ============================================================================
;; Helper: _pvtCollIterateResults(sess histName hsdb)  (S3_DESIGN §3)
;;
;; Walks the result DB of `histName` in 3 passes:
;;   1. OK loop  — for every numeric / non-"wave" string output, emit one row.
;;   2. failed/running — for every test with status != 'done, emit one
;;      __sim_status__ sentinel row.
;;   3. no_convergence — for every (test, point) seen in pass 1 but not
;;      written, emit one __sim_status__ row with status="no_convergence".
;;
;; Returns (pvtOk (firstTestName . listOfRows)) where firstTestName is the
;; first test seen in pass 1 (used by netlist copy) or nil if no rows. The
;; rows list is in proper output-list order.
;;
;; All caches (corner-vars, test-note, sweep-per-pid) are lexical to this
;; proc and die when it returns (S3_DESIGN §E).
;; ============================================================================

(procedure (_pvtCollEvalThunk thunk)
  ;; Evaluate `thunk` under errset; return its value, or nil if SKILL signalled.
  ;; errset returns a list (value) on success, nil on error. We unwrap.
  (prog (r)
    (setq r (errset (funcall thunk) nil))
    (cond
      ((and r (listp r)) (return (car r)))
      (t                 (return nil))))
)


;; ============================================================================
;; Helper: _pvtCollBuildPidListFromTests(tstsForPids)
;;
;; Pure helper extracted from _pvtCollWalkRdb section 2 (Bug B fix). Takes a
;; list of tst-like objects exposing the `pointID` slot via `tst->pointID`
;; and returns the sorted, de-duplicated list of valid pids.
;;
;; Why a separate helper:
;;   The Bug B fix (DECISIONS #23) changed the walker from counting points
;;   via `(while (rdb->point pid) ...)` count-up from pid=1 — which stopped
;;   on the first nil and so under-counted gappy-pid simulations — to
;;   iterating `(rdb->tests)` and reading `tst->pointID`. Direct Tier-1
;;   testing of the live walker is blocked by maeReadResDB being write-
;;   protected (skillbridge putd refuses to override it), so this helper
;;   carries the gappy-pid coverage at the unit-test level. Tier-1 mock
;;   tst objects (defstruct) drive the helper through every dedup / sort /
;;   skip-nil path; the walker calls into this helper and is verified
;;   end-to-end by Tier-2 byte-identical regression on `simkit_verify`.
;;
;; Contract:
;;   - tstsForPids: list of objects exposing `tst->pointID`. May be nil
;;     (returns nil). Per-test slot access is errset-wrapped via
;;     _pvtCollEvalThunk so a tst whose slot read throws is silently
;;     skipped (matches the live walker's classic-SKILL-trap discipline,
;;     DECISIONS #16).
;;   - Returns: list of pids ascending, no duplicates, nils removed.
;; ============================================================================

(procedure (_pvtCollBuildPidListFromTests tstsForPids)
  (let (pidList tstPid)
    (foreach tst tstsForPids
      (setq tstPid (_pvtCollEvalThunk (lambda () tst->pointID)))
      (cond
        ((and tstPid (null (member tstPid pidList)))
          (setq pidList (cons tstPid pidList)))))
    (sort pidList 'lessp)))



;; ============================================================================
;; Helper: _pvtCollWalkRdb(sess histName hsdb)
;;   Step 1 of the Plan-B §3 refactor (docs/plans/§3_messy_data.md §1).
;;
;; Live-side walker. Opens the result DB, enumerates points / tests / outputs /
;; corner params / test notes, and packages everything as raw tuples + caches
;; in a `walkData` makeTable. Does NO row shaping, NO sentinel decisions, NO
;; "" → nil flattening — that work belongs to _pvtCollRowsFromTuples (Step 2).
;;
;; Returns (pvtOk walkData) where walkData carries these keys:
;;
;;   "outputs"          : list of (pid cornerName testName outputName outputValue)
;;                        — raw per-output tuples. cornerName / testName /
;;                        outputName are strings; outputValue is whatever
;;                        pt->outputs emitted (number, string, "wave", nil for
;;                        failed-to-evaluate, etc).
;;   "tests"            : list of (pid cornerName testName statusSym).
;;                        statusSym stays a symbol (or nil / non-symbol) so the
;;                        shaper can exercise the _pvtSymToStr fallback path.
;;   "pidSweepCache"    : alist ((pid sweepAssoc) ...). sweepAssoc is the raw
;;                        result of (maeGetParamConditions pid), pre-
;;                        _pvtCollAssocToTable.
;;   "cornerParamCache" : alist ((cornerName paramsList) ...). paramsList is the
;;                        raw (funcall cornerObj->params) output, pre-
;;                        _pvtCollParseCornerParams. nil entry means cornerObj
;;                        was not resolvable from this row's path.
;;   "testNoteCache"    : alist ((testName noteOrNil) ...). The raw axlGetNote
;;                        result. nil = no note registered; "" preserved
;;                        verbatim for the shaper to flatten.
;;   "totalPoints"      : int.
;;
;; All caches are "raw" so Tier-1 tests of _pvtCollRowsFromTuples can exercise
;; _pvtCollAssocToTable / _pvtCollParseCornerParams / note flattening from
;; synthetic inputs. Alists use 2-element list entries — NOT dotted pairs —
;; because classic SKILL `cons` requires its 2nd arg to be a list (DECISIONS
;; #14 trap #6); lookups should use `(cadr (assoc key alist))`.
;;
;; Returns pvtErr 'pvt_collect_iter (same category as _pvtCollIterateResults)
;; when maeReadResDB cannot locate the history. Live throws inside the walk
;; are swallowed-to-nil via _pvtCollEvalThunk — matching existing behaviour and
;; the rationale in DECISIONS #16 (the wrapper is what *masks* funobj-rule
;; bugs; fixes belong at the call site, not in the wrapper).
;;
;; NOT YET WIRED — _pvtCollIterateResults still owns its own live walk. The
;; swap is Step 3 of the refactor and requires Tier-2 verification via
;; skillbridge against the simkit_verify history. Adding this function
;; alongside the existing path is a zero-behaviour-change extraction so the
;; diff stays reviewable per piece.
;; ============================================================================

(procedure (_pvtCollWalkRdb sess histName hsdb)
  (prog (rdbR rdb totalPoints pid pt
         outputs tests
         pidSweepCache cornerParamCache testNoteCache
         cname tname outName outVal
         tstStatus testObj cornerObj
         paramR noteR
         tstPid pidList tstsForPids)
    ;; --- 1. open the result DB
    (setq rdbR (errset (maeReadResDB ?historyName histName ?session sess) nil))
    (setq rdb (cond ((and rdbR (listp rdbR)) (car rdbR)) (t nil)))
    (cond
      ((null rdb)
        (return (pvtErr 'pvt_collect_iter
                        (sprintf nil "history %L not found in session %L"
                                 histName sess)
                        nil))))
    ;; --- 2. build pid list from (rdb->tests)→tst->pointID. Bug B fix
    ;;        (Step 4): the pre-fix walker counted points by `(while
    ;;        (rdb->point pid) ...)` from pid=1, which stopped at the
    ;;        first nil — so a gappy pid sequence (e.g. pid 3 missing,
    ;;        pid 4 present) under-counted totalPoints and pid 4 was
    ;;        never visited. tst->pointID gives the authoritative set
    ;;        of pids that actually exist in the rdb. Dedup+sort logic
    ;;        lives in _pvtCollBuildPidListFromTests so Tier-1 can cover
    ;;        the gappy/dup/nil cases directly (DECISIONS #23 update).
    (setq tstsForPids
          (or (_pvtCollEvalThunk (lambda () (funcall (rdb->tests)))) nil))
    (setq pidList (_pvtCollBuildPidListFromTests tstsForPids))
    (setq totalPoints (length pidList))
    ;; --- 3. walk state — alists, head-first; reversed at return for stable order.
    (setq outputs          nil)
    (setq tests            nil)
    (setq pidSweepCache    nil)
    (setq cornerParamCache nil)
    (setq testNoteCache    nil)
    ;; --- 4. open results for parameter introspection
    (errset (maeOpenResults ?history histName ?session sess) nil)
    (unwindProtect
      (progn
        ;; --- 5. per-point: stage sweep + outputs + lazily populate corner /
        ;;       test-note caches. Iterate the gap-tolerant pidList built in
        ;;       section 2 (Bug B fix).
        (foreach pid pidList
          (setq pt (_pvtCollEvalThunk (lambda () (funcall (rdb->point) pid))))
          (cond
            (pt
              (cond
                ((null (assoc pid pidSweepCache))
                  (setq paramR
                        (_pvtCollEvalThunk
                          (lambda () (maeGetParamConditions pid))))
                  (setq pidSweepCache
                        (cons (list pid paramR) pidSweepCache))))
              (foreach out
                       (or (_pvtCollEvalThunk
                             (lambda () (funcall (pt->outputs) ?type 'expr)))
                           nil)
                (setq cname   (_pvtCollEvalThunk (lambda () out->cornerName)))
                (setq tname   (_pvtCollEvalThunk (lambda () out->testName)))
                (setq outName (_pvtCollEvalThunk (lambda () out->name)))
                (setq outVal  (_pvtCollEvalThunk (lambda () out->value)))
                (setq outputs
                      (cons (list pid cname tname outName outVal) outputs))
                (cond
                  ((and cname (null (assoc cname cornerParamCache)))
                    (setq testObj
                          (_pvtCollEvalThunk (lambda () (funcall out->test))))
                    (setq cornerObj
                          (cond
                            (testObj
                              (_pvtCollEvalThunk
                                (lambda () (funcall testObj->corner))))
                            (t nil)))
                    (setq cornerParamCache
                          (cons
                            (list cname
                                  (cond
                                    (cornerObj
                                      (_pvtCollEvalThunk
                                        (lambda () (funcall cornerObj->params))))
                                    (t nil)))
                            cornerParamCache))))
                (cond
                  ((and tname (null (assoc tname testNoteCache)))
                    (setq noteR
                          (_pvtCollEvalThunk
                            (lambda () (axlGetNote hsdb "test" tname))))
                    (setq testNoteCache
                          (cons (list tname noteR) testNoteCache))))))))
        ;; --- 6. per-test: tuples + back-fill caches for tests that had no
        ;;       outputs (running / failed tests). Matches the existing
        ;;       _pvtCollIterateResults pass-2 cache-population paths.
        (foreach tst
                 (or (_pvtCollEvalThunk (lambda () (funcall (rdb->tests)))) nil)
          (setq tstStatus (_pvtCollEvalThunk (lambda () tst->status)))
          (setq tstPid    (_pvtCollEvalThunk (lambda () tst->pointID)))
          (setq cname     (_pvtCollEvalThunk (lambda () tst->cornerName)))
          (setq tname     (_pvtCollEvalThunk (lambda () tst->name)))
          (setq tests (cons (list tstPid cname tname tstStatus) tests))
          (cond
            ((and tstPid (null (assoc tstPid pidSweepCache)))
              (setq paramR
                    (_pvtCollEvalThunk
                      (lambda () (maeGetParamConditions tstPid))))
              (setq pidSweepCache
                    (cons (list tstPid paramR) pidSweepCache))))
          (cond
            ((and cname (null (assoc cname cornerParamCache)))
              (setq cornerObj
                    (_pvtCollEvalThunk (lambda () (funcall tst->corner))))
              (setq cornerParamCache
                    (cons
                      (list cname
                            (cond
                              (cornerObj
                                (_pvtCollEvalThunk
                                  (lambda () (funcall cornerObj->params))))
                              (t nil)))
                      cornerParamCache))))
          (cond
            ((and tname (null (assoc tname testNoteCache)))
              (setq noteR
                    (_pvtCollEvalThunk
                      (lambda () (axlGetNote hsdb "test" tname))))
              (setq testNoteCache
                    (cons (list tname noteR) testNoteCache))))))
      ;; cleanup
      (errset (maeCloseResults) nil))
    ;; --- 7. package walkData (insertion-ordered: reverse the head-first lists)
    (setq walkData (makeTable 'pvtCollectWalk nil))
    (setarray walkData "outputs"          (reverse outputs))
    (setarray walkData "tests"            (reverse tests))
    (setarray walkData "pidSweepCache"    (reverse pidSweepCache))
    (setarray walkData "cornerParamCache" (reverse cornerParamCache))
    (setarray walkData "testNoteCache"    (reverse testNoteCache))
    (setarray walkData "totalPoints"      totalPoints)
    (return (pvtOk walkData)))
)


;; ============================================================================
;; Helper: _pvtCollRowsFromTuples(walkData)
;;   Step 2 of the Plan-B §3 refactor (docs/plans/§3_messy_data.md §1, §7).
;;
;; PURE shaping function — no live calls, no errset. Takes the `walkData`
;; produced by _pvtCollWalkRdb and runs the three result-shaping passes:
;;
;;   pass 1 — OK rows: every numeric / non-"wave" string output emits an
;;            "ok" row. Builds seenSet / writtenSet / keysSeen for pass 3.
;;   pass 2 — failed/running sentinel rows: every test whose statusSym is
;;            non-nil and != 'done emits one __sim_status__ row with the
;;            mapped status string ("failed", "running", `_pvtSymToStr` for
;;            other symbols, or "running" fallback for non-symbol). NOTE:
;;            this preserves the existing _pvtCollIterateResults behaviour
;;            of emitting the sentinel even when pass 1 already wrote rows
;;            for the same (cname,pid) — that is Bug C per Plan-B §3 and
;;            the fix lands as part of Step 4.
;;   pass 3 — no_convergence sentinel rows: for every (cname,pid) seen in
;;            pass 1 but not written, emit one __sim_status__ row with
;;            status "no_convergence".
;;
;; Returns (firstTestName . listOfRows) — the same shape that
;; _pvtCollIterateResults returns wrapped in pvtOk today. firstTestName is
;; the first test that contributed an ok row in pass 1 (used by the netlist
;; copy), or nil if pass 1 emitted nothing.
;;
;; Lookups against walkData caches use 2-list assoc entries:
;;   (cadr (assoc key alist)) — value (could be nil)
;;   (assoc key alist)        — the pair itself (nil iff key absent)
;; This lets the shaper distinguish "key absent" (e.g. unreachable corner)
;; from "key present with nil value" (e.g. cornerObj resolution failed).
;; Unified fallback marker (Step 4 / Bug D): all three passes emit
;; "_no_corner_vars" when corner_vars resolution fails, regardless of
;; the reason (cache miss vs cornerObj parse failure). The validator W1
;; generically warns on any "_"-prefixed key, and "_no_corner_vars" is
;; the canonical marker named in validate.py.
;;
;; NOT YET WIRED — see _pvtCollWalkRdb header.
;; ============================================================================

(procedure (_pvtCollRowsFromTuples walkData)
  (prog (outputs tests pidSweepCache cornerParamCache testNoteCache
         pid cname tname outName outVal statusSym
         sweepRaw noteRaw cornerParamsAssoc
         sweepObj cornerVarsObj cornerVarsAlist noteForRow
         seenSet writtenSet writtenByTest seenKey writtenByTestKey keysSeen
         rows firstTestName statusStr
         tstPid tstCname tstTname)
    ;; --- 1. extract walkData fields
    (setq outputs          (arrayref walkData "outputs"))
    (setq tests            (arrayref walkData "tests"))
    (setq pidSweepCache    (arrayref walkData "pidSweepCache"))
    (setq cornerParamCache (arrayref walkData "cornerParamCache"))
    (setq testNoteCache    (arrayref walkData "testNoteCache"))
    ;; --- 2. shape state
    (setq seenSet       (makeTable 'pvtSeen    nil))
    (setq writtenSet    (makeTable 'pvtWritten nil))
    ;; Per-(cname,pid,tname) write set — pass-2 uses this for I1-safe skip.
    ;; writtenSet alone is too coarse: different tests at the same (cname,pid)
    ;; are independent I1 triples and must not block each other's sentinels.
    (setq writtenByTest (makeTable 'pvtWrittenByTest nil))
    (setq keysSeen      nil)
    (setq rows          nil)
    (setq firstTestName nil)
    ;; ===== PASS 1: OK rows ====================================================
    (foreach outTuple outputs
      (setq pid     (nthelem 1 outTuple))
      (setq cname   (nthelem 2 outTuple))
      (setq tname   (nthelem 3 outTuple))
      (setq outName (nthelem 4 outTuple))
      (setq outVal  (nthelem 5 outTuple))
      (cond
        ((and cname tname)
          (setq seenKey (sprintf nil "%s|%d" cname pid))
          (cond
            ((null (arrayref seenSet seenKey))
              (setq sweepRaw (cadr (assoc pid pidSweepCache)))
              (setq sweepObj (_pvtCollAssocToTable sweepRaw 'pvtSweep))
              (setarray seenSet seenKey (list tname cname pid sweepObj))
              (setq keysSeen (cons seenKey keysSeen))))
          (cond
            ((or (numberp outVal)
                 (and (stringp outVal) (not (equal outVal "wave"))))
              ;; corner_vars
              (setq cornerParamsAssoc (assoc cname cornerParamCache))
              (setq cornerVarsAlist
                    (cond
                      ;; Unified Step 4 / Bug D marker for either failure
                      ;; mode (cache miss or cornerObj parse failure).
                      ((null cornerParamsAssoc)
                        (list (list "_no_corner_vars" cname)))
                      ((null (cadr cornerParamsAssoc))
                        (list (list "_no_corner_vars" cname)))
                      (t (_pvtCollParseCornerParams (cadr cornerParamsAssoc)))))
              (setq cornerVarsObj
                    (_pvtCollAssocToTable cornerVarsAlist 'pvtCornerVars))
              ;; sweep — re-resolve from cache (the seenSet entry holds an
              ;; isomorphic copy; using the cache makes the lookup uniform
              ;; across passes).
              (setq sweepRaw (cadr (assoc pid pidSweepCache)))
              (setq sweepObj (_pvtCollAssocToTable sweepRaw 'pvtSweep))
              ;; test_note — flatten "" → PVT_JSON_NULL (existing pattern).
              (setq noteRaw (cadr (assoc tname testNoteCache)))
              (setq noteForRow
                    (cond
                      ((and (stringp noteRaw) (greaterp (strlen noteRaw) 0))
                        noteRaw)
                      (t PVT_JSON_NULL)))
              (setq rows
                    (cons
                      (_pvtCollMakeRow
                        pid cname tname outName outVal "ok"
                        sweepObj cornerVarsObj noteForRow)
                      rows))
              (setarray writtenSet seenKey t)
              (setarray writtenByTest
                        (sprintf nil "%s|%d|%s" cname pid tname) t)
              (cond
                ((null firstTestName) (setq firstTestName tname))))))))
    ;; ===== PASS 2: failed / running sentinel rows ============================
    (foreach tstTuple tests
      (setq tstPid    (nthelem 1 tstTuple))
      (setq tstCname  (nthelem 2 tstTuple))
      (setq tstTname  (nthelem 3 tstTuple))
      (setq statusSym (nthelem 4 tstTuple))
      (cond
        ((and statusSym (not (equal statusSym 'done)))
          (cond
            ((and tstPid tstCname tstTname
                  ;; Bug C fix (Step 4): skip if pass 1 already wrote an ok
                  ;; row for this EXACT (cname,pid,tname). Validator I1 keys
                  ;; sentinel/ok mutual-exclusion by (corner,point,test), so
                  ;; the per-test set is what we need; the coarser writtenSet
                  ;; would wrongly suppress sentinels for sibling tests that
                  ;; share (cname,pid) (e.g. mid-flight: tran=done + noise=running).
                  (null (arrayref writtenByTest
                                  (sprintf nil "%s|%d|%s"
                                           tstCname tstPid tstTname))))
              (setq statusStr
                    (cond
                      ((equal statusSym 'failed)  "failed")
                      ((equal statusSym 'running) "running")
                      ((symbolp statusSym)        (_pvtSymToStr statusSym))
                      ;; Non-symbol status (nil filtered upstream): surface as
                      ;; "unknown" so validator I12 (status enum) hard-errors
                      ;; rather than silently coercing to "running" and hiding
                      ;; the gap. Step 4 / Bug A.
                      (t                          "unknown")))
              (setq sweepRaw (cadr (assoc tstPid pidSweepCache)))
              (setq sweepObj (_pvtCollAssocToTable sweepRaw 'pvtSweep))
              (setq cornerParamsAssoc (assoc tstCname cornerParamCache))
              (setq cornerVarsAlist
                    (cond
                      ;; Unified Step 4 / Bug D marker.
                      ((null cornerParamsAssoc)
                        (list (list "_no_corner_vars" tstCname)))
                      ((null (cadr cornerParamsAssoc))
                        (list (list "_no_corner_vars" tstCname)))
                      (t (_pvtCollParseCornerParams (cadr cornerParamsAssoc)))))
              (setq cornerVarsObj
                    (_pvtCollAssocToTable cornerVarsAlist 'pvtCornerVars))
              (setq noteRaw (cadr (assoc tstTname testNoteCache)))
              (setq noteForRow
                    (cond
                      ((and (stringp noteRaw) (greaterp (strlen noteRaw) 0))
                        noteRaw)
                      (t PVT_JSON_NULL)))
              (setq rows
                    (cons
                      (_pvtCollMakeRow
                        tstPid tstCname tstTname "__sim_status__"
                        PVT_JSON_NULL statusStr
                        sweepObj cornerVarsObj noteForRow)
                      rows)))))))
    ;; ===== PASS 3: no_convergence sentinel rows ==============================
    (foreach key (reverse keysSeen)
      (cond
        ((null (arrayref writtenSet key))
          (let ((entry (arrayref seenSet key)))
            (cond
              ((and entry (listp entry))
                (let ((ttn   (nthelem 1 entry))
                      (cnm   (nthelem 2 entry))
                      (pidn  (nthelem 3 entry))
                      (sweep (nthelem 4 entry)))
                  (setq cornerParamsAssoc (assoc cnm cornerParamCache))
                  (setq cornerVarsAlist
                        (cond
                          ;; Unified Step 4 / Bug D marker.
                          ((null cornerParamsAssoc)
                            (list (list "_no_corner_vars" cnm)))
                          ((null (cadr cornerParamsAssoc))
                            (list (list "_no_corner_vars" cnm)))
                          (t (_pvtCollParseCornerParams (cadr cornerParamsAssoc)))))
                  (setq cornerVarsObj
                        (_pvtCollAssocToTable cornerVarsAlist 'pvtCornerVars))
                  (setq noteRaw (cadr (assoc ttn testNoteCache)))
                  (setq noteForRow
                        (cond
                          ((and (stringp noteRaw) (greaterp (strlen noteRaw) 0))
                            noteRaw)
                          (t PVT_JSON_NULL)))
                  (setq rows
                        (cons
                          (_pvtCollMakeRow
                            pidn cnm ttn "__sim_status__"
                            PVT_JSON_NULL "no_convergence"
                            sweep cornerVarsObj noteForRow)
                          rows)))))))))
    (return (cons firstTestName (reverse rows))))
)


(procedure (_pvtCollIterateResults sess histName hsdb)
  ;; Composer (Plan-B §3 Step 3). Live-side _pvtCollWalkRdb produces a
  ;; raw walkData; pure _pvtCollRowsFromTuples shapes it into the
  ;; (firstTestName . rows) list. Tier-2 verified against simkit_verify
  ;; on 2026-05-11 — byte-identical first-row values and row count (42)
  ;; vs the pre-refactor live walk.
  (prog (walkR)
    (setq walkR (_pvtCollWalkRdb sess histName hsdb))
    (cond
      ((pvtIsErr walkR) (return walkR))
      (t (return (pvtOk (_pvtCollRowsFromTuples (pvtUnwrap walkR)))))))
)


;; ============================================================================
;; Helper: _pvtCollCopyNetlist  (S3_DESIGN §4)
;;
;; Best-effort. Returns "input.scs" on success, nil on any soft miss, with
;; one warn() per miss. NEVER returns pvtErr.
;;
;; Simulator detection (2026-05-12 update): the original implementation
;; called `asiGetAnalogSimulator sess` to gate "is this Spectre?" before
;; the netlist copy. In a Maestro / ADE-XL context that's the wrong API
;; — `asiGet*` is a generic that wants a tool handle, not a Maestro
;; session — and the call falls into the generic-dispatch error path,
;; returning nil and tripping the "simulator nil is not Spectre" warning
;; on every spectre run. Replaced with a *path-based* probe: only Spectre
;; emits `input.scs` into the per-point netlist dir (HSPICE writes
;; `input.cir`, etc.), so testing `(isFile srcPath)` is itself the
;; authoritative gate. The probe and the warning collapse into one step.
;; ============================================================================

(procedure (_pvtCollCopyNetlist sess histID firstTestName absRunDir)
  (prog (netlistDir srcPath dstPath cmd cid status)
    ;; --- 1. empty result-set guard
    (cond
      ((or (null firstTestName) (not (stringp firstTestName)))
        (warn "PvtSave: empty result set — skipping netlist copy\n")
        (return nil)))
    ;; --- 2. resolve top-level netlist dir
    (setq netlistDir
          (errset (axlGetPointNetlistDir histID firstTestName) nil))
    (setq netlistDir
          (cond ((listp netlistDir) (car netlistDir)) (t nil)))
    (cond
      ((or (null netlistDir) (not (stringp netlistDir)))
        (warn "PvtSave: axlGetPointNetlistDir returned nil for test %L — skipping netlist copy\n"
              firstTestName)
        (return nil)))
    ;; --- 3. file presence is the simulator probe — only Spectre emits
    ;;        input.scs. If the file isn't there, the simulator is
    ;;        non-Spectre or the netlist hasn't been written yet; either
    ;;        way the right move is a clean soft-miss with the path so
    ;;        the user can see what was looked for.
    (setq srcPath (strcat netlistDir "/input.scs"))
    (cond
      ((not (isFile srcPath))
        (warn "PvtSave: no Spectre netlist at %L — skipping netlist copy (non-Spectre simulator or netlist not yet emitted)\n"
              srcPath)
        (return nil)))
    ;; --- 4. shell out to cp. The `--` guard prevents any leading-dash
    ;;        surprise; %L produces "/path/with quotes" so paths containing
    ;;        odd chars survive the shell round-trip.
    (setq dstPath (strcat absRunDir "/input.scs"))
    (setq cmd (sprintf nil "cp -- %L %L" srcPath dstPath))
    (setq cid (errset (ipcBeginProcess cmd) nil))
    (cond
      ((or (null cid) (not (listp cid)) (null (car cid)))
        (warn "PvtSave: could not spawn cp for %L — skipping netlist copy\n"
              srcPath)
        (return nil)))
    (setq cid (car cid))
    (errset (ipcWait cid) nil)
    (setq status (errset (ipcGetExitStatus cid) nil))
    (cond
      ((or (null status) (not (listp status)))
        (warn "PvtSave: cp exit status unavailable for %L — skipping netlist copy\n"
              srcPath)
        (return nil))
      ((not (zerop (car status)))
        (warn "PvtSave: cp exited %d for %L — skipping netlist copy\n"
              (car status) srcPath)
        (return nil)))
    (return "input.scs"))
)


;; ============================================================================
;; Helper: _pvtCollCaptureScreenshot  (S3_DESIGN §5 — v1 stub)
;;
;; Always returns nil. With requested=t, emits exactly one warn per Cadence
;; session (gated on _pvtCollScreenshotWarned). The TODO §3.5 reference is
;; intentional and will be redirected to DECISIONS #16 by S4.
;; ============================================================================

(procedure (_pvtCollCaptureScreenshot absRunDir requested)
  (prog ()
    (cond
      ((and requested (null _pvtCollScreenshotWarned))
        (warn "PvtSave ?captureScreenshot t: screenshot capture deferred to v1.1 — see TODO.md §3.5\n")
        (setq _pvtCollScreenshotWarned t)))
    (return nil))
)


;; ============================================================================
;; Helper: _pvtCollBuildEnvelope  (S3_DESIGN §7)
;;
;; Pure construction. Output table has exactly four keys in order:
;;   schema_version, run, results, artifacts
;; ============================================================================

(procedure (_pvtCollBuildEnvelope runMeta results netlistPath artifacts)
  (prog (env keys)
    ;; Overwrite the pre-populated netlist_path slot if a copy succeeded.
    (cond
      ((and netlistPath (stringp netlistPath))
        (setarray runMeta "netlist_path" netlistPath)))
    (setq env (makeTable 'pvtEnvelope PVT_JSON_NULL))
    (setq keys (list "schema_version" "run" "results" "artifacts"))
    (setarray env "schema_version" 1)            ; INTEGER, not "1"
    (setarray env "run"            runMeta)
    ;; results: empty list -> emit "[]" via the empty-array sentinel; non-
    ;; empty list emits as a JSON array natively.
    (setarray env "results"
              (cond ((null results) PVT_JSON_EMPTY_ARRAY) (t results)))
    (setarray env "artifacts"
              (cond ((null artifacts) PVT_JSON_EMPTY_ARRAY) (t artifacts)))
    (setarray env _PVT_JSON_KEYS_PROP keys)
    (return (pvtOk env)))
)


;; ============================================================================
;; Helper: _pvtCollWriteRunJson  (S3_DESIGN §8)
;;
;; Owns the open/close pair via unwindProtect. Compact (no pretty-printing).
;; Returns (pvtOk t_absJsonPath) or pvtErr 'pvt_collect_io.
;; ============================================================================

(procedure (_pvtCollWriteRunJson envelope absRunDir)
  (prog (jsonPath port writeR)
    (setq jsonPath (strcat absRunDir "/run.json"))
    (setq port (outfile jsonPath "w"))
    (cond
      ((null port)
        (return (pvtErr 'pvt_collect_io
                        (sprintf nil "could not open %L for writing" jsonPath)
                        nil))))
    (setq writeR
          (errset
            (unwindProtect
              (pvtJsonEmitToPort envelope port)
              (close port))
            nil))
    (cond
      ((null writeR)
        (return (pvtErr 'pvt_collect_io
                        (sprintf nil "I/O error while writing %L" jsonPath)
                        nil))))
    (return (pvtOk jsonPath)))
)


;; ============================================================================
;; Public: PvtSave(?histName ?label ?note ?captureScreenshot)
;;
;; See S3_DESIGN.md §9 for the body sketch this implementation follows.
;; Returns (pvtOk t_absJsonPath) or pvtErr.
;; ============================================================================

(procedure (PvtSave @key (histName nil) (label nil) (note nil)
                        (captureScreenshot nil))
  (prog (projR proj sessR sessTuple sess hsdb histID resolvedName
         captR runMeta iterR iterPair firstTest results
         runDirR runDir netlistPath envR envelope writeR)
    ;; --- 1. project
    (setq projR (pvtLoadPvtProject))
    (cond ((pvtIsErr projR) (return projR)))
    (setq proj (pvtUnwrap projR))
    ;; --- 2. session + history
    (setq sessR (_pvtCollResolveSession histName))
    (cond ((pvtIsErr sessR) (return sessR)))
    (setq sessTuple (pvtUnwrap sessR))
    (setq sess         (nthelem 1 sessTuple))
    (setq hsdb         (nthelem 2 sessTuple))
    (setq histID       (nthelem 3 sessTuple))
    (setq resolvedName (nthelem 4 sessTuple))
    ;; --- 3. metadata capture (with caller-supplied note)
    (setq captR (_pvtCollAutoCapture proj sess histID resolvedName note))
    (cond ((pvtIsErr captR) (return captR)))
    (setq runMeta (pvtUnwrap captR))
    ;; ?label is intentionally NULL at dump time (D #11). Caller-supplied
    ;; label is folded in only if non-nil, matching pseudocode in S3_DESIGN.
    (cond
      ((and label (stringp label))
        (setarray runMeta "label" label)))
    ;; --- 4. results iteration
    (setq iterR (_pvtCollIterateResults sess resolvedName hsdb))
    (cond ((pvtIsErr iterR) (return iterR)))
    (setq iterPair (pvtUnwrap iterR))
    (setq firstTest (car iterPair))
    (setq results   (cdr iterPair))
    ;; --- 5. mkdir, copy netlist, screenshot stub
    (setq runDirR (_pvtCollMakeRunDir proj->dbRoot
                                      (arrayref runMeta "run_id")))
    (cond ((pvtIsErr runDirR) (return runDirR)))
    (setq runDir (pvtUnwrap runDirR))
    (setq netlistPath
          (_pvtCollCopyNetlist sess histID firstTest runDir))
    (_pvtCollCaptureScreenshot runDir captureScreenshot)
    ;; --- 6. envelope
    (setq envR (_pvtCollBuildEnvelope runMeta results netlistPath nil))
    (cond ((pvtIsErr envR) (return envR)))
    (setq envelope (pvtUnwrap envR))
    ;; --- 7. write
    (setq writeR (_pvtCollWriteRunJson envelope runDir))
    (cond ((pvtIsErr writeR) (return writeR)))
    (return writeR))
)


t  ; load-time return
