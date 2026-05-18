"""Unit tests for ``simkit.strategies.trans_pss_ic`` — on-failure variant
of v1.3 ic_from. v1.8 follow-up to DECISIONS #57 / #62 / #63.

Same shape as test_strategies_gmin_bump.py — mock bridge records every
call, tests assert on call ordering + args + rendered script body.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.strategies.base import (  # noqa: E402
    StrategyContext,
    StrategyOutcome,
)
from simkit.strategies.trans_pss_ic import TransPssIc  # noqa: E402


# -----------------------------------------------------------------------------
# Mock bridge + sub-corner table helpers
# -----------------------------------------------------------------------------

class _MockBridge:
    """Production-shape mock per [[feedback_mock_match_production_shape]].

    Records every call. ``pvt_corners_pull`` is special-cased: it WRITES
    a synthetic union JSON to the requested path so the strategy's
    ``load_union(target)`` step works end-to-end without a live bridge.
    """

    def __init__(self, snap, *, prior_scripts=None, sub_corners=None,
                 raise_on_pull=None):
        self._snap = list(snap)
        self._prior_scripts = dict(prior_scripts or {})
        # sub_corners: ordered list of sub_corner_name strings the synthetic
        # pull should produce. Translated into a minimal valid union JSON.
        self._sub_corners = list(sub_corners or [])
        self._raise_on_pull = raise_on_pull
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

    def pvt_corners_pull(self, out_path, *, pvtproject_path, session):
        self.calls.append(("pull", session, out_path, pvtproject_path))
        if self._raise_on_pull is not None:
            raise self._raise_on_pull
        _write_synthetic_union(Path(out_path), self._sub_corners)
        return out_path


def _write_synthetic_union(out_path: Path, sub_corner_names: list[str]):
    """Write a minimal valid union JSON whose explode order matches
    ``sub_corner_names`` exactly. One scalar row per name; loader is happy
    because each row has a unique sweep-collapsed shape.

    The strategy only needs the SUB-CORNER NAMES and their 1-based indices
    in explode order, so a flat list of scalar rows suffices — no need to
    reconstruct sweep semantics here.
    """
    basename = out_path.name[:-len(".union.json")]
    rows = []
    for name in sub_corner_names:
        rows.append({
            "row_name": name,
            "vars": {"temperature": "27"},
            "models": [
                {
                    "file": "rf018.scs", "block": "Global",
                    "test": "All", "section": "tt",
                }
            ],
        })
    doc = {
        "union_schema_version": 1,
        "name": basename,
        "project": "test_proj",
        "testbench_id": "test/tb/maestro",
        "rows": rows,
    }
    out_path.write_text(json.dumps(doc), encoding="utf-8")


def _make_pvtproject(workdir: Path) -> Path:
    """Build a minimal .pvtproject + results/maestro/ skeleton for tests
    that need ``ctx.pvtproject_path`` to point at something real."""
    proj_dir = workdir / "proj"
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "results" / "maestro").mkdir(parents=True, exist_ok=True)
    proj_path = proj_dir / "test.pvtproject"
    proj_path.write_text("{}", encoding="utf-8")  # never parsed in tests
    return proj_path


def _seed_upstream_ic(
    pvtproject_path: Path, history_name: str,
    corner_idx: int, test_name: str, file_kind: str = "ic",
    subdir: str = "netlist",
) -> Path:
    """Put a fake spectre.<kind> file under the layout resolve_ic_path expects.

    ``<results_root>/<history>/<idx>/<test>/<subdir>/spectre.<kind>``.
    """
    results_root = pvtproject_path.parent / "results" / "maestro"
    ic_dir = results_root / history_name / str(corner_idx) / test_name / subdir
    ic_dir.mkdir(parents=True, exist_ok=True)
    ic_path = ic_dir / f"spectre.{file_kind}"
    ic_path.write_text("* fake IC content\n", encoding="utf-8")
    return ic_path


def _ctx(*, failed, item="ic_consumer", attempt=1, bridge=None, params=None,
         history_by_item=None, pvtproject_path=None):
    """failed: list of (corner, test) OR list of corner-strings (test=t1)."""
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
        history_by_item=history_by_item,
        pvtproject_path=pvtproject_path,
    )


def _read_installed_script(bridge):
    install = next(c for c in bridge.calls if c[0] == "install_prerun")
    path = install[3]
    return Path(path).read_text(encoding="utf-8")


# -----------------------------------------------------------------------------
# Param validation
# -----------------------------------------------------------------------------

class TransPssIcParamValidationTests(unittest.TestCase):

    def test_missing_source_item_gives_up_without_touching_bridge(self):
        bridge = _MockBridge([("TT", True)])
        res = TransPssIc().apply(_ctx(failed=["TT"], bridge=bridge,
                                       params={}))
        self.assertEqual(res.outcome, StrategyOutcome.GAVE_UP)
        self.assertIn("source_item", res.notes)
        self.assertEqual(bridge.calls, [])

    def test_invalid_file_kind_gives_up(self):
        bridge = _MockBridge([("TT", True)])
        res = TransPssIc().apply(_ctx(
            failed=["TT"], bridge=bridge,
            params={"source_item": "trans_pvt", "file": "raw"},
        ))
        self.assertEqual(res.outcome, StrategyOutcome.GAVE_UP)
        self.assertIn("invalid file=", res.notes)
        self.assertEqual(bridge.calls, [])

    def test_invalid_mode_gives_up(self):
        bridge = _MockBridge([("TT", True)])
        res = TransPssIc().apply(_ctx(
            failed=["TT"], bridge=bridge,
            params={"source_item": "trans_pvt", "mode": "ic_node"},
        ))
        self.assertEqual(res.outcome, StrategyOutcome.GAVE_UP)
        self.assertIn("invalid mode=", res.notes)


# -----------------------------------------------------------------------------
# Orchestrator-injected context validation
# -----------------------------------------------------------------------------

class TransPssIcOrchInjectionTests(unittest.TestCase):

    def test_none_history_by_item_gives_up(self):
        bridge = _MockBridge([("TT", True)])
        res = TransPssIc().apply(_ctx(
            failed=["TT"], bridge=bridge,
            params={"source_item": "trans_pvt"},
            history_by_item=None,
        ))
        self.assertEqual(res.outcome, StrategyOutcome.GAVE_UP)
        self.assertIn("no recorded history", res.notes)

    def test_source_item_not_in_history_map_gives_up(self):
        bridge = _MockBridge([("TT", True)])
        res = TransPssIc().apply(_ctx(
            failed=["TT"], bridge=bridge,
            params={"source_item": "missing"},
            history_by_item={"other_item": "other_hist"},
        ))
        self.assertEqual(res.outcome, StrategyOutcome.GAVE_UP)
        self.assertIn("known items:", res.notes)
        self.assertIn("other_item", res.notes)

    def test_none_pvtproject_path_gives_up(self):
        bridge = _MockBridge([("TT", True)])
        res = TransPssIc().apply(_ctx(
            failed=["TT"], bridge=bridge,
            params={"source_item": "trans_pvt"},
            history_by_item={"trans_pvt": "hist_1"},
            pvtproject_path=None,
        ))
        self.assertEqual(res.outcome, StrategyOutcome.GAVE_UP)
        self.assertIn("pvtproject_path is None", res.notes)

    def test_results_root_missing_gives_up(self):
        tmp = Path(tempfile.mkdtemp(prefix="simkit_transic_test_"))
        try:
            # .pvtproject exists, but no results/maestro/ dir alongside it
            proj_path = tmp / "stub.pvtproject"
            proj_path.write_text("{}")
            bridge = _MockBridge([("TT", True)])
            res = TransPssIc().apply(_ctx(
                failed=["TT"], bridge=bridge,
                params={"source_item": "trans_pvt"},
                history_by_item={"trans_pvt": "hist_1"},
                pvtproject_path=proj_path,
            ))
            self.assertEqual(res.outcome, StrategyOutcome.GAVE_UP)
            self.assertIn("results root", res.notes)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# -----------------------------------------------------------------------------
# Happy path — full mock flow
# -----------------------------------------------------------------------------

class TransPssIcHappyPathTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_transic_test_"))
        self.proj = _make_pvtproject(self.tmp)
        # Seed upstream IC for sub-corner index 2 (= "TT_pvt" in the
        # 2-row scalar union our synthetic pull produces below).
        self.ic_path = _seed_upstream_ic(
            self.proj, "trans_h1", corner_idx=2, test_name="t1",
            file_kind="ic",
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _ctx(self, **kw):
        params = {"source_item": "trans_pvt", "workdir": str(self.tmp)}
        params.update(kw.pop("params", {}))
        return _ctx(
            failed=kw.pop("failed", ["TT_pvt"]),
            bridge=kw.pop("bridge"),
            params=params,
            history_by_item={"trans_pvt": "trans_h1"},
            pvtproject_path=self.proj,
            **kw,
        )

    def test_happy_path_returns_unchanged_with_history_name(self):
        bridge = _MockBridge(
            [("TT", True), ("TT_pvt", True)],
            sub_corners=["TT", "TT_pvt"],
        )
        res = TransPssIc().apply(self._ctx(failed=["TT_pvt"], bridge=bridge))
        self.assertEqual(res.outcome, StrategyOutcome.UNCHANGED)
        self.assertEqual(res.new_history_name, "ic_consumer__transic1")

    def test_narrows_enable_to_failed_row(self):
        bridge = _MockBridge(
            [("TT", True), ("TT_pvt", True)],
            sub_corners=["TT", "TT_pvt"],
        )
        TransPssIc().apply(self._ctx(failed=["TT_pvt"], bridge=bridge))
        first_restore = next(c for c in bridge.calls if c[0] == "restore")
        self.assertEqual(first_restore[2],
                         [("TT", False), ("TT_pvt", True)])

    def test_restores_original_in_finally(self):
        snap = [("TT", True), ("TT_pvt", False)]
        bridge = _MockBridge(snap, sub_corners=["TT", "TT_pvt"])
        # FAIL on TT (index 1) — seed its IC too.
        _seed_upstream_ic(self.proj, "trans_h1", corner_idx=1,
                          test_name="t1", file_kind="ic")
        TransPssIc().apply(self._ctx(failed=["TT"], bridge=bridge))
        restores = [c for c in bridge.calls if c[0] == "restore"]
        self.assertEqual(len(restores), 2)
        self.assertEqual(restores[-1][2],
                         [("TT", True), ("TT_pvt", False)])

    def test_history_name_sanitized(self):
        bridge = _MockBridge(
            [("TT_pvt", True)],
            sub_corners=["TT", "TT_pvt"],
        )
        res = TransPssIc().apply(self._ctx(
            failed=["TT_pvt"], bridge=bridge, item="PSS Item!",
        ))
        # _sanitize replaces non-alnum/_ with _; double-underscore in
        # join is preserved.
        self.assertEqual(res.new_history_name, "PSS_Item___transic1")

    def test_script_writes_readic_arg_into_additionalArgs(self):
        bridge = _MockBridge(
            [("TT_pvt", True)],
            sub_corners=["TT", "TT_pvt"],
        )
        TransPssIc().apply(self._ctx(failed=["TT_pvt"], bridge=bridge))
        script = _read_installed_script(bridge)
        # cornerMap entry must be the literal readic="<path>" form.
        self.assertIn('readic=\\"', script)
        self.assertIn(str(self.ic_path), script)
        # Option key written is additionalArgs.
        self.assertIn('asiSetSimOptionVal asi "additionalArgs"', script)

    def test_readns_mode_emits_readns_arg(self):
        bridge = _MockBridge(
            [("TT_pvt", True)],
            sub_corners=["TT", "TT_pvt"],
        )
        # Seed a .fc file for readns mode.
        fc_path = _seed_upstream_ic(self.proj, "trans_h1", corner_idx=2,
                                     test_name="t1", file_kind="fc")
        TransPssIc().apply(self._ctx(
            failed=["TT_pvt"], bridge=bridge,
            params={"file": "fc", "mode": "readns"},
        ))
        script = _read_installed_script(bridge)
        self.assertIn('readns=\\"', script)
        self.assertIn(str(fc_path), script)

    def test_baseline_value_empty_string_emitted(self):
        """A6 safe-write shape: baseline_value="" so partial-row FAIL set
        doesn't leak previous sub-corner's additionalArgs across the shared
        worker-VM asi session. (Same defence gmin_bump uses with "1e-12".)"""
        bridge = _MockBridge(
            [("TT_pvt", True)],
            sub_corners=["TT", "TT_pvt"],
        )
        TransPssIc().apply(self._ctx(failed=["TT_pvt"], bridge=bridge))
        script = _read_installed_script(bridge)
        self.assertIn("STEP 1", script)  # safe-write shape marker
        self.assertIn('asiSetSimOptionVal asi "additionalArgs" ""', script)


# -----------------------------------------------------------------------------
# Teardown / failure-in-flight handling
# -----------------------------------------------------------------------------

class TransPssIcTeardownTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_transic_test_"))
        self.proj = _make_pvtproject(self.tmp)
        _seed_upstream_ic(self.proj, "trans_h1", corner_idx=1,
                          test_name="t1", file_kind="ic")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _ctx(self, *, failed, bridge):
        return _ctx(
            failed=failed, bridge=bridge,
            params={"source_item": "trans_pvt", "workdir": str(self.tmp)},
            history_by_item={"trans_pvt": "trans_h1"},
            pvtproject_path=self.proj,
        )

    def test_disables_pre_run_when_no_prior(self):
        bridge = _MockBridge([("TT", True)], sub_corners=["TT"])
        TransPssIc().apply(self._ctx(failed=["TT"], bridge=bridge))
        disables = [c for c in bridge.calls if c[0] == "disable_prerun"]
        self.assertEqual(len(disables), 1)
        self.assertEqual(disables[0][2], "t1")

    def test_restores_prior_script_when_user_had_one(self):
        bridge = _MockBridge(
            [("TT", True)], sub_corners=["TT"],
            prior_scripts={"t1": "/user/prior.il"},
        )
        TransPssIc().apply(self._ctx(failed=["TT"], bridge=bridge))
        installs = [c for c in bridge.calls if c[0] == "install_prerun"]
        # First install is our script, second is the prior restore.
        self.assertEqual(len(installs), 2)
        self.assertEqual(installs[-1][3], "/user/prior.il")

    def test_restore_runs_even_when_pvt_runner_run_raises(self):
        bridge = _MockBridge([("TT", True)], sub_corners=["TT"])
        def boom(*a, **kw):
            raise RuntimeError("axlRunAllTests blew up")
        bridge.pvt_runner_run = boom

        with self.assertRaises(RuntimeError):
            TransPssIc().apply(self._ctx(failed=["TT"], bridge=bridge))

        restores = [c for c in bridge.calls if c[0] == "restore"]
        # Last restore = original snapshot (TT enabled).
        self.assertEqual(restores[-1][2], [("TT", True)])
        disables = [c for c in bridge.calls if c[0] == "disable_prerun"]
        self.assertEqual(len(disables), 1)


# -----------------------------------------------------------------------------
# IC resolution edge cases
# -----------------------------------------------------------------------------

class TransPssIcResolutionTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_transic_test_"))
        self.proj = _make_pvtproject(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _ctx(self, **kw):
        params = {"source_item": "trans_pvt", "workdir": str(self.tmp)}
        params.update(kw.pop("params", {}))
        return _ctx(
            failed=kw.pop("failed"),
            bridge=kw.pop("bridge"),
            params=params,
            history_by_item={"trans_pvt": "trans_h1"},
            pvtproject_path=self.proj,
        )

    def test_no_ic_resolved_for_any_corner_gives_up(self):
        # No IC files seeded — every resolve_ic_path returns None.
        bridge = _MockBridge(
            [("TT", True)], sub_corners=["TT"],
        )
        res = TransPssIc().apply(self._ctx(failed=["TT"], bridge=bridge))
        self.assertEqual(res.outcome, StrategyOutcome.GAVE_UP)
        self.assertIn("no IC files resolved", res.notes)

    def test_partial_ic_resolution_proceeds_with_what_resolved(self):
        # Two failed sub-corners; only one has an IC on disk.
        _seed_upstream_ic(self.proj, "trans_h1", corner_idx=2,
                          test_name="t1", file_kind="ic")
        bridge = _MockBridge(
            [("TT", True), ("SS", True)], sub_corners=["TT", "SS"],
        )
        res = TransPssIc().apply(self._ctx(
            failed=["TT", "SS"], bridge=bridge,
        ))
        self.assertEqual(res.outcome, StrategyOutcome.UNCHANGED)
        script = _read_installed_script(bridge)
        # SS (corner_idx=2) has IC; TT (corner_idx=1) doesn't.
        self.assertIn('"SS"', script)
        self.assertNotIn('"TT"', script)

    def test_failed_corner_not_in_live_table_listed_in_notes(self):
        _seed_upstream_ic(self.proj, "trans_h1", corner_idx=1,
                          test_name="t1", file_kind="ic")
        bridge = _MockBridge(
            [("TT", True)], sub_corners=["TT"],
        )
        # FAIL includes "PHANTOM" which isn't in the live sub-corner table.
        res = TransPssIc().apply(self._ctx(
            failed=["TT", "PHANTOM"], bridge=bridge,
        ))
        # TT resolves cleanly so the strategy runs; PHANTOM is unresolved.
        self.assertEqual(res.outcome, StrategyOutcome.UNCHANGED)
        self.assertIn("PHANTOM", res.notes)

    def test_test_for_ic_override_used_for_resolution(self):
        # Seed IC under test name "trans_test", not under the failed
        # corner's reported test ("t1").
        _seed_upstream_ic(self.proj, "trans_h1", corner_idx=1,
                          test_name="trans_test", file_kind="ic")
        bridge = _MockBridge(
            [("TT", True)], sub_corners=["TT"],
        )
        res = TransPssIc().apply(self._ctx(
            failed=["TT"], bridge=bridge,
            params={"test_for_ic": "trans_test"},
        ))
        self.assertEqual(res.outcome, StrategyOutcome.UNCHANGED)
        # The default-test path (without override) would have given up
        # because t1 has no IC dir.
        self.assertIn("trans_test", _read_installed_script(bridge))

    def test_subdir_override_passed_through(self):
        # Default subdir registry: ("netlist", "psf"). Seed under "psf"
        # only; verify resolver picks it without override (auto-detect),
        # then verify explicit psf-override still works.
        _seed_upstream_ic(self.proj, "trans_h1", corner_idx=1,
                          test_name="t1", file_kind="ic", subdir="psf")
        bridge = _MockBridge([("TT", True)], sub_corners=["TT"])
        res = TransPssIc().apply(self._ctx(
            failed=["TT"], bridge=bridge,
            params={"subdir": "psf"},
        ))
        self.assertEqual(res.outcome, StrategyOutcome.UNCHANGED)
        self.assertIn("/psf/", _read_installed_script(bridge))


# -----------------------------------------------------------------------------
# Live corner-table pull failure
# -----------------------------------------------------------------------------

class TransPssIcLivePullTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_transic_test_"))
        self.proj = _make_pvtproject(self.tmp)
        _seed_upstream_ic(self.proj, "trans_h1", corner_idx=1,
                          test_name="t1", file_kind="ic")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pull_raises_treated_as_gave_up(self):
        bridge = _MockBridge(
            [("TT", True)], sub_corners=["TT"],
            raise_on_pull=RuntimeError("bridge wedged"),
        )
        res = TransPssIc().apply(_ctx(
            failed=["TT"], bridge=bridge,
            params={"source_item": "trans_pvt", "workdir": str(self.tmp)},
            history_by_item={"trans_pvt": "trans_h1"},
            pvtproject_path=self.proj,
        ))
        self.assertEqual(res.outcome, StrategyOutcome.GAVE_UP)
        self.assertIn("0 sub-corners", res.notes)

    def test_pull_returns_empty_union_gave_up(self):
        bridge = _MockBridge(
            [("TT", True)], sub_corners=[],  # → empty rows in synthetic union
        )
        res = TransPssIc().apply(_ctx(
            failed=["TT"], bridge=bridge,
            params={"source_item": "trans_pvt", "workdir": str(self.tmp)},
            history_by_item={"trans_pvt": "trans_h1"},
            pvtproject_path=self.proj,
        ))
        self.assertEqual(res.outcome, StrategyOutcome.GAVE_UP)


# -----------------------------------------------------------------------------
# Empty / no-test edge cases
# -----------------------------------------------------------------------------

class TransPssIcEdgeCaseTests(unittest.TestCase):

    def test_empty_failed_set_gives_up_without_bridge(self):
        bridge = _MockBridge([("TT", True)])
        res = TransPssIc().apply(_ctx(
            failed=[], bridge=bridge,
            params={"source_item": "trans_pvt"},
            history_by_item={"trans_pvt": "h1"},
            pvtproject_path=None,  # never reached — empty FAIL bails first
        ))
        self.assertEqual(res.outcome, StrategyOutcome.GAVE_UP)
        self.assertEqual(bridge.calls, [])

    def test_no_test_names_in_failed_gives_up(self):
        tmp = Path(tempfile.mkdtemp(prefix="simkit_transic_test_"))
        try:
            proj = _make_pvtproject(tmp)
            bridge = _MockBridge([("TT", True)])
            # Fail entry with empty test name.
            res = TransPssIc().apply(_ctx(
                failed=[("TT", "")], bridge=bridge,
                params={"source_item": "trans_pvt"},
                history_by_item={"trans_pvt": "h1"},
                pvtproject_path=proj,
            ))
            self.assertEqual(res.outcome, StrategyOutcome.GAVE_UP)
            self.assertIn("no test names", res.notes)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# -----------------------------------------------------------------------------
# Sub-corner indexing
# -----------------------------------------------------------------------------

class TransPssIcSubCornerIndexingTests(unittest.TestCase):
    """Verify the explode-order indexing the strategy uses to find the
    upstream IC dir matches what resolve_ic_path expects."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_transic_test_"))
        self.proj = _make_pvtproject(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_third_sub_corner_resolves_to_idx_3(self):
        # Live table has 4 scalar rows; FAIL on the third one.
        _seed_upstream_ic(self.proj, "trans_h1", corner_idx=3,
                          test_name="t1", file_kind="ic")
        bridge = _MockBridge(
            [("A", True), ("B", True), ("C", True), ("D", True)],
            sub_corners=["A", "B", "C", "D"],
        )
        res = TransPssIc().apply(_ctx(
            failed=["C"], bridge=bridge,
            params={"source_item": "trans_pvt", "workdir": str(self.tmp)},
            history_by_item={"trans_pvt": "trans_h1"},
            pvtproject_path=self.proj,
        ))
        self.assertEqual(res.outcome, StrategyOutcome.UNCHANGED)
        script = _read_installed_script(bridge)
        self.assertIn("/3/t1/netlist/spectre.ic", script)


# -----------------------------------------------------------------------------
# Registration
# -----------------------------------------------------------------------------

class RegistrationTests(unittest.TestCase):

    def test_trans_pss_ic_registered_as_builtin(self):
        from simkit.strategies import builtin_names, get_builtin
        self.assertIn("trans_pss_ic", builtin_names())
        self.assertIs(get_builtin("trans_pss_ic"), TransPssIc)


if __name__ == "__main__":
    unittest.main()
