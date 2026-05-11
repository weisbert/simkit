"""Unit tests for ``simkit.attach`` (core attach function).

Run with stdlib unittest:

    PYTHONPATH=python python3 -m unittest tests.test_attach -v
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.attach import attach_artifact  # noqa: E402
from simkit.db import bootstrap, connect  # noqa: E402
from simkit.errors import (  # noqa: E402
    DuplicateArtifactError,
    InvalidArtifactTypeError,
    MissingDumpError,
    RunNotFoundError,
    SimkitError,
)
from simkit.ingest import ingest_run_json  # noqa: E402


_FIXTURES = _REPO_ROOT / "tests" / "fixtures" / "runs"
_SYN_MIN_JSON = _FIXTURES / "synthetic_minimal" / "run.json"
_SYN_MIN_RUN_ID = "11111111-1111-4111-8111-111111111111"


def _frozen_now(s: str = "2026-05-11T22:30:00+00:00"):
    dt = datetime.fromisoformat(s)
    return lambda: dt


class AttachArtifactTests(unittest.TestCase):
    """Core behaviour of ``attach_artifact``."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_attach_"))
        self.db = self.tmp / "simkit.duckdb"
        self.runs_root = self.tmp / "runs"
        self.con = connect(self.db)
        bootstrap(self.con)
        # Seed one run via the real ingester so the runs row + initial
        # artifacts row layout is realistic.
        ingest_run_json(self.con, _SYN_MIN_JSON)
        # Source files for attach calls.
        self.src = self.tmp / "image.png"
        self.src.write_bytes(b"fake-png-bytes")

    def tearDown(self):
        self.con.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- happy path ----

    def test_attach_copies_file_and_inserts_row(self):
        res = attach_artifact(
            self.con,
            run_id=_SYN_MIN_RUN_ID,
            src_path=self.src,
            artifact_type="image",
            runs_root=self.runs_root,
            description="screenshot of the bode plot",
            now=_frozen_now(),
        )

        self.assertEqual(res.run_id, _SYN_MIN_RUN_ID)
        self.assertEqual(res.artifact_type, "image")
        self.assertEqual(res.relative_path, "artifacts/image.png")
        self.assertTrue(res.absolute_path.is_file())
        self.assertEqual(
            res.absolute_path,
            self.runs_root / _SYN_MIN_RUN_ID / "artifacts" / "image.png",
        )
        self.assertEqual(res.absolute_path.read_bytes(), b"fake-png-bytes")
        self.assertEqual(res.description, "screenshot of the bode plot")

        # DB row inserted.
        rows = self.con.execute(
            "SELECT type, relative_path, description, source FROM artifacts "
            "WHERE run_id = ? AND relative_path = ?",
            [_SYN_MIN_RUN_ID, "artifacts/image.png"],
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "image")
        self.assertEqual(rows[0][1], "artifacts/image.png")
        self.assertEqual(rows[0][2], "screenshot of the bode plot")
        self.assertEqual(rows[0][3], "manual")

    def test_attach_with_dest_name_override(self):
        res = attach_artifact(
            self.con,
            run_id=_SYN_MIN_RUN_ID,
            src_path=self.src,
            artifact_type="image",
            runs_root=self.runs_root,
            dest_name="bode_plot.png",
        )
        self.assertEqual(res.relative_path, "artifacts/bode_plot.png")
        self.assertTrue(
            (self.runs_root / _SYN_MIN_RUN_ID / "artifacts" / "bode_plot.png").is_file()
        )

    def test_attach_no_description(self):
        res = attach_artifact(
            self.con,
            run_id=_SYN_MIN_RUN_ID,
            src_path=self.src,
            artifact_type="other",
            runs_root=self.runs_root,
        )
        row = self.con.execute(
            "SELECT description FROM artifacts "
            "WHERE run_id = ? AND relative_path = ?",
            [_SYN_MIN_RUN_ID, "artifacts/image.png"],
        ).fetchone()
        self.assertIsNone(row[0])
        self.assertIsNone(res.description)

    def test_attach_creates_artifacts_dir(self):
        target = self.runs_root / _SYN_MIN_RUN_ID / "artifacts"
        self.assertFalse(target.exists())
        attach_artifact(
            self.con,
            run_id=_SYN_MIN_RUN_ID,
            src_path=self.src,
            artifact_type="image",
            runs_root=self.runs_root,
        )
        self.assertTrue(target.is_dir())

    def test_attach_records_iso_timestamp_with_offset(self):
        res = attach_artifact(
            self.con,
            run_id=_SYN_MIN_RUN_ID,
            src_path=self.src,
            artifact_type="image",
            runs_root=self.runs_root,
            now=_frozen_now("2026-05-11T22:30:00+00:00"),
        )
        # The AttachResult carries the formatted string; assert that, and
        # confirm the DB row is non-null + castable back to the same ISO.
        self.assertEqual(res.created_at, "2026-05-11T22:30:00+00:00")
        # DuckDB stores TIMESTAMPTZ as an instant; reading it back depends
        # on the connection's tz binding. Confirm the row exists and an
        # epoch-second cast lands on the same instant.
        epoch = self.con.execute(
            "SELECT CAST(EPOCH(created_at) AS BIGINT) FROM artifacts "
            "WHERE run_id = ? AND relative_path = ?",
            [_SYN_MIN_RUN_ID, "artifacts/image.png"],
        ).fetchone()[0]
        expected = int(
            datetime(2026, 5, 11, 22, 30, 0, tzinfo=timezone.utc).timestamp()
        )
        self.assertEqual(epoch, expected)

    # ---- error paths ----

    def test_attach_unknown_run_id(self):
        with self.assertRaises(RunNotFoundError):
            attach_artifact(
                self.con,
                run_id="00000000-0000-0000-0000-000000000000",
                src_path=self.src,
                artifact_type="image",
                runs_root=self.runs_root,
            )

    def test_attach_invalid_type(self):
        with self.assertRaises(InvalidArtifactTypeError):
            attach_artifact(
                self.con,
                run_id=_SYN_MIN_RUN_ID,
                src_path=self.src,
                artifact_type="bogus_type",
                runs_root=self.runs_root,
            )

    def test_attach_missing_src_file(self):
        with self.assertRaises(MissingDumpError):
            attach_artifact(
                self.con,
                run_id=_SYN_MIN_RUN_ID,
                src_path=self.tmp / "nonexistent.png",
                artifact_type="image",
                runs_root=self.runs_root,
            )

    def test_attach_dup_relative_path_in_db(self):
        attach_artifact(
            self.con,
            run_id=_SYN_MIN_RUN_ID,
            src_path=self.src,
            artifact_type="image",
            runs_root=self.runs_root,
        )
        # Second attempt with the same basename: must raise.
        src2 = self.tmp / "other.bin"
        src2.write_bytes(b"different")
        with self.assertRaises(DuplicateArtifactError):
            attach_artifact(
                self.con,
                run_id=_SYN_MIN_RUN_ID,
                src_path=src2,
                artifact_type="image",
                runs_root=self.runs_root,
                dest_name="image.png",  # same as first
            )

    def test_attach_dup_dest_file_on_disk(self):
        # Pre-existing file at the destination, but no DB row. Still error.
        dest = self.runs_root / _SYN_MIN_RUN_ID / "artifacts"
        dest.mkdir(parents=True)
        (dest / "image.png").write_bytes(b"squatter")
        with self.assertRaises(DuplicateArtifactError):
            attach_artifact(
                self.con,
                run_id=_SYN_MIN_RUN_ID,
                src_path=self.src,
                artifact_type="image",
                runs_root=self.runs_root,
            )
        # And the DB row was never inserted.
        n = self.con.execute(
            "SELECT COUNT(*) FROM artifacts WHERE run_id = ? AND relative_path = ?",
            [_SYN_MIN_RUN_ID, "artifacts/image.png"],
        ).fetchone()[0]
        self.assertEqual(n, 0)

    def test_attach_rejects_path_in_dest_name(self):
        with self.assertRaises(SimkitError):
            attach_artifact(
                self.con,
                run_id=_SYN_MIN_RUN_ID,
                src_path=self.src,
                artifact_type="image",
                runs_root=self.runs_root,
                dest_name="../escape.png",
            )

    def test_attach_rejects_empty_dest_name(self):
        with self.assertRaises(SimkitError):
            attach_artifact(
                self.con,
                run_id=_SYN_MIN_RUN_ID,
                src_path=self.src,
                artifact_type="image",
                runs_root=self.runs_root,
                dest_name="",
            )

    def test_attach_preserves_source_bytes(self):
        # 1 KB of mixed bytes, including non-UTF8 sequences.
        blob = bytes(range(256)) * 4
        big = self.tmp / "blob.bin"
        big.write_bytes(blob)
        res = attach_artifact(
            self.con,
            run_id=_SYN_MIN_RUN_ID,
            src_path=big,
            artifact_type="other",
            runs_root=self.runs_root,
        )
        self.assertEqual(res.absolute_path.read_bytes(), blob)


if __name__ == "__main__":
    unittest.main()
