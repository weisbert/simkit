"""Tests for simkit.star (pure-Python star + sync-plan core)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "python"))

from simkit.db import bootstrap, connect  # noqa: E402
from simkit.errors import RunNotFoundError  # noqa: E402
from simkit.star import (  # noqa: E402
    apply_sync_plan,
    compute_sync_plan,
    load_db_rows,
    set_run_starred,
)


def _seed(con, run_id: str, *, history_name: str, starred: bool = False) -> None:
    con.execute(
        "INSERT INTO runs (run_id, project_id, testbench_id, testbench_alias, "
        "timestamp, author, label, note, netlist_path, history_name, "
        "schema_version, ingested_at, starred) "
        "VALUES (?, 'p', 'tb', NULL, '2026-05-18T12:00:00+08:00', 'a', "
        "NULL, NULL, NULL, ?, 3, '2026-05-18T12:00:00+08:00', ?)",
        [run_id, history_name, starred],
    )


class SetRunStarredTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_star_"))
        self.db = self.tmp / "simkit.duckdb"
        self.con = connect(self.db)
        bootstrap(self.con)
        _seed(self.con, "r1", history_name="h1")
        _seed(self.con, "r2", history_name="h2", starred=True)

    def tearDown(self):
        self.con.close()

    def test_set_when_unstarred_returns_set(self):
        res = set_run_starred(self.con, run_id="r1", starred=True)
        self.assertEqual(res.action, "set")
        self.assertFalse(res.previous)
        self.assertTrue(res.current)
        self.assertEqual(res.history_name, "h1")
        row = self.con.execute(
            "SELECT starred FROM runs WHERE run_id='r1'"
        ).fetchone()
        self.assertTrue(row[0])

    def test_set_when_already_starred_is_noop(self):
        res = set_run_starred(self.con, run_id="r2", starred=True)
        self.assertEqual(res.action, "noop")
        self.assertTrue(res.previous)
        self.assertTrue(res.current)

    def test_clear_when_starred_returns_cleared(self):
        res = set_run_starred(self.con, run_id="r2", starred=False)
        self.assertEqual(res.action, "cleared")
        self.assertTrue(res.previous)
        self.assertFalse(res.current)

    def test_clear_when_already_clear_is_noop(self):
        res = set_run_starred(self.con, run_id="r1", starred=False)
        self.assertEqual(res.action, "noop")

    def test_missing_run_raises(self):
        with self.assertRaises(RunNotFoundError):
            set_run_starred(self.con, run_id="ghost", starred=True)


class LoadDbRowsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_star_"))
        self.db = self.tmp / "simkit.duckdb"
        self.con = connect(self.db)
        bootstrap(self.con)
        _seed(self.con, "r1", history_name="h_solo", starred=True)
        _seed(self.con, "r2", history_name="h_dup", starred=False)
        _seed(self.con, "r3", history_name="h_dup", starred=True)
        _seed(self.con, "r4", history_name="h_dup", starred=False)

    def tearDown(self):
        self.con.close()

    def test_groups_by_history_and_ors_starred(self):
        m = load_db_rows(self.con)
        self.assertEqual(set(m.keys()), {"h_solo", "h_dup"})
        self.assertEqual(m["h_solo"], (True, ("r1",)))
        # any-starred → True even when only 1 of 3 is set
        starred, ids = m["h_dup"]
        self.assertTrue(starred)
        self.assertEqual(set(ids), {"r2", "r3", "r4"})


class ComputeSyncPlanPushTests(unittest.TestCase):
    def test_db_starred_unlocked_maestro_emits_lock(self):
        plan = compute_sync_plan(
            direction="push",
            db_rows={"h": (True, ("r1",))},
            maestro_lock_map={"h": False},
        )
        self.assertEqual(len(plan.actions), 1)
        self.assertEqual(plan.actions[0].kind, "maestro_lock")
        self.assertEqual(plan.actions[0].history_name, "h")

    def test_db_unstarred_locked_maestro_emits_unlock(self):
        plan = compute_sync_plan(
            direction="push",
            db_rows={"h": (False, ("r1",))},
            maestro_lock_map={"h": True},
        )
        self.assertEqual(plan.actions[0].kind, "maestro_unlock")

    def test_already_in_sync_emits_nothing(self):
        plan = compute_sync_plan(
            direction="push",
            db_rows={
                "h1": (True, ("r1",)),
                "h2": (False, ("r2",)),
            },
            maestro_lock_map={"h1": True, "h2": False},
        )
        self.assertEqual(plan.actions, ())
        self.assertEqual(plan.warnings, ())

    def test_starred_but_missing_in_maestro_warns(self):
        plan = compute_sync_plan(
            direction="push",
            db_rows={"ghost": (True, ("r1", "r2"))},
            maestro_lock_map={"other": False},
        )
        self.assertEqual(plan.actions, ())
        self.assertEqual(len(plan.warnings), 1)
        self.assertIn("ghost", plan.warnings[0])
        self.assertIn("r1", plan.warnings[0])

    def test_unstarred_missing_in_maestro_is_silent(self):
        plan = compute_sync_plan(
            direction="push",
            db_rows={"defunct": (False, ("r1",))},
            maestro_lock_map={},
        )
        self.assertEqual(plan.actions, ())
        self.assertEqual(plan.warnings, ())


class ComputeSyncPlanPullTests(unittest.TestCase):
    def test_maestro_locked_db_unstarred_emits_db_star(self):
        plan = compute_sync_plan(
            direction="pull",
            db_rows={"h": (False, ("r1",))},
            maestro_lock_map={"h": True},
        )
        self.assertEqual(plan.actions[0].kind, "db_star")
        self.assertEqual(plan.actions[0].affected_run_ids, ("r1",))

    def test_maestro_unlocked_db_starred_emits_db_unstar(self):
        plan = compute_sync_plan(
            direction="pull",
            db_rows={"h": (True, ("r1",))},
            maestro_lock_map={"h": False},
        )
        self.assertEqual(plan.actions[0].kind, "db_unstar")

    def test_maestro_history_with_no_db_row_skipped(self):
        plan = compute_sync_plan(
            direction="pull",
            db_rows={},
            maestro_lock_map={"orphan": True},
        )
        self.assertEqual(plan.actions, ())

    def test_already_in_sync_emits_nothing(self):
        plan = compute_sync_plan(
            direction="pull",
            db_rows={"h": (True, ("r1",))},
            maestro_lock_map={"h": True},
        )
        self.assertEqual(plan.actions, ())


class ApplySyncPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_star_apply_"))
        self.db = self.tmp / "simkit.duckdb"
        self.con = connect(self.db)
        bootstrap(self.con)
        _seed(self.con, "r1", history_name="h_solo", starred=False)
        _seed(self.con, "r2a", history_name="h_dup", starred=False)
        _seed(self.con, "r2b", history_name="h_dup", starred=False)
        self.lock_calls = []

    def tearDown(self):
        self.con.close()

    def _record_lock(self, name: str, lock: bool) -> None:
        self.lock_calls.append((name, lock))

    def test_apply_push_calls_setter_once_per_action(self):
        plan = compute_sync_plan(
            direction="push",
            db_rows=load_db_rows(self.con),
            maestro_lock_map={"h_solo": True, "h_dup": True},
        )
        # All DB rows are unstarred → expect 2 unlock calls
        apply_sync_plan(
            plan, con=self.con, set_history_lock=self._record_lock,
        )
        self.assertEqual(len(self.lock_calls), 2)
        self.assertTrue(all(lock is False for _, lock in self.lock_calls))
        self.assertEqual(
            {name for name, _ in self.lock_calls}, {"h_solo", "h_dup"},
        )

    def test_apply_pull_db_star_affects_every_matching_row(self):
        # Maestro locks h_dup; pull should star both r2a + r2b
        plan = compute_sync_plan(
            direction="pull",
            db_rows=load_db_rows(self.con),
            maestro_lock_map={"h_dup": True, "h_solo": False},
        )
        apply_sync_plan(
            plan, con=self.con, set_history_lock=self._record_lock,
        )
        # No Maestro write on pull
        self.assertEqual(self.lock_calls, [])
        rows = self.con.execute(
            "SELECT run_id, starred FROM runs ORDER BY run_id"
        ).fetchall()
        self.assertEqual(
            dict(rows),
            {"r1": False, "r2a": True, "r2b": True},
        )


if __name__ == "__main__":
    unittest.main()
