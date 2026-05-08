;; ============================================================================
;; testPvtJson.il -- exercise pvtJson.il in isolation.
;;
;; Each test is a thunk registered via pvtTestRegister at load time.
;; Assertions are made via pvtAssert / pvtAssertOk / pvtAssertErrCategory.
;; ============================================================================

;; --- helpers ---------------------------------------------------------------

(procedure (_pvtTestParseOk text)
  ;; Parse and unwrap, asserting :ok internally.
  (let ((r (pvtJsonParseString text)))
    (cond
      ((pvtIsOk r) (pvtUnwrap r))
      (t
        (error "expected :ok parse of %L, got %L" text r))))
)


;; --- positives -------------------------------------------------------------

(pvtTestRegister "json/empty-object"
  (lambda ()
    (let ((r (pvtJsonParseString "{}")))
      (pvtAssertOk r "parse {}")
      (pvtAssert (pvtJsonObjectp (pvtUnwrap r)) t "{} is an object"))))

(pvtTestRegister "json/empty-array"
  (lambda ()
    (let ((r (pvtJsonParseString "[]")))
      (pvtAssertOk r "parse []")
      (pvtAssert (pvtUnwrap r) nil "[] is empty list"))))

(pvtTestRegister "json/nested-objects"
  (lambda ()
    (let ((r (pvtJsonParseString "{\"a\": {\"b\": {\"c\": 1}}}")))
      (pvtAssertOk r "parse nested-objects")
      (when (pvtIsOk r)
        (let ((val (pvtUnwrap r)))
          (let ((a (arrayref val "a")))
            (let ((b (arrayref a "b")))
              (let ((c (arrayref b "c")))
                (pvtAssert c 1 "deeply nested int")))))))))

(pvtTestRegister "json/escaped-strings"
  (lambda ()
    (let ((r (pvtJsonParseString "\"a\\\"b\\nc\\\\d\"")))
      (pvtAssertOk r "parse escaped-strings")
      (when (pvtIsOk r)
        (let ((val (pvtUnwrap r)))
          (pvtAssert val "a\"b\nc\\d" "escapes decoded"))))))

(pvtTestRegister "json/unicode-escape-bmp"
  (lambda ()
    ;; U+00E9 = é. UTF-8 encoding: 0xC3 0xA9.
    (let ((r (pvtJsonParseString "\"caf\\u00e9\"")))
      (pvtAssertOk r "parse \\u00e9")
      ;; We don't string-equal here because SKILL string literal cannot
      ;; portably represent é; check length only.
      (when (pvtIsOk r)
        (let ((s (pvtUnwrap r)))
          (pvtAssert (greaterp (strlen s) 3) t "decoded string non-trivial"))))))

(pvtTestRegister "json/integer-positive"
  (lambda ()
    (let ((r (pvtJsonParseString "42")))
      (pvtAssertOk r "parse 42")
      (pvtAssert (pvtUnwrap r) 42 "value 42")
      (pvtAssert (integerp (pvtUnwrap r)) t "42 is integer"))))

(pvtTestRegister "json/integer-negative"
  (lambda ()
    (let ((r (pvtJsonParseString "-7")))
      (pvtAssertOk r "parse -7")
      (pvtAssert (pvtUnwrap r) -7 "value -7"))))

(pvtTestRegister "json/integer-zero"
  (lambda ()
    (let ((r (pvtJsonParseString "0")))
      (pvtAssertOk r "parse 0")
      (pvtAssert (pvtUnwrap r) 0 "value 0"))))

(pvtTestRegister "json/float-simple"
  (lambda ()
    (let ((r (pvtJsonParseString "3.5")))
      (pvtAssertOk r "parse 3.5")
      (pvtAssert (floatp (pvtUnwrap r)) t "3.5 is float")
      (pvtAssert (pvtUnwrap r) 3.5 "value 3.5"))))

(pvtTestRegister "json/float-exponent"
  (lambda ()
    (let ((r (pvtJsonParseString "1.5e2")))
      (pvtAssertOk r "parse 1.5e2")
      (pvtAssert (floatp (pvtUnwrap r)) t "1.5e2 is float")
      (pvtAssert (pvtUnwrap r) 150.0 "value 150.0"))))

(pvtTestRegister "json/true-false-null-sentinels"
  (lambda ()
    (let ((tR (pvtJsonParseString "true"))
          (fR (pvtJsonParseString "false"))
          (nR (pvtJsonParseString "null")))
      (pvtAssertOk tR "parse true")
      (pvtAssertOk fR "parse false")
      (pvtAssertOk nR "parse null")
      (pvtAssert (pvtJsonTruep  (pvtUnwrap tR)) t "true sentinel")
      (pvtAssert (pvtJsonFalsep (pvtUnwrap fR)) t "false sentinel")
      (pvtAssert (pvtJsonNullp  (pvtUnwrap nR)) t "null sentinel")
      ;; Critically: the boolean sentinel is NOT integer 1.
      (pvtAssert (integerp (pvtUnwrap tR)) nil "true is NOT integer"))))


;; --- negatives -------------------------------------------------------------

(pvtTestRegister "json/reject-trailing-comma-array"
  (lambda ()
    (let ((r (pvtJsonParseString "[1, 2,]")))
      (pvtAssertErrCategory r 'pvt_json "trailing comma array"))))

(pvtTestRegister "json/reject-trailing-comma-object"
  (lambda ()
    (let ((r (pvtJsonParseString "{\"a\":1,}")))
      (pvtAssertErrCategory r 'pvt_json "trailing comma object"))))

(pvtTestRegister "json/reject-unquoted-key"
  (lambda ()
    (let ((r (pvtJsonParseString "{a: 1}")))
      (pvtAssertErrCategory r 'pvt_json "unquoted key"))))

(pvtTestRegister "json/reject-comment"
  (lambda ()
    (let ((r (pvtJsonParseString "/* hi */ 1")))
      (pvtAssertErrCategory r 'pvt_json "comment"))))

(pvtTestRegister "json/reject-single-quotes"
  (lambda ()
    (let ((r (pvtJsonParseString "'hi'")))
      (pvtAssertErrCategory r 'pvt_json "single quotes"))))

(pvtTestRegister "json/reject-leading-zero"
  (lambda ()
    (let ((r (pvtJsonParseString "01")))
      (pvtAssertErrCategory r 'pvt_json "leading zero"))))

(pvtTestRegister "json/reject-lone-surrogate"
  (lambda ()
    (let ((r (pvtJsonParseString "\"\\uD83D\"")))
      (pvtAssertErrCategory r 'pvt_json "lone surrogate"))))

(pvtTestRegister "json/reject-unterminated-string"
  (lambda ()
    (let ((r (pvtJsonParseString "\"abc")))
      (pvtAssertErrCategory r 'pvt_json "unterminated string"))))

(pvtTestRegister "json/reject-empty-input"
  (lambda ()
    (let ((r (pvtJsonParseString "")))
      (pvtAssertErrCategory r 'pvt_json "empty input"))))


;; --- absent vs. null distinction --------------------------------------------

(pvtTestRegister "json/absent-vs-null"
  (lambda ()
    (let ((r (pvtJsonParseString "{\"a\": null}")))
      (pvtAssertOk r "parse {a:null}")
      (when (pvtIsOk r)
        (let ((val (pvtUnwrap r)))
          (let ((present (arrayref val "a"))
                (missing (arrayref val "b")))
            (pvtAssert (pvtJsonNullp present) t "a is JSON null sentinel")
            (pvtAssert (pvtJsonAbsentp missing) t "b is absent sentinel")))))))


;; --- key order via pvtJsonObjectKeys ----------------------------------------

(pvtTestRegister "json/key-order-preserved"
  (lambda ()
    (let ((r (pvtJsonParseString "{\"z\": 1, \"a\": 2, \"m\": 3}")))
      (pvtAssertOk r "parse keyed object")
      (when (pvtIsOk r)
        (let ((val (pvtUnwrap r)))
          (let ((keys (pvtJsonObjectKeys val)))
            (pvtAssert keys '("z" "a" "m") "keys in insertion order")))))))


;; ============================================================================
;; Emitter tests
;;
;; The emitter is the inverse of the parser, with one documented exception:
;; SKILL nil collapses to JSON null (the parser produces nil for both null
;; and the empty array; we pick null as the canonical emit). Empty arrays
;; are exercised explicitly via "[]" parse → nil → "null" emit, NOT
;; round-tripped as a property.
;; ============================================================================

;; --- direct emit assertions (no round-trip) --------------------------------

(pvtTestRegister "json/emit-integer-positive"
  (lambda ()
    (pvtAssert (pvtJsonEmit 42) "42" "emit 42")))

(pvtTestRegister "json/emit-integer-negative"
  (lambda ()
    (pvtAssert (pvtJsonEmit -7) "-7" "emit -7")))

(pvtTestRegister "json/emit-integer-zero"
  (lambda ()
    (pvtAssert (pvtJsonEmit 0) "0" "emit 0")))

(pvtTestRegister "json/emit-float-keeps-decimal"
  (lambda ()
    ;; 150.0 round-trips as a float, not an integer. The bare "%g" format
    ;; would print "150" — emitter pads ".0" so the parser re-reads it as
    ;; float. (See _pvtJsonEmitFloat.)
    (pvtAssert (pvtJsonEmit 150.0) "150.0" "emit 150.0 with explicit decimal")))

(pvtTestRegister "json/emit-float-fraction"
  (lambda ()
    (pvtAssert (pvtJsonEmit 3.5) "3.5" "emit 3.5")))

(pvtTestRegister "json/emit-string-simple"
  (lambda ()
    (pvtAssert (pvtJsonEmit "hello") "\"hello\"" "emit plain string")))

(pvtTestRegister "json/emit-string-quote-escape"
  (lambda ()
    (pvtAssert (pvtJsonEmit "a\"b") "\"a\\\"b\"" "emit quote-escape")))

(pvtTestRegister "json/emit-string-backslash-escape"
  (lambda ()
    (pvtAssert (pvtJsonEmit "a\\b") "\"a\\\\b\"" "emit backslash-escape")))

(pvtTestRegister "json/emit-string-named-control-escapes"
  (lambda ()
    ;; \n \t \r \b \f all map to the named JSON escapes.
    (pvtAssert (pvtJsonEmit "a\nb") "\"a\\nb\"" "emit \\n")
    (pvtAssert (pvtJsonEmit "a\tb") "\"a\\tb\"" "emit \\t")
    (pvtAssert (pvtJsonEmit "a\rb") "\"a\\rb\"" "emit \\r")))

(pvtTestRegister "json/emit-null-from-nil"
  (lambda ()
    ;; nil collapses to JSON null per the documented disambiguation policy.
    (pvtAssert (pvtJsonEmit nil) "null" "emit nil -> null")))

(pvtTestRegister "json/emit-null-from-sentinel"
  (lambda ()
    (pvtAssert (pvtJsonEmit PVT_JSON_NULL) "null" "emit pvt_json_null sentinel")))

(pvtTestRegister "json/emit-empty-array-sentinel"
  (lambda ()
    ;; Sentinel distinct from nil — collector uses this for `[]` slots that
    ;; must not collapse to `null` (e.g. an empty `artifacts` array in the
    ;; top-level run.json envelope).
    (pvtAssert (pvtJsonEmit PVT_JSON_EMPTY_ARRAY) "[]"
               "emit empty-array sentinel -> []")
    ;; Composition test: a freshly-built object with the sentinel as a
    ;; value emits with literal []. Build via parse to install the key
    ;; sidecar, then mutate the value to the sentinel.
    (let ((obj (_pvtTestParseOk "{\"artifacts\":null}")))
      (setarray obj "artifacts" PVT_JSON_EMPTY_ARRAY)
      (pvtAssert (pvtJsonEmit obj) "{\"artifacts\":[]}"
                 "object with empty-array sentinel value -> {\"artifacts\":[]}"))))

(pvtTestRegister "json/emit-empty-object-sentinel"
  (lambda ()
    (pvtAssert (pvtJsonEmit PVT_JSON_EMPTY_OBJECT) "{}"
               "emit empty-object sentinel -> {}")
    (let ((obj (_pvtTestParseOk "{\"sweep\":null}")))
      (setarray obj "sweep" PVT_JSON_EMPTY_OBJECT)
      (pvtAssert (pvtJsonEmit obj) "{\"sweep\":{}}"
                 "object with empty-object sentinel value -> {\"sweep\":{}}"))))

(pvtTestRegister "json/emit-true-false"
  (lambda ()
    (pvtAssert (pvtJsonEmit PVT_JSON_TRUE)  "true"  "emit true sentinel")
    (pvtAssert (pvtJsonEmit PVT_JSON_FALSE) "false" "emit false sentinel")
    ;; Bare t alias to true is convenience for callers that don't carry the
    ;; sentinel through (the parser still emits the sentinel form).
    (pvtAssert (pvtJsonEmit t) "true" "emit raw t -> true")))

(pvtTestRegister "json/emit-array-flat"
  (lambda ()
    (pvtAssert (pvtJsonEmit (list 1 2 3)) "[1,2,3]" "emit [1,2,3]")))

(pvtTestRegister "json/emit-array-nested"
  (lambda ()
    (pvtAssert (pvtJsonEmit (list (list 1 2) (list 3 4)))
               "[[1,2],[3,4]]" "emit [[1,2],[3,4]]")))

(pvtTestRegister "json/emit-array-of-objects"
  (lambda ()
    ;; Build the value via parse so the object key sidecar is set.
    (let ((v (_pvtTestParseOk "[{\"a\":1},{\"b\":2}]")))
      (pvtAssert (pvtJsonEmit v) "[{\"a\":1},{\"b\":2}]"
                 "emit array of objects"))))

(pvtTestRegister "json/emit-object-with-array-values"
  (lambda ()
    (let ((v (_pvtTestParseOk "{\"xs\":[1,2,3],\"ys\":[]}")))
      ;; Empty array becomes nil in SKILL and emits as "null" per policy.
      ;; This is the documented round-trip caveat, exercised explicitly.
      (pvtAssert (pvtJsonEmit v) "{\"xs\":[1,2,3],\"ys\":null}"
                 "emit object with array values; [] -> null"))))

(pvtTestRegister "json/emit-deep-nesting-five-levels"
  (lambda ()
    (let ((v (_pvtTestParseOk "{\"a\":{\"b\":{\"c\":{\"d\":{\"e\":42}}}}}")))
      (pvtAssert (pvtJsonEmit v)
                 "{\"a\":{\"b\":{\"c\":{\"d\":{\"e\":42}}}}}"
                 "emit 5-level deep nesting"))))


;; --- round-trip property tests --------------------------------------------
;; Helper: parse a fixture, emit, parse again, compare to the first parse.
;; We compare the parser outputs (not the emitted strings) so that
;; equivalent representations (e.g. "1.5e2" vs "150.0") still match.

(procedure (_pvtTestRoundTrip text label)
  (let ((once nil) (emitted nil) (twice nil))
    (setq once (_pvtTestParseOk text))
    (setq emitted (pvtJsonEmit once))
    (let ((r (pvtJsonParseString emitted)))
      (pvtAssertOk r (strcat "RT re-parse: " label))
      (when (pvtIsOk r)
        (setq twice (pvtUnwrap r))
        (pvtAssert (_pvtTestSameJson once twice) t
                   (strcat "RT equal: " label))))))

;; Recursive deep equality for the parser's value family. SKILL `equal`
;; works for atoms, lists and strings but NOT for makeTable values — two
;; tables with the same entries compare as different identities. We walk
;; tables key-by-key via the sidecar.
(procedure (_pvtTestSameJson a b)
  (cond
    ((and (tablep a) (tablep b))
      (let ((ka (pvtJsonObjectKeys a)) (kb (pvtJsonObjectKeys b)))
        (cond
          ((not (equal ka kb)) nil)
          (t
            (let ((same t))
              (foreach key ka
                (when same
                  (cond
                    ((not (_pvtTestSameJson (arrayref a key) (arrayref b key)))
                      (setq same nil)))))
              same)))))
    ((and (listp a) (listp b))
      (let ((same t) (la a) (lb b))
        (cond
          ((not (equal (length la) (length lb))) (setq same nil))
          (t
            (while (and same la lb)
              (cond
                ((not (_pvtTestSameJson (car la) (car lb)))
                  (setq same nil)))
              (setq la (cdr la))
              (setq lb (cdr lb)))))
        same))
    (t (equal a b))))

(pvtTestRegister "json/rt-1-int"
  (lambda () (_pvtTestRoundTrip "42" "int")))

(pvtTestRegister "json/rt-2-negative-int"
  (lambda () (_pvtTestRoundTrip "-7" "negative int")))

(pvtTestRegister "json/rt-3-float"
  (lambda () (_pvtTestRoundTrip "3.5" "float")))

(pvtTestRegister "json/rt-4-string-with-escapes"
  (lambda ()
    ;; "a\"b\nc\\d" — embedded quote, newline and backslash.
    (_pvtTestRoundTrip "\"a\\\"b\\nc\\\\d\"" "escaped string")))

(pvtTestRegister "json/rt-5-string-unicode"
  (lambda ()
    ;; é = é. The decoded UTF-8 bytes round-trip verbatim through emit.
    (_pvtTestRoundTrip "\"caf\\u00e9\"" "unicode-via-bmp-escape")))

(pvtTestRegister "json/rt-6-bools-and-null"
  (lambda ()
    (_pvtTestRoundTrip "[true, false, null]" "bool/null array")))

(pvtTestRegister "json/rt-7-flat-object"
  (lambda ()
    (_pvtTestRoundTrip "{\"a\":1,\"b\":\"two\",\"c\":3.5}" "flat object")))

(pvtTestRegister "json/rt-8-array-of-objects"
  (lambda ()
    (_pvtTestRoundTrip "[{\"a\":1},{\"a\":2},{\"a\":3}]" "array-of-objects")))

(pvtTestRegister "json/rt-9-deeply-nested"
  (lambda ()
    (_pvtTestRoundTrip
      "{\"a\":{\"b\":{\"c\":{\"d\":{\"e\":[1,2,3]}}}}}"
      "5-level nested")))

(pvtTestRegister "json/rt-10-mixed-types"
  (lambda ()
    (_pvtTestRoundTrip
      "{\"i\":42,\"f\":3.5,\"s\":\"hi\",\"n\":null,\"b\":true,\"a\":[1,\"x\",false]}"
      "mixed types")))

(pvtTestRegister "json/rt-11-key-order"
  (lambda ()
    ;; Sanity: insertion order is preserved by both parser and emitter, so
    ;; the literal output string can be compared directly.
    (let ((src "{\"z\":1,\"a\":2,\"m\":3}"))
      (let ((once (_pvtTestParseOk src)))
        (pvtAssert (pvtJsonEmit once) src "emit preserves key order")))))

(pvtTestRegister "json/rt-12-numeric-types-preserved"
  (lambda ()
    ;; Critical: integer round-trips as integer, float as float.
    (let ((iVal (_pvtTestParseOk "42"))
          (fVal (_pvtTestParseOk "150.0")))
      (let ((iEmit (pvtJsonEmit iVal))
            (fEmit (pvtJsonEmit fVal)))
        (let ((iAgain (_pvtTestParseOk iEmit))
              (fAgain (_pvtTestParseOk fEmit)))
          (pvtAssert (integerp iAgain) t "int stays int")
          (pvtAssert (floatp   fAgain) t "float stays float"))))))


;; --- pvtJsonEmitToPort: stream to file, read back ------------------------

(pvtTestRegister "json/emit-to-port-roundtrip"
  (lambda ()
    ;; makeTempFileName (sklangref.pdf p.456) gives us a unique path in /tmp.
    ;; We deliberately leave the file behind — there is no documented public
    ;; deleteFile / rmFile primitive on this Cadence (verified live), and
    ;; /tmp is OS-managed.
    (let ((tmpPath (makeTempFileName "/tmp/_pvtJson_emitTest_")))
      (let ((src "{\"x\":1,\"y\":[1,2,3],\"z\":\"hello\"}"))
        (let ((once (_pvtTestParseOk src)))
          (let ((port (outfile tmpPath)))
            (pvtAssert (pvtJsonEmitToPort once port) t
                       "EmitToPort returns t")
            (close port))
          (let ((readBack (pvtJsonParseFile tmpPath)))
            (pvtAssertOk readBack "parse re-read of emitted file")
            (when (pvtIsOk readBack)
              (pvtAssert (_pvtTestSameJson once (pvtUnwrap readBack)) t
                         "EmitToPort -> file -> parse equals input"))))))))


t
