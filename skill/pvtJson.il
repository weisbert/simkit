;; ============================================================================
;; pvtJson.il
;;
;; Strict JSON parser for the .pvtproject SKILL loader. Recursive descent.
;; Mirrors the *intent* of Python's stdlib `json.loads` but is independent
;; (red-zone target has no JSON-capable Cadence module).
;;
;; SKILL note: every procedure that uses `(return ...)` for early exit MUST
;; have its body wrapped in `(prog () ...)`, because in classic SKILL `return`
;; only escapes the enclosing `prog` form (sklangref.pdf p.379), not the
;; surrounding `procedure`. We adopt the convention "every procedure body is
;; a prog block" throughout this file. The final form of each prog uses an
;; explicit `(return <expr>)` so the procedure's value is well-defined.
;;
;; Design summary
;; --------------
;; * State is a mutable defstruct `pvtJsonState` carrying { text, pos, len,
;;   depth }.  pos is 1-based to match SKILL substring conventions
;;   (sklangref.pdf p.223).
;; * Strings are scanned character-by-character; we slice the lexeme via
;;   substring before any atoi/atof call to avoid the trailing-garbage trap
;;   documented at sklangref.pdf p.230 (atoi("123abc") => 123).
;; * Numbers distinguish integers (fixnum) from floats (flonum). Detect
;;   overflow by manual digit accumulation against INT_MAX = 2147483647.
;; * Booleans / null are NOT mapped to native t/nil because the validator
;;   layer must distinguish "JSON null" from "absent key" and "JSON true/false"
;;   from a numeric 1/0.  We use sentinel symbols:
;;       'pvt_json_true  'pvt_json_false  'pvt_json_null
;; * Objects are makeTable values (sklangref.pdf p.84) with default
;;   'pvt_absent so validators can distinguish "missing" from "null".
;;   The parser also stashes the insertion-order key list under a private
;;   sentinel slot so callers can iterate keys (makeTable has no built-in
;;   key enumeration).
;; * Arrays are SKILL lists (cons cells).
;; * Hard depth cap of 64 to bound stack usage.
;; ============================================================================

;; --- depth cap ----------------------------------------------------------
(defvar PVT_JSON_MAX_DEPTH 64)

;; --- INT_MAX for 32-bit SKILL fixnum (sklangref defMathConstants p.232) -
(defvar PVT_INT_MAX 2147483647)

;; --- sentinel symbols emitted by the parser ----------------------------
(defvar PVT_JSON_TRUE  'pvt_json_true)
(defvar PVT_JSON_FALSE 'pvt_json_false)
(defvar PVT_JSON_NULL  'pvt_json_null)
;; Default value stored in object tables for "key is absent" — distinct from
;; PVT_JSON_NULL which means "key is present with explicit JSON null".
(defvar PVT_JSON_ABSENT 'pvt_absent)

;; --- emitter-only sentinels for empty array / empty object --------------
;; SKILL `nil` represents both "the empty list" and "no value", so the emitter
;; cannot tell `[]` from `null` from a bare nil. Builders that need to emit a
;; literal empty `[]` or `{}` (e.g. the collector's run.json `artifacts: []`
;; when no artifacts were captured) must use these sentinels instead of nil.
;; The parser does NOT produce them — by parser convention `[]` parses to nil
;; — so they exist purely as a contract for emit-side callers.
(defvar PVT_JSON_EMPTY_ARRAY  'pvt_json_empty_array)
(defvar PVT_JSON_EMPTY_OBJECT 'pvt_json_empty_object)

;; Private sidecar slot that stores the insertion-order key list on each
;; parsed object table. Not meant to be read by anything other than
;; pvtJsonObjectKeys below.
(defvar _PVT_JSON_KEYS_PROP 'pvt_objKeys)


;; ----------------------------------------------------------------------------
;; Parser state
;; Ref: defstruct sklangref.pdf p.80. Slots are mutable via the
;;      `state->slot = value` arrow syntax.
;; ----------------------------------------------------------------------------

(defstruct pvtJsonState text pos len depth)


;; ----------------------------------------------------------------------------
;; Predicates exposed to validators
;; ----------------------------------------------------------------------------

(procedure (pvtJsonObjectp x)  (tablep x))
(procedure (pvtJsonNullp   x)  (equal x PVT_JSON_NULL))
(procedure (pvtJsonTruep   x)  (equal x PVT_JSON_TRUE))
(procedure (pvtJsonFalsep  x)  (equal x PVT_JSON_FALSE))
(procedure (pvtJsonBoolp   x)  (or (pvtJsonTruep x) (pvtJsonFalsep x)))
(procedure (pvtJsonAbsentp x)  (equal x PVT_JSON_ABSENT))
(procedure (pvtJsonEmptyArrayp  x) (equal x PVT_JSON_EMPTY_ARRAY))
(procedure (pvtJsonEmptyObjectp x) (equal x PVT_JSON_EMPTY_OBJECT))


;; ----------------------------------------------------------------------------
;; Low-level state accessors (1-based pos to match substring())
;; ----------------------------------------------------------------------------

(procedure (_pvtJsonAtEnd state)
  (greaterp state->pos state->len)
)

(procedure (_pvtJsonPeek state)
  (cond
    ((_pvtJsonAtEnd state) nil)
    (t (substring state->text state->pos 1)))
)

(procedure (_pvtJsonAdvance state)
  (state->pos = (plus state->pos 1))
  state
)

(procedure (_pvtJsonRemainingChars state)
  (plus 1 (difference state->len state->pos))
)


;; ----------------------------------------------------------------------------
;; Error helper: build a :err result with a 20-char window around pos.
;; ----------------------------------------------------------------------------

(procedure (_pvtJsonError state msg)
  (let ((around (_pvtJsonContextWindow state)))
    (pvtErr 'pvt_json
            (sprintf nil "JSON parse error at byte %d: %s near %L"
                     state->pos msg around)
            nil))
)

(procedure (_pvtJsonContextWindow state)
  (let ((start (max 1 (difference state->pos 10)))
        (avail nil))
    (setq avail (plus 1 (difference state->len start)))
    (when (greaterp avail 20) (setq avail 20))
    (cond
      ((lessp avail 1) "")
      (t (substring state->text start avail))))
)


;; ----------------------------------------------------------------------------
;; Whitespace skipper
;; JSON whitespace: space, tab, LF, CR (RFC 8259 §2)
;; ----------------------------------------------------------------------------

(procedure (_pvtJsonSkipWs state)
  (prog ()
    (while (not (_pvtJsonAtEnd state))
      (let ((ch (_pvtJsonPeek state)))
        (cond
          ((or (equal ch " ") (equal ch "\t")
               (equal ch "\n") (equal ch "\r"))
            (_pvtJsonAdvance state))
          (t (return state)))))
    (return state))
)


;; ----------------------------------------------------------------------------
;; Public API
;; ----------------------------------------------------------------------------

(procedure (pvtJsonParseString text)
  (prog (state parsed val)
    (cond
      ((not (stringp text))
        (return (pvtErr 'pvt_json
                        "pvtJsonParseString: input must be a string"
                        nil))))
    (setq state (make_pvtJsonState ?text text ?pos 1
                                   ?len (strlen text) ?depth 0))
    (_pvtJsonSkipWs state)
    (cond
      ((_pvtJsonAtEnd state)
        (return (pvtErr 'pvt_json "empty JSON document" nil))))
    (setq parsed (_pvtJsonParseValue state))
    (cond
      ((pvtIsErr parsed) (return parsed)))
    (setq val (pvtUnwrap parsed))
    (_pvtJsonSkipWs state)
    (cond
      ((not (_pvtJsonAtEnd state))
        (return (_pvtJsonError state "trailing characters after JSON value"))))
    (return (pvtOk val)))
)


(procedure (pvtJsonParseFile path)
  ;; Ref: unwindProtect sklangref.pdf p.606 — guarantees `close` even on
  ;; abnormal exit. infile (p.423) returns nil if the file cannot be opened.
  (prog (port text line result)
    (setq text "")
    (cond
      ((not (stringp path))
        (return (pvtErr 'pvt_io
                        "pvtJsonParseFile: path must be a string"
                        nil))))
    (setq port (infile path))
    (cond
      ((null port)
        (return (pvtErr 'pvt_io
                        (sprintf nil "cannot open file %L for reading" path)
                        path))))
    (unwindProtect
      (progn
        ;; gets stops at newline and KEEPS the newline char (sklangref p.420),
        ;; so concatenation reproduces the file verbatim.
        (while (gets line port)
          (setq text (strcat text line)))
        (setq result (pvtJsonParseString text))
        (cond
          ((pvtIsErr result)
            (setq result
                  (pvtErr (pvtErrCategory result)
                          (pvtErrMessage  result)
                          path)))))
      ;; cleanup form
      (close port))
    (return result))
)


;; ----------------------------------------------------------------------------
;; Recursive descent — value dispatcher
;; ----------------------------------------------------------------------------

(procedure (_pvtJsonParseValue state)
  (prog (ch)
    (cond
      ((_pvtJsonAtEnd state)
        (return (_pvtJsonError state "unexpected end of input"))))
    (cond
      ((greaterp state->depth PVT_JSON_MAX_DEPTH)
        (return (_pvtJsonError state
                               (sprintf nil "max nesting depth %d exceeded"
                                        PVT_JSON_MAX_DEPTH)))))
    (setq ch (_pvtJsonPeek state))
    (cond
      ((equal ch "{")  (return (_pvtJsonParseObject state)))
      ((equal ch "[")  (return (_pvtJsonParseArray  state)))
      ((equal ch "\"") (return (_pvtJsonParseString_ state)))
      ((equal ch "t")  (return (_pvtJsonParseLiteral state "true"  PVT_JSON_TRUE)))
      ((equal ch "f")  (return (_pvtJsonParseLiteral state "false" PVT_JSON_FALSE)))
      ((equal ch "n")  (return (_pvtJsonParseLiteral state "null"  PVT_JSON_NULL)))
      ((or (equal ch "-") (_pvtJsonIsDigitChar ch))
        (return (_pvtJsonParseNumber state)))
      (t (return (_pvtJsonError state
                                (sprintf nil "unexpected character %L" ch))))))
)


;; ----------------------------------------------------------------------------
;; Literals: true / false / null
;; ----------------------------------------------------------------------------

(procedure (_pvtJsonParseLiteral state expected sentinel)
  (prog (avail need slice)
    (setq avail (_pvtJsonRemainingChars state))
    (setq need  (strlen expected))
    (cond
      ((lessp avail need)
        (return (_pvtJsonError state
                               (sprintf nil "expected literal %L" expected)))))
    (setq slice (substring state->text state->pos need))
    (cond
      ((equal slice expected)
        (state->pos = (plus state->pos need))
        (return (pvtOk sentinel)))
      (t (return (_pvtJsonError state
                                (sprintf nil "expected literal %L, got %L"
                                         expected slice))))))
)


;; ----------------------------------------------------------------------------
;; Numbers
;;
;; Grammar (RFC 8259):
;;   number = [ minus ] int [ frac ] [ exp ]
;;   int    = zero | digit1-9 *DIGIT
;;   frac   = "." 1*DIGIT
;;   exp    = ("e"|"E") ["+"|"-"] 1*DIGIT
;;
;; Strictness rules enforced:
;;   - leading "+" not allowed
;;   - leading zero on multi-digit integer not allowed (e.g. 01)
;;   - bare "." or trailing "." not allowed
;;   - integers and floats remain distinct types in the SKILL output
;; ----------------------------------------------------------------------------

(procedure (_pvtJsonParseNumber state)
  (prog (start isFloat intLexEnd ch next ec sc lex)
    (setq start state->pos)
    ;; optional leading minus
    (cond
      ((equal (_pvtJsonPeek state) "-")
        (_pvtJsonAdvance state)))
    ;; integer part
    (setq ch (_pvtJsonPeek state))
    (cond
      ((null ch)
        (return (_pvtJsonError state "expected digit after '-'")))
      ((equal ch "0")
        (_pvtJsonAdvance state)
        (setq next (_pvtJsonPeek state))
        (cond
          ((and next (_pvtJsonIsDigitChar next))
            (return (_pvtJsonError state "leading zero in integer")))))
      ((_pvtJsonIsDigitChar ch)
        (while (and (not (_pvtJsonAtEnd state))
                    (_pvtJsonIsDigitChar (_pvtJsonPeek state)))
          (_pvtJsonAdvance state)))
      (t (return (_pvtJsonError state "invalid number"))))
    (setq intLexEnd state->pos)
    ;; optional fraction
    (cond
      ((equal (_pvtJsonPeek state) ".")
        (setq isFloat t)
        (_pvtJsonAdvance state)
        (cond
          ((or (_pvtJsonAtEnd state)
               (not (_pvtJsonIsDigitChar (_pvtJsonPeek state))))
            (return (_pvtJsonError state "expected digit after '.'"))))
        (while (and (not (_pvtJsonAtEnd state))
                    (_pvtJsonIsDigitChar (_pvtJsonPeek state)))
          (_pvtJsonAdvance state))))
    ;; optional exponent
    (setq ec (_pvtJsonPeek state))
    (cond
      ((or (equal ec "e") (equal ec "E"))
        (setq isFloat t)
        (_pvtJsonAdvance state)
        (setq sc (_pvtJsonPeek state))
        (cond
          ((or (equal sc "+") (equal sc "-"))
            (_pvtJsonAdvance state)))
        (cond
          ((or (_pvtJsonAtEnd state)
               (not (_pvtJsonIsDigitChar (_pvtJsonPeek state))))
            (return (_pvtJsonError state "expected digit in exponent"))))
        (while (and (not (_pvtJsonAtEnd state))
                    (_pvtJsonIsDigitChar (_pvtJsonPeek state)))
          (_pvtJsonAdvance state))))
    ;; Slice the lexeme. CRITICAL: atoi accepts trailing garbage
    ;; (sklangref.pdf p.230 — atoi("123abc") => 123). We always slice the
    ;; verified lexeme before invoking atoi/atof.
    (setq lex (substring state->text start (difference state->pos start)))
    (cond
      (isFloat
        (let ((f (atof lex)))
          (cond
            ((null f)
              (return (_pvtJsonError state
                                     (sprintf nil "invalid float lexeme %L" lex))))
            (t (return (pvtOk f))))))
      (t
        (let ((intLex (substring state->text start
                                 (difference intLexEnd start))))
          (return (_pvtJsonParseIntegerLexeme state intLex))))))
)

(procedure (_pvtJsonParseIntegerLexeme state lex)
  ;; Manual digit accumulation. Handles a leading '-'. Integer-only at this point.
  (prog (i n sign acc digit overflow)
    (setq i 1)
    (setq sign 1)
    (setq acc 0)
    (setq n (strlen lex))
    (cond
      ((equal (substring lex 1 1) "-")
        (setq sign -1)
        (setq i 2)))
    (while (and (leqp i n) (not overflow))
      (setq digit (atoi (substring lex i 1)))
      (cond
        ((null digit)
          (return (_pvtJsonError state
                                 (sprintf nil "invalid integer lexeme %L" lex))))
        (t
          ;; check overflow before applying: acc*10 + digit > INT_MAX ?
          (cond
            ((greaterp acc (quotient PVT_INT_MAX 10))
              (setq overflow t))
            ((and (equal acc (quotient PVT_INT_MAX 10))
                  (greaterp digit (remainder PVT_INT_MAX 10)))
              (setq overflow t))
            (t
              (setq acc (plus (times acc 10) digit))
              (setq i (plus i 1)))))))
    (cond
      (overflow
        (return (_pvtJsonError state
                               (sprintf nil "integer literal %L overflows %d"
                                        lex PVT_INT_MAX))))
      (t (return (pvtOk (times sign acc))))))
)

(procedure (_pvtJsonIsDigitChar ch)
  (and (stringp ch)
       (equal (strlen ch) 1)
       (member ch '("0" "1" "2" "3" "4" "5" "6" "7" "8" "9")))
)


;; ----------------------------------------------------------------------------
;; Strings (internal: dispatcher entry point on '"').
;; The external entry point is pvtJsonParseString (no underscore).
;; ----------------------------------------------------------------------------

(procedure (_pvtJsonParseString_ state)
  (prog (parts done errResult ch esc)
    (_pvtJsonAdvance state)              ; consume opening quote
    (while (and (not done) (not errResult))
      (cond
        ((_pvtJsonAtEnd state)
          (setq errResult (_pvtJsonError state "unterminated string")))
        (t
          (setq ch (_pvtJsonPeek state))
          (cond
            ((equal ch "\"")
              (_pvtJsonAdvance state)
              (setq done t))
            ((equal ch "\\")
              (setq esc (_pvtJsonParseEscape state))
              (cond
                ((pvtIsErr esc) (setq errResult esc))
                (t (setq parts (cons (pvtUnwrap esc) parts)))))
            ;; Reject raw control chars 0x00-0x1F per RFC 8259.
            ((and (equal (strlen ch) 1)
                  (lessp (_pvtJsonCharCode ch) 32))
              (setq errResult
                    (_pvtJsonError state "raw control character in string")))
            (t
              (setq parts (cons ch parts))
              (_pvtJsonAdvance state))))))
    (cond
      (errResult (return errResult))
      (t (return (pvtOk (_pvtJsonReverseConcat parts))))))
)

(procedure (_pvtJsonReverseConcat parts)
  ;; parts is a reverse-order list of single-char (or escape-product) strings.
  ;; reverse + foreach + strcat is O(n) total. (sklangref p.55, p.215.)
  (let ((out "") (lst (reverse parts)))
    (foreach piece lst (setq out (strcat out piece)))
    out)
)

(procedure (_pvtJsonCharCode ch)
  ;; Best-effort char-to-codepoint for ASCII control detection (0..31).
  (cond
    ((equal ch "\n") 10)
    ((equal ch "\r") 13)
    ((equal ch "\t") 9)
    ((equal ch "\b") 8)
    ((equal ch "\f") 12)
    (t 32))
)


;; ----------------------------------------------------------------------------
;; String escapes
;; ----------------------------------------------------------------------------

(procedure (_pvtJsonParseEscape state)
  (prog (ch)
    (_pvtJsonAdvance state)            ; consume the backslash
    (cond
      ((_pvtJsonAtEnd state)
        (return (_pvtJsonError state "unterminated escape sequence"))))
    (setq ch (_pvtJsonPeek state))
    (cond
      ((equal ch "\"") (_pvtJsonAdvance state) (return (pvtOk "\"")))
      ((equal ch "\\") (_pvtJsonAdvance state) (return (pvtOk "\\")))
      ((equal ch "/")  (_pvtJsonAdvance state) (return (pvtOk "/")))
      ((equal ch "b")  (_pvtJsonAdvance state) (return (pvtOk "\b")))
      ((equal ch "f")  (_pvtJsonAdvance state) (return (pvtOk "\f")))
      ((equal ch "n")  (_pvtJsonAdvance state) (return (pvtOk "\n")))
      ((equal ch "r")  (_pvtJsonAdvance state) (return (pvtOk "\r")))
      ((equal ch "t")  (_pvtJsonAdvance state) (return (pvtOk "\t")))
      ((equal ch "u")  (return (_pvtJsonParseUnicodeEscape state)))
      (t (return (_pvtJsonError state
                                (sprintf nil "invalid escape \\%s" ch))))))
)

(procedure (_pvtJsonParseUnicodeEscape state)
  ;; State points at 'u'. Consume it then read exactly 4 hex digits.
  (prog (hex cp)
    (_pvtJsonAdvance state)
    (cond
      ((lessp (_pvtJsonRemainingChars state) 4)
        (return (_pvtJsonError state "incomplete \\u escape"))))
    (setq hex (substring state->text state->pos 4))
    (cond
      ((not (_pvtJsonIsHex4 hex))
        (return (_pvtJsonError state
                               (sprintf nil "invalid hex in \\u escape: %L" hex)))))
    (setq cp (_pvtJsonHex4ToInt hex))
    (state->pos = (plus state->pos 4))
    (cond
      ;; Lone surrogate -> error (we do not implement surrogate-pair joining).
      ((and (geqp cp 0xD800) (leqp cp 0xDFFF))
        (return (_pvtJsonError state
                               (sprintf nil "lone UTF-16 surrogate U+%x in string" cp))))
      ;; ASCII fast path
      ((leqp cp 0x7F)
        (return (pvtOk (_pvtJsonByteString cp))))
      ;; Two-byte UTF-8: U+0080..U+07FF
      ((leqp cp 0x7FF)
        (let ((b1 (plus 0xC0 (quotient cp 64)))
              (b2 (plus 0x80 (remainder cp 64))))
          (return (pvtOk (strcat (_pvtJsonByteString b1)
                                 (_pvtJsonByteString b2))))))
      ;; Three-byte UTF-8: U+0800..U+FFFF (excl. surrogate range handled above)
      (t
        (let ((b1 (plus 0xE0 (quotient cp 4096)))
              (b2 (plus 0x80 (remainder (quotient cp 64) 64)))
              (b3 (plus 0x80 (remainder cp 64))))
          (return (pvtOk (strcat (_pvtJsonByteString b1)
                                 (_pvtJsonByteString b2)
                                 (_pvtJsonByteString b3))))))))
)

(procedure (_pvtJsonIsHex4 s)
  (and (stringp s)
       (equal (strlen s) 4)
       (pcreMatchp "^[0-9A-Fa-f]{4}$" s))
)

(procedure (_pvtJsonHex4ToInt s)
  ;; Manual 4-digit hex parse — atoi does not accept hex notation.
  (let ((acc 0) (i 1) (n (strlen s)) (c nil) (d nil))
    (while (leqp i n)
      (setq c (substring s i 1))
      (setq d
            (cond
              ((member c '("0" "1" "2" "3" "4" "5" "6" "7" "8" "9"))
                (atoi c))
              ((member c '("a" "b" "c" "d" "e" "f"))
                (plus 10 (difference (_pvtJsonAsciiOrd c)
                                     (_pvtJsonAsciiOrd "a"))))
              (t
                (plus 10 (difference (_pvtJsonAsciiOrd c)
                                     (_pvtJsonAsciiOrd "A"))))))
      (setq acc (plus (times acc 16) d))
      (setq i (plus i 1)))
    acc)
)

(procedure (_pvtJsonAsciiOrd c)
  ;; Ord() for ASCII hex digits only. Sufficient for the hex parser above.
  (cond
    ((equal c "0") 48) ((equal c "1") 49) ((equal c "2") 50) ((equal c "3") 51)
    ((equal c "4") 52) ((equal c "5") 53) ((equal c "6") 54) ((equal c "7") 55)
    ((equal c "8") 56) ((equal c "9") 57)
    ((equal c "a") 97) ((equal c "b") 98) ((equal c "c") 99) ((equal c "d") 100)
    ((equal c "e") 101) ((equal c "f") 102)
    ((equal c "A") 65) ((equal c "B") 66) ((equal c "C") 67) ((equal c "D") 68)
    ((equal c "E") 69) ((equal c "F") 70)
    (t 0))
)

;; Byte lookup table covering codes 1..255 (code 0 is excluded because classic
;; SKILL strings are NUL-terminated and embedding a NUL byte would truncate).
;; Indexed as `(substring _PVT_BYTE_LUT code 1)` — note 1-based, code N at
;; position N. Required because classic SKILL has no int->char primitive:
;;   - sprintf "%c" expects a STRING (not int) on this Cadence (verified live)
;;   - intChar / asciiChar / charString are all undefined here.
;; Authoring note: the literal is one big string of 3-digit octal escapes,
;; produced once by an offline script. Each \nnn is parsed by the SKILL string
;; reader into a single byte at load time.
(defvar _PVT_BYTE_LUT
  "\001\002\003\004\005\006\007\010\011\012\013\014\015\016\017\020\021\022\023\024\025\026\027\030\031\032\033\034\035\036\037\040\041\042\043\044\045\046\047\050\051\052\053\054\055\056\057\060\061\062\063\064\065\066\067\070\071\072\073\074\075\076\077\100\101\102\103\104\105\106\107\110\111\112\113\114\115\116\117\120\121\122\123\124\125\126\127\130\131\132\133\134\135\136\137\140\141\142\143\144\145\146\147\150\151\152\153\154\155\156\157\160\161\162\163\164\165\166\167\170\171\172\173\174\175\176\177\200\201\202\203\204\205\206\207\210\211\212\213\214\215\216\217\220\221\222\223\224\225\226\227\230\231\232\233\234\235\236\237\240\241\242\243\244\245\246\247\250\251\252\253\254\255\256\257\260\261\262\263\264\265\266\267\270\271\272\273\274\275\276\277\300\301\302\303\304\305\306\307\310\311\312\313\314\315\316\317\320\321\322\323\324\325\326\327\330\331\332\333\334\335\336\337\340\341\342\343\344\345\346\347\350\351\352\353\354\355\356\357\360\361\362\363\364\365\366\367\370\371\372\373\374\375\376\377")

(procedure (_pvtJsonByteString code)
  "Build a one-byte string from `code` in [1..255] using a precomputed LUT."
  (substring _PVT_BYTE_LUT code 1)
)


;; ----------------------------------------------------------------------------
;; Arrays
;; ----------------------------------------------------------------------------

(procedure (_pvtJsonParseArray state)
  (prog (acc errResult done elt ch)
    (_pvtJsonAdvance state)              ; consume '['
    (state->depth = (plus state->depth 1))
    (_pvtJsonSkipWs state)
    ;; empty array fast path
    (cond
      ((equal (_pvtJsonPeek state) "]")
        (_pvtJsonAdvance state)
        (state->depth = (difference state->depth 1))
        (return (pvtOk nil))))
    (while (and (not done) (not errResult))
      (setq elt (_pvtJsonParseValue state))
      (cond
        ((pvtIsErr elt) (setq errResult elt))
        (t
          (setq acc (cons (pvtUnwrap elt) acc))
          (_pvtJsonSkipWs state)
          (setq ch (_pvtJsonPeek state))
          (cond
            ((equal ch ",")
              (_pvtJsonAdvance state)
              (_pvtJsonSkipWs state)
              ;; Reject trailing comma: next must NOT be ']'.
              (cond
                ((equal (_pvtJsonPeek state) "]")
                  (setq errResult
                        (_pvtJsonError state "trailing comma in array")))))
            ((equal ch "]")
              (_pvtJsonAdvance state)
              (setq done t))
            (t
              (setq errResult
                    (_pvtJsonError state "expected ',' or ']' in array")))))))
    (cond
      (errResult (return errResult))
      (t (state->depth = (difference state->depth 1))
         (return (pvtOk (reverse acc))))))
)


;; ----------------------------------------------------------------------------
;; Objects (records insertion-order key list in a private sidecar slot).
;; ----------------------------------------------------------------------------

(procedure (_pvtJsonParseObject state)
  (prog (tab keyOrder errResult done keyR key valR ch)
    (setq tab (makeTable 'pvtJsonObj PVT_JSON_ABSENT))
    (_pvtJsonAdvance state)              ; consume '{'
    (state->depth = (plus state->depth 1))
    (_pvtJsonSkipWs state)
    ;; empty-object fast path
    (cond
      ((equal (_pvtJsonPeek state) "}")
        (_pvtJsonAdvance state)
        (state->depth = (difference state->depth 1))
        (setarray tab _PVT_JSON_KEYS_PROP nil)
        (return (pvtOk tab))))
    (while (and (not done) (not errResult))
      (cond
        ((not (equal (_pvtJsonPeek state) "\""))
          (setq errResult
                (_pvtJsonError state "expected string key in object")))
        (t
          (setq keyR (_pvtJsonParseString_ state))
          (cond
            ((pvtIsErr keyR) (setq errResult keyR))
            (t
              (setq key (pvtUnwrap keyR))
              (_pvtJsonSkipWs state)
              (cond
                ((not (equal (_pvtJsonPeek state) ":"))
                  (setq errResult
                        (_pvtJsonError state "expected ':' after object key")))
                (t
                  (_pvtJsonAdvance state)
                  (_pvtJsonSkipWs state)
                  (setq valR (_pvtJsonParseValue state))
                  (cond
                    ((pvtIsErr valR) (setq errResult valR))
                    (t
                      (setarray tab key (pvtUnwrap valR))
                      (setq keyOrder (cons key keyOrder))
                      (_pvtJsonSkipWs state)
                      (setq ch (_pvtJsonPeek state))
                      (cond
                        ((equal ch ",")
                          (_pvtJsonAdvance state)
                          (_pvtJsonSkipWs state)
                          (cond
                            ((equal (_pvtJsonPeek state) "}")
                              (setq errResult
                                    (_pvtJsonError state
                                                   "trailing comma in object")))))
                        ((equal ch "}")
                          (_pvtJsonAdvance state)
                          (setq done t))
                        (t
                          (setq errResult
                                (_pvtJsonError state
                                               "expected ',' or '}' in object")))))))))))))
    (cond
      (errResult (return errResult))
      (t (state->depth = (difference state->depth 1))
         (setarray tab _PVT_JSON_KEYS_PROP (reverse keyOrder))
         (return (pvtOk tab)))))
)


;; ----------------------------------------------------------------------------
;; Public: enumerate object keys in insertion order.
;; Reads the sidecar list stashed by _pvtJsonParseObject above.
;; ----------------------------------------------------------------------------

(procedure (pvtJsonObjectKeys obj)
  (cond
    ((not (tablep obj)) nil)
    (t (let ((kl (arrayref obj _PVT_JSON_KEYS_PROP)))
         (cond
           ((or (null kl) (pvtJsonAbsentp kl)) nil)
           (t kl)))))
)


;; ============================================================================
;; JSON emitter (writer)
;;
;; Inverse of the parser above, with the same value family:
;;
;;   numbers   -> int (printed via %d) or float (%g, ".0" appended if neither
;;               '.' nor 'e' is present so the round-trip stays a float)
;;   strings   -> "..."  with backslash, quote, and 0x00..0x1F escapes
;;   nil       -> "null"  (parser also emits nil for the empty array, so an
;;                empty array / nil / null all collapse to the JSON "null"
;;                token in the emitter — see ROUND-TRIP CAVEAT below)
;;   t         -> "true"  (alias for the PVT_JSON_TRUE sentinel)
;;   PVT_JSON_TRUE  / 'pvt_json_true   -> "true"
;;   PVT_JSON_FALSE / 'pvt_json_false  -> "false"
;;   PVT_JSON_NULL  / 'pvt_json_null   -> "null"
;;   tablep    -> "{...}"  (object). Key order comes from the sidecar
;;               _PVT_JSON_KEYS_PROP list set by _pvtJsonParseObject; for
;;               freshly-built tables (no sidecar) we fall back to arrayref
;;               of every key returned by getSGkeys-equivalent walking, but
;;               in practice all callers route through pvtJsonParseFile so
;;               the sidecar is present.
;;   listp     -> "[...]" (proper list — array)
;;
;; ROUND-TRIP CAVEAT.  The parser emits SKILL `nil` for both JSON `null` and
;; the empty array `[]`. The emitter therefore picks one canonical mapping —
;; we emit `nil` as `null`. Concretely:
;;
;;     parse "[]"     -> nil   ;  emit nil   -> "null"   (NOT "[]")
;;     parse "null"   -> 'pvt_json_null  (sentinel) -> emit "null"
;;
;; If you want round-trip identity for empty arrays, parse with an
;; empty-array sentinel layer above this module. Builders (e.g. the
;; collector) that need to emit a literal `[]` or `{}` distinct from `null`
;; use the PVT_JSON_EMPTY_ARRAY / PVT_JSON_EMPTY_OBJECT sentinels declared at
;; the top of this file.
;;
;; ESCAPE / BYTE-SYNTHESIS ASYMMETRY.  The parser side decodes \uXXXX
;; sequences into UTF-8 bytes via the precomputed _PVT_BYTE_LUT (see
;; DECISIONS.md #15) because classic SKILL has no integer->char primitive.
;; The emitter side does NOT need byte synthesis — it only needs to ESCAPE
;; bytes the parser already produced.  For control characters we therefore
;; just sprintf "\\u%04x" against the integer code; no LUT lookup needed.
;; This asymmetry is intentional.
;; ============================================================================

;; Public entry point: emit to a fresh outstring port and return the string.
;; Reuses pvtJsonEmitToPort so there is exactly one writer code path.
(procedure (pvtJsonEmit value)
  (let ((port (outstring)) (out nil))
    (pvtJsonEmitToPort value port)
    (setq out (getOutstring port))
    (close port)
    out)
)

;; Public entry point: stream the JSON form of `value` to an open output
;; port. Used by the collector to write multi-MB run.json files without
;; first building a giant in-memory string. Returns t on success.
(procedure (pvtJsonEmitToPort value port)
  (_pvtJsonEmitValue value port)
  t
)

;; --- internal recursive walker --------------------------------------------

(procedure (_pvtJsonEmitValue value port)
  (prog ()
    (cond
      ;; Sentinels and t/nil first — they would otherwise be misclassified
      ;; (e.g. 'pvt_json_true is a symbol, not a list, but symbolp matches
      ;; both t and nil too on classic SKILL, so we test by `equal`).
      ((equal value PVT_JSON_EMPTY_ARRAY)  (fprintf port "[]") (return t))
      ((equal value PVT_JSON_EMPTY_OBJECT) (fprintf port "{}") (return t))
      ((equal value PVT_JSON_NULL)   (fprintf port "null")  (return t))
      ((equal value PVT_JSON_TRUE)   (fprintf port "true")  (return t))
      ((equal value PVT_JSON_FALSE)  (fprintf port "false") (return t))
      ((null value)                  (fprintf port "null")  (return t))
      ((equal value t)               (fprintf port "true")  (return t))
      ;; tablep BEFORE listp because both can match the same value in
      ;; principle on some Cadence builds; the parser-produced object is
      ;; always tablep, the parser-produced array is never tablep.
      ((tablep value)                (_pvtJsonEmitObject value port) (return t))
      ((listp value)                 (_pvtJsonEmitArray  value port) (return t))
      ((stringp value)               (_pvtJsonEmitString value port) (return t))
      ((integerp value)              (fprintf port "%d" value) (return t))
      ((floatp value)                (_pvtJsonEmitFloat  value port) (return t))
      (t
        ;; Unknown type. We don't raise — the emitter is best-effort and
        ;; the caller path is "build a string for output". Falling back to
        ;; the symbol's print name as a JSON string keeps the output
        ;; lexically valid; the test harness will fail the round-trip
        ;; assertion if anything weird sneaks in, surfacing the bug.
        (_pvtJsonEmitString (sprintf nil "%L" value) port)
        (return t))))
)

;; --- floats ---------------------------------------------------------------
;; %g format strips trailing ".0" from integer-valued floats (verified live:
;; sprintf "%g" 150.0 -> "150"). For the parser to reread the result as a
;; float instead of an integer, we re-add a ".0" when the formatted text
;; contains neither '.' nor 'e'/'E'.

(procedure (_pvtJsonEmitFloat f port)
  (let ((s (sprintf nil "%g" f)))
    (cond
      ((_pvtJsonStringContainsAny s '("." "e" "E"))
        (fprintf port "%s" s))
      (t
        (fprintf port "%s.0" s))))
)

(procedure (_pvtJsonStringContainsAny s charlist)
  ;; Returns t iff `s` contains any of the single-char strings in charlist.
  ;; Linear scan; charlist is short (3 elements above) so this is fine.
  (prog (i n found)
    (setq i 1)
    (setq n (strlen s))
    (while (and (leqp i n) (not found))
      (let ((c (substring s i 1)))
        (when (member c charlist) (setq found t)))
      (setq i (plus i 1)))
    (return found))
)

;; --- arrays ---------------------------------------------------------------

(procedure (_pvtJsonEmitArray lst port)
  (cond
    ((null lst) (fprintf port "[]"))
    (t
      (fprintf port "[")
      (_pvtJsonEmitValue (car lst) port)
      (foreach elt (cdr lst)
        (fprintf port ",")
        (_pvtJsonEmitValue elt port))
      (fprintf port "]")))
)

;; --- objects --------------------------------------------------------------
;; Iteration uses the sidecar key list set by _pvtJsonParseObject. For an
;; empty parsed object the sidecar is `nil` (set explicitly in the parser);
;; for non-empty parsed objects it is a non-empty list of keys in insertion
;; order. We probe the sidecar slot DIRECTLY here (rather than via
;; pvtJsonObjectKeys, which collapses both "empty" and "no sidecar at all"
;; to a return value of nil) so we can tell the two apart.
;;
;; Tables built outside the parser (i.e. with no sidecar at all — sidecar
;; slot returns the table's own default sentinel, whatever that is) are
;; emitted as the empty object "{}". This is best-effort: classic SKILL
;; on ICADVM18.1 has no documented public way to enumerate the keys of a
;; makeTable. The simkit emitter has no caller that constructs object
;; values outside the parser; should §3 collector need to do so, it should
;; either build via the parser or set the sidecar explicitly.

(procedure (_pvtJsonEmitObject tab port)
  (prog (sidecar keys first)
    (setq sidecar (arrayref tab _PVT_JSON_KEYS_PROP))
    ;; Sidecar may be: a list (parser-built, possibly empty), the table's
    ;; own default value (table built outside parser), or anything else.
    ;; The parser uses 'pvt_absent as the default, which pvtJsonAbsentp
    ;; recognises; for any other table we can't introspect keys safely.
    (cond
      ((listp sidecar) (setq keys sidecar))
      (t               (setq keys nil)))
    (setq first t)
    (fprintf port "{")
    (foreach key keys
      (cond
        (first (setq first nil))
        (t (fprintf port ",")))
      (_pvtJsonEmitString key port)
      (fprintf port ":")
      (_pvtJsonEmitValue (arrayref tab key) port))
    (fprintf port "}")
    (return t))
)

;; --- strings --------------------------------------------------------------
;; JSON requires escaping " and \ and the control chars 0x00..0x1F (RFC 8259
;; §7). All other bytes (including 0x80..0xFF UTF-8 continuation bytes from
;; multi-byte characters the parser already produced) pass through verbatim.

(procedure (_pvtJsonEmitString s port)
  (prog (i n ch code)
    (fprintf port "\"")
    (setq i 1)
    (setq n (strlen s))
    (while (leqp i n)
      (setq ch (substring s i 1))
      (cond
        ((equal ch "\"") (fprintf port "\\\""))
        ((equal ch "\\") (fprintf port "\\\\"))
        ((equal ch "\b") (fprintf port "\\b"))
        ((equal ch "\f") (fprintf port "\\f"))
        ((equal ch "\n") (fprintf port "\\n"))
        ((equal ch "\r") (fprintf port "\\r"))
        ((equal ch "\t") (fprintf port "\\t"))
        (t
          (setq code (_pvtJsonByteOrd ch))
          (cond
            ((and code (lessp code 32))
              ;; Other control char (e.g. 0x01..0x07, 0x0E..0x1F).
              ;; Emitter-only path — see comment above re asymmetry with the
              ;; parser's LUT.
              (fprintf port "\\u%04x" code))
            (t (fprintf port "%s" ch)))))
      (setq i (plus i 1)))
    (fprintf port "\"")
    (return t))
)

;; Map a single-byte string to its 0..255 code, or nil for "not a 1-byte
;; string we recognize as a control char". Only used by the emitter to
;; decide whether to emit "\uXXXX" — for 0x20..0x7F ASCII we don't care
;; about the code value (the char passes through), so this only needs to
;; be accurate for control chars. We reuse the parser's named-control map
;; and otherwise return 32 as a sentinel meaning "not a control char".
(procedure (_pvtJsonByteOrd ch)
  (cond
    ((or (not (stringp ch)) (not (equal (strlen ch) 1))) nil)
    ((equal ch "\n") 10)
    ((equal ch "\r") 13)
    ((equal ch "\t") 9)
    ((equal ch "\b") 8)
    ((equal ch "\f") 12)
    ;; Catch any remaining bytes that printable-checks won't help with.
    ;; We compare the 1-char string against the LUT to recover its code.
    ;; This is O(255) per char in the worst case but only fires for raw
    ;; control bytes outside the named set above — extremely rare in
    ;; practice. For ASCII printable / multi-byte UTF-8 continuation we
    ;; short-circuit by returning a non-control sentinel.
    (t (_pvtJsonLookupByteCode ch)))
)

(procedure (_pvtJsonLookupByteCode ch)
  ;; Linear search of _PVT_BYTE_LUT for a match. Returns the 1-based
  ;; position (== code, since the LUT layout is "byte N at position N")
  ;; or 32 if not found in the 1..31 control range. We only really need
  ;; to distinguish "0..31 control" from "everything else"; any code >=
  ;; 32 is fine as a non-control sentinel.
  (prog (i)
    (setq i 1)
    (while (leqp i 31)
      (when (equal ch (substring _PVT_BYTE_LUT i 1))
        (return i))
      (setq i (plus i 1)))
    (return 32))
)


t  ; load-time return
