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
;;   pvtRunnerSubmit(sess)                              [v1.1, 2026-05-16]
;;     -> (pvtOk t)                     fire-and-forget; axlRunAllTests
;;        returns immediately even though the sim runs in the background
;;        (DECISIONS #54). Callers poll pvtRunnerGetStatus and call
;;        pvtRunnerRename once idle.
;;
;;   pvtRunnerRename(sess, historyName)                 [v1.1, 2026-05-16]
;;     -> (pvtOk actualHistoryName)     wraps axlGetCurrentHistory +
;;        axlSetHistoryName. DESTRUCTIVE — must only run once
;;        axlGetRunStatus says [0,0]; otherwise risks ASSEMBLER-2423
;;        modal (DECISIONS #54 #5).
;;
;;   pvtRunnerRun(sess, historyName)                    [v1, LEGACY]
;;     -> (pvtOk [status-code sub-code historyName]) on completion
;;     -> (pvtErr 'pvt_runner_run "<msg>")   on submit error (errset trapped)
;;
;;     NOT poll-blocking — dispatches axlRunAllTests + immediately renames.
;;     Safe for CIW interactive use on small/cached runs, BUT unsafe for
;;     real-Spectre runs because the rename hits the setupdb while Maestro
;;     is still finalising the history (modal-dialog risk per DECISIONS
;;     #54). Python orchestrator uses pvtRunnerSubmit + poll + Rename
;;     instead. Kept for back-compat; prefer the split pair.
;;
;;   pvtRunnerGetStatus(sess)
;;     -> (pvtOk [code sub])            wraps axlGetRunStatus(sess)
;;        Pure read — safe to call during the async tail of axlRunAllTests.
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

(procedure (pvtRunnerSubmit sess)
  ;; Fire-and-forget dispatch of axlRunAllTests. Returns immediately even
  ;; though the Spectre sims keep running in the background (DECISIONS
  ;; #54). Caller polls pvtRunnerGetStatus to know when truly idle, THEN
  ;; calls pvtRunnerRename. Splitting the legacy pvtRunnerRun into
  ;; submit + rename gives the Python orchestrator a seam to insert
  ;; poll-to-idle in between, which avoids the ASSEMBLER-2423 modal that
  ;; pops when axlSetHistoryName fires against a still-running setupdb.
  ;;
  ;; Probe findings re-confirmed 2026-05-16:
  ;;   * axlRunAllTests takes (string, string); type template "tt".
  ;;   * 2nd arg ignored as a history name; Maestro always auto-names
  ;;     "Interactive.<N+1>".
  ;;
  ;; Returns (pvtOk t).
  (prog (sessR sessName rc)
    (setq sessR (_pvtRunnerResolveSession sess))
    (cond ((pvtIsErr sessR) (return sessR)))
    (setq sessName (pvtUnwrap sessR))
    (setq rc (errset (axlRunAllTests sessName "") nil))
    (cond
      ((null rc)
        (return (pvtErr 'pvt_runner_submit
                        (lsprintf "axlRunAllTests failed (session %L)"
                                  sessName)))))
    (return (pvtOk t))))


(procedure (pvtRunnerRename sess historyName)
  ;; Rename the session's current history entry. DESTRUCTIVE — must be
  ;; called after the run has reached idle (axlGetRunStatus == [0 0]
  ;; with prior non-idle observation, OR the sim was cached / no-op).
  ;; Calling this against a still-finalising setupdb is the documented
  ;; route to ASSEMBLER-2423 (DECISIONS #54 #5/#6). Returns the actual
  ;; post-rename name (in case Maestro mangles it).
  (prog (sessR sessName newHist hname)
    (cond
      ((or (null historyName) (not (stringp historyName)) (equal historyName ""))
        (return (pvtErr 'pvt_validation
                        "historyName must be a non-empty string"))))
    (setq sessR (_pvtRunnerResolveSession sess))
    (cond ((pvtIsErr sessR) (return sessR)))
    (setq sessName (pvtUnwrap sessR))

    (setq newHist (errset (axlGetCurrentHistory sessName) nil))
    (setq newHist (cond ((listp newHist) (car newHist)) (t nil)))
    (cond
      ((null newHist)
        (return (pvtErr 'pvt_runner_rename
                        "axlGetCurrentHistory returned nil"))))
    (errset (axlSetHistoryName newHist historyName) nil)
    (setq hname (errset (axlGetHistoryName newHist) nil))
    (setq hname (cond ((listp hname) (car hname)) (t historyName)))
    (return (pvtOk hname))))


(procedure (pvtRunnerRun sess historyName)
  ;; LEGACY (v1) — kept for CIW callers. Atomically dispatches +
  ;; immediately renames. NOT poll-blocking; the rename races the
  ;; async sim tail. Safe only for cached / instant runs. Python
  ;; orchestrator uses Submit + poll + Rename instead.
  ;;
  ;; Returns (pvtOk [statusCode subCode actualHistoryName]).
  (prog (subR renameR statusR sessR sessName hname)
    (cond
      ((or (null historyName) (not (stringp historyName)) (equal historyName ""))
        (return (pvtErr 'pvt_validation
                        "historyName must be a non-empty string"))))
    (setq subR (pvtRunnerSubmit sess))
    (cond ((pvtIsErr subR) (return subR)))
    (setq renameR (pvtRunnerRename sess historyName))
    (cond ((pvtIsErr renameR) (return renameR)))
    (setq hname (pvtUnwrap renameR))
    (setq sessR (_pvtRunnerResolveSession sess))
    (cond ((pvtIsErr sessR) (return sessR)))
    (setq sessName (pvtUnwrap sessR))
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
  ;; CAVEAT (probed 2026-05-16): axlGetRunStatus throws an UNCATCHABLE
  ;; *Error* "Cannot find a setup database entry for handle 0." when
  ;; there's no active in-flight run record (i.e. the session is fully
  ;; idle with no recent run, or some time after a run completes and
  ;; Maestro drops the active-run handle). errset does NOT trap this
  ;; class of C-level error; neither does errsetstring or any flag we
  ;; could find. So we let it escape — the Python caller wraps the
  ;; bridge call and treats the "handle 0" RuntimeError as semantically
  ;; equivalent to "idle, no active run." Pre-submit and post-cleanup
  ;; both surface that way.
  ;;
  ;; When the call succeeds, returns (pvtOk [code sub]). Observed
  ;; values during 2026-05-16 dogfood: [0,0] briefly post-completion,
  ;; [1,1] post-cached-run, [5,9] post-real-run-completion (DECISIONS
  ;; #54 #3).
  (prog (sessR r)
    (setq sessR (_pvtRunnerResolveSession sess))
    (cond ((pvtIsErr sessR) (return sessR)))
    (setq r (errset (axlGetRunStatus (pvtUnwrap sessR)) nil))
    (cond
      ((and r (car r))
        (return (pvtOk (car r))))
      (t (return (pvtErr 'pvt_runner_status
                         "axlGetRunStatus returned nil"))))))


;; --- IC source (readns / readic) per Phase 3A v1.2 (DECISIONS #57) ---------
;;
;; Two helpers point the Spectre simulator at a per-corner spectre.fc / .ic /
;; .dc file by writing a +nodeset or +ic command-line arg into the test's
;; `additionalArgs` simulator option. Architectural notes from the
;; 2026-05-16 live probe (DECISIONS #57):
;;
;;   * `readns` / `readic` are NOT in the 133-option Spectre Options form
;;     (the user's "Options" form holds reltol/gmin/temp/iabstol/etc.).
;;     They're netlist-syntax keywords, not option-form fields, so trying
;;     `asiSetSimOptionVal(asi, "readns", path)` returns nil.
;;   * BUT every Spectre test has an `additionalArgs` option (in the 133
;;     defaults) that accepts arbitrary Spectre command-line args. Spectre
;;     CLI supports `+nodeset <file>` and `+ic <file>` for reading the
;;     nodeset / IC files respectively — same semantics as the netlist
;;     `readns`/`readic` keywords. Verified: setting
;;     `additionalArgs="+nodeset /tmp/x.fc"` round-trips cleanly via
;;     asiSetSimOptionVal AND requires zero one-time UI setup from the
;;     user (the option always exists).
;;   * Per-test asi session: asiGetCurrentSession returns the per-test
;;     session matching whichever test is currently active in Maestro.
;;     v1.2 relies on this; v1.3 will switch active test programmatically
;;     once we find the test-switcher API.
;;
;; Trade-off vs. trying to surface `readns` as its own option: we share
;; one slot (`additionalArgs`) with whatever else the user might have put
;; in there. We snapshot the prev value on entry and pass it back through
;; ClearIcSource so the user's manual additionalArgs survive. If the user
;; relies on additionalArgs for OTHER per-test needs (e.g. -log level),
;; concurrent ic_from use will clobber them — flag a v1.2.1 follow-up if
;; this bites in dogfood.
;;
;; pvtRunnerSetIcSource(sess testName icPath mode)
;;   icPath: absolute path to spectre.{fc,ic,dc}
;;   mode: "readns" → emits `+nodeset <icPath>`
;;         "readic" → emits `+ic <icPath>`
;;   -> (pvtOk prevAdditionalArgsValue)  for caller to restore via Clear
;;   -> (pvtErr 'pvt_validation msg)     on bad args
;;   -> (pvtErr 'pvt_runner_set_ic msg)  on api failure
;;
;; pvtRunnerClearIcSource(sess testName mode prevValue)
;;   Restores `additionalArgs` to prevValue ("" / nil → empty).
;;   -> (pvtOk t) / (pvtErr ...)

(procedure (_pvtRunnerValidateMode mode)
  ;; Returns t on valid mode, nil otherwise.
  (and (stringp mode) (or (equal mode "readns") (equal mode "readic"))))


(procedure (_pvtRunnerIcSpectreArg mode icPath)
  ;; mode → Spectre CLI flag:
  ;;   "readns" → "+nodeset"     (soft, nodeset hint)
  ;;   "readic" → "+ic"          (hard, initial condition)
  ;; Returns the assembled arg string (e.g. `"+nodeset /tmp/x.fc"`).
  (cond
    ((equal mode "readns") (lsprintf "+nodeset %s" icPath))
    ((equal mode "readic") (lsprintf "+ic %s" icPath))
    (t nil)))


(procedure (pvtRunnerSetIcSource sess testName icPath mode)
  (prog (sessR sessName asi prev newVal r)
    ;; arg validation
    (cond
      ((or (null testName) (not (stringp testName)) (equal testName ""))
        (return (pvtErr 'pvt_validation "testName must be a non-empty string"))))
    (cond
      ((or (null icPath) (not (stringp icPath)) (equal icPath ""))
        (return (pvtErr 'pvt_validation "icPath must be a non-empty string"))))
    (cond
      ((not (_pvtRunnerValidateMode mode))
        (return (pvtErr 'pvt_validation
                        (lsprintf "mode must be \"readns\" or \"readic\", got %L"
                                  mode)))))

    (setq sessR (_pvtRunnerResolveSession sess))
    (cond ((pvtIsErr sessR) (return sessR)))
    (setq sessName (pvtUnwrap sessR))

    ;; Ref: skartistref.pdf p.636 — asiGetCurrentSession returns the
    ;; currently-active simulation session. In Maestro context the
    ;; per-test session is exposed via this call.
    (setq asi (errset (asiGetCurrentSession) nil))
    (setq asi (cond ((listp asi) (car asi)) (t nil)))
    (cond
      ((null asi)
        (return (pvtErr 'pvt_runner_set_ic
                        "asiGetCurrentSession returned nil — no live ADE session"))))

    ;; Snapshot prev value of additionalArgs so caller can restore on clear.
    ;; Ref: skartistref.pdf p.315 — asiGetSimOptionVal.
    (setq prev (errset (asiGetSimOptionVal asi "additionalArgs") nil))
    (setq prev (cond ((listp prev) (car prev)) (t nil)))

    ;; Assemble the spectre CLI arg.
    (setq newVal (_pvtRunnerIcSpectreArg mode icPath))

    ;; Ref: skartistref.pdf p.321 — asiSetSimOptionVal returns the new
    ;; value on success. additionalArgs always exists in default Spectre
    ;; sessions so this path should not return nil; if it does, that's
    ;; a real failure worth surfacing.
    (setq r (errset (asiSetSimOptionVal asi "additionalArgs" newVal) nil))
    (setq r (cond ((listp r) (car r)) (t nil)))
    (cond
      ((null r)
        (return (pvtErr 'pvt_runner_set_ic
                        (lsprintf
                          "asiSetSimOptionVal(additionalArgs, %L) failed — \
                           session may be read-only or in an unexpected state"
                          newVal)))))

    ;; Return prev (empty-string sentinel for nil to keep Python decode simple)
    (return (pvtOk (cond ((null prev) "") (t prev))))))


(procedure (pvtRunnerClearIcSource sess testName mode prevValue)
  ;; Restores additionalArgs to prevValue. Pass "" / nil to set empty.
  ;; testName + mode are captured for symmetry / future per-test work;
  ;; v1.2 always acts on the currently-active session.
  (prog (sessR asi val r)
    (cond
      ((or (null testName) (not (stringp testName)) (equal testName ""))
        (return (pvtErr 'pvt_validation "testName must be a non-empty string"))))
    (cond
      ((not (_pvtRunnerValidateMode mode))
        (return (pvtErr 'pvt_validation
                        (lsprintf "mode must be \"readns\" or \"readic\", got %L"
                                  mode)))))

    (setq sessR (_pvtRunnerResolveSession sess))
    (cond ((pvtIsErr sessR) (return sessR)))

    (setq asi (errset (asiGetCurrentSession) nil))
    (setq asi (cond ((listp asi) (car asi)) (t nil)))
    (cond
      ((null asi)
        (return (pvtErr 'pvt_runner_clear_ic
                        "asiGetCurrentSession returned nil"))))

    ;; Normalise prevValue: nil → ""
    (setq val (cond
                ((null prevValue) "")
                (t prevValue)))

    (setq r (errset (asiSetSimOptionVal asi "additionalArgs" val) nil))
    ;; Treat nil-return as no-op for clear semantics; the value is
    ;; either already what we wanted or wasn't set.
    (return (pvtOk t))))


;; --- per-corner enable/disable for Phase 3A v1.2 stage-2 ----------------
;;
;; The IC piping (SetIcSource above) gives us per-corner IC values, but
;; the legacy axlRunAllTests submits ALL enabled corners in one batch —
;; so different IC per corner needs us to actually run corners ONE AT A
;; TIME. These helpers let the Python orchestrator iterate corners
;; sequentially: snapshot current enable mask → for each idx, enable
;; only that one → run → restore.
;;
;; Live-probe 2026-05-16 (DECISIONS #57 stage-2): axlSetEnabled accepts
;; a corner handle (returned by axlGetCorner(sdb, name)) and a t/nil
;; payload. Round-trip TT_pvt True→nil→True confirmed working on
;; fnxSession0. axlGetCorners(sdb) returns [containerHandle, names-list]
;; — names-list ORDER matches the explode order of the source union,
;; which matches the /1, /2, ... result-dir numbering Phase 1 collector
;; sees post-run. So 1-based corner_idx is consistent across:
;;   - ic_source.resolve_ic_path(corner_idx=N)
;;   - this helper's enable index
;;   - the per-corner result dir on disk
;;
;; pvtRunnerSnapshotCornersEnable(sess)
;;   -> (pvtOk ((cornerName cornerEnabledBool) ...))
;;     Snapshot serialised as list-of-pairs so the Python orchestrator
;;     can stash it through a Restore call after the loop. Same shape
;;     as pvtRunnerSnapshotTestState for symmetry.
;;
;; pvtRunnerEnableCornerByIndex(sess, idx)
;;   -> (pvtOk enabledCornerName)
;;   -> (pvtErr 'pvt_validation msg) on idx out of range
;;     Disables every corner except the one at 1-based idx. Returns the
;;     enabled corner's name so the caller can log it.
;;
;; pvtRunnerRestoreCornersEnable(sess, snap)
;;   -> (pvtOk snap)
;;     Apply snapshot. Missing names = no-op (snapshot was taken before
;;     a corner was added; nothing to restore).

(procedure (pvtRunnerSnapshotCornersEnable sess)
  (prog (sessR sdbR sdb cornersRaw names out h en)
    (setq sessR (_pvtRunnerResolveSession sess))
    (cond ((pvtIsErr sessR) (return sessR)))
    (setq sdbR (_pvtRunnerGetSdb (pvtUnwrap sessR)))
    (cond ((pvtIsErr sdbR) (return sdbR)))
    (setq sdb (pvtUnwrap sdbR))
    (setq cornersRaw (errset (axlGetCorners sdb) nil))
    (setq cornersRaw (cond ((listp cornersRaw) (car cornersRaw)) (t nil)))
    (cond
      ((or (null cornersRaw) (lessp (length cornersRaw) 2))
        (return (pvtErr 'pvt_runner_snapshot_corners
                        "axlGetCorners returned nil or wrong shape"))))
    (setq names (cadr cornersRaw))
    (setq out nil)
    (foreach name names
      (setq h (errset (axlGetCorner sdb name) nil))
      (setq h (cond ((listp h) (car h)) (t nil)))
      (cond
        (h
          (setq en (errset (axlGetEnabled h) nil))
          (setq en (cond ((and en (car en)) t) (t nil)))
          (setq out (cons (list name en) out)))))
    (return (pvtOk (reverse out)))))


(procedure (pvtRunnerEnableCornerByIndex sess idx)
  (prog (sessR sdbR sdb cornersRaw names targetName h)
    (cond
      ((or (null idx) (not (integerp idx)) (lessp idx 1))
        (return (pvtErr 'pvt_validation
                        (lsprintf "idx must be a positive integer (1-based), got %L"
                                  idx)))))
    (setq sessR (_pvtRunnerResolveSession sess))
    (cond ((pvtIsErr sessR) (return sessR)))
    (setq sdbR (_pvtRunnerGetSdb (pvtUnwrap sessR)))
    (cond ((pvtIsErr sdbR) (return sdbR)))
    (setq sdb (pvtUnwrap sdbR))
    (setq cornersRaw (errset (axlGetCorners sdb) nil))
    (setq cornersRaw (cond ((listp cornersRaw) (car cornersRaw)) (t nil)))
    (cond
      ((or (null cornersRaw) (lessp (length cornersRaw) 2))
        (return (pvtErr 'pvt_runner_enable_corner
                        "axlGetCorners returned nil or wrong shape"))))
    (setq names (cadr cornersRaw))
    (cond
      ((greaterp idx (length names))
        (return (pvtErr 'pvt_validation
                        (lsprintf "idx %d out of range (only %d corners)"
                                  idx (length names))))))
    (setq targetName (nthelem idx names))  ; nthelem is 1-based in SKILL
    (foreach name names
      (setq h (errset (axlGetCorner sdb name) nil))
      (setq h (cond ((listp h) (car h)) (t nil)))
      (cond
        (h
          (axlSetEnabled h (if (equal name targetName) t nil)))))
    (return (pvtOk targetName))))


(procedure (pvtRunnerRestoreCornersEnable sess snap)
  (prog (sessR sdbR sdb pair name want h)
    (setq sessR (_pvtRunnerResolveSession sess))
    (cond ((pvtIsErr sessR) (return sessR)))
    (setq sdbR (_pvtRunnerGetSdb (pvtUnwrap sessR)))
    (cond ((pvtIsErr sdbR) (return sdbR)))
    (setq sdb (pvtUnwrap sdbR))
    (foreach pair snap
      (setq name (car pair))
      (setq want (cadr pair))
      (setq h (errset (axlGetCorner sdb name) nil))
      (setq h (cond ((listp h) (car h)) (t nil)))
      (cond
        (h (axlSetEnabled h (if want t nil)))))
    (return (pvtOk snap))))


;; --- v1.3: pre-run-script attachment for single-batch per-corner IC ---
;;
;; Architecture (DECISIONS #57 stage-3): Maestro lets you attach a
;; per-test SKILL script via axlImportPreRunScript that fires BEFORE
;; each (test, corner) point is netlisted. The script runs in
;; Maestro's worker virtuoso VM and has access to:
;;   * axlGetCornerNameForCurrentPointInRun — returns the exact
;;     sub-corner name (e.g. "TT_pvt_3", not just "TT_pvt")
;;   * asiGetCurrentSession / asiGetSimOptionVal / asiSetSimOptionVal
;;     on the per-test asi session
;; Combining these lets us write per-corner-specific +nodeset/+ic into
;; the test's additionalArgs IMMEDIATELY before its netlist+Spectre
;; invocation — single axlRunAllTests, single Maestro history, per-corner
;; IC delivered. Replaces v1.2 stage-2's N-submit pattern.
;;
;; Live-probed 2026-05-16 on fnxSession0:
;;   * Pre-run fires once per sub-corner — 9 distinct firings for
;;     1 + 6 + 1 = 8 sub-corners on a 3-row union (TT, TT_pvt × 6 expand,
;;     TT_2p5G) + one pre-flight call with corner="" that we MUST guard
;;     against (asi is nil at that point).
;;   * asiSetSimOptionVal works in worker VM; write persists across
;;     sub-corner pre-run firings (within the same run).
;;   * Script returning nil aborts that corner; we always return t.
;;
;; pvtRunnerInstallPreRunScript(sess, testName, scriptPath)
;;   Wraps axlImportPreRunScript + axlSetPreRunScriptEnabled(true).
;;   -> (pvtOk attachedPath)  the path Maestro recorded
;;   -> (pvtErr 'pvt_validation msg)
;;   -> (pvtErr 'pvt_runner_install_prerun msg)
;;
;; pvtRunnerDisablePreRunScript(sess, testName)
;;   axlSetPreRunScriptEnabled(false). Maestro has no detach API, so
;;   disable is our equivalent of "remove."
;;   -> (pvtOk t)
;;
;; pvtRunnerGetPreRunScript(sess, testName)
;;   Returns the currently-attached script path (or "" if none) so the
;;   caller can snapshot it before install and restore on cleanup.
;;   -> (pvtOk pathOrEmpty)

(procedure (pvtRunnerInstallPreRunScript sess testName scriptPath)
  (prog (sessR sessName r)
    (cond
      ((or (null testName) (not (stringp testName)) (equal testName ""))
        (return (pvtErr 'pvt_validation "testName must be a non-empty string"))))
    (cond
      ((or (null scriptPath) (not (stringp scriptPath)) (equal scriptPath ""))
        (return (pvtErr 'pvt_validation "scriptPath must be a non-empty string"))))
    (cond
      ((not (isFile scriptPath))
        (return (pvtErr 'pvt_validation
                        (lsprintf "scriptPath %L does not exist on disk"
                                  scriptPath)))))
    (setq sessR (_pvtRunnerResolveSession sess))
    (cond ((pvtIsErr sessR) (return sessR)))
    (setq sessName (pvtUnwrap sessR))
    (setq r (errset (axlImportPreRunScript sessName testName scriptPath) nil))
    (setq r (cond ((listp r) (car r)) (t nil)))
    (cond
      ((null r)
        (return (pvtErr 'pvt_runner_install_prerun
                        (lsprintf "axlImportPreRunScript failed for test %L"
                                  testName)))))
    (errset (axlSetPreRunScriptEnabled sessName testName t) nil)
    (return (pvtOk r))))


(procedure (pvtRunnerDisablePreRunScript sess testName)
  (prog (sessR sessName)
    (cond
      ((or (null testName) (not (stringp testName)) (equal testName ""))
        (return (pvtErr 'pvt_validation "testName must be a non-empty string"))))
    (setq sessR (_pvtRunnerResolveSession sess))
    (cond ((pvtIsErr sessR) (return sessR)))
    (setq sessName (pvtUnwrap sessR))
    (errset (axlSetPreRunScriptEnabled sessName testName nil) nil)
    (return (pvtOk t))))


(procedure (pvtRunnerGetPreRunScript sess testName)
  (prog (sessR sessName r)
    (cond
      ((or (null testName) (not (stringp testName)) (equal testName ""))
        (return (pvtErr 'pvt_validation "testName must be a non-empty string"))))
    (setq sessR (_pvtRunnerResolveSession sess))
    (cond ((pvtIsErr sessR) (return sessR)))
    (setq sessName (pvtUnwrap sessR))
    (setq r (errset (axlGetPreRunScript sessName testName) nil))
    (setq r (cond ((listp r) (car r)) (t nil)))
    ;; axlGetPreRunScript returns "" when nothing attached; preserve.
    (return (pvtOk (cond ((null r) "") (t r))))))


(printf "[simkit] pvtRunner.il loaded (Phase 3A v1.3: + pre-run script install/disable/get)\n")
