"""Unit tests for ``pvt validate`` (simkit.cli.validate).

Exit codes asserted:
    0 — clean
    1 — warnings only
    2 — error-severity violation
    3 — IO error / file not found / run_id not in DB
"""

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
_REAL_RUN = _FIXTURES / "bdc13f17-d39b-4a13-b58e-846435996a29" / "run.json"
_SYN_MIN = _FIXTURES / "synthetic_minimal" / "run.json"
_BAD_STATUS = _FIXTURES / "bad_status" / "run.json"
_REAL_RUN_ID = "bdc13f17-d39b-4a13-b58e-846435996a29"
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


class CliValidateFileTests(unittest.TestCase):
    """Default (file-mode) behaviour."""

    def test_clean_synthetic_returns_zero(self):
        rc, out, err = _run("validate", str(_SYN_MIN))
        self.assertEqual(rc, 0, f"stderr={err}")
        self.assertIn("OK", out)

    def test_real_fixture_warning_only_returns_one(self):
        # Real run has W2 (null netlist_path) but no errors → exit 1.
        rc, out, err = _run("validate", str(_REAL_RUN))
        self.assertEqual(rc, 1, f"out={out} err={err}")
        self.assertIn("W2", out)

    def test_bad_status_returns_two(self):
        rc, out, err = _run("validate", str(_BAD_STATUS))
        self.assertEqual(rc, 2, f"out={out} err={err}")
        self.assertIn("I12", out)

    def test_missing_file_returns_three(self):
        rc, _out, err = _run("validate", "/nonexistent/run.json")
        self.assertEqual(rc, 3, f"err={err}")
        self.assertIn("not a file", err)

    def test_no_target_returns_two(self):
        rc, _out, err = _run("validate")
        self.assertEqual(rc, 2, f"err={err}")
        self.assertIn("target is required", err)


class CliValidateFromDbTests(unittest.TestCase):
    """``--from-db`` mode: rebuild dump from DB row and validate."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_cli_val_db_"))
        self.db = self.tmp / "simkit.duckdb"
        con = connect(self.db)
        try:
            bootstrap(con)
            ingest_run_json(con, _SYN_MIN)
            ingest_run_json(con, _REAL_RUN)
        finally:
            con.close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_from_db_clean_returns_zero(self):
        rc, out, err = _run(
            "validate", _SYN_MIN_RUN_ID,
            "--from-db", "--db", str(self.db),
        )
        self.assertEqual(rc, 0, f"out={out} err={err}")
        self.assertIn("OK", out)

    def test_from_db_warning_returns_one(self):
        # The real fixture has W2 (null netlist_path).
        rc, out, err = _run(
            "validate", _REAL_RUN_ID,
            "--from-db", "--db", str(self.db),
        )
        self.assertEqual(rc, 1, f"out={out} err={err}")
        self.assertIn("W2", out)

    def test_from_db_unknown_run_id_returns_three(self):
        rc, _out, err = _run(
            "validate", "00000000-0000-0000-0000-000000000000",
            "--from-db", "--db", str(self.db),
        )
        self.assertEqual(rc, 3, f"err={err}")
        self.assertIn("not found", err)

    def test_from_db_missing_db_returns_three(self):
        rc, _out, err = _run(
            "validate", _SYN_MIN_RUN_ID,
            "--from-db", "--db", str(self.tmp / "no.duckdb"),
        )
        self.assertEqual(rc, 3, f"err={err}")
        self.assertIn("DB not found", err)


if __name__ == "__main__":
    unittest.main()
