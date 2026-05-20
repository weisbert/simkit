"""Unit tests for :mod:`simkit.gui.corner_expand` (G-9, pure / no Qt)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.gui.corner_expand import (  # noqa: E402
    coherence_warnings,
    expand_flat_row,
    expansion_count,
    expansion_tooltip,
)


class ExpandFlatRowTests(unittest.TestCase):

    def test_process_comma_list_expands_to_n_subcorners(self):
        row = {"row_name": "TT_pvt", "process": "tt,ss,ff",
               "temperature": "27", "vdd": "1.0",
               "model_file": "/pdk/rf018.scs"}
        subs = expand_flat_row(row)
        self.assertEqual(len(subs), 3)
        # Sub-corner names match what simkit.union.explode produces — the
        # same names that show up in the Results table.
        names = sorted(s.sub_corner_name for s in subs)
        self.assertEqual(names, ["TT_pvt_0", "TT_pvt_1", "TT_pvt_2"])

    def test_single_process_is_one_corner_keeps_row_name(self):
        row = {"row_name": "nom", "process": "tt", "vdd": "1.0",
               "model_file": "/pdk/m.scs"}
        subs = expand_flat_row(row)
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0].sub_corner_name, "nom")

    def test_extra_vars_sweep_multiplies_count(self):
        row = {"row_name": "C", "process": "tt,ss",
               "model_file": "/pdk/m.scs", "extra_vars": "VDD=3,2.8"}
        # 2 process sections * 2 supply values.
        self.assertEqual(expansion_count(row), 4)

    def test_incomplete_row_expands_to_zero(self):
        # row_name only — not yet a corner.
        self.assertEqual(expansion_count({"row_name": "draft"}), 0)
        self.assertEqual(expand_flat_row({"row_name": "draft"}), [])

    def test_missing_row_name_expands_to_zero(self):
        self.assertEqual(expansion_count({"process": "tt",
                                          "model_file": "/m.scs"}), 0)

    def test_var_only_row_with_no_model_still_expands(self):
        row = {"row_name": "t_only", "temperature": "85"}
        self.assertEqual(expansion_count(row), 1)


class ExpansionTooltipTests(unittest.TestCase):

    def test_tooltip_lists_each_subcorner(self):
        row = {"row_name": "TT_pvt", "process": "tt,ss,ff",
               "vdd": "1.0", "model_file": "/pdk/m.scs"}
        tip = expansion_tooltip(row)
        self.assertIn("3 sub-corners", tip)
        self.assertIn("TT_pvt_0", tip)
        self.assertIn("TT_pvt_2", tip)

    def test_tooltip_for_incomplete_row(self):
        tip = expansion_tooltip({"row_name": "draft"})
        self.assertIn("not a complete corner", tip)

    def test_tooltip_single_corner(self):
        tip = expansion_tooltip({"row_name": "nom", "process": "tt",
                                 "model_file": "/m.scs"})
        self.assertIn("1 corner", tip)


class CoherenceWarningTests(unittest.TestCase):

    def test_supply_hidden_in_extra_vars_warns(self):
        row = {"row_name": "C1", "process": "tt", "vdd": "",
               "model_file": "/m.scs", "extra_vars": "VDD=3,2.8"}
        warns = coherence_warnings(row)
        self.assertEqual(len(warns), 1)
        self.assertIn("extra_vars", warns[0])
        self.assertIn("vdd column is empty", warns[0])

    def test_supply_in_both_places_warns(self):
        row = {"row_name": "C2", "process": "tt", "vdd": "1.0",
               "model_file": "/m.scs", "extra_vars": "VDD=3"}
        warns = coherence_warnings(row)
        self.assertEqual(len(warns), 1)
        self.assertIn("defined in both", warns[0])

    def test_supply_only_in_vdd_column_is_clean(self):
        row = {"row_name": "C3", "process": "tt", "vdd": "1.0",
               "model_file": "/m.scs", "extra_vars": "L=100n"}
        self.assertEqual(coherence_warnings(row), [])

    def test_supply_alias_supply_detected(self):
        row = {"row_name": "C4", "process": "tt", "vdd": "",
               "model_file": "/m.scs", "extra_vars": "supply=1.8"}
        self.assertEqual(len(coherence_warnings(row)), 1)

    def test_non_supply_extra_var_is_clean(self):
        row = {"row_name": "C5", "process": "tt", "vdd": "",
               "model_file": "/m.scs", "extra_vars": "cap_load=1p"}
        self.assertEqual(coherence_warnings(row), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
