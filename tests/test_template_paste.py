"""Unit tests for simkit.template_paste (`paste_to_template`).

Run with stdlib unittest or pytest:

    PYTHONPATH=python python3 -m unittest tests.test_template_paste -v
    python3 -m pytest tests/test_template_paste.py -v
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.template import Template  # noqa: E402
from simkit.template_paste import paste_to_template  # noqa: E402


_EXAMPLE_TMPL = _REPO_ROOT / "config" / "rise_time_threshold.template.json"
# fnxSession0's actual Rtime_clkout expression — matches the example's
# `_pasted_from` field. Used by Gate M1.
_RTIME_CLKOUT_RAW = (
    'average(riseTime(vtime(\'tran "/Vout") 0 nil VAR("VDD") nil 10 90 t "time"))'
)


class SignalExtractionTests(unittest.TestCase):

    def test_single_signal_extracted(self):
        t = paste_to_template(_RTIME_CLKOUT_RAW, name="r")
        self.assertIsInstance(t, Template)
        self.assertIn("$SIG", t.expression)
        self.assertNotIn('"/Vout"', t.expression)
        sig = t.signal_param()
        self.assertIsNotNone(sig)
        self.assertEqual(sig.key, "SIG")

    def test_zero_signals_raises(self):
        # No "/..." literal — should fail.
        with self.assertRaises(ValueError):
            paste_to_template('average(VAR("VDD"))', name="r")

    def test_two_distinct_signals_raises(self):
        with self.assertRaises(ValueError) as cm:
            paste_to_template(
                'diff(vtime(\'tran "/Vout") vtime(\'tran "/Vin"))',
                name="r",
            )
        self.assertIn("multi-signal", str(cm.exception))

    def test_pasted_from_preserved(self):
        t = paste_to_template(_RTIME_CLKOUT_RAW, name="r")
        self.assertEqual(t.pasted_from, _RTIME_CLKOUT_RAW)


class ShortAliasTests(unittest.TestCase):

    def test_auto_derive_from_outer_function(self):
        t = paste_to_template(_RTIME_CLKOUT_RAW, name="r")
        self.assertEqual(t.short_alias, "Average")

    def test_override_short_alias(self):
        t = paste_to_template(
            _RTIME_CLKOUT_RAW, name="r", short_alias="Rtime",
        )
        self.assertEqual(t.short_alias, "Rtime")

    def test_invalid_short_alias_override_raises(self):
        with self.assertRaises(ValueError):
            paste_to_template(
                _RTIME_CLKOUT_RAW, name="r", short_alias="bad-alias",
            )

    def test_invalid_name_raises(self):
        with self.assertRaises(ValueError):
            paste_to_template(_RTIME_CLKOUT_RAW, name="BadName")


class NumericPromptTests(unittest.TestCase):

    def test_no_prompt_retains_all_numerics(self):
        t = paste_to_template(_RTIME_CLKOUT_RAW, name="r")
        # No NUM_/V_ params — all numerics kept.
        keys = [p.key for p in t.params]
        self.assertEqual(keys, ["SIG"])
        # Literals 0, 10, 90 still in expression.
        self.assertIn(" 10 ", t.expression)
        self.assertIn(" 90 ", t.expression)

    def test_prompt_always_no_retains_literals(self):
        calls: list[str] = []

        def always_no(msg: str) -> bool:
            calls.append(msg)
            return False

        t = paste_to_template(_RTIME_CLKOUT_RAW, name="r", prompt=always_no)
        keys = [p.key for p in t.params]
        self.assertEqual(keys, ["SIG"])
        self.assertGreater(len(calls), 0)

    def test_prompt_two_yeses_yields_V_LOW_V_HIGH(self):
        """Two `yes` answers → params V_LOW, V_HIGH in source order
        (matches the example template + Gate M1 contract)."""
        # Want exactly literals 10 and 90 parameterised; say no to 0.
        yeses_for = {"10", "90"}

        def prompt(msg: str) -> bool:
            m = re.search(r"parameterise literal '([^']*)'", msg)
            assert m is not None
            return m.group(1) in yeses_for

        t = paste_to_template(_RTIME_CLKOUT_RAW, name="r", prompt=prompt)
        keys = [p.key for p in t.params]
        self.assertEqual(keys, ["SIG", "V_LOW", "V_HIGH"])
        # In source order, 10 comes first → V_LOW; 90 → V_HIGH.
        bk = {p.key: p for p in t.params}
        self.assertEqual(bk["V_LOW"].default, "10")
        self.assertEqual(bk["V_HIGH"].default, "90")

    def test_prompt_one_yes_yields_NUM_1(self):
        yeses_for = {"10"}

        def prompt(msg: str) -> bool:
            m = re.search(r"parameterise literal '([^']*)'", msg)
            return m.group(1) in yeses_for

        t = paste_to_template(_RTIME_CLKOUT_RAW, name="r", prompt=prompt)
        keys = [p.key for p in t.params]
        self.assertEqual(keys, ["SIG", "NUM_1"])

    def test_prompt_three_yeses_yields_NUM_1_2_3(self):
        # Say yes to all three: 0, 10, 90.
        def prompt(msg: str) -> bool:
            return True

        t = paste_to_template(_RTIME_CLKOUT_RAW, name="r", prompt=prompt)
        keys = [p.key for p in t.params]
        self.assertEqual(keys, ["SIG", "NUM_1", "NUM_2", "NUM_3"])

    def test_numerics_inside_string_skipped(self):
        # The "time" string + "VDD" string contain no numerics, but let's
        # construct one that does.
        raw = 'average(vtime(\'tran "/Vout") "param10" 5)'
        calls: list[str] = []

        def prompt(msg: str) -> bool:
            calls.append(msg)
            return False

        paste_to_template(raw, name="r", prompt=prompt)
        # Only "5" should be offered (the "10" inside "param10" is in-string).
        self.assertEqual(len(calls), 1)
        self.assertIn("'5'", calls[0])


class GateM1PasteRoundTripTests(unittest.TestCase):
    """Gate M1 — Paste-import faithfulness.

    Paste fnxSession0's Rtime_clkout expression; parameterise 10 → V_LOW,
    90 → V_HIGH; render via the bundle path with signal=/Vout and the
    template's own V_LOW=10, V_HIGH=90 defaults. The rendered expression
    must equal the original input modulo whitespace.
    """

    @staticmethod
    def _normalise(s: str) -> str:
        return re.sub(r"\s+", " ", s).strip()

    def test_pasted_from_matches_example_fixture(self):
        # The pasted_from text we use must match the example sidecar's
        # _pasted_from field — anchors the M1 round-trip to a real fixture.
        data = json.loads(_EXAMPLE_TMPL.read_text(encoding="utf-8"))
        self.assertEqual(data["_pasted_from"], _RTIME_CLKOUT_RAW)

    def test_paste_then_render_equals_input(self):
        # Paste-importer preserves the surrounding "..." around the signal
        # path (`"/Vout"` → `"$SIG"`), so template body keeps the caller's
        # quoting and render-side bare-string substitution ($SIG → /Vout)
        # reconstitutes the original verbatim. No manual editing needed.
        def prompt(msg: str) -> bool:
            m = re.search(r"parameterise literal '([^']*)'", msg)
            return m.group(1) in {"10", "90"}

        t = paste_to_template(
            _RTIME_CLKOUT_RAW,
            name="rise_time_threshold",
            short_alias="Rtime",
            prompt=prompt,
        )

        # Render via the substitution machinery directly (no need to spin up
        # a whole bundle just for one row).
        from simkit.template_render import _substitute  # noqa: WPS437
        rendered = _substitute(
            t, overrides={}, signal_value="/Vout", idx=0,
        )

        self.assertEqual(
            self._normalise(rendered),
            self._normalise(_RTIME_CLKOUT_RAW),
        )

    def test_param_count_matches_example(self):
        def prompt(msg: str) -> bool:
            m = re.search(r"parameterise literal '([^']*)'", msg)
            return m.group(1) in {"10", "90"}

        t = paste_to_template(
            _RTIME_CLKOUT_RAW, name="rise_time_threshold", prompt=prompt,
        )
        # 3 params (SIG + V_LOW + V_HIGH), matching the example template.
        self.assertEqual(len(t.params), 3)


class TempDirSerialiseTests(unittest.TestCase):
    """The paste output must be serialisable through `load_template` after
    being persisted to disk — sanity-check that paste-importer produces a
    document the loader actually accepts."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_paste_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_paste_roundtrips_through_loader(self):
        def prompt(msg: str) -> bool:
            return True

        t = paste_to_template(
            _RTIME_CLKOUT_RAW, name="rise_time_threshold", prompt=prompt,
        )
        # Persist as JSON.
        doc = {
            "template_schema_version": t.template_schema_version,
            "name": t.name,
            "short_alias": t.short_alias,
            "expression": t.expression,
            "params": [
                {
                    "key": p.key,
                    "kind": p.kind,
                    **({"default": p.default} if p.default is not None else {}),
                }
                for p in t.params
            ],
            "eval_type": t.eval_type,
            "plot": t.plot,
            "save": t.save,
            "_pasted_from": t.pasted_from,
        }
        path = self.tmp / f"{t.name}.template.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        from simkit.template import load_template
        loaded = load_template(path)
        self.assertEqual(loaded.name, t.name)
        self.assertEqual(loaded.expression, t.expression)
        self.assertEqual(len(loaded.params), len(t.params))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
