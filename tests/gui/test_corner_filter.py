"""Tests for the Corner Manager filter matcher (pure logic, no Qt)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.gui.corner_filter import (  # noqa: E402
    DEFAULT_MODE,
    MENU_ORDER,
    FilterMode,
    Matcher,
)


class MatcherTest(unittest.TestCase):
    def test_inactive_matcher_passes_everything(self):
        m = Matcher()
        self.assertFalse(m.active)
        self.assertTrue(m.matches("anything"))
        self.assertTrue(m.matches(""))

    def test_default_mode_is_contains(self):
        self.assertIs(DEFAULT_MODE, FilterMode.CONTAINS)
        m = Matcher(pattern="RX")
        self.assertTrue(m.matches("BT_2G_RX_TT"))
        self.assertFalse(m.matches("BT_2G_TX_TT"))

    def test_case_insensitive_by_default(self):
        self.assertTrue(Matcher(pattern="rx").matches("BT_RX"))
        self.assertFalse(
            Matcher(pattern="rx", case_sensitive=True).matches("BT_RX")
        )

    def test_equals_begins_ends(self):
        self.assertTrue(Matcher(FilterMode.EQUALS, "tt").matches("TT"))
        self.assertFalse(Matcher(FilterMode.EQUALS, "tt").matches("TT_2G"))
        self.assertTrue(Matcher(FilterMode.BEGINS_WITH, "BT").matches("BT_RX"))
        self.assertTrue(Matcher(FilterMode.ENDS_WITH, "_TT").matches("RX_TT"))
        self.assertFalse(Matcher(FilterMode.ENDS_WITH, "_TT").matches("RX_SS"))

    def test_word_sets(self):
        self.assertTrue(
            Matcher(FilterMode.ALL_WORDS, "rx tt").matches("BT_RX_TT")
        )
        self.assertFalse(
            Matcher(FilterMode.ALL_WORDS, "rx ff").matches("BT_RX_TT")
        )
        self.assertTrue(
            Matcher(FilterMode.ANY_WORDS, "rx ff").matches("BT_RX_TT")
        )
        self.assertTrue(
            Matcher(FilterMode.NONE_WORDS, "ff ss").matches("BT_RX_TT")
        )
        self.assertFalse(
            Matcher(FilterMode.NONE_WORDS, "rx ss").matches("BT_RX_TT")
        )

    def test_wildcard(self):
        self.assertTrue(Matcher(FilterMode.WILDCARD, "bt_*_tt").matches("bt_rx_tt"))
        self.assertFalse(Matcher(FilterMode.WILDCARD, "bt_*_tt").matches("bt_rx_ss"))

    def test_regex(self):
        self.assertTrue(Matcher(FilterMode.REGEX, r"RX|TX").matches("BT_TX_TT"))
        # an invalid regex must not blank the table — it passes everything
        self.assertTrue(Matcher(FilterMode.REGEX, r"RX(").matches("anything"))

    def test_numeric_operators(self):
        gt = Matcher(FilterMode.NUMERIC, ">15")
        self.assertTrue(gt.matches("20"))
        self.assertFalse(gt.matches("15"))
        self.assertFalse(gt.matches("10"))
        self.assertTrue(Matcher(FilterMode.NUMERIC, ">=15").matches("15"))
        self.assertTrue(Matcher(FilterMode.NUMERIC, "<=15").matches("15"))
        self.assertTrue(Matcher(FilterMode.NUMERIC, "<15").matches("10"))
        self.assertTrue(Matcher(FilterMode.NUMERIC, "=15").matches("15"))
        self.assertTrue(Matcher(FilterMode.NUMERIC, "15").matches("15"))

    def test_numeric_range(self):
        m = Matcher(FilterMode.NUMERIC, "15..20")
        self.assertTrue(m.matches("17"))
        self.assertTrue(m.matches("15"))
        self.assertTrue(m.matches("20"))
        self.assertFalse(m.matches("14"))
        self.assertFalse(m.matches("21"))

    def test_numeric_against_nonnumeric_value(self):
        # a non-numeric cell can never satisfy a numeric filter
        self.assertFalse(Matcher(FilterMode.NUMERIC, ">15").matches("tt"))

    def test_numeric_floats_and_signs(self):
        self.assertTrue(Matcher(FilterMode.NUMERIC, ">-40").matches("55"))
        self.assertTrue(Matcher(FilterMode.NUMERIC, "<=0.9").matches("0.85"))

    def test_every_mode_has_a_chip(self):
        for mode in MENU_ORDER:
            self.assertTrue(mode.chip)
        self.assertEqual(len(MENU_ORDER), len(set(MENU_ORDER)))


if __name__ == "__main__":
    unittest.main()
