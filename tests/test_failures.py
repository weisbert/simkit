"""Unit tests for ``simkit.failures`` — per-corner FAIL aggregation."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.db import bootstrap, connect  # noqa: E402
from simkit.failures import (  # noqa: E402
    FailedCorner,
    REASON_EVAL,
    REASON_SIM,
    REASON_SPEC,
    auto_retry_corners,
    find_failed_corners,
)


_RID = "test-run-0001"


def _insert_row(con, *, corner, test, output, status, spec_status=None):
    con.execute(
        """
        INSERT INTO results
          (run_id, point, corner, test, output, value_num, value_str,
           status, sweep, corner_vars, test_note, spec, spec_status)
        VALUES (?, 1, ?, ?, ?, NULL, NULL, ?, '{}', '{}', NULL, NULL, ?)
        """,
        [_RID, corner, test, output, status, spec_status],
    )


class FindFailedCornersTests(unittest.TestCase):

    def setUp(self) -> None:
        self.con = connect(":memory:")
        bootstrap(self.con)

    def tearDown(self) -> None:
        self.con.close()

    def test_clean_run_returns_empty(self):
        _insert_row(self.con, corner="TT", test="t1", output="vout",
                    status="ok", spec_status="pass")
        self.assertEqual(find_failed_corners(self.con, _RID), ())

    def test_spec_fail_picked_up(self):
        _insert_row(self.con, corner="TT", test="t1", output="rtime",
                    status="ok", spec_status="fail")
        out = find_failed_corners(self.con, _RID)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].corner, "TT")
        self.assertEqual(out[0].reasons, frozenset({REASON_SPEC}))
        self.assertEqual(out[0].sample_test, "t1")
        self.assertEqual(out[0].sample_output, "rtime")

    def test_sim_status_failed_sentinel_picked_up(self):
        _insert_row(self.con, corner="SS", test="t1", output="__sim_status__",
                    status="failed")
        out = find_failed_corners(self.con, _RID)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].corner, "SS")
        self.assertEqual(out[0].reasons, frozenset({REASON_SIM}))

    def test_no_convergence_sentinel_picked_up(self):
        _insert_row(self.con, corner="SS", test="t1", output="__sim_status__",
                    status="no_convergence")
        out = find_failed_corners(self.con, _RID)
        self.assertEqual(out[0].reasons, frozenset({REASON_SIM}))

    def test_running_pending_sentinels_NOT_picked_up(self):
        # v1.5 F2 polling should prevent this, but if it slips through,
        # "retry" is not the right action — caller should wait.
        _insert_row(self.con, corner="A", test="t1", output="__sim_status__",
                    status="running")
        _insert_row(self.con, corner="B", test="t1", output="__sim_status__",
                    status="pending")
        self.assertEqual(find_failed_corners(self.con, _RID), ())

    def test_eval_err_excluded_by_default(self):
        _insert_row(self.con, corner="TT", test="t1", output="vout",
                    status="eval_err")
        self.assertEqual(find_failed_corners(self.con, _RID), ())

    def test_eval_err_surfaced_when_opted_in(self):
        _insert_row(self.con, corner="TT", test="t1", output="vout",
                    status="eval_err")
        out = find_failed_corners(self.con, _RID, include_eval_err=True)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].reasons, frozenset({REASON_EVAL}))

    def test_one_corner_multiple_reasons(self):
        _insert_row(self.con, corner="TT", test="t1", output="rtime",
                    status="ok", spec_status="fail")
        _insert_row(self.con, corner="TT", test="t2", output="__sim_status__",
                    status="failed")
        out = find_failed_corners(self.con, _RID)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].reasons, frozenset({REASON_SPEC, REASON_SIM}))

    def test_multiple_corners_each_with_fail(self):
        _insert_row(self.con, corner="TT", test="t1", output="vout",
                    status="ok", spec_status="fail")
        _insert_row(self.con, corner="SS", test="t1", output="__sim_status__",
                    status="failed")
        _insert_row(self.con, corner="FF", test="t1", output="vout",
                    status="ok", spec_status="pass")  # clean
        out = find_failed_corners(self.con, _RID)
        names = {f.corner for f in out}
        self.assertEqual(names, {"TT", "SS"})

    def test_run_id_isolation(self):
        _insert_row(self.con, corner="TT", test="t1", output="vout",
                    status="ok", spec_status="fail")
        # Other run_id pollution should not leak.
        self.con.execute(
            """
            INSERT INTO results
              (run_id, point, corner, test, output, value_num, value_str,
               status, sweep, corner_vars, test_note, spec, spec_status)
            VALUES ('other-run', 1, 'OTHER', 't1', 'x', NULL, NULL, 'ok',
                    '{}', '{}', NULL, NULL, 'fail')
            """
        )
        out = find_failed_corners(self.con, _RID)
        self.assertEqual({f.corner for f in out}, {"TT"})


class AutoRetryCornersTests(unittest.TestCase):

    def test_includes_spec_and_sim(self):
        fc = (
            FailedCorner("A", frozenset({REASON_SPEC}), "t", "o"),
            FailedCorner("B", frozenset({REASON_SIM}), "t", "o"),
        )
        self.assertEqual(auto_retry_corners(fc), ("A", "B"))

    def test_excludes_eval_only(self):
        fc = (
            FailedCorner("A", frozenset({REASON_EVAL}), "t", "o"),
            FailedCorner("B", frozenset({REASON_SPEC}), "t", "o"),
        )
        self.assertEqual(auto_retry_corners(fc), ("B",))

    def test_mixed_reasons_corner_included(self):
        # Corner with both eval_err and spec_fail → still retryable
        # (the spec_fail might recover even if eval_err is permanent).
        fc = (
            FailedCorner("A", frozenset({REASON_EVAL, REASON_SPEC}), "t", "o"),
        )
        self.assertEqual(auto_retry_corners(fc), ("A",))


if __name__ == "__main__":
    unittest.main()
