"""Reconstruct a JSON-dump-shaped dict from rows already in DuckDB.

Used by ``pvt validate --from-db``: the §3(d) invariant checker
works on the JSON dump shape (``docs/schema.md`` §2), so to audit a
run already in the DB we rebuild the same dict from the three tables
and feed it to :func:`simkit.validate.validate_dump`.

Loss-of-information caveats:

* ``value`` is reconstructed from ``(value_num, value_str)``. If both
  are NULL we yield ``None``. This matches the ingester's
  :func:`simkit.ingest._split_value` round-trip.
* ``schema_version`` is taken from ``runs.schema_version`` (per
  DECISIONS #19: stored per-run in the DB).
* ``corner_vars`` and ``sweep`` are stored as JSON columns in DuckDB
  and decoded back to Python dicts here.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

import duckdb

from simkit.errors import RunNotFoundError


def load_dump_from_db(
    con: duckdb.DuckDBPyConnection, run_id: str,
) -> Dict[str, Any]:
    """Rebuild a JSON-dump-shaped dict for ``run_id``.

    Raises :class:`RunNotFoundError` if no ``runs`` row matches.
    """
    run = _load_run(con, run_id)
    return {
        "schema_version": run.pop("_schema_version"),
        "run": run,
        "results": _load_results(con, run_id),
        "artifacts": _load_artifacts(con, run_id),
    }


def _load_run(
    con: duckdb.DuckDBPyConnection, run_id: str,
) -> Dict[str, Any]:
    row = con.execute(
        """
        SELECT
          run_id, project_id, testbench_id, testbench_alias,
          CAST(timestamp AS VARCHAR), author, label, note,
          netlist_path, history_name, schema_version
        FROM runs
        WHERE run_id = ?
        """,
        [run_id],
    ).fetchone()
    if row is None:
        raise RunNotFoundError(f"run_id {run_id!r} not found in DB")
    return {
        "run_id": row[0],
        "project_id": row[1],
        "testbench_id": row[2],
        "testbench_alias": row[3],
        "timestamp": _normalize_iso(row[4]),
        "author": row[5],
        "label": row[6],
        "note": row[7],
        "netlist_path": row[8],
        "history_name": row[9],
        "_schema_version": int(row[10]),
    }


def _load_results(
    con: duckdb.DuckDBPyConnection, run_id: str,
) -> List[Dict[str, Any]]:
    rows = con.execute(
        """
        SELECT
          point, corner, test, output,
          value_num, value_str, status,
          sweep, corner_vars, test_note
        FROM results
        WHERE run_id = ?
        ORDER BY point, corner, test, output
        """,
        [run_id],
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append({
            "point": int(r[0]),
            "corner": r[1],
            "test": r[2],
            "output": r[3],
            "value": _merge_value(r[4], r[5]),
            "status": r[6],
            "sweep": _decode_json(r[7]),
            "corner_vars": _decode_json(r[8]),
            "test_note": r[9],
        })
    return out


def _load_artifacts(
    con: duckdb.DuckDBPyConnection, run_id: str,
) -> List[Dict[str, Any]]:
    rows = con.execute(
        """
        SELECT
          type, relative_path, description, source,
          CAST(created_at AS VARCHAR)
        FROM artifacts
        WHERE run_id = ?
        ORDER BY relative_path
        """,
        [run_id],
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append({
            "type": r[0],
            "relative_path": r[1],
            "description": r[2],
            "source": r[3],
            "created_at": _normalize_iso(r[4]),
        })
    return out


def _merge_value(value_num: Any, value_str: Any) -> Any:
    if value_num is not None:
        return float(value_num)
    if value_str is not None:
        return value_str
    return None


def _decode_json(value: Any) -> Any:
    if value is None:
        return {}
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def _normalize_iso(s: Optional[str]) -> Optional[str]:
    """Convert a DuckDB-stringified TIMESTAMPTZ to a strict ISO 8601 form.

    DuckDB renders TIMESTAMPTZ as ``YYYY-MM-DD hh:mm:ss±hh`` (space
    separator, hour-only offset). The validator's I6 expects ``T`` and
    a colonised offset, so we round-trip through :func:`datetime.fromisoformat`
    (3.11+, permissive) and re-emit via :meth:`datetime.isoformat`.
    """
    if s is None or s == "":
        return s
    try:
        dt = datetime.fromisoformat(s.replace(" ", "T", 1))
    except ValueError:
        return s
    return dt.isoformat()
