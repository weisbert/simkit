;; ============================================================================
;; pvtProjectDialog.il
;;
;; SKILL-only first-save bootstrap form for .pvtproject. Loaded after
;; pvtProject.il; doing so flips (boundp 'pvtProjectFirstSaveDialog) to t,
;; which is the gate pvtLoadPvtProject step 3 keys on (along with DISPLAY).
;;
;; v1 scope (DECISIONS #28 — four-field minimum-viable):
;;   Project name (REQUIRED).
;;   DB root      (optional; blank -> "./simkit_data").
;;   Author       (optional; blank -> key omitted, collector falls back to $USER).
;;   Save target  (optional+editable; blank -> <cwd>/.pvtproject).
;; testbench_aliases checkbox deferred to v1.1.
;;
;; Design split (Tier-1 testable layers vs. Virtuoso-only UI):
;;
;;   Pure layers (no hi*/axl* calls):
;;     _pvtDlgCwdBasename       absPath -> cleaned-leaf | nil
;;     _pvtDlgNormaliseLibName  string  -> cleaned     | nil
;;     _pvtDlgDeriveDefaults    @key cwd session defaults -> alist
;;     _pvtDlgValidateProject   string  -> pvtError
;;     _pvtDlgValidateDbRoot    string  -> pvtError
;;     _pvtDlgValidateAuthor    string  -> pvtError
;;     _pvtDlgValidateSavePath  string  -> pvtError
;;     _pvtDlgBuildJsonTable    alist   -> table (with _PVT_JSON_KEYS_PROP)
;;     _pvtDlgWriteFile         path+alist -> pvtError (round-trip checked)
;;
;;   Impure (live Virtuoso only):
;;     _pvtDlgProbeSession      () -> (lib cell view) | nil
;;     _pvtDlgOnOk              form -> t
;;     pvtProjectFirstSaveDialog @key defaults savePath -> string|nil
;;
;; PDF refs (all skuiref.pdf unless tagged):
;;   hiCreateAppForm       p.502
;;   hiCreateStringField   p.650 (field access form->sym->value p.651)
;;   hiDisplayForm         p.688 (blocks until form dismissed)
;;   hiSetCallbackStatus   p.801 (keep form open on validation fail)
;;   hiFormDone            p.700 (?unmapAfterCB t + status semantics)
;;   axlGetWindowSession   p.38  (adexlSKILLref.pdf)
;;   axlGetSessionLibName  p.33  (adexlSKILLref.pdf)
;;   axlGetSessionCellName p.32  (adexlSKILLref.pdf)
;;   axlGetSessionViewName p.34  (adexlSKILLref.pdf)
;;   pcreMatchp p.186, lowerCase p.161, simplifyFilename p.465,
;;   outfile p.450, unwindProtect p.606, deleteFile p.620,
;;   isFile p.428, isDir p.631 — all sklangref.pdf.
;; ============================================================================


;; --- module-level constants -------------------------------------------------

(defvar _PVT_DLG_DEFAULT_DBROOT  "./simkit_data")
(defvar _PVT_DLG_DEFAULT_PROJECT "my_project")
(defvar _PVT_DLG_DOC
  "Created by simkit first-save dialog; edit by hand to add testbench aliases or change author.")

;; Module-level result slot. The OK callback writes the absolute path of the
;; freshly-written .pvtproject here on success; pvtProjectFirstSaveDialog
;; reads it AFTER hiDisplayForm unblocks (callback is sync per skuiref p.700).
(defvar _pvtDlgLastWrittenPath nil)


;; ============================================================================
;; Pure: normalisation helpers
;; ============================================================================

(procedure (_pvtDlgNormaliseLibName libName)
  "Project-name normaliser: lowercase + map any char outside [a-z0-9_-] to '_'.
   Returns nil if the input is empty, non-string, or contains zero alphanumeric
   characters to anchor the cleaned form.
   Ref: pcreMatchp sklangref p.186, lowerCase sklangref p.161."
  (prog (n i ch out)
    (cond
      ((null libName)              (return nil))
      ((not (stringp libName))     (return nil))
      ((zerop (strlen libName))    (return nil)))
    (setq out "")
    (setq n (strlen libName))
    (setq i 1)
    (while (leqp i n)
      (setq ch (substring libName i 1))
      (cond
        ((pcreMatchp "^[A-Z]$" ch) (setq ch (lowerCase ch))))
      (cond
        ((pcreMatchp "^[a-z0-9_-]$" ch) (setq out (strcat out ch)))
        (t                              (setq out (strcat out "_"))))
      (setq i (plus i 1)))
    (cond
      ((not (pcreMatchp "[a-z0-9]" out)) (return nil))
      (t (return out))))
)


(procedure (_pvtDlgCwdBasename absCwd)
  "Return the trailing path component of an absolute cwd, normalised through
   _pvtDlgNormaliseLibName. nil if no alphanumeric anchor survives (e.g. root,
   trailing-slash, or non-string input)."
  (prog (n i lastSlash leaf)
    (cond
      ((or (null absCwd) (not (stringp absCwd))) (return nil)))
    (setq n (strlen absCwd))
    (cond ((zerop n) (return nil)))
    (setq i 1)
    (setq lastSlash 0)
    (while (leqp i n)
      (cond
        ((equal (substring absCwd i 1) "/") (setq lastSlash i)))
      (setq i (plus i 1)))
    (cond
      ((zerop lastSlash)      (setq leaf absCwd))
      ((equal lastSlash n)    (return nil))           ; trailing slash, no leaf
      (t (setq leaf (substring absCwd (plus lastSlash 1)))))
    (return (_pvtDlgNormaliseLibName leaf)))
)


;; ============================================================================
;; Pure: defaults derivation
;; ============================================================================

(procedure (_pvtDlgDeriveDefaults @key (cwd nil) (session nil) (defaults nil))
  "Pure: derive form pre-fill values from cwd + optional session tuple +
   optional caller-supplied overrides. Returns an alist with symbol keys
   'project, 'dbRoot, 'author, 'savePath.
   `cwd'      : absolute path string (caller pipes through simplifyFilename).
   `session'  : (lib cell view) string tuple or nil.
   `defaults' : optional alist of (sym . string) overrides."
  (let ((projectDflt   nil)
        (dbRootDflt    _PVT_DLG_DEFAULT_DBROOT)
        (authorDflt    nil)
        (savePathDflt  nil)
        (libName       nil)
        (override      nil))
    ;; project: (a) lib normalised; (b) cwd basename; (c) literal fallback.
    (when (and session (listp session))
      (setq libName (car session)))
    (when (and libName (stringp libName))
      (setq projectDflt (_pvtDlgNormaliseLibName libName)))
    (when (null projectDflt)
      (setq projectDflt (_pvtDlgCwdBasename cwd)))
    (when (null projectDflt)
      (setq projectDflt _PVT_DLG_DEFAULT_PROJECT))
    ;; author: $USER or empty.
    (setq authorDflt (or (getShellEnvVar "USER") ""))
    ;; savePath: <cwd>/.pvtproject, simplified.
    (setq savePathDflt
      (cond
        ((and cwd (stringp cwd) (greaterp (strlen cwd) 0))
          (simplifyFilename (strcat cwd "/" PVTPROJECT_FILENAME)))
        (t (strcat "./" PVTPROJECT_FILENAME))))
    ;; Caller overrides.
    (when defaults
      (setq override (assoc 'project defaults))
      (when override (setq projectDflt (cadr override)))
      (setq override (assoc 'dbRoot defaults))
      (when override (setq dbRootDflt (cadr override)))
      (setq override (assoc 'author defaults))
      (when override (setq authorDflt (cadr override)))
      (setq override (assoc 'savePath defaults))
      (when override (setq savePathDflt (cadr override))))
    (list
      (list 'project   projectDflt)
      (list 'dbRoot    dbRootDflt)
      (list 'author    authorDflt)
      (list 'savePath  savePathDflt)))
)


;; ============================================================================
;; Pure: per-field validators (all return pvtError discriminated result)
;; ============================================================================

(procedure (_pvtDlgValidateProject raw)
  (cond
    ((or (null raw) (not (stringp raw)))
      (pvtErr 'pvt_dialog "Project name is required" nil))
    ((zerop (strlen raw))
      (pvtErr 'pvt_dialog "Project name is required" nil))
    ((not (pcreMatchp _PVT_PROJECT_NAME_RE raw))
      (pvtErr 'pvt_dialog
              (sprintf nil "Project name %L must match %s"
                       raw _PVT_PROJECT_NAME_RE)
              nil))
    (t (pvtOk raw)))
)


(procedure (_pvtDlgValidateDbRoot raw)
  ;; Caller substitutes the default before validation, so a blank here is a
  ;; programmer error. Path validity is the collector's job — it creates the
  ;; dir lazily on first save (_pvtCollMakeRunDir).
  (cond
    ((or (null raw) (not (stringp raw)))
      (pvtErr 'pvt_dialog "DB root must be a string" nil))
    ((zerop (strlen raw))
      (pvtErr 'pvt_dialog "DB root is empty (should have been defaulted)" nil))
    (t (pvtOk raw)))
)


(procedure (_pvtDlgValidateAuthor raw)
  ;; Blank is fine — build-table step omits the key entirely so the loader
  ;; sees it as absent (collector then falls back to $USER at dump time).
  (cond
    ((null raw)              (pvtOk ""))
    ((not (stringp raw))
      (pvtErr 'pvt_dialog "Author must be a string" nil))
    (t (pvtOk raw)))
)


(procedure (_pvtDlgValidateSavePath raw)
  ;; Path constraints: must end in .pvtproject, parent dir must exist, file
  ;; must NOT already exist (no silent overwrite — see plan §6).
  (prog (resolved parent fnameStart leaf)
    (cond
      ((or (null raw) (not (stringp raw)) (zerop (strlen raw)))
        (return (pvtErr 'pvt_dialog
                  "Save path is empty (should have been defaulted)" nil))))
    (setq resolved (simplifyFilename raw))
    ;; Filename-leaf must equal PVTPROJECT_FILENAME exactly.
    (setq fnameStart
          (plus 1 (difference (strlen resolved) (strlen PVTPROJECT_FILENAME))))
    (cond
      ((lessp fnameStart 1)
        (return (pvtErr 'pvt_dialog
                  (sprintf nil "Save path %L must end in %L"
                           resolved PVTPROJECT_FILENAME)
                  nil))))
    (setq leaf (substring resolved fnameStart))
    (cond
      ((not (equal leaf PVTPROJECT_FILENAME))
        (return (pvtErr 'pvt_dialog
                  (sprintf nil "Save path %L must end in %L"
                           resolved PVTPROJECT_FILENAME)
                  nil))))
    ;; Refuse to overwrite an existing file.
    (cond
      ((isFile resolved)
        (return (pvtErr 'pvt_dialog
                  (sprintf nil "%L already exists — refusing to overwrite"
                           resolved)
                  nil))))
    ;; Parent dir must exist.
    (setq parent (_pvtParentDir resolved))
    (cond
      ((or (null parent) (not (isDir parent)))
        (return (pvtErr 'pvt_dialog
                  (sprintf nil "Parent directory %L does not exist" parent)
                  nil))))
    (return (pvtOk resolved)))
)


;; ============================================================================
;; Pure: JSON-table assembly (sidecar key list = emit order)
;; ============================================================================

(procedure (_pvtDlgBuildJsonTable vals)
  "Build a JSON-emitter-ready table for the .pvtproject file. `vals' is an
   alist with required keys 'project (string), 'dbRoot (string), and optional
   'author (string; blank or nil omits the key entirely). schema_version
   is always 1; _doc is the static helper string.
   Mirrors the sidecar-key convention used by _pvtCollAutoCapture."
  (let ((tab    (makeTable 'pvtProjectFile PVT_JSON_NULL))
        (keys   (list "_doc" "project" "dbRoot"))
        (proj   (cadr (assoc 'project vals)))
        (dbRoot (cadr (assoc 'dbRoot  vals)))
        (author (cadr (assoc 'author  vals))))
    (setarray tab "_doc"           _PVT_DLG_DOC)
    (setarray tab "project"        proj)
    (setarray tab "dbRoot"         dbRoot)
    (when (and author (stringp author) (greaterp (strlen author) 0))
      (setarray tab "author"  author)
      (setq keys (append keys (list "author"))))
    (setarray tab "schema_version" 1)
    (setq keys (append keys (list "schema_version")))
    (setarray tab _PVT_JSON_KEYS_PROP keys)
    tab)
)


;; ============================================================================
;; File write — error-cleanup + round-trip self-check
;; ============================================================================

(procedure (_pvtDlgWriteFile path vals)
  "Build the JSON table from `vals` and write it to `path`. After write,
   round-trip via pvtParsePvtProject. On parser failure OR write error,
   delete the partial file and return :err. Returns pvtOk(absPath) on success.

   Cleanup behaviour matters: a corrupt .pvtproject would break the loader
   the very next session, so we'd rather erase the partial file and have
   the user re-trigger the dialog (or hand-write the file) than leave an
   invalid one behind.

   Ref: outfile sklangref p.450, unwindProtect p.606, deleteFile p.620."
  (prog (tab port absPath wrR parseR)
    (setq absPath (simplifyFilename path))
    (setq tab     (_pvtDlgBuildJsonTable vals))
    ;; Open output port. errset wraps so a failed open surfaces as :pvt_dialog.
    (setq port (errset (outfile absPath "w") nil))
    (setq port (cond ((listp port) (car port)) (t nil)))
    (cond
      ((null port)
        (return (pvtErr 'pvt_dialog
                  (sprintf nil "cannot open %L for writing" absPath)
                  absPath))))
    (setq wrR (errset
                (unwindProtect
                  (pvtJsonEmitToPort tab port)
                  (close port))
                t))
    (cond
      ((null wrR)
        ;; Emitter raised mid-stream; remove partial output.
        (errset (deleteFile absPath) nil)
        (return (pvtErr 'pvt_dialog
                  (sprintf nil "JSON emit failed for %L (partial file removed)"
                           absPath)
                  absPath))))
    ;; Round-trip self-check via the loader (same parser as ingest path).
    (setq parseR (pvtParsePvtProject absPath))
    (cond
      ((pvtIsErr parseR)
        (errset (deleteFile absPath) nil)
        (return (pvtErr 'pvt_dialog
                  (sprintf nil "round-trip parse rejected just-written %L: %s"
                           absPath (pvtErrMessage parseR))
                  absPath))))
    (return (pvtOk absPath)))
)


;; ============================================================================
;; Impure: ADE-XL session probe (best-effort)
;; ============================================================================

(procedure (_pvtDlgProbeSession)
  "Best-effort probe of the active ADE-XL session for default field values.
   Returns (lib cell view) on success, nil if no session or any axl* call
   fails. Wrapped in errset so calling from a non-Maestro window is silent
   (plan Risk #9).
   Ref: axlGetWindowSession adexlSKILLref p.38;
        axlGetSession{Lib,Cell,View}Name p.32-34."
  (prog (sess lib cell view)
    (setq sess (errset (axlGetWindowSession) nil))
    (setq sess (cond ((listp sess) (car sess)) (t nil)))
    (cond ((null sess) (return nil)))
    (setq lib  (errset (axlGetSessionLibName  sess) nil))
    (setq lib  (cond ((listp lib)  (car lib))  (t nil)))
    (setq cell (errset (axlGetSessionCellName sess) nil))
    (setq cell (cond ((listp cell) (car cell)) (t nil)))
    (setq view (errset (axlGetSessionViewName sess) nil))
    (setq view (cond ((listp view) (car view)) (t nil)))
    (cond
      ((and (stringp lib) (stringp cell) (stringp view))
        (return (list lib cell view)))
      (t (return nil))))
)


;; ============================================================================
;; UI: OK callback (sync inside hiDisplayForm per skuiref p.700)
;; ============================================================================

(procedure (_pvtDlgOnOk form)
  "OK callback. Reads field values from `form`, applies blank-to-default
   substitution for optional fields, runs validators, writes the file. On
   any failure: warn to CIW + (hiSetCallbackStatus form nil) -> form stays
   open. On success: stash the path in _pvtDlgLastWrittenPath; the default
   callback status (t) lets the form unmap.
   Ref: hiSetCallbackStatus skuiref p.801 (requires ?unmapAfterCB t p.506);
        field access form->sym->value p.651."
  (prog (proj dbRoot author savePath vals wrR pjR drR auR spR)
    (setq proj     form->pvtDlgProject->value)
    (setq dbRoot   form->pvtDlgDbRoot->value)
    (setq author   form->pvtDlgAuthor->value)
    (setq savePath form->pvtDlgSavePath->value)
    ;; Blank optional fields collapse to their defaults BEFORE validation.
    (when (and (stringp dbRoot) (zerop (strlen dbRoot)))
      (setq dbRoot _PVT_DLG_DEFAULT_DBROOT))
    (when (and (stringp savePath) (zerop (strlen savePath)))
      (setq savePath (simplifyFilename
                       (strcat (getWorkingDir) "/" PVTPROJECT_FILENAME))))
    ;; Validate.
    (setq pjR (_pvtDlgValidateProject  proj))
    (cond ((pvtIsErr pjR)
            (warn "pvt: %s\n" (pvtErrMessage pjR))
            (hiSetCallbackStatus form nil)
            (return t)))
    (setq drR (_pvtDlgValidateDbRoot   dbRoot))
    (cond ((pvtIsErr drR)
            (warn "pvt: %s\n" (pvtErrMessage drR))
            (hiSetCallbackStatus form nil)
            (return t)))
    (setq auR (_pvtDlgValidateAuthor   author))
    (cond ((pvtIsErr auR)
            (warn "pvt: %s\n" (pvtErrMessage auR))
            (hiSetCallbackStatus form nil)
            (return t)))
    (setq spR (_pvtDlgValidateSavePath savePath))
    (cond ((pvtIsErr spR)
            (warn "pvt: %s\n" (pvtErrMessage spR))
            (hiSetCallbackStatus form nil)
            (return t)))
    ;; Write the file (incl. round-trip self-check).
    (setq vals (list
                 (list 'project (pvtUnwrap pjR))
                 (list 'dbRoot  (pvtUnwrap drR))
                 (list 'author  (pvtUnwrap auR))))
    (setq wrR (_pvtDlgWriteFile (pvtUnwrap spR) vals))
    (cond
      ((pvtIsErr wrR)
        (warn "pvt: %s\n" (pvtErrMessage wrR))
        (hiSetCallbackStatus form nil)
        (return t))
      (t
        (setq _pvtDlgLastWrittenPath (pvtUnwrap wrR))
        (return t))))
)


;; ============================================================================
;; UI: public entry point
;; ============================================================================

(procedure (pvtProjectFirstSaveDialog @key (defaults nil) (savePath nil))
  "Modal first-save bootstrap form. Returns the absolute path of the freshly-
   written .pvtproject on Save, or nil on Cancel / validation give-up.

   The DISPLAY + boundp gate is handled by pvtLoadPvtProject step 3 — by the
   time this is invoked, we already know we have a display.

   Ref: hiCreateAppForm skuiref p.502, hiDisplayForm p.688,
        hiSetCallbackStatus p.801 (paired with ?unmapAfterCB t)."
  (prog (cwd sess derived dfltSavePath fProj fDb fAu fSp form)
    ;; Defensive reset of the module-level result slot.
    (setq _pvtDlgLastWrittenPath nil)
    (setq cwd (simplifyFilename (getWorkingDir)))
    (setq sess (_pvtDlgProbeSession))
    (setq derived (_pvtDlgDeriveDefaults
                    ?cwd cwd ?session sess ?defaults defaults))
    (setq dfltSavePath
      (cond
        ((and savePath (stringp savePath)) savePath)
        (t (cadr (assoc 'savePath derived)))))
    ;; Field handles. Names are bare symbols (not _PVT_DLG_FIELD_* constants)
    ;; so the callback can use the form->name->value accessor syntax directly.
    (setq fProj (hiCreateStringField
                  ?name   'pvtDlgProject
                  ?prompt "Project name (required)"
                  ?value  (cadr (assoc 'project derived))))
    (setq fDb   (hiCreateStringField
                  ?name   'pvtDlgDbRoot
                  ?prompt "DB root (blank = ./simkit_data)"
                  ?value  (cadr (assoc 'dbRoot derived))))
    (setq fAu   (hiCreateStringField
                  ?name   'pvtDlgAuthor
                  ?prompt "Author (blank omitted)"
                  ?value  (cadr (assoc 'author derived))))
    (setq fSp   (hiCreateStringField
                  ?name   'pvtDlgSavePath
                  ?prompt "Save .pvtproject to"
                  ?value  dfltSavePath))
    (setq form
          (hiCreateAppForm
            ?name          'pvtProjectFirstSaveDialogForm
            ?formTitle     "Create new .pvtproject"
            ?fields        (list fProj fDb fAu fSp)
            ?callback      '_pvtDlgOnOk
            ?dialogStyle   'modal
            ?buttonLayout  'OKCancel
            ?unmapAfterCB  t))
    ;; hiDisplayForm blocks until form is dismissed. Even on OK, the callback
    ;; may have aborted on validation failure — _pvtDlgLastWrittenPath stays
    ;; nil in that case, which is the correct "no file written" signal.
    (hiDisplayForm form)
    (return _pvtDlgLastWrittenPath))
)


t  ; load-time return value
