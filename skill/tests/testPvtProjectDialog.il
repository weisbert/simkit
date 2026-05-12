;; ============================================================================
;; testPvtProjectDialog.il
;;
;; Tier-1 tests for the pure layers of pvtProjectDialog.il. The UI wrapper
;; itself (pvtProjectFirstSaveDialog) is exercised via Tier-2 scenarios in
;; skill/tests/tier2/scenarios.md against a live Virtuoso session.
;;
;; Reuses _pvtTestEnsureScratchDir from testPvtProject.il for fs fixtures;
;; load order in runTests.il guarantees that helper exists.
;; ============================================================================


;; --------------------------------------------------------------------------
;; Helpers local to this test file
;; --------------------------------------------------------------------------

(procedure (_pvtTestDlgScratch)
  (_pvtTestEnsureScratchDir "dialog")
)

(procedure (_pvtTestDlgGetField alist key)
  (cadr (assoc key alist))
)


;; ============================================================================
;; Defaults derivation
;; ============================================================================

(pvtTestRegister "dialog/defaults-cwd-basename-no-session"
  (lambda ()
    (let ((d (_pvtDlgDeriveDefaults ?cwd "/home/yusheng/cadence_work/my_ldo"
                                    ?session nil)))
      (pvtAssert (_pvtTestDlgGetField d 'project) "my_ldo"
                 "cwd basename used when no session")
      (pvtAssert (_pvtTestDlgGetField d 'dbRoot) "./simkit_data"
                 "dbRoot default = ./simkit_data"))))

(pvtTestRegister "dialog/defaults-lib-normalised"
  (lambda ()
    (let ((d (_pvtDlgDeriveDefaults ?cwd "/tmp/x"
                                    ?session '("MY_LIB" "amp" "schematic"))))
      (pvtAssert (_pvtTestDlgGetField d 'project) "my_lib"
                 "lib name lowercased to match project regex"))))

(pvtTestRegister "dialog/defaults-author-from-USER"
  (lambda ()
    ;; The pure layer reads getShellEnvVar at call time. We don't override
    ;; $USER in tests — but we can at least assert the field is a string
    ;; (or empty if USER is unset, which Tier-1 unit env may produce).
    (let ((d (_pvtDlgDeriveDefaults ?cwd "/tmp/x" ?session nil)))
      (pvtAssert (stringp (_pvtTestDlgGetField d 'author)) t
                 "author default is a string"))))

(pvtTestRegister "dialog/defaults-invalid-lib-falls-back-to-cwd"
  (lambda ()
    (let ((d (_pvtDlgDeriveDefaults ?cwd "/home/yusheng/my_proj"
                                    ?session '("___" "x" "y"))))
      ;; "___" normalises to nil (no alphanumeric); falls back to cwd basename.
      (pvtAssert (_pvtTestDlgGetField d 'project) "my_proj"
                 "invalid lib falls back to cwd basename"))))

(pvtTestRegister "dialog/defaults-caller-override-wins"
  (lambda ()
    (let ((d (_pvtDlgDeriveDefaults
               ?cwd "/tmp/x"
               ?session '("LIB" "x" "y")
               ?defaults (list (list 'project "explicit_name")
                               (list 'dbRoot  "/explicit/root")))))
      (pvtAssert (_pvtTestDlgGetField d 'project) "explicit_name"
                 "caller ?defaults overrides derived project")
      (pvtAssert (_pvtTestDlgGetField d 'dbRoot) "/explicit/root"
                 "caller ?defaults overrides derived dbRoot"))))


;; ============================================================================
;; Project-name validator
;; ============================================================================

(pvtTestRegister "dialog/validate-project-rejects-empty"
  (lambda ()
    (pvtAssertErrCategory (_pvtDlgValidateProject "") 'pvt_dialog
                          "empty project rejected")))

(pvtTestRegister "dialog/validate-project-rejects-uppercase"
  (lambda ()
    (pvtAssertErrCategory (_pvtDlgValidateProject "My_LDO") 'pvt_dialog
                          "uppercase project rejected")))

(pvtTestRegister "dialog/validate-project-rejects-spaces"
  (lambda ()
    (pvtAssertErrCategory (_pvtDlgValidateProject "my ldo") 'pvt_dialog
                          "project containing space rejected")))

(pvtTestRegister "dialog/validate-project-accepts-mixed-allowed"
  (lambda ()
    (pvtAssertOk (_pvtDlgValidateProject "vco_lo-v2_3")
                 "hyphen + underscore + digits accepted")))


;; ============================================================================
;; DB-root validator
;; ============================================================================

(pvtTestRegister "dialog/validate-dbroot-rejects-empty"
  (lambda ()
    (pvtAssertErrCategory (_pvtDlgValidateDbRoot "") 'pvt_dialog
                          "empty dbRoot rejected (caller defaults first)")))

(pvtTestRegister "dialog/validate-dbroot-accepts-relative"
  (lambda ()
    (pvtAssertOk (_pvtDlgValidateDbRoot "./simkit_data")
                 "./relative dbRoot accepted")))

(pvtTestRegister "dialog/validate-dbroot-accepts-absolute"
  (lambda ()
    (pvtAssertOk (_pvtDlgValidateDbRoot "/scratch/proj_data")
                 "/absolute dbRoot accepted")))


;; ============================================================================
;; Author validator
;; ============================================================================

(pvtTestRegister "dialog/validate-author-accepts-empty-as-ok"
  (lambda ()
    (let ((r (_pvtDlgValidateAuthor "")))
      (pvtAssertOk r "empty author yields :ok (build-table omits the key)"))))

(pvtTestRegister "dialog/validate-author-rejects-non-string"
  (lambda ()
    (pvtAssertErrCategory (_pvtDlgValidateAuthor 42) 'pvt_dialog
                          "non-string author rejected")))


;; ============================================================================
;; Save-path validator (fs-touching — needs scratch dir)
;; ============================================================================

(pvtTestRegister "dialog/validate-savepath-requires-pvtproject-leaf"
  (lambda ()
    (let ((dir (_pvtTestDlgScratch)))
      (pvtAssertErrCategory
        (_pvtDlgValidateSavePath (strcat dir "/not_the_right.name"))
        'pvt_dialog
        "save path leaf must equal .pvtproject"))))

(pvtTestRegister "dialog/validate-savepath-rejects-existing-file"
  (lambda ()
    (letseq ((dir (_pvtTestDlgScratch))
           (path (strcat dir "/.pvtproject"))
           (port (outfile path "w")))
      (fprintf port "{}\n")
      (close port)
      (pvtAssertErrCategory (_pvtDlgValidateSavePath path)
                            'pvt_dialog
                            "existing file refused (no overwrite)"))))

(pvtTestRegister "dialog/validate-savepath-rejects-missing-parent"
  (lambda ()
    (letseq ((dir (_pvtTestDlgScratch))
           (path (strcat dir "/no_such_subdir/.pvtproject")))
      (pvtAssertErrCategory (_pvtDlgValidateSavePath path)
                            'pvt_dialog
                            "missing parent dir refused"))))

(pvtTestRegister "dialog/validate-savepath-accepts-good"
  (lambda ()
    (letseq ((dir (_pvtTestDlgScratch))
           (path (strcat dir "/.pvtproject")))
      (pvtAssertOk (_pvtDlgValidateSavePath path)
                   "fresh path under existing dir accepted"))))


;; ============================================================================
;; Build / write round-trip
;; ============================================================================

(pvtTestRegister "dialog/write-roundtrip-min-fields"
  (lambda ()
    (letseq ((dir  (_pvtTestDlgScratch))
           (path (strcat dir "/.pvtproject"))
           (wr   (_pvtDlgWriteFile
                   path
                   (list (list 'project "demo_proj")
                         (list 'dbRoot  "./simkit_data")
                         (list 'author  "")))))
      (pvtAssertOk wr "min-fields write succeeded")
      (let ((rd (pvtParsePvtProject path)))
        (pvtAssertOk rd "min-fields round-trip parse")
        (when (pvtIsOk rd)
          (let ((p (pvtUnwrap rd)))
            (pvtAssert p->project "demo_proj" "round-trip project")
            (pvtAssert p->author nil "round-trip author omitted -> nil")
            (pvtAssert p->schemaVersion 1 "round-trip schemaVersion = 1")))))))

(pvtTestRegister "dialog/write-roundtrip-with-author"
  (lambda ()
    (letseq ((dir  (_pvtTestDlgScratch))
           (path (strcat dir "/.pvtproject"))
           (wr   (_pvtDlgWriteFile
                   path
                   (list (list 'project "demo_proj")
                         (list 'dbRoot  "./simkit_data")
                         (list 'author  "alice")))))
      (pvtAssertOk wr "author-set write succeeded")
      (let ((rd (pvtParsePvtProject path)))
        (pvtAssertOk rd "author-set round-trip parse")
        (when (pvtIsOk rd)
          (pvtAssert (pvtUnwrap rd)->author "alice"
                     "round-trip author preserved"))))))

(pvtTestRegister "dialog/write-schema-version-is-integer"
  (lambda ()
    ;; pvtParsePvtProject already rejects bool / string schema_version
    ;; (see _pvtValSchemaVersion). A successful parse proves it was emitted
    ;; as an integer. Also assert the raw JSON byte representation contains
    ;; `"schema_version": 1` rather than `"schema_version": "1"`.
    (letseq ((dir  (_pvtTestDlgScratch))
           (path (strcat dir "/.pvtproject"))
           (wr0  (_pvtDlgWriteFile
                   path
                   (list (list 'project "demo_proj")
                         (list 'dbRoot  "./d")
                         (list 'author  ""))))
           (port (infile path))
           (raw  ""))
      (let ((line nil))
        (while (gets line port) (setq raw (strcat raw line))))
      (close port)
      (pvtAssert (pcreMatchp "\"schema_version\"[[:space:]]*:[[:space:]]*1[^\"0-9]" raw) t
                 "schema_version emitted as bare integer 1"))))

(pvtTestRegister "dialog/write-author-blank-omits-key"
  (lambda ()
    (letseq ((dir  (_pvtTestDlgScratch))
           (path (strcat dir "/.pvtproject"))
           (wr0  (_pvtDlgWriteFile
                   path
                   (list (list 'project "demo_proj")
                         (list 'dbRoot  "./d")
                         (list 'author  ""))))
           (port (infile path))
           (raw  ""))
      (let ((line nil))
        (while (gets line port) (setq raw (strcat raw line))))
      (close port)
      (pvtAssert (pcreMatchp "\"author\"" raw) nil
                 "blank author -> author key entirely absent from JSON"))))

(pvtTestRegister "dialog/write-refuses-to-overwrite"
  (lambda ()
    (letseq ((dir  (_pvtTestDlgScratch))
           (path (strcat dir "/.pvtproject"))
           (wr1  (_pvtDlgWriteFile
                   path
                   (list (list 'project "first")
                         (list 'dbRoot  "./d")
                         (list 'author  ""))))
           (wr2  nil))
      (pvtAssertOk wr1 "first write succeeded")
      ;; _pvtDlgWriteFile itself opens with mode "w" so it WILL overwrite —
      ;; the no-overwrite guarantee comes from _pvtDlgValidateSavePath in
      ;; front of it. Verify that combined gate refuses the second attempt.
      (setq wr2 (_pvtDlgValidateSavePath path))
      (pvtAssertErrCategory wr2 'pvt_dialog
                            "savepath validator refuses second write"))))


t
