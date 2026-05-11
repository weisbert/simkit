"""Unit tests for ``pvt ingest`` (simkit.cli.ingest + simkit.cli.__main__).

Run with stdlib unittest:

    PYTHONPATH=python python3.11 -m unittest tests.test_cli_ingest -v
"""

from __future__ import annotations

import io
import json
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


_FIXTURES = _REPO_ROOT / "tests" / "fixtures" / "runs"
_REAL_RUN = _FIXTURES / "bdc13f17-d39b-4a13-b58e-846435996a29" / "run.json"
_SYN_MIN = _FIXTURES / "synthetic_minimal" / "run.json"
_BAD_STATUS = _FIXTURES / "bad_status" / "run.json"


def _run(*args: str) -> tuple:
    """Invoke ``pvt`` with the given args, capturing stdout / stderr.

    Returns ``(rc, stdout, stderr)``.
    """
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = cli_main(list(args))
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 2
    return rc, out.getvalue(), err.getvalue()


class CliIngestTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_cli_"))
        self.db = self.tmp / "simkit.duckdb"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pvt_ingest_real_fixture_exit_zero(self):
        rc, out, err = _run(
            "ingest", str(_REAL_RUN), "--db", str(self.db),
        )
        self.assertEqual(rc, 0, f"stderr={err}")
        self.assertIn("inserted", out)
        # Spot-check the DB contents.
        con = connect(self.db)
        try:
            n = con.execute("SELECT COUNT(*) FROM results").fetchone()[0]
            self.assertEqual(n, 42)
        finally:
            con.close()

    def test_pvt_ingest_duplicate_exits_one_without_force(self):
        rc1, _, _ = _run(
            "ingest", str(_SYN_MIN), "--db", str(self.db),
        )
        self.assertEqual(rc1, 0)
        rc2, _out, err = _run(
            "ingest", str(_SYN_MIN), "--db", str(self.db),
        )
        self.assertEqual(rc2, 1, f"stderr={err}")

    def test_pvt_ingest_force_replaces(self):
        rc1, _, _ = _run(
            "ingest", str(_SYN_MIN), "--db", str(self.db),
        )
        self.assertEqual(rc1, 0)
        # Modify the dump and re-ingest with --force; the new note must
        # be in the DB.
        modified = self.tmp / "syn_min_v2"
        shutil.copytree(_SYN_MIN.parent, modified)
        with (modified / "run.json").open("r", encoding="utf-8") as f:
            dump = json.load(f)
        dump["run"]["note"] = "v2 note"
        (modified / "run.json").write_text(
            json.dumps(dump), encoding="utf-8",
        )
        rc2, out, err = _run(
            "ingest", str(modified / "run.json"),
            "--db", str(self.db), "--force",
        )
        self.assertEqual(rc2, 0, f"stderr={err}")
        self.assertIn("replaced", out)
        con = connect(self.db)
        try:
            note = con.execute(
                "SELECT note FROM runs WHERE run_id = ?",
                ["11111111-1111-4111-8111-111111111111"],
            ).fetchone()[0]
            self.assertEqual(note, "v2 note")
        finally:
            con.close()

    def test_pvt_ingest_continue_on_error(self):
        # Walk a fake dbRoot with one good + one bad-status run.
        runs_root = self.tmp / "runs"
        good = runs_root / "11111111-1111-4111-8111-111111111111"
        shutil.copytree(_SYN_MIN.parent, good)
        bad = runs_root / "55555555-5555-4555-8555-555555555555"
        shutil.copytree(_BAD_STATUS.parent, bad)
        rc, out, err = _run(
            "ingest", str(self.tmp), "--db", str(self.db),
            "--continue-on-error",
        )
        # Walk completes — exit 0 even with one failure skipped.
        self.assertEqual(rc, 0, f"stderr={err}")
        # The good one is in the DB.
        con = connect(self.db)
        try:
            ids = {
                r[0] for r in con.execute(
                    "SELECT run_id FROM runs"
                ).fetchall()
            }
            self.assertIn(
                "11111111-1111-4111-8111-111111111111", ids,
            )
            self.assertNotIn(
                "55555555-5555-4555-8555-555555555555", ids,
            )
        finally:
            con.close()

    def test_pvt_ingest_resolves_db_from_pvtproject(self):
        # Build a temp .pvtproject that points dbRoot at self.tmp.
        proj_dir = self.tmp / "proj"
        proj_dir.mkdir()
        (proj_dir / ".pvtproject").write_text(
            json.dumps({
                "project": "cli_test", "dbRoot": str(self.tmp / "data"),
            }),
            encoding="utf-8",
        )
        # Use PVT_PROJECT to force resolution.
        import os
        old = os.environ.get("PVT_PROJECT")
        os.environ["PVT_PROJECT"] = str(proj_dir / ".pvtproject")
        try:
            rc, out, err = _run("ingest", str(_SYN_MIN))
        finally:
            if old is None:
                os.environ.pop("PVT_PROJECT", None)
            else:
                os.environ["PVT_PROJECT"] = old
        self.assertEqual(rc, 0, f"stderr={err}")
        # DB landed at <dbRoot>/simkit.duckdb.
        derived_db = self.tmp / "data" / "simkit.duckdb"
        self.assertTrue(derived_db.is_file())

    def test_pvt_ingest_missing_path_returns_3(self):
        rc, _, err = _run(
            "ingest", str(self.tmp / "nope"), "--db", str(self.db),
        )
        self.assertEqual(rc, 3, f"err={err}")

    def test_pvt_ingest_no_validate_loads_bad_status(self):
        rc, out, err = _run(
            "ingest", str(_BAD_STATUS), "--db", str(self.db),
            "--no-validate",
        )
        self.assertEqual(rc, 0, f"stderr={err}")
        con = connect(self.db)
        try:
            n = con.execute(
                "SELECT COUNT(*) FROM results WHERE status = 'potato'"
            ).fetchone()[0]
            self.assertEqual(n, 1)
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
