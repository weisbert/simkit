"""Unit tests for ``simkit.from_db`` (DB → JSON-shape reconstruction)."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.db import bootstrap, connect  # noqa: E402
from simkit.errors import RunNotFoundError  # noqa: E402
from simkit.from_db import load_dump_from_db  # noqa: E402
from simkit.ingest import ingest_run_json  # noqa: E402
from simkit.validate import validate_dump  # noqa: E402


_FIXTURES = _REPO_ROOT / "tests" / "fixtures" / "runs"
_SYN_MIN = _FIXTURES / "synthetic_minimal" / "run.json"
_SYN_ART = _FIXTURES / "synthetic_with_artifacts" / "run.json"
_REAL = _FIXTURES / "bdc13f17-d39b-4a13-b58e-846435996a29" / "run.json"
_SYN_MIN_RUN_ID = "11111111-1111-4111-8111-111111111111"
_SYN_ART_RUN_ID = "33333333-3333-4333-8333-333333333333"
_REAL_RUN_ID = "bdc13f17-d39b-4a13-b58e-846435996a29"


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


class LoadDumpFromDbTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_fromdb_"))
        self.db = self.tmp / "simkit.duckdb"
        self.con = connect(self.db)
        bootstrap(self.con)
        ingest_run_json(self.con, _SYN_MIN)
        ingest_run_json(self.con, _SYN_ART)
        ingest_run_json(self.con, _REAL)

    def tearDown(self):
        self.con.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_reconstruct_minimal_run(self):
        dump = load_dump_from_db(self.con, _SYN_MIN_RUN_ID)
        self.assertEqual(dump["schema_version"], 1)
        self.assertEqual(dump["run"]["run_id"], _SYN_MIN_RUN_ID)
        self.assertEqual(dump["run"]["project_id"], "synthetic")
        self.assertIn("results", dump)
        self.assertIn("artifacts", dump)

    def test_reconstructed_dump_passes_validator(self):
        for rid in (_SYN_MIN_RUN_ID, _SYN_ART_RUN_ID, _REAL_RUN_ID):
            dump = load_dump_from_db(self.con, rid)
            violations = validate_dump(dump)
            errors = [v for v in violations if v.severity == "error"]
            self.assertEqual(
                errors, [],
                f"validator errors for {rid}: {[v.message for v in errors]}",
            )

    def test_results_count_matches_original(self):
        original = _load_json(_REAL)
        dump = load_dump_from_db(self.con, _REAL_RUN_ID)
        self.assertEqual(len(dump["results"]), len(original["results"]))

    def test_artifacts_round_trip_for_synthetic_with_artifacts(self):
        original = _load_json(_SYN_ART)
        dump = load_dump_from_db(self.con, _SYN_ART_RUN_ID)
        # Artifact rows present, with expected keys.
        self.assertEqual(
            len(dump["artifacts"]), len(original["artifacts"]),
        )
        if original["artifacts"]:
            keys = set(dump["artifacts"][0].keys())
            self.assertEqual(
                keys,
                {"type", "relative_path", "description", "source", "created_at"},
            )

    def test_unknown_run_id_raises(self):
        with self.assertRaises(RunNotFoundError):
            load_dump_from_db(
                self.con,
                "00000000-0000-0000-0000-000000000000",
            )

    def test_corner_vars_and_sweep_decoded_as_dicts(self):
        dump = load_dump_from_db(self.con, _REAL_RUN_ID)
        # All results have dict corner_vars / sweep.
        for r in dump["results"]:
            self.assertIsInstance(r["corner_vars"], dict)
            self.assertIsInstance(r["sweep"], dict)

    def test_value_round_trip_numeric_and_null(self):
        dump = load_dump_from_db(self.con, _REAL_RUN_ID)
        # All status='ok' rows have numeric value (or string).
        # All __sim_status__ rows have value None (the real fixture has
        # no such rows but we still cover the conditional).
        for r in dump["results"]:
            if r["status"] == "ok":
                self.assertIsNotNone(r["value"])
            else:
                # not-ok rows always have value=None in our fixtures
                self.assertIsNone(r["value"])


if __name__ == "__main__":
    unittest.main()
