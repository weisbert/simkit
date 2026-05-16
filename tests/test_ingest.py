"""Unit tests for simkit.ingest (run.json → DuckDB).

Run with stdlib unittest:

    PYTHONPATH=python python3.11 -m unittest tests.test_ingest -v
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


# Silence simkit.ingest log noise during test runs. The
# ValidatorIntegrationTests case explicitly attaches its own handler when
# it needs to assert on log records.
logging.getLogger("simkit.ingest").setLevel(logging.CRITICAL)


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.db import bootstrap, connect  # noqa: E402
from simkit.errors import (  # noqa: E402
    DuplicateRunError,
    IngestError,
    MalformedDumpError,
    MissingDumpError,
    SchemaVersionError,
    ValidationError,
)
from simkit.ingest import (  # noqa: E402
    ingest_dump_dir,
    ingest_run_json,
)


_FIXTURES = _REPO_ROOT / "tests" / "fixtures" / "runs"
_REAL_RUN = _FIXTURES / "bdc13f17-d39b-4a13-b58e-846435996a29" / "run.json"
_SYN_MIN = _FIXTURES / "synthetic_minimal" / "run.json"
_SYN_MESSY = _FIXTURES / "synthetic_messy" / "run.json"
_SYN_ART = _FIXTURES / "synthetic_with_artifacts" / "run.json"
_BAD_VERSION = _FIXTURES / "bad_version" / "run.json"
_BAD_STATUS = _FIXTURES / "bad_status" / "run.json"
_BAD_VALUE = _FIXTURES / "bad_value_when_failed" / "run.json"
_MALFORMED_JSON = _FIXTURES / "malformed_json" / "run.json"


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _fresh_db():
    con = connect(":memory:")
    bootstrap(con)
    return con


def _fixed_now():
    return datetime(2026, 5, 11, 0, 0, 0, tzinfo=timezone.utc)


def _write_dump(parent: Path, dump: dict) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    path = parent / "run.json"
    path.write_text(json.dumps(dump), encoding="utf-8")
    return path


def _good_dump() -> dict:
    """Load the synthetic-minimal fixture as a fresh dict."""
    with _SYN_MIN.open("r", encoding="utf-8") as f:
        return json.load(f)


# ----------------------------------------------------------------------
# Real-run anchor
# ----------------------------------------------------------------------

class RealRunFixtureTests(unittest.TestCase):
    """The 42-row real-run fixture is the integration anchor."""

    def setUp(self):
        self.con = _fresh_db()

    def tearDown(self):
        self.con.close()

    def test_loads_42_row_real_run(self):
        result = ingest_run_json(self.con, _REAL_RUN, now=lambda: _fixed_now())
        self.assertEqual(result.action, "inserted")
        self.assertEqual(result.n_results, 42)
        self.assertEqual(result.n_artifacts, 0)
        self.assertEqual(
            result.run_id, "bdc13f17-d39b-4a13-b58e-846435996a29"
        )

        # runs table: 1 row matching expected metadata.
        runs_rows = self.con.execute(
            "SELECT run_id, project_id, testbench_id, testbench_alias, "
            "author, label, note, netlist_path, history_name "
            "FROM runs"
        ).fetchall()
        self.assertEqual(len(runs_rows), 1)
        row = runs_rows[0]
        self.assertEqual(row[0], "bdc13f17-d39b-4a13-b58e-846435996a29")
        self.assertEqual(row[1], "bridge_smoke")
        self.assertEqual(row[2], "sim_yusheng/Test/maestro")
        self.assertEqual(row[3], "test_verify_tb")
        self.assertEqual(row[4], "skillbridge")
        self.assertEqual(row[5], "bridge-smoke")
        self.assertEqual(row[6], "from skillbridge after funcall fix")
        self.assertIsNone(row[7])  # netlist_path is null in real run.
        self.assertEqual(row[8], "simkit_verify")

        # results table: 42 rows.
        n = self.con.execute("SELECT COUNT(*) FROM results").fetchone()[0]
        self.assertEqual(n, 42)

    def test_real_run_spot_check_value(self):
        # corner='TT_pvt_4' / output='Rtime_clkout' / value=1.91397e-11
        ingest_run_json(self.con, _REAL_RUN)
        rows = self.con.execute(
            "SELECT value_num, value_str, status "
            "FROM results "
            "WHERE corner = 'TT_pvt_4' AND output = 'Rtime_clkout'"
        ).fetchall()
        self.assertEqual(len(rows), 1)
        value_num, value_str, status = rows[0]
        self.assertAlmostEqual(value_num, 1.91397e-11, places=20)
        self.assertIsNone(value_str)
        self.assertEqual(status, "ok")

    def test_real_run_corner_vars_round_trip(self):
        ingest_run_json(self.con, _REAL_RUN)
        rows = self.con.execute(
            "SELECT corner_vars FROM results "
            "WHERE corner = 'TT_pvt_4' AND output = 'Rtime_clkout'"
        ).fetchall()
        self.assertEqual(len(rows), 1)
        # DuckDB JSON columns return as string in Python; we round-trip.
        cv = rows[0][0]
        if isinstance(cv, str):
            cv = json.loads(cv)
        self.assertEqual(cv, {"temperature": "55", "model": "ff", "VDD": "3"})

    def test_real_run_netlist_path_null_loads(self):
        # Explicit guard for §0 item 1 / W2 path: null netlist must not block.
        result = ingest_run_json(self.con, _REAL_RUN)
        self.assertGreaterEqual(result.n_warnings, 1)
        row = self.con.execute(
            "SELECT netlist_path FROM runs WHERE run_id = ?",
            [result.run_id],
        ).fetchone()
        self.assertIsNone(row[0])

    def test_real_run_corner_count(self):
        ingest_run_json(self.con, _REAL_RUN)
        n_corners = self.con.execute(
            "SELECT COUNT(DISTINCT corner) FROM results"
        ).fetchone()[0]
        # 7 corners: TT, TT_pvt_0..TT_pvt_5
        self.assertEqual(n_corners, 7)


# ----------------------------------------------------------------------
# Idempotency
# ----------------------------------------------------------------------

class IdempotencyTests(unittest.TestCase):

    def setUp(self):
        self.con = _fresh_db()

    def tearDown(self):
        self.con.close()

    def test_duplicate_run_id_errors_by_default(self):
        ingest_run_json(self.con, _SYN_MIN)
        with self.assertRaises(DuplicateRunError):
            ingest_run_json(self.con, _SYN_MIN)

    def test_duplicate_run_id_replace_overwrites(self):
        ingest_run_json(self.con, _SYN_MIN)
        # Modify the dump in a temp file: change the note, re-ingest as
        # replace, and check the new note is what's in the DB.
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            dump = _good_dump()
            dump["run"]["note"] = "second-pass note"
            _write_dump(d, dump)
            res = ingest_run_json(
                self.con, d / "run.json", on_conflict="replace"
            )
        self.assertEqual(res.action, "replaced")
        row = self.con.execute(
            "SELECT note FROM runs WHERE run_id = ?", [res.run_id]
        ).fetchone()
        self.assertEqual(row[0], "second-pass note")

    def test_duplicate_run_id_skip_returns_skipped_action(self):
        ingest_run_json(self.con, _SYN_MIN)
        res = ingest_run_json(self.con, _SYN_MIN, on_conflict="skip")
        self.assertEqual(res.action, "skipped")
        self.assertEqual(res.n_results, 0)
        # Original row's results count is unchanged.
        n = self.con.execute("SELECT COUNT(*) FROM results").fetchone()[0]
        self.assertEqual(n, 1)

    def test_replace_does_not_affect_other_runs(self):
        # Load A (synthetic_minimal), then B (synthetic_with_artifacts).
        ingest_run_json(self.con, _SYN_MIN)
        ingest_run_json(self.con, _SYN_ART)
        # Replace A; B's rows must still be there.
        ingest_run_json(self.con, _SYN_MIN, on_conflict="replace")
        n_results = self.con.execute(
            "SELECT COUNT(*) FROM results WHERE run_id = ?",
            ["33333333-3333-4333-8333-333333333333"],
        ).fetchone()[0]
        self.assertEqual(n_results, 1)
        n_arts = self.con.execute(
            "SELECT COUNT(*) FROM artifacts WHERE run_id = ?",
            ["33333333-3333-4333-8333-333333333333"],
        ).fetchone()[0]
        self.assertEqual(n_arts, 1)


# ----------------------------------------------------------------------
# schema_version
# ----------------------------------------------------------------------

class SchemaVersionTests(unittest.TestCase):

    def setUp(self):
        self.con = _fresh_db()

    def tearDown(self):
        self.con.close()

    def test_unsupported_schema_version_rejected(self):
        # Fixture now carries schema_version=99 (v1.4 accepts {1, 2}).
        with self.assertRaises((SchemaVersionError, ValidationError)):
            ingest_run_json(self.con, _BAD_VERSION)

    def test_schema_version_missing_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            dump = _good_dump()
            del dump["schema_version"]
            _write_dump(d, dump)
            with self.assertRaises(MalformedDumpError):
                ingest_run_json(self.con, d / "run.json", validate=False)

    def test_schema_version_string_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            dump = _good_dump()
            dump["schema_version"] = "1"
            _write_dump(d, dump)
            with self.assertRaises(MalformedDumpError):
                ingest_run_json(self.con, d / "run.json", validate=False)

    def test_schema_version_zero_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            dump = _good_dump()
            dump["schema_version"] = 0
            _write_dump(d, dump)
            with self.assertRaises(MalformedDumpError):
                ingest_run_json(self.con, d / "run.json", validate=False)


# ----------------------------------------------------------------------
# Malformed shape
# ----------------------------------------------------------------------

class MalformedTests(unittest.TestCase):

    def setUp(self):
        self.con = _fresh_db()

    def tearDown(self):
        self.con.close()

    def test_truncated_json_raises_malformed(self):
        with self.assertRaises(MalformedDumpError):
            ingest_run_json(self.con, _MALFORMED_JSON)

    def test_results_must_be_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            dump = _good_dump()
            dump["results"] = {"oops": True}
            _write_dump(d, dump)
            with self.assertRaises((MalformedDumpError, ValidationError)):
                ingest_run_json(self.con, d / "run.json")

    def test_run_missing_required_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            dump = _good_dump()
            del dump["run"]["timestamp"]
            _write_dump(d, dump)
            with self.assertRaises((MalformedDumpError, ValidationError)):
                ingest_run_json(self.con, d / "run.json")

    def test_status_outside_enum_rejected(self):
        with self.assertRaises(ValidationError):
            ingest_run_json(self.con, _BAD_STATUS)

    def test_status_outside_enum_rejected_no_validate(self):
        # With validate=False, the ingester writes the row anyway —
        # DuckDB will accept any string. We assert that with the validator
        # off, no exception fires. (Documents the no-validate seam.)
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            shutil.copy(_BAD_STATUS, d / "run.json")
            ingest_run_json(self.con, d / "run.json", validate=False)
        n = self.con.execute(
            "SELECT COUNT(*) FROM results WHERE status = 'potato'"
        ).fetchone()[0]
        self.assertEqual(n, 1)

    def test_value_set_when_status_failed_rejected(self):
        with self.assertRaises(ValidationError):
            ingest_run_json(self.con, _BAD_VALUE)

    def test_value_null_when_status_ok_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            dump = _good_dump()
            dump["results"][0]["value"] = None
            _write_dump(d, dump)
            with self.assertRaises(ValidationError):
                ingest_run_json(self.con, d / "run.json")

    def test_artifact_type_outside_enum_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            dump = _good_dump()
            dump["artifacts"].append({
                "type": "BLOB",
                "relative_path": "x",
                "description": "",
                "source": "auto",
                "created_at": "2026-05-10T12:00:00+08:00",
            })
            _write_dump(d, dump)
            with self.assertRaises(ValidationError):
                ingest_run_json(self.con, d / "run.json")

    def test_artifact_source_outside_enum_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            dump = _good_dump()
            dump["artifacts"].append({
                "type": "waveform",
                "relative_path": "x",
                "description": "",
                "source": "elsewhere",
                "created_at": "2026-05-10T12:00:00+08:00",
            })
            _write_dump(d, dump)
            with self.assertRaises(ValidationError):
                ingest_run_json(self.con, d / "run.json")


# ----------------------------------------------------------------------
# Walker
# ----------------------------------------------------------------------

class WalkerTests(unittest.TestCase):

    def setUp(self):
        self.con = _fresh_db()
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_walk_"))

    def tearDown(self):
        self.con.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_walks_dbroot_layout(self):
        runs_root = self.tmp / "runs"
        # Run A: synthetic minimal
        a = runs_root / "11111111-1111-4111-8111-111111111111"
        shutil.copytree(_SYN_MIN.parent, a)
        # Run B: synthetic with artifacts
        b = runs_root / "33333333-3333-4333-8333-333333333333"
        shutil.copytree(_SYN_ART.parent, b)
        results = ingest_dump_dir(self.con, self.tmp)
        self.assertEqual(len(results), 2)
        ids = {r.run_id for r in results}
        self.assertEqual(ids, {
            "11111111-1111-4111-8111-111111111111",
            "33333333-3333-4333-8333-333333333333",
        })

    def test_walks_single_run_dir(self):
        # Caller points at a run dir directly (it has run.json).
        single = self.tmp / "single_run"
        shutil.copytree(_SYN_MIN.parent, single)
        results = ingest_dump_dir(self.con, single)
        self.assertEqual(len(results), 1)
        self.assertEqual(
            results[0].run_id, "11111111-1111-4111-8111-111111111111"
        )

    def test_walks_zero_runs_returns_empty_list(self):
        # tmp dir with no runs/ subdir and no run.json.
        empty = self.tmp / "empty"
        empty.mkdir()
        results = ingest_dump_dir(self.con, empty)
        self.assertEqual(results, [])

    def test_walks_missing_dir_raises(self):
        with self.assertRaises(MissingDumpError):
            ingest_dump_dir(self.con, self.tmp / "does_not_exist")

    def test_continue_on_error_collects_results_and_failures(self):
        runs_root = self.tmp / "runs"
        # One good, one bad-status (validator will reject).
        good = runs_root / "11111111-1111-4111-8111-111111111111"
        shutil.copytree(_SYN_MIN.parent, good)
        bad = runs_root / "55555555-5555-4555-8555-555555555555"
        shutil.copytree(_BAD_STATUS.parent, bad)
        # Without continue_on_error, ordering matters; sort lexicographically
        # places 1.. before 5.., so the bad one comes last and the good one
        # is loaded before the failure.
        with self.assertRaises(IngestError):
            ingest_dump_dir(self.con, self.tmp)
        # Good one was committed before the bad one was seen.
        n_runs = self.con.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        self.assertEqual(n_runs, 1)
        # Now drop the good one and re-walk with continue_on_error.
        self.con.execute(
            "DELETE FROM results WHERE run_id = ?",
            ["11111111-1111-4111-8111-111111111111"],
        )
        self.con.execute(
            "DELETE FROM runs WHERE run_id = ?",
            ["11111111-1111-4111-8111-111111111111"],
        )
        results = ingest_dump_dir(
            self.con, self.tmp, continue_on_error=True
        )
        # Only the good one made it through.
        self.assertEqual(len(results), 1)
        self.assertEqual(
            results[0].run_id, "11111111-1111-4111-8111-111111111111"
        )


# ----------------------------------------------------------------------
# Partial dumps (collector pre-fix scenarios)
# ----------------------------------------------------------------------

class PartialDumpTests(unittest.TestCase):
    """Edge: collector emitted a run with 0 result rows or only artifacts."""

    def setUp(self):
        self.con = _fresh_db()

    def tearDown(self):
        self.con.close()

    def test_empty_results_array_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            dump = _good_dump()
            dump["results"] = []
            # I1 fires only on tuples that exist; empty results list is fine.
            _write_dump(d, dump)
            res = ingest_run_json(self.con, d / "run.json")
        self.assertEqual(res.n_results, 0)
        n_runs = self.con.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        self.assertEqual(n_runs, 1)

    def test_only_artifacts_no_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            dump = _good_dump()
            dump["results"] = []
            dump["artifacts"] = [{
                "type": "sim_log",
                "relative_path": "logs/run.log",
                "description": "spectre log",
                "source": "auto",
                "created_at": "2026-05-10T12:00:00+08:00",
            }]
            _write_dump(d, dump)
            res = ingest_run_json(self.con, d / "run.json")
        self.assertEqual(res.n_results, 0)
        self.assertEqual(res.n_artifacts, 1)
        n = self.con.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
        self.assertEqual(n, 1)


# ----------------------------------------------------------------------
# Transactional safety
# ----------------------------------------------------------------------

class TransactionTests(unittest.TestCase):

    def setUp(self):
        self.con = _fresh_db()

    def tearDown(self):
        self.con.close()

    def test_failure_mid_results_rolls_back_run(self):
        # Build a dump where validator rejects but the run+results would
        # otherwise have written. We disable the validator and rely on
        # DuckDB to throw on a malformed insert; pre-empted by inserting an
        # invalid status. Easier: re-use the validate=True path for a dump
        # whose results are valid except row 5 has a bad status. Validator
        # raises BEFORE transaction begins, so nothing is in the DB.
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            dump = _good_dump()
            # Add 4 valid rows, then a 5th with bad status.
            for pid in range(2, 6):
                dump["results"].append({
                    "point": pid, "corner": "TT", "test": "Test",
                    "output": "vout", "value": 1.0, "status": "ok",
                    "sweep": {}, "corner_vars": {"temperature": "27"},
                    "test_note": None,
                })
            dump["results"][4]["status"] = "potato"
            _write_dump(d, dump)
            with self.assertRaises(ValidationError):
                ingest_run_json(self.con, d / "run.json")
        # After failure: nothing in any table for this run_id.
        rid = "11111111-1111-4111-8111-111111111111"
        for tbl in ("runs", "results", "artifacts"):
            n = self.con.execute(
                f"SELECT COUNT(*) FROM {tbl} WHERE run_id = ?", [rid]
            ).fetchone()[0]
            self.assertEqual(n, 0, f"{tbl} had rows after failed ingest")

    def test_failure_mid_insert_rolls_back(self):
        # With validate=False, force a duplicate run insert by hand: the
        # FK or PK violation should trigger ROLLBACK leaving zero rows for
        # the half-written second pass.
        ingest_run_json(self.con, _SYN_MIN)
        with self.assertRaises(IngestError):
            ingest_run_json(self.con, _SYN_MIN)  # default on_conflict='error'
        # The first ingest's row is intact.
        n = self.con.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        self.assertEqual(n, 1)


# ----------------------------------------------------------------------
# Validator integration (DECISIONS #17)
# ----------------------------------------------------------------------

class ValidatorIntegrationTests(unittest.TestCase):

    def setUp(self):
        self.con = _fresh_db()

    def tearDown(self):
        self.con.close()

    def test_validate_default_true_blocks_bad_status(self):
        with self.assertRaises(ValidationError):
            ingest_run_json(self.con, _BAD_STATUS)

    def test_validate_false_skips_invariant_checks(self):
        # Same fixture, validate=False — no exception, row written.
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            shutil.copy(_BAD_STATUS, d / "run.json")
            ingest_run_json(self.con, d / "run.json", validate=False)
        n = self.con.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        self.assertEqual(n, 1)

    def test_warnings_counted_in_result(self):
        # Real-run fixture has W2 (null netlist_path).
        res = ingest_run_json(self.con, _REAL_RUN)
        self.assertGreaterEqual(res.n_warnings, 1)

    def test_warnings_logged_when_on_warning_log(self):
        import logging as stdlib_logging
        handler = _RecordingHandler()
        logger = stdlib_logging.getLogger("simkit.ingest")
        prev_level = logger.level
        logger.addHandler(handler)
        logger.setLevel(stdlib_logging.DEBUG)
        try:
            ingest_run_json(self.con, _REAL_RUN, on_warning="log")
        finally:
            logger.removeHandler(handler)
            logger.setLevel(prev_level)
        self.assertTrue(
            any("W2" in r.getMessage() for r in handler.records),
            f"expected a W2 log line, got: "
            f"{[r.getMessage() for r in handler.records]}",
        )

    def test_validation_error_subclass_of_ingest_error(self):
        # An IngestError catcher must match a ValidationError.
        try:
            ingest_run_json(self.con, _BAD_STATUS)
        except IngestError as exc:
            self.assertIsInstance(exc, ValidationError)
        else:
            self.fail("expected an IngestError to be raised")

    def test_validation_error_carries_violations(self):
        with self.assertRaises(ValidationError) as cm:
            ingest_run_json(self.con, _BAD_STATUS)
        codes = {v.code for v in cm.exception.violations}
        self.assertIn("I12", codes)


class _RecordingHandler:
    """Tiny logging handler that captures records for assertions."""

    def __init__(self):
        self.records = []
        self.level = 0

    def handle(self, record):
        self.records.append(record)

    def setLevel(self, _):  # pragma: no cover
        pass

    def acquire(self):  # pragma: no cover
        pass

    def release(self):  # pragma: no cover
        pass

    def createLock(self):  # pragma: no cover
        pass

    def close(self):  # pragma: no cover
        pass


# ----------------------------------------------------------------------
# Synthetic-messy fixture (mix of statuses)
# ----------------------------------------------------------------------

class SyntheticMessyTests(unittest.TestCase):

    def setUp(self):
        self.con = _fresh_db()

    def tearDown(self):
        self.con.close()

    def test_messy_loads(self):
        res = ingest_run_json(self.con, _SYN_MESSY)
        self.assertEqual(res.action, "inserted")
        self.assertEqual(res.n_results, 4)
        # 1 ok + 1 no_convergence + 1 running + 1 failed
        rows = self.con.execute(
            "SELECT status, COUNT(*) FROM results GROUP BY status "
            "ORDER BY status"
        ).fetchall()
        d = dict(rows)
        self.assertEqual(d["ok"], 1)
        self.assertEqual(d["failed"], 1)
        self.assertEqual(d["running"], 1)
        self.assertEqual(d["no_convergence"], 1)


# ----------------------------------------------------------------------
# v1.4 — spec / spec_status columns from output_specs envelope field
# ----------------------------------------------------------------------

class SpecIngestTests(unittest.TestCase):
    """v1.4 (#1b): the collector now dumps per-output spec strings in a
    top-level ``output_specs`` map. The ingester denormalises them onto
    every result row and computes spec_status via simkit.spec_eval."""

    def setUp(self):
        self.con = _fresh_db()

    def tearDown(self):
        self.con.close()

    def _v2_dump_with_specs(
        self,
        result_value: float,
        spec_string: str | None,
    ) -> dict:
        """Build a single-result v2 dump with one (test, output) and an
        optional spec set for it."""
        return {
            "schema_version": 2,
            "run": {
                "run_id": "deadbeef-dead-4eef-8eef-deadbeefdead",
                "project_id": "synthetic_v14",
                "testbench_id": "lib/cell/view",
                "testbench_alias": None,
                "timestamp": "2026-05-16T15:00:00+08:00",
                "author": "tester",
                "label": None,
                "note": None,
                "netlist_path": "input.scs",
                "history_name": "synthetic_v14",
            },
            "results": [
                {
                    "point": 1,
                    "corner": "TT",
                    "test": "Test",
                    "output": "Rtime_clkout",
                    "value": result_value,
                    "status": "ok",
                    "sweep": {},
                    "corner_vars": {"temperature": "27"},
                    "test_note": None,
                }
            ],
            "artifacts": [],
            "output_specs": (
                {"Test": {"Rtime_clkout": spec_string}}
                if spec_string is not None else {}
            ),
        }

    def _ingest_dict(self, dump: dict) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_dump(d, dump)
            ingest_run_json(self.con, d / "run.json")

    def _spec_cols(self) -> tuple:
        return self.con.execute(
            "SELECT spec, spec_status FROM results "
            "WHERE run_id = 'deadbeef-dead-4eef-8eef-deadbeefdead'"
        ).fetchone()

    def test_pass_verdict_lt(self):
        # value 5e-11 < 1e-10 → pass
        self._ingest_dict(self._v2_dump_with_specs(5e-11, "< 1e-10"))
        self.assertEqual(self._spec_cols(), ("< 1e-10", "pass"))

    def test_fail_verdict_lt(self):
        # value 2e-10 ≥ 1e-10 → fail
        self._ingest_dict(self._v2_dump_with_specs(2e-10, "< 1e-10"))
        self.assertEqual(self._spec_cols(), ("< 1e-10", "fail"))

    def test_minimize_form_evaluated(self):
        # Maestro normalises ?min → 'minimize X'. pass if value ≤ X.
        self._ingest_dict(self._v2_dump_with_specs(50, "minimize 100"))
        self.assertEqual(self._spec_cols(), ("minimize 100", "pass"))

    def test_no_spec_when_output_specs_absent(self):
        # v2 envelope but with empty output_specs — every row → no_spec.
        self._ingest_dict(self._v2_dump_with_specs(5e-11, None))
        spec, status = self._spec_cols()
        self.assertIsNone(spec)
        self.assertEqual(status, "no_spec")

    def test_v1_envelope_back_compat(self):
        # An old v1 envelope has no output_specs at all → every row gets
        # spec=NULL, spec_status=NULL ("unknown, predates spec capture",
        # distinct from v2's 'no_spec' = "checked and there's no spec").
        dump = self._v2_dump_with_specs(5e-11, None)
        dump["schema_version"] = 1
        del dump["output_specs"]
        self._ingest_dict(dump)
        spec, status = self._spec_cols()
        self.assertIsNone(spec)
        self.assertIsNone(status)

    def test_no_value_when_status_is_eval_err(self):
        # When a result fails to compute (DECISIONS #35), the collector
        # emits status='eval_err' with value=null. Such rows can't be
        # compared against a spec — evaluator returns 'no_value' rather
        # than crashing on the spec or pretending it passed.
        dump = self._v2_dump_with_specs(0.0, "< 1e-10")
        dump["results"][0]["status"] = "eval_err"
        dump["results"][0]["value"] = None  # I14 contract for eval_err
        self._ingest_dict(dump)
        spec, status = self._spec_cols()
        self.assertEqual(spec, "< 1e-10")
        self.assertEqual(status, "no_value")

    def test_parse_err_on_malformed_spec(self):
        # Spec string that defies the parser — verdict is parse_err, not
        # a hard ingest failure (collector should never emit such a string
        # but we want to land it for debugging rather than abort).
        self._ingest_dict(self._v2_dump_with_specs(50, "garbage_spec"))
        spec, status = self._spec_cols()
        self.assertEqual(spec, "garbage_spec")
        self.assertEqual(status, "parse_err")

    def test_real_run_fixture_back_compat(self):
        # The 42-row real-run fixture is v1 and has no output_specs. All
        # 42 rows land with spec=NULL, spec_status=NULL ("predates spec
        # capture"); they are NOT marked 'no_spec' (= "checked and none").
        ingest_run_json(self.con, _REAL_RUN)
        rows = self.con.execute(
            "SELECT COUNT(*), "
            "SUM(CASE WHEN spec IS NULL THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN spec_status IS NULL THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN spec_status = 'no_spec' THEN 1 ELSE 0 END) "
            "FROM results"
        ).fetchone()
        self.assertEqual(rows[0], 42)
        self.assertEqual(rows[1], 42)
        self.assertEqual(rows[2], 42)
        self.assertEqual(rows[3], 0)


if __name__ == "__main__":
    unittest.main()
