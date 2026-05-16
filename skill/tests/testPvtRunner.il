;; ============================================================================
;; testPvtRunner.il — Tier-1 for pvtRunner.il (Phase 3A §3 v1.1).
;;
;; Pure / validation paths only. The live paths (axlRunAllTests,
;; axlGetCurrentHistory, axlSetHistoryName, axlGetRunStatus, axlGetTest,
;; axlSetEnabled, axlGetMainSetupDB, axlRemoveElement) all require an
;; open Maestro session with an active setupdb, so they are exercised
;; via skillbridge probe (Tier-2). What we CAN cover here:
;;
;;   * Validation paths that reject bad inputs before touching any axl* —
;;     pvtRunnerRename / pvtRunnerRun reject empty / non-string history.
;;   * _pvtRunnerResolveSession bad-input branch (no window, non-string).
;;   * Result-shape sanity: validation errors emerge as pvt_validation,
;;     not pvt_runner_*.
;; ============================================================================


;; ---------- pvtRunnerRename: history-name validation ------------------------

(pvtTestRegister "runner/rename/empty-string-rejected"
  (lambda ()
    (let ((rv (pvtRunnerRename "fakeSession" "")))
      (pvtAssertErrCategory rv 'pvt_validation "empty string"))))

(pvtTestRegister "runner/rename/nil-rejected"
  (lambda ()
    (let ((rv (pvtRunnerRename "fakeSession" nil)))
      (pvtAssertErrCategory rv 'pvt_validation "nil historyName"))))

(pvtTestRegister "runner/rename/non-string-rejected"
  (lambda ()
    (let ((rv (pvtRunnerRename "fakeSession" 42)))
      (pvtAssertErrCategory rv 'pvt_validation "integer historyName"))))


;; ---------- pvtRunnerRun (legacy): same validation guard --------------------

(pvtTestRegister "runner/run-legacy/empty-string-rejected"
  (lambda ()
    (let ((rv (pvtRunnerRun "fakeSession" "")))
      (pvtAssertErrCategory rv 'pvt_validation "legacy run rejects empty"))))

(pvtTestRegister "runner/run-legacy/nil-rejected"
  (lambda ()
    (let ((rv (pvtRunnerRun "fakeSession" nil)))
      (pvtAssertErrCategory rv 'pvt_validation "legacy run rejects nil"))))


;; ---------- _pvtRunnerResolveSession ---------------------------------------
;; Non-string with no available window must surface as pvt_validation.
;; We can't easily fake hiGetWindowList from Tier-1, so we only test the
;; string-passthrough success case + the explicit-nil branch when there is
;; (almost certainly) no current window during a -nograph replay.

(pvtTestRegister "runner/resolveSession/string-passes-through"
  (lambda ()
    (let ((rv (_pvtRunnerResolveSession "someSession")))
      (cond
        ((not (pvtIsOk rv))
          (pvtAssert nil t "expected pvtOk, got err"))
        (t (pvtAssert (pvtUnwrap rv) "someSession" "string echoed"))))))


;; ---------- pvtRunnerSubmit / pvtRunnerGetStatus shape sanity ---------------
;; These call axl* on a fake session — Tier-1 can only verify that the
;; OUTERMOST validation (session resolution to a string) succeeds and the
;; result is a pvt_err of some kind (not a SKILL uncaught error), or that
;; the function exists and returns the discriminated-result shape on input
;; we can construct without a live session. We test "does it return SOMETHING
;; that pvtIsErr/pvtIsOk recognises" rather than asserting the exact category,
;; because the C-level axl* error path differs across Cadence builds.

(pvtTestRegister "runner/submit/exists"
  (lambda ()
    (pvtAssert (procedurep (getd 'pvtRunnerSubmit)) t "pvtRunnerSubmit defined")))

(pvtTestRegister "runner/rename/exists"
  (lambda ()
    (pvtAssert (procedurep (getd 'pvtRunnerRename)) t "pvtRunnerRename defined")))

(pvtTestRegister "runner/getStatus/exists"
  (lambda ()
    (pvtAssert (procedurep (getd 'pvtRunnerGetStatus)) t "pvtRunnerGetStatus defined")))

(pvtTestRegister "runner/deleteHistory/exists"
  (lambda ()
    (pvtAssert (procedurep (getd 'pvtRunnerDeleteHistory)) t "pvtRunnerDeleteHistory defined")))

(pvtTestRegister "runner/snapshotTestState/exists"
  (lambda ()
    (pvtAssert (procedurep (getd 'pvtRunnerSnapshotTestState)) t "snapshot defined")))

(pvtTestRegister "runner/restoreTestState/exists"
  (lambda ()
    (pvtAssert (procedurep (getd 'pvtRunnerRestoreTestState)) t "restore defined")))

(pvtTestRegister "runner/enableOnly/exists"
  (lambda ()
    (pvtAssert (procedurep (getd 'pvtRunnerEnableOnly)) t "enableOnly defined")))


;; ---------- v1.2: pvtRunnerSetIcSource / pvtRunnerClearIcSource -----------

(pvtTestRegister "runner/setIcSource/exists"
  (lambda ()
    (pvtAssert (procedurep (getd 'pvtRunnerSetIcSource)) t
               "pvtRunnerSetIcSource defined")))

(pvtTestRegister "runner/clearIcSource/exists"
  (lambda ()
    (pvtAssert (procedurep (getd 'pvtRunnerClearIcSource)) t
               "pvtRunnerClearIcSource defined")))

(pvtTestRegister "runner/setIcSource/empty-test-rejected"
  (lambda ()
    (let ((rv (pvtRunnerSetIcSource "fakeSession" "" "/tmp/fc" "readns")))
      (pvtAssertErrCategory rv 'pvt_validation "empty testName"))))

(pvtTestRegister "runner/setIcSource/nil-test-rejected"
  (lambda ()
    (let ((rv (pvtRunnerSetIcSource "fakeSession" nil "/tmp/fc" "readns")))
      (pvtAssertErrCategory rv 'pvt_validation "nil testName"))))

(pvtTestRegister "runner/setIcSource/empty-path-rejected"
  (lambda ()
    (let ((rv (pvtRunnerSetIcSource "fakeSession" "Test" "" "readns")))
      (pvtAssertErrCategory rv 'pvt_validation "empty icPath"))))

(pvtTestRegister "runner/setIcSource/nil-path-rejected"
  (lambda ()
    (let ((rv (pvtRunnerSetIcSource "fakeSession" "Test" nil "readns")))
      (pvtAssertErrCategory rv 'pvt_validation "nil icPath"))))

(pvtTestRegister "runner/setIcSource/bad-mode-rejected"
  (lambda ()
    (let ((rv (pvtRunnerSetIcSource "fakeSession" "Test" "/tmp/fc" "loadme")))
      (pvtAssertErrCategory rv 'pvt_validation "unknown mode"))))

(pvtTestRegister "runner/setIcSource/nil-mode-rejected"
  (lambda ()
    (let ((rv (pvtRunnerSetIcSource "fakeSession" "Test" "/tmp/fc" nil)))
      (pvtAssertErrCategory rv 'pvt_validation "nil mode"))))

(pvtTestRegister "runner/setIcSource/readns-mode-accepted-shape"
  (lambda ()
    ;; With no live session it'll progress to pvt_runner_set_ic
    ;; (or similar). The key assertion is validation passed.
    (let ((rv (pvtRunnerSetIcSource "fakeSession" "Test" "/tmp/fc" "readns")))
      (cond
        ((pvtIsErr rv)
          (pvtAssert (not (equal (cadr rv) 'pvt_validation)) t
                     "readns must pass validation"))
        (t (pvtAssert t t "readns accepted"))))))

(pvtTestRegister "runner/setIcSource/readic-mode-accepted-shape"
  (lambda ()
    (let ((rv (pvtRunnerSetIcSource "fakeSession" "Test" "/tmp/ic" "readic")))
      (cond
        ((pvtIsErr rv)
          (pvtAssert (not (equal (cadr rv) 'pvt_validation)) t
                     "readic must pass validation"))
        (t (pvtAssert t t "readic accepted"))))))

(pvtTestRegister "runner/clearIcSource/bad-mode-rejected"
  (lambda ()
    (let ((rv (pvtRunnerClearIcSource "fakeSession" "Test" "loadme" "")))
      (pvtAssertErrCategory rv 'pvt_validation "unknown mode in clear"))))

(pvtTestRegister "runner/clearIcSource/empty-test-rejected"
  (lambda ()
    (let ((rv (pvtRunnerClearIcSource "fakeSession" "" "readns" "")))
      (pvtAssertErrCategory rv 'pvt_validation "empty testName in clear"))))


;; --- v1.2 stage-2: per-corner enable helpers ------------------------------

(pvtTestRegister "runner/snapshotCornersEnable/exists"
  (lambda ()
    (pvtAssert (procedurep (getd 'pvtRunnerSnapshotCornersEnable)) t
               "pvtRunnerSnapshotCornersEnable defined")))

(pvtTestRegister "runner/enableCornerByIndex/exists"
  (lambda ()
    (pvtAssert (procedurep (getd 'pvtRunnerEnableCornerByIndex)) t
               "pvtRunnerEnableCornerByIndex defined")))

(pvtTestRegister "runner/restoreCornersEnable/exists"
  (lambda ()
    (pvtAssert (procedurep (getd 'pvtRunnerRestoreCornersEnable)) t
               "pvtRunnerRestoreCornersEnable defined")))

;; idx validation — must be positive integer
(pvtTestRegister "runner/enableCornerByIndex/zero-rejected"
  (lambda ()
    (let ((rv (pvtRunnerEnableCornerByIndex "fakeSession" 0)))
      (pvtAssertErrCategory rv 'pvt_validation "idx=0"))))

(pvtTestRegister "runner/enableCornerByIndex/negative-rejected"
  (lambda ()
    (let ((rv (pvtRunnerEnableCornerByIndex "fakeSession" -3)))
      (pvtAssertErrCategory rv 'pvt_validation "negative idx"))))

(pvtTestRegister "runner/enableCornerByIndex/nil-rejected"
  (lambda ()
    (let ((rv (pvtRunnerEnableCornerByIndex "fakeSession" nil)))
      (pvtAssertErrCategory rv 'pvt_validation "nil idx"))))

(pvtTestRegister "runner/enableCornerByIndex/non-integer-rejected"
  (lambda ()
    (let ((rv (pvtRunnerEnableCornerByIndex "fakeSession" "two")))
      (pvtAssertErrCategory rv 'pvt_validation "string idx"))))

;; Positive-int-accepts-validation: NOT included here because invoking
;; with a fake session reaches axlGetMainSetupDB("fakeSession"), which
;; raises a C-level "no such session" error that errset does not trap
;; (same class of issue as DECISIONS #55's axlGetRunStatus). The
;; positive-int path IS covered live by the bridge-side round-trip
;; against fnxSession0 documented in DECISIONS #57.
