"""Unit tests for :mod:`simkit.gui.run_summary` (G-3 margin + G-4 health).

Pure DuckDB — no Qt, no skillbridge. An in-memory DB is bootstrapped via
:func:`simkit.db.bootstrap` and loaded with a small mixed fixture.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.db import bootstrap, connect  # noqa: E402
from simkit.gui.run_summary import (  # noqa: E402
    margin_rollup,
    run_health,
)


def _con():
    """In-memory DB, run 'R1' (partial), mixed result rows.

    Real outputs: PN (spec, 1 pass + 1 fail corner), gain (no spec),
    nf (spec but value missing → no_value). Plus one __sim_status__
    sentinel row marking a failed corner.
    """
    con = connect(":memory:")
    bootstrap(con)
    con.execute(
        """
        INSERT INTO runs(
          run_id, project_id, testbench_id, timestamp,
          author, history_name, schema_version, ingested_at
        ) VALUES
          ('R1', 'projA', 'tbA', TIMESTAMPTZ '2026-05-19 12:00:00+00',
           'tester', 'h1', 4, TIMESTAMPTZ '2026-05-19 12:00:00+00')
        """
    )
    con.execute("UPDATE runs SET partial_run = TRUE WHERE run_id = 'R1'")
    con.execute(
        """
        INSERT INTO results(
          run_id, point, corner, test, output,
          value_num, value_str, status, sweep, corner_vars,
          test_note, spec, spec_status
        ) VALUES
          ('R1', 0, 'TT', 't', 'PN',
           25.0, NULL, 'ok', '{}', '{}', NULL, '>= 20', 'pass'),
          ('R1', 0, 'SS', 't', 'PN',
           15.0, NULL, 'ok', '{}', '{}', NULL, '>= 20', 'fail'),
          ('R1', 0, 'TT', 't', 'gain',
           5.0, NULL, 'ok', '{}', '{}', NULL, NULL, 'no_spec'),
          ('R1', 0, 'TT', 't', 'nf',
           NULL, NULL, 'eval_err', '{}', '{}', NULL, '<= 3', 'no_value'),
          ('R1', 0, 'FF', 't', '__sim_status__',
           NULL, NULL, 'failed', '{}', '{}', NULL, NULL, NULL)
        """
    )
    return con


# --- run_health (G-4) ----------------------------------------------------


def test_run_health_counts_exclude_sim_sentinel():
    con = _con()
    try:
        h = run_health(con, "R1")
    finally:
        con.close()
    assert h.total_rows == 4  # PN×2, gain, nf — not __sim_status__
    assert h.status_counts == {"ok": 3, "eval_err": 1}


def test_run_health_surfaces_sim_failures_and_partial_flag():
    con = _con()
    try:
        h = run_health(con, "R1")
    finally:
        con.close()
    assert h.sim_fail_corners == 1  # corner FF
    assert h.partial_run is True
    assert h.clean is False


def test_run_health_clean_for_an_all_ok_run():
    con = connect(":memory:")
    bootstrap(con)
    con.execute(
        """
        INSERT INTO runs(
          run_id, project_id, testbench_id, timestamp,
          author, history_name, schema_version, ingested_at
        ) VALUES
          ('OK', 'p', 't', TIMESTAMPTZ '2026-05-19 12:00:00+00',
           'a', 'h', 4, TIMESTAMPTZ '2026-05-19 12:00:00+00')
        """
    )
    con.execute(
        """
        INSERT INTO results(
          run_id, point, corner, test, output,
          value_num, value_str, status, sweep, corner_vars,
          test_note, spec, spec_status
        ) VALUES
          ('OK', 0, 'TT', 't', 'PN',
           1.0, NULL, 'ok', '{}', '{}', NULL, '>= 0', 'pass')
        """
    )
    try:
        h = run_health(con, "OK")
    finally:
        con.close()
    assert h.clean is True


# --- margin_rollup (G-3) -------------------------------------------------


def test_margin_rollup_one_entry_per_output_sorted():
    con = _con()
    try:
        rollup = margin_rollup(con, "R1")
    finally:
        con.close()
    assert [r.output for r in rollup] == ["PN", "gain", "nf"]


def test_margin_rollup_worst_corner_is_most_violating():
    con = _con()
    try:
        rollup = margin_rollup(con, "R1")
    finally:
        con.close()
    pn = next(r for r in rollup if r.output == "PN")
    # >= 20: TT(25)→+5, SS(15)→-5; worst is SS.
    assert pn.worst_corner == "SS"
    assert pn.worst_value == 15.0
    assert pn.margin == -5.0
    assert pn.verdict == "fail"
    assert pn.n_corners == 2


def test_margin_rollup_no_spec_output_listed_with_no_spec_verdict():
    con = _con()
    try:
        rollup = margin_rollup(con, "R1")
    finally:
        con.close()
    gain = next(r for r in rollup if r.output == "gain")
    assert gain.spec is None
    assert gain.verdict == "no_spec"
    assert gain.margin is None


def test_margin_rollup_missing_value_yields_no_value_verdict():
    con = _con()
    try:
        rollup = margin_rollup(con, "R1")
    finally:
        con.close()
    nf = next(r for r in rollup if r.output == "nf")
    assert nf.spec == "<= 3"
    assert nf.margin is None  # value was NULL
    assert nf.verdict == "no_value"
    assert nf.worst_corner == "TT"  # still points at a concrete corner
