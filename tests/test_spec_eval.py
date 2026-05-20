"""v1.4 — Tests for simkit.spec_eval.

Covers both directions of the spec parser surface:

  * **User-friendly forms** (what users write in bundle ``spec:`` fields):
    ``<X``, ``>X``, ``<=X``, ``>=X``, ``[X:Y]``, ``X..Y``, ``range X Y``,
    ``tol X``.
  * **Maestro CSV normalised forms** (what ``axlOutputsExportToFile`` emits
    on the read-back direction): ``< X``, ``> X``, ``minimize X``,
    ``maximize X``, ``range X Y``, ``tolerance X ()``.

Plus the four-way evaluator decision matrix (no_spec / no_value / pass /
fail / unsupported / parse_err).
"""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.spec_eval import (  # noqa: E402
    SPEC_STATUS_ENUM,
    SpecParseError,
    evaluate_spec,
    parse_spec,
    spec_margin,
)


# --- parse_spec — happy paths ----------------------------------------------


class ParseSpecHappyTests(unittest.TestCase):

    # 1-arg operator forms
    def test_lt_strict(self):
        self.assertEqual(parse_spec("<100"), ("lt", 100.0))

    def test_lt_with_space(self):
        # Maestro CSV always emits with a space; v1.3 user form has no space.
        self.assertEqual(parse_spec("< 100"), ("lt", 100.0))

    def test_gt_strict(self):
        self.assertEqual(parse_spec(">10"), ("gt", 10.0))

    def test_le_inclusive(self):
        self.assertEqual(parse_spec("<= 100"), ("le", 100.0))

    def test_ge_inclusive(self):
        self.assertEqual(parse_spec(">=0"), ("ge", 0.0))

    # SI suffix support
    def test_lt_with_si_p(self):
        self.assertAlmostEqual(parse_spec("<100p")[1], 1e-10)

    def test_lt_with_si_meg(self):
        self.assertAlmostEqual(parse_spec("<2.4Meg")[1], 2.4e6)

    def test_negative_number(self):
        self.assertEqual(parse_spec("<-100"), ("lt", -100.0))

    def test_negative_with_space(self):
        self.assertEqual(parse_spec("< -100"), ("lt", -100.0))

    # Scientific notation (Maestro normalises SI to e-notation on CSV)
    def test_lt_scientific(self):
        self.assertEqual(parse_spec("< 1e-10"), ("lt", 1e-10))

    # range forms
    def test_range_keyword(self):
        self.assertEqual(parse_spec("range 1 2"), ("range", 1.0, 2.0))

    def test_range_decimal_keyword(self):
        self.assertEqual(parse_spec("range -150 -100"),
                         ("range", -150.0, -100.0))

    def test_range_bracket(self):
        self.assertEqual(parse_spec("[2.4G:2.6G]"),
                         ("range", 2.4e9, 2.6e9))

    def test_range_dotted(self):
        self.assertEqual(parse_spec("1.5..2.5"), ("range", 1.5, 2.5))

    def test_range_dotted_negative(self):
        self.assertEqual(parse_spec("-150..-100"),
                         ("range", -150.0, -100.0))

    # Maestro-normalised keyword forms (the "v1.3 round-trip" gap)
    def test_minimize_form(self):
        # Maestro emits this when ?min was used at push time.
        self.assertEqual(parse_spec("minimize 0"), ("minimize", 0.0))

    def test_maximize_form(self):
        self.assertEqual(parse_spec("maximize 5"), ("maximize", 5.0))

    def test_tolerance_form_with_empty_parens(self):
        # Maestro literal output is "tolerance 0.05 ()" — trailing tail ignored.
        self.assertEqual(parse_spec("tolerance 0.05 ()"),
                         ("tolerance", 0.05))

    def test_tol_short_form(self):
        # User-friendly form (bundle).
        self.assertEqual(parse_spec("tol 0.05"), ("tolerance", 0.05))


# --- parse_spec — error paths ----------------------------------------------


class ParseSpecErrorTests(unittest.TestCase):

    def test_empty_string(self):
        with self.assertRaisesRegex(SpecParseError, "empty"):
            parse_spec("")

    def test_whitespace_only(self):
        with self.assertRaisesRegex(SpecParseError, "empty"):
            parse_spec("   ")

    def test_garbage(self):
        with self.assertRaisesRegex(SpecParseError, "unrecognised"):
            parse_spec("garbage")

    def test_not_a_string(self):
        with self.assertRaisesRegex(SpecParseError, "must be string"):
            parse_spec(42)  # type: ignore[arg-type]

    def test_bad_bracket_form(self):
        with self.assertRaisesRegex(SpecParseError, "bracket form"):
            parse_spec("[1:2:3]")

    def test_dotted_3plus_dots(self):
        with self.assertRaisesRegex(SpecParseError, "3\\+"):
            parse_spec("1...2")

    def test_dotted_multi_dotdot(self):
        with self.assertRaisesRegex(SpecParseError, "multiple"):
            parse_spec("1..2..3")

    def test_lt_non_numeric_value(self):
        with self.assertRaisesRegex(SpecParseError, "not a number"):
            parse_spec("< abc")

    def test_unknown_si_suffix(self):
        with self.assertRaisesRegex(SpecParseError, "SI suffix"):
            parse_spec("<100xyz")

    def test_range_wrong_arity(self):
        with self.assertRaisesRegex(SpecParseError, "unrecognised"):
            parse_spec("range 1")


# --- evaluate_spec — verdict matrix ----------------------------------------


class EvaluateSpecTests(unittest.TestCase):

    # no_spec path
    def test_no_spec_when_spec_is_none(self):
        self.assertEqual(evaluate_spec(None, 42), "no_spec")

    def test_no_spec_when_empty_string(self):
        self.assertEqual(evaluate_spec("", 42), "no_spec")
        self.assertEqual(evaluate_spec("   ", 42), "no_spec")

    # no_value path
    def test_no_value_when_none(self):
        self.assertEqual(evaluate_spec("<100", None), "no_value")

    def test_no_value_when_nan(self):
        self.assertEqual(evaluate_spec("<100", float("nan")), "no_value")

    def test_no_value_when_non_numeric(self):
        # Pass a string — the collector's eval_err path can produce string
        # values that bypass the typical numeric pipe.
        self.assertEqual(evaluate_spec("<100", "abc"), "no_value")  # type: ignore[arg-type]

    # strict ops
    def test_lt_pass(self):
        self.assertEqual(evaluate_spec("< 100", 50), "pass")

    def test_lt_fail_at_boundary(self):
        # Strict — exactly at boundary fails.
        self.assertEqual(evaluate_spec("< 100", 100), "fail")

    def test_lt_fail_above(self):
        self.assertEqual(evaluate_spec("< 100", 150), "fail")

    def test_gt_pass(self):
        self.assertEqual(evaluate_spec("> 10", 50), "pass")

    def test_gt_fail_at_boundary(self):
        self.assertEqual(evaluate_spec("> 10", 10), "fail")

    # inclusive ops
    def test_le_pass_at_boundary(self):
        self.assertEqual(evaluate_spec("<= 100", 100), "pass")

    def test_le_fail_above(self):
        self.assertEqual(evaluate_spec("<= 100", 100.01), "fail")

    def test_ge_pass_at_boundary(self):
        self.assertEqual(evaluate_spec(">= 0", 0), "pass")

    def test_ge_fail_below(self):
        self.assertEqual(evaluate_spec(">= 0", -0.01), "fail")

    # minimize / maximize (ADE-XL convention)
    def test_minimize_pass_at_boundary(self):
        # minimize X = pass if value <= X (the goal is to be at-or-below X).
        self.assertEqual(evaluate_spec("minimize 100", 100), "pass")

    def test_minimize_pass_below(self):
        self.assertEqual(evaluate_spec("minimize 100", 50), "pass")

    def test_minimize_fail_above(self):
        self.assertEqual(evaluate_spec("minimize 100", 150), "fail")

    def test_maximize_pass_at_boundary(self):
        self.assertEqual(evaluate_spec("maximize 100", 100), "pass")

    def test_maximize_pass_above(self):
        self.assertEqual(evaluate_spec("maximize 100", 150), "pass")

    def test_maximize_fail_below(self):
        self.assertEqual(evaluate_spec("maximize 100", 50), "fail")

    # range
    def test_range_pass_in_middle(self):
        self.assertEqual(evaluate_spec("range 1 2", 1.5), "pass")

    def test_range_pass_at_lower(self):
        self.assertEqual(evaluate_spec("range 1 2", 1.0), "pass")

    def test_range_pass_at_upper(self):
        self.assertEqual(evaluate_spec("range 1 2", 2.0), "pass")

    def test_range_fail_below(self):
        self.assertEqual(evaluate_spec("range 1 2", 0.5), "fail")

    def test_range_fail_above(self):
        self.assertEqual(evaluate_spec("range 1 2", 2.5), "fail")

    def test_range_bracket_form(self):
        self.assertEqual(evaluate_spec("[2.4G:2.6G]", 2.5e9), "pass")
        self.assertEqual(evaluate_spec("[2.4G:2.6G]", 2.3e9), "fail")

    def test_range_dotted_form(self):
        self.assertEqual(evaluate_spec("1.5..2.5", 2.0), "pass")
        self.assertEqual(evaluate_spec("1.5..2.5", 3.0), "fail")

    # tolerance — unsupported
    def test_tolerance_unsupported(self):
        self.assertEqual(evaluate_spec("tolerance 0.05", 1.0), "unsupported")
        self.assertEqual(evaluate_spec("tolerance 0.05 ()", 1.0), "unsupported")
        self.assertEqual(evaluate_spec("tol 0.05", 1.0), "unsupported")

    # parse_err — bad spec strings don't raise; they produce a verdict.
    def test_parse_err_on_garbage(self):
        self.assertEqual(evaluate_spec("garbage", 42), "parse_err")

    def test_parse_err_on_bad_number(self):
        self.assertEqual(evaluate_spec("< abc", 42), "parse_err")

    # SI suffix round-trip (sim returns SI-equivalent value, spec uses SI)
    def test_si_suffix_pass(self):
        # 100ps spec; value comes back from sim as 5e-11 (50ps) — should pass.
        self.assertEqual(evaluate_spec("< 100p", 5e-11), "pass")
        self.assertEqual(evaluate_spec("< 100p", 1.5e-10), "fail")

    # Real-world fnxSession0 dogfood specs
    def test_dogfood_pn_at_1mhz(self):
        # PN_at_1MHz spec was "<-100" — a phase-noise spec on -100 dBc/Hz.
        self.assertEqual(evaluate_spec("< -100", -120), "pass")
        self.assertEqual(evaluate_spec("< -100", -80), "fail")

    def test_dogfood_rtime(self):
        # Rtime_clkout spec was "<100p" — rise time under 100 ps.
        self.assertEqual(evaluate_spec("< 1e-10", 5e-11), "pass")
        self.assertEqual(evaluate_spec("< 1e-10", 2e-10), "fail")


# --- enum invariant ---------------------------------------------------------


class EnumInvariantTests(unittest.TestCase):

    def test_every_evaluate_outcome_is_in_enum(self):
        # Exhaustive across the verdict matrix.
        cases = [
            (None, 0, "no_spec"),
            ("", 0, "no_spec"),
            ("< 100", None, "no_value"),
            ("< 100", float("nan"), "no_value"),
            ("< 100", 50, "pass"),
            ("< 100", 150, "fail"),
            ("range 1 2", 1.5, "pass"),
            ("range 1 2", 3.0, "fail"),
            ("minimize 5", 4, "pass"),
            ("maximize 5", 6, "pass"),
            ("tolerance 0.05", 1, "unsupported"),
            ("garbage", 0, "parse_err"),
        ]
        for spec, value, expected in cases:
            got = evaluate_spec(spec, value)
            self.assertIn(got, SPEC_STATUS_ENUM,
                          f"{got!r} not in enum for ({spec!r}, {value!r})")
            self.assertEqual(got, expected,
                             f"({spec!r}, {value!r}): expected {expected!r}, got {got!r}")


# --- spec_margin — signed distance to the spec limit -----------------------


class SpecMarginTests(unittest.TestCase):

    def test_ge_margin_is_positive_when_passing(self):
        # >= 20 @ 25 → 5 of room.
        self.assertEqual(spec_margin(">= 20", 25), 5.0)

    def test_ge_margin_is_negative_when_violating(self):
        self.assertEqual(spec_margin(">= 20", 18), -2.0)

    def test_le_margin_room_is_limit_minus_value(self):
        self.assertEqual(spec_margin("<= 5", 3), 2.0)
        self.assertEqual(spec_margin("<= 5", 7), -2.0)

    def test_minimize_and_maximize_match_le_ge(self):
        self.assertEqual(spec_margin("minimize 100", 80), 20.0)
        self.assertEqual(spec_margin("maximize 100", 120), 20.0)

    def test_range_margin_is_distance_to_nearer_bound(self):
        # range 1 5 @ 4.5 → nearer bound is 5, distance 0.5.
        self.assertEqual(spec_margin("range 1 5", 4.5), 0.5)
        # @ 1.2 → nearer bound is 1, distance 0.2.
        self.assertAlmostEqual(spec_margin("range 1 5", 1.2), 0.2)
        # outside the range → negative.
        self.assertEqual(spec_margin("range 1 5", 6), -1.0)

    def test_margin_none_for_no_spec_or_no_value(self):
        self.assertIsNone(spec_margin(None, 5))
        self.assertIsNone(spec_margin("", 5))
        self.assertIsNone(spec_margin(">= 20", None))
        self.assertIsNone(spec_margin(">= 20", float("nan")))

    def test_margin_none_for_unparseable_or_tolerance(self):
        self.assertIsNone(spec_margin("garbage", 5))
        self.assertIsNone(spec_margin("tolerance 0.05", 5))


if __name__ == "__main__":
    unittest.main()
