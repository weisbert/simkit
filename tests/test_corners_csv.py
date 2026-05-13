"""Unit tests for the Maestro corners-CSV emitter."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "python"))

from simkit.corners_csv import (  # noqa: E402
    CsvBuildError,
    CsvBuildResult,
    build_csv,
    parse_csv,
)
from simkit.union import (  # noqa: E402
    ModelEntry,
    Union,
    UnionRow,
)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _make_row(
    name: str,
    vars_: dict[str, tuple[str, ...]],
    section: tuple[str, ...] = ("tt",),
    *,
    enabled: bool = True,
    file_abs: str | None = "/opt/pdk/rf018.scs",
) -> UnionRow:
    sweep_keys = frozenset(k for k, v in vars_.items() if len(v) > 1)
    return UnionRow(
        row_name=name,
        vars=dict(vars_),
        models=(
            ModelEntry(
                file="rf018.scs",
                block="Global",
                test="All",
                section=section,
                file_abs=file_abs,
            ),
        ),
        sweep_var_keys=sweep_keys,
        sweep_model_indices=(
            frozenset({0}) if len(section) > 1 else frozenset()
        ),
        enabled=enabled,
    )


def _make_union(rows: tuple[UnionRow, ...], testbench: str = "L/Test/schematic") -> Union:
    return Union(
        union_schema_version=1,
        name="u",
        project="p",
        testbench_id=testbench,
        rows=rows,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class HeaderRowTests(unittest.TestCase):

    def test_corner_header_lists_row_names(self):
        u = _make_union((
            _make_row("TT", {"temperature": ("55",)}),
            _make_row("FF", {"temperature": ("85",)}),
        ))
        r = build_csv(u)
        self.assertEqual(r.text.splitlines()[0], "Corner,TT,FF")

    def test_enable_row_t_for_enabled(self):
        u = _make_union((
            _make_row("TT", {"temperature": ("55",)}, enabled=True),
            _make_row("FF", {"temperature": ("85",)}, enabled=False),
        ))
        r = build_csv(u)
        self.assertEqual(r.text.splitlines()[1], "Enable,t,f")

    def test_returns_csvbuildresult(self):
        u = _make_union((_make_row("TT", {"temperature": ("55",)}),))
        r = build_csv(u)
        self.assertIsInstance(r, CsvBuildResult)
        self.assertEqual(r.warnings, ())


class VarRowTests(unittest.TestCase):

    def test_temperature_is_title_cased_on_display(self):
        """Maestro GUI shows 'Temperature' even though SKILL stores 'temperature'."""
        u = _make_union((_make_row("TT", {"temperature": ("55",)}),))
        r = build_csv(u)
        lines = r.text.splitlines()
        self.assertEqual(lines[2], "Temperature,55")

    def test_user_var_preserves_case(self):
        u = _make_union((_make_row("TT", {"VDD": ("3",)}),))
        r = build_csv(u)
        self.assertIn("VDD,3", r.text)

    def test_sweep_values_are_space_separated(self):
        u = _make_union((_make_row("TT_pvt", {"VDD": ("3", "2.8")}),))
        r = build_csv(u)
        self.assertIn("VDD,3 2.8", r.text)

    def test_var_absent_for_a_corner_emits_empty_cell(self):
        u = _make_union((
            _make_row("TT", {"temperature": ("55",)}),
            _make_row("TT_2p5G", {"temperature": ("55",), "VDD": ("3",), "flo": ("1M",)}),
        ))
        text = build_csv(u).text
        # 'VDD' was absent in TT, present in TT_2p5G → first cell empty.
        self.assertIn("VDD,,3", text)
        self.assertIn("flo,,1M", text)

    def test_var_insertion_order_preserved_across_rows(self):
        """First-appearance order: temperature (TT), VDD (TT_pvt), flo (TT_2p5G)."""
        u = _make_union((
            _make_row("TT",      {"temperature": ("55",)}),
            _make_row("TT_pvt",  {"temperature": ("55",), "VDD": ("3", "2.8")}),
            _make_row("TT_2p5G", {"temperature": ("55",), "VDD": ("3",), "flo": ("1M",)}),
        ))
        lines = build_csv(u).text.splitlines()
        # Header / Enable then 3 var lines in insertion order.
        self.assertEqual(lines[2].split(",")[0], "Temperature")
        self.assertEqual(lines[3].split(",")[0], "VDD")
        self.assertEqual(lines[4].split(",")[0], "flo")


class ModelRowTests(unittest.TestCase):

    def test_single_modelfile_row_per_unique_abs_path(self):
        u = _make_union((
            _make_row("TT", {"temperature": ("55",)}, file_abs="/opt/pdk/rf018.scs"),
            _make_row("FF", {"temperature": ("85",)}, file_abs="/opt/pdk/rf018.scs"),
        ))
        text = build_csv(u).text
        self.assertEqual(text.count("Modelfile::/opt/pdk/rf018.scs,"), 1)

    def test_modelfile_row_format(self):
        u = _make_union((_make_row("TT", {"temperature": ("55",)}, section=("tt",)),))
        text = build_csv(u).text
        self.assertIn("Modelfile::/opt/pdk/rf018.scs,t tt", text)

    def test_section_sweep_uses_space_separator(self):
        u = _make_union((
            _make_row("TT_pvt", {"temperature": ("55",)}, section=("tt", "ss", "ff")),
        ))
        text = build_csv(u).text
        self.assertIn("Modelfile::/opt/pdk/rf018.scs,t tt ss ff", text)

    def test_missing_file_abs_emits_warning(self):
        u = _make_union((
            _make_row("TT", {"temperature": ("55",)}, file_abs=None),
        ))
        r = build_csv(u)
        self.assertEqual(len(r.warnings), 1)
        self.assertEqual(r.warnings[0].code, "missing_file_abs")
        # Falls back to basename so callers can still inspect the partial CSV.
        self.assertIn("Modelfile::rf018.scs,", r.text)


class TestRowTests(unittest.TestCase):

    def test_test_row_uses_testbench_cell_as_block_and_test(self):
        """Derived from testbench_id parts[1]. For 'L/Test/schematic' → Test."""
        u = _make_union((_make_row("TT", {"temperature": ("55",)}),),
                        testbench="LIB/MyCell/schematic")
        text = build_csv(u).text
        self.assertIn("\nt MyCell::MyCell,t\n", text)

    def test_test_row_all_corners_default_to_enabled(self):
        """Observed in ground-truth: even when corner Enable=f, the per-test
        bit for that corner is still 't'."""
        u = _make_union((
            _make_row("TT", {"temperature": ("55",)}, enabled=False),
            _make_row("FF", {"temperature": ("85",)}, enabled=True),
        ))
        text = build_csv(u).text
        # Test row is last; should have all 't' regardless of Enable col.
        last = text.strip().splitlines()[-1]
        self.assertTrue(last.endswith(",t,t"))


# ---------------------------------------------------------------------------
# Trailing newline + line count sanity
# ---------------------------------------------------------------------------


class StructureTests(unittest.TestCase):

    def test_trailing_newline(self):
        u = _make_union((_make_row("TT", {"temperature": ("55",)}),))
        self.assertTrue(build_csv(u).text.endswith("\n"))

    def test_line_count_matches_layout(self):
        """1 header + 1 enable + N vars + M modelfiles + 1 test row."""
        u = _make_union((
            _make_row("TT", {"temperature": ("55",)}),
            _make_row("TT_pvt", {"temperature": ("55",), "VDD": ("3", "2.8")}),
        ))
        n_lines = len(build_csv(u).text.strip().splitlines())
        self.assertEqual(n_lines, 1 + 1 + 2 + 1 + 1)  # Corner / Enable / 2 vars / 1 model / 1 test


# ---------------------------------------------------------------------------
# Cell-safety: special chars
# ---------------------------------------------------------------------------


class CellSafetyTests(unittest.TestCase):

    def test_comma_in_var_value_rejected(self):
        u = _make_union((_make_row("TT", {"temperature": ("0,1",)}),))
        with self.assertRaises(CsvBuildError) as ctx:
            build_csv(u)
        self.assertIn("','", str(ctx.exception))

    def test_quote_in_section_rejected(self):
        u = _make_union((_make_row("TT", {"temperature": ("55",)}, section=('t"t',)),))
        with self.assertRaises(CsvBuildError):
            build_csv(u)


# ---------------------------------------------------------------------------
# parse_csv (reverse of build_csv) — used by `pvt corners restore`.
# ---------------------------------------------------------------------------


class ParseCsvHappyPathTests(unittest.TestCase):

    def test_minimal_csv_parses(self):
        text = (
            "Corner,TT\n"
            "Enable,t\n"
            "Temperature,55\n"
            "Modelfile::/opt/pdk/rf018.scs,t tt\n"
            "t Test::Test,t\n"
        )
        u = parse_csv(text, testbench_id="L/Test/schematic")
        self.assertEqual(len(u.rows), 1)
        r = u.rows[0]
        self.assertEqual(r.row_name, "TT")
        self.assertTrue(r.enabled)
        self.assertEqual(r.vars["temperature"], ("55",))
        self.assertEqual(r.models[0].file, "rf018.scs")
        self.assertEqual(r.models[0].file_abs, "/opt/pdk/rf018.scs")
        self.assertEqual(r.models[0].section, ("tt",))

    def test_temperature_display_case_reversed(self):
        """Maestro emits 'Temperature' on display; parse must canonicalize
        back to 'temperature' to match SKILL-side var name."""
        text = (
            "Corner,TT\n"
            "Enable,t\n"
            "Temperature,55\n"
            "Modelfile::/opt/pdk/rf018.scs,t tt\n"
        )
        u = parse_csv(text, testbench_id="L/Test/schematic")
        self.assertIn("temperature", u.rows[0].vars)
        self.assertNotIn("Temperature", u.rows[0].vars)

    def test_disabled_corner_round_trips(self):
        text = (
            "Corner,TT,FF\n"
            "Enable,f,t\n"
            "Temperature,55,85\n"
            "Modelfile::/opt/pdk/rf018.scs,t tt,t tt\n"
        )
        u = parse_csv(text, testbench_id="L/Test/schematic")
        self.assertFalse(u.rows[0].enabled)
        self.assertTrue(u.rows[1].enabled)

    def test_var_absent_for_a_corner(self):
        text = (
            "Corner,TT,TT_pvt\n"
            "Enable,t,t\n"
            "Temperature,55,55\n"
            "VDD,,3 2.8\n"
            "Modelfile::/opt/pdk/rf018.scs,t tt,t tt ss ff\n"
        )
        u = parse_csv(text, testbench_id="L/Test/schematic")
        # TT has only temperature; TT_pvt has temperature + VDD sweep.
        self.assertEqual(set(u.rows[0].vars.keys()), {"temperature"})
        self.assertEqual(u.rows[1].vars["VDD"], ("3", "2.8"))
        self.assertIn("VDD", u.rows[1].sweep_var_keys)

    def test_section_sweep_parsed_to_tuple(self):
        text = (
            "Corner,TT_pvt\n"
            "Enable,t\n"
            "Temperature,55\n"
            "Modelfile::/opt/pdk/rf018.scs,t tt ss ff\n"
        )
        u = parse_csv(text, testbench_id="L/Test/schematic")
        self.assertEqual(u.rows[0].models[0].section, ("tt", "ss", "ff"))
        self.assertIn(0, u.rows[0].sweep_model_indices)


class ParseCsvRoundTripTests(unittest.TestCase):

    def test_build_parse_build_identity_on_minimal_union(self):
        u = _make_union((
            _make_row("TT", {"temperature": ("55",)}, enabled=False),
            _make_row("TT_pvt", {"temperature": ("55",), "VDD": ("3", "2.8")}, section=("tt", "ss")),
        ))
        csv1 = build_csv(u).text
        u2 = parse_csv(csv1, testbench_id=u.testbench_id,
                       union_name=u.name, project=u.project)
        csv2 = build_csv(u2).text
        self.assertEqual(csv1, csv2)


class ParseCsvErrorTests(unittest.TestCase):

    def test_too_short_rejected(self):
        with self.assertRaises(CsvBuildError):
            parse_csv("Corner,TT\nEnable,t\n", testbench_id="L/C/s")

    def test_missing_corner_header_rejected(self):
        text = "Foo,TT\nEnable,t\nTemperature,55\nModelfile::/x,t tt\n"
        with self.assertRaises(CsvBuildError) as ctx:
            parse_csv(text, testbench_id="L/C/s")
        self.assertIn("Corner,", str(ctx.exception))

    def test_missing_enable_row_rejected(self):
        text = "Corner,TT\nTemperature,55\nModelfile::/x,t tt\n"
        with self.assertRaises(CsvBuildError) as ctx:
            parse_csv(text, testbench_id="L/C/s")
        self.assertIn("Enable,", str(ctx.exception))

    def test_cell_count_mismatch_rejected(self):
        text = (
            "Corner,TT,FF\n"
            "Enable,t\n"   # only 1 cell
            "Temperature,55,85\n"
            "Modelfile::/x,t tt,t tt\n"
        )
        with self.assertRaises(CsvBuildError):
            parse_csv(text, testbench_id="L/C/s")


if __name__ == "__main__":
    unittest.main()
