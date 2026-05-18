;; ============================================================================
;; testPvtCorners.il — exercise pvtCorners.il pure helpers (no live session).
;;
;; The walkers (_pvtCornersReadVars / _pvtCornersReadModels /
;; _pvtCornersReadRow) call axl* APIs and are verified via skillbridge probe
;; (Tier-2), not from this file.
;; ============================================================================


;; ---------- _pvtCornersIsSpace ---------------------------------------------

(pvtTestRegister "corners/isSpace/space-yes"
  (lambda () (pvtAssert (_pvtCornersIsSpace " ") t "single space")))

(pvtTestRegister "corners/isSpace/tab-yes"
  (lambda () (pvtAssert (_pvtCornersIsSpace "\t") t "single tab")))

(pvtTestRegister "corners/isSpace/letter-no"
  (lambda () (pvtAssert (_pvtCornersIsSpace "a") nil "letter is not space")))

(pvtTestRegister "corners/isSpace/empty-no"
  (lambda () (pvtAssert (_pvtCornersIsSpace "") nil "empty string is not space")))


;; ---------- _pvtCornersSplitWhitespace -------------------------------------

(pvtTestRegister "corners/split/nil"
  (lambda () (pvtAssert (_pvtCornersSplitWhitespace nil) nil "nil input")))

(pvtTestRegister "corners/split/empty"
  (lambda () (pvtAssert (_pvtCornersSplitWhitespace "") nil "empty input")))

(pvtTestRegister "corners/split/single-token"
  (lambda () (pvtAssert (_pvtCornersSplitWhitespace "55") '("55") "single token")))

(pvtTestRegister "corners/split/two-tokens"
  (lambda () (pvtAssert (_pvtCornersSplitWhitespace "3 2.8") '("3" "2.8") "two tokens")))

(pvtTestRegister "corners/split/three-tokens"
  (lambda () (pvtAssert (_pvtCornersSplitWhitespace "tt ss ff") '("tt" "ss" "ff") "three tokens")))

(pvtTestRegister "corners/split/leading-trailing"
  (lambda () (pvtAssert (_pvtCornersSplitWhitespace "  a b  ") '("a" "b") "leading + trailing space")))

(pvtTestRegister "corners/split/multiple-internal"
  (lambda () (pvtAssert (_pvtCornersSplitWhitespace "a   b") '("a" "b") "runs of spaces")))


;; ---------- _pvtCornersStripQuotes -----------------------------------------

(pvtTestRegister "corners/stripQuotes/wrapped"
  (lambda () (pvtAssert (_pvtCornersStripQuotes "\"tt\"") "tt" "quoted token")))

(pvtTestRegister "corners/stripQuotes/unquoted"
  (lambda () (pvtAssert (_pvtCornersStripQuotes "tt") "tt" "unquoted passes through")))

(pvtTestRegister "corners/stripQuotes/empty-inside"
  (lambda () (pvtAssert (_pvtCornersStripQuotes "\"\"") "" "empty quoted -> empty string")))

(pvtTestRegister "corners/stripQuotes/single-char"
  (lambda () (pvtAssert (_pvtCornersStripQuotes "a") "a" "single char unchanged")))

(pvtTestRegister "corners/stripQuotes/only-leading"
  (lambda () (pvtAssert (_pvtCornersStripQuotes "\"tt") "\"tt" "unmatched leading quote unchanged")))


;; ---------- _pvtCornersParseVarValue ---------------------------------------

(pvtTestRegister "corners/parseVar/scalar"
  (lambda () (pvtAssert (_pvtCornersParseVarValue "55") '("55") "scalar")))

(pvtTestRegister "corners/parseVar/sweep"
  (lambda () (pvtAssert (_pvtCornersParseVarValue "3 2.8") '("3" "2.8") "sweep")))


;; ---------- _pvtCornersParseSectionValue -----------------------------------

(pvtTestRegister "corners/parseSection/single"
  (lambda () (pvtAssert (_pvtCornersParseSectionValue "\"tt\"") '("tt") "single quoted section")))

(pvtTestRegister "corners/parseSection/sweep"
  (lambda () (pvtAssert (_pvtCornersParseSectionValue "\"tt\" \"ss\" \"ff\"")
                        '("tt" "ss" "ff") "three quoted sections")))


;; ---------- _pvtCornersValueToJsonField -----------------------------------

(pvtTestRegister "corners/jsonField/empty-nil"
  (lambda () (pvtAssert (_pvtCornersValueToJsonField nil) nil "empty -> nil")))

(pvtTestRegister "corners/jsonField/single-scalar"
  (lambda () (pvtAssert (_pvtCornersValueToJsonField '("55")) "55" "single token -> scalar string")))

(pvtTestRegister "corners/jsonField/multi-array"
  (lambda () (pvtAssert (_pvtCornersValueToJsonField '("3" "2.8")) '("3" "2.8") "multi -> SKILL list (JSON array)")))


;; ---------- _pvtCornersUnionBasename ---------------------------------------

(pvtTestRegister "corners/basename/correct-suffix-no-dir"
  (lambda () (pvtAssert (_pvtCornersUnionBasename "foo.union.json") "foo" "no dir, correct suffix")))

(pvtTestRegister "corners/basename/correct-suffix-with-dir"
  (lambda () (pvtAssert (_pvtCornersUnionBasename "/a/b/c.union.json") "c" "dir prefix stripped")))

(pvtTestRegister "corners/basename/correct-suffix-rich"
  (lambda () (pvtAssert (_pvtCornersUnionBasename "/path/pvt_union_example.union.json")
                        "pvt_union_example" "matches the bundled example name")))

(pvtTestRegister "corners/basename/wrong-suffix-json-only"
  (lambda () (pvtAssert (_pvtCornersUnionBasename "foo.json") nil "single .json suffix rejected")))

(pvtTestRegister "corners/basename/no-suffix"
  (lambda () (pvtAssert (_pvtCornersUnionBasename "foo") nil "no suffix rejected")))

(pvtTestRegister "corners/basename/nil"
  (lambda () (pvtAssert (_pvtCornersUnionBasename nil) nil "nil input")))

(pvtTestRegister "corners/basename/exactly-suffix"
  (lambda () (pvtAssert (_pvtCornersUnionBasename ".union.json") nil
                        "suffix-only filename rejected (no basename)")))


;; ---------- _pvtCornersJoinWithSpace -------------------------------------

(pvtTestRegister "corners/join/empty"
  (lambda () (pvtAssert (_pvtCornersJoinWithSpace nil) "" "empty list -> empty string")))

(pvtTestRegister "corners/join/single"
  (lambda () (pvtAssert (_pvtCornersJoinWithSpace '("55")) "55" "single token")))

(pvtTestRegister "corners/join/two"
  (lambda () (pvtAssert (_pvtCornersJoinWithSpace '("3" "2.8")) "3 2.8" "two tokens")))

(pvtTestRegister "corners/join/three"
  (lambda () (pvtAssert (_pvtCornersJoinWithSpace '("tt" "ss" "ff")) "tt ss ff" "three tokens")))


;; ---------- _pvtCornersFormatVarValueFromJson ----------------------------

(pvtTestRegister "corners/formatVar/scalar"
  (lambda () (pvtAssert (_pvtCornersFormatVarValueFromJson "55") "55" "scalar passes through")))

(pvtTestRegister "corners/formatVar/sweep"
  (lambda () (pvtAssert (_pvtCornersFormatVarValueFromJson '("3" "2.8")) "3 2.8" "sweep -> joined")))

(pvtTestRegister "corners/formatVar/nil"
  (lambda () (pvtAssert (_pvtCornersFormatVarValueFromJson nil) nil "nil -> nil")))


;; ---------- _pvtCornersFormatSectionValueFromJson ------------------------

(pvtTestRegister "corners/formatSection/scalar-wraps"
  (lambda () (pvtAssert (_pvtCornersFormatSectionValueFromJson "tt") "\"tt\"" "scalar -> quoted")))

(pvtTestRegister "corners/formatSection/sweep-wraps-each"
  (lambda () (pvtAssert (_pvtCornersFormatSectionValueFromJson '("tt" "ss" "ff"))
                        "\"tt\" \"ss\" \"ff\"" "sweep -> each token quoted, space joined")))

(pvtTestRegister "corners/formatSection/nil"
  (lambda () (pvtAssert (_pvtCornersFormatSectionValueFromJson nil) nil "nil -> nil")))


;; ---------- round-trip identity: get-form -> set-form -> get-form -------

(pvtTestRegister "corners/roundtrip/var-scalar"
  (lambda ()
    (let ((parsed (_pvtCornersParseVarValue "55")))
      (let ((jsonForm (_pvtCornersValueToJsonField parsed)))
        (let ((setForm (_pvtCornersFormatVarValueFromJson jsonForm)))
          (pvtAssert setForm "55" "scalar var: parse -> json-shape -> set is identity"))))))

(pvtTestRegister "corners/roundtrip/var-sweep"
  (lambda ()
    (let ((parsed (_pvtCornersParseVarValue "3 2.8")))
      (let ((jsonForm (_pvtCornersValueToJsonField parsed)))
        (let ((setForm (_pvtCornersFormatVarValueFromJson jsonForm)))
          (pvtAssert setForm "3 2.8" "sweep var: parse -> json-shape -> set is identity"))))))

(pvtTestRegister "corners/roundtrip/section-sweep"
  (lambda ()
    (let ((parsed (_pvtCornersParseSectionValue "\"tt\" \"ss\" \"ff\"")))
      (let ((jsonForm (_pvtCornersValueToJsonField parsed)))
        (let ((setForm (_pvtCornersFormatSectionValueFromJson jsonForm)))
          (pvtAssert setForm "\"tt\" \"ss\" \"ff\""
                     "sweep section: parse -> json-shape -> set is identity"))))))


;; ---------- _pvtCornersCollectRowNames (Phase 3A v1.9 #1, DECISIONS #67) ----

(pvtTestRegister "corners/collectRowNames/empty"
  (lambda () (pvtAssert (_pvtCornersCollectRowNames nil) nil
                        "empty rows list -> nil")))

(pvtTestRegister "corners/collectRowNames/single"
  (lambda ()
    (let ((tab (makeTable 'r1 'unbound)))
      (setarray tab "row_name" "TT")
      (pvtAssert (_pvtCornersCollectRowNames (list tab))
                 '("TT")
                 "single-row table -> 1-elt name list"))))

(pvtTestRegister "corners/collectRowNames/preserves-order"
  (lambda ()
    (let ((t1 (makeTable 'r1 'unbound))
          (t2 (makeTable 'r2 'unbound))
          (t3 (makeTable 'r3 'unbound)))
      (setarray t1 "row_name" "TT")
      (setarray t2 "row_name" "TT_pvt")
      (setarray t3 "row_name" "TT_2p5G")
      (pvtAssert (_pvtCornersCollectRowNames (list t1 t2 t3))
                 '("TT" "TT_pvt" "TT_2p5G")
                 "declaration order preserved"))))

(pvtTestRegister "corners/collectRowNames/skips-missing-row_name"
  (lambda ()
    (let ((t1 (makeTable 'r1 'unbound))
          (t2 (makeTable 'r2 'unbound))
          (t3 (makeTable 'r3 'unbound)))
      (setarray t1 "row_name" "good_a")
      ;; t2 has no row_name field at all
      (setarray t3 "row_name" "good_b")
      (pvtAssert (_pvtCornersCollectRowNames (list t1 t2 t3))
                 '("good_a" "good_b")
                 "rows missing row_name silently dropped (push will pvtErr them)"))))

(pvtTestRegister "corners/collectRowNames/skips-non-string-row_name"
  (lambda ()
    (let ((t1 (makeTable 'r1 'unbound))
          (t2 (makeTable 'r2 'unbound)))
      (setarray t1 "row_name" "ok")
      (setarray t2 "row_name" 42)
      (pvtAssert (_pvtCornersCollectRowNames (list t1 t2))
                 '("ok")
                 "non-string row_name dropped"))))

(pvtTestRegister "corners/collectRowNames/skips-non-table-entries"
  (lambda ()
    (let ((t1 (makeTable 'r1 'unbound)))
      (setarray t1 "row_name" "TT")
      (pvtAssert (_pvtCornersCollectRowNames (list t1 "not_a_table" 42))
                 '("TT")
                 "non-table list entries dropped"))))
