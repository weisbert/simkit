;; ============================================================================
;; pvtMeasure.il
;;
;; Phase 3B §3 SKILL bridge between live ADE-XL Outputs table and the
;; pre-rendered measurement-bundle JSON sidecars produced by the Python
;; renderer (template + signal_group + bundle -> flat row list).
;;
;; Sibling of pvtCorners.il (Phase 2 §3). Same conventions:
;;   - Every procedure body wrapped in `(prog () ...)` for `return` (sklang
;;     trap, DECISIONS #14 trap 7).
;;   - Named arithmetic / comparison functions only (`plus`, `leqp`, ...),
;;     no operator shorthand (DECISIONS #32).
;;   - Reuses pvtError.il (pvtOk / pvtErr / pvtIs* / pvtUnwrap) and
;;     pvtJson.il (parser + emitter + sentinels). No new helper modules.
;;
;; Public API:
;;   pvtMeasurePush(?sess t_sess ?renderedJsonPath t_path
;;                  [?testName "Test"] [?dryRun nil] [?replace nil])
;;     -> pvtOk(report) | pvtErr
;;     report := makeTable with keys (n_pushed, rows)
;;       rows := list of per-row tables (name, status, reason?)
;;       status := "added" | "replaced" | "failed" | "would_add" | "would_replace"
;;
;;   pvtMeasurePull(?sess t_sess ?outPath t_outPath
;;                  [?testName "Test"] [?includeSignals nil])
;;     -> pvtOk(report) | pvtErr
;;     report := makeTable with keys (n_rows, path)
;;
;; Maestro Outputs CSV columns (probed live 2026-05-14, DECISIONS #40):
;;   Test, Name, Type, Output, Plot, Save, Spec
;;     Type ∈ {"net", "expr"};
;;     Plot/Save ∈ {"t", ""}; Spec usually empty.
;; Phase 3B v1 push handles `expr` rows only; pull defaults to `expr` only
;; and ?includeSignals t includes `net`.
;;
;; SKILL APIs referenced (each cited at the call site):
;;   axlGetWindowSession      adexlSKILLref.pdf p.38
;;   axlGetMainSetupDB        adexlSKILLref.pdf p.31   (parity with pvtCorners)
;;   axlAddOutputExpr         adexlSKILLref.pdf p.239
;;   axlDeleteOutput          adexlSKILLref.pdf p.245
;;   axlOutputsExportToFile   adexlSKILLref.pdf p.249
;;   infile / gets / close    sklangref.pdf  p.423 / p.420 / p.391
;;   outfile                  sklangref.pdf  p.450
;;   random                   sklangref.pdf  p.267  (unique tmp suffix)
;;
;; Module map:
;;   pvtMeasurePush
;;     ├── _pvtMeasureLoadRendered      (JSON parse + shape validation)
;;     ├── _pvtMeasureRowField           (read JSON field, default-or-absent)
;;     ├── _pvtMeasureBoolFromJson       (PVT_JSON_TRUE -> t, PVT_JSON_FALSE -> nil)
;;     ├── _pvtMeasurePushOne            (per-row axlDelete + axlAdd)
;;     └── _pvtMeasureMakeRowResult      (name/status/reason table)
;;
;;   pvtMeasurePull
;;     ├── _pvtMeasureUniqueTmpPath      (random suffix; no getpid)
;;     ├── _pvtMeasureReadCsvLines       (infile + gets; strip trailing \n)
;;     ├── _pvtMeasureParseCsvLine       (strict 7-field comma split)
;;     ├── _pvtMeasureNormaliseBool      ("t" -> t, "" -> nil)
;;     ├── _pvtMeasureKeepRowP           (test + includeSignals filter)
;;     ├── _pvtMeasureBuildSnapshot      (top-level JSON envelope)
;;     └── _pvtMeasureWriteOutput        (pvtJsonEmitToPort to disk)
;; ============================================================================


(defvar _PVT_MEASURE_RENDERED_SCHEMA_VERSION 1)
(defvar _PVT_MEASURE_SNAPSHOT_SCHEMA_VERSION 1)
(defvar _PVT_MEASURE_DEFAULT_TEST            "Test")
(defvar _PVT_MEASURE_CSV_NCOLS               7)


;; ----------------------------------------------------------------------------
;; Pure helpers — Tier-1 testable, no axl* / hi* / IO dependency
;; ----------------------------------------------------------------------------

(procedure (_pvtMeasureRowField rowObj key default)
  "Read `key` from a parsed JSON row table. Returns `default` when the key is
   absent or holds PVT_JSON_NULL. Returns the value otherwise."
  ;; Ref: pvtJson.il (arrayref returns PVT_JSON_ABSENT for missing keys).
  (prog (v)
    (cond
      ((not (tablep rowObj)) (return default)))
    (setq v (arrayref rowObj key))
    (cond
      ((equal v PVT_JSON_ABSENT) (return default))
      ((equal v PVT_JSON_NULL)   (return default))
      (t                          (return v)))
  )
)


(procedure (_pvtMeasureBoolFromJson v default)
  "Map a JSON-parsed boolean (PVT_JSON_TRUE / PVT_JSON_FALSE / SKILL t / nil /
   absent / null) to a SKILL boolean. PVT_JSON_TRUE / t -> t; PVT_JSON_FALSE
   / nil-with-explicit-null -> nil; absent -> `default`."
  (prog ()
    (cond
      ((equal v PVT_JSON_ABSENT) (return default))
      ((equal v PVT_JSON_NULL)   (return default))
      ((equal v PVT_JSON_TRUE)   (return t))
      ((equal v PVT_JSON_FALSE)  (return nil))
      ((equal v t)               (return t))
      ((null v)                  (return nil))
      (t                          (return t))))
)


(procedure (_pvtMeasureNormaliseBool csvField)
  "Maestro CSV plot/save normalisation: \"t\" -> t, \"\" / nil -> nil.
   Any other token is returned as nil (Maestro never emits anything else for
   these columns)."
  (cond
    ((null csvField)     nil)
    ((equal csvField "") nil)
    ((equal csvField "t") t)
    (t                   nil))
)


;; _pvtMeasureCountChar : count occurrences of single-char string `ch` in s.
;; Used by _pvtMeasureParseCsvLine to detect "comma inside a field" — v1
;; rejects such rows (escalate). Returns 0 for nil / empty.
(procedure (_pvtMeasureCountChar s ch)
  (prog (i n acc)
    (cond
      ((null s)         (return 0))
      ((not (stringp s)) (return 0))
      ((equal s "")     (return 0)))
    (setq n (strlen s))
    (setq i 1)
    (setq acc 0)
    (while (leqp i n)
      (cond
        ((equal (substring s i 1) ch) (setq acc (plus acc 1))))
      (setq i (plus i 1)))
    (return acc)
  )
)


(procedure (_pvtMeasureSplitOnComma s)
  "Split `s` on every literal comma. Empty trailing fields produce \"\"."
  ;; Classic SKILL `parseString s ","` collapses runs of separators by
  ;; default; we want strict "one comma = one boundary, even adjacent",
  ;; so we walk the string ourselves. Matches CSV semantics where a row
  ;; like "Test,,net,..." must split to 7 fields including the empty Name.
  (prog (tokens i n start ch)
    (cond
      ((null s)         (return (list "")))
      ((not (stringp s)) (return (list "")))
      ((equal s "")     (return (list ""))))
    (setq n (strlen s))
    (setq i 1)
    (setq start 1)
    (setq tokens nil)
    (while (leqp i n)
      (setq ch (substring s i 1))
      (cond
        ((equal ch ",")
          (cond
            ((lessp start i)
              (setq tokens (cons (substring s start (difference i start)) tokens)))
            (t
              (setq tokens (cons "" tokens))))
          (setq start (plus i 1))))
      (setq i (plus i 1)))
    ;; trailing token (or empty string if line ended with ",")
    (cond
      ((leqp start n)
        (setq tokens (cons (substring s start (difference (plus n 1) start)) tokens)))
      (t
        (setq tokens (cons "" tokens))))
    (return (reverse tokens))
  )
)


(procedure (_pvtMeasureParseCsvLine line)
  "Parse one Maestro CSV row (post-newline-strip). Returns pvtOk(fieldsList)
   on success (always 7 elements) or pvtErr on:
     - column-count mismatch (comma-in-field case is impossible to recover
       in v1 without quoting support — Maestro export does not emit quoting,
       per scratch_p3b_fnxsession0_outputs.csv 2026-05-14 evidence — so a
       row with extra commas is a hard error).
     - nil / non-string input."
  (prog (parts n)
    (cond
      ((null line)
        (return (pvtErr 'pvt_validation "csv line is nil" nil)))
      ((not (stringp line))
        (return (pvtErr 'pvt_validation "csv line is not a string" nil))))
    (setq parts (_pvtMeasureSplitOnComma line))
    (setq n (length parts))
    (cond
      ((not (equal n _PVT_MEASURE_CSV_NCOLS))
        (return (pvtErr 'pvt_validation
                  (sprintf nil "csv row has %d fields, expected %d (comma inside field not supported in v1): %s"
                           n _PVT_MEASURE_CSV_NCOLS line)
                  nil))))
    (return (pvtOk parts))
  )
)


(procedure (_pvtMeasureStripTrailingNewline s)
  "gets() returns the line WITH trailing \\n (sklangref p.420). Strip it."
  (prog (n last)
    (cond
      ((null s) (return s))
      ((not (stringp s)) (return s)))
    (setq n (strlen s))
    (cond
      ((zerop n) (return s)))
    (setq last (substring s n 1))
    (cond
      ((equal last "\n") (return (substring s 1 (difference n 1))))
      (t                  (return s)))
  )
)


(procedure (_pvtMeasureKeepRowP fields testName includeSignals)
  "Apply pull-side filters: keep only rows whose Test column equals `testName`,
   AND (if includeSignals is nil) drop rows where Type == \"net\".
   `fields` must be a 7-element list (Test, Name, Type, Output, Plot, Save, Spec)."
  (prog (rTest rType)
    (cond
      ((or (null fields) (not (listp fields))) (return nil))
      ((not (equal (length fields) _PVT_MEASURE_CSV_NCOLS)) (return nil)))
    (setq rTest (nthelem 1 fields))
    (setq rType (nthelem 3 fields))
    (cond
      ((not (equal rTest testName)) (return nil)))
    (cond
      ((and (null includeSignals) (equal rType "net")) (return nil)))
    (return t)
  )
)


(procedure (_pvtMeasureMakeRowResult name status reason @optional (specStatus nil))
  "Build a per-row outcome table { name, status [, reason] [, spec_status] }
   for the push report. `reason` may be nil (omitted). v1.3: optional
   `specStatus` field carries the outcome of the per-row axlAddSpecToOutput
   call (one of \"ok\" / \"skipped\" / \"failed:<reason>\")."
  (prog (tab keys)
    (setq tab (makeTable 'pvtMeasureRowResult PVT_JSON_NULL))
    (setarray tab "name"   name)
    (setarray tab "status" status)
    (setq keys (list "name" "status"))
    (cond
      (reason
        (setarray tab "reason" reason)
        (setq keys (append keys (list "reason")))))
    (cond
      (specStatus
        (setarray tab "spec_status" specStatus)
        (setq keys (append keys (list "spec_status")))))
    (setarray tab _PVT_JSON_KEYS_PROP keys)
    (return tab)
  )
)


;; --------------------------------------------------------------------------
;; v1.3 — guard for evalstring on numeric tokens. errset does NOT catch
;; "unbound variable" errors raised inside evalstring, so we have to vet
;; the token shape before handing it off (confirmed live 2026-05-15 via
;; the _pvtTestEvalstring probe). Pattern matches optional sign, mandatory
;; digits, optional fractional + exponent, optional single SI suffix.
;; --------------------------------------------------------------------------
(defvar _PVT_MEASURE_NUM_CHARS "0123456789+-.eE")
(defvar _PVT_MEASURE_SI_SUFFIXES "fpnumkMGT")


(procedure (_pvtMeasureSafeEvalNumber tok)
  "Return (pvtOk number) | pvtErr for a single numeric token (optional SI
   suffix). Vets the character set first because errset cannot catch
   evalstring's unbound-variable error path (confirmed live 2026-05-15).
   Classic SKILL rexCompile lacks ? and () so we hand-roll the check."
  (prog (rv stripped lastCh i ch)
    (cond
      ((or (null tok) (not (stringp tok)) (equal tok ""))
        (return (pvtErr 'pvt_validation "empty numeric token" tok))))
    (setq stripped tok)
    ;; Peel a single trailing SI-suffix char if present.
    (setq lastCh (substring tok (strlen tok) 1))
    (cond
      ((index _PVT_MEASURE_SI_SUFFIXES lastCh)
        (setq stripped (substring tok 1 (sub1 (strlen tok))))))
    (cond
      ((equal stripped "")
        (return (pvtErr 'pvt_validation
                  (strcat "not a numeric token: " tok) tok))))
    ;; Every remaining char must be a number char.
    (setq i 1)
    (while (leqp i (strlen stripped))
      (setq ch (substring stripped i 1))
      (cond
        ((null (index _PVT_MEASURE_NUM_CHARS ch))
          (return (pvtErr 'pvt_validation
                    (strcat "not a numeric token: " tok) tok))))
      (setq i (add1 i)))
    (setq rv (errset (evalstring tok) t))
    (cond
      ((or (null rv) (not (numberp (car rv))))
        (return (pvtErr 'pvt_validation
                  (strcat "evalstring produced non-number for: " tok) tok))))
    (return (pvtOk (car rv)))
  )
)


;; --------------------------------------------------------------------------
;; v1.3 — Cadence-native spec string parser.
;;
;; Accepts the same forms Cadence emits in CSV (verified live 2026-05-15
;; against fnxSession0; see DECISIONS #45):
;;   "<X"       /  "<X"     /  "< X"   → ("lt"    X_eval)
;;   ">X"       /  "> X"               → ("gt"    X_eval)
;;   "<=X"      /  "<= X"              → ("max"   X_eval)        [inclusive]
;;   ">=X"      /  ">= X"              → ("min"   X_eval)        [inclusive]
;;   "range X Y"                       → ("range" X_eval Y_eval)
;;   "[X:Y]"                           → ("range" X_eval Y_eval)
;;   "X..Y"                            → ("range" X_eval Y_eval)
;;   "tol X"                           → ("tol"   X_eval)
;;
;; SI suffixes (100p, 2.4G, …) work for free — evalstring resolves them via
;; Cadence's native reader. Returns (pvtOk (kindStr val1 [val2])) on success,
;; pvtErr on bad syntax / non-numeric value.
;; --------------------------------------------------------------------------
(procedure (_pvtMeasureParseSpec specStr)
  "Parse a Cadence-style spec string into a tagged value list.
   Returns pvtOk (list kindStr v1 [v2]) | pvtErr."
  (prog (s op rest parts v1 v2)
    (cond
      ((or (null specStr) (not (stringp specStr)))
        (return (pvtErr 'pvt_validation
                  "spec must be a non-empty string" specStr))))
    ;; Strip leading/trailing whitespace via a poor-man trim. Classic SKILL
    ;; has no built-in strip, so peel manually.
    (setq s specStr)
    (while (and (greaterp (strlen s) 0)
                (member (substring s 1 1) (list " " "\t" "\n")))
      (setq s (substring s 2)))
    (while (and (greaterp (strlen s) 0)
                (member (substring s (strlen s) 1)
                        (list " " "\t" "\n")))
      (setq s (substring s 1 (sub1 (strlen s)))))
    (cond
      ((equal s "")
        (return (pvtErr 'pvt_validation "spec is empty after trim" specStr))))

    ;; Bracket range form: "[X:Y]"
    (cond
      ((and (greaterp (strlen s) 2)
            (equal (substring s 1 1) "[")
            (equal (substring s (strlen s) 1) "]"))
        (setq rest (substring s 2 (difference (strlen s) 2)))
        (setq parts (parseString rest ":"))
        (cond
          ((not (equal (length parts) 2))
            (return (pvtErr 'pvt_validation
                      (strcat "spec bracket form must be [X:Y], got: " specStr)
                      specStr))))
        (setq v1 (_pvtMeasureSafeEvalNumber (car parts)))
        (cond ((pvtIsErr v1) (return v1)))
        (setq v2 (_pvtMeasureSafeEvalNumber (cadr parts)))
        (cond ((pvtIsErr v2) (return v2)))
        (return (pvtOk (list "range" (pvtUnwrap v1) (pvtUnwrap v2))))))

    ;; v1.4 — Dotted "X..Y" range form. parseString can't help (single-char
    ;; delimiter only), but classic SKILL `index` returns the substring at
    ;; first match — enough to split on the literal ".." substring without
    ;; confusing it with numeric-literal dots. Each side is then validated
    ;; via _pvtMeasureSafeEvalNumber. Two pre-checks are mandatory:
    ;;   (a) reject 3+ consecutive dots ("1...2"). Without this, "1...2"
    ;;       silently parses as range(1, .2) since .2 is a valid number.
    ;;   (b) reject multiple ".." occurrences ("1..2..3"). Without this,
    ;;       the right side "2..3" feeds evalstring, which calls the SKILL
    ;;       reader on "2..3" and crashes uncatchably (errset cannot trap
    ;;       evalstring reader errors per DECISIONS #45).
    (cond
      ((index s "...")
        (return (pvtErr 'pvt_validation
                  (strcat "spec has 3+ consecutive dots; "
                          "use 'X..Y' exactly (got: " specStr ")")
                  specStr))))
    (setq rest (index s ".."))
    (cond
      ((and rest
            ;; both sides non-empty: suffix-after-dotdot >= 3 chars
            ;; (".." plus at least one rhs char) AND less than the full string
            ;; (at least one lhs char).
            (greaterp (strlen rest) 2)
            (lessp (strlen rest) (strlen s)))
        ;; Guard against multiple ".." (e.g. "1..2..3"): the rhs would then
        ;; hit evalstring with a malformed token, and the SKILL reader's
        ;; error path is uncatchable per DECISIONS #45.
        (cond
          ((index (substring rest 3) "..")
            (return (pvtErr 'pvt_validation
                      (strcat "spec contains multiple '..' separators; "
                              "use a single 'X..Y' (got: " specStr ")")
                      specStr))))
        (setq v2 (_pvtMeasureSafeEvalNumber (substring rest 3)))
        (cond ((pvtIsErr v2) (return v2)))
        (setq v1 (_pvtMeasureSafeEvalNumber
                   (substring s 1 (difference (strlen s) (strlen rest)))))
        (cond ((pvtIsErr v1) (return v1)))
        (return (pvtOk (list "range" (pvtUnwrap v1) (pvtUnwrap v2))))))

    ;; Comparison operators — handle the 2-char ones first (<=, >=) then 1-char.
    (cond
      ((and (greaterp (strlen s) 2) (equal (substring s 1 2) "<="))
        (setq op "max") (setq rest (substring s 3)))
      ((and (greaterp (strlen s) 2) (equal (substring s 1 2) ">="))
        (setq op "min") (setq rest (substring s 3)))
      ((and (greaterp (strlen s) 1) (equal (substring s 1 1) "<"))
        (setq op "lt") (setq rest (substring s 2)))
      ((and (greaterp (strlen s) 1) (equal (substring s 1 1) ">"))
        (setq op "gt") (setq rest (substring s 2)))
      (t (setq op nil)))

    (cond
      (op
        ;; The op suffix may have leading whitespace ("< 100p" form). Trim
        ;; before vetting.
        (while (and (greaterp (strlen rest) 0)
                    (equal (substring rest 1 1) " "))
          (setq rest (substring rest 2)))
        (setq v1 (_pvtMeasureSafeEvalNumber rest))
        (cond ((pvtIsErr v1) (return v1)))
        (return (pvtOk (list op (pvtUnwrap v1))))))

    ;; Keyword forms: "range X Y" / "tol X"
    (setq parts (parseString s " \t"))
    (cond
      ((and (equal (car parts) "range") (equal (length parts) 3))
        (setq v1 (_pvtMeasureSafeEvalNumber (cadr  parts)))
        (cond ((pvtIsErr v1) (return v1)))
        (setq v2 (_pvtMeasureSafeEvalNumber (caddr parts)))
        (cond ((pvtIsErr v2) (return v2)))
        (return (pvtOk (list "range" (pvtUnwrap v1) (pvtUnwrap v2)))))
      ((and (equal (car parts) "tol") (equal (length parts) 2))
        (setq v1 (_pvtMeasureSafeEvalNumber (cadr parts)))
        (cond ((pvtIsErr v1) (return v1)))
        (return (pvtOk (list "tol" (pvtUnwrap v1))))))

    (return (pvtErr 'pvt_validation
              (strcat "unrecognised spec form: " specStr) specStr))
  )
)


;; --------------------------------------------------------------------------
;; v1.3 — dispatch a parsed spec to axlAddSpecToOutput with the correct
;; mutually-exclusive keyword. Returns t on success, string reason on fail.
;; --------------------------------------------------------------------------
(procedure (_pvtMeasureApplyParsedSpec sdb testName outName parsed)
  "Call axlAddSpecToOutput with the keyword arg implied by the parsed tag.
   `parsed` is the (kindStr v1 [v2]) list produced by _pvtMeasureParseSpec.
   Returns t on success, a string reason on failure."
  (prog (kind v1 v2 rv)
    (setq kind (car parsed))
    (setq v1   (cadr parsed))
    (setq v2   (caddr parsed))
    (setq rv
      (cond
        ((equal kind "lt")
          (errset (axlAddSpecToOutput sdb testName outName ?lt v1) nil))
        ((equal kind "gt")
          (errset (axlAddSpecToOutput sdb testName outName ?gt v1) nil))
        ((equal kind "min")
          (errset (axlAddSpecToOutput sdb testName outName ?min v1) nil))
        ((equal kind "max")
          (errset (axlAddSpecToOutput sdb testName outName ?max v1) nil))
        ((equal kind "range")
          (errset (axlAddSpecToOutput sdb testName outName
                                      ?range (list v1 v2)) nil))
        ((equal kind "tol")
          (errset (axlAddSpecToOutput sdb testName outName ?tol v1) nil))
        (t nil)))
    (cond
      ((null rv)
        (return (sprintf nil "axlAddSpecToOutput threw for kind=%s" kind)))
      ((null (car rv))
        (return (sprintf nil "axlAddSpecToOutput returned nil for kind=%s" kind)))
      (t (return t)))
  )
)


(procedure (_pvtMeasureBuildPushReport rowsList nPushed)
  "Wrap a list of per-row outcome tables (most-recent-first → reversed here)
   plus a count into the top-level push report table."
  (prog (tab)
    (setq tab (makeTable 'pvtMeasurePushReport PVT_JSON_NULL))
    (setarray tab "n_pushed" nPushed)
    (setarray tab "rows"     (or (reverse rowsList) PVT_JSON_EMPTY_ARRAY))
    (setarray tab _PVT_JSON_KEYS_PROP (list "n_pushed" "rows"))
    (return tab)
  )
)


(procedure (_pvtMeasureBuildSnapshotRow fields)
  "Build one snapshot JSON row table from a parsed CSV 7-field list.
   Fields: (Test Name Type Output Plot Save Spec). Snapshot key order:
   name, type, expression, plot, save, spec."
  (prog (rName rType rOutput rPlot rSave rSpec tab)
    (setq rName   (nthelem 2 fields))
    (setq rType   (nthelem 3 fields))
    (setq rOutput (nthelem 4 fields))
    (setq rPlot   (nthelem 5 fields))
    (setq rSave   (nthelem 6 fields))
    (setq rSpec   (nthelem 7 fields))
    (setq tab (makeTable 'pvtMeasureSnapshotRow PVT_JSON_NULL))
    (setarray tab "name"       (or rName ""))
    (setarray tab "type"       (or rType ""))
    (setarray tab "expression" (or rOutput ""))
    (setarray tab "plot"
      (cond ((_pvtMeasureNormaliseBool rPlot) PVT_JSON_TRUE) (t PVT_JSON_FALSE)))
    (setarray tab "save"
      (cond ((_pvtMeasureNormaliseBool rSave) PVT_JSON_TRUE) (t PVT_JSON_FALSE)))
    (setarray tab "spec"       (or rSpec ""))
    (setarray tab _PVT_JSON_KEYS_PROP
      (list "name" "type" "expression" "plot" "save" "spec"))
    (return tab)
  )
)


(procedure (_pvtMeasureBuildSnapshotEnvelope sess testName rowsList path)
  "Top-level pull-side JSON: snapshot_schema_version, session, test, rows."
  (prog (env nRows tab)
    (setq nRows (length rowsList))
    (setq env (makeTable 'pvtMeasureSnapshotEnvelope PVT_JSON_NULL))
    (setarray env "snapshot_schema_version" _PVT_MEASURE_SNAPSHOT_SCHEMA_VERSION)
    (setarray env "session"                 (or sess ""))
    (setarray env "test"                    (or testName _PVT_MEASURE_DEFAULT_TEST))
    (setarray env "rows"                    (or rowsList PVT_JSON_EMPTY_ARRAY))
    (setarray env _PVT_JSON_KEYS_PROP
      (list "snapshot_schema_version" "session" "test" "rows"))
    ;; Side-channel: caller wants the row count + path for the success report.
    (setq tab (makeTable 'pvtMeasurePullReport PVT_JSON_NULL))
    (setarray tab "n_rows" nRows)
    (setarray tab "path"   (or path ""))
    (setarray tab _PVT_JSON_KEYS_PROP (list "n_rows" "path"))
    (return (list env tab))
  )
)


;; ----------------------------------------------------------------------------
;; Session resolution — shared idiom with pvtCorners.il
;; ----------------------------------------------------------------------------

(procedure (_pvtMeasureResolveSession sess)
  ;; If `sess` is nil, use the current Maestro window's session. Otherwise
  ;; pass through. Returns (pvtOk sessId) | pvtErr.
  ;; Ref: axlGetWindowSession adexlSKILLref.pdf p.38.
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


;; ----------------------------------------------------------------------------
;; Push side
;; ----------------------------------------------------------------------------

(procedure (_pvtMeasureLoadRendered path)
  "Parse the pre-rendered JSON sidecar and pull the top-level fields. Returns
   pvtOk(list test rowsList) on success or pvtErr on schema violation."
  ;; Ref: pvtJsonParseFile (pvtJson.il public entry).
  (prog (parsed sessTest test rowsArr schemaV)
    (cond
      ((null path)
        (return (pvtErr 'pvt_validation
                  "pvtMeasurePush: ?renderedJsonPath is required" nil))))

    (setq parsed (pvtJsonParseFile path))
    (cond
      ((pvtIsErr parsed) (return parsed)))
    (setq parsed (pvtUnwrap parsed))
    (cond
      ((not (tablep parsed))
        (return (pvtErr 'pvt_validation
                  (strcat path ": top-level must be a JSON object") path))))

    (setq schemaV (arrayref parsed "rendered_schema_version"))
    (cond
      ((or (null schemaV) (equal schemaV PVT_JSON_ABSENT))
        (return (pvtErr 'pvt_validation
                  (strcat path ": missing 'rendered_schema_version'") path)))
      ((not (equal schemaV _PVT_MEASURE_RENDERED_SCHEMA_VERSION))
        (return (pvtErr 'pvt_validation
                  (sprintf nil "%s: unsupported rendered_schema_version %L (expected %d)"
                           path schemaV _PVT_MEASURE_RENDERED_SCHEMA_VERSION)
                  path))))

    (setq test (arrayref parsed "test"))
    (cond
      ((or (null test) (equal test PVT_JSON_ABSENT) (not (stringp test)))
        (setq test _PVT_MEASURE_DEFAULT_TEST)))

    (setq rowsArr (arrayref parsed "rows"))
    (cond
      ((or (null rowsArr) (equal rowsArr PVT_JSON_ABSENT))
        (return (pvtErr 'pvt_validation
                  (strcat path ": missing 'rows' array") path)))
      ((equal rowsArr PVT_JSON_EMPTY_ARRAY)
        (setq rowsArr nil))
      ((not (listp rowsArr))
        (return (pvtErr 'pvt_validation
                  (strcat path ": 'rows' must be a JSON array") path))))

    (setq sessTest (arrayref parsed "session"))
    (return (pvtOk (list test rowsArr sessTest)))
  )
)


(procedure (_pvtMeasurePushOne sess sdb testName rowObj dryRun replace)
  "Push (or simulate pushing) one rendered row.
   Returns a per-row outcome table from _pvtMeasureMakeRowResult.
   v1.3: `sdb` is the setup-database handle from axlGetMainSetupDB —
   required when a row carries a non-empty `spec`. May be nil in dry-run."
  ;; Ref: axlDeleteOutput adexlSKILLref.pdf p.245 ; axlAddOutputExpr p.239 ;
  ;;      axlAddSpecToOutput p.299.
  (prog (name expr evalType plotV saveV specStr existedBefore addRv delRv
         status reason specStatus parsed applyRv)
    (cond
      ((not (tablep rowObj))
        (return (_pvtMeasureMakeRowResult ""
                  "failed" "row is not a JSON object"))))

    (setq name     (_pvtMeasureRowField rowObj "output_name" nil))
    (setq expr     (_pvtMeasureRowField rowObj "expression"  nil))
    (setq evalType (_pvtMeasureRowField rowObj "eval_type"   "point"))
    (setq plotV    (_pvtMeasureBoolFromJson
                     (arrayref rowObj "plot") t))    ; default true (per spec §3.2)
    (setq saveV    (_pvtMeasureBoolFromJson
                     (arrayref rowObj "save") nil))  ; default false
    (setq specStr  (_pvtMeasureRowField rowObj "spec" ""))

    (cond
      ((or (null name) (not (stringp name)) (equal name ""))
        (return (_pvtMeasureMakeRowResult (or name "")
                  "failed" "missing or empty 'output_name'"))))
    (cond
      ((or (null expr) (not (stringp expr)) (equal expr ""))
        (return (_pvtMeasureMakeRowResult name
                  "failed" "missing or empty 'expression'"))))

    ;; Dry-run: do not call axl*. Report the would-be status.
    (cond
      (dryRun
        (return (_pvtMeasureMakeRowResult name
                  (cond (replace "would_replace") (t "would_add"))
                  nil
                  (cond ((and specStr (not (equal specStr "")))
                          (sprintf nil "would_apply: %s" specStr))
                        (t nil))))))

    ;; Replace: try deleting first; treat "no such output" as benign.
    ;; axlDeleteOutput returns t on success / t_error on failure. We ignore
    ;; the failure shape — if there was nothing to delete, the subsequent
    ;; add still succeeds.
    (setq existedBefore nil)
    (cond
      (replace
        (setq delRv (errset (axlDeleteOutput sess testName name ?type "expr") nil))
        (cond
          ((and delRv (equal (car delRv) t))
            (setq existedBefore t)))))

    ;; Add the expression. Note `?plot` / `?save` accept t / nil in classic
    ;; SKILL; passing the SKILL t directly maps to the documented g_plot
    ;; "if non-nil, plot the output" semantics.
    (setq addRv (errset
                  (axlAddOutputExpr sess testName name
                                    ?expr     expr
                                    ?evalType evalType
                                    ?plot     plotV
                                    ?save     saveV)
                  nil))
    (cond
      ((or (null addRv) (null (car addRv)))
        (setq status "failed")
        (setq reason "axlAddOutputExpr returned nil / threw"))
      ((equal (car addRv) t)
        (setq status (cond (existedBefore "replaced") (t "added")))
        (setq reason nil))
      (t
        ;; Per DECISIONS #40, axlAddOutputExpr returns t even when the
        ;; expression is malformed (silent no-op). The non-t / non-nil
        ;; branch should not be reached on this Cadence version, but we
        ;; still capture the returned token verbatim in that case.
        (setq status "failed")
        (setq reason (sprintf nil "axlAddOutputExpr returned %L" (car addRv)))))

    ;; v1.3 — only attempt the spec push when the output add succeeded AND
    ;; the row carries a non-empty spec. Spec failure does NOT down-grade
    ;; the row's primary status (added/replaced); the spec_status field
    ;; carries that signal separately so callers can decide what's fatal.
    (setq specStatus nil)
    (cond
      ((and (or (equal status "added") (equal status "replaced"))
            (stringp specStr) (not (equal specStr "")))
        (cond
          ((null sdb)
            (setq specStatus "failed: no sdb handle"))
          (t
            (setq parsed (_pvtMeasureParseSpec specStr))
            (cond
              ((pvtIsErr parsed)
                (setq specStatus
                  (strcat "failed: " (pvtErrMessage parsed))))
              (t
                (setq applyRv (_pvtMeasureApplyParsedSpec
                                sdb testName name (pvtUnwrap parsed)))
                (cond
                  ((equal applyRv t) (setq specStatus "ok"))
                  (t (setq specStatus (strcat "failed: " applyRv))))))))))

    (return (_pvtMeasureMakeRowResult name status reason specStatus))
  )
)


(procedure (pvtMeasurePush @key (sess nil) (renderedJsonPath nil)
                                 (testName nil) (dryRun nil) (replace nil))
  "Push pre-rendered DPL-like rows from `renderedJsonPath` into the live
   ADE-XL setup's Outputs table for `testName` (default \"Test\").
   `dryRun t` validates the JSON shape and reports would-be status without
   calling axl* writers. `replace t` first deletes each named output.
   Returns pvtOk(pushReport) on success — pvtErr on any per-row failure,
   carrying the full report in its source slot for caller inspection."
  (prog (loadResult triple test rowsArr sessResult resolvedSess
         rowOutcomes rowResult rowFailed nPushed report
         effectiveTest dryRunSess sdbHandle)
    ;; Default test name first so the JSON load can fall back to it.
    (setq effectiveTest (or testName _PVT_MEASURE_DEFAULT_TEST))

    (setq loadResult (_pvtMeasureLoadRendered renderedJsonPath))
    (cond
      ((pvtIsErr loadResult) (return loadResult)))
    (setq triple (pvtUnwrap loadResult))
    (setq test   (nthelem 1 triple))
    (setq rowsArr (nthelem 2 triple))
    (cond
      (testName (setq test testName)))       ; explicit param wins
    (cond
      ((null test) (setq test _PVT_MEASURE_DEFAULT_TEST)))

    ;; Dry-run does NOT require a live session — we only validate JSON +
    ;; report what would be pushed. This lets `pvt measure apply --dry-run`
    ;; run from a non-Maestro shell.
    (setq dryRunSess (or sess "DRYRUN"))

    (cond
      ((null dryRun)
        (setq sessResult (_pvtMeasureResolveSession sess))
        (cond
          ((pvtIsErr sessResult) (return sessResult)))
        (setq resolvedSess (pvtUnwrap sessResult)))
      (t
        (setq resolvedSess dryRunSess)))

    ;; v1.3 — resolve the setup DB handle once per push call. Required by
    ;; axlAddSpecToOutput when a row carries a non-empty `spec`. May be nil
    ;; in dry-run; pushOne tolerates that.
    (cond
      (dryRun (setq sdbHandle nil))
      (t
        (setq sdbHandle (car (errset (axlGetMainSetupDB resolvedSess) nil)))))

    (setq rowOutcomes nil)
    (setq rowFailed   nil)
    (setq nPushed     0)
    (foreach rowObj rowsArr
      (setq rowResult
        (_pvtMeasurePushOne resolvedSess sdbHandle test rowObj dryRun replace))
      (setq rowOutcomes (cons rowResult rowOutcomes))
      (cond
        ((member (arrayref rowResult "status")
                 (list "added" "replaced" "would_add" "would_replace"))
          (setq nPushed (plus nPushed 1)))
        (t
          (cond
            ((null rowFailed) (setq rowFailed rowResult))))))

    (setq report (_pvtMeasureBuildPushReport rowOutcomes nPushed))
    (cond
      (rowFailed
        (return (pvtErr 'pvt_validation
                  (strcat "pvtMeasurePush: row '"
                          (or (arrayref rowFailed "name") "?")
                          "' failed: "
                          (or (arrayref rowFailed "reason") "(no reason)"))
                  report))))
    (return (pvtOk report))
  )
)


;; ----------------------------------------------------------------------------
;; Pull side
;; ----------------------------------------------------------------------------

(procedure (_pvtMeasureUniqueTmpPath)
  "Build a /tmp path with a random 9-digit suffix. SKILL has no documented
   getpid on ICADVM18.1; random() is the project's collision-avoidance
   primitive (already used elsewhere)."
  ;; Ref: random sklangref.pdf p.267.
  (sprintf nil "/tmp/_pvtMeasurePull_%d.csv" (random 1000000000))
)


(procedure (_pvtMeasureReadCsvLines path)
  "Open `path`, read every line via gets(), strip trailing newlines, close.
   Returns pvtOk(linesList) on success or pvtErr on open failure."
  ;; Ref: infile sklangref.pdf p.423 ; gets sklangref.pdf p.420 ;
  ;;      close sklangref.pdf p.391.
  (prog (port lines line)
    (setq port (errset (infile path) nil))
    (cond
      ((or (null port) (null (car port)))
        (return (pvtErr 'pvt_io
                  (strcat "cannot open for read: " path) path))))
    (setq port (car port))
    (setq lines nil)
    (while (gets line port)
      (setq lines (cons (_pvtMeasureStripTrailingNewline line) lines)))
    (close port)
    (return (pvtOk (reverse lines)))
  )
)


(procedure (_pvtMeasureWriteOutput obj outPath)
  "Emit a pvtJson-shaped object to `outPath`. Mirrors _pvtCornersWriteOutput."
  ;; Ref: outfile sklangref.pdf p.450 ; pvtJsonEmitToPort (pvtJson.il).
  (prog (port)
    (setq port (errset (outfile outPath) nil))
    (cond
      ((or (null port) (null (car port)))
        (return (pvtErr 'pvt_io
                  (strcat "cannot open for write: " outPath) outPath))))
    (pvtJsonEmitToPort obj (car port))
    (fprintf (car port) "\n")
    (close (car port))
    (return (pvtOk outPath))
  )
)


(procedure (pvtMeasurePull @key (sess nil) (outPath nil)
                                 (testName nil) (includeSignals nil))
  "Snapshot the live Outputs table to `outPath` (JSON). Filters to rows whose
   Test column equals `testName` (default \"Test\"); drops Type==\"net\" rows
   unless `includeSignals t`. Returns pvtOk(pullReport) on success or pvtErr."
  (prog (sessResult resolvedSess effectiveTest tmpCsv exportRv readResult
         lines firstLine dataLines parseResult fields snapshotRows
         envelopeAndReport envelope report writeResult)
    (cond
      ((null outPath)
        (return (pvtErr 'pvt_validation
                  "pvtMeasurePull: ?outPath is required" nil))))

    (setq effectiveTest (or testName _PVT_MEASURE_DEFAULT_TEST))

    (setq sessResult (_pvtMeasureResolveSession sess))
    (cond
      ((pvtIsErr sessResult) (return sessResult)))
    (setq resolvedSess (pvtUnwrap sessResult))

    (setq tmpCsv (_pvtMeasureUniqueTmpPath))
    ;; Ref: axlOutputsExportToFile adexlSKILLref.pdf p.249.
    ;; We keep the Test column (do not pass ?omitTestCol t) because we
    ;; filter on Test downstream.
    (setq exportRv (errset (axlOutputsExportToFile resolvedSess tmpCsv) nil))
    (cond
      ((or (null exportRv) (null (car exportRv)))
        (return (pvtErr 'pvt_io
                  (strcat "axlOutputsExportToFile returned nil for " tmpCsv)
                  tmpCsv))))

    (setq readResult (_pvtMeasureReadCsvLines tmpCsv))
    (cond
      ((pvtIsErr readResult) (return readResult)))
    (setq lines (pvtUnwrap readResult))

    ;; Drop the header row (first line). If the file is empty, that's an error.
    (cond
      ((null lines)
        (return (pvtErr 'pvt_validation
                  (strcat "exported CSV is empty: " tmpCsv) tmpCsv))))
    (setq firstLine (car lines))
    (setq dataLines (cdr lines))
    ;; Sanity-check the header has 7 columns. If not, the CSV format moved
    ;; under us and the rest of the parser will misbehave.
    (setq parseResult (_pvtMeasureParseCsvLine firstLine))
    (cond
      ((pvtIsErr parseResult)
        (return (pvtErr 'pvt_validation
                  (strcat "unexpected CSV header shape in " tmpCsv ": " firstLine)
                  tmpCsv))))

    (setq snapshotRows nil)
    (foreach line dataLines
      (cond
        ((or (null line) (equal line "")) nil)
        (t
          (setq parseResult (_pvtMeasureParseCsvLine line))
          (cond
            ((pvtIsErr parseResult)
              ;; Re-raise with the offending file path attached so callers
              ;; can locate the bad line in /tmp.
              (return parseResult)))
          (setq fields (pvtUnwrap parseResult))
          (cond
            ((_pvtMeasureKeepRowP fields effectiveTest includeSignals)
              (setq snapshotRows
                (cons (_pvtMeasureBuildSnapshotRow fields) snapshotRows)))))))
    (setq snapshotRows (reverse snapshotRows))

    (setq envelopeAndReport
      (_pvtMeasureBuildSnapshotEnvelope resolvedSess effectiveTest
                                        snapshotRows outPath))
    (setq envelope (nthelem 1 envelopeAndReport))
    (setq report   (nthelem 2 envelopeAndReport))

    (setq writeResult (_pvtMeasureWriteOutput envelope outPath))
    (cond
      ((pvtIsErr writeResult) (return writeResult)))

    (return (pvtOk report))
  )
)


t  ; load-time return value
