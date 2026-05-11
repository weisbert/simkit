"""Run-listing queries: the data layer behind ``pvt list``.

Pure DuckDB query module. Returns a list of :class:`RunRow` records;
formatting decisions (pretty table vs JSON) live in the CLI layer.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import duckdb


@dataclass(frozen=True)
class RunRow:
    run_id: str
    project_id: str
    testbench_id: str
    testbench_alias: Optional[str]
    timestamp: str  # ISO-formatted by the query layer
    author: str
    label: Optional[str]
    note: Optional[str]
    netlist_path: Optional[str]
    history_name: str
    schema_version: int
    ingested_at: str  # ISO-formatted

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def list_runs(
    con: duckdb.DuckDBPyConnection,
    *,
    project: Optional[str] = None,
    slice_only: bool = False,
    limit: Optional[int] = None,
) -> List[RunRow]:
    """Return runs from the DB, ordered by ``timestamp DESC``.

    Filters:

    * ``project``    — exact match on ``runs.project_id``.
    * ``slice_only`` — only ``label IS NOT NULL`` rows.
    * ``limit``      — top-N cutoff.
    """
    where: list[str] = []
    params: list[Any] = []
    if project is not None:
        where.append("project_id = ?")
        params.append(project)
    if slice_only:
        where.append("label IS NOT NULL")

    # CAST the TIMESTAMPTZ columns to VARCHAR at the DuckDB layer so we
    # never trigger the pytz code path in the Python binding. DuckDB
    # emits a deterministic ISO-like string (e.g. "2026-05-12 06:30:00+08").
    sql = """
        SELECT
          run_id, project_id, testbench_id, testbench_alias,
          CAST(timestamp AS VARCHAR), author, label, note,
          netlist_path, history_name, schema_version,
          CAST(ingested_at AS VARCHAR)
        FROM runs
    """
    if where:
        sql += "WHERE " + " AND ".join(where) + " "
    sql += "ORDER BY timestamp DESC, run_id "
    if limit is not None:
        sql += f" LIMIT {int(limit)}"

    rows = con.execute(sql, params).fetchall()
    return [_row_to_runrow(r) for r in rows]


def _row_to_runrow(r: tuple) -> RunRow:
    return RunRow(
        run_id=r[0],
        project_id=r[1],
        testbench_id=r[2],
        testbench_alias=r[3],
        timestamp=_isofmt(r[4]),
        author=r[5],
        label=r[6],
        note=r[7],
        netlist_path=r[8],
        history_name=r[9],
        schema_version=int(r[10]),
        ingested_at=_isofmt(r[11]),
    )


def _isofmt(value: Any) -> str:
    """Stringify a DuckDB-returned timestamp value (datetime or str)."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
