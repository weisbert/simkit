"""Unit tests for ``simkit.strategies.naive_retry`` — per-corner enable
filtering + restore (DECISIONS #62)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.strategies.base import (  # noqa: E402
    StrategyContext,
    StrategyOutcome,
)
from simkit.strategies.naive_retry import NaiveRetry  # noqa: E402


class _MockBridge:
    """Records every call so tests can assert on call ordering."""

    def __init__(self, snap):
        self._snap = list(snap)
        self.calls: list[tuple] = []

    def pvt_runner_snapshot_corners_enable(self, *, session):
        self.calls.append(("snapshot", session))
        return [(n, en) for n, en in self._snap]

    def pvt_runner_restore_corners_enable(self, target, *, session):
        # Copy target so later mutation by caller doesn't change record.
        self.calls.append(("restore", session, list(target)))

    def pvt_runner_run(self, history_name, *, session, **kw):
        self.calls.append(("run", session, history_name, kw))
        return (1, 1, history_name)  # generic OK tuple


def _ctx(*, failed, item="item1", attempt=1, bridge=None):
    return StrategyContext(
        session="sess",
        item_name=item,
        failed_corners=tuple((c, "t1", "spec_fail") for c in failed),
        attempt_number=attempt,
        bridge=bridge,
        params={},
    )


class NaiveRetryFilteringTests(unittest.TestCase):

    def test_narrows_enable_to_failed_corners(self):
        bridge = _MockBridge([("TT", True), ("SS", True), ("FF", True)])
        strat = NaiveRetry()
        res = strat.apply(_ctx(failed=["SS"], bridge=bridge))

        self.assertEqual(res.outcome, StrategyOutcome.UNCHANGED)
        # Find the FIRST restore call — it's the narrowed one.
        first_restore = next(c for c in bridge.calls if c[0] == "restore")
        self.assertEqual(
            first_restore[2],
            [("TT", False), ("SS", True), ("FF", False)],
        )

    def test_restores_original_in_finally(self):
        snap = [("TT", True), ("SS", False), ("FF", True)]
        bridge = _MockBridge(snap)
        strat = NaiveRetry()
        strat.apply(_ctx(failed=["TT"], bridge=bridge))

        restores = [c for c in bridge.calls if c[0] == "restore"]
        self.assertEqual(len(restores), 2)
        # Last call must be byte-equal to original snapshot.
        self.assertEqual(restores[-1][2], [("TT", True), ("SS", False), ("FF", True)])

    def test_restore_runs_even_when_pvt_runner_run_raises(self):
        bridge = _MockBridge([("TT", True), ("SS", True)])
        def boom(*a, **kw):
            raise RuntimeError("axlRunAllTests blew up")
        bridge.pvt_runner_run = boom

        strat = NaiveRetry()
        with self.assertRaises(RuntimeError):
            strat.apply(_ctx(failed=["TT"], bridge=bridge))

        restores = [c for c in bridge.calls if c[0] == "restore"]
        self.assertEqual(restores[-1][2], [("TT", True), ("SS", True)])

    def test_history_name_includes_attempt_and_is_sanitized(self):
        bridge = _MockBridge([("TT", True)])
        strat = NaiveRetry()
        res = strat.apply(_ctx(item="My Item!", failed=["TT"], attempt=3,
                               bridge=bridge))
        self.assertEqual(res.new_history_name, "My_Item___retry3")
        run_call = next(c for c in bridge.calls if c[0] == "run")
        self.assertEqual(run_call[2], "My_Item___retry3")

    def test_empty_failed_set_gives_up_without_calling_bridge(self):
        bridge = _MockBridge([("TT", True)])
        strat = NaiveRetry()
        res = strat.apply(_ctx(failed=[], bridge=bridge))
        self.assertEqual(res.outcome, StrategyOutcome.GAVE_UP)
        self.assertEqual(bridge.calls, [])  # no bridge touched
        self.assertIsNone(res.new_history_name)

    def test_failed_corner_not_in_live_table_gives_up(self):
        bridge = _MockBridge([("TT", True), ("SS", True)])
        strat = NaiveRetry()
        res = strat.apply(_ctx(failed=["NOPE"], bridge=bridge))
        self.assertEqual(res.outcome, StrategyOutcome.GAVE_UP)
        # Snapshotted, but never restored (didn't change anything).
        self.assertEqual([c[0] for c in bridge.calls], ["snapshot"])

    def test_partial_match_runs_kept_subset_notes_missing(self):
        bridge = _MockBridge([("TT", True), ("SS", True)])
        strat = NaiveRetry()
        res = strat.apply(_ctx(failed=["TT", "GHOST"], bridge=bridge))
        self.assertEqual(res.outcome, StrategyOutcome.UNCHANGED)
        self.assertIn("GHOST", res.notes)
        first_restore = next(c for c in bridge.calls if c[0] == "restore")
        self.assertEqual(first_restore[2],
                         [("TT", True), ("SS", False)])


class NaiveRetrySubCornerMappingTests(unittest.TestCase):
    """DB FAIL is at sub-corner ('TT_pvt_3'); bridge snap is row-level
    ('TT_pvt'). Verify the prefix-match mapping picks the right row."""

    def test_subcorner_maps_to_parent_row(self):
        bridge = _MockBridge([("TT", True), ("TT_pvt", True)])
        strat = NaiveRetry()
        res = strat.apply(_ctx(failed=["TT_pvt_3"], bridge=bridge))
        self.assertEqual(res.outcome, StrategyOutcome.UNCHANGED)
        first_restore = next(c for c in bridge.calls if c[0] == "restore")
        # TT_pvt enabled (parent of TT_pvt_3), TT disabled.
        self.assertEqual(first_restore[2],
                         [("TT", False), ("TT_pvt", True)])

    def test_longest_prefix_wins_over_shorter(self):
        # Both 'TT' and 'TT_pvt' are valid prefixes of 'TT_pvt_3'.
        # Must pick the LONGER one.
        bridge = _MockBridge([("TT", True), ("TT_pvt", True)])
        strat = NaiveRetry()
        strat.apply(_ctx(failed=["TT_pvt_3"], bridge=bridge))
        first_restore = next(c for c in bridge.calls if c[0] == "restore")
        # Must NOT enable TT.
        self.assertEqual(dict(first_restore[2]),
                         {"TT": False, "TT_pvt": True})

    def test_mixed_scalar_and_subcorner(self):
        bridge = _MockBridge([("TT", True), ("TT_pvt", True),
                              ("TT_2p5G", True)])
        strat = NaiveRetry()
        # TT is a scalar (exact match); TT_pvt_3 maps to row TT_pvt.
        strat.apply(_ctx(failed=["TT", "TT_pvt_3"], bridge=bridge))
        first_restore = next(c for c in bridge.calls if c[0] == "restore")
        self.assertEqual(dict(first_restore[2]),
                         {"TT": True, "TT_pvt": True, "TT_2p5G": False})

    def test_unmappable_subcorner_gives_up_when_only_one(self):
        # Sub-corner whose row isn't in the live table → cannot map.
        bridge = _MockBridge([("TT", True)])
        strat = NaiveRetry()
        res = strat.apply(_ctx(failed=["SS_pvt_1"], bridge=bridge))
        self.assertEqual(res.outcome, StrategyOutcome.GAVE_UP)


if __name__ == "__main__":
    unittest.main()
