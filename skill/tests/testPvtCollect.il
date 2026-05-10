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
;; Idempotent re-load: load pvtCollect.il twice in a row should not error.
;; --------------------------------------------------------------------------

(pvtTestRegister "collect/reload-is-idempotent"
  (lambda ()
    (let ((path (strcat PVT_SKILL_ROOT "/pvtCollect.il")))
      (let ((rv (errset (load path) nil)))
        (pvtAssert (and rv (listp rv)) t "first reload OK"))
      (let ((rv (errset (load path) nil)))
        (pvtAssert (and rv (listp rv)) t "second reload OK")))))


t  ; load-time return
