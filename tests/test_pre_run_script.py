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
    def test_readns_emits_nodeset_flag(self):
        m = build_corner_arg_map(
            ["TT", "TT_pvt_0"],
            {"TT": "/a/spectre.fc", "TT_pvt_0": "/b/spectre.fc"},
            "readns",
        )
        self.assertEqual(m, {
            "TT": "+nodeset /a/spectre.fc",
            "TT_pvt_0": "+nodeset /b/spectre.fc",
        })

    def test_readic_emits_ic_flag(self):
        m = build_corner_arg_map(
            ["TT"], {"TT": "/p/spectre.ic"}, "readic",
        )
        self.assertEqual(m, {"TT": "+ic /p/spectre.ic"})

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
            corner_to_arg={"TT": "+nodeset /a.fc"},
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
                "TT": "+nodeset /a.fc",
                "TT_pvt_0": "+nodeset /b.fc",
            },
        )
        src = render_pre_run_script(spec)
        # Each entry is a 2-element list (NOT a cons-cell — Cadence
        # SKILL's cons rejects non-list 2nd arg). assoc returns the
        # whole list; cadr extracts the value.
        self.assertIn('(list "TT" "+nodeset /a.fc")', src)
        self.assertIn('(list "TT_pvt_0" "+nodeset /b.fc")', src)

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


class WriteScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_prerun_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_writes_file_with_il_extension(self):
        spec = PreRunSpec(item_name="my_item", mode="readns",
                          corner_to_arg={"TT": "+nodeset /x.fc"})
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
                        corner_to_arg={"TT": "+nodeset /a"})
        s2 = PreRunSpec(item_name="x", mode="readns",
                        corner_to_arg={"TT": "+nodeset /b"})
        p1 = write_pre_run_script(s1, self.tmp)
        p2 = write_pre_run_script(s2, self.tmp)
        self.assertNotEqual(p1, p2)  # content hash differs

    def test_same_spec_idempotent_filename(self):
        spec = PreRunSpec(item_name="x", mode="readns",
                          corner_to_arg={"TT": "+nodeset /a"})
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


if __name__ == "__main__":
    unittest.main()
