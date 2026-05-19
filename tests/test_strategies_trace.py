"""Phase 3A v1.9 #3 #5 — env-gated per-attempt corner-enable tracer log.

The three built-in strategies (``naive_retry``, ``gmin_bump``,
``trans_pss_ic``) emit a single line on stdout when ``SIMKIT_TRACE=1``,
showing the strategy name, the attempt number, the targeted (kept) row
set, and the FAIL set as observed at apply-start. Default OFF.

Helps debug "did naive_retry actually scope to my failed corner or did
it overshoot to the whole row?" without adding logging-framework
ceremony to the codebase.
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.strategies.base import (  # noqa: E402
    StrategyContext, StrategyOutcome,
)
from simkit.strategies.naive_retry import NaiveRetry  # noqa: E402
from simkit.strategies.gmin_bump import GminBump  # noqa: E402
from simkit.strategies.trans_pss_ic import TransPssIc  # noqa: E402


class _SimpleBridge:
    """Minimal bridge that lets naive_retry / gmin_bump apply() reach the
    trace-emit point without raising."""

    def __init__(self, snap):
        self._snap = list(snap)
        self.calls = []

    def pvt_runner_snapshot_corners_enable(self, *, session):
        return list(self._snap)

    def pvt_runner_restore_corners_enable(self, target, *, session):
        self.calls.append(("restore", target))

    def pvt_runner_get_pre_run_script(self, test, *, session):
        return ""

    def pvt_runner_install_pre_run_script(self, test, path, *, session):
        return path

    def pvt_runner_disable_pre_run_script(self, test, *, session):
        pass

    def pvt_runner_run(self, hist, *, session, **kw):
        return (1, 1, hist)


def _ctx(failed, *, bridge, item="item_x", attempt=1, params=None,
         history_by_item=None, pvtproject_path=None):
    fcs = tuple(((c, "t1", "spec_fail") for c in failed))
    return StrategyContext(
        session="sess", item_name=item, failed_corners=fcs,
        attempt_number=attempt, bridge=bridge, params=params or {},
        history_by_item=history_by_item, pvtproject_path=pvtproject_path,
    )


class TraceUnsetIsSilentTests(unittest.TestCase):
    """When SIMKIT_TRACE is not set in the environment, every strategy's
    apply() must produce zero ``[trace]`` lines on stdout."""

    def setUp(self):
        # Defensively clear the var so a CI runner with it set globally
        # doesn't trip these tests.
        self._patcher = mock.patch.dict(os.environ, clear=False)
        self._patcher.start()
        os.environ.pop("SIMKIT_TRACE", None)

    def tearDown(self):
        self._patcher.stop()

    def test_naive_retry_emits_no_trace_when_unset(self):
        bridge = _SimpleBridge([("TT", True)])
        buf = io.StringIO()
        with redirect_stdout(buf):
            NaiveRetry().apply(_ctx(["TT"], bridge=bridge))
        self.assertNotIn("[trace]", buf.getvalue())

    def test_gmin_bump_emits_no_trace_when_unset(self):
        bridge = _SimpleBridge([("TT", True)])
        tmp = Path(tempfile.mkdtemp(prefix="simkit_trace_"))
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                GminBump().apply(_ctx(["TT"], bridge=bridge,
                                       params={"workdir": str(tmp)}))
            self.assertNotIn("[trace]", buf.getvalue())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TraceOnEmitsLineTests(unittest.TestCase):
    """SIMKIT_TRACE=1 → exactly one trace line per apply() invocation,
    containing the strategy name + attempt + targeted set + remaining."""

    def setUp(self):
        self._patcher = mock.patch.dict(os.environ, {"SIMKIT_TRACE": "1"})
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def test_naive_retry_emits_expected_line_format(self):
        bridge = _SimpleBridge([("TT", True), ("SS", True)])
        buf = io.StringIO()
        with redirect_stdout(buf):
            NaiveRetry().apply(_ctx(["TT"], bridge=bridge, attempt=2))
        out = buf.getvalue()
        self.assertIn("[trace] naive_retry attempt #2:", out)
        self.assertIn("targeted=['TT']", out)
        self.assertIn("remaining_before=['TT']", out)

    def test_gmin_bump_emits_expected_line_format(self):
        bridge = _SimpleBridge([("TT", True), ("SS", True)])
        tmp = Path(tempfile.mkdtemp(prefix="simkit_trace_"))
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                GminBump().apply(_ctx(["TT"], bridge=bridge, attempt=1,
                                       params={"workdir": str(tmp)}))
            out = buf.getvalue()
            self.assertIn("[trace] gmin_bump attempt #1:", out)
            self.assertIn("targeted=['TT']", out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_trans_pss_ic_emits_expected_line_format(self):
        # trans_pss_ic needs more scaffolding to reach _trace; build a
        # tmp .pvtproject + seeded IC + history map.
        tmp = Path(tempfile.mkdtemp(prefix="simkit_trace_"))
        try:
            proj_dir = tmp / "proj"
            proj_dir.mkdir()
            (proj_dir / "results" / "maestro").mkdir(parents=True)
            proj_path = proj_dir / "test.pvtproject"
            proj_path.write_text("{}")
            # Seed IC for sub-corner index 1
            ic_dir = (proj_dir / "results" / "maestro" / "trans_h" / "1" /
                       "t1" / "netlist")
            ic_dir.mkdir(parents=True)
            (ic_dir / "spectre.ic").write_text("# fake\n")

            class B(_SimpleBridge):
                def pvt_corners_pull(self, out_path, *, pvtproject_path,
                                      session):
                    import json
                    basename = Path(out_path).name[:-len(".union.json")]
                    Path(out_path).write_text(json.dumps({
                        "union_schema_version": 1, "name": basename,
                        "project": "p", "testbench_id": "tb",
                        "rows": [{"row_name": "TT",
                                  "vars": {"temperature": "27"},
                                  "models": [{
                                      "file": "rf018.scs", "block": "Global",
                                      "test": "All", "section": "tt",
                                  }]}],
                    }))
                def pvt_runner_clear_ic_source(self, t, m, p, *, session):
                    pass

            bridge = B([("TT", True)])
            buf = io.StringIO()
            with redirect_stdout(buf):
                TransPssIc().apply(_ctx(
                    ["TT"], bridge=bridge,
                    params={"source_item": "trans_pvt",
                            "workdir": str(tmp)},
                    history_by_item={"trans_pvt": "trans_h"},
                    pvtproject_path=proj_path,
                ))
            out = buf.getvalue()
            self.assertIn("[trace] trans_pss_ic attempt #1:", out)
            self.assertIn("targeted=", out)
            self.assertIn("remaining_before=", out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TraceArbitraryValueIsTreatedAsOffTests(unittest.TestCase):
    """Only the literal value ``"1"`` enables tracing. Anything else
    (``"0"``, ``"true"``, ``""``) is treated as off."""

    def setUp(self):
        self._patcher = mock.patch.dict(os.environ, {"SIMKIT_TRACE": "true"})
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def test_value_true_does_not_enable(self):
        bridge = _SimpleBridge([("TT", True)])
        buf = io.StringIO()
        with redirect_stdout(buf):
            NaiveRetry().apply(_ctx(["TT"], bridge=bridge))
        self.assertNotIn("[trace]", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
