"""CLI tests for ``pvt star`` / ``pvt unstar`` / ``pvt sync-stars`` (v1.8 #4).

Bridge is mocked via ``unittest.mock.patch`` on the lazy-import helper, so
no live Maestro is touched.
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "python"))

from simkit.cli.__main__ import main as cli_main  # noqa: E402
from simkit.db import bootstrap, connect  # noqa: E402
from simkit.skill_bridge import SkillBridgeError  # noqa: E402


def _seed(db_path: Path, run_id: str, *,
          history_name: str, starred: bool = False) -> None:
    con = connect(db_path)
    try:
        bootstrap(con)
        con.execute(
            "INSERT INTO runs (run_id, project_id, testbench_id, "
            "testbench_alias, timestamp, author, label, note, netlist_path, "
            "history_name, schema_version, ingested_at, starred) "
            "VALUES (?, 'p', 'tb', NULL, '2026-05-18T12:00:00+08:00', 'a', "
            "NULL, NULL, NULL, ?, 3, '2026-05-18T12:00:00+08:00', ?)",
            [run_id, history_name, starred],
        )
    finally:
        con.close()


def _run(*args: str) -> tuple:
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = cli_main(list(args))
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 2
    return rc, out.getvalue(), err.getvalue()


def _mock_bridge():
    """Build a SimpleNamespace exposing the wrappers the CLI uses."""
    return SimpleNamespace(
        pvt_runner_set_history_lock=MagicMock(),
        pvt_runner_get_history_lock_map=MagicMock(return_value={}),
        SkillBridgeError=SkillBridgeError,
    )


class PvtStarSingleRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_cli_star_"))
        self.db = self.tmp / "simkit.duckdb"
        _seed(self.db, "abc123", history_name="my_hist")

    def test_star_pushes_lock_to_maestro_by_default(self):
        sb = _mock_bridge()
        with patch("simkit.cli.star._import_bridge", return_value=sb):
            rc, out, err = _run(
                "star", "abc123", "--session", "fnxSession0",
                "--db", str(self.db),
            )
        self.assertEqual(rc, 0, err)
        sb.pvt_runner_set_history_lock.assert_called_once_with(
            "my_hist", True, session="fnxSession0",
        )
        self.assertIn("starred", out)
        # DB also updated
        con = connect(self.db, read_only=True)
        try:
            row = con.execute(
                "SELECT starred FROM runs WHERE run_id='abc123'"
            ).fetchone()
        finally:
            con.close()
        self.assertTrue(row[0])

    def test_star_no_push_keeps_maestro_untouched(self):
        sb = _mock_bridge()
        with patch("simkit.cli.star._import_bridge", return_value=sb):
            rc, out, _err = _run(
                "star", "abc123", "--no-push",
                "--db", str(self.db),
            )
        self.assertEqual(rc, 0)
        sb.pvt_runner_set_history_lock.assert_not_called()

    def test_unstar_emits_nil_lock(self):
        _seed(self.tmp / "u.duckdb", "rid", history_name="h", starred=True)
        sb = _mock_bridge()
        with patch("simkit.cli.star._import_bridge", return_value=sb):
            rc, out, _err = _run(
                "unstar", "rid", "--session", "S",
                "--db", str(self.tmp / "u.duckdb"),
            )
        self.assertEqual(rc, 0)
        sb.pvt_runner_set_history_lock.assert_called_once_with(
            "h", False, session="S",
        )

    def test_missing_run_exits_1(self):
        rc, _out, err = _run(
            "star", "ghost", "--no-push", "--db", str(self.db),
        )
        self.assertEqual(rc, 1)
        self.assertIn("ghost", err)

    def test_star_without_session_and_with_push_exits_2(self):
        rc, _out, err = _run("star", "abc123", "--db", str(self.db))
        self.assertEqual(rc, 2)
        self.assertIn("--session", err)

    def test_maestro_push_fail_returns_1_but_db_stays_set(self):
        sb = _mock_bridge()
        sb.pvt_runner_set_history_lock.side_effect = SkillBridgeError(
            "lock_failed", "Maestro refused",
        )
        with patch("simkit.cli.star._import_bridge", return_value=sb):
            rc, _out, err = _run(
                "star", "abc123", "--session", "S",
                "--db", str(self.db),
            )
        self.assertEqual(rc, 1)
        self.assertIn("Maestro push failed", err)
        # DB still updated
        con = connect(self.db, read_only=True)
        try:
            row = con.execute(
                "SELECT starred FROM runs WHERE run_id='abc123'"
            ).fetchone()
        finally:
            con.close()
        self.assertTrue(row[0])


class PvtSyncStarsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_cli_sync_"))
        self.db = self.tmp / "simkit.duckdb"
        _seed(self.db, "r1", history_name="h_starred", starred=True)
        _seed(self.db, "r2", history_name="h_clean", starred=False)

    def test_push_emits_lock_for_starred(self):
        sb = _mock_bridge()
        sb.pvt_runner_get_history_lock_map.return_value = {
            "h_starred": False, "h_clean": False,
        }
        with patch("simkit.cli.star._import_bridge", return_value=sb):
            rc, out, _err = _run(
                "sync-stars", "push", "--session", "S",
                "--db", str(self.db),
            )
        self.assertEqual(rc, 0, out)
        sb.pvt_runner_set_history_lock.assert_called_once_with(
            "h_starred", True, session="S",
        )

    def test_pull_updates_db_from_maestro(self):
        sb = _mock_bridge()
        # Maestro has h_clean locked but h_starred unlocked
        sb.pvt_runner_get_history_lock_map.return_value = {
            "h_starred": False, "h_clean": True,
        }
        with patch("simkit.cli.star._import_bridge", return_value=sb):
            rc, out, _err = _run(
                "sync-stars", "pull", "--session", "S",
                "--db", str(self.db),
            )
        self.assertEqual(rc, 0, out)
        # Maestro is not written on pull
        sb.pvt_runner_set_history_lock.assert_not_called()
        con = connect(self.db, read_only=True)
        try:
            d = dict(con.execute(
                "SELECT run_id, starred FROM runs ORDER BY run_id"
            ).fetchall())
        finally:
            con.close()
        self.assertEqual(d, {"r1": False, "r2": True})

    def test_dry_run_skips_apply(self):
        sb = _mock_bridge()
        sb.pvt_runner_get_history_lock_map.return_value = {
            "h_starred": False, "h_clean": False,
        }
        with patch("simkit.cli.star._import_bridge", return_value=sb):
            rc, out, _err = _run(
                "sync-stars", "push", "--session", "S",
                "--db", str(self.db), "--dry-run",
            )
        self.assertEqual(rc, 0)
        sb.pvt_runner_set_history_lock.assert_not_called()
        self.assertIn("dry-run", out)
        self.assertIn("NOT applied", out)

    def test_warning_emitted_for_starred_run_with_no_maestro_history(self):
        sb = _mock_bridge()
        sb.pvt_runner_get_history_lock_map.return_value = {
            "other_thing": False,
        }
        with patch("simkit.cli.star._import_bridge", return_value=sb):
            rc, _out, err = _run(
                "sync-stars", "push", "--session", "S",
                "--db", str(self.db),
            )
        self.assertEqual(rc, 0)  # warning, not error
        self.assertIn("h_starred", err)
        self.assertIn("not present", err)

    def test_in_sync_emits_nothing_to_do(self):
        sb = _mock_bridge()
        sb.pvt_runner_get_history_lock_map.return_value = {
            "h_starred": True, "h_clean": False,
        }
        with patch("simkit.cli.star._import_bridge", return_value=sb):
            rc, out, _err = _run(
                "sync-stars", "push", "--session", "S",
                "--db", str(self.db),
            )
        self.assertEqual(rc, 0)
        self.assertIn("nothing to do", out)


class PvtListStarredColumnTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_cli_list_star_"))
        self.db = self.tmp / "simkit.duckdb"
        _seed(self.db, "starred1", history_name="h_lock", starred=True)
        _seed(self.db, "plain1", history_name="h_open", starred=False)

    def test_star_column_renders_glyph_for_starred(self):
        rc, out, _err = _run("list", "--db", str(self.db))
        self.assertEqual(rc, 0)
        self.assertIn("★", out)
        # Find the data rows (skip header + separator). Both start with the
        # ★-column cell; data row for starred1 begins "★ …", data row for
        # plain1 begins " … " (single space).
        starred_line = next(
            l for l in out.splitlines() if "starred1"[:8] in l
        )
        plain_line = next(
            l for l in out.splitlines() if "plain1"[:8] in l
        )
        self.assertTrue(starred_line.startswith("★"))
        self.assertFalse(plain_line.startswith("★"))

    def test_starred_only_filter(self):
        rc, out, _err = _run(
            "list", "--db", str(self.db), "--starred-only",
        )
        self.assertEqual(rc, 0)
        self.assertIn("starred1"[:8], out)
        self.assertNotIn("plain1"[:8], out)

    def test_json_carries_starred_field(self):
        import json
        rc, out, _err = _run("list", "--db", str(self.db), "--json")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        d = {r["run_id"][:8]: r["starred"] for r in data}
        self.assertEqual(d, {"starred1": True, "plain1": False})


if __name__ == "__main__":
    unittest.main()
