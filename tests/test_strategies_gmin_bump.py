"""Unit tests for ``simkit.strategies.gmin_bump`` — per-corner gmin floor
override via pre-run script (v1.7 follow-up to DECISIONS #62 chain dispatch).
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.strategies.base import (  # noqa: E402
    StrategyContext,
    StrategyOutcome,
)
from simkit.strategies.gmin_bump import (  # noqa: E402
    DEFAULT_OPTION_NAME,
    DEFAULT_RAMP,
    GminBump,
    _format_value,
)


class _MockBridge:
    """Records every call so tests can assert call ordering and args.

    Mirrors the production shape (verified against skill_bridge.py):
      * snapshot_corners_enable returns list[tuple[str, bool]]
      * restore_corners_enable takes target + session kw
      * install_pre_run_script takes (test_name, script_path, *, session)
      * disable_pre_run_script takes (test_name, *, session)
      * get_pre_run_script takes (test_name, *, session) → str
      * pvt_runner_run takes (history_name, *, session, **kw) → tuple
    """

    def __init__(self, snap, *, prior_scripts=None):
        self._snap = list(snap)
        self._prior_scripts = dict(prior_scripts or {})
        self.calls: list[tuple] = []

    def pvt_runner_snapshot_corners_enable(self, *, session):
        self.calls.append(("snapshot", session))
        return [(n, en) for n, en in self._snap]

    def pvt_runner_restore_corners_enable(self, target, *, session):
        self.calls.append(("restore", session, list(target)))

    def pvt_runner_get_pre_run_script(self, test_name, *, session):
        self.calls.append(("get_prerun", session, test_name))
        return self._prior_scripts.get(test_name, "")

    def pvt_runner_install_pre_run_script(self, test_name, script_path, *,
                                          session):
        self.calls.append(("install_prerun", session, test_name, script_path))
        return script_path

    def pvt_runner_disable_pre_run_script(self, test_name, *, session):
        self.calls.append(("disable_prerun", session, test_name))

    def pvt_runner_run(self, history_name, *, session, **kw):
        self.calls.append(("run", session, history_name, kw))
        return (1, 1, history_name)


def _ctx(*, failed, item="item1", attempt=1, bridge=None, params=None):
    """failed is list of (corner, test) OR list of corner-strings (test=t1)."""
    fcs = []
    for entry in failed:
        if isinstance(entry, tuple):
            c, t = entry
            fcs.append((c, t, "spec_fail"))
        else:
            fcs.append((entry, "t1", "spec_fail"))
    return StrategyContext(
        session="sess",
        item_name=item,
        failed_corners=tuple(fcs),
        attempt_number=attempt,
        bridge=bridge,
        params=params or {},
    )


def _read_installed_script(bridge):
    install = next(c for c in bridge.calls if c[0] == "install_prerun")
    path = install[3]
    return Path(path).read_text(encoding="utf-8")


class GminBumpHappyPathTests(unittest.TestCase):

    def setUp(self):
        # Push gmin_bump's tempfile output to an isolated dir so tests
        # don't leave stray scripts in /tmp across runs.
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_gmin_test_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _params(self, **extra):
        return {"workdir": str(self.tmp), **extra}

    def test_narrows_enable_to_failed_corners(self):
        bridge = _MockBridge([("TT", True), ("SS", True), ("FF", True)])
        strat = GminBump()
        res = strat.apply(_ctx(failed=["SS"], bridge=bridge,
                               params=self._params()))

        self.assertEqual(res.outcome, StrategyOutcome.UNCHANGED)
        first_restore = next(c for c in bridge.calls if c[0] == "restore")
        self.assertEqual(
            first_restore[2],
            [("TT", False), ("SS", True), ("FF", False)],
        )

    def test_restores_original_in_finally(self):
        snap = [("TT", True), ("SS", False), ("FF", True)]
        bridge = _MockBridge(snap)
        strat = GminBump()
        strat.apply(_ctx(failed=["TT"], bridge=bridge,
                         params=self._params()))

        restores = [c for c in bridge.calls if c[0] == "restore"]
        self.assertEqual(len(restores), 2)
        self.assertEqual(restores[-1][2],
                         [("TT", True), ("SS", False), ("FF", True)])

    def test_disables_pre_run_script_on_teardown_when_no_prior(self):
        bridge = _MockBridge([("TT", True), ("SS", True)])
        strat = GminBump()
        strat.apply(_ctx(failed=["TT"], bridge=bridge,
                         params=self._params()))

        teardown = [c for c in bridge.calls
                    if c[0] in ("disable_prerun", "install_prerun")]
        # Two install_prerun events fired: one for the FAIL's test ("t1"),
        # zero for restoration since prior was empty. Then disable_prerun.
        installs = [c for c in teardown if c[0] == "install_prerun"]
        disables = [c for c in teardown if c[0] == "disable_prerun"]
        self.assertEqual(len(installs), 1)
        self.assertEqual(len(disables), 1)
        self.assertEqual(disables[0][2], "t1")

    def test_restores_prior_script_when_user_had_one(self):
        bridge = _MockBridge(
            [("TT", True)],
            prior_scripts={"t1": "/user/prior.il"},
        )
        strat = GminBump()
        strat.apply(_ctx(failed=["TT"], bridge=bridge,
                         params=self._params()))

        installs = [c for c in bridge.calls if c[0] == "install_prerun"]
        # First install is OUR script, second is the prior restore.
        self.assertEqual(len(installs), 2)
        self.assertEqual(installs[-1][3], "/user/prior.il")

    def test_restore_runs_even_when_pvt_runner_run_raises(self):
        bridge = _MockBridge([("TT", True), ("SS", True)])
        def boom(*a, **kw):
            raise RuntimeError("axlRunAllTests blew up")
        bridge.pvt_runner_run = boom

        strat = GminBump()
        with self.assertRaises(RuntimeError):
            strat.apply(_ctx(failed=["TT"], bridge=bridge,
                             params=self._params()))

        # Both corner-restore AND pre-run disable must have fired.
        restores = [c for c in bridge.calls if c[0] == "restore"]
        self.assertEqual(restores[-1][2], [("TT", True), ("SS", True)])
        disables = [c for c in bridge.calls if c[0] == "disable_prerun"]
        self.assertEqual(len(disables), 1)

    def test_history_name_includes_attempt_and_is_sanitized(self):
        bridge = _MockBridge([("TT", True)])
        strat = GminBump()
        res = strat.apply(_ctx(item="My Item!", failed=["TT"], attempt=2,
                               bridge=bridge, params=self._params()))
        self.assertEqual(res.new_history_name, "My_Item___gmin2")
        run_call = next(c for c in bridge.calls if c[0] == "run")
        self.assertEqual(run_call[2], "My_Item___gmin2")


class GminBumpRampTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_gmin_test_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _params(self, **extra):
        return {"workdir": str(self.tmp), **extra}

    def test_attempt_1_uses_first_ramp_value(self):
        bridge = _MockBridge([("TT", True)])
        strat = GminBump()
        strat.apply(_ctx(failed=["TT"], attempt=1, bridge=bridge,
                         params=self._params()))
        script = _read_installed_script(bridge)
        self.assertIn(_format_value(DEFAULT_RAMP[0]), script)

    def test_attempt_3_uses_third_ramp_value(self):
        bridge = _MockBridge([("TT", True)])
        strat = GminBump()
        strat.apply(_ctx(failed=["TT"], attempt=3, bridge=bridge,
                         params=self._params()))
        script = _read_installed_script(bridge)
        self.assertIn(_format_value(DEFAULT_RAMP[2]), script)

    def test_attempt_beyond_ramp_reuses_last_value(self):
        bridge = _MockBridge([("TT", True)])
        strat = GminBump()
        strat.apply(_ctx(failed=["TT"], attempt=99, bridge=bridge,
                         params=self._params(ramp=[1e-11, 1e-9])))
        script = _read_installed_script(bridge)
        self.assertIn("1e-09", script)
        self.assertNotIn("1e-11", script)

    def test_custom_ramp_overrides_default(self):
        bridge = _MockBridge([("TT", True)])
        strat = GminBump()
        strat.apply(_ctx(failed=["TT"], attempt=2, bridge=bridge,
                         params=self._params(ramp=[1e-8, 1e-6, 1e-4])))
        script = _read_installed_script(bridge)
        self.assertIn("1e-06", script)

    def test_string_ramp_values_passed_verbatim(self):
        bridge = _MockBridge([("TT", True)])
        strat = GminBump()
        strat.apply(_ctx(failed=["TT"], attempt=1, bridge=bridge,
                         params=self._params(ramp=["100p", "1n"])))
        script = _read_installed_script(bridge)
        self.assertIn('"100p"', script)

    def test_empty_ramp_gives_up(self):
        bridge = _MockBridge([("TT", True)])
        strat = GminBump()
        res = strat.apply(_ctx(failed=["TT"], bridge=bridge,
                               params=self._params(ramp=[])))
        self.assertEqual(res.outcome, StrategyOutcome.GAVE_UP)
        # Bridge must not have been touched.
        self.assertEqual(bridge.calls, [])


class GminBumpOptionNameTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_gmin_test_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_option_name_is_gmin(self):
        bridge = _MockBridge([("TT", True)])
        strat = GminBump()
        strat.apply(_ctx(failed=["TT"], bridge=bridge,
                         params={"workdir": str(self.tmp)}))
        script = _read_installed_script(bridge)
        self.assertIn('asiSetSimOptionVal asi "gmin"', script)

    def test_option_name_override(self):
        bridge = _MockBridge([("TT", True)])
        strat = GminBump()
        strat.apply(_ctx(failed=["TT"], bridge=bridge,
                         params={"workdir": str(self.tmp),
                                 "option_name": "gmindc"}))
        script = _read_installed_script(bridge)
        self.assertIn('asiSetSimOptionVal asi "gmindc"', script)
        self.assertNotIn('"additionalArgs"', script)


class GminBumpMultiTestTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_gmin_test_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_installs_pre_run_on_each_test_with_failures(self):
        bridge = _MockBridge([("TT", True), ("SS", True)])
        strat = GminBump()
        # TT failed on test "t1", SS failed on test "t2".
        strat.apply(_ctx(failed=[("TT", "t1"), ("SS", "t2")], bridge=bridge,
                         params={"workdir": str(self.tmp)}))
        installs = [c for c in bridge.calls if c[0] == "install_prerun"]
        # Two distinct test names → two installs of our script.
        installed_test_names = sorted({c[2] for c in installs})
        self.assertEqual(installed_test_names, ["t1", "t2"])

    def test_single_test_one_install(self):
        bridge = _MockBridge([("TT", True), ("SS", True)])
        strat = GminBump()
        strat.apply(_ctx(failed=[("TT", "t1"), ("SS", "t1")], bridge=bridge,
                         params={"workdir": str(self.tmp)}))
        installs = [c for c in bridge.calls if c[0] == "install_prerun"]
        installed_test_names = sorted({c[2] for c in installs})
        self.assertEqual(installed_test_names, ["t1"])


class GminBumpSubCornerMappingTests(unittest.TestCase):
    """DB FAIL is at sub-corner ('TT_pvt_3'); bridge snap is row-level
    ('TT_pvt'). Verify the prefix-match mapping picks the right row AND
    that BOTH sub-corner name + parent row name land in the script's map
    (the worker hook may see either string at runtime)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_gmin_test_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_subcorner_maps_to_parent_row_for_enable(self):
        bridge = _MockBridge([("TT", True), ("TT_pvt", True)])
        strat = GminBump()
        res = strat.apply(_ctx(failed=["TT_pvt_3"], bridge=bridge,
                               params={"workdir": str(self.tmp)}))
        self.assertEqual(res.outcome, StrategyOutcome.UNCHANGED)
        first_restore = next(c for c in bridge.calls if c[0] == "restore")
        self.assertEqual(first_restore[2],
                         [("TT", False), ("TT_pvt", True)])

    def test_script_table_carries_both_sub_and_row_name(self):
        # Belt-and-braces: cornerMap embeds TT_pvt_3 (what the per-point
        # firing actually emits) AND TT_pvt (what a scalar firing would
        # emit). Either string in the runtime's assoc lookup hits.
        bridge = _MockBridge([("TT", True), ("TT_pvt", True)])
        strat = GminBump()
        strat.apply(_ctx(failed=["TT_pvt_3"], bridge=bridge,
                         params={"workdir": str(self.tmp)}))
        script = _read_installed_script(bridge)
        self.assertIn('(list "TT_pvt_3"', script)
        self.assertIn('(list "TT_pvt"', script)


class GminBumpEdgeCaseTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_gmin_test_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty_failed_set_gives_up_without_calling_bridge(self):
        bridge = _MockBridge([("TT", True)])
        strat = GminBump()
        res = strat.apply(_ctx(failed=[], bridge=bridge,
                               params={"workdir": str(self.tmp)}))
        self.assertEqual(res.outcome, StrategyOutcome.GAVE_UP)
        self.assertEqual(bridge.calls, [])
        self.assertIsNone(res.new_history_name)

    def test_failed_corner_not_in_live_table_gives_up(self):
        bridge = _MockBridge([("TT", True), ("SS", True)])
        strat = GminBump()
        res = strat.apply(_ctx(failed=["NOPE"], bridge=bridge,
                               params={"workdir": str(self.tmp)}))
        self.assertEqual(res.outcome, StrategyOutcome.GAVE_UP)
        self.assertEqual([c[0] for c in bridge.calls], ["snapshot"])

    def test_notes_mention_attempt_and_value(self):
        bridge = _MockBridge([("TT", True)])
        strat = GminBump()
        res = strat.apply(_ctx(failed=["TT"], attempt=2, bridge=bridge,
                               params={"workdir": str(self.tmp)}))
        self.assertIn("attempt #2", res.notes)
        self.assertIn(DEFAULT_OPTION_NAME, res.notes)
        self.assertIn(_format_value(DEFAULT_RAMP[1]), res.notes)


class FormatValueTests(unittest.TestCase):

    def test_float_uses_g_format(self):
        self.assertEqual(_format_value(1e-10), "1e-10")
        self.assertEqual(_format_value(1.5e-9), "1.5e-09")

    def test_string_passes_through(self):
        self.assertEqual(_format_value("100p"), "100p")
        self.assertEqual(_format_value("1e-10"), "1e-10")


class GminBumpBaselineTests(unittest.TestCase):
    """A5 Phase 1 bug fix: rendered script must restore baseline on every
    firing so previous sub-corner's bump doesn't leak through the
    shared worker-VM asi session."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_gmin_test_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _params(self, **extra):
        return {"workdir": str(self.tmp), **extra}

    def test_default_baseline_is_1e_minus_12(self):
        bridge = _MockBridge([("TT", True)])
        strat = GminBump()
        strat.apply(_ctx(failed=["TT"], bridge=bridge,
                         params=self._params()))
        script = _read_installed_script(bridge)
        self.assertIn('asiSetSimOptionVal asi "gmin" "1e-12"', script)
        self.assertIn("STEP 1", script)

    def test_baseline_value_override_via_params(self):
        bridge = _MockBridge([("TT", True)])
        strat = GminBump()
        strat.apply(_ctx(failed=["TT"], bridge=bridge,
                         params=self._params(baseline_value="5e-12")))
        script = _read_installed_script(bridge)
        self.assertIn('asiSetSimOptionVal asi "gmin" "5e-12"', script)

    def test_baseline_write_precedes_override_write(self):
        bridge = _MockBridge([("TT", True)])
        strat = GminBump()
        strat.apply(_ctx(failed=["TT"], attempt=1, bridge=bridge,
                         params=self._params()))
        script = _read_installed_script(bridge)
        # Baseline write line (literal value) must appear BEFORE the
        # override write line (uses (cadr entry) to lookup from the map).
        # Source-text order matches runtime-execution order in this block.
        # We grep the line bodies, not the literal "1e-11" — the bump
        # value also appears earlier in the cornerMap literal.
        baseline_line = 'asiSetSimOptionVal asi "gmin" "1e-12"'
        override_line = 'asiSetSimOptionVal asi "gmin" (cadr entry)'
        self.assertLess(script.find(baseline_line),
                        script.find(override_line))

    def test_string_baseline_passes_through(self):
        bridge = _MockBridge([("TT", True)])
        strat = GminBump()
        strat.apply(_ctx(failed=["TT"], bridge=bridge,
                         params=self._params(baseline_value="100f")))
        script = _read_installed_script(bridge)
        self.assertIn('"100f"', script)


class RegistrationTests(unittest.TestCase):

    def test_gmin_bump_registered_as_builtin(self):
        from simkit.strategies import builtin_names, get_builtin
        self.assertIn("gmin_bump", builtin_names())
        self.assertIs(get_builtin("gmin_bump"), GminBump)


if __name__ == "__main__":
    unittest.main()
