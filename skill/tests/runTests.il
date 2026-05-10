;; ============================================================================
;; runTests.il  --  mini xUnit-style harness for the .pvtproject SKILL loader.
;;
;; Run from the repo root:
;;   cd <simkit>
;;   virtuoso -nograph -replay skill/tests/runTests.il
;;
;; Or, if -replay is unavailable on the target Cadence version, evaluate
;;   load("skill/tests/runTests.il")
;; from a Virtuoso CIW.
;;
;; The harness loads the production sources from sibling skill/, then loads
;; each test file, then runs every zero-arg procedure pushed onto
;; `_pvtTestRegistry` by the test files.
;; ============================================================================

;; --------------------------------------------------------------------------
;; Resolve the on-disk location of THIS script so we can find sibling files
;; regardless of cwd. simplifyFilename(currentFilename()) is unavailable; we
;; rely on the SKILL global `currentFilename`-style hint indirectly by using
;; `which` — but the safest portable form is to require the user to invoke
;; us with the cwd at the test directory or to supply PVT_TEST_ROOT.
;; --------------------------------------------------------------------------

(defvar PVT_TEST_ROOT
  (or (getShellEnvVar "PVT_TEST_ROOT")
      (strcat (simplifyFilename (getWorkingDir)) "/skill/tests")))

(defvar PVT_SKILL_ROOT
  (or (getShellEnvVar "PVT_SKILL_ROOT")
      (strcat (simplifyFilename (getWorkingDir)) "/skill")))


;; --------------------------------------------------------------------------
;; Test-state globals
;; --------------------------------------------------------------------------

(defvar _pvtTestPass     0)
(defvar _pvtTestFail     0)
(defvar _pvtTestSkip     0)
(defvar _pvtTestLog      nil)        ; reverse-list of "[PASS|FAIL|SKIP] label"
(defvar _pvtTestRegistry nil)        ; reverse-list of (label . zero-arg-proc)
(defvar _pvtTestCurrent  nil)        ; label of test currently running


;; --------------------------------------------------------------------------
;; Registration API used by individual test files
;; --------------------------------------------------------------------------

(procedure (pvtTestRegister label thunk)
  "Append a zero-arg test procedure under a human-readable label.
   Stored as (list label thunk) — classic SKILL `cons` rejects a non-list
   second arg, so dotted pairs are not used."
  (setq _pvtTestRegistry (cons (list label thunk) _pvtTestRegistry))
  t
)


;; --------------------------------------------------------------------------
;; Assertion helpers used inside test bodies
;; --------------------------------------------------------------------------

(procedure (pvtAssert actual expected label)
  "Compare `actual` to `expected` via SKILL `equal`. Logs PASS/FAIL."
  (cond
    ((equal actual expected)
      (setq _pvtTestPass (plus _pvtTestPass 1))
      (setq _pvtTestLog
            (cons (sprintf nil "  PASS  %s" label) _pvtTestLog))
      t)
    (t
      (setq _pvtTestFail (plus _pvtTestFail 1))
      (setq _pvtTestLog
            (cons (sprintf nil "  FAIL  %s : expected %L, got %L"
                           label expected actual)
                  _pvtTestLog))
      nil))
)

(procedure (pvtAssertOk result label)
  "Assert `result` is a pvtError :ok value. Logs the wrapped err on FAIL."
  (cond
    ((pvtIsOk result)
      (setq _pvtTestPass (plus _pvtTestPass 1))
      (setq _pvtTestLog (cons (sprintf nil "  PASS  %s" label) _pvtTestLog))
      t)
    (t
      (setq _pvtTestFail (plus _pvtTestFail 1))
      (setq _pvtTestLog
            (cons (sprintf nil "  FAIL  %s : expected :ok, got %L"
                           label result)
                  _pvtTestLog))
      nil))
)

(procedure (pvtAssertErrCategory result expectedCat label)
  "Assert `result` is a pvtError :err with category symbol `expectedCat`."
  (cond
    ((not (pvtIsErr result))
      (setq _pvtTestFail (plus _pvtTestFail 1))
      (setq _pvtTestLog
            (cons (sprintf nil "  FAIL  %s : expected :err %L, got :ok"
                           label expectedCat)
                  _pvtTestLog))
      nil)
    ((not (equal (pvtErrCategory result) expectedCat))
      (setq _pvtTestFail (plus _pvtTestFail 1))
      (setq _pvtTestLog
            (cons (sprintf nil "  FAIL  %s : expected category %L, got %L (msg=%L)"
                           label expectedCat
                           (pvtErrCategory result)
                           (pvtErrMessage result))
                  _pvtTestLog))
      nil)
    (t
      (setq _pvtTestPass (plus _pvtTestPass 1))
      (setq _pvtTestLog (cons (sprintf nil "  PASS  %s" label) _pvtTestLog))
      t))
)


;; --------------------------------------------------------------------------
;; Helper: read a fixture file path into the form we hand to the loader.
;; Fixtures live at <PVT_TEST_ROOT>/fixtures/<name>.
;; --------------------------------------------------------------------------

(procedure (pvtTestFixture name)
  (strcat PVT_TEST_ROOT "/fixtures/" name)
)


;; --------------------------------------------------------------------------
;; Driver
;; --------------------------------------------------------------------------

(procedure (pvtTestRun)
  "Run all registered tests, print summary, return t iff zero failures."
  (let ((entries (reverse _pvtTestRegistry)))
    (foreach entry entries
      (let ((label (car entry)) (thunk (nthelem 2 entry)))
        (setq _pvtTestCurrent label)
        ;; errset (sklangref p.555) catches any SKILL error so a bad test
        ;; doesn't abort the whole run. The list-vs-nil return is sufficient
        ;; because we already record per-assertion outcomes via assert helpers.
        (let ((rv (errset (funcall thunk) t)))
          (cond
            ((null rv)
              (setq _pvtTestFail (plus _pvtTestFail 1))
              (setq _pvtTestLog
                    (cons (sprintf nil "  FAIL  %s : SKILL error during execution"
                                   label)
                          _pvtTestLog)))
            (t nil))))))
  ;; Print the log in original order.
  (foreach line (reverse _pvtTestLog) (println line))
  (printf "\n=== Summary: %d pass, %d fail, %d skip ===\n"
          _pvtTestPass _pvtTestFail _pvtTestSkip)
  (zerop _pvtTestFail)
)


;; --------------------------------------------------------------------------
;; Bring in production sources, then test files, then run.
;; load() returns t on success; we ignore the value but rely on the file
;; existing.  This script does not catch load errors — fail loud.
;; --------------------------------------------------------------------------

(load (strcat PVT_SKILL_ROOT "/pvtError.il"))
(load (strcat PVT_SKILL_ROOT "/pvtJson.il"))
(load (strcat PVT_SKILL_ROOT "/pvtProject.il"))
(load (strcat PVT_SKILL_ROOT "/pvtCollect.il"))

(load (strcat PVT_TEST_ROOT "/testPvtJson.il"))
(load (strcat PVT_TEST_ROOT "/testPvtProject.il"))
(load (strcat PVT_TEST_ROOT "/testPvtCollect.il"))

(pvtTestRun)
