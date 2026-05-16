;; ============================================================================
;; pvtRunner.il
;;
;; Phase 3A §3 SKILL bridge for the simulation orchestrator. Drives Maestro
;; through one suite item: snapshot test enable state, enable only the named
;; tests, run synchronously via axlRunAllTests, restore on exit. Reads
;; per-corner status via the existing Phase 1 PvtSave path.
;;
;; Public API (all return discriminated-result wrappers per pvtError.il):
;;
;;   pvtRunnerSnapshotTestState(sess)
;;     -> (pvtOk list_of_pairs)         e.g. (("Test" t) ("OtherTest" nil))
;;
;;   pvtRunnerRestoreTestState(sess, snap)
;;     -> (pvtOk snap)                  (snap echoed for confirmation)
;;
;;   pvtRunnerEnableOnly(sess, testNamesList)
;;     -> (pvtOk diff_list)             e.g. (("Test" t->t) ("OtherTest" t->nil))
;;        (where each entry says before -> after; same value = no change)
;;
;;   pvtRunnerRun(sess, historyName)
;;     -> (pvtOk [status-code sub-code]) on completion
;;     -> (pvtErr 'pvt_runner_run "<msg>")   on submit error (errset trapped)
;;
;;     This is the SYNCHRONOUS path — wraps axlRunAllTests, which blocks
;;     until the suite finishes. v1.1 may add an async with-callback variant.
;;
;;   pvtRunnerGetStatus(sess)
;;     -> (pvtOk [code sub])            wraps axlGetRunStatus(sess)
;;
;; Reuse:
;;   - pvtError.il:  pvtOk / pvtErr / pvtIsErr / pvtUnwrap
;;   - pvtCollect.il (caller-side): PvtSave still does dump after a run
;;
;; SKILL note (recap from pvtCorners.il): every procedure that uses (return ...)
;; for early exit MUST wrap its body in (prog () ...), because classic SKILL
;; `return` only escapes the enclosing prog form. We follow that convention.
;; ============================================================================


;; --- helpers ---------------------------------------------------------------

(procedure (_pvtRunnerResolveSession sess)
  ;; Same convention as Phase 2/3B helpers. nil sess means try the current
  ;; window — but skillbridge usually runs in CIW context where axlGetWindowSession
  ;; returns nil; in practice callers pass the session name explicitly.
  (prog (resolved win)
    (cond
      ((and sess (stringp sess))
        (return (pvtOk sess))))
    (setq win (errset (car (hiGetWindowList)) nil))
    (cond ((not win)
            (return (pvtErr 'pvt_validation
                            "no current Cadence window — pass an explicit session"))))
    (setq resolved (errset (axlGetWindowSession (car win)) nil))
    (cond
      ((null resolved)
        (return (pvtErr 'pvt_validation
                        "axlGetWindowSession returned nil — pass an explicit session")))
      (t (return (pvtOk (car resolved)))))))


(procedure (_pvtRunnerGetSdb sess)
  ;; Returns (pvtOk sdb_integer) or pvtErr if session is not active.
  (prog (r)
    (setq r (errset (axlGetMainSetupDB sess) nil))
    (cond
      ((or (null r) (null (car r)))
        (return (pvtErr 'pvt_validation
                        (lsprintf "axlGetMainSetupDB failed for session %L" sess))))
      (t (return (pvtOk (car r)))))))


(procedure (_pvtRunnerTestNames sdb)
  ;; Read the test-name list from axlGetTests(sdb). axlGetTests returns
  ;; (containerHandle list_of_names). Returns the bare list of name strings.
  (prog (raw)
    (setq raw (axlGetTests sdb))
    (cond
      ((and (listp raw) (geqp (length raw) 2))
        (return (cadr raw)))
      (t (return nil)))))


(procedure (_pvtRunnerEnabledMap sdb)
  ;; Returns a list of (name . enabled-bool) pairs for every test in sdb.
  (prog (names out h en)
    (setq names (_pvtRunnerTestNames sdb))
    (setq out nil)
    (foreach name names
      (setq h (errset (axlGetTest sdb name) nil))
      (cond
        ((and h (car h))
          (setq en (errset (axlGetEnabled (car h)) nil))
          (setq out (cons (list name
                                (if (and en (car en)) t nil))
                          out)))))
    (return (reverse out))))


;; --- snapshot + restore -----------------------------------------------------

(procedure (pvtRunnerSnapshotTestState sess)
  (prog (sessR sdbR sdb)
    (setq sessR (_pvtRunnerResolveSession sess))
    (cond ((pvtIsErr sessR) (return sessR)))
    (setq sdbR (_pvtRunnerGetSdb (pvtUnwrap sessR)))
    (cond ((pvtIsErr sdbR) (return sdbR)))
    (setq sdb (pvtUnwrap sdbR))
    (return (pvtOk (_pvtRunnerEnabledMap sdb)))))


(procedure (pvtRunnerRestoreTestState sess snap)
  ;; snap is a list of (name enabled-bool) pairs.
  (prog (sessR sdbR sdb h pair name want)
    (setq sessR (_pvtRunnerResolveSession sess))
    (cond ((pvtIsErr sessR) (return sessR)))
    (setq sdbR (_pvtRunnerGetSdb (pvtUnwrap sessR)))
    (cond ((pvtIsErr sdbR) (return sdbR)))
    (setq sdb (pvtUnwrap sdbR))
    (foreach pair snap
      (setq name (car pair))
      (setq want (cadr pair))
      (setq h (errset (axlGetTest sdb name) nil))
      (cond
        ((and h (car h))
          (axlSetEnabled (car h) want))))
    (return (pvtOk snap))))


;; --- enable only the named tests --------------------------------------------

(procedure (pvtRunnerEnableOnly sess testNames)
  ;; testNames is a list of strings. Every test in sdb is enabled iff its
  ;; name is in testNames; all others are disabled. Returns a diff list of
  ;; (name before after) triples.
  (prog (sessR sdbR sdb names diff h before want)
    (setq sessR (_pvtRunnerResolveSession sess))
    (cond ((pvtIsErr sessR) (return sessR)))
    (setq sdbR (_pvtRunnerGetSdb (pvtUnwrap sessR)))
    (cond ((pvtIsErr sdbR) (return sdbR)))
    (setq sdb (pvtUnwrap sdbR))

    (setq names (_pvtRunnerTestNames sdb))
    (setq diff nil)
    (foreach n names
      (setq h (errset (axlGetTest sdb n) nil))
      (cond
        ((and h (car h))
          (setq before (if (and (errset (axlGetEnabled (car h)) nil)
                                (car (errset (axlGetEnabled (car h)) nil)))
                           t nil))
          (setq want (if (member n testNames) t nil))
          (axlSetEnabled (car h) want)
          (setq diff (cons (list n before want) diff)))))

    ;; Validate that every requested name was actually present.
    (foreach req testNames
      (cond
        ((not (member req names))
          (return
            (pvtErr 'pvt_runner_unknown_test
                    (lsprintf "test %L not in session (available: %L)"
                              req names))))))
    (return (pvtOk (reverse diff)))))


;; --- run + status -----------------------------------------------------------

(procedure (pvtRunnerRun sess historyName)
  ;; Synchronous run: blocks until axlRunAllTests returns. historyName is
  ;; the NAME WE WANT the new history entry to have; we rename after
  ;; because axlRunAllTests always auto-names to "Interactive.NN" (probe
  ;; finding 2026-05-16).
  ;;
  ;; Probe findings (2026-05-16):
  ;;   * axlRunAllTests takes (session-string, X-string), NOT (sdb, name)
  ;;     — type template "tt" confirmed (open spec decision 10.2).
  ;;   * The 2nd arg is NOT honoured as a history name. Maestro always
  ;;     names the new history "Interactive.<N+1>". We pass empty string
  ;;     pending further investigation of what the field actually drives.
  ;;   * axlSetHistoryName(handle, name) renames after the fact.
  ;;
  ;; Returns (pvtOk [statusCode subCode actualHistoryName]).
  (prog (sessR sessName rc statusR newHist hname renameR)
    (cond
      ((or (null historyName) (not (stringp historyName)) (equal historyName ""))
        (return (pvtErr 'pvt_validation
                        "historyName must be a non-empty string"))))
    (setq sessR (_pvtRunnerResolveSession sess))
    (cond ((pvtIsErr sessR) (return sessR)))
    (setq sessName (pvtUnwrap sessR))

    (setq rc (errset (axlRunAllTests sessName "") nil))
    (cond
      ((null rc)
        (return (pvtErr 'pvt_runner_run
                        (lsprintf "axlRunAllTests failed (session %L)"
                                  sessName)))))

    ;; Grab the auto-created history handle + rename it to what the caller asked.
    (setq newHist (errset (axlGetCurrentHistory sessName) nil))
    (setq newHist (cond ((listp newHist) (car newHist)) (t nil)))
    (cond
      ((null newHist)
        (return (pvtErr 'pvt_runner_run
                        "axlGetCurrentHistory returned nil after run"))))
    (setq renameR (errset (axlSetHistoryName newHist historyName) nil))
    ;; Re-read the actual name post-rename (in case Maestro mangled it).
    (setq hname (errset (axlGetHistoryName newHist) nil))
    (setq hname (cond ((listp hname) (car hname)) (t historyName)))

    (setq statusR (errset (axlGetRunStatus sessName) nil))
    (cond
      ((and statusR (car statusR))
        (return (pvtOk (append (car statusR) (list hname)))))
      (t (return (pvtOk (list 0 0 hname)))))))


(procedure (pvtRunnerDeleteHistory sess historyName)
  ;; Helper for cleanup: remove a named history from the session. Returns
  ;; (pvtOk t) if deleted, (pvtErr ...) if not found / not deletable.
  ;; Uses axlSetHistoryName + ... wait, no — Maestro doesn't expose a
  ;; direct delete. Instead use axlSetHistoryLock(h, nil) + the GUI's
  ;; equivalent (axlRemoveElement on the history handle).
  (prog (sessR sessName sdb h r)
    (setq sessR (_pvtRunnerResolveSession sess))
    (cond ((pvtIsErr sessR) (return sessR)))
    (setq sessName (pvtUnwrap sessR))
    (setq sdb (errset (axlGetMainSetupDB sessName) nil))
    (setq sdb (cond ((listp sdb) (car sdb)) (t nil)))
    (cond ((null sdb)
            (return (pvtErr 'pvt_validation
                            "axlGetMainSetupDB failed"))))
    (setq h (errset (axlGetHistoryEntry sdb historyName) nil))
    (setq h (cond ((listp h) (car h)) (t nil)))
    (cond
      ((or (null h) (equal h 0))
        (return (pvtErr 'pvt_runner_delete
                        (lsprintf "history %L not found" historyName)))))
    (setq r (errset (axlRemoveElement h) nil))
    (cond
      ((null r)
        (return (pvtErr 'pvt_runner_delete
                        (lsprintf "axlRemoveElement failed for %L" historyName))))
      (t (return (pvtOk t))))))


(procedure (pvtRunnerGetStatus sess)
  (prog (sessR r)
    (setq sessR (_pvtRunnerResolveSession sess))
    (cond ((pvtIsErr sessR) (return sessR)))
    (setq r (errset (axlGetRunStatus (pvtUnwrap sessR)) nil))
    (cond
      ((and r (car r))
        (return (pvtOk (car r))))
      (t (return (pvtErr 'pvt_runner_status
                         "axlGetRunStatus returned nil"))))))


(printf "[simkit] pvtRunner.il loaded (Phase 3A §3 v1)\n")
