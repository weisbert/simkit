;; ============================================================================
;; testPvtProject.il -- exercise pvtProject.il via the fixture files.
;;
;; Each test loads a fixture from skill/tests/fixtures/ via its full path.
;; Fixtures named `*.pvtproject` are NOT renamed at test time; we pass the
;; full path explicitly to pvtParsePvtProject.  The walker / env-var paths
;; are exercised separately in the dedicated tests below.
;; ============================================================================

;; --------------------------------------------------------------------------
;; helpers
;; --------------------------------------------------------------------------

(procedure (_pvtTestParseFixture name)
  (pvtParsePvtProject (pvtTestFixture name))
)

(procedure (_pvtTestEnsureScratchDir prefix)
  "Create a unique empty subdirectory under the system tempdir and return
   its absolute path. Caller is responsible for cleanup if desired; for
   these tests we leave artefacts behind because OS will clean /tmp."
  (let ((root (getTempDir))
        ;; getCurrentTime returns a date STRING in classic SKILL — use %s, not %d.
        (id (sprintf nil "%s_%d_%d" prefix (random 1000000) (random 1000000)))
        (dir nil))
    (setq dir (strcat root "/" id))
    ;; createDir is the SKILL builtin to create directories. Some Cadence
    ;; versions name it `system("mkdir -p ...")`. We try createDir first.
    (let ((rv (errset (createDir dir) nil)))
      (when (null rv) (system (sprintf nil "mkdir -p %s" dir))))
    (simplifyFilename dir))
)


;; --------------------------------------------------------------------------
;; Direct parse (no walker) — minimum-valid sanity
;; --------------------------------------------------------------------------

(pvtTestRegister "project/min-valid-loads"
  (lambda ()
    (let ((r (_pvtTestParseFixture "min_valid.pvtproject")))
      (pvtAssertOk r "min valid loads")
      (let ((p (pvtUnwrap r)))
        (pvtAssert p->project "my_ldo" "project field")
        (pvtAssert p->author nil "author defaults to nil")
        (pvtAssert p->schemaVersion 1 "schema_version defaults to 1")))))


;; --------------------------------------------------------------------------
;; Validation negatives — first-error-wins semantics
;; --------------------------------------------------------------------------

(pvtTestRegister "project/bad-json-unterminated"
  (lambda ()
    (let ((r (_pvtTestParseFixture "bad_json_unterminated.pvtproject")))
      (pvtAssertErrCategory r 'pvt_validation "unterminated JSON -> validation"))))

(pvtTestRegister "project/bad-json-trailing-comma"
  (lambda ()
    (let ((r (_pvtTestParseFixture "bad_json_trailing_comma.pvtproject")))
      (pvtAssertErrCategory r 'pvt_validation "trailing comma -> validation"))))

(pvtTestRegister "project/missing-project-field"
  (lambda ()
    (let ((r (_pvtTestParseFixture "missing_project.pvtproject")))
      (pvtAssertErrCategory r 'pvt_validation "missing project")
      (when (pvtIsErr r)
        (pvtAssert (and (pcreMatchp "project" (pvtErrMessage r)) t)
                   t "msg mentions 'project'")))))

(pvtTestRegister "project/missing-dbroot-field"
  (lambda ()
    (let ((r (_pvtTestParseFixture "missing_dbroot.pvtproject")))
      (pvtAssertErrCategory r 'pvt_validation "missing dbRoot"))))

(pvtTestRegister "project/regex-uppercase-rejected"
  (lambda ()
    (let ((r (_pvtTestParseFixture "project_uppercase.pvtproject")))
      (pvtAssertErrCategory r 'pvt_validation "uppercase project name rejected"))))

(pvtTestRegister "project/regex-spaces-rejected"
  (lambda ()
    (let ((r (_pvtTestParseFixture "project_with_spaces.pvtproject")))
      (pvtAssertErrCategory r 'pvt_validation "spaces in project name rejected"))))

(pvtTestRegister "project/dbroot-empty-rejected"
  (lambda ()
    (let ((r (_pvtTestParseFixture "dbroot_empty.pvtproject")))
      (pvtAssertErrCategory r 'pvt_validation "empty dbRoot rejected"))))

(pvtTestRegister "project/dbroot-wrong-type-rejected"
  (lambda ()
    (let ((r (_pvtTestParseFixture "dbroot_wrong_type.pvtproject")))
      (pvtAssertErrCategory r 'pvt_validation "non-string dbRoot rejected"))))

(pvtTestRegister "project/author-null-becomes-nil"
  (lambda ()
    (let ((r (_pvtTestParseFixture "author_null.pvtproject")))
      (pvtAssertOk r "author:null parses")
      (when (pvtIsOk r)
        (let ((p (pvtUnwrap r)))
          (pvtAssert p->author nil "author == nil"))))))

(pvtTestRegister "project/author-int-rejected"
  (lambda ()
    (let ((r (_pvtTestParseFixture "author_int.pvtproject")))
      (pvtAssertErrCategory r 'pvt_validation "int author rejected"))))

(pvtTestRegister "project/aliases-duplicate-value-rejected"
  (lambda ()
    (let ((r (_pvtTestParseFixture "aliases_duplicate.pvtproject")))
      (pvtAssertErrCategory r 'pvt_validation "duplicate alias value rejected"))))

(pvtTestRegister "project/aliases-int-value-rejected"
  (lambda ()
    (let ((r (_pvtTestParseFixture "aliases_int_value.pvtproject")))
      (pvtAssertErrCategory r 'pvt_validation "non-string alias value rejected"))))

(pvtTestRegister "project/schema-version-999-rejected"
  (lambda ()
    (let ((r (_pvtTestParseFixture "schema_version_999.pvtproject")))
      (pvtAssertErrCategory r 'pvt_validation "schema_version 999 unsupported"))))

(pvtTestRegister "project/schema-version-bool-rejected"
  (lambda ()
    (let ((r (_pvtTestParseFixture "schema_version_bool.pvtproject")))
      (pvtAssertErrCategory r 'pvt_validation "schema_version true rejected"))))

(pvtTestRegister "project/schema-version-string-rejected"
  (lambda ()
    (let ((r (_pvtTestParseFixture "schema_version_string.pvtproject")))
      (pvtAssertErrCategory r 'pvt_validation "schema_version \"1\" rejected"))))


;; --------------------------------------------------------------------------
;; Unknown-key & underscore-key behaviours
;; --------------------------------------------------------------------------

(pvtTestRegister "project/unknown-key-warns-but-loads"
  (lambda ()
    (let ((r (_pvtTestParseFixture "unknown_key.pvtproject")))
      ;; SKILL `warn` does not raise; we cannot easily intercept the buffered
      ;; warning text in a portable way, so we only assert :ok.
      (pvtAssertOk r "unknown key still loads"))))

(pvtTestRegister "project/underscore-key-silent"
  (lambda ()
    (let ((r (_pvtTestParseFixture "underscore_keys.pvtproject")))
      (pvtAssertOk r "underscore keys still load"))))


;; --------------------------------------------------------------------------
;; Walker behaviours (cwd ↑) and env-var precedence.
;; These tests exercise pvtFindPvtProject and pvtLoadPvtProject directly
;; using a scratch directory tree under /tmp.
;; --------------------------------------------------------------------------

(pvtTestRegister "find/walks-up-from-deep-dir"
  (lambda ()
    (let ((root (_pvtTestEnsureScratchDir "simkit_find")))
      (let ((deep    (strcat root "/a/b/c"))
            (fixture (pvtTestFixture "min_valid.pvtproject"))
            (target  (strcat root "/.pvtproject")))
        (system (sprintf nil "mkdir -p %s" deep))
        (system (sprintf nil "cp %s %s" fixture target))
        (let ((found (pvtFindPvtProject deep)))
          (pvtAssert (and found (equal (simplifyFilename target) found))
                     t "walker returned exact root .pvtproject"))))))

(pvtTestRegister "find/returns-nil-when-walked-to-root"
  (lambda ()
    ;; We start in /tmp/<unique>/sub which has no ancestor .pvtproject within
    ;; that subtree.  An ancestor on the host might own one; we can only
    ;; assert that the returned path is NOT inside our scratch root.
    (let ((root (_pvtTestEnsureScratchDir "simkit_nofind")))
      (let ((deep (strcat root "/x/y")))
        (system (sprintf nil "mkdir -p %s" deep))
        (let ((found (pvtFindPvtProject deep)))
          (cond
            ((null found)
              (pvtAssert t t "no .pvtproject found in scratch tree"))
            (t
              ;; If found, must be strictly outside root.
              (pvtAssert (and (not (pcreMatchp root found)) t)
                         t "found path outside scratch (host-level)"))))))))

(pvtTestRegister "load/env-var-beats-walker"
  (lambda ()
    (let ((root (_pvtTestEnsureScratchDir "simkit_envwin")))
      (let ((far  (strcat root "/far/.pvtproject"))
            (near (strcat root "/near/deep")))
        (system (sprintf nil "mkdir -p %s" (strcat root "/far")))
        (system (sprintf nil "mkdir -p %s" near))
        ;; Far file uses a unique project name we can verify came back.
        (let ((p (outfile far)))
          (fprintf p "{\"project\":\"far_proj\",\"dbRoot\":\"./d\"}")
          (close p))
        ;; Near file uses a different project to ensure the walker would see it.
        (let ((p (outfile (strcat root "/near/.pvtproject"))))
          (fprintf p "{\"project\":\"near_proj\",\"dbRoot\":\"./d\"}")
          (close p))
        (let ((r (pvtLoadPvtProject ?start near ?env far)))
          (pvtAssertOk r "env-var beats walker")
          (when (pvtIsOk r)
            (let ((p (pvtUnwrap r)))
              (pvtAssert p->project "far_proj" "got far_proj"))))))))

(pvtTestRegister "load/env-var-missing-file-is-not-found"
  (lambda ()
    (let ((r (pvtLoadPvtProject ?env "/nonexistent/.pvtproject")))
      (pvtAssertErrCategory r 'pvt_notFound "env points to missing file"))))


;; --------------------------------------------------------------------------
;; Bundled example file should load cleanly.
;; --------------------------------------------------------------------------

(pvtTestRegister "example/bundled-loads-cleanly"
  (lambda ()
    (let ((scratch (_pvtTestEnsureScratchDir "simkit_example")))
      ;; PVT_SKILL_ROOT is `<simkit>/skill`; the example file lives at
      ;; `<simkit>/config/pvtproject.example.json`. Anchor the lookup on the
      ;; skill-root global rather than (getWorkingDir), which on a running
      ;; Virtuoso is rarely the simkit checkout root.
      (let ((target (strcat scratch "/.pvtproject"))
            (example (strcat (_pvtParentDir PVT_SKILL_ROOT)
                             "/config/pvtproject.example.json")))
        (system (sprintf nil "cp %s %s" example target))
        (let ((r (pvtParsePvtProject target)))
          (pvtAssertOk r "bundled example loads")
          (when (pvtIsOk r)
            (let ((p (pvtUnwrap r)))
              (pvtAssert p->project "my_ldo" "example project")
              (pvtAssert p->author "yusheng" "example author")
              (pvtAssert (pvtProjectAliasFor p "MY_LIB/ldo_top_tb/schematic")
                         "ldo_heavy_tb"
                         "example alias resolves"))))))))


t
