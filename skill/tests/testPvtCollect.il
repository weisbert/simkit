;; ============================================================================
;; testPvtCollect.il -- Tier-1 unit tests for skill/pvtCollect.il.
;;
;; Tier 1 = no live ADE-XL session required. The collector's pure helpers
;; (_pvtCollNewRunId, _pvtCollMakeRunDir, _pvtCollIsoTimestamp,
;; _pvtCollBuildEnvelope, _pvtCollWriteRunJson, _pvtCollCaptureScreenshot)
;; and the end-to-end PvtSave error paths (no .pvtproject, no session) are
;; exercised here. Live-session paths (Tier 2) and golden-file diff
;; (Tier 3) are deliberately out of scope per S3_DESIGN.md.
;;
;; Driven by `runTests.il` — this file only registers thunks via
;; pvtTestRegister; pvtTestRun() in the harness drives them.
;; ============================================================================

;; --------------------------------------------------------------------------
;; helpers
;; --------------------------------------------------------------------------

(procedure (_pvtCollTestEnsureScratchDir prefix)
  "Create a unique empty subdirectory under the system tempdir and return
   its absolute path. Caller is responsible for cleanup; we leave artefacts
   behind because /tmp is OS-managed (matches testPvtProject.il convention)."
  (let ((root (getTempDir))
        (id (sprintf nil "%s_%d_%d" prefix (random 1000000) (random 1000000)))
        (dir nil))
    (setq dir (strcat root "/" id))
    (system (sprintf nil "mkdir -p %s" dir))
    (simplifyFilename dir))
)

(procedure (_pvtCollTestUuidShape s)
  "t iff `s` matches RFC 4122 v4 string shape."
  (and (stringp s)
       (equal (strlen s) 36)
       (pcreMatchp
         "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
         s))
)


;; --------------------------------------------------------------------------
;; UUID generator: shape, uniqueness, no trailing newline.
;; --------------------------------------------------------------------------

(pvtTestRegister "collect/uuid-shape-single"
  (lambda ()
    (let ((r (_pvtCollNewRunId)))
      (pvtAssertOk r "newRunId returns :ok")
      (when (pvtIsOk r)
        (let ((s (pvtUnwrap r)))
          (pvtAssert (stringp s) t "uuid is a string")
          (pvtAssert (strlen s) 36 "uuid length 36")
          (pvtAssert (_pvtCollTestUuidShape s) t "uuid shape matches v4 regex"))))))

(pvtTestRegister "collect/uuid-no-trailing-newline"
  (lambda ()
    (let ((r (_pvtCollNewRunId)))
      (when (pvtIsOk r)
        (let ((s (pvtUnwrap r)))
          ;; substring 36 1 returns the 36th char; if length is exactly 36
          ;; there is no trailing \n. We assert that the LAST char is hex,
          ;; which together with the length check above precludes "\n".
          (pvtAssert (pcreMatchp "[0-9a-f]$" s) t
                     "uuid last char is hex (no trailing newline)"))))))

(pvtTestRegister "collect/uuid-unique-100x"
  (lambda ()
    ;; Generate 100 UUIDs, assert all distinct. We use a makeTable as a set.
    (let ((seen (makeTable 'pvtUuidTest nil))
          (count 100)
          (i 0)
          (collisions 0))
      (while (lessp i count)
        (let ((r (_pvtCollNewRunId)))
          (when (pvtIsOk r)
            (let ((s (pvtUnwrap r)))
              (cond
                ((arrayref seen s)
                  (setq collisions (plus collisions 1)))
                (t
                  (setarray seen s t))))))
        (setq i (plus i 1)))
      (pvtAssert collisions 0
                 (sprintf nil "no UUID collisions across %d calls" count)))))


;; --------------------------------------------------------------------------
;; ISO 8601 timestamp shape.
;; --------------------------------------------------------------------------

(pvtTestRegister "collect/timestamp-iso-shape"
  (lambda ()
    (let ((s (_pvtCollIsoTimestamp)))
      (pvtAssert (stringp s) t "timestamp is a string")
      ;; Either a real ISO offset (±HH:MM) or the UTC fallback. Both shapes
      ;; should at minimum match the offset suffix or be the bareword fallback.
      (pvtAssert
        (or (pcreMatchp
              "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[+-][0-9]{2}:[0-9]{2}$"
              s)
            ;; Fallback shape includes "+00:00" suffix on whatever
            ;; getCurrentTime returned.
            (pcreMatchp "[+-][0-9]{2}:[0-9]{2}$" s))
        t "timestamp matches ISO offset shape (or fallback w/ offset suffix)"))))


;; --------------------------------------------------------------------------
;; mkdir -p semantics: success path.
;; --------------------------------------------------------------------------

(pvtTestRegister "collect/mkrundir-creates-tree"
  (lambda ()
    (let ((dbRoot (_pvtCollTestEnsureScratchDir "simkit_mkrun"))
          (runId  "11111111-1111-4111-8111-111111111111"))
      (let ((r (_pvtCollMakeRunDir dbRoot runId)))
        (pvtAssertOk r "mkRunDir returns :ok")
        (when (pvtIsOk r)
          (let ((runDir (pvtUnwrap r)))
            (pvtAssert (stringp runDir) t "runDir is a string")
            ;; Absolute path, no trailing slash.
            (pvtAssert (pcreMatchp "^/" runDir) t "runDir is absolute")
            (pvtAssert (pcreMatchp "/$" runDir) nil "runDir has no trailing slash")
            (pvtAssert (isDir runDir) t "runDir exists")
            (pvtAssert (isDir (strcat runDir "/artifacts")) t
                       "artifacts/ subdir exists")
            ;; The intermediate runs/ directory was created on the way up.
            (pvtAssert (isDir (strcat dbRoot "/runs")) t
                       "runs/ parent dir exists")))))))


;; --------------------------------------------------------------------------
;; mkdir error path: dbRoot does not exist.
;; --------------------------------------------------------------------------

(pvtTestRegister "collect/mkrundir-missing-dbroot-errors"
  (lambda ()
    (let ((bogusRoot "/tmp/__simkit_does_not_exist_zzz12345__"))
      ;; Belt-and-braces: ensure it really doesn't exist.
      (system (sprintf nil "rm -rf %s" bogusRoot))
      (let ((r (_pvtCollMakeRunDir bogusRoot "any-uuid")))
        (pvtAssertErrCategory r 'pvt_collect_io
                              "missing dbRoot -> pvt_collect_io")
        (when (pvtIsErr r)
          (pvtAssert (pcreMatchp "does not exist" (pvtErrMessage r)) t
                     "error msg mentions 'does not exist'"))))))


;; --------------------------------------------------------------------------
;; Envelope builder: keys, key order, schema_version int, empty-array sentinels.
;; --------------------------------------------------------------------------

(procedure (_pvtCollTestSampleRunMeta)
  "Build a hand-rolled runMeta table that mimics what _pvtCollAutoCapture
   would produce for a fixture session. Use this as input to
   _pvtCollBuildEnvelope for offline tests."
  (let ((tab (makeTable 'pvtRunMetaT PVT_JSON_NULL))
        (keys (list "run_id" "project_id" "testbench_id" "testbench_alias"
                    "timestamp" "author" "label" "note" "netlist_path"
                    "history_name")))
    (setarray tab "run_id"          "11111111-1111-4111-8111-111111111111")
    (setarray tab "project_id"      "fake_proj")
    (setarray tab "testbench_id"    "LIB/cell/schematic")
    (setarray tab "testbench_alias" PVT_JSON_NULL)
    (setarray tab "timestamp"       "2026-05-08T12:00:00+00:00")
    (setarray tab "author"          "tester")
    (setarray tab "label"           PVT_JSON_NULL)
    (setarray tab "note"            PVT_JSON_NULL)
    (setarray tab "netlist_path"    PVT_JSON_NULL)
    (setarray tab "history_name"    "Interactive.0")
    (setarray tab _PVT_JSON_KEYS_PROP keys)
    tab)
)

(pvtTestRegister "collect/envelope-key-order"
  (lambda ()
    (let ((envR (_pvtCollBuildEnvelope (_pvtCollTestSampleRunMeta) nil nil nil)))
      (pvtAssertOk envR "buildEnvelope returns :ok")
      (when (pvtIsOk envR)
        (let ((env (pvtUnwrap envR)))
          (pvtAssert (pvtJsonObjectKeys env)
                     '("schema_version" "run" "results" "artifacts")
                     "envelope keys in canonical order"))))))

(pvtTestRegister "collect/envelope-schema-version-int"
  (lambda ()
    (let ((envR (_pvtCollBuildEnvelope (_pvtCollTestSampleRunMeta) nil nil nil)))
      (when (pvtIsOk envR)
        (let ((env (pvtUnwrap envR)))
          (pvtAssert (arrayref env "schema_version") 1 "schema_version == 1")
          (pvtAssert (integerp (arrayref env "schema_version")) t
                     "schema_version is integer"))))))

(pvtTestRegister "collect/envelope-empty-artifacts-is-array-sentinel"
  (lambda ()
    (let ((envR (_pvtCollBuildEnvelope (_pvtCollTestSampleRunMeta) nil nil nil)))
      (when (pvtIsOk envR)
        (let ((env (pvtUnwrap envR)))
          (pvtAssert (pvtJsonEmptyArrayp (arrayref env "artifacts")) t
                     "empty artifacts uses PVT_JSON_EMPTY_ARRAY sentinel"))))))

(pvtTestRegister "collect/envelope-empty-results-is-array-sentinel"
  (lambda ()
    (let ((envR (_pvtCollBuildEnvelope (_pvtCollTestSampleRunMeta) nil nil nil)))
      (when (pvtIsOk envR)
        (let ((env (pvtUnwrap envR)))
          (pvtAssert (pvtJsonEmptyArrayp (arrayref env "results")) t
                     "empty results uses PVT_JSON_EMPTY_ARRAY sentinel"))))))

(pvtTestRegister "collect/envelope-netlist-path-overwritten"
  (lambda ()
    (let ((envR (_pvtCollBuildEnvelope (_pvtCollTestSampleRunMeta)
                                       nil "input.scs" nil)))
      (when (pvtIsOk envR)
        (let ((env (pvtUnwrap envR)))
          (let ((run (arrayref env "run")))
            (pvtAssert (arrayref run "netlist_path") "input.scs"
                       "netlist_path overwritten when string supplied")))))))

(pvtTestRegister "collect/envelope-netlist-path-null-when-skipped"
  (lambda ()
    (let ((envR (_pvtCollBuildEnvelope (_pvtCollTestSampleRunMeta) nil nil nil)))
      (when (pvtIsOk envR)
        (let ((env (pvtUnwrap envR)))
          (let ((run (arrayref env "run")))
            (pvtAssert (pvtJsonNullp (arrayref run "netlist_path")) t
                       "netlist_path stays null when nil supplied")))))))


;; --------------------------------------------------------------------------
;; WriteRunJson: produce a file, re-parse via pvtJsonParseFile, assert keys
;; match. Also pipe through `python3 -m json.tool` for schema-level validation.
;; --------------------------------------------------------------------------

(pvtTestRegister "collect/writeRunJson-roundtrips-via-pvtJsonParseFile"
  (lambda ()
    (let ((scratch (_pvtCollTestEnsureScratchDir "simkit_writejson")))
      (let ((envR (_pvtCollBuildEnvelope (_pvtCollTestSampleRunMeta)
                                         nil nil nil)))
        (when (pvtIsOk envR)
          (let ((wR (_pvtCollWriteRunJson (pvtUnwrap envR) scratch)))
            (pvtAssertOk wR "writeRunJson returns :ok")
            (when (pvtIsOk wR)
              (let ((path (pvtUnwrap wR)))
                (pvtAssert (isFile path) t "run.json exists at returned path")
                ;; Re-parse with the same parser to verify it's well-formed JSON
                (let ((parsed (pvtJsonParseFile path)))
                  (pvtAssertOk parsed "re-parse via pvtJsonParseFile")
                  (when (pvtIsOk parsed)
                    (let ((env (pvtUnwrap parsed)))
                      (pvtAssert (pvtJsonObjectKeys env)
                                 '("schema_version" "run" "results" "artifacts")
                                 "round-trip key order")
                      (pvtAssert (arrayref env "schema_version") 1
                                 "round-trip schema_version int"))))))))))))

(pvtTestRegister "collect/writeRunJson-validates-via-python"
  (lambda ()
    ;; Belt-and-braces: pipe the produced file through `python3 -m json.tool`
    ;; and assert exit 0. This catches any byte-level malformation that the
    ;; SKILL parser might tolerate but external tools would not.
    (let ((scratch (_pvtCollTestEnsureScratchDir "simkit_writejson_py")))
      (let ((envR (_pvtCollBuildEnvelope (_pvtCollTestSampleRunMeta)
                                         nil nil nil)))
        (when (pvtIsOk envR)
          (let ((wR (_pvtCollWriteRunJson (pvtUnwrap envR) scratch)))
            (when (pvtIsOk wR)
              (let ((path (pvtUnwrap wR)))
                ;; system returns the shell exit value (sklangref p.638).
                ;; Send stdout / stderr to /dev/null so the test log stays clean.
                (let ((rc (system (sprintf nil
                                           "python3 -m json.tool %s >/dev/null 2>&1"
                                           path))))
                  ;; system returns the shell exit code: 0 = success.
                  ;; Some Cadence builds return t/nil, others return the
                  ;; integer exit value directly. Accept either "OK" form.
                  (pvtAssert (or (equal rc 0) (equal rc t)) t
                             "python3 -m json.tool accepts run.json"))))))))))


;; --------------------------------------------------------------------------
;; Screenshot capture stub: always nil, warn-once with ?captureScreenshot t.
;; --------------------------------------------------------------------------

(pvtTestRegister "collect/screenshot-stub-returns-nil"
  (lambda ()
    ;; ?captureScreenshot nil branch: returns nil; no warn.
    (let ((rv (_pvtCollCaptureScreenshot "/tmp" nil)))
      (pvtAssert rv nil "screenshot stub returns nil when not requested"))))

(pvtTestRegister "collect/screenshot-stub-warn-when-requested"
  (lambda ()
    ;; Force the warn-gate variable to nil so we can observe a fresh first
    ;; invocation regardless of ordering with other test modules.
    (setq _pvtCollScreenshotWarned nil)
    ;; Two consecutive invocations both return nil. We can't easily intercept
    ;; warn() output, but we CAN observe the gate variable flipping to t after
    ;; the first call — which is the underlying contract.
    (let ((rv1 (_pvtCollCaptureScreenshot "/tmp" t)))
      (pvtAssert rv1 nil "first call returns nil")
      (pvtAssert _pvtCollScreenshotWarned t "warn-gate flipped to t"))
    (let ((rv2 (_pvtCollCaptureScreenshot "/tmp" t)))
      (pvtAssert rv2 nil "second call also returns nil")
      ;; Gate stays t — second call cannot un-set it.
      (pvtAssert _pvtCollScreenshotWarned t "warn-gate stays t after second call"))))


;; --------------------------------------------------------------------------
;; PvtSave error paths.
;; --------------------------------------------------------------------------

(pvtTestRegister "collect/pvtsave-no-pvtproject-errors"
  (lambda ()
    ;; Drive PvtSave under conditions where pvtLoadPvtProject must fail:
    ;;   - PVT_PROJECT env var unset
    ;;   - cwd has no .pvtproject in any ancestor we can guarantee
    ;; We can't unset the env var from inside SKILL portably, so we test
    ;; pvtLoadPvtProject directly with a known-empty start dir + explicit
    ;; ?env "" to bypass the env-var path. Then we verify that PvtSave
    ;; would propagate the same error if it received this projR.
    ;;
    ;; Strategy: invoke pvtLoadPvtProject with ?env "" ?start "<scratch>"
    ;; — that scratch dir has no .pvtproject. The result MUST be
    ;; pvtErr 'pvt_notFound and the message MUST mention both PVT_PROJECT
    ;; and .pvtproject (S3_DESIGN test point Q-F).
    (let ((scratch (_pvtCollTestEnsureScratchDir "simkit_nopvtproj")))
      ;; Ensure scratch is genuinely free of any .pvtproject ancestor on
      ;; this host: walk up and delete any stray we might have made earlier.
      ;; For the test we check that the load returns the expected error;
      ;; if a host-level .pvtproject above /tmp exists we cannot prevent it
      ;; from leaking into the walker. Skip the assertion in that case.
      (let ((found (pvtFindPvtProject scratch)))
        (cond
          ((null found)
            (let ((projR (pvtLoadPvtProject ?start scratch ?env ""
                                            ?allowDialog nil)))
              (pvtAssertErrCategory projR 'pvt_notFound
                                    "no .pvtproject -> pvt_notFound")
              (when (pvtIsErr projR)
                (let ((msg (pvtErrMessage projR)))
                  (pvtAssert (and (pcreMatchp "PVT_PROJECT" msg)
                                  (pcreMatchp "\\.pvtproject" msg))
                             t
                             "msg mentions both PVT_PROJECT and .pvtproject")))))
          (t
            ;; Host has a stray .pvtproject above /tmp — record a skip-style
            ;; note but don't fail; this isn't a code defect.
            (setq _pvtTestSkip (plus _pvtTestSkip 1))
            (setq _pvtTestLog
                  (cons (sprintf nil "  SKIP  collect/pvtsave-no-pvtproject-errors : host has %L"
                                 found)
                        _pvtTestLog))))))))

(pvtTestRegister "collect/pvtsave-no-session-errors-via-resolveSession"
  (lambda ()
    ;; PvtSave needs an ADE-XL session; on this dev host there is no session
    ;; (axlNoSession() => t). _pvtCollResolveSession is the entrypoint that
    ;; surfaces this; we hit it directly to avoid coupling the test to the
    ;; pvtLoadPvtProject precondition.
    (let ((r (_pvtCollResolveSession nil)))
      (pvtAssertErrCategory r 'pvt_collect_session
                            "no session -> pvt_collect_session"))))


;; --------------------------------------------------------------------------
;; _pvtCollRowsFromTuples — pure-shaper tests (Plan-B §3 Step 2)
;;
;; Each test builds a synthetic walkData via _pvtCollTestMakeWalk and calls
;; the shaper directly. No live ADE-XL session needed. Tests document the
;; *current* behaviour, including Bug A / C / D quirks called out in
;; Plan-B §3 — those flip to "fixed" assertions in Step 4.
;; --------------------------------------------------------------------------

(procedure (_pvtCollTestMakeWalk outputs tests pidSweepCache
                                 cornerParamCache testNoteCache totalPoints)
  "Build a synthetic walkData makeTable matching the _pvtCollWalkRdb contract."
  (let ((w (makeTable 'pvtCollectTestWalk nil)))
    (setarray w "outputs"          outputs)
    (setarray w "tests"            tests)
    (setarray w "pidSweepCache"    pidSweepCache)
    (setarray w "cornerParamCache" cornerParamCache)
    (setarray w "testNoteCache"    testNoteCache)
    (setarray w "totalPoints"      totalPoints)
    w)
)


(pvtTestRegister "collect/rows-empty-walk-returns-nil-firstTest-and-empty-rows"
  (lambda ()
    (let ((walk (_pvtCollTestMakeWalk nil nil nil nil nil 0))
          (result nil))
      (setq result (_pvtCollRowsFromTuples walk))
      (pvtAssert (car result) nil "firstTestName is nil")
      (pvtAssert (cdr result) nil "rows list empty"))))


(pvtTestRegister "collect/rows-single-ok-output-emits-ok-row"
  (lambda ()
    (let ((walk (_pvtCollTestMakeWalk
                  (list (list 1 "TT" "tran" "vout" 1.234))
                  nil
                  (list (list 1 nil))            ; sweep cache; nil → empty-obj
                  (list (list "TT" nil))         ; cornerObj resolution failed
                  nil
                  1))
          (result nil) (rows nil) (row nil))
      (setq result (_pvtCollRowsFromTuples walk))
      (setq rows (cdr result))
      (pvtAssert (length rows) 1 "exactly 1 row")
      (pvtAssert (car result) "tran" "firstTestName = tran")
      (setq row (car rows))
      (pvtAssert (arrayref row "status") "ok" "status = ok")
      (pvtAssert (arrayref row "value")  1.234 "value passed through")
      (pvtAssert (arrayref row "test")   "tran" "test name")
      (pvtAssert (arrayref row "corner") "TT" "corner name")
      (pvtAssert (arrayref row "point")  1 "point id")
      (pvtAssert (arrayref row "output") "vout" "output name")
      (pvtAssert (arrayref row "test_note") PVT_JSON_NULL
                 "no testNote → JSON null"))))


(pvtTestRegister "collect/rows-multiple-points-multiple-corners-cross-product"
  (lambda ()
    (let ((walk (_pvtCollTestMakeWalk
                  (list
                    (list 1 "TT"     "tran" "vout"  1.0)
                    (list 1 "WC_VDD" "tran" "vout"  0.9)
                    (list 2 "TT"     "tran" "vout"  1.1)
                    (list 2 "WC_VDD" "tran" "vout"  0.8))
                  nil
                  (list (list 1 nil) (list 2 nil))
                  (list (list "TT" nil) (list "WC_VDD" nil))
                  nil
                  2))
          (result nil))
      (setq result (_pvtCollRowsFromTuples walk))
      (pvtAssert (length (cdr result)) 4 "2 corners × 2 points = 4 rows")
      (pvtAssert (car result) "tran" "firstTestName is the first ok-row's test"))))


(pvtTestRegister "collect/rows-string-value-wave-skipped"
  (lambda ()
    (let ((walk (_pvtCollTestMakeWalk
                  (list (list 1 "TT" "tran" "vout" "wave"))
                  nil
                  (list (list 1 nil))
                  (list (list "TT" nil))
                  nil
                  1))
          (result nil))
      (setq result (_pvtCollRowsFromTuples walk))
      ;; "wave" outputs do not emit an ok row; key IS in seenSet → pass 3
      ;; emits a no_convergence row.
      (pvtAssert (length (cdr result)) 1 "wave skips ok, pass-3 emits 1 sentinel")
      (pvtAssert (arrayref (car (cdr result)) "status") "no_convergence"
                 "wave-only key becomes no_convergence sentinel")
      (pvtAssert (car result) nil
                 "no ok-row emitted → firstTestName stays nil"))))


(pvtTestRegister "collect/rows-string-value-non-wave-emits-ok"
  (lambda ()
    (let ((walk (_pvtCollTestMakeWalk
                  (list (list 1 "TT" "tran" "verdict" "PASS"))
                  nil
                  (list (list 1 nil))
                  (list (list "TT" nil))
                  nil
                  1))
          (result nil))
      (setq result (_pvtCollRowsFromTuples walk))
      (pvtAssert (length (cdr result)) 1 "string value (not 'wave') emits ok")
      (pvtAssert (arrayref (car (cdr result)) "status") "ok" "status = ok")
      (pvtAssert (arrayref (car (cdr result)) "value") "PASS" "value passed through"))))


;; --- eval_err: per-output sentinel for outputs whose rdb value is a list /
;;     nil / other non-shapable type. Pass-1 emits an eval_err row preserving
;;     the output name; pass-3 stays out of the way (writtenSet IS marked).
;;     Mirrors the simkit_Rtime_err case where Rtime_clkout's rdb value comes
;;     back as a list while other outputs in the same triple succeed.

(pvtTestRegister "collect/rows-list-value-emits-eval-err"
  (lambda ()
    (let ((walk (_pvtCollTestMakeWalk
                  (list (list 1 "TT" "tran" "Rtime_clkout" '(eval_err "bad")))
                  nil
                  (list (list 1 nil))
                  (list (list "TT" nil))
                  nil
                  1))
          (result nil) (row nil))
      (setq result (_pvtCollRowsFromTuples walk))
      (pvtAssert (length (cdr result)) 1 "list value emits exactly 1 row")
      (setq row (car (cdr result)))
      (pvtAssert (arrayref row "status") "eval_err" "status = eval_err")
      (pvtAssert (arrayref row "output") "Rtime_clkout"
                 "output name preserved (NOT __sim_status__)")
      (pvtAssert (arrayref row "value") PVT_JSON_NULL "value = null")
      (pvtAssert (arrayref row "test")   "tran" "test name carried")
      (pvtAssert (arrayref row "corner") "TT" "corner carried")
      (pvtAssert (arrayref row "point")  1 "point carried"))))


(pvtTestRegister "collect/rows-nil-value-emits-eval-err"
  (lambda ()
    (let ((walk (_pvtCollTestMakeWalk
                  (list (list 1 "TT" "tran" "weird_out" nil))
                  nil
                  (list (list 1 nil))
                  (list (list "TT" nil))
                  nil
                  1))
          (result nil) (row nil))
      (setq result (_pvtCollRowsFromTuples walk))
      (pvtAssert (length (cdr result)) 1 "nil value emits 1 eval_err row")
      (setq row (car (cdr result)))
      (pvtAssert (arrayref row "status") "eval_err" "status = eval_err")
      (pvtAssert (arrayref row "output") "weird_out" "output preserved"))))


(pvtTestRegister "collect/rows-eval-err-coexists-with-ok-in-same-triple"
  (lambda ()
    (let ((walk (_pvtCollTestMakeWalk
                  ;; Same (point=1, corner=TT, test=tran). 2 ok + 1 eval_err.
                  (list
                    (list 1 "TT" "tran" "PN_1M"        -200.0)
                    (list 1 "TT" "tran" "PN_3M"        -210.0)
                    (list 1 "TT" "tran" "Rtime_clkout" '(eval_err "bad")))
                  nil
                  (list (list 1 nil))
                  (list (list "TT" nil))
                  nil
                  1))
          (result nil) (rows nil)
          (n_ok 0) (n_eval 0) (n_sent 0))
      (setq result (_pvtCollRowsFromTuples walk))
      (setq rows (cdr result))
      (pvtAssert (length rows) 3 "2 ok + 1 eval_err = 3 rows total")
      (foreach r rows
        (cond
          ((equal (arrayref r "status") "ok")             (setq n_ok   (plus n_ok 1)))
          ((equal (arrayref r "status") "eval_err")       (setq n_eval (plus n_eval 1)))
          ((equal (arrayref r "output") "__sim_status__") (setq n_sent (plus n_sent 1)))))
      (pvtAssert n_ok 2 "2 ok rows (the converged outputs)")
      (pvtAssert n_eval 1 "1 eval_err row")
      (pvtAssert n_sent 0 "no triple-level sentinel (writtenSet IS marked)"))))


;; --- Scenario A: partial-convergence corner → no_convergence sentinel
(pvtTestRegister "collect/rows-partial-converge-emits-no-convergence"
  (lambda ()
    (let ((walk (_pvtCollTestMakeWalk
                  (list
                    (list 1 "TT"     "tran" "vout"  1.234)
                    (list 1 "TT"     "tran" "delay" 42e-12)
                    (list 1 "WC_VDD" "tran" "vout"  "wave"))    ; corner failed
                  (list
                    (list 1 "TT"     "tran" 'done)
                    (list 1 "WC_VDD" "tran" 'done))             ; test='done despite per-corner fail
                  (list (list 1 nil))
                  (list (list "TT" nil) (list "WC_VDD" nil))
                  nil
                  1))
          (result nil) (rows nil) (sentinel nil))
      (setq result (_pvtCollRowsFromTuples walk))
      (setq rows (cdr result))
      (pvtAssert (length rows) 3 "2 ok (TT) + 1 no_convergence (WC_VDD)")
      ;; The sentinel is the last row (pass 3 runs after pass 1; rows are
      ;; reversed-once so insertion order is preserved).
      (setq sentinel (nthelem 3 rows))
      (pvtAssert (arrayref sentinel "status") "no_convergence" "WC_VDD → no_convergence")
      (pvtAssert (arrayref sentinel "corner") "WC_VDD" "sentinel corner")
      (pvtAssert (arrayref sentinel "output") "__sim_status__" "sentinel output"))))


;; --- Scenario B: mid-flight PvtSave — test 'running' sentinel
(pvtTestRegister "collect/rows-mid-flight-running-sentinel"
  (lambda ()
    (let ((walk (_pvtCollTestMakeWalk
                  (list (list 1 "TT" "tran" "vout" 1.0))      ; tran finished
                  (list (list 1 "TT" "tran"  'done)
                        (list 1 "TT" "noise" 'running))       ; noise still running
                  (list (list 1 nil))
                  (list (list "TT" nil))
                  nil
                  1))
          (result nil) (rows nil) (sentinel nil))
      (setq result (_pvtCollRowsFromTuples walk))
      (setq rows (cdr result))
      (pvtAssert (length rows) 2 "1 ok (tran) + 1 running sentinel (noise)")
      (setq sentinel (nthelem 2 rows))
      (pvtAssert (arrayref sentinel "status") "running" "noise → status running")
      (pvtAssert (arrayref sentinel "test")   "noise" "sentinel test = noise")
      (pvtAssert (arrayref sentinel "output") "__sim_status__" "sentinel output"))))


;; --- Scenario C: 'failed' test
(pvtTestRegister "collect/rows-failed-test-sentinel"
  (lambda ()
    (let ((walk (_pvtCollTestMakeWalk
                  (list (list 1 "TT" "tran" "vout" 1.0))
                  (list (list 1 "TT" "tran"     'done)
                        (list 1 "TT" "spectreX" 'failed))
                  (list (list 1 nil))
                  (list (list "TT" nil))
                  nil
                  1))
          (result nil) (rows nil) (sentinel nil))
      (setq result (_pvtCollRowsFromTuples walk))
      (setq rows (cdr result))
      (pvtAssert (length rows) 2 "1 ok + 1 failed sentinel")
      (setq sentinel (nthelem 2 rows))
      (pvtAssert (arrayref sentinel "status") "failed" "spectreX → failed"))))


;; --- Scenario D1: aborted symbol passthrough via _pvtSymToStr
(pvtTestRegister "collect/rows-aborted-symbol-passthrough"
  (lambda ()
    (let ((walk (_pvtCollTestMakeWalk
                  nil
                  (list (list 1 "TT" "tran" 'aborted))
                  (list (list 1 nil))
                  (list (list "TT" nil))
                  nil
                  1))
          (result nil) (rows nil))
      (setq result (_pvtCollRowsFromTuples walk))
      (setq rows (cdr result))
      (pvtAssert (length rows) 1 "1 sentinel row")
      (pvtAssert (arrayref (car rows) "status") "aborted"
                 "'aborted symbol → \"aborted\" via _pvtSymToStr"))))


;; --- Scenario D2: non-symbol status → "unknown" fallback (Bug A — post-fix)
(pvtTestRegister "collect/rows-non-symbol-status-becomes-unknown"
  (lambda ()
    ;; Bug A fix (Step 4): the pass-2 statusStr fallback for a non-symbol,
    ;; non-nil status returns "unknown" rather than silently coercing to
    ;; "running". The validator's I12 invariant rejects "unknown" as an
    ;; illegal status, surfacing the upstream gap instead of hiding it.
    ;; NOTE: nil status is filtered out by the outer `(and statusSym ...)`
    ;; guard, so use a non-symbol non-nil status (a string) to exercise
    ;; the fallback arm specifically.
    (let ((walk (_pvtCollTestMakeWalk
                  nil
                  (list (list 1 "TT" "tran" "oddball-string"))
                  (list (list 1 nil))
                  (list (list "TT" nil))
                  nil
                  1))
          (result nil) (rows nil))
      (setq result (_pvtCollRowsFromTuples walk))
      (setq rows (cdr result))
      (pvtAssert (length rows) 1 "1 sentinel row")
      (pvtAssert (arrayref (car rows) "status") "unknown"
                 "non-symbol status → \"unknown\" (Bug A fixed)"))))


;; --- Scenario E1: gappy pid sequence — pure shaper is gap-tolerant
(pvtTestRegister "collect/rows-gappy-pids-shape-correctly"
  (lambda ()
    (let ((walk (_pvtCollTestMakeWalk
                  (list
                    (list 1 "TT" "tran" "v" 1.0)
                    (list 2 "TT" "tran" "v" 2.0)
                    (list 4 "TT" "tran" "v" 4.0))             ; pid 3 missing
                  nil
                  (list (list 1 nil) (list 2 nil) (list 4 nil))
                  (list (list "TT" nil))
                  nil
                  4))                                          ; totalPoints uneven
          (result nil) (rows nil))
      (setq result (_pvtCollRowsFromTuples walk))
      (setq rows (cdr result))
      (pvtAssert (length rows) 3 "3 ok rows even though pid 3 is missing")
      (pvtAssert (arrayref (nthelem 1 rows) "point") 1 "row 1 → pid 1")
      (pvtAssert (arrayref (nthelem 2 rows) "point") 2 "row 2 → pid 2")
      (pvtAssert (arrayref (nthelem 3 rows) "point") 4 "row 3 → pid 4"))))


;; --- Scenario F1: a "wave" output among converged ones suppresses pass-3
(pvtTestRegister "collect/rows-mixed-converge-wave-suppresses-sentinel"
  (lambda ()
    (let ((walk (_pvtCollTestMakeWalk
                  (list
                    (list 1 "TT" "tran" "vout"  "wave")       ; "wave" registers seenKey
                    (list 1 "TT" "tran" "delay" 1.5))         ; numeric writes seenKey
                  nil
                  (list (list 1 nil))
                  (list (list "TT" nil))
                  nil
                  1))
          (result nil) (rows nil))
      (setq result (_pvtCollRowsFromTuples walk))
      (setq rows (cdr result))
      (pvtAssert (length rows) 1 "1 ok row, no no_convergence sentinel")
      (pvtAssert (arrayref (car rows) "status") "ok" "delay row is ok")
      (pvtAssert (arrayref (car rows) "value")  1.5 "delay value"))))


;; --- Bug C (post-fix): pass-2 skips when pass-1 already wrote (cname,pid)
(pvtTestRegister "collect/rows-pass2-skips-when-pass1-wrote"
  (lambda ()
    ;; Bug C fix (Step 4): a 'running test whose tran already produced an
    ;; ok row in pass 1 must NOT emit a __sim_status__ sentinel in pass 2
    ;; for the same (cname,pid). Validator I1 (mutual exclusion: either
    ;; ≥1 ok row OR exactly one sentinel per (cname,pid), never both)
    ;; would otherwise reject the dump. The writtenSet check is the gate.
    (let ((walk (_pvtCollTestMakeWalk
                  (list (list 1 "TT" "tran" "vout" 1.0))     ; pass 1 writes ok
                  (list (list 1 "TT" "tran" 'running))       ; pass 2 should skip
                  (list (list 1 nil))
                  (list (list "TT" nil))
                  nil
                  1))
          (result nil) (rows nil))
      (setq result (_pvtCollRowsFromTuples walk))
      (setq rows (cdr result))
      (pvtAssert (length rows) 1
                 "Bug C fixed: only ok row remains, sentinel suppressed")
      (pvtAssert (arrayref (car rows) "status") "ok" "row 1 is ok")
      (pvtAssert (arrayref (car rows) "output") "vout" "row 1 is the pass-1 vout"))))


;; --- Bug D (post-fix): pass-3 fallback marker is unified "_no_corner_vars"
(pvtTestRegister "collect/rows-pass3-uses-no-corner-vars-marker"
  (lambda ()
    ;; Bug D fix (Step 4): all three passes emit "_no_corner_vars" when
    ;; corner_vars resolution fails. Pre-fix, pass-3 used "_no_cache" and
    ;; pass-1/2 used "_parse_error" / "_no_cache" for the same state; the
    ;; validator W1 generically warns on "_"-prefixed keys, and
    ;; "_no_corner_vars" is the canonical marker named in validate.py.
    (let ((walk (_pvtCollTestMakeWalk
                  (list (list 1 "TT" "tran" "vout" "wave"))    ; pass 3 fires for (TT,1)
                  nil
                  (list (list 1 nil))
                  (list (list "TT" nil))                       ; cornerObj failed
                  nil
                  1))
          (result nil) (rows nil) (cv nil))
      (setq result (_pvtCollRowsFromTuples walk))
      (setq rows (cdr result))
      (pvtAssert (length rows) 1 "1 sentinel row from pass 3")
      (setq cv (arrayref (car rows) "corner_vars"))
      (pvtAssert (and cv (arrayref cv "_no_corner_vars")) "TT"
                 "Bug D fixed: pass-3 emits {_no_corner_vars: <cname>} marker"))))


;; --- test_note flattening: "" → JSON null
(pvtTestRegister "collect/rows-test-note-empty-string-becomes-null"
  (lambda ()
    (let ((walk (_pvtCollTestMakeWalk
                  (list (list 1 "TT" "tran" "vout" 1.0))
                  nil
                  (list (list 1 nil))
                  (list (list "TT" nil))
                  (list (list "tran" ""))                     ; empty-string note
                  1))
          (result nil))
      (setq result (_pvtCollRowsFromTuples walk))
      (pvtAssert (arrayref (car (cdr result)) "test_note") PVT_JSON_NULL
                 "empty-string note → PVT_JSON_NULL"))))


;; --- test_note passthrough: non-empty string preserved
(pvtTestRegister "collect/rows-test-note-non-empty-passes-through"
  (lambda ()
    (let ((walk (_pvtCollTestMakeWalk
                  (list (list 1 "TT" "tran" "vout" 1.0))
                  nil
                  (list (list 1 nil))
                  (list (list "TT" nil))
                  (list (list "tran" "primary transient"))
                  1))
          (result nil))
      (setq result (_pvtCollRowsFromTuples walk))
      (pvtAssert (arrayref (car (cdr result)) "test_note") "primary transient"
                 "non-empty note preserved verbatim"))))


;; --- firstTestName = the FIRST ok-row's test, not the first tuple's
(pvtTestRegister "collect/rows-firstTestName-is-first-pass1-write"
  (lambda ()
    (let ((walk (_pvtCollTestMakeWalk
                  (list
                    (list 1 "TT" "tran" "vout" "wave")        ; not numeric → skipped
                    (list 1 "TT" "dc"   "iout" 1e-3))         ; numeric → writes
                  nil
                  (list (list 1 nil))
                  (list (list "TT" nil))
                  nil
                  1))
          (result nil))
      (setq result (_pvtCollRowsFromTuples walk))
      (pvtAssert (car result) "dc"
                 "firstTestName = first test that emitted an ok row, not 'tran'"))))


;; --------------------------------------------------------------------------
;; Idempotent re-load: load pvtCollect.il twice in a row should not error.
;; --------------------------------------------------------------------------

(pvtTestRegister "collect/reload-is-idempotent"
  (lambda ()
    (let ((path (strcat PVT_SKILL_ROOT "/pvtCollect.il")))
      (let ((rv (errset (load path) nil)))
        (pvtAssert (and rv (listp rv)) t "first reload OK"))
      (let ((rv (errset (load path) nil)))
        (pvtAssert (and rv (listp rv)) t "second reload OK")))))


;; ============================================================================
;; _pvtCollBuildPidListFromTests — Bug B harness (DECISIONS #23 follow-up).
;;
;; The walker's pidList build was refactored out into a pure helper so Tier-1
;; can exercise the gappy/dup/nil cases directly. The helper reads
;; `tst->pointID` via _pvtCollEvalThunk, so any object exposing that slot
;; (defstruct or otherwise) works as a mock test. The live walker is still
;; covered only by Tier-2 byte-identical regression on `simkit_verify` —
;; reason: maeReadResDB is write-protected in classic SKILL (skillbridge
;; `putd` refuses to override it), so a live-side mock-rdb is not feasible.
;; ============================================================================

;; Define the mock-tst struct exactly once (defstruct re-emit warns + may
;; reset slot values; doing it inside each test thunk would warn every run).
(defstruct pvtMockTst pointID)

(procedure (_pvtTestMakeMockTst pid)
  "Construct a mock-tst whose `tst->pointID` slot returns `pid`."
  (let ((m (make_pvtMockTst)))
    (setf m->pointID pid)
    m))

(pvtTestRegister "collect/buildPidList-empty-input"
  (lambda ()
    (pvtAssert (_pvtCollBuildPidListFromTests nil) nil
               "empty input → nil pidList")))

(pvtTestRegister "collect/buildPidList-single-pid"
  (lambda ()
    (pvtAssert
      (_pvtCollBuildPidListFromTests (list (_pvtTestMakeMockTst 7)))
      '(7)
      "single mock-tst → singleton pidList")))

(pvtTestRegister "collect/buildPidList-contiguous-pids-sorted"
  (lambda ()
    (pvtAssert
      (_pvtCollBuildPidListFromTests
        (list (_pvtTestMakeMockTst 1)
              (_pvtTestMakeMockTst 2)
              (_pvtTestMakeMockTst 3)))
      '(1 2 3)
      "contiguous 1..3 → [1,2,3]")))

(pvtTestRegister "collect/buildPidList-gappy-pids-preserved"
  (lambda ()
    ;; THIS IS THE BUG B WITNESS. The pre-fix walker would stop at the
    ;; first nil from `(rdb->point pid)` and never visit pid=4. The new
    ;; helper iterates `tst->pointID` across the tst list, so pid=4 is
    ;; included whether or not pid=3 ever existed in the rdb.
    (pvtAssert
      (_pvtCollBuildPidListFromTests
        (list (_pvtTestMakeMockTst 1)
              (_pvtTestMakeMockTst 2)
              (_pvtTestMakeMockTst 4)
              (_pvtTestMakeMockTst 5)))
      '(1 2 4 5)
      "gap at pid=3 → [1,2,4,5] (Bug B fix)")))

(pvtTestRegister "collect/buildPidList-unsorted-input-emits-sorted"
  (lambda ()
    (pvtAssert
      (_pvtCollBuildPidListFromTests
        (list (_pvtTestMakeMockTst 5)
              (_pvtTestMakeMockTst 1)
              (_pvtTestMakeMockTst 3)
              (_pvtTestMakeMockTst 2)))
      '(1 2 3 5)
      "input order doesn't matter — output ascending")))

(pvtTestRegister "collect/buildPidList-duplicates-deduped"
  (lambda ()
    ;; Multiple tests share a pid (one pid per corner is the usual shape
    ;; — same point swept across N corners means N tst rows with the same
    ;; pointID).
    (pvtAssert
      (_pvtCollBuildPidListFromTests
        (list (_pvtTestMakeMockTst 1)
              (_pvtTestMakeMockTst 2)
              (_pvtTestMakeMockTst 1)
              (_pvtTestMakeMockTst 2)
              (_pvtTestMakeMockTst 3)))
      '(1 2 3)
      "duplicate pids deduped")))

(pvtTestRegister "collect/buildPidList-nil-pointID-skipped"
  (lambda ()
    ;; A tst with nil pointID (slot unset, or live SKILL returned nil) must
    ;; not pollute pidList — and must not poison sort/length.
    (let ((bad (make_pvtMockTst)))
      ;; bad->pointID stays unset / nil
      (pvtAssert
        (_pvtCollBuildPidListFromTests
          (list (_pvtTestMakeMockTst 1)
                bad
                (_pvtTestMakeMockTst 2)))
        '(1 2)
        "tst with nil pointID skipped"))))

(pvtTestRegister "collect/buildPidList-all-nil-pointIDs"
  (lambda ()
    (let ((b1 (make_pvtMockTst)) (b2 (make_pvtMockTst)))
      (pvtAssert
        (_pvtCollBuildPidListFromTests (list b1 b2))
        nil
        "all-nil-pointID list → nil pidList"))))

(pvtTestRegister "collect/buildPidList-large-gappy-set"
  (lambda ()
    ;; 10 pids with two gaps, randomly ordered. Covers sort+dedup+nil-skip
    ;; in one shot.
    (let ((bad (make_pvtMockTst)))
      (pvtAssert
        (_pvtCollBuildPidListFromTests
          (list (_pvtTestMakeMockTst 11)
                (_pvtTestMakeMockTst 3)
                (_pvtTestMakeMockTst 7)
                bad
                (_pvtTestMakeMockTst 11)  ; duplicate
                (_pvtTestMakeMockTst 1)
                (_pvtTestMakeMockTst 9)
                (_pvtTestMakeMockTst 3))) ; duplicate
        '(1 3 7 9 11)
        "11-pid scrambled+gappy+dup+nil → [1,3,7,9,11]"))))


t  ; load-time return
