;; ============================================================================
;; testPvtMeasure.il — exercise pvtMeasure.il pure helpers (no live session).
;;
;; The live walkers (pvtMeasurePush actually-calling-axl* / pvtMeasurePull
;; actually-calling-axlOutputsExportToFile) are verified via skillbridge
;; probe (Tier-2), not from this file.
;; ============================================================================


;; ---------- _pvtMeasureStripTrailingNewline ---------------------------------

(pvtTestRegister "measure/stripNL/with-nl"
  (lambda () (pvtAssert (_pvtMeasureStripTrailingNewline "abc\n") "abc" "trailing \\n stripped")))

(pvtTestRegister "measure/stripNL/no-nl"
  (lambda () (pvtAssert (_pvtMeasureStripTrailingNewline "abc") "abc" "no \\n unchanged")))

(pvtTestRegister "measure/stripNL/empty"
  (lambda () (pvtAssert (_pvtMeasureStripTrailingNewline "") "" "empty string passes through")))

(pvtTestRegister "measure/stripNL/nil"
  (lambda () (pvtAssert (_pvtMeasureStripTrailingNewline nil) nil "nil passes through")))


;; ---------- _pvtMeasureCountChar -------------------------------------------

(pvtTestRegister "measure/countChar/none"
  (lambda () (pvtAssert (_pvtMeasureCountChar "abcdef" ",") 0 "no comma")))

(pvtTestRegister "measure/countChar/six"
  (lambda () (pvtAssert (_pvtMeasureCountChar "Test,,net,/Vin,t,," ",") 6
                        "standard 7-column row has 6 commas")))


;; ---------- _pvtMeasureSplitOnComma ----------------------------------------

(pvtTestRegister "measure/split/empty-trailing"
  (lambda ()
    (pvtAssert (_pvtMeasureSplitOnComma "Test,,net,/Vin,t,,")
               '("Test" "" "net" "/Vin" "t" "" "")
               "two adjacent commas and trailing comma keep empty fields")))

(pvtTestRegister "measure/split/no-comma"
  (lambda ()
    (pvtAssert (_pvtMeasureSplitOnComma "single") '("single") "no comma -> 1 field")))

(pvtTestRegister "measure/split/trailing"
  (lambda ()
    (pvtAssert (_pvtMeasureSplitOnComma "a,") '("a" "") "trailing comma -> empty last")))

(pvtTestRegister "measure/split/leading"
  (lambda ()
    (pvtAssert (_pvtMeasureSplitOnComma ",b") '("" "b") "leading comma -> empty first")))


;; ---------- _pvtMeasureParseCsvLine (CSV row parsing) ----------------------

(pvtTestRegister "measure/parse/happy-7col-expr"
  (lambda ()
    (let ((r (_pvtMeasureParseCsvLine
              "Test,Rtime_clkout,expr,average(VT(\"/Vout\")),t,,")))
      (pvtAssertOk r "happy 7-col expression row parses")
      (pvtAssert (pvtUnwrap r)
                 '("Test" "Rtime_clkout" "expr" "average(VT(\"/Vout\"))" "t" "" "")
                 "7 fields extracted in order"))))

(pvtTestRegister "measure/parse/happy-7col-net"
  (lambda ()
    (let ((r (_pvtMeasureParseCsvLine "Test,,net,/Vin,t,,")))
      (pvtAssertOk r "happy 7-col net row parses (empty Name)")
      (pvtAssert (pvtUnwrap r)
                 '("Test" "" "net" "/Vin" "t" "" "")
                 "net row keeps empty Name field"))))

(pvtTestRegister "measure/parse/header-row"
  (lambda ()
    (let ((r (_pvtMeasureParseCsvLine "Test,Name,Type,Output,Plot,Save,Spec")))
      (pvtAssertOk r "header row parses as 7 fields"))))

(pvtTestRegister "measure/parse/special-chars-no-comma"
  (lambda ()
    (let ((r (_pvtMeasureParseCsvLine
              "Test,PN_wave,expr,rfEdgePhaseNoise(?result \"pnoise\" ?eventList 'nil),t,,")))
      (pvtAssertOk r "expression with quotes / ?key / 'nil parses")
      (pvtAssert (nthelem 4 (pvtUnwrap r))
                 "rfEdgePhaseNoise(?result \"pnoise\" ?eventList 'nil)"
                 "4th field is the full expression"))))

(pvtTestRegister "measure/parse/empty-trailing-spec"
  (lambda ()
    (let ((r (_pvtMeasureParseCsvLine "Test,X,expr,foo,,t,")))
      (pvtAssertOk r "row with empty Spec field still parses")
      (pvtAssert (nthelem 7 (pvtUnwrap r)) "" "Spec field is empty string"))))

(pvtTestRegister "measure/parse/wrong-column-count-rejected"
  (lambda ()
    (let ((r (_pvtMeasureParseCsvLine "Test,X,expr")))
      (pvtAssertErrCategory r 'pvt_validation
        "row with <7 fields is rejected as pvt_validation"))))

(pvtTestRegister "measure/parse/comma-in-field-rejected"
  (lambda ()
    ;; A field containing a literal comma is not recoverable by the
    ;; un-quoted-CSV reader; spec demands escalation.
    (let ((r (_pvtMeasureParseCsvLine "Test,X,expr,foo(a,b),t,,")))
      (pvtAssertErrCategory r 'pvt_validation
        "row with embedded comma is rejected"))))

(pvtTestRegister "measure/parse/nil-line-rejected"
  (lambda ()
    (let ((r (_pvtMeasureParseCsvLine nil)))
      (pvtAssertErrCategory r 'pvt_validation "nil line is rejected"))))


;; ---------- _pvtMeasureNormaliseBool (plot/save) ---------------------------

(pvtTestRegister "measure/bool/t-yields-t"
  (lambda () (pvtAssert (_pvtMeasureNormaliseBool "t") t "CSV \"t\" -> t")))

(pvtTestRegister "measure/bool/empty-yields-nil"
  (lambda () (pvtAssert (_pvtMeasureNormaliseBool "") nil "CSV \"\" -> nil")))

(pvtTestRegister "measure/bool/nil-yields-nil"
  (lambda () (pvtAssert (_pvtMeasureNormaliseBool nil) nil "nil -> nil")))

(pvtTestRegister "measure/bool/other-yields-nil"
  (lambda () (pvtAssert (_pvtMeasureNormaliseBool "0") nil "any other token -> nil")))


;; ---------- _pvtMeasureKeepRowP (includeSignals filter) --------------------

(pvtTestRegister "measure/filter/keep-expr-default"
  (lambda ()
    (pvtAssert (_pvtMeasureKeepRowP
                 '("Test" "X" "expr" "foo()" "t" "" "")
                 "Test" nil)
               t "Test/expr row kept when includeSignals is nil")))

(pvtTestRegister "measure/filter/drop-net-default"
  (lambda ()
    (pvtAssert (_pvtMeasureKeepRowP
                 '("Test" "" "net" "/Vin" "t" "" "")
                 "Test" nil)
               nil "Test/net row dropped when includeSignals is nil")))

(pvtTestRegister "measure/filter/keep-net-when-included"
  (lambda ()
    (pvtAssert (_pvtMeasureKeepRowP
                 '("Test" "" "net" "/Vin" "t" "" "")
                 "Test" t)
               t "Test/net row kept when includeSignals is t")))

(pvtTestRegister "measure/filter/drop-wrong-test"
  (lambda ()
    (pvtAssert (_pvtMeasureKeepRowP
                 '("OtherTest" "X" "expr" "foo()" "t" "" "")
                 "Test" t)
               nil "row with different Test value dropped")))

(pvtTestRegister "measure/filter/short-row-dropped"
  (lambda ()
    (pvtAssert (_pvtMeasureKeepRowP '("Test" "X") "Test" t)
               nil "malformed short row treated as not-keepable")))


;; ---------- _pvtMeasureBoolFromJson ----------------------------------------

(pvtTestRegister "measure/bfj/true-sentinel"
  (lambda () (pvtAssert (_pvtMeasureBoolFromJson PVT_JSON_TRUE nil) t "TRUE sentinel -> t")))

(pvtTestRegister "measure/bfj/false-sentinel"
  (lambda () (pvtAssert (_pvtMeasureBoolFromJson PVT_JSON_FALSE t) nil "FALSE sentinel -> nil")))

(pvtTestRegister "measure/bfj/absent-uses-default-true"
  (lambda () (pvtAssert (_pvtMeasureBoolFromJson PVT_JSON_ABSENT t) t "absent -> default t")))

(pvtTestRegister "measure/bfj/absent-uses-default-nil"
  (lambda () (pvtAssert (_pvtMeasureBoolFromJson PVT_JSON_ABSENT nil) nil "absent -> default nil")))


;; ---------- _pvtMeasureBuildSnapshotRow (JSON output shape) ----------------

(pvtTestRegister "measure/snapRow/expr-shape"
  (lambda ()
    (let ((row (_pvtMeasureBuildSnapshotRow
                 '("Test" "Rtime_Vout" "expr" "average(VT(\"/Vout\"))" "t" "" ""))))
      (pvtAssert (arrayref row "name")       "Rtime_Vout"
                 "snap row 'name' from CSV Name col")
      (pvtAssert (arrayref row "type")       "expr"
                 "snap row 'type' from CSV Type col")
      (pvtAssert (arrayref row "expression") "average(VT(\"/Vout\"))"
                 "snap row 'expression' from CSV Output col")
      (pvtAssert (arrayref row "plot")       PVT_JSON_TRUE
                 "snap row 'plot' is sentinel TRUE")
      (pvtAssert (arrayref row "save")       PVT_JSON_FALSE
                 "snap row 'save' is sentinel FALSE")
      (pvtAssert (arrayref row "spec")       ""
                 "snap row 'spec' is empty string"))))

(pvtTestRegister "measure/snapRow/key-order"
  (lambda ()
    (let ((row (_pvtMeasureBuildSnapshotRow
                 '("Test" "x" "expr" "1" "" "" ""))))
      (pvtAssert (arrayref row _PVT_JSON_KEYS_PROP)
                 '("name" "type" "expression" "plot" "save" "spec")
                 "snap row key-order is the spec §3.x order"))))


;; ---------- _pvtMeasureMakeRowResult / report shape ------------------------

(pvtTestRegister "measure/rowResult/no-reason-omits-key"
  (lambda ()
    (let ((r (_pvtMeasureMakeRowResult "Rt" "added" nil)))
      (pvtAssert (arrayref r _PVT_JSON_KEYS_PROP)
                 '("name" "status")
                 "rowResult without reason has 2 keys"))))

(pvtTestRegister "measure/rowResult/reason-included"
  (lambda ()
    (let ((r (_pvtMeasureMakeRowResult "Rt" "failed" "boom")))
      (pvtAssert (arrayref r _PVT_JSON_KEYS_PROP)
                 '("name" "status" "reason")
                 "rowResult with reason has 3 keys")
      (pvtAssert (arrayref r "reason") "boom" "reason value round-trips"))))


;; ---------- _pvtMeasureLoadRendered (push-side JSON validation) ------------

(pvtTestRegister "measure/loadRendered/missing-path-errors"
  (lambda ()
    (let ((r (_pvtMeasureLoadRendered nil)))
      (pvtAssertErrCategory r 'pvt_validation
        "nil renderedJsonPath rejected"))))

(pvtTestRegister "measure/loadRendered/no-such-file"
  (lambda ()
    (let ((r (_pvtMeasureLoadRendered
              "/tmp/nonexistent_pvtmeasure_fixture_xyz123.json")))
      ;; Missing file surfaces as pvt_io (from pvtJsonParseFile) — still :err.
      (cond
        ((not (pvtIsErr r))
          (pvtAssert (pvtIsErr r) t "missing-file load returns :err"))
        (t
          (pvtAssert (pvtIsErr r) t "missing-file load returns :err"))))))


;; ---------- v1.3 _pvtMeasureParseSpec --------------------------------------

(pvtTestRegister "measure/parseSpec/lt-with-si"
  (lambda ()
    (let ((r (_pvtMeasureParseSpec "<100p")))
      (pvtAssert (pvtIsOk r) t "<100p parses ok")
      (pvtAssert (pvtUnwrap r) '("lt" 1e-10) "<100p -> (lt 1e-10)"))))

(pvtTestRegister "measure/parseSpec/lt-with-space"
  (lambda ()
    (let ((r (_pvtMeasureParseSpec "< 100p")))
      (pvtAssert (pvtUnwrap r) '("lt" 1e-10) "< 100p -> (lt 1e-10)"))))

(pvtTestRegister "measure/parseSpec/gt-negative-no-space"
  (lambda ()
    (let ((r (_pvtMeasureParseSpec ">-140")))
      (pvtAssert (pvtUnwrap r) '("gt" -140) ">-140 -> (gt -140)"))))

(pvtTestRegister "measure/parseSpec/inclusive-max"
  (lambda ()
    (let ((r (_pvtMeasureParseSpec "<= 1e-10")))
      (pvtAssert (pvtUnwrap r) '("max" 1e-10) "<= maps to ?max"))))

(pvtTestRegister "measure/parseSpec/inclusive-min"
  (lambda ()
    (let ((r (_pvtMeasureParseSpec ">=0")))
      (pvtAssert (pvtUnwrap r) '("min" 0) ">= maps to ?min"))))

(pvtTestRegister "measure/parseSpec/range-keyword"
  (lambda ()
    (let ((r (_pvtMeasureParseSpec "range -150 -100")))
      (pvtAssert (pvtUnwrap r) '("range" -150 -100) "range keyword form"))))

(pvtTestRegister "measure/parseSpec/range-bracket"
  (lambda ()
    (let ((r (_pvtMeasureParseSpec "[2.4G:2.6G]")))
      (pvtAssert (pvtUnwrap r) '("range" 2400000000.0 2600000000.0)
                 "bracket form with SI suffixes"))))

(pvtTestRegister "measure/parseSpec/tol"
  (lambda ()
    (let ((r (_pvtMeasureParseSpec "tol 0.05")))
      (pvtAssert (pvtUnwrap r) '("tol" 0.05) "tol keyword form"))))

(pvtTestRegister "measure/parseSpec/garbage-rejected"
  (lambda ()
    (let ((r (_pvtMeasureParseSpec "garbage")))
      (pvtAssertErrCategory r 'pvt_validation "unrecognised form -> err"))))

(pvtTestRegister "measure/parseSpec/non-numeric-tail-rejected"
  (lambda ()
    (let ((r (_pvtMeasureParseSpec "< abc")))
      (pvtAssertErrCategory r 'pvt_validation "non-numeric value -> err"))))

(pvtTestRegister "measure/parseSpec/empty-string-rejected"
  (lambda ()
    (let ((r (_pvtMeasureParseSpec "")))
      (pvtAssertErrCategory r 'pvt_validation "empty -> err"))))


;; ---------- v1.3 _pvtMeasureSafeEvalNumber ---------------------------------

(pvtTestRegister "measure/safeEval/integer"
  (lambda ()
    (pvtAssert (pvtUnwrap (_pvtMeasureSafeEvalNumber "100")) 100 "100 -> 100")))

(pvtTestRegister "measure/safeEval/si-pico"
  (lambda ()
    (pvtAssert (pvtUnwrap (_pvtMeasureSafeEvalNumber "100p")) 1e-10
               "100p -> 1e-10")))

(pvtTestRegister "measure/safeEval/letters-rejected"
  (lambda ()
    (pvtAssertErrCategory (_pvtMeasureSafeEvalNumber "abc")
                          'pvt_validation
                          "letters -> err (errset cannot catch this!)")))

(pvtTestRegister "measure/safeEval/embedded-letter-rejected"
  (lambda ()
    (pvtAssertErrCategory (_pvtMeasureSafeEvalNumber "1+x")
                          'pvt_validation
                          "1+x -> err — x would be unbound")))
