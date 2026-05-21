;; ============================================================================
;; pvtCorners.il
;;
;; Phase 2 §3 SKILL bridge between live ADE-XL setup and `.union.json` sidecar
;; files. Implements `pvtCornersPull` (Maestro → JSON) for v1; `pvtCornersPush`
;; is symmetric and lands as a follow-up.
;;
;; Public API:
;;   pvtCornersPull(sess outPath [?unionName])
;;     -> (pvtOk t_absJsonPath) | pvtErr
;;
;; SKILL note: every procedure that uses `(return ...)` for early exit MUST
;; have its body wrapped in `(prog () ...)`, because in classic SKILL `return`
;; only escapes the enclosing `prog` form (sklangref.pdf p.379), not the
;; surrounding `procedure`. We follow the "every procedure body is a prog
;; block" convention from pvtJson.il / pvtProject.il / pvtCollect.il.
;;
;; Reuse:
;;   - pvtError.il:    pvtOk / pvtErr / pvtIsOk / pvtIsErr / pvtUnwrap
;;   - pvtJson.il:     pvtJsonEmitToPort / PVT_JSON_NULL /
;;                     PVT_JSON_EMPTY_OBJECT / _PVT_JSON_KEYS_PROP
;;   - pvtProject.il:  pvtLoadPvtProject / proj->project accessor
;;
;; Maestro data-model recovery (DECISIONS #29):
;;   - Corners have two parallel axes — vars + models — both sweep-encoded
;;     as space-separated strings. Model sections additionally `"..."`-wrapped.
;;   - axlGetCorners(sdb) returns (handle (cname1 cname2 ...)).
;;   - axlGetVars(corner)  returns (handle (vname1 vname2 ...)).
;;   - axlGetModels(corner) returns (handle (file1 file2 ...)).
;;   - axlGetModel(corner fileBasename) returns a model handle.
;;   - axlGetModel{File,Section,Block,Test}(modelHandle) return strings.
;;
;; Sweep value scalar/array policy at the SKILL layer:
;;   - axlGetVarValue returns a single string (e.g. "55" or "3 2.8") with no
;;     distinction between "scalar" and "single-element sweep". This loader
;;     emits JSON string for one whitespace-separated token, JSON array for
;;     2+. Sidecar length-1 arrays therefore collapse to scalar on a
;;     push -> pull cycle; documented as fidelity-modulus item (iv) in
;;     spec §4.2.
;;
;; Module map:
;;   pvtCornersPull
;;     ├── _pvtCornersResolveSession
;;     ├── _pvtCornersResolveProject     (via pvtLoadPvtProject)
;;     ├── _pvtCornersBuildUnionTable
;;     │     └── _pvtCornersReadRow
;;     │           ├── _pvtCornersReadVars
;;     │           │     ├── _pvtCornersParseVarValue
;;     │           │     └── _pvtCornersValueToJsonField
;;     │           └── _pvtCornersReadModels
;;     │                 ├── _pvtCornersParseSectionValue
;;     │                 └── _pvtCornersValueToJsonField
;;     └── _pvtCornersWriteOutput
;; ============================================================================


;; ----------------------------------------------------------------------------
;; Pure helpers — Tier-1 testable, no axl* / hi* dependency
;; ----------------------------------------------------------------------------

(defvar _PVT_CORNERS_SCHEMA_VERSION 1)
(defvar _PVT_CORNERS_FILE_SUFFIX ".union.json")
(defvar _PVT_CORNERS_DEFAULT_MODEL_BLOCK "Global")
(defvar _PVT_CORNERS_DEFAULT_MODEL_TEST  "All")


;; _pvtCornersIsSpace : t iff ch is the single-character string " " or "\t".
;; Classic SKILL has no whitespace test primitive; Maestro var values only
;; use ASCII space as the separator on this code path.
;; Ref: sklangref.pdf p.223 (string indexing).
(procedure (_pvtCornersIsSpace ch)
  (or (equal ch " ") (equal ch "\t"))
)


;; _pvtCornersSplitWhitespace : split a string on runs of ASCII space / tab.
;; Empty / nil input -> ().
(procedure (_pvtCornersSplitWhitespace s)
  (prog (tokens i n start ch)
    (cond
      ((null s)         (return nil))
      ((not (stringp s)) (return nil))
      ((equal s "")     (return nil)))
    (setq n (strlen s))
    (setq i 1)
    (setq start nil)
    (setq tokens nil)
    (while (leqp i n)
      (setq ch (substring s i 1))
      (cond
        ((_pvtCornersIsSpace ch)
          (cond
            (start
              (setq tokens (cons (substring s start (difference i start)) tokens))
              (setq start nil))))
        (t
          (cond
            ((null start) (setq start i)))))
      (setq i (plus i 1)))
    (cond
      (start
        (setq tokens (cons (substring s start (difference (plus n 1) start)) tokens))))
    (return (reverse tokens))
  )
)


(procedure (_pvtCornersStripQuotes s)
  "Strip exactly one matched pair of double quotes from `s`. If `s` is not
   doubly-quoted, return it unchanged. Used for unwrapping Maestro model-
   section storage (`\"tt\"` -> `tt`)."
  (prog (n)
    (cond
      ((null s) (return s))
      ((not (stringp s)) (return s)))
    (setq n (strlen s))
    (cond
      ((lessp n 2) (return s))
      ((and (equal (substring s 1 1) "\"")
            (equal (substring s n 1) "\""))
        (return (substring s 2 (difference n 2)))))
    (return s)
  )
)


(procedure (_pvtCornersParseVarValue raw)
  "Parse a Maestro var-value string (vars axis). Returns a list of token
   strings. Single-token => 1-element list; multi-token (sweep) => N-element."
  (_pvtCornersSplitWhitespace raw)
)


(procedure (_pvtCornersParseSectionValue raw)
  "Parse a Maestro model-section string (models axis). Splits on whitespace
   THEN strips one pair of `\"...\"` from each token. `'\"tt\" \"ss\"'` ->
   (\"tt\" \"ss\")."
  (mapcar (lambda (tok) (_pvtCornersStripQuotes tok))
          (_pvtCornersSplitWhitespace raw))
)


(procedure (_pvtCornersValueToJsonField tokens)
  "Map a non-empty token list to either a JSON string (length 1) or a JSON
   array (length >=2). Empty input is treated as nil — the caller already
   filtered out empty values."
  (cond
    ((null tokens) nil)
    ((null (cdr tokens)) (car tokens))      ; one element -> scalar string
    (t tokens)                              ; >=2 -> SKILL list = JSON array
  )
)


(procedure (_pvtCornersTestScope allTests disabledTests)
  "Return allTests with disabledTests removed, preserving allTests order.
   nil disabledTests => nil so the caller omits the 'tests' key (= every
   test). Ref: axlGetCornerDisabledTests adexlSKILLref.pdf p.319."
  (prog (acc)
    (cond
      ((null disabledTests) (return nil)))
    (setq acc nil)
    (foreach tn allTests
      (cond
        ((null (member tn disabledTests)) (setq acc (cons tn acc)))))
    (return (reverse acc))
  )
)


;; ---------- push-side encoders (inverse of the parsers above) -------------

(procedure (_pvtCornersJoinWithSpace tokens)
  ;; Join a list of token strings with single ASCII spaces. Used to build
  ;; the axlPutVar value for a sweep var. ("3" "2.8") -> "3 2.8".
  (prog (acc first)
    (setq acc "")
    (setq first t)
    (foreach tok tokens
      (cond
        (first
          (setq acc tok)
          (setq first nil))
        (t
          (setq acc (strcat acc " " tok)))))
    (return acc)
  )
)


(procedure (_pvtCornersFormatVarValueFromJson raw)
  ;; Inverse of _pvtCornersParseVarValue. Accepts either a SKILL string
  ;; (scalar) or a list of strings (sweep, from JSON array). Returns the
  ;; space-separated string that axlPutVar expects. nil -> nil (no-op
  ;; signal for the caller).
  (cond
    ((null raw) nil)
    ((stringp raw) raw)
    ((listp raw)   (_pvtCornersJoinWithSpace raw))
    (t nil)
  )
)


(procedure (_pvtCornersFormatSectionValueFromJson raw)
  ;; Inverse of _pvtCornersParseSectionValue. Each section name is wrapped
  ;; in `"..."` per Maestro storage convention; sweep entries separated by
  ;; single spaces. Scalar "tt" -> '"tt"'. Sweep ("tt" "ss") -> '"tt" "ss"'.
  (cond
    ((null raw) nil)
    ((stringp raw) (strcat "\"" raw "\""))
    ((listp raw)
      (_pvtCornersJoinWithSpace
        (mapcar (lambda (tok) (strcat "\"" tok "\"")) raw)))
    (t nil)
  )
)


(procedure (_pvtCornersUnionBasename path)
  "Filename basename sans `.union.json`. Returns nil if `path` does not end
   in the required suffix — caller emits the spec §3.1 error."
  (prog (base sfx tail)
    (cond
      ((null path) (return nil))
      ((not (stringp path)) (return nil)))
    ;; rindex(s ch) returns the suffix substring beginning at the last
    ;; occurrence of `ch` (skill string func, sklangref p.213) — NOT a
    ;; position. So "/a/b/c.json" -> "/c.json", and we drop the leading
    ;; separator with substring(tail, 2).
    (setq tail (rindex path "/"))
    (setq base
      (cond
        (tail (substring tail 2))
        (t    path)))
    (setq sfx _PVT_CORNERS_FILE_SUFFIX)
    (cond
      ((leqp (strlen base) (strlen sfx)) (return nil)))
    (cond
      ((equal (substring base (plus (difference (strlen base) (strlen sfx)) 1) (strlen sfx))
              sfx)
        (return (substring base 1 (difference (strlen base) (strlen sfx))))))
    (return nil)
  )
)


;; ----------------------------------------------------------------------------
;; Live-session walkers — call axl* and require an open ADE-XL session
;; ----------------------------------------------------------------------------

(procedure (_pvtCornersResolveSession sess)
  "If `sess` is nil, use the current Maestro window's session. Otherwise pass
   through. Returns (pvtOk sessId) | pvtErr."
  ;; Ref: axlGetWindowSession adexlSKILLref.pdf p.151.
  (prog (resolved win)
    (cond
      (sess
        (return (pvtOk sess))))
    (setq win (errset (hiGetCurrentWindow) nil))
    (cond
      ((or (null win) (null (car win)))
        (return (pvtErr 'pvt_validation
                  "no current Cadence window — pass an explicit session"
                  nil))))
    (setq resolved (errset (axlGetWindowSession (car win)) nil))
    (cond
      ((or (null resolved) (null (car resolved)))
        (return (pvtErr 'pvt_validation
                  "axlGetWindowSession returned nil — open Maestro on the testbench"
                  nil))))
    (return (pvtOk (car resolved)))
  )
)


(procedure (_pvtCornersReadVars cornerHandle)
  "Read all vars + values on a corner. Returns an alist ((vname tokens) ...)
   in the order Maestro lists them via axlGetVars. `tokens` is a list of
   strings (length >= 1 by construction)."
  ;; Ref: axlGetVars adexlSKILLref.pdf p.175, axlGetVar p.173,
  ;;      axlGetVarValue p.177.
  (prog (raw names alist vh val tokens)
    (setq raw (errset (axlGetVars cornerHandle) nil))
    (cond
      ((null raw) (return nil)))
    (setq names (cadr (car raw)))
    (cond
      ((null names) (return nil)))
    (setq alist nil)
    (foreach name names
      (setq vh (errset (axlGetVar cornerHandle name) nil))
      (cond
        ((or (null vh) (null (car vh))) nil)
        (t
          (setq val (errset (axlGetVarValue (car vh)) nil))
          (cond
            ((or (null val) (null (car val))) nil)
            (t
              (setq tokens (_pvtCornersParseVarValue (car val)))
              (cond
                (tokens
                  (setq alist (cons (list name tokens) alist)))))))))
    (return (reverse alist))
  )
)


(procedure (_pvtCornersReadModels cornerHandle)
  "Read all models on a corner. Returns a list of model alists; each entry is
   ((file F) (_file_abs A) (block B) (test T) (section TOKENS)). `TOKENS` is a
   list of unwrapped section names (length >= 1). `_file_abs` is the
   absolute model file path (informational — `_`-prefixed per spec §4.2 so
   it does not participate in round-trip; used by `pvt corners build` to
   emit Maestro-importable CSV)."
  ;; Ref: axlGetModels p.215, axlGetModel p.203, axlGetModelFile p.205,
  ;;      axlGetModelSection p.211, axlGetModelBlock p.204, axlGetModelTest p.213.
  (prog (raw files out mh file fileAbs block test secRaw secTokens)
    (setq raw (errset (axlGetModels cornerHandle) nil))
    (cond
      ((null raw) (return nil)))
    (setq files (cadr (car raw)))
    (cond
      ((null files) (return nil)))
    (setq out nil)
    (foreach fname files
      (setq mh (errset (axlGetModel cornerHandle fname) nil))
      (cond
        ((or (null mh) (null (car mh)) (equal (car mh) 0)) nil)
        (t
          ;; file: prefer the basename Maestro stored on the corner (already
          ;; what axlGetModels returned). axlGetModelFile returns the
          ;; absolute path which is environment-specific and not what we
          ;; round-trip; capture it separately as `_file_abs` for CSV build.
          (setq file    fname)
          (setq fileAbs (car (errset (axlGetModelFile (car mh)) nil)))
          (setq block (or (car (errset (axlGetModelBlock (car mh)) nil))
                          _PVT_CORNERS_DEFAULT_MODEL_BLOCK))
          (setq test  (or (car (errset (axlGetModelTest  (car mh)) nil))
                          _PVT_CORNERS_DEFAULT_MODEL_TEST))
          (setq secRaw (car (errset (axlGetModelSection (car mh)) nil)))
          (setq secTokens (_pvtCornersParseSectionValue secRaw))
          (cond
            (secTokens
              (setq out
                (cons (list (list "file"      file)
                            (list "_file_abs" (or fileAbs file))
                            (list "block"     block)
                            (list "test"      test)
                            (list "section"   secTokens))
                      out)))))))
    (return (reverse out))
  )
)


(procedure (_pvtCornersAlistToObj alist tableName)
  "Convert a SKILL alist ((k v) (k v) ...) into a pvtJson object preserving
   key order. Mirrors _pvtCollAssocToTable in pvtCollect.il but inlined here
   to avoid cross-module dependency on private helpers."
  (prog (tab keyOrder)
    (cond
      ((null alist) (return PVT_JSON_EMPTY_OBJECT)))
    (setq tab (makeTable tableName PVT_JSON_NULL))
    (setq keyOrder nil)
    (foreach pair alist
      (cond
        ((and (listp pair) (stringp (car pair)))
          (setarray tab (car pair) (cadr pair))
          (setq keyOrder (cons (car pair) keyOrder)))))
    (setarray tab _PVT_JSON_KEYS_PROP (reverse keyOrder))
    (return tab)
  )
)


(procedure (_pvtCornersReadRow sdb cornerName allTests)
  "Build the JSON object for one corner row. Returns a pvtJson-emit-ready
   table with keys row_name / enabled / vars / models / tests, or nil if the
   corner can't be resolved or has no var/model content. `allTests` is the
   list of every test name (used to invert the disabled-test list)."
  ;; Ref: axlGetCorner adexlSKILLref.pdf p.315.
  (prog (ch varsAlist modelsList varsObj modelsArr rowObj
         jsonVarsAlist jsonModelsList enabled disabledRaw scopedTests)
    (setq ch (errset (axlGetCorner sdb cornerName) nil))
    (cond
      ((or (null ch) (null (car ch))) (return nil)))
    (setq varsAlist   (_pvtCornersReadVars   (car ch)))
    (setq modelsList  (_pvtCornersReadModels (car ch)))
    (cond
      ((and (null varsAlist) (null modelsList))
        ;; Defensive: spec §3.3 disallows fully-empty rows. Skip rather than
        ;; emit a sidecar that would fail load_union.
        (return nil)))

    ;; Map var alist into JSON-shaped values (scalar or array).
    (setq jsonVarsAlist
      (mapcar (lambda (pair)
                (list (car pair)
                      (_pvtCornersValueToJsonField (cadr pair))))
              varsAlist))
    (setq varsObj
      (cond
        (jsonVarsAlist (_pvtCornersAlistToObj jsonVarsAlist 'pvtUnionVars))
        (t             PVT_JSON_EMPTY_OBJECT)))

    ;; Map each model entry into a JSON object with normalised section.
    (setq jsonModelsList
      (mapcar (lambda (m)
                (_pvtCornersAlistToObj
                  (list (assoc "file"      m)
                        (assoc "_file_abs" m)
                        (assoc "block"     m)
                        (assoc "test"      m)
                        (list  "section"
                               (_pvtCornersValueToJsonField
                                 (cadr (assoc "section" m)))))
                  'pvtUnionModel))
              modelsList))
    (setq modelsArr (or jsonModelsList PVT_JSON_EMPTY_ARRAY))

    ;; NEW (2026-05-13): capture per-corner enabled flag for CSV build path.
    ;; axlGetEnabled returns t/nil; map to PVT_JSON_TRUE/PVT_JSON_FALSE so the
    ;; emitter writes JSON booleans, not the SKILL `t`/nil that pvtJson would
    ;; otherwise stringify as "true"/empty.
    (setq enabled (car (errset (axlGetEnabled (car ch)) nil)))

    ;; NEW (2026 UX): per-corner Test scope. Cadence stores the DISABLED
    ;; tests; the union carries the ENABLED list, omitted entirely when the
    ;; corner runs every test. Ref: axlGetCornerDisabledTests p.319.
    (setq disabledRaw
      (car (errset (axlGetCornerDisabledTests (car ch)) nil)))
    (setq scopedTests (_pvtCornersTestScope allTests (cadr disabledRaw)))

    (setq rowObj (makeTable 'pvtUnionRow PVT_JSON_NULL))
    (setarray rowObj "row_name" cornerName)
    (setarray rowObj "enabled"
      (cond (enabled PVT_JSON_TRUE) (t PVT_JSON_FALSE)))
    (setarray rowObj "vars"     varsObj)
    (setarray rowObj "models"   modelsArr)
    (cond
      (scopedTests (setarray rowObj "tests" scopedTests)))
    (setarray rowObj _PVT_JSON_KEYS_PROP
      (append (list "row_name" "enabled" "vars" "models")
              (cond (scopedTests (list "tests")) (t nil))))
    (return rowObj)
  )
)


(procedure (_pvtCornersBuildUnionTable sess sdb unionName projectName)
  "Build the top-level union JSON table per spec §3.2. Returns a pvtJson
   table or nil if `axlGetCorners` is empty / fails."
  (prog (raw cnames rows row tbid lib cell view envelope keyOrder allTests)
    (setq raw (errset (axlGetCorners sdb) nil))
    (cond
      ((or (null raw) (null (car raw)))
        (return nil)))
    (setq cnames (cadr (car raw)))
    (cond
      ((null cnames) (return nil)))

    ;; The global test list — every row inverts its disabled-test set
    ;; against this. Ref: axlGetTests adexlSKILLref.pdf p.282.
    (setq allTests (cadr (car (errset (axlGetTests sdb) nil))))

    (setq rows nil)
    (foreach cname cnames
      (setq row (_pvtCornersReadRow sdb cname allTests))
      (cond
        (row (setq rows (cons row rows)))))
    (setq rows (reverse rows))
    (cond
      ((null rows) (return nil)))

    ;; testbench_id := lib/cell/view from the session.
    ;; Ref: axlGetSessionLibName / CellName / ViewName p.135 / p.137 / p.138.
    (setq lib  (car (errset (axlGetSessionLibName  sess) nil)))
    (setq cell (car (errset (axlGetSessionCellName sess) nil)))
    (setq view (car (errset (axlGetSessionViewName sess) nil)))
    (setq tbid
      (cond
        ((and lib cell view) (strcat lib "/" cell "/" view))
        (t "")))

    (setq envelope (makeTable 'pvtUnionEnvelope PVT_JSON_NULL))
    (setarray envelope "union_schema_version" _PVT_CORNERS_SCHEMA_VERSION)
    (setarray envelope "name"                 unionName)
    (setarray envelope "project"              projectName)
    (setarray envelope "testbench_id"         tbid)
    (setarray envelope "rows"                 rows)
    (setq keyOrder (list "union_schema_version" "name" "project"
                         "testbench_id" "rows"))
    (setarray envelope _PVT_JSON_KEYS_PROP keyOrder)
    (return envelope)
  )
)


(procedure (_pvtCornersWriteOutput obj outPath)
  "Emit a pvtJson-shaped object to `outPath`. Returns (pvtOk absPath) | pvtErr."
  (prog (port)
    (setq port (errset (outfile outPath) nil))
    (cond
      ((or (null port) (null (car port)))
        (return (pvtErr 'pvt_io
                  (strcat "cannot open for write: " outPath)
                  outPath))))
    (pvtJsonEmitToPort obj (car port))
    (fprintf (car port) "\n")
    (close (car port))
    (return (pvtOk outPath))
  )
)


;; ----------------------------------------------------------------------------
;; Public entry point
;; ----------------------------------------------------------------------------

(procedure (pvtCornersPull @key (sess nil) (outPath nil) (unionName nil))
  "Pull the live ADE-XL setup's corner table into a `.union.json` sidecar.
   - `sess`     : Maestro session id (string). nil => infer from current window.
   - `outPath`  : where to write. Filename basename must end `.union.json`
                  and equal the union's `name`.
   - `unionName`: optional override; defaults to filename basename.

   Returns (pvtOk absPath) on success or pvtErr (validation / io / notFound)."
  (prog (sessResult resolvedSess sdb projResult projObj projName
         nameFromPath finalName unionObj)
    (cond
      ((null outPath)
        (return (pvtErr 'pvt_validation
                  "pvtCornersPull: ?outPath is required" nil))))

    (setq nameFromPath (_pvtCornersUnionBasename outPath))
    (cond
      ((null nameFromPath)
        (return (pvtErr 'pvt_validation
                  (strcat "outPath basename must end '" _PVT_CORNERS_FILE_SUFFIX
                          "': " outPath)
                  outPath))))
    (setq finalName (or unionName nameFromPath))

    (setq sessResult (_pvtCornersResolveSession sess))
    (cond
      ((pvtIsErr sessResult) (return sessResult)))
    (setq resolvedSess (pvtUnwrap sessResult))

    (setq sdb (car (errset (axlGetMainSetupDB resolvedSess) nil)))
    (cond
      ((null sdb)
        (return (pvtErr 'pvt_validation
                  "axlGetMainSetupDB returned nil for the resolved session"
                  nil))))

    ;; project name from .pvtproject; surface load errors as-is.
    (setq projResult (pvtLoadPvtProject ?allowDialog nil))
    (cond
      ((pvtIsErr projResult) (return projResult)))
    (setq projObj (pvtUnwrap projResult))
    (setq projName (projObj->project))

    (setq unionObj
      (_pvtCornersBuildUnionTable resolvedSess sdb finalName projName))
    (cond
      ((null unionObj)
        (return (pvtErr 'pvt_validation
                  "no corners found in the active setup — nothing to pull"
                  nil))))

    (return (_pvtCornersWriteOutput unionObj outPath))
  )
)


;; ============================================================================
;; pvtCornersPush — write a parsed union JSON into the live ADE-XL setup
;;
;; Walks rows; per row, calls axlPutCorner then drives the vars + models axes
;; via axlPutVar + axlPutModel + axlSetModel{Section,Block,Test}. Returns
;; (pvtOk t_unionName) on success or a pvtErr (pvt_io / pvt_validation).
;;
;; NOT idempotent on session state: even pushing identical content can mark
;; Maestro's setup dirty. Callers should use a sandbox session per spec
;; §6 Gate U1 guidance.
;; ============================================================================

(procedure (_pvtCornersExtractJsonField tab key)
  ;; Pull a key from a parsed JSON object table. Returns the value, or
  ;; PVT_JSON_ABSENT when key is absent. Wrapper around arrayref that
  ;; doesn't depend on private pvtJson internals.
  (let ((v (arrayref tab key)))
    (cond
      ((equal v PVT_JSON_ABSENT) PVT_JSON_ABSENT)
      (t v))
  )
)


(procedure (_pvtCornersPushRow sdb rowObj allTests)
  ;; Push one parsed JSON row table into the SDB. Returns (pvtOk t) on
  ;; success or pvtErr on failure. `allTests` is every test name — used to
  ;; apply the row's per-corner Test scope.
  (prog (rowName varsObj modelsList ch varKeys mh
         mFile mFileAbs mBlock mTest mSection)
    (setq rowName (_pvtCornersExtractJsonField rowObj "row_name"))
    (cond
      ((or (null rowName) (not (stringp rowName)))
        (return (pvtErr 'pvt_validation
                  "row missing 'row_name' string" nil))))

    (setq ch (errset (axlPutCorner sdb rowName) nil))
    (cond
      ((or (null ch) (null (car ch)))
        (return (pvtErr 'pvt_io
                  (strcat "axlPutCorner failed for " rowName) nil))))
    (setq ch (car ch))

    ;; --- vars axis ---------------------------------------------------------
    (setq varsObj (_pvtCornersExtractJsonField rowObj "vars"))
    (cond
      ((or (equal varsObj PVT_JSON_ABSENT)
           (equal varsObj PVT_JSON_EMPTY_OBJECT)) nil)
      ((tablep varsObj)
        (setq varKeys (arrayref varsObj _PVT_JSON_KEYS_PROP))
        (foreach vname varKeys
          (let ((raw (arrayref varsObj vname)))
            (let ((s (_pvtCornersFormatVarValueFromJson raw)))
              (cond
                (s (errset (axlPutVar ch vname s) nil))))))))

    ;; --- models axis -------------------------------------------------------
    (setq modelsList (_pvtCornersExtractJsonField rowObj "models"))
    (cond
      ((or (equal modelsList PVT_JSON_ABSENT)
           (equal modelsList PVT_JSON_EMPTY_ARRAY)) nil)
      ((listp modelsList)
        (foreach mEntry modelsList
          (cond
            ((tablep mEntry)
              (setq mFile    (_pvtCornersExtractJsonField mEntry "file"))
              (setq mFileAbs (_pvtCornersExtractJsonField mEntry "_file_abs"))
              (setq mBlock   (_pvtCornersExtractJsonField mEntry "block"))
              (setq mTest    (_pvtCornersExtractJsonField mEntry "test"))
              (setq mSection (_pvtCornersExtractJsonField mEntry "section"))
              (cond
                ((and (stringp mFile)
                      (not (equal mFile PVT_JSON_ABSENT)))
                  (setq mh (errset (axlPutModel ch mFile) nil))
                  (cond
                    ((and mh (car mh) (not (equal (car mh) 0)))
                      (setq mh (car mh))
                      ;; axlPutModel only registers the model NAME; the file
                      ;; PATH must be set separately or Spectre emits
                      ;; `include ""` (SFE-73). Ref: adexlSKILLref.pdf p.221
                      ;; (axlSetModelFile) + p.217 example. `_file_abs` carries
                      ;; the absolute path captured at pull time.
                      (cond
                        ((and (stringp mFileAbs)
                              (not (equal mFileAbs PVT_JSON_ABSENT))
                              (not (equal mFileAbs "")))
                          (errset (axlSetModelFile mh mFileAbs) nil)))
                      (cond
                        ((and (stringp mBlock)
                              (not (equal mBlock PVT_JSON_ABSENT)))
                          (errset (axlSetModelBlock mh mBlock) nil)))
                      (cond
                        ((and (stringp mTest)
                              (not (equal mTest PVT_JSON_ABSENT)))
                          (errset (axlSetModelTest mh mTest) nil)))
                      (let ((sec
                              (_pvtCornersFormatSectionValueFromJson mSection)))
                        (cond
                          (sec
                            (errset (axlSetModelSection mh sec) nil)))))))))))))

    ;; --- tests axis (per-corner Test scope, 2026 UX) -----------------------
    ;; A non-empty 'tests' list scopes the corner to exactly those tests;
    ;; absent / empty restores the all-tests default. Cadence has no direct
    ;; "scope" call — disable the complement test-by-test.
    ;; Ref: axlSetCornerTestEnabled adexlSKILLref.pdf p.339.
    (let ((testsField (_pvtCornersExtractJsonField rowObj "tests")))
      (cond
        ((and (listp testsField) testsField)
          (foreach tn allTests
            (errset
              (axlSetCornerTestEnabled ch tn
                (cond ((member tn testsField) t) (t nil)))
              nil)))
        (t
          (foreach tn allTests
            (errset (axlSetCornerTestEnabled ch tn t) nil)))))

    (return (pvtOk t))
  )
)


(procedure (_pvtCornersCollectRowNames rowsArr)
  ;; Return a list of "row_name" strings extracted from a parsed sidecar's
  ;; `rows` array. Skips rows missing the field (downstream push will
  ;; surface those as pvtErr).
  (let ((acc nil))
    (foreach rowObj rowsArr
      (cond
        ((tablep rowObj)
          (let ((n (_pvtCornersExtractJsonField rowObj "row_name")))
            (cond
              ((stringp n) (setq acc (cons n acc))))))))
    (reverse acc)
  )
)


(procedure (_pvtCornersRemoveByName sdb name)
  ;; Drop one corner row by name. Returns (pvtOk t) on success, pvtErr
  ;; otherwise. Uses axlGetCorner to fetch the handle then axlRemoveElement
  ;; per DECISIONS #54 #8 (v1.1 dogfood workaround vetted on fnxSession0).
  (let ((ch nil))
    (setq ch (car (errset (axlGetCorner sdb name) nil)))
    (cond
      ((null ch)
        (pvtErr 'pvt_io
          (strcat "axlGetCorner returned nil for " name) nil))
      (t
        (let ((rv (errset (axlRemoveElement ch) nil)))
          (cond
            ((null rv)
              (pvtErr 'pvt_io
                (strcat "axlRemoveElement failed for " name) nil))
            (t (pvtOk t))))))
  )
)


(procedure (pvtCornersPush @key (sess nil) (unionJsonPath nil) (dryRun nil)
                                (replace nil))
  ;; Push a `.union.json` sidecar into the live ADE-XL setup.
  ;;   - `sess`           : Maestro session id (string). nil => infer.
  ;;   - `unionJsonPath`  : path to the sidecar to load and push.
  ;;   - `dryRun`         : t means parse + validate but do not call axl*
  ;;                        write APIs. v1 only enforces parse-stage error
  ;;                        surfacing; write-stage validation lives in axl*.
  ;;   - `replace`        : t means drop every live corner whose name is
  ;;                        NOT in the sidecar's `rows[*].row_name` set
  ;;                        before pushing. Default (nil) preserves the v1
  ;;                        ADD-semantics where existing-but-unmentioned
  ;;                        rows survive. Empty sidecar + replace = refused
  ;;                        as pvt_validation (would wipe everything).
  ;;                        dryRun + replace = combined (no deletes, no
  ;;                        adds — just parse-validate).
  ;; Returns (pvtOk t_unionName) on success or pvtErr.
  (prog (sessResult resolvedSess sdb parsed rowsArr rowResult rowFailed
         unionName liveCorners liveNames keepNames toDelete delResult
         allTests)
    (cond
      ((null unionJsonPath)
        (return (pvtErr 'pvt_validation
                  "pvtCornersPush: ?unionJsonPath is required" nil))))

    (setq parsed (pvtJsonParseFile unionJsonPath))
    (cond
      ((pvtIsErr parsed) (return parsed)))
    (setq parsed (pvtUnwrap parsed))

    (cond
      ((not (tablep parsed))
        (return (pvtErr 'pvt_validation
                  (strcat unionJsonPath ": top-level must be a JSON object")
                  unionJsonPath))))

    (setq unionName (arrayref parsed "name"))
    (cond
      ((or (null unionName) (equal unionName PVT_JSON_ABSENT))
        (return (pvtErr 'pvt_validation
                  (strcat unionJsonPath ": missing 'name' field")
                  unionJsonPath))))

    (setq rowsArr (arrayref parsed "rows"))
    (cond
      ((or (null rowsArr) (equal rowsArr PVT_JSON_ABSENT))
        (return (pvtErr 'pvt_validation
                  (strcat unionJsonPath ": missing 'rows' array")
                  unionJsonPath)))
      ((not (listp rowsArr))
        (return (pvtErr 'pvt_validation
                  (strcat unionJsonPath ": 'rows' must be a JSON array")
                  unionJsonPath))))

    ;; --- replace-mode accident guard (BEFORE dryRun short-circuits so
    ;;     dry-run-replace also surfaces the empty-sidecar problem) -----
    (setq keepNames (_pvtCornersCollectRowNames rowsArr))
    (cond
      ((and replace (null keepNames))
        (return (pvtErr 'pvt_validation
                  (strcat unionJsonPath ": replace=t with 0 valid rows would "
                          "wipe every corner; refusing")
                  unionJsonPath))))

    (cond
      (dryRun (return (pvtOk unionName))))

    (setq sessResult (_pvtCornersResolveSession sess))
    (cond
      ((pvtIsErr sessResult) (return sessResult)))
    (setq resolvedSess (pvtUnwrap sessResult))

    (setq sdb (car (errset (axlGetMainSetupDB resolvedSess) nil)))
    (cond
      ((null sdb)
        (return (pvtErr 'pvt_validation
                  "axlGetMainSetupDB returned nil for the resolved session"
                  nil))))

    ;; Global test list — each row applies its Test scope against this.
    ;; Ref: axlGetTests adexlSKILLref.pdf p.282.
    (setq allTests (cadr (car (errset (axlGetTests sdb) nil))))

    ;; --- replace pass: drop live rows not in the keep set ----------------
    (cond
      (replace
        (setq liveCorners (car (errset (axlGetCorners sdb) nil)))
        (cond
          ((null liveCorners)
            (return (pvtErr 'pvt_io
                      "axlGetCorners returned nil during replace" nil))))
        ;; axlGetCorners returns (handle name-list); cadr is the names.
        (setq liveNames (cadr liveCorners))
        (setq toDelete nil)
        (foreach n liveNames
          (cond
            ((not (member n keepNames))
              (setq toDelete (cons n toDelete)))))
        (setq toDelete (reverse toDelete))
        (foreach n toDelete
          (cond
            ((null rowFailed)
              (setq delResult (_pvtCornersRemoveByName sdb n))
              (cond
                ((pvtIsErr delResult) (setq rowFailed delResult))))))
        (cond
          (rowFailed (return rowFailed)))))

    (setq rowFailed nil)
    (foreach rowObj rowsArr
      (cond
        ((null rowFailed)
          (setq rowResult (_pvtCornersPushRow sdb rowObj allTests))
          (cond
            ((pvtIsErr rowResult) (setq rowFailed rowResult))))))

    (cond
      (rowFailed (return rowFailed)))

    (return (pvtOk unionName))
  )
)
