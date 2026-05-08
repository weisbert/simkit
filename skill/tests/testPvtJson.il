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


t
