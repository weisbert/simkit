"""Unit tests for ``pvt attach`` CLI (simkit.cli.attach).

Run with stdlib unittest:

    PYTHONPATH=python python3 -m unittest tests.test_cli_attach -v
"""

from __future__ import annotations

import io
import json
import os
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


class CliAttachTests(unittest.TestCase):
    """End-to-end CLI surface for ``pvt attach``."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_cli_attach_"))
        self.db = self.tmp / "simkit.duckdb"
        # Seed the DB with one run.
        con = connect(self.db)
        try:
            bootstrap(con)
            ingest_run_json(con, _SYN_MIN_JSON)
        finally:
            con.close()
        # Source file.
        self.src = self.tmp / "img.png"
        self.src.write_bytes(b"fake-png")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pvt_attach_success(self):
        rc, out, err = _run(
            "attach", _SYN_MIN_RUN_ID, str(self.src),
            "--type", "image", "--desc", "hello",
            "--db", str(self.db),
        )
        self.assertEqual(rc, 0, f"stderr={err}")
        self.assertIn("attached artifacts/img.png", out)
        # File copied.
        copied = self.tmp / "runs" / _SYN_MIN_RUN_ID / "artifacts" / "img.png"
        self.assertTrue(copied.is_file())
        # Row inserted.
        con = connect(self.db)
        try:
            n = con.execute(
                "SELECT COUNT(*) FROM artifacts WHERE run_id = ? "
                "AND relative_path = 'artifacts/img.png'",
                [_SYN_MIN_RUN_ID],
            ).fetchone()[0]
            self.assertEqual(n, 1)
        finally:
            con.close()

    def test_pvt_attach_invalid_type_rejected_by_argparse(self):
        rc, _out, err = _run(
            "attach", _SYN_MIN_RUN_ID, str(self.src),
            "--type", "bogus", "--db", str(self.db),
        )
        # argparse choices=… ⇒ exit 2.
        self.assertEqual(rc, 2, f"err={err}")

    def test_pvt_attach_unknown_run_id_exits_1(self):
        rc, _out, err = _run(
            "attach", "00000000-0000-0000-0000-000000000000",
            str(self.src), "--type", "image",
            "--db", str(self.db),
        )
        self.assertEqual(rc, 1)
        self.assertIn("not found", err)

    def test_pvt_attach_missing_src_exits_3(self):
        rc, _out, err = _run(
            "attach", _SYN_MIN_RUN_ID,
            str(self.tmp / "nope.png"),
            "--type", "image", "--db", str(self.db),
        )
        self.assertEqual(rc, 3, f"err={err}")

    def test_pvt_attach_duplicate_exits_1(self):
        rc1, _, _ = _run(
            "attach", _SYN_MIN_RUN_ID, str(self.src),
            "--type", "image", "--db", str(self.db),
        )
        self.assertEqual(rc1, 0)
        # Second call collides on basename.
        rc2, _out, err = _run(
            "attach", _SYN_MIN_RUN_ID, str(self.src),
            "--type", "image", "--db", str(self.db),
        )
        self.assertEqual(rc2, 1, f"err={err}")
        self.assertIn("already exists", err)

    def test_pvt_attach_as_rename_avoids_collision(self):
        rc1, _, _ = _run(
            "attach", _SYN_MIN_RUN_ID, str(self.src),
            "--type", "image", "--db", str(self.db),
        )
        self.assertEqual(rc1, 0)
        rc2, out, err = _run(
            "attach", _SYN_MIN_RUN_ID, str(self.src),
            "--type", "image", "--db", str(self.db),
            "--as", "img_v2.png",
        )
        self.assertEqual(rc2, 0, f"err={err}")
        self.assertIn("artifacts/img_v2.png", out)

    def test_pvt_attach_db_missing_exits_3(self):
        rc, _out, err = _run(
            "attach", _SYN_MIN_RUN_ID, str(self.src),
            "--type", "image",
            "--db", str(self.tmp / "no_such.duckdb"),
        )
        self.assertEqual(rc, 3, f"err={err}")
        self.assertIn("DB not found", err)

    def test_pvt_attach_resolves_db_from_pvtproject(self):
        proj_dir = self.tmp / "proj"
        proj_dir.mkdir()
        (proj_dir / ".pvtproject").write_text(
            json.dumps({
                "project": "attach_test", "dbRoot": str(self.tmp),
            }),
            encoding="utf-8",
        )
        old = os.environ.get("PVT_PROJECT")
        os.environ["PVT_PROJECT"] = str(proj_dir / ".pvtproject")
        try:
            rc, _out, err = _run(
                "attach", _SYN_MIN_RUN_ID, str(self.src),
                "--type", "image",
            )
        finally:
            if old is None:
                os.environ.pop("PVT_PROJECT", None)
            else:
                os.environ["PVT_PROJECT"] = old
        self.assertEqual(rc, 0, f"err={err}")


if __name__ == "__main__":
    unittest.main()
