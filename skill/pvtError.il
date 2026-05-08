;; ============================================================================
;; pvtError.il
;;
;; Discriminated-result error model for the .pvtproject SKILL loader.
;; Mirrors the intent of python/simkit/project.py exception hierarchy
;; without leaking SKILL stack-unwinding behaviour into normal control flow.
;;
;; Result shape:
;;   :ok   -> list('pvt_ok  value)
;;   :err  -> list('pvt_err category msg sourcePathOrNil)
;;
;; Categories:
;;   'pvt_notFound    -> Python PvtProjectNotFoundError
;;   'pvt_validation  -> Python PvtProjectValidationError
;;   'pvt_json        -> raw JSON parse failure
;;   'pvt_io          -> file read error
;;
;; By convention :json and :io categories may be bubbled up to callers and
;; collapsed to :validation by `pvtErrToValidation` to mirror Python's
;; "JSON / OSError -> PvtProjectValidationError" wrapping behaviour.
;;
;; Authoring notes (SKILL-on-ICADVM18.1):
;;   - Built on plain SKILL lists (cons cells), no defstruct, so callers
;;     can pattern-match with car/cadr/caddr without dragging defstruct
;;     accessor helpers into hot paths.
;;   - All Cadence functions used here are language-core only; no db*/hi*
;;     dependency, so this file is safe to load in -nograph batch mode.
;; ============================================================================

;; Sentinel head symbols. Using namespaced symbols (with a colon in the print
;; name) avoids collisions with any global symbol in a host .cdsinit.
;; Reference: sklangref.pdf p.480 (boundp / symbol semantics).

(defvar PVT_OK_HEAD  'pvt_ok)
(defvar PVT_ERR_HEAD 'pvt_err)


;; ----------------------------------------------------------------------------
;; Constructors
;; ----------------------------------------------------------------------------

(procedure (pvtOk value)
  "Wrap a successful value into the discriminated-result form."
  (list PVT_OK_HEAD value)
)

(procedure (pvtErr category msg @optional (source nil))
  "Wrap an error into the discriminated-result form.
   `category` must be one of 'pvt_notFound 'pvt_validation 'pvt_json 'pvt_io.
   `msg` is a human-readable string; `source` is the .pvtproject path or nil."
  (list PVT_ERR_HEAD category msg source)
)


;; ----------------------------------------------------------------------------
;; Predicates / accessors
;; ----------------------------------------------------------------------------

(procedure (pvtIsOk result)
  "t iff `result` is a successful discriminated-result value."
  (and (listp result) (equal (car result) PVT_OK_HEAD))
)

(procedure (pvtIsErr result)
  "t iff `result` is an error discriminated-result value."
  (and (listp result) (equal (car result) PVT_ERR_HEAD))
)

;; nthelem (sklangref.pdf p.53) is 1-indexed; we use it instead of the
;; cadr/caddr/cadddr accessors because classic SKILL on ICADVM18.1 only
;; documents car/cdr/cddddr/nth/nthelem in the list-functions section.

(procedure (pvtUnwrap result)
  "Extract the wrapped value. Caller must ensure pvtIsOk(result)."
  (unless (pvtIsOk result)
    (error "pvtUnwrap: expected :ok result, got %L" result))
  (nthelem 2 result)
)

(procedure (pvtErrCategory result)
  "Extract the category symbol from an :err result."
  (unless (pvtIsErr result)
    (error "pvtErrCategory: expected :err result, got %L" result))
  (nthelem 2 result)
)

(procedure (pvtErrMessage result)
  "Extract the human-readable message string from an :err result."
  (unless (pvtIsErr result)
    (error "pvtErrMessage: expected :err result, got %L" result))
  (nthelem 3 result)
)

(procedure (pvtErrSource result)
  "Extract the source-path string (or nil) from an :err result."
  (unless (pvtIsErr result)
    (error "pvtErrSource: expected :err result, got %L" result))
  (nthelem 4 result)
)


;; ----------------------------------------------------------------------------
;; Utilities
;; ----------------------------------------------------------------------------

(procedure (pvtRaise result)
  "If `result` is an :err, signal a SKILL error with a formatted message.
   Used at the API boundary when the caller wants exception-style behaviour
   instead of result-passing.  No-op (returns the wrapped value) on :ok."
  (cond
    ((pvtIsOk result)  (pvtUnwrap result))
    ((pvtIsErr result)
      ;; Format: <category>: <msg> [in <source>]
      (let ((cat (pvtErrCategory result))
            (msg (pvtErrMessage  result))
            (src (pvtErrSource   result)))
        (if src
            (error "%s: %s (in %s)" (_pvtSymToStr cat) msg src)
            (error "%s: %s"          (_pvtSymToStr cat) msg))))
    (t (error "pvtRaise: not a pvt result: %L" result)))
)

(procedure (pvtErrToValidation result)
  "Collapse a :json or :io error into a :validation error.
   Mirrors python/simkit/project.py:_parse_pvtproject which raises
   PvtProjectValidationError for both json.JSONDecodeError and OSError.
   Preserves the original message verbatim."
  (cond
    ((pvtIsOk result) result)
    ((pvtIsErr result)
      (let ((cat (pvtErrCategory result)))
        (if (or (equal cat 'pvt_json) (equal cat 'pvt_io))
            (pvtErr 'pvt_validation
                    (pvtErrMessage result)
                    (pvtErrSource  result))
            result)))
    (t (error "pvtErrToValidation: not a pvt result: %L" result)))
)


;; Helper: stringify a symbol for use inside error messages.
;; SKILL has no built-in _pvtSymToStr; symbolp(s) and the print-name path is
;; via `get_pname` or `symbolp + concat`.  `strcat` accepts symbol args and
;; concatenates them by their print name (sklangref.pdf p.215).
(procedure (_pvtSymToStr sym)
  "Return the print-name of a symbol as a string."
  (cond
    ((stringp sym) sym)
    ((symbolp sym) (strcat sym))
    (t             (sprintf nil "%L" sym)))
)

t  ; load-time return value
