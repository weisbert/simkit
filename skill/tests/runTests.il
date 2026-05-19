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
;; regardless of cwd. Classic SKILL exposes no in-flight "current loading
;; file" primitive (getAllLoadedFiles only records the file after the load
;; completes, so it cannot help during the in-flight load that needs the
;; path NOW), so we layer four strategies, in priority order:
;;   1. SKILL global `_PVT_FORCE_ROOT` set by the caller before `(load ...)`.
;;      Useful for skillbridge / Python launchers that compute the path
;;      from the script name they're about to load.
;;   2. Env vars PVT_TEST_ROOT / PVT_SKILL_ROOT. Empty-string env values
;;      are treated as unset because SKILL's (or "" X) returns "" — a
;;      real-world stumble when callers export blank values "to clear them".
;;   3. Upward walk from cwd looking for a marker `skill/tests/runTests.il`.
;;      Handles cwd anywhere inside the simkit checkout, including the
;;      `cd <simkit> && virtuoso -replay skill/tests/runTests.il` path.
;;      Also scans `getSkillPath()` entries.
;;   4. Last-resort: cwd + "/skill[/tests]" (preserves the original
;;      pre-fix behavior). If layer 4 trips, the subsequent (load ...)
;;      calls will fail loudly at line 168 with a clear "can't access
;;      file" error pointing at the bad path printed below.
;; --------------------------------------------------------------------------

(procedure (_pvtEnvOrNil name)
  "(getShellEnvVar name) returns \"\" both when the var is unset AND when
   it is exported as the empty string. SKILL's `or` treats \"\" as truthy,
   so we coerce empty values to nil here."
  (let ((v (getShellEnvVar name)))
    (cond
      ((null v) nil)
      ((zerop (strlen v)) nil)
      (t v))))

(procedure (_pvtFindRepoRoot startDir)
  "Walk up from startDir looking for the marker `skill/tests/runTests.il`.
   Returns the directory containing `skill/...` or nil if nothing found
   within 12 parent levels. simplifyFilename collapses `..` segments so
   the loop terminates at filesystem root."
  (let ((dir (simplifyFilename startDir)) (hit nil) (i 0))
    (while (and (not hit) (lessp i 12) dir (nequal dir ""))
      (when (isFile (strcat dir "/skill/tests/runTests.il"))
        (setq hit dir))
      (let ((parent (simplifyFilename (strcat dir "/.."))))
        (cond
          ((equal parent dir) (setq dir nil))   ;; hit filesystem root
          (t (setq dir parent))))
      (setq i (plus i 1)))
    hit))

(defvar _PVT_REPO_ROOT
  (or
    ;; (1) caller-set SKILL global override
    (and (boundp '_PVT_FORCE_ROOT)
         _PVT_FORCE_ROOT
         (stringp _PVT_FORCE_ROOT)
         (not (zerop (strlen _PVT_FORCE_ROOT)))
         (simplifyFilename _PVT_FORCE_ROOT))
    ;; (3a) upward walk from cwd
    (_pvtFindRepoRoot (getWorkingDir))
    ;; (3b) scan SKILL search path entries
    (let ((found nil))
      (foreach p (or (getSkillPath) (list "."))
        (unless found
          (let ((cand (_pvtFindRepoRoot p)))
            (when cand (setq found cand)))))
      found)))

(defvar PVT_TEST_ROOT
  (or (_pvtEnvOrNil "PVT_TEST_ROOT")
      (and _PVT_REPO_ROOT (strcat _PVT_REPO_ROOT "/skill/tests"))
      (strcat (simplifyFilename (getWorkingDir)) "/skill/tests")))

(defvar PVT_SKILL_ROOT
  (or (_pvtEnvOrNil "PVT_SKILL_ROOT")
      (and _PVT_REPO_ROOT (strcat _PVT_REPO_ROOT "/skill"))
      (strcat (simplifyFilename (getWorkingDir)) "/skill")))

(printf "[runTests] PVT_SKILL_ROOT=%s\n" PVT_SKILL_ROOT)
(printf "[runTests] PVT_TEST_ROOT=%s\n"  PVT_TEST_ROOT)
(unless (isFile (strcat PVT_SKILL_ROOT "/pvtError.il"))
  (printf "[runTests] WARNING: %s/pvtError.il not found.\n" PVT_SKILL_ROOT)
  (printf "[runTests]   To fix: (a) cd into the simkit repo root, OR\n")
  (printf "[runTests]           (b) (setq _PVT_FORCE_ROOT \"/abs/path/to/simkit\") before (load ...), OR\n")
  (printf "[runTests]           (c) export PVT_SKILL_ROOT / PVT_TEST_ROOT env vars.\n"))


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
(load (strcat PVT_SKILL_ROOT "/pvtProjectDialog.il"))
(load (strcat PVT_SKILL_ROOT "/pvtCollect.il"))
(load (strcat PVT_SKILL_ROOT "/pvtCorners.il"))
(load (strcat PVT_SKILL_ROOT "/pvtMeasure.il"))
(load (strcat PVT_SKILL_ROOT "/pvtRunner.il"))

(load (strcat PVT_TEST_ROOT "/testPvtJson.il"))
(load (strcat PVT_TEST_ROOT "/testPvtProject.il"))
(load (strcat PVT_TEST_ROOT "/testPvtProjectDialog.il"))
(load (strcat PVT_TEST_ROOT "/testPvtCollect.il"))
(load (strcat PVT_TEST_ROOT "/testPvtCorners.il"))
(load (strcat PVT_TEST_ROOT "/testPvtMeasure.il"))
(load (strcat PVT_TEST_ROOT "/testPvtRunner.il"))

(pvtTestRun)
