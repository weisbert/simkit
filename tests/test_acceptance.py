"""Phase 1 §6 end-to-end acceptance tests.

Exercises the four user-facing acceptance gates from TODO.md §6 against
pinned fixtures captured from a live Maestro `simkit_verify` run on
2026-05-12. The fixtures live under ``tests/fixtures/acceptance/`` and
both pass ``pvt validate`` cleanly.

Gate map:

1. ``GateOne_SaveIngestQuery`` — ingest run_a's run.json into a fresh
   DuckDB and query basic shape + metadata.
2. ``GateTwo_WorstCaseAcrossCorners`` — TT-family worst-case queries
   over the 7 corners in the fixture (Rtime_clkout maximum,
   PN_100M maximum).
3. ``GateThree_NetlistDiffBetweenSlices`` — label both runs, run
   ``pvt diff`` via the CLI, assert the manual C0 capacitor edit and
   the per-row +1% delta show up.
4. ``GateFour_PostHocAttachAndRetrieve`` — attach the dummy PNG to
   run_a via ``pvt attach``, query the artifacts table, confirm the
   file landed under ``<dbRoot>/runs/<run_id>/artifacts/``.

Each gate is one TestCase class with multiple assertions.
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
from simkit.ingest import ingest_run_json  # noqa: E402
from simkit.label import set_run_label  # noqa: E402


_FX = _REPO_ROOT / "tests" / "fixtures" / "acceptance"
_RUN_A_JSON = _FX / "run_a" / "run.json"
_RUN_B_JSON = _FX / "run_b" / "run.json"
_RUN_A_SCS = _FX / "run_a" / "input.scs"
_RUN_B_SCS = _FX / "run_b" / "input.scs"
_SCREENSHOT = _FX / "screenshot.png"

_RUN_A_ID = "ace00000-0000-4000-8000-000000000001"
_RUN_B_ID = "ace00000-0000-4000-8000-000000000002"


def _cli(*args: str) -> tuple:
    """Run pvt CLI with stdout/stderr captured."""
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = cli_main(list(args))
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 2
    return rc, out.getvalue(), err.getvalue()


def _make_dbroot(tmp: Path) -> Path:
    """Lay out a usable <dbRoot> with the fixture run dirs copied in.

    Mirrors the disk layout the collector would have written: each
    fixture's run.json + input.scs live under ``<dbRoot>/runs/<run_id>/``
    so that ``pvt attach`` and the netlist-diff path resolve correctly.
    """
    db_root = tmp / "dbRoot"
    (db_root / "runs" / _RUN_A_ID).mkdir(parents=True)
    (db_root / "runs" / _RUN_B_ID).mkdir(parents=True)
    shutil.copy2(_RUN_A_JSON, db_root / "runs" / _RUN_A_ID / "run.json")
    shutil.copy2(_RUN_A_SCS, db_root / "runs" / _RUN_A_ID / "input.scs")
    shutil.copy2(_RUN_B_JSON, db_root / "runs" / _RUN_B_ID / "run.json")
    shutil.copy2(_RUN_B_SCS, db_root / "runs" / _RUN_B_ID / "input.scs")
    return db_root


# ---------------------------------------------------------------------------
# Gate 1 — PvtSave → ingest → Python query
# ---------------------------------------------------------------------------

class GateOne_SaveIngestQuery(unittest.TestCase):
    """End-to-end loop: collector dump → ingester → DB-side queries."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="acc_gate1_"))
        self.db_root = _make_dbroot(self.tmp)
        self.db = self.db_root / "simkit.duckdb"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ingest_real_dump_creates_runs_row(self):
        rc, out, err = _cli(
            "ingest", str(self.db_root / "runs" / _RUN_A_ID / "run.json"),
            "--db", str(self.db),
        )
        self.assertEqual(rc, 0, f"err={err}")
        self.assertIn("inserted", out)

    def test_basic_metadata_round_trip(self):
        _cli(
            "ingest", str(self.db_root / "runs" / _RUN_A_ID / "run.json"),
            "--db", str(self.db),
        )
        con = connect(self.db, read_only=True)
        try:
            row = con.execute(
                "SELECT project_id, testbench_id, netlist_path, history_name, "
                "  schema_version "
                "FROM runs WHERE run_id = ?",
                [_RUN_A_ID],
            ).fetchone()
        finally:
            con.close()
        self.assertEqual(row[0], "netlist_fix_smoke")
        self.assertEqual(row[2], "input.scs")  # netlist_path populated
        self.assertEqual(row[4], 1)

    def test_result_row_count_and_status_breakdown(self):
        _cli(
            "ingest", str(self.db_root / "runs" / _RUN_A_ID / "run.json"),
            "--db", str(self.db),
        )
        con = connect(self.db, read_only=True)
        try:
            total = con.execute(
                "SELECT COUNT(*) FROM results WHERE run_id = ?",
                [_RUN_A_ID],
            ).fetchone()[0]
            n_ok = con.execute(
                "SELECT COUNT(*) FROM results WHERE run_id = ? AND status='ok'",
                [_RUN_A_ID],
            ).fetchone()[0]
        finally:
            con.close()
        # The 2026-05-10 reference + the 2026-05-12 re-run both had
        # 42 rows; all converged (status='ok').
        self.assertEqual(total, 42)
        self.assertEqual(n_ok, 42)


# ---------------------------------------------------------------------------
# Gate 2 — TT worst-case query across corners
# ---------------------------------------------------------------------------

class GateTwo_WorstCaseAcrossCorners(unittest.TestCase):
    """Cross-corner aggregations: the kind of SQL an IC engineer
    actually runs after a sweep finishes."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="acc_gate2_"))
        self.db_root = _make_dbroot(self.tmp)
        self.db = self.db_root / "simkit.duckdb"
        _cli(
            "ingest", str(self.db_root / "runs" / _RUN_A_ID / "run.json"),
            "--db", str(self.db),
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_seven_corners_present_for_Rtime(self):
        con = connect(self.db, read_only=True)
        try:
            corners = con.execute(
                "SELECT DISTINCT corner FROM results "
                "WHERE run_id = ? AND output = 'Rtime_clkout'"
                "ORDER BY corner",
                [_RUN_A_ID],
            ).fetchall()
        finally:
            con.close()
        self.assertEqual(len(corners), 7)

    def test_worst_case_rise_time_picks_a_real_corner(self):
        """Across the 7 corners, the slowest Rtime_clkout = MAX(value_num)."""
        con = connect(self.db, read_only=True)
        try:
            row = con.execute(
                "SELECT corner, value_num FROM results "
                "WHERE run_id = ? AND output = 'Rtime_clkout' "
                "  AND status = 'ok' "
                "ORDER BY value_num DESC LIMIT 1",
                [_RUN_A_ID],
            ).fetchone()
        finally:
            con.close()
        self.assertIsNotNone(row)
        worst_corner, worst_value = row
        # The fixture corners are TT plus six TT_pvt_N sweep points.
        self.assertTrue(
            worst_corner == "TT" or worst_corner.startswith("TT_pvt_"),
            f"unexpected worst-case corner: {worst_corner!r}",
        )
        # Rtime is on the order of ~1e-11 s; sanity-check the magnitude.
        self.assertGreater(worst_value, 0.0)
        self.assertLess(worst_value, 1e-9)

    def test_worst_phase_noise_at_100M(self):
        """Phase noise: less-negative = worse suppression."""
        con = connect(self.db, read_only=True)
        try:
            row = con.execute(
                "SELECT corner, value_num FROM results "
                "WHERE run_id = ? AND output = 'PN_100M' AND status='ok' "
                "ORDER BY value_num DESC LIMIT 1",
                [_RUN_A_ID],
            ).fetchone()
        finally:
            con.close()
        self.assertIsNotNone(row)
        worst_corner, worst_value = row
        # Phase noise at 100M is in the ballpark of -174 dBc/Hz in the
        # fixture; "worst" stays in the same order of magnitude.
        self.assertLess(worst_value, -100.0)
        self.assertGreater(worst_value, -200.0)

    def test_per_corner_aggregation_returns_one_row_per_corner(self):
        """The shape a 'compliance table' query would use."""
        con = connect(self.db, read_only=True)
        try:
            rows = con.execute(
                "SELECT corner, MIN(value_num), MAX(value_num) "
                "FROM results "
                "WHERE run_id = ? AND status = 'ok' "
                "  AND output = 'Rtime_clkout' "
                "GROUP BY corner",
                [_RUN_A_ID],
            ).fetchall()
        finally:
            con.close()
        # 7 corners × 1 point each = 7 GROUP BY rows.
        self.assertEqual(len(rows), 7)
        for corner, vmin, vmax in rows:
            # One value per (corner, point) ⇒ MIN == MAX.
            self.assertEqual(vmin, vmax)


# ---------------------------------------------------------------------------
# Gate 3 — netlist diff between two slices with a known manual change
# ---------------------------------------------------------------------------

class GateThree_NetlistDiffBetweenSlices(unittest.TestCase):
    """The slice-diff loop end-to-end. Manual change: run_b's input.scs
    has a single-line C0 capacitor edit (10f → 12f, plus a comment),
    and every row's value is +1% vs run_a so the results table also
    moves."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="acc_gate3_"))
        self.db_root = _make_dbroot(self.tmp)
        self.db = self.db_root / "simkit.duckdb"
        _cli(
            "ingest", str(self.db_root / "runs" / _RUN_A_ID / "run.json"),
            "--db", str(self.db),
        )
        _cli(
            "ingest", str(self.db_root / "runs" / _RUN_B_ID / "run.json"),
            "--db", str(self.db),
        )
        # Promote both to named slices so the diff invocation uses labels.
        con = connect(self.db)
        try:
            set_run_label(con, run_id=_RUN_A_ID, label="baseline")
            set_run_label(con, run_id=_RUN_B_ID, label="c0_bumped")
        finally:
            con.close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_diff_shows_manual_netlist_edit(self):
        rc, out, err = _cli(
            "diff", "baseline", "c0_bumped", "--db", str(self.db),
        )
        self.assertEqual(rc, 0, f"err={err}")
        # The unified diff section must include the changed line in
        # both the '-' and '+' forms.
        self.assertIn("-C0 (Vout AGND) capacitor c=10f", out)
        self.assertIn("+C0 (Vout AGND) capacitor c=12f", out)
        self.assertIn("edited for acceptance fixture", out)

    def test_diff_results_table_carries_numeric_deltas(self):
        rc, out, err = _cli(
            "diff", "baseline", "c0_bumped", "--db", str(self.db),
        )
        self.assertEqual(rc, 0, f"err={err}")
        # Every numeric row in run_b is +1% vs run_a; the table should
        # therefore show a +1.00% rel delta on at least one row.
        self.assertIn("+1.00%", out)
        # And the header still labels the columns.
        for header in ("test", "corner", "value_a", "value_b", "dRel"):
            self.assertIn(header, out)

    def test_diff_json_payload_round_trips(self):
        rc, out, err = _cli(
            "diff", "baseline", "c0_bumped", "--json",
            "--db", str(self.db),
        )
        self.assertEqual(rc, 0, f"err={err}")
        payload = json.loads(out)
        self.assertEqual(payload["slice_a"]["run_id"], _RUN_A_ID)
        self.assertEqual(payload["slice_b"]["run_id"], _RUN_B_ID)
        self.assertEqual(len(payload["results"]), 42)
        self.assertIsNotNone(payload["netlist"]["diff_text"])
        # All result rows are 'match' kind (no only_a / only_b / status_mismatch).
        kinds = {r["kind"] for r in payload["results"]}
        self.assertEqual(kinds, {"match"})


# ---------------------------------------------------------------------------
# Gate 4 — attach a screenshot post-hoc and retrieve it
# ---------------------------------------------------------------------------

class GateFour_PostHocAttachAndRetrieve(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="acc_gate4_"))
        self.db_root = _make_dbroot(self.tmp)
        self.db = self.db_root / "simkit.duckdb"
        _cli(
            "ingest", str(self.db_root / "runs" / _RUN_A_ID / "run.json"),
            "--db", str(self.db),
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_attach_screenshot_then_query(self):
        rc, out, err = _cli(
            "attach", _RUN_A_ID, str(_SCREENSHOT),
            "--type", "image",
            "--desc", "post-hoc smoke screenshot",
            "--db", str(self.db),
        )
        self.assertEqual(rc, 0, f"err={err}")

        # Row written, source='manual', desc carried.
        con = connect(self.db, read_only=True)
        try:
            row = con.execute(
                "SELECT type, relative_path, description, source "
                "FROM artifacts WHERE run_id = ?",
                [_RUN_A_ID],
            ).fetchone()
        finally:
            con.close()
        self.assertEqual(row[0], "image")
        self.assertEqual(row[1], "artifacts/screenshot.png")
        self.assertEqual(row[2], "post-hoc smoke screenshot")
        self.assertEqual(row[3], "manual")

        # File copied onto disk under the run dir.
        copied = (
            self.db_root / "runs" / _RUN_A_ID
            / "artifacts" / "screenshot.png"
        )
        self.assertTrue(copied.is_file())
        self.assertEqual(copied.read_bytes(), _SCREENSHOT.read_bytes())

    def test_attach_then_list_via_cli(self):
        """End-to-end: attach, then `pvt list` shows the run; the
        artifact is reachable via a follow-up query."""
        _cli(
            "attach", _RUN_A_ID, str(_SCREENSHOT),
            "--type", "image", "--db", str(self.db),
        )
        rc, out, err = _cli(
            "list", "--json", "--db", str(self.db),
        )
        self.assertEqual(rc, 0, f"err={err}")
        data = json.loads(out)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["run_id"], _RUN_A_ID)


if __name__ == "__main__":
    unittest.main()
