;; ============================================================================
;; pvtProject.il
;;
;; .pvtproject discovery, parsing and validation. Mirrors python/simkit/
;; project.py at the semantic level, deviating only on naming (camelCase
;; vs snake_case) per the project's SKILL conventions.
;;
;; SKILL note: every procedure that uses `(return ...)` for early exit MUST
;; have its body wrapped in `(prog () ...)`, because in classic SKILL `return`
;; only escapes the enclosing `prog` form (sklangref.pdf p.379).
;;
;; Public API:
;;   pvtFindPvtProject(@optional t_startDir)      -> string|nil
;;   pvtLoadPvtProject(@key start env allowDialog) -> pvtError result
;;   pvtParsePvtProject(t_path)                   -> pvtError result
;;   pvtProjectAliasFor(o_proj t_testbenchId)     -> string|nil
;;
;; Module record:
;;   pvtProject defstruct
;;     project           string
;;     dbRoot            string  (absolute, simplifyFilename'd)
;;     author            string|nil
;;     testbenchAliases  assoc table  (string -> string)
;;     schemaVersion     fixnum
;;     sourcePath        string  (absolute path of the .pvtproject file)
;; ============================================================================

;; --- module-level constants -------------------------------------------------

(defvar PVTPROJECT_FILENAME ".pvtproject")
(defvar PVT_ENV_VAR         "PVT_PROJECT")

(defvar _PVT_SUPPORTED_SCHEMA_VERSIONS '(1))

;; Mirrors Python project.py:_KNOWN_FIELDS. Phase 2 added unionsDir;
;; Phase 3A added reviewsDir; Phase 3B added templatesDir / signalGroupsDir /
;; measurementsDir. Unknown keys emit a warning + are ignored (forward-compat).
(defvar _PVT_KNOWN_FIELDS
  '("project" "dbRoot" "author" "testbench_aliases" "schema_version"
    "unionsDir" "reviewsDir"
    "templatesDir" "signalGroupsDir" "measurementsDir"))

;; project name regex per docs/schema.md §1, identical to Python
;; project.py:_PROJECT_NAME_RE. Loosened to allow uppercase (Phase 4 production
;; names like "1AXX", "NDIV_pre_FDR" — engineers should not be forced into
;; lowercase). See DECISIONS #79.
(defvar _PVT_PROJECT_NAME_RE "^[A-Za-z0-9_-]+$")


;; --- defstruct --------------------------------------------------------------
;; Reference: defstruct sklangref.pdf p.80. Constructor function name is
;; `make_pvtProject`; slot reads use `s->slot`; slot writes use `s->slot = v`.
(defstruct pvtProject
  project
  dbRoot
  author
  testbenchAliases
  schemaVersion
  sourcePath)


;; ============================================================================
;; Discovery: walker
;; ============================================================================

(procedure (_pvtParentDir absPath)
  "Return the parent directory of an absolute path, or '/' at filesystem root.
   SKILL has no `dirname` builtin; we slice on the last '/'.  Caller must
   pass an already-simplified path (simplifyFilename, sklangref p.465)."
  (prog (n i lastSlash)
    (setq i 1)
    (setq lastSlash 0)
    (cond
      ((or (null absPath) (not (stringp absPath))) (return nil))
      ((equal absPath "/") (return "/")))
    (setq n (strlen absPath))
    (while (leqp i n)
      (cond
        ((equal (substring absPath i 1) "/")
          (setq lastSlash i)))
      (setq i (plus i 1)))
    (cond
      ((equal lastSlash 0) (return absPath))           ; no slash: leave as-is
      ((equal lastSlash 1) (return "/"))               ; "/foo" -> "/"
      (t (return (substring absPath 1 (difference lastSlash 1))))))
)

(procedure (pvtFindPvtProject @optional (startDir nil))
  "Walk up from `startDir` (or cwd if nil) looking for a .pvtproject file.
   Returns the fully resolved path of the first match, or nil at root."
  (prog (cur candidate parent found)
    ;; getWorkingDir returns ~/-prefixed form (sklangref p.630), so we pipe
    ;; it through simplifyFilename (p.465) to get an absolute path.
    (setq cur
          (cond
            ((null startDir) (simplifyFilename (getWorkingDir)))
            (t               (simplifyFilename startDir))))
    ;; If the start happens to be a regular file, walk up from its dir.
    (cond
      ((isFile cur) (setq cur (_pvtParentDir cur))))
    (while (and cur (null found))
      (setq candidate (strcat cur "/" PVTPROJECT_FILENAME))
      (cond
        ((isFile candidate)
          (setq found (simplifyFilename candidate)))
        (t
          (setq parent (_pvtParentDir cur))
          (cond
            ((or (null parent) (equal parent cur))
              (setq cur nil))
            (t (setq cur parent))))))
    (return found))
)


;; ============================================================================
;; Validators
;;
;; Each validator returns a pvtError discriminated result.  First-error-wins:
;; pvtParsePvtProject calls them in sequence and returns the first :err.
;; ============================================================================

(procedure (_pvtValSchemaVersion path obj)
  ;; Default 1 when absent; must be a fixnum (NOT a JSON bool sentinel);
  ;; must be a member of _PVT_SUPPORTED_SCHEMA_VERSIONS.
  (let ((raw (arrayref obj "schema_version")))
    (cond
      ((pvtJsonAbsentp raw) (pvtOk 1))
      ;; Python rejects bools because isinstance(True, int) == True. JSON
      ;; bools come through as the sentinel symbols 'pvt_json_true /
      ;; 'pvt_json_false, so the check is symbolic, not numeric.
      ((pvtJsonBoolp raw)
        (pvtErr 'pvt_validation
                "'schema_version' must be an integer (got JSON boolean)"
                path))
      ((not (integerp raw))
        (pvtErr 'pvt_validation
                "'schema_version' must be an integer"
                path))
      ((not (member raw _PVT_SUPPORTED_SCHEMA_VERSIONS))
        (pvtErr 'pvt_validation
                (sprintf nil "schema_version %d not supported (supported: %L)"
                         raw _PVT_SUPPORTED_SCHEMA_VERSIONS)
                path))
      (t (pvtOk raw))))
)

(procedure (_pvtValProject path obj)
  (let ((raw (arrayref obj "project")))
    (cond
      ((pvtJsonAbsentp raw)
        (pvtErr 'pvt_validation "missing required field 'project'" path))
      ((not (stringp raw))
        (pvtErr 'pvt_validation "'project' must be a string" path))
      ((not (pcreMatchp _PVT_PROJECT_NAME_RE raw))
        (pvtErr 'pvt_validation
                (sprintf nil "'project' %L does not match %s"
                         raw _PVT_PROJECT_NAME_RE)
                path))
      (t (pvtOk raw))))
)

(procedure (_pvtValDbRoot path obj)
  (let ((raw (arrayref obj "dbRoot"))
        (resolved nil)
        (parent  nil)
        (firstCh nil))
    (cond
      ((pvtJsonAbsentp raw)
        (pvtErr 'pvt_validation "missing required field 'dbRoot'" path))
      ((not (stringp raw))
        (pvtErr 'pvt_validation "'dbRoot' must be a non-empty string" path))
      ((equal raw "")
        (pvtErr 'pvt_validation "'dbRoot' must be a non-empty string" path))
      (t
        ;; Resolve. Absolute paths (incl. ~/...) handed straight to
        ;; simplifyFilename. Relative paths anchored against the .pvtproject
        ;; file's own directory, matching Python's `path.parent / resolved`.
        (setq firstCh (substring raw 1 1))
        (cond
          ((or (equal firstCh "/") (equal firstCh "~"))
            (setq resolved (simplifyFilename raw)))
          (t
            (setq parent (_pvtParentDir path))
            (setq resolved (simplifyFilename (strcat parent "/" raw)))))
        (pvtOk resolved))))
)

(procedure (_pvtValAuthor path obj)
  ;; author is optional. Absent OR explicit JSON null -> nil. Else must be string.
  (let ((raw (arrayref obj "author")))
    (cond
      ((pvtJsonAbsentp raw) (pvtOk nil))
      ((pvtJsonNullp   raw) (pvtOk nil))
      ((stringp        raw) (pvtOk raw))
      (t (pvtErr 'pvt_validation "'author' must be a string if set" path))))
)

(procedure (_pvtValAliases path obj)
  ;; testbench_aliases optional. Must be a JSON object string->string with no
  ;; duplicate VALUES across keys.
  (prog (raw keys out seenValues errResult v priorKey)
    (setq raw (arrayref obj "testbench_aliases"))
    (cond
      ((pvtJsonAbsentp raw)
        (return (pvtOk (makeTable 'pvtAliases nil))))
      ((not (pvtJsonObjectp raw))
        (return (pvtErr 'pvt_validation
                        "'testbench_aliases' must be a JSON object"
                        path))))
    (setq keys (pvtJsonObjectKeys raw))
    (setq out (makeTable 'pvtAliases nil))
    (setq seenValues (makeTable 'pvtAliasValues nil))
    (foreach k keys
      (cond
        ((null errResult)
          (setq v (arrayref raw k))
          (cond
            ((not (stringp k))
              (setq errResult
                    (pvtErr 'pvt_validation
                            "'testbench_aliases' keys and values must be strings"
                            path)))
            ((not (stringp v))
              (setq errResult
                    (pvtErr 'pvt_validation
                            "'testbench_aliases' keys and values must be strings"
                            path)))
            (t
              (setq priorKey (arrayref seenValues v))
              (cond
                (priorKey
                  (setq errResult
                        (pvtErr 'pvt_validation
                                (sprintf nil
                                         "duplicate alias %L (used by both %L and %L)"
                                         v priorKey k)
                                path)))
                (t
                  (setarray seenValues v k)
                  (setarray out k v))))))))
    (cond
      (errResult (return errResult))
      (t         (return (pvtOk out)))))
)

(procedure (_pvtWarnUnknownKeys path obj)
  ;; Emit one `warn` per unknown key. Keys starting with `_` are silent.
  ;; Mirrors python project.py:_warn_unknown_keys.
  (let ((keys (pvtJsonObjectKeys obj)))
    (foreach k keys
      (cond
        ((member k _PVT_KNOWN_FIELDS) nil)
        ((and (stringp k)
              (greaterp (strlen k) 0)
              (equal (substring k 1 1) "_"))
          nil)
        (t (warn "%s: unknown key %L (ignored)\n" path k)))))
  nil
)


;; ============================================================================
;; Parsing entry point
;; ============================================================================

(procedure (pvtParsePvtProject path)
  "Read and validate the .pvtproject file at `path`. Returns a pvtError
   result wrapping a populated pvtProject defstruct on success."
  (prog (absPath jsonR obj svR pjR drR auR alR)
    (setq absPath (simplifyFilename path))
    (setq jsonR (pvtJsonParseFile absPath))
    (cond
      ((pvtIsErr jsonR)
        ;; Collapse :json / :io to :validation per python's
        ;; _parse_pvtproject error wrapping.
        (return (pvtErrToValidation jsonR))))
    (setq obj (pvtUnwrap jsonR))
    (cond
      ((not (pvtJsonObjectp obj))
        (return (pvtErr 'pvt_validation
                        (sprintf nil "top-level must be a JSON object, got %L" obj)
                        absPath))))
    ;; First-error-wins. Order matches python._parse_pvtproject.
    (setq svR (_pvtValSchemaVersion absPath obj))
    (cond ((pvtIsErr svR) (return svR)))
    (setq pjR (_pvtValProject       absPath obj))
    (cond ((pvtIsErr pjR) (return pjR)))
    (setq drR (_pvtValDbRoot        absPath obj))
    (cond ((pvtIsErr drR) (return drR)))
    (setq auR (_pvtValAuthor        absPath obj))
    (cond ((pvtIsErr auR) (return auR)))
    (setq alR (_pvtValAliases       absPath obj))
    (cond ((pvtIsErr alR) (return alR)))
    (_pvtWarnUnknownKeys absPath obj)
    (return (pvtOk
              (make_pvtProject
                ?project          (pvtUnwrap pjR)
                ?dbRoot           (pvtUnwrap drR)
                ?author           (pvtUnwrap auR)
                ?testbenchAliases (pvtUnwrap alR)
                ?schemaVersion    (pvtUnwrap svR)
                ?sourcePath       absPath))))
)


;; ============================================================================
;; Lookup chain (mirrors python load_pvtproject semantics, plus the
;; SKILL-only first-save-dialog fallback per DECISIONS #6 §3).
;; ============================================================================

(procedure (pvtLoadPvtProject @key (start nil) (env nil) (allowDialog t))
  "Layered .pvtproject lookup:
     1. Explicit ?env keyword OR getShellEnvVar(PVT_ENV_VAR).
        - if set: must point at an existing file, else :err 'pvt_notFound
     2. pvtFindPvtProject(start) walker.
     3. allowDialog AND boundp('pvtProjectFirstSaveDialog) AND DISPLAY set:
        invoke dialog, recurse on its return value.
     4. else :err 'pvt_notFound."
  (prog (envVal p found dlgPath origin)
    ;; --- step 1: env var --------------------------------------------------
    (setq envVal (or env (getShellEnvVar PVT_ENV_VAR)))
    (cond
      ((and envVal (stringp envVal) (greaterp (strlen envVal) 0))
        (setq p (simplifyFilename envVal))
        (cond
          ((isFile p) (return (pvtParsePvtProject p)))
          (t
            (return (pvtErr 'pvt_notFound
                            (sprintf nil "%s=%L does not point to an existing file"
                                     PVT_ENV_VAR envVal)
                            nil))))))
    ;; --- step 2: walker ---------------------------------------------------
    (setq found (pvtFindPvtProject start))
    (cond
      (found (return (pvtParsePvtProject found))))
    ;; --- step 3: optional first-save dialog (SKILL only) ------------------
    ;; The dialog file is loaded by simkit's startup; the getd probe makes
    ;; this a no-op until that file is loaded. Classic SKILL `boundp` only
    ;; sees value-cell bindings, not procedure definitions — DECISIONS #28
    ;; trap #15. `getd` returns the function object or nil, which is the
    ;; correct presence check. Per plan risk #4: getShellEnvVar("DISPLAY")
    ;; is the headless detector because hiInBatchMode is not always defined.
    (cond
      ((and allowDialog
            (getd 'pvtProjectFirstSaveDialog)
            (getShellEnvVar "DISPLAY"))
        (setq dlgPath (pvtProjectFirstSaveDialog))
        (cond
          ((and dlgPath (stringp dlgPath) (isFile dlgPath))
            (return (pvtParsePvtProject (simplifyFilename dlgPath)))))))
    ;; --- step 4: hard fail ------------------------------------------------
    (setq origin
          (cond
            ((null start) (simplifyFilename (getWorkingDir)))
            (t            (simplifyFilename start))))
    (return (pvtErr 'pvt_notFound
                    (sprintf nil
                             "no %s found walking up from %s and %s is not set"
                             PVTPROJECT_FILENAME origin PVT_ENV_VAR)
                    nil)))
)


;; ============================================================================
;; Convenience accessor
;; ============================================================================

(procedure (pvtProjectAliasFor proj testbenchId)
  "Return the alias string for `testbenchId`, or nil if none defined."
  (cond
    ((null proj) nil)
    ((not (defstructp proj 'pvtProject)) nil)
    (t (let ((tab proj->testbenchAliases))
         (cond
           ((tablep tab) (arrayref tab testbenchId))
           (t nil)))))
)


t  ; load-time return
