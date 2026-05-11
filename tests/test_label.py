"""Unit tests for ``simkit.label`` (set_run_label core)."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.db import bootstrap, connect  # noqa: E402
from simkit.errors import (  # noqa: E402
    LabelConflictError,
    RunNotFoundError,
    SimkitError,
)
from simkit.ingest import ingest_run_json  # noqa: E402
from simkit.label import set_run_label  # noqa: E402


_FIXTURES = _REPO_ROOT / "tests" / "fixtures" / "runs"
_SYN_MIN_JSON = _FIXTURES / "synthetic_minimal" / "run.json"
_SYN_MIN_RUN_ID = "11111111-1111-4111-8111-111111111111"


class SetRunLabelTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_label_"))
        self.db = self.tmp / "simkit.duckdb"
        self.con = connect(self.db)
        bootstrap(self.con)
        ingest_run_json(self.con, _SYN_MIN_JSON)

    def tearDown(self):
        self.con.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _label(self) -> str | None:
        return self.con.execute(
            "SELECT label FROM runs WHERE run_id = ?",
            [_SYN_MIN_RUN_ID],
        ).fetchone()[0]

    # ---- happy paths ----

    def test_set_label_on_null(self):
        res = set_run_label(
            self.con, run_id=_SYN_MIN_RUN_ID, label="tt-golden",
        )
        self.assertEqual(res.action, "set")
        self.assertIsNone(res.previous)
        self.assertEqual(res.current, "tt-golden")
        self.assertEqual(self._label(), "tt-golden")

    def test_overwrite_with_force(self):
        set_run_label(self.con, run_id=_SYN_MIN_RUN_ID, label="v1")
        res = set_run_label(
            self.con, run_id=_SYN_MIN_RUN_ID, label="v2", force=True,
        )
        self.assertEqual(res.action, "overwritten")
        self.assertEqual(res.previous, "v1")
        self.assertEqual(res.current, "v2")
        self.assertEqual(self._label(), "v2")

    def test_clear_an_existing_label(self):
        set_run_label(self.con, run_id=_SYN_MIN_RUN_ID, label="v1")
        res = set_run_label(self.con, run_id=_SYN_MIN_RUN_ID, label=None)
        self.assertEqual(res.action, "cleared")
        self.assertEqual(res.previous, "v1")
        self.assertIsNone(res.current)
        self.assertIsNone(self._label())

    def test_clear_already_null_is_noop(self):
        res = set_run_label(self.con, run_id=_SYN_MIN_RUN_ID, label=None)
        self.assertEqual(res.action, "noop")
        self.assertIsNone(res.previous)
        self.assertIsNone(res.current)

    def test_clear_does_not_require_force(self):
        set_run_label(self.con, run_id=_SYN_MIN_RUN_ID, label="v1")
        # force=False (default) — should still clear without error.
        res = set_run_label(self.con, run_id=_SYN_MIN_RUN_ID, label=None)
        self.assertEqual(res.action, "cleared")

    def test_label_is_stripped(self):
        set_run_label(
            self.con, run_id=_SYN_MIN_RUN_ID, label="  golden  ",
        )
        self.assertEqual(self._label(), "golden")

    # ---- error paths ----

    def test_overwrite_without_force_raises(self):
        set_run_label(self.con, run_id=_SYN_MIN_RUN_ID, label="v1")
        with self.assertRaises(LabelConflictError):
            set_run_label(
                self.con, run_id=_SYN_MIN_RUN_ID, label="v2",
            )
        # State unchanged.
        self.assertEqual(self._label(), "v1")

    def test_unknown_run_id(self):
        with self.assertRaises(RunNotFoundError):
            set_run_label(
                self.con,
                run_id="00000000-0000-0000-0000-000000000000",
                label="x",
            )

    def test_empty_label_rejected(self):
        with self.assertRaises(SimkitError):
            set_run_label(self.con, run_id=_SYN_MIN_RUN_ID, label="")

    def test_whitespace_only_label_rejected(self):
        with self.assertRaises(SimkitError):
            set_run_label(
                self.con, run_id=_SYN_MIN_RUN_ID, label="   \t  ",
            )

    def test_newline_in_label_rejected(self):
        with self.assertRaises(SimkitError):
            set_run_label(
                self.con, run_id=_SYN_MIN_RUN_ID, label="v1\nv2",
            )

    def test_overlong_label_rejected(self):
        with self.assertRaises(SimkitError):
            set_run_label(
                self.con, run_id=_SYN_MIN_RUN_ID, label="x" * 201,
            )


if __name__ == "__main__":
    unittest.main()
