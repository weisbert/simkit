"""Unit tests for ``pvt label`` CLI (simkit.cli.label)."""

from __future__ import annotations

import io
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.cli.__main__ import main as cli_main  # noqa: E402
from simkit.db import bootstrap, connect  # noqa: E402
from simkit.ingest import ingest_run_json  # noqa: E402


_FIXTURES = _REPO_ROOT / "tests" / "fixtures" / "runs"
_SYN_MIN_JSON = _FIXTURES / "synthetic_minimal" / "run.json"
_SYN_MIN_RUN_ID = "11111111-1111-4111-8111-111111111111"


def _run(*args: str) -> tuple:
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = cli_main(list(args))
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 2
    return rc, out.getvalue(), err.getvalue()


class CliLabelTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_cli_label_"))
        self.db = self.tmp / "simkit.duckdb"
        con = connect(self.db)
        try:
            bootstrap(con)
            ingest_run_json(con, _SYN_MIN_JSON)
        finally:
            con.close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _read_label(self):
        con = connect(self.db)
        try:
            return con.execute(
                "SELECT label FROM runs WHERE run_id = ?",
                [_SYN_MIN_RUN_ID],
            ).fetchone()[0]
        finally:
            con.close()

    def test_set_label_success(self):
        rc, out, err = _run(
            "label", _SYN_MIN_RUN_ID, "tt-golden",
            "--db", str(self.db),
        )
        self.assertEqual(rc, 0, f"err={err}")
        self.assertIn("set run_id=", out)
        self.assertIn("tt-golden", out)
        self.assertEqual(self._read_label(), "tt-golden")

    def test_overwrite_without_force_exit_1(self):
        rc1, _, _ = _run(
            "label", _SYN_MIN_RUN_ID, "v1", "--db", str(self.db),
        )
        self.assertEqual(rc1, 0)
        rc2, _out, err = _run(
            "label", _SYN_MIN_RUN_ID, "v2", "--db", str(self.db),
        )
        self.assertEqual(rc2, 1, f"err={err}")
        self.assertIn("already has label", err)
        # Unchanged.
        self.assertEqual(self._read_label(), "v1")

    def test_overwrite_with_force(self):
        _run("label", _SYN_MIN_RUN_ID, "v1", "--db", str(self.db))
        rc, out, err = _run(
            "label", _SYN_MIN_RUN_ID, "v2", "--force",
            "--db", str(self.db),
        )
        self.assertEqual(rc, 0, f"err={err}")
        self.assertIn("overwrote", out)
        self.assertEqual(self._read_label(), "v2")

    def test_clear_existing_label(self):
        _run("label", _SYN_MIN_RUN_ID, "v1", "--db", str(self.db))
        rc, out, err = _run(
            "label", _SYN_MIN_RUN_ID, "--clear", "--db", str(self.db),
        )
        self.assertEqual(rc, 0, f"err={err}")
        self.assertIn("cleared", out)
        self.assertIsNone(self._read_label())

    def test_clear_already_null_is_noop_exit_0(self):
        rc, out, err = _run(
            "label", _SYN_MIN_RUN_ID, "--clear", "--db", str(self.db),
        )
        self.assertEqual(rc, 0, f"err={err}")
        self.assertIn("noop", out)

    def test_clear_with_label_arg_exits_2(self):
        rc, _out, err = _run(
            "label", _SYN_MIN_RUN_ID, "v1", "--clear",
            "--db", str(self.db),
        )
        self.assertEqual(rc, 2)
        self.assertIn("--clear takes no label", err)

    def test_missing_label_arg_exits_2(self):
        rc, _out, err = _run(
            "label", _SYN_MIN_RUN_ID, "--db", str(self.db),
        )
        self.assertEqual(rc, 2, f"err={err}")
        self.assertIn("required", err)

    def test_unknown_run_id_exits_1(self):
        rc, _out, err = _run(
            "label", "00000000-0000-0000-0000-000000000000", "x",
            "--db", str(self.db),
        )
        self.assertEqual(rc, 1)
        self.assertIn("not found", err)

    def test_db_missing_exits_3(self):
        rc, _out, err = _run(
            "label", _SYN_MIN_RUN_ID, "x",
            "--db", str(self.tmp / "no.duckdb"),
        )
        self.assertEqual(rc, 3)
        self.assertIn("DB not found", err)


if __name__ == "__main__":
    unittest.main()
