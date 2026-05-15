"""Tests for the shipped builtin templates under ``config/builtins/``.

Every ``*.template.json`` in that directory must:
1. load via :func:`simkit.template.load_template` without error;
2. render via :func:`simkit.template_render.render_bundle` when given a
   plausible signal value (for signal-kind templates) and overrides for any
   required-no-default param;
3. round-trip its expression byte-for-byte against a small set of
   reverse-engineered references covering the user's real DCOBUF formulas.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.measure_bundle import MeasureApply, MeasureBundle  # noqa: E402
from simkit.signal_group import SignalGroup  # noqa: E402
from simkit.template import (  # noqa: E402
    TEMPLATE_FILE_SUFFIX,
    Template,
    load_template,
)
from simkit.template_render import render_bundle  # noqa: E402

_BUILTINS_DIR = _REPO_ROOT / "config" / "builtins"

_EXPECTED_BUILTIN_NAMES = frozenset({
    "i_avg_window",
    "i_avg_full",
    "freq_window",
    "duty_cycle_window",
    "rise_time_auto",
    "fall_time_auto",
    "rise_time_fixed",
    "fall_time_fixed",
    # v1.2 (b) — unwindowed rise/fall variants. Follow the i_avg_window /
    # i_avg_full naming precedent rather than adding a CLIP parameter.
    "rise_time_auto_full",
    "rise_time_fixed_full",
    "fall_time_auto_full",
    "fall_time_fixed_full",
    "dft_window",
    "dft_mag_at_freq",
    "dft_phase_at_freq",
    "db20_ratio",
    "edge_delay_avg",
    "edge_delay_wave",
    "cycle_wrap_positive",
    "phase_diff_wrap",
    "value_at",
})


def _render_once(
    template: Template,
    signal: str | None,
    overrides: dict[str, str],
) -> str:
    has_sig = template.signal_param() is not None
    if has_sig and signal is None:
        raise AssertionError(
            f"{template.name}: signal template called with signal=None"
        )
    sg = (
        SignalGroup(
            signal_group_schema_version=1,
            name="probe",
            signals=(signal,),
            source_path=Path("-"),
        )
        if has_sig
        else None
    )
    apply = MeasureApply(
        template=template,
        signal_group=sg,
        param_overrides=overrides,
        alias_suffix="",
    )
    bundle = MeasureBundle(
        measure_schema_version=1,
        name="probe",
        project="p",
        testbench_id="L/C/V",
        test_name="Test",
        apply=(apply,),
        source_path=Path("-"),
    )
    return render_bundle(bundle)[0].expression


class BuiltinsLoadTests(unittest.TestCase):

    def test_builtins_dir_exists(self):
        self.assertTrue(
            _BUILTINS_DIR.is_dir(),
            f"missing builtins dir: {_BUILTINS_DIR}",
        )

    def test_all_expected_names_present(self):
        found = {
            p.name[: -len(TEMPLATE_FILE_SUFFIX)]
            for p in _BUILTINS_DIR.glob(f"*{TEMPLATE_FILE_SUFFIX}")
        }
        missing = _EXPECTED_BUILTIN_NAMES - found
        extra = found - _EXPECTED_BUILTIN_NAMES
        self.assertFalse(missing, f"missing builtin(s): {sorted(missing)}")
        self.assertFalse(
            extra,
            f"unexpected file(s) in builtins dir: {sorted(extra)}",
        )

    def test_each_builtin_loads(self):
        for path in sorted(_BUILTINS_DIR.glob(f"*{TEMPLATE_FILE_SUFFIX}")):
            with self.subTest(name=path.name):
                t = load_template(path)
                # name matches filename basename
                self.assertEqual(
                    t.name,
                    path.name[: -len(TEMPLATE_FILE_SUFFIX)],
                )
                self.assertEqual(t.template_schema_version, 1)
                # short_alias is set (we always set it for builtins)
                self.assertNotEqual(t.short_alias, "")


# --------------------------------------------------------------------------
# Render contract — supply a plausible signal + override for any required
# param that lacks a default. The render must succeed.
# --------------------------------------------------------------------------


_RENDER_INPUTS: dict[str, tuple[str | None, dict[str, str]]] = {
    "i_avg_window":        ("/L1/PLUS", {}),
    "i_avg_full":          ("/L1/PLUS", {}),
    "freq_window":         ("/bufp", {}),
    "duty_cycle_window":   ("/bufp", {}),
    "rise_time_auto":      ("/bufp", {}),
    "fall_time_auto":      ("/bufp", {}),
    "rise_time_fixed":     ("/Vout", {}),
    "fall_time_fixed":     ("/Vout", {}),
    "rise_time_auto_full": ("/bufp", {}),
    "fall_time_auto_full": ("/bufp", {}),
    "rise_time_fixed_full": ("/Vout", {}),
    "fall_time_fixed_full": ("/Vout", {}),
    "dft_window":          ("/LOIP", {}),
    "dft_mag_at_freq":     (None,    {"OUT_NAME": "LOIP_DFT",  "FREQ": "2.5e9"}),
    "dft_phase_at_freq":   (None,    {"OUT_NAME": "LOIP_DFT",  "FREQ": "2.5e9"}),
    "db20_ratio":          (None,    {"OUT_A": "LOIP_mag",     "OUT_B": "LOQP_mag"}),
    "edge_delay_avg":      ("/LOIN_5G", {"SIG_B": "/clk_ref"}),
    "edge_delay_wave":     ("/LOIN_5G", {"SIG_B": "/clk_ref"}),
    "cycle_wrap_positive": (None,    {"OUT_NAME": "Tedge_5G"}),
    "phase_diff_wrap":     (None,    {"OUT_NAME": "PhaseDiff"}),
    "value_at":            (None,    {"OUT_NAME": "phaseNoise", "X": "1000"}),
}


class BuiltinsRenderTests(unittest.TestCase):

    def test_every_builtin_renders(self):
        names_covered = set()
        for path in sorted(_BUILTINS_DIR.glob(f"*{TEMPLATE_FILE_SUFFIX}")):
            name = path.name[: -len(TEMPLATE_FILE_SUFFIX)]
            with self.subTest(name=name):
                self.assertIn(
                    name, _RENDER_INPUTS,
                    f"add a render input fixture for {name}",
                )
                signal, overrides = _RENDER_INPUTS[name]
                t = load_template(path)
                expr = _render_once(t, signal, overrides)
                # Render must produce non-empty output with no leftover $TOKENS
                self.assertTrue(expr)
                self.assertNotIn("$", expr, f"unresolved token in: {expr!r}")
                names_covered.add(name)
        self.assertEqual(names_covered, _EXPECTED_BUILTIN_NAMES)


# --------------------------------------------------------------------------
# Byte-for-byte reverse-engineering checks — these are the DCOBUF formulas
# the user supplied, reconstructed via the template + their actual overrides.
# --------------------------------------------------------------------------


_REFERENCE_RENDERS = [
    # row from DCOBUF: sim_DCOBUF,1,expr,average(clip(itime('tran "/I_BUF2G/DCO2G_buf_to_adpll/VDD") VAR("t_1") VAR("t_2")))
    (
        "i_avg_window",
        "/I_BUF2G/DCO2G_buf_to_adpll/VDD",
        {},
        'average(clip(itime(\'tran "/I_BUF2G/DCO2G_buf_to_adpll/VDD") VAR("t_1") VAR("t_2")))',
    ),
    # I_dco2gbuf_total_ave_all,expr,average(itime('tran "/L1/PLUS"))
    (
        "i_avg_full",
        "/L1/PLUS",
        {},
        'average(itime(\'tran "/L1/PLUS"))',
    ),
    # PSS power equivalent (user-cited shape)
    (
        "i_avg_full",
        "/L1/PLUS",
        {"ANALYSIS": "pss"},
        'average(itime(\'pss "/L1/PLUS"))',
    ),
    # Freq_bufp_2g,expr,frequency(clip(vtime('tran "/bufp") VAR("t_1") VAR("t_2")))
    (
        "freq_window",
        "/bufp",
        {},
        'frequency(clip(vtime(\'tran "/bufp") VAR("t_1") VAR("t_2")))',
    ),
    # DutyC_dco2g_bufp,expr,dutyCycle(clip(vtime('tran "/bufp") VAR("t_1") VAR("t_2")) ?mode "auto" ?xName "cycle" ?outputType "average")
    (
        "duty_cycle_window",
        "/bufp",
        {},
        'dutyCycle(clip(vtime(\'tran "/bufp") VAR("t_1") VAR("t_2")) '
        '?mode "auto" ?xName "cycle" ?outputType "average")',
    ),
    # Rtime_dco2g_bufp,expr,average(riseTime(clip(vtime('tran "/bufp") VAR("t_1") VAR("t_2"))
    # ymin(clip(vtime('tran "/bufp") VAR("t_1") VAR("t_2"))) nil ymax(clip(vtime('tran "/bufp") VAR("t_1") VAR("t_2"))) nil 10 90 t "time"))
    (
        "rise_time_auto",
        "/bufp",
        {},
        'average(riseTime(clip(vtime(\'tran "/bufp") VAR("t_1") VAR("t_2")) '
        'ymin(clip(vtime(\'tran "/bufp") VAR("t_1") VAR("t_2"))) nil '
        'ymax(clip(vtime(\'tran "/bufp") VAR("t_1") VAR("t_2"))) nil '
        '10 90 t "time"))',
    ),
    # Ftime_dco2g_bufp,expr,... (same body but 90 10)
    (
        "fall_time_auto",
        "/bufp",
        {},
        'average(riseTime(clip(vtime(\'tran "/bufp") VAR("t_1") VAR("t_2")) '
        'ymin(clip(vtime(\'tran "/bufp") VAR("t_1") VAR("t_2"))) nil '
        'ymax(clip(vtime(\'tran "/bufp") VAR("t_1") VAR("t_2"))) nil '
        '90 10 t "time"))',
    ),
    # LOIP_2G_amp_DFF,expr,dft(vtime('tran "/LOIP") VAR("t_3") VAR("t_4") 512 "Rectangular" 0 0 1)
    (
        "dft_window",
        "/LOIP",
        {},
        'dft(vtime(\'tran "/LOIP") VAR("t_3") VAR("t_4") 512 "Rectangular" 0 0 1)',
    ),
    # LOIP_2G_amp_DFF_2P5G,expr,mag(value(LOIP_2G_amp_DFF 2.5e+09))
    (
        "dft_mag_at_freq",
        None,
        {"OUT_NAME": "LOIP_2G_amp_DFF", "FREQ": "2.5e+09"},
        "mag(value(LOIP_2G_amp_DFF 2.5e+09))",
    ),
    # Phase_I_2P5G,expr,value(phaseDeg(LOIP_2G_amp_DFF) 2.5e+09)
    (
        "dft_phase_at_freq",
        None,
        {"OUT_NAME": "LOIP_2G_amp_DFF", "FREQ": "2.5e+09"},
        "value(phaseDeg(LOIP_2G_amp_DFF) 2.5e+09)",
    ),
    # IQmis_Amp_dB_2P5G,expr,dB20((LOIP_2G_amp_DFF_2P5G / LOQP_2G_amp_DFF_2P5G))
    (
        "db20_ratio",
        None,
        {"OUT_A": "LOIP_2G_amp_DFF_2P5G", "OUT_B": "LOQP_2G_amp_DFF_2P5G"},
        "dB20((LOIP_2G_amp_DFF_2P5G / LOQP_2G_amp_DFF_2P5G))",
    ),
    # T_criteria_temp_5G,expr,average((cross(clip(vtime('tran "/LOIN_5G") VAR("t_3") VAR("t_4")) (VAR("VDD_DCO") / 2) 1 "rising" t "cycle" nil) - cross(clip(vtime('tran "/clk2ckgating_out") VAR("t_3") VAR("t_4")) (VAR("VDD_DCO") / 2) 1 "rising" t "cycle" nil)))
    (
        "edge_delay_avg",
        "/LOIN_5G",
        {
            "SIG_B": "/clk2ckgating_out",
            "THRESH": '(VAR("VDD_DCO") / 2)',
        },
        'average((cross(clip(vtime(\'tran "/LOIN_5G") VAR("t_3") VAR("t_4")) '
        '(VAR("VDD_DCO") / 2) 1 "rising" t "cycle" nil) - '
        'cross(clip(vtime(\'tran "/clk2ckgating_out") VAR("t_3") VAR("t_4")) '
        '(VAR("VDD_DCO") / 2) 1 "rising" t "cycle" nil)))',
    ),
    # T_criteria_5G,expr,if((T_criteria_temp_5G < 0) then (T_criteria_temp_5G + (1 / VAR("flo_5g"))) else T_criteria_temp_5G)
    (
        "cycle_wrap_positive",
        None,
        {
            "OUT_NAME": "T_criteria_temp_5G",
            "PERIOD_EXPR": '(1 / VAR("flo_5g"))',
        },
        'if((T_criteria_temp_5G < 0) then (T_criteria_temp_5G + '
        '(1 / VAR("flo_5g"))) else T_criteria_temp_5G)',
    ),
    # Phase_IQ_diff_2P5G,expr,if((Phase_IQ_diff_temp_2P5G > 0) then (Phase_IQ_diff_temp_2P5G - 90) else (Phase_IQ_diff_temp_2P5G + 270))
    (
        "phase_diff_wrap",
        None,
        {"OUT_NAME": "Phase_IQ_diff_temp_2P5G"},
        'if((Phase_IQ_diff_temp_2P5G > 0) then (Phase_IQ_diff_temp_2P5G - 90) '
        'else (Phase_IQ_diff_temp_2P5G + (360 - 90)))',
    ),
    # value_at: user-cited "value(PN 1000)"
    (
        "value_at",
        None,
        {"OUT_NAME": "PN", "X": "1000"},
        "value(PN 1000)",
    ),
    # rise_time_fixed at default VDD/VSS rails
    (
        "rise_time_fixed",
        "/Vout",
        {},
        'average(riseTime(clip(vtime(\'tran "/Vout") VAR("t_1") VAR("t_2")) '
        '0 nil VAR("VDD") nil 10 90 t "time"))',
    ),
    # v1.2 (b): rise_time_fixed_full at default rails — exactly matches the
    # live fnxSession0 Rtime_clkout output expression (unwindowed form).
    (
        "rise_time_fixed_full",
        "/Vout",
        {},
        'average(riseTime(vtime(\'tran "/Vout") '
        '0 nil VAR("VDD") nil 10 90 t "time"))',
    ),
    # edge_delay_wave
    (
        "edge_delay_wave",
        "/LOIN_5G",
        {
            "SIG_B": "/clk2ckgating_out",
            "THRESH": '(VAR("VDD_DCO") / 2)',
        },
        '(cross(clip(vtime(\'tran "/LOIN_5G") VAR("t_3") VAR("t_4")) '
        '(VAR("VDD_DCO") / 2) 1 "rising" t "cycle" nil) - '
        'cross(clip(vtime(\'tran "/clk2ckgating_out") VAR("t_3") VAR("t_4")) '
        '(VAR("VDD_DCO") / 2) 1 "rising" t "cycle" nil))',
    ),
]


class BuiltinsByteForByteTests(unittest.TestCase):
    """Per builtin, render against an exact reference string lifted from
    (or reverse-engineered from) the user's real DCOBUF Outputs CSV."""

    def test_byte_for_byte_against_dcobuf(self):
        for name, signal, overrides, expected in _REFERENCE_RENDERS:
            with self.subTest(template=name):
                t = load_template(
                    _BUILTINS_DIR / f"{name}{TEMPLATE_FILE_SUFFIX}"
                )
                actual = _render_once(t, signal, overrides)
                self.assertEqual(actual, expected)


# --------------------------------------------------------------------------
# Note on the phase_diff_wrap case: the user's original CSV writes the
# constant `270` directly, while our template body emits `(360 - 90)`.
# Both are arithmetically equal; Cadence evaluates the literal-form
# expression at parse time and the rendered output is computationally
# identical. We deliberately keep the (360 - OFFSET) form parameterised so
# OFFSET=60 (or whatever) wraps correctly without a second template.
# --------------------------------------------------------------------------


if __name__ == "__main__":
    unittest.main()
