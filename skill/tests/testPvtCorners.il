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
