"""Tests for the milestone-tagging write side (spec §15.2 / cap #6)."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "python"))


from simkit.db import connect  # noqa: E402
from simkit.errors import RunNotFoundError, SimkitError  # noqa: E402
from simkit.milestone import (  # noqa: E402
    MilestoneConflictError,
    set_run_milestone,
)


def _make_db_with_run(tmp_path: Path, *, run_id: str) -> Path:
    """Build a minimal DuckDB with a single row in ``runs``."""
    db = tmp_path / "m.duckdb"
    con = connect(db)
    try:
        con.execute(
            "CREATE TABLE runs ("
            "  run_id VARCHAR, project_id VARCHAR, "
            "  testbench_id VARCHAR, milestone VARCHAR DEFAULT NULL"
            ")"
        )
        con.execute(
            "INSERT INTO runs VALUES (?, 'p', 'tb', NULL)", [run_id]
        )
    finally:
        con.close()
    return db


def test_set_milestone_inserts_when_null(tmp_path):
    db = _make_db_with_run(tmp_path, run_id="r1")
    con = connect(db)
    try:
        result = set_run_milestone(con, run_id="r1", milestone="PDR")
    finally:
        con.close()
    assert result.action == "set"
    assert result.previous is None
    assert result.current == "PDR"

    con = connect(db, read_only=True)
    try:
        row = con.execute(
            "SELECT milestone FROM runs WHERE run_id='r1'"
        ).fetchone()
    finally:
        con.close()
    assert row[0] == "PDR"


def test_set_milestone_conflict_without_force(tmp_path):
    db = _make_db_with_run(tmp_path, run_id="r1")
    con = connect(db)
    try:
        set_run_milestone(con, run_id="r1", milestone="PDR")
        with pytest.raises(MilestoneConflictError):
            set_run_milestone(con, run_id="r1", milestone="CDR")
    finally:
        con.close()


def test_set_milestone_overwrites_with_force(tmp_path):
    db = _make_db_with_run(tmp_path, run_id="r1")
    con = connect(db)
    try:
        set_run_milestone(con, run_id="r1", milestone="PDR")
        result = set_run_milestone(
            con, run_id="r1", milestone="CDR", force=True,
        )
    finally:
        con.close()
    assert result.action == "overwritten"
    assert result.previous == "PDR"
    assert result.current == "CDR"


def test_set_milestone_clears(tmp_path):
    db = _make_db_with_run(tmp_path, run_id="r1")
    con = connect(db)
    try:
        set_run_milestone(con, run_id="r1", milestone="PDR")
        result = set_run_milestone(con, run_id="r1", milestone=None)
    finally:
        con.close()
    assert result.action == "cleared"
    assert result.previous == "PDR"
    assert result.current is None


def test_set_milestone_clear_when_already_null_is_noop(tmp_path):
    db = _make_db_with_run(tmp_path, run_id="r1")
    con = connect(db)
    try:
        result = set_run_milestone(con, run_id="r1", milestone=None)
    finally:
        con.close()
    assert result.action == "noop"


def test_set_milestone_same_value_is_noop(tmp_path):
    db = _make_db_with_run(tmp_path, run_id="r1")
    con = connect(db)
    try:
        set_run_milestone(con, run_id="r1", milestone="PDR")
        result = set_run_milestone(con, run_id="r1", milestone="PDR")
    finally:
        con.close()
    assert result.action == "noop"


def test_set_milestone_missing_run_id_raises(tmp_path):
    db = _make_db_with_run(tmp_path, run_id="r1")
    con = connect(db)
    try:
        with pytest.raises(RunNotFoundError):
            set_run_milestone(con, run_id="nope", milestone="PDR")
    finally:
        con.close()


def test_set_milestone_rejects_empty_string(tmp_path):
    db = _make_db_with_run(tmp_path, run_id="r1")
    con = connect(db)
    try:
        with pytest.raises(SimkitError):
            set_run_milestone(con, run_id="r1", milestone="   ")
    finally:
        con.close()


def test_set_milestone_rejects_too_long(tmp_path):
    db = _make_db_with_run(tmp_path, run_id="r1")
    con = connect(db)
    try:
        with pytest.raises(SimkitError):
            set_run_milestone(con, run_id="r1", milestone="x" * 65)
    finally:
        con.close()


def test_set_milestone_rejects_control_chars(tmp_path):
    db = _make_db_with_run(tmp_path, run_id="r1")
    con = connect(db)
    try:
        with pytest.raises(SimkitError):
            set_run_milestone(con, run_id="r1", milestone="bad\x01value")
    finally:
        con.close()


def test_set_milestone_strips_whitespace(tmp_path):
    db = _make_db_with_run(tmp_path, run_id="r1")
    con = connect(db)
    try:
        result = set_run_milestone(con, run_id="r1", milestone="  PDR  ")
    finally:
        con.close()
    assert result.current == "PDR"
