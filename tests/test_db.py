"""Unit tests for simkit.db (connection / bootstrap / transaction).

Run with stdlib unittest:

    PYTHONPATH=python python3.11 -m unittest tests.test_db -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

import duckdb  # noqa: E402

from simkit.db import bootstrap, connect, transaction  # noqa: E402
from simkit.schema_sql import DB_SCHEMA_VERSION, TABLE_NAMES  # noqa: E402


class BootstrapTests(unittest.TestCase):
    """Bootstrap creates every table, is idempotent, and seeds the meta row."""

    def setUp(self):
        self.con = connect(":memory:")

    def tearDown(self):
        self.con.close()

    def test_bootstrap_creates_all_tables(self):
        bootstrap(self.con)
        rows = self.con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' ORDER BY table_name"
        ).fetchall()
        names = {r[0] for r in rows}
        for expected in TABLE_NAMES:
            self.assertIn(expected, names, f"missing table {expected!r} in {names}")

    def test_bootstrap_is_idempotent(self):
        bootstrap(self.con)
        # Insert a sentinel run; bootstrap again; sentinel must survive.
        self.con.execute("BEGIN")
        self.con.execute(
            "INSERT INTO simkit_meta(key, value) VALUES (?, ?)",
            ["sentinel", "value-1"],
        )
        self.con.execute("COMMIT")
        bootstrap(self.con)
        row = self.con.execute(
            "SELECT value FROM simkit_meta WHERE key = 'sentinel'"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "value-1")

    def test_bootstrap_writes_meta_version_1(self):
        bootstrap(self.con)
        row = self.con.execute(
            "SELECT value FROM simkit_meta WHERE key = 'db_schema_version'"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], str(DB_SCHEMA_VERSION))

    def test_bootstrap_refuses_future_schema_version(self):
        # v1.4 (DECISIONS #47): bootstrap is now migration-aware. A DB
        # marked at a future schema_version means "opened by a newer simkit
        # build" — refuse rather than corrupt.
        bootstrap(self.con)
        self.con.execute(
            "UPDATE simkit_meta SET value = '99' WHERE key = 'db_schema_version'"
        )
        with self.assertRaisesRegex(RuntimeError, "newer than this simkit"):
            bootstrap(self.con)

    def test_bootstrap_migrates_v1_to_v2(self):
        # v1.4: a DB previously bootstrapped at v1 should pick up the v2
        # additive columns (results.spec, results.spec_status) on next
        # bootstrap, and have its meta version updated.
        bootstrap(self.con)
        self.con.execute(
            "UPDATE simkit_meta SET value = '1' WHERE key = 'db_schema_version'"
        )
        bootstrap(self.con)
        row = self.con.execute(
            "SELECT value FROM simkit_meta WHERE key = 'db_schema_version'"
        ).fetchone()
        self.assertEqual(row[0], str(DB_SCHEMA_VERSION))
        # The migration is additive — both new columns exist.
        cols = {r[1] for r in self.con.execute(
            "PRAGMA table_info('results')"
        ).fetchall()}
        self.assertIn("spec", cols)
        self.assertIn("spec_status", cols)


def _runs_columns(con) -> set:
    """Return the set of column names on the runs table."""
    return {r[1] for r in con.execute("PRAGMA table_info('runs')").fetchall()}


def _insert_minimal_run(con, run_id: str = "r1") -> None:
    """Insert a runs row that matches the v1 column list (the columns
    that have been present on every schema version since v1). Used by
    migration tests so existing-row backfill behaviour can be observed.
    """
    con.execute(
        """
        INSERT INTO runs (
          run_id, project_id, testbench_id, testbench_alias,
          timestamp, author, label, note,
          netlist_path, history_name, schema_version, ingested_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            run_id, "proj", "tb", None,
            "2026-01-01 00:00:00+00", "alice", None, None,
            None, "history_1", 1, "2026-01-01 00:00:00+00",
        ],
    )


class SchemaV4MigrationTests(unittest.TestCase):
    """Schema migration tests — milestone/partial_run (v4) and provenance
    (v5). Cover fresh bootstrap, chained upgrades from each historical
    version, idempotency, forward-compat refusal, and round-trip of the
    new columns.
    """

    def setUp(self):
        self.con = connect(":memory:")

    def tearDown(self):
        self.con.close()

    def test_fresh_bootstrap_is_v4_with_new_columns(self):
        bootstrap(self.con)
        row = self.con.execute(
            "SELECT value FROM simkit_meta WHERE key = 'db_schema_version'"
        ).fetchone()
        self.assertEqual(row[0], "5")
        cols = _runs_columns(self.con)
        self.assertIn("milestone", cols)
        self.assertIn("partial_run", cols)
        self.assertIn("provenance", cols)

    def test_v4_db_migrates_to_v5_adds_provenance(self):
        # Stand up a v4-shaped DB, drop the v5 column, rewind meta to 4,
        # then re-bootstrap to exercise V5_MIGRATION_DDL.
        bootstrap(self.con)
        self.con.execute("ALTER TABLE runs DROP COLUMN provenance")
        _insert_minimal_run(self.con, run_id="pre-v5")
        self.con.execute(
            "UPDATE simkit_meta SET value = '4' WHERE key = 'db_schema_version'"
        )
        bootstrap(self.con)
        row = self.con.execute(
            "SELECT value FROM simkit_meta WHERE key = 'db_schema_version'"
        ).fetchone()
        self.assertEqual(row[0], "5")
        self.assertIn("provenance", _runs_columns(self.con))
        prov = self.con.execute(
            "SELECT provenance FROM runs WHERE run_id = 'pre-v5'"
        ).fetchone()
        self.assertIsNone(prov[0])

    def test_v3_db_migrates_to_v4_backfills_defaults(self):
        # Stand up a v3-shaped DB: full bootstrap, then rewind the
        # meta-version to 3 and drop the v4 columns so a follow-up
        # bootstrap exercises the V4_MIGRATION_DDL path against a row
        # that pre-dates the new columns.
        bootstrap(self.con)
        self.con.execute("ALTER TABLE runs DROP COLUMN milestone")
        self.con.execute("ALTER TABLE runs DROP COLUMN partial_run")
        _insert_minimal_run(self.con, run_id="pre-v4")
        self.con.execute(
            "UPDATE simkit_meta SET value = '3' WHERE key = 'db_schema_version'"
        )
        bootstrap(self.con)
        # Meta bumped.
        row = self.con.execute(
            "SELECT value FROM simkit_meta WHERE key = 'db_schema_version'"
        ).fetchone()
        self.assertEqual(row[0], "5")
        # New columns exist and the pre-existing row backfilled to the
        # spec'd DEFAULTs (NULL milestone, FALSE partial_run).
        cols = _runs_columns(self.con)
        self.assertIn("milestone", cols)
        self.assertIn("partial_run", cols)
        ms, pr = self.con.execute(
            "SELECT milestone, partial_run FROM runs WHERE run_id = 'pre-v4'"
        ).fetchone()
        self.assertIsNone(ms)
        self.assertFalse(bool(pr))

    def test_v2_db_chains_v3_and_v4(self):
        # v2 → v3 → v4 in one bootstrap pass. Confirms ``starred`` (v3),
        # plus ``milestone`` + ``partial_run`` (v4) all land, and the
        # v2 columns on results survive.
        bootstrap(self.con)
        self.con.execute("ALTER TABLE runs DROP COLUMN starred")
        self.con.execute("ALTER TABLE runs DROP COLUMN milestone")
        self.con.execute("ALTER TABLE runs DROP COLUMN partial_run")
        self.con.execute(
            "UPDATE simkit_meta SET value = '2' WHERE key = 'db_schema_version'"
        )
        bootstrap(self.con)
        runs_cols = _runs_columns(self.con)
        self.assertIn("starred", runs_cols)
        self.assertIn("milestone", runs_cols)
        self.assertIn("partial_run", runs_cols)
        results_cols = {r[1] for r in self.con.execute(
            "PRAGMA table_info('results')"
        ).fetchall()}
        self.assertIn("spec", results_cols)
        self.assertIn("spec_status", results_cols)

    def test_v1_db_chains_v2_v3_and_v4(self):
        # v1 → v2 → v3 → v4 in one bootstrap pass. To simulate a v1
        # results table we have to drop the index that DuckDB attaches
        # to ``results`` before stripping the v2 columns — DuckDB
        # otherwise refuses the column drop with DependencyException.
        bootstrap(self.con)
        self.con.execute("DROP INDEX IF EXISTS results_proj_corner_idx")
        self.con.execute("DROP INDEX IF EXISTS results_run_id_idx")
        self.con.execute("ALTER TABLE results DROP COLUMN spec")
        self.con.execute("ALTER TABLE results DROP COLUMN spec_status")
        self.con.execute("ALTER TABLE runs DROP COLUMN starred")
        self.con.execute("ALTER TABLE runs DROP COLUMN milestone")
        self.con.execute("ALTER TABLE runs DROP COLUMN partial_run")
        self.con.execute(
            "UPDATE simkit_meta SET value = '1' WHERE key = 'db_schema_version'"
        )
        bootstrap(self.con)
        runs_cols = _runs_columns(self.con)
        results_cols = {r[1] for r in self.con.execute(
            "PRAGMA table_info('results')"
        ).fetchall()}
        for col in ("starred", "milestone", "partial_run"):
            self.assertIn(col, runs_cols)
        for col in ("spec", "spec_status"):
            self.assertIn(col, results_cols)
        row = self.con.execute(
            "SELECT value FROM simkit_meta WHERE key = 'db_schema_version'"
        ).fetchone()
        self.assertEqual(row[0], "5")

    def test_v4_db_rebootstrap_is_idempotent(self):
        # A DB already at v4 must round-trip through bootstrap without
        # ALTER errors and without bumping or shifting the meta row.
        bootstrap(self.con)
        _insert_minimal_run(self.con, run_id="r-keep")
        bootstrap(self.con)
        bootstrap(self.con)
        row = self.con.execute(
            "SELECT value FROM simkit_meta WHERE key = 'db_schema_version'"
        ).fetchone()
        self.assertEqual(row[0], "5")
        keep = self.con.execute(
            "SELECT run_id FROM runs WHERE run_id = 'r-keep'"
        ).fetchone()
        self.assertIsNotNone(keep)

    def test_future_db_refused_by_forward_compat_guard(self):
        # Forward-compat error path still triggers when the DB is marked
        # at a version > DB_SCHEMA_VERSION — proves bumping to v5 didn't
        # accidentally widen the guard.
        bootstrap(self.con)
        self.con.execute(
            "UPDATE simkit_meta SET value = '6' WHERE key = 'db_schema_version'"
        )
        with self.assertRaisesRegex(RuntimeError, "newer than this simkit"):
            bootstrap(self.con)

    def test_milestone_and_partial_run_roundtrip(self):
        # Round-trip an explicit milestone + partial_run on a fresh v4 DB.
        bootstrap(self.con)
        self.con.execute(
            """
            INSERT INTO runs (
              run_id, project_id, testbench_id, testbench_alias,
              timestamp, author, label, note,
              netlist_path, history_name, schema_version, ingested_at,
              milestone, partial_run
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "r-cdr", "proj", "tb", None,
                "2026-05-19 00:00:00+00", "alice", None, None,
                None, "history_cdr", 1, "2026-05-19 00:00:00+00",
                "CDR-2026q2", True,
            ],
        )
        ms, pr = self.con.execute(
            "SELECT milestone, partial_run FROM runs WHERE run_id = 'r-cdr'"
        ).fetchone()
        self.assertEqual(ms, "CDR-2026q2")
        self.assertTrue(bool(pr))


class TransactionTests(unittest.TestCase):
    """`transaction` commits on clean exit, rolls back on exception."""

    def setUp(self):
        self.con = connect(":memory:")
        bootstrap(self.con)

    def tearDown(self):
        self.con.close()

    def test_commit_on_success(self):
        with transaction(self.con):
            self.con.execute(
                "INSERT INTO simkit_meta(key, value) VALUES (?, ?)",
                ["k1", "v1"],
            )
        row = self.con.execute(
            "SELECT value FROM simkit_meta WHERE key = 'k1'"
        ).fetchone()
        self.assertEqual(row[0], "v1")

    def test_rollback_on_exception(self):
        class _Boom(RuntimeError):
            pass

        with self.assertRaises(_Boom):
            with transaction(self.con):
                self.con.execute(
                    "INSERT INTO simkit_meta(key, value) VALUES (?, ?)",
                    ["k2", "v2"],
                )
                raise _Boom("forced rollback")
        row = self.con.execute(
            "SELECT value FROM simkit_meta WHERE key = 'k2'"
        ).fetchone()
        self.assertIsNone(row)


class ConnectTests(unittest.TestCase):
    """`connect` accepts ``:memory:`` and on-disk paths."""

    def test_connect_memory(self):
        con = connect(":memory:")
        try:
            con.execute("SELECT 1").fetchone()
        finally:
            con.close()

    def test_connect_on_disk(self):
        with tempfile.TemporaryDirectory(prefix="simkit_db_") as tmp:
            path = Path(tmp) / "simkit.duckdb"
            con = connect(path)
            try:
                bootstrap(con)
            finally:
                con.close()
            # Reopen and confirm tables persisted.
            con2 = connect(path)
            try:
                rows = con2.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'main'"
                ).fetchall()
                names = {r[0] for r in rows}
                self.assertIn("runs", names)
            finally:
                con2.close()


if __name__ == "__main__":
    unittest.main()
