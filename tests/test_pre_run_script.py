"""Unit tests for simkit.pre_run_script (Phase 3A v1.3 / DECISIONS #57 stage-3).

The generator is pure-Python + filesystem; the SKILL it produces is
verified by visual inspection here (the live behaviour gets dogfooded
on a real Maestro run).
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.pre_run_script import (  # noqa: E402
    PreRunSpec,
    build_corner_arg_map,
    render_pre_run_script,
    write_per_test_pre_run_scripts,
    write_pre_run_script,
    _skill_quote,
)


class SkillQuoteTests(unittest.TestCase):
    def test_simple_path(self):
        self.assertEqual(_skill_quote("/abs/spectre.fc"),
                         '"/abs/spectre.fc"')

    def test_embedded_double_quote_escaped(self):
        self.assertEqual(_skill_quote('foo"bar'), '"foo\\"bar"')

    def test_backslash_escaped(self):
        self.assertEqual(_skill_quote("a\\b"), '"a\\\\b"')


class BuildCornerArgMapTests(unittest.TestCase):
    def test_readns_emits_simulator_option(self):
        # additionalArgs gets appended into the netlist's simulatorOptions
        # block (NOT spectre CLI), so the value must be netlist-syntax
        # `readns="path"` — not the +nodeset CLI flag.
        m = build_corner_arg_map(
            ["TT", "TT_pvt_0"],
            {"TT": "/a/spectre.fc", "TT_pvt_0": "/b/spectre.fc"},
            "readns",
        )
        self.assertEqual(m, {
            "TT": 'readns="/a/spectre.fc"',
            "TT_pvt_0": 'readns="/b/spectre.fc"',
        })

    def test_readic_emits_simulator_option(self):
        m = build_corner_arg_map(
            ["TT"], {"TT": "/p/spectre.ic"}, "readic",
        )
        self.assertEqual(m, {"TT": 'readic="/p/spectre.ic"'})

    def test_none_paths_dropped(self):
        # Corners with no upstream IC are omitted so the SKILL assoc
        # misses → that corner runs naked.
        m = build_corner_arg_map(
            ["C1", "C2", "C3"],
            {"C1": "/x", "C2": None, "C3": "/y"},
            "readns",
        )
        self.assertEqual(set(m.keys()), {"C1", "C3"})

    def test_missing_keys_dropped(self):
        # Corner name in the explode order but not in the IC dict
        # (shouldn't happen in practice but pin behaviour) → drop.
        m = build_corner_arg_map(
            ["C1", "C2"], {"C1": "/x"}, "readns",
        )
        self.assertEqual(set(m.keys()), {"C1"})

    def test_bad_mode_rejected(self):
        with self.assertRaises(ValueError):
            build_corner_arg_map(["C1"], {"C1": "/x"}, "loadme")


class RenderScriptTests(unittest.TestCase):
    def test_header_contains_item_name_and_mode(self):
        spec = PreRunSpec(
            item_name="bt2grx_pss",
            mode="readns",
            corner_to_arg={"TT": 'readns="/a.fc"'},
        )
        src = render_pre_run_script(spec)
        self.assertIn("bt2grx_pss", src)
        self.assertIn("readns", src)
        self.assertIn("(1 corners mapped)", src)

    def test_corner_table_embedded(self):
        spec = PreRunSpec(
            item_name="pss",
            mode="readns",
            corner_to_arg={
                "TT": 'readns="/a.fc"',
                "TT_pvt_0": 'readns="/b.fc"',
            },
        )
        src = render_pre_run_script(spec)
        # Each entry is a 2-element list (NOT a cons-cell — Cadence
        # SKILL's cons rejects non-list 2nd arg). assoc returns the
        # whole list; cadr extracts the value. Inner " are escaped
        # by the SKILL string quoter.
        self.assertIn(r'(list "TT" "readns=\"/a.fc\"")', src)
        self.assertIn(r'(list "TT_pvt_0" "readns=\"/b.fc\"")', src)

    def test_script_always_returns_t(self):
        spec = PreRunSpec(item_name="x", mode="readns", corner_to_arg={})
        src = render_pre_run_script(spec)
        # Last non-empty non-comment line is `t)` so Maestro doesn't abort
        last_code_line = [
            l.strip() for l in src.splitlines()
            if l.strip() and not l.strip().startswith(";")
        ][-1]
        self.assertEqual(last_code_line, "t)")

    def test_pre_flight_guard_present(self):
        # The (when (and cornerName ... not equal "" ...)) guard prevents
        # the pre-flight call (corner="") from touching the asi session.
        spec = PreRunSpec(item_name="x", mode="readns", corner_to_arg={})
        src = render_pre_run_script(spec)
        self.assertIn('(not (equal cornerName ""))', src)

    def test_empty_map_renders_safely(self):
        # 0-corner spec must still produce parseable SKILL — the
        # cornerMap is just empty, lookup always misses, script returns t.
        spec = PreRunSpec(item_name="x", mode="readic", corner_to_arg={})
        src = render_pre_run_script(spec)
        self.assertIn("(let ", src)  # NOT let* — worker VM is strict
        # Should have ZERO `(list "` lines
        self.assertEqual(src.count('(list "'), 0)

    def test_default_option_key_is_additionalArgs(self):
        # Backward compat for ic_from callers (DECISIONS #57): the v1.3
        # readns/readic flow doesn't pass option_key — must still emit
        # additionalArgs.
        spec = PreRunSpec(item_name="x", mode="readns",
                          corner_to_arg={"TT": 'readns="/a"'})
        src = render_pre_run_script(spec)
        self.assertIn('asiSetSimOptionVal asi "additionalArgs"', src)

    def test_option_key_override_threads_into_script(self):
        spec = PreRunSpec(item_name="x", mode="gmin_bump",
                          corner_to_arg={"TT": "1e-10"},
                          option_key="gmin")
        src = render_pre_run_script(spec)
        self.assertIn('asiSetSimOptionVal asi "gmin"', src)
        self.assertNotIn('"additionalArgs"', src)

    def test_baseline_value_none_keeps_ic_from_shape(self):
        # Backward-compat: ic_from never set baseline_value, expects
        # asi only resolved INSIDE (when entry ...).
        spec = PreRunSpec(item_name="x", mode="readns",
                          corner_to_arg={"TT": 'readns="/a"'})
        src = render_pre_run_script(spec)
        # The (setq asi ...) line must be NESTED under (when entry ...)
        # — i.e. it appears AFTER the (when entry line.
        when_entry_pos = src.find("(when entry")
        setq_asi_pos = src.find("(setq asi")
        self.assertGreater(when_entry_pos, 0)
        self.assertGreater(setq_asi_pos, when_entry_pos,
                           "asi must be resolved inside (when entry ...) "
                           "for ic_from back-compat — A5 2026-05-18 verified")
        # No STEP 1 baseline comment.
        self.assertNotIn("STEP 1", src)

    def test_baseline_value_set_emits_baseline_write_first(self):
        # Phase 1 A5 bug fix: when baseline_value is set, the script must
        # restore baseline FIRST on every firing (so previous sub-corner's
        # override doesn't leak through the shared worker-VM asi), THEN
        # conditionally apply the per-corner bump.
        spec = PreRunSpec(item_name="x", mode="gmin_bump",
                          corner_to_arg={"TT_pvt_3": "1e-10"},
                          option_key="gmin",
                          baseline_value="1e-12")
        src = render_pre_run_script(spec)
        # asi resolved UNCONDITIONALLY (outside (when entry ...))
        when_and_pos = src.find("(when (and cornerName")
        setq_asi_pos = src.find("(setq asi")
        when_entry_pos = src.find("(when entry")
        self.assertGreater(setq_asi_pos, when_and_pos)
        self.assertLess(setq_asi_pos, when_entry_pos,
                        "asi must resolve BEFORE (when entry ...) so "
                        "baseline restore can fire even when entry misses")
        # Baseline write present and occurs BEFORE the override write.
        baseline_write = 'asiSetSimOptionVal asi "gmin" "1e-12"'
        override_write_marker = "(cadr entry)"
        self.assertIn(baseline_write, src)
        self.assertLess(src.find(baseline_write),
                        src.find(override_write_marker),
                        "baseline restore must come first")
        # STEP 1 / STEP 2 comments present (documents intent in generated SKILL).
        self.assertIn("STEP 1", src)
        self.assertIn("STEP 2", src)


class WriteScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_prerun_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_writes_file_with_il_extension(self):
        spec = PreRunSpec(item_name="my_item", mode="readns",
                          corner_to_arg={"TT": 'readns="/x.fc"'})
        p = write_pre_run_script(spec, self.tmp)
        self.assertTrue(p.exists())
        self.assertEqual(p.suffix, ".il")
        self.assertIn("my_item", p.name)

    def test_lands_under_subdir(self):
        spec = PreRunSpec(item_name="x", mode="readns", corner_to_arg={})
        p = write_pre_run_script(spec, self.tmp)
        # Default subdir = .simkit/pre_run
        self.assertTrue(str(p).endswith(".il"))
        self.assertIn(".simkit/pre_run/", str(p))

    def test_different_specs_produce_different_filenames(self):
        s1 = PreRunSpec(item_name="x", mode="readns",
                        corner_to_arg={"TT": 'readns="/a"'})
        s2 = PreRunSpec(item_name="x", mode="readns",
                        corner_to_arg={"TT": 'readns="/b"'})
        p1 = write_pre_run_script(s1, self.tmp)
        p2 = write_pre_run_script(s2, self.tmp)
        self.assertNotEqual(p1, p2)  # content hash differs

    def test_same_spec_idempotent_filename(self):
        spec = PreRunSpec(item_name="x", mode="readns",
                          corner_to_arg={"TT": 'readns="/a"'})
        self.assertEqual(write_pre_run_script(spec, self.tmp),
                         write_pre_run_script(spec, self.tmp))

    def test_item_name_with_special_chars_sanitised(self):
        # CJK / spaces / punctuation in item name must not break the
        # filename (filesystem-safe sanitisation).
        spec = PreRunSpec(item_name="干扰仿真 / PSS",
                         mode="readns", corner_to_arg={})
        p = write_pre_run_script(spec, self.tmp)
        self.assertTrue(p.exists())
        # No spaces or slashes in basename
        self.assertNotIn(" ", p.name)
        self.assertNotIn("/", p.name)


# ---------------------------------------------------------------------------
# Phase 3A v1.9 #3 gap #2 — per-test pre-run scripts


class PreRunSpecPerTestFieldTests(unittest.TestCase):
    """The new ``per_test_corner_to_arg`` optional field on PreRunSpec.

    Default is None (preserves v1.3/v1.7 single-map shape). When set to a
    Mapping[test_name, Mapping[corner, arg]], it acts as a per-test
    override consumed by ``write_per_test_pre_run_scripts``.
    """

    def test_default_per_test_field_is_none(self):
        spec = PreRunSpec(item_name="x", mode="readns",
                          corner_to_arg={"TT": 'readns="/a"'})
        self.assertIsNone(spec.per_test_corner_to_arg)

    def test_per_test_field_accepts_mapping(self):
        per_test = {
            "Test": {"TT": 'readns="/a.fc"'},
            "Test_trans": {"TT": 'readns="/b.fc"'},
        }
        spec = PreRunSpec(
            item_name="x", mode="readns",
            corner_to_arg={"TT": 'readns="/c.fc"'},
            per_test_corner_to_arg=per_test,
        )
        self.assertEqual(
            spec.per_test_corner_to_arg["Test"], {"TT": 'readns="/a.fc"'},
        )

    def test_render_pre_run_script_ignores_per_test_field(self):
        # Single render path is unchanged — only write_per_test_*
        # consumes the field. render_pre_run_script consults only the
        # top-level corner_to_arg.
        spec = PreRunSpec(
            item_name="x", mode="readns",
            corner_to_arg={"TT": 'readns="/top.fc"'},
            per_test_corner_to_arg={"t1": {"TT": 'readns="/per_test.fc"'}},
        )
        src = render_pre_run_script(spec)
        self.assertIn('"readns=\\"/top.fc\\""', src)
        self.assertNotIn("/per_test.fc", src)


class WritePerTestPreRunScriptsTests(unittest.TestCase):
    """v1.9 #3 gap #2 helper: write_per_test_pre_run_scripts."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_prerun_pt_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_returns_one_file_per_test_when_per_test_overrides_present(self):
        spec = PreRunSpec(
            item_name="pss",
            mode="readns",
            corner_to_arg={"TT": 'readns="/default.fc"'},
            per_test_corner_to_arg={
                "Test": {"TT": 'readns="/test_a.fc"'},
                "Test_trans": {"TT": 'readns="/test_b.fc"'},
            },
        )
        out = write_per_test_pre_run_scripts(
            spec, ["Test", "Test_trans"], self.tmp,
        )
        self.assertEqual(set(out.keys()), {"Test", "Test_trans"})
        # Two distinct files on disk (divergent content → divergent hash)
        self.assertNotEqual(out["Test"], out["Test_trans"])
        for p in out.values():
            self.assertTrue(p.exists())
        # Each contains its specific path
        self.assertIn("/test_a.fc", out["Test"].read_text())
        self.assertIn("/test_b.fc", out["Test_trans"].read_text())

    def test_falls_back_to_top_level_when_test_absent_from_per_test(self):
        # Per-test only has "Test"; "Test_trans" must inherit the top-level map.
        spec = PreRunSpec(
            item_name="pss", mode="readns",
            corner_to_arg={"TT": 'readns="/default.fc"'},
            per_test_corner_to_arg={
                "Test": {"TT": 'readns="/special.fc"'},
            },
        )
        out = write_per_test_pre_run_scripts(
            spec, ["Test", "Test_trans"], self.tmp,
        )
        self.assertIn("/special.fc", out["Test"].read_text())
        self.assertIn("/default.fc", out["Test_trans"].read_text())

    def test_none_per_test_yields_same_file_for_all_tests(self):
        # v1.3/v1.7 compatibility: per_test_corner_to_arg=None means every
        # test gets the same content (identical hash → identical filename).
        spec = PreRunSpec(
            item_name="pss", mode="readns",
            corner_to_arg={"TT": 'readns="/shared.fc"'},
            per_test_corner_to_arg=None,
        )
        out = write_per_test_pre_run_scripts(
            spec, ["Test", "Test_trans"], self.tmp,
        )
        self.assertEqual(out["Test"], out["Test_trans"])

    def test_empty_tests_list_returns_empty_dict(self):
        spec = PreRunSpec(item_name="x", mode="readns",
                          corner_to_arg={"TT": 'readns="/a"'})
        out = write_per_test_pre_run_scripts(spec, [], self.tmp)
        self.assertEqual(out, {})

    def test_per_test_scripts_carry_correct_corner_to_arg(self):
        # Two tests, two distinct full maps with different keys
        spec = PreRunSpec(
            item_name="pss", mode="readns",
            corner_to_arg={"DEFAULT": 'readns="/x.fc"'},
            per_test_corner_to_arg={
                "Test": {"C1": 'readns="/a1.fc"', "C2": 'readns="/a2.fc"'},
                "Test_trans": {"C1": 'readns="/b1.fc"'},
            },
        )
        out = write_per_test_pre_run_scripts(
            spec, ["Test", "Test_trans"], self.tmp,
        )
        test_a = out["Test"].read_text()
        test_b = out["Test_trans"].read_text()
        # Test has 2 corner entries; Test_trans has 1
        self.assertIn('(list "C1"', test_a)
        self.assertIn('(list "C2"', test_a)
        self.assertIn('(list "C1"', test_b)
        self.assertNotIn('(list "C2"', test_b)

    def test_top_level_corner_to_arg_unchanged_when_per_test_used(self):
        # Pin: the per-test rendering must NOT mutate the parent spec's
        # corner_to_arg in place. Build a frozen spec and verify.
        original_top = {"TT": 'readns="/top.fc"'}
        spec = PreRunSpec(
            item_name="pss", mode="readns",
            corner_to_arg=original_top,
            per_test_corner_to_arg={
                "Test": {"TT": 'readns="/override.fc"'},
            },
        )
        write_per_test_pre_run_scripts(spec, ["Test", "Other"], self.tmp)
        # spec.corner_to_arg points to the same dict we passed in
        self.assertEqual(spec.corner_to_arg, original_top)

    def test_baseline_value_propagates_to_per_test_scripts(self):
        # gmin_bump-style spec — baseline_value must thread through per-test
        # rendering so partial overrides still get the A6 safe-write shape.
        spec = PreRunSpec(
            item_name="g", mode="gmin_bump",
            corner_to_arg={"TT": "1e-10"},
            option_key="gmin",
            baseline_value="1e-12",
            per_test_corner_to_arg={
                "Test": {"TT": "1e-10"},
                "Test_trans": {"TT": "5e-11"},
            },
        )
        out = write_per_test_pre_run_scripts(
            spec, ["Test", "Test_trans"], self.tmp,
        )
        for p in out.values():
            txt = p.read_text()
            self.assertIn("STEP 1", txt)
            self.assertIn('asiSetSimOptionVal asi "gmin" "1e-12"', txt)

    def test_option_key_propagates_to_per_test_scripts(self):
        spec = PreRunSpec(
            item_name="g", mode="custom",
            corner_to_arg={"TT": "v"},
            option_key="reltol",
            per_test_corner_to_arg={
                "T": {"TT": "1e-4"},
            },
        )
        out = write_per_test_pre_run_scripts(spec, ["T"], self.tmp)
        self.assertIn('asiSetSimOptionVal asi "reltol"', out["T"].read_text())

    def test_default_subdir_under_simkit_pre_run(self):
        spec = PreRunSpec(item_name="x", mode="readns",
                          corner_to_arg={"TT": 'readns="/a"'})
        out = write_per_test_pre_run_scripts(spec, ["T"], self.tmp)
        self.assertIn(".simkit/pre_run/", str(out["T"]))

    def test_existing_single_render_path_uses_only_top_level(self):
        # Regression: write_pre_run_script alone (the v1.3/v1.7 path)
        # ignores per_test_corner_to_arg — only the new helper consumes it.
        spec = PreRunSpec(
            item_name="x", mode="readns",
            corner_to_arg={"TT": 'readns="/top.fc"'},
            per_test_corner_to_arg={
                "T": {"TT": 'readns="/per_test.fc"'},
            },
        )
        p = write_pre_run_script(spec, self.tmp)
        self.assertIn("/top.fc", p.read_text())
        self.assertNotIn("/per_test.fc", p.read_text())


if __name__ == "__main__":
    unittest.main()
