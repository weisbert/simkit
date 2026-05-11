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

    def test_bootstrap_does_not_overwrite_meta_version(self):
        bootstrap(self.con)
        # Force a different version, re-bootstrap, expect no overwrite.
        self.con.execute(
            "UPDATE simkit_meta SET value = '99' WHERE key = 'db_schema_version'"
        )
        bootstrap(self.con)
        row = self.con.execute(
            "SELECT value FROM simkit_meta WHERE key = 'db_schema_version'"
        ).fetchone()
        self.assertEqual(row[0], "99")


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
