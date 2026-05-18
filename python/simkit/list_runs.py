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
    # v1.4 — per-run spec verdict aggregate. Both default to 0 for runs
    # whose results table has no spec_status column (impossible after the
    # v2 migration) OR whose results carry only 'no_spec' verdicts. The
    # CLI shows "<passed>/<has_spec>" when has_spec > 0.
    n_pass: int = 0
    n_fail: int = 0
    n_has_spec: int = 0  # pass + fail + unsupported + parse_err + no_value
    # v1.8 #4 — user-flagged "keep this one forever" bit. Synced to
    # Maestro's axlSetHistoryLock via `pvt sync-stars`.
    starred: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def list_runs(
    con: duckdb.DuckDBPyConnection,
    *,
    project: Optional[str] = None,
    slice_only: bool = False,
    limit: Optional[int] = None,
    failed_only: bool = False,
    starred_only: bool = False,
) -> List[RunRow]:
    """Return runs from the DB, ordered by ``timestamp DESC``.

    Filters:

    * ``project``      — exact match on ``runs.project_id``.
    * ``slice_only``   — only ``label IS NOT NULL`` rows.
    * ``limit``        — top-N cutoff.
    * ``failed_only``  — only runs whose results contain at least one
      ``spec_status='fail'`` row. Useful for the v1.4 "which corners
      failed spec?" question. Implicit no-op against v1 data.
    * ``starred_only`` — only ``runs.starred = TRUE`` rows. v1.8 #4.
    """
    where: list[str] = []
    params: list[Any] = []
    if project is not None:
        where.append("project_id = ?")
        params.append(project)
    if slice_only:
        where.append("label IS NOT NULL")
    if failed_only:
        where.append(
            "EXISTS (SELECT 1 FROM results r "
            "WHERE r.run_id = runs.run_id AND r.spec_status = 'fail')"
        )
    if starred_only:
        where.append("starred = TRUE")

    # CAST the TIMESTAMPTZ columns to VARCHAR at the DuckDB layer so we
    # never trigger the pytz code path in the Python binding. DuckDB
    # emits a deterministic ISO-like string (e.g. "2026-05-12 06:30:00+08").
    #
    # v1.4 — JOIN spec-aggregate counts so the CLI table can show a
    # "<pass>/<has_spec>" column without a second round-trip. LEFT JOIN
    # because pre-v2 runs may have zero rows with spec_status set.
    sql = """
        SELECT
          runs.run_id, runs.project_id, runs.testbench_id, runs.testbench_alias,
          CAST(runs.timestamp AS VARCHAR), runs.author, runs.label, runs.note,
          runs.netlist_path, runs.history_name, runs.schema_version,
          CAST(runs.ingested_at AS VARCHAR),
          COALESCE(spec_agg.n_pass, 0),
          COALESCE(spec_agg.n_fail, 0),
          COALESCE(spec_agg.n_has_spec, 0),
          runs.starred
        FROM runs
        LEFT JOIN (
          SELECT
            run_id,
            SUM(CASE WHEN spec_status = 'pass' THEN 1 ELSE 0 END) AS n_pass,
            SUM(CASE WHEN spec_status = 'fail' THEN 1 ELSE 0 END) AS n_fail,
            SUM(CASE WHEN spec_status IS NOT NULL
                          AND spec_status <> 'no_spec'
                     THEN 1 ELSE 0 END) AS n_has_spec
          FROM results
          GROUP BY run_id
        ) spec_agg ON spec_agg.run_id = runs.run_id
    """
    if where:
        sql += "WHERE " + " AND ".join(where) + " "
    sql += "ORDER BY runs.timestamp DESC, runs.run_id "
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
        n_pass=int(r[12]),
        n_fail=int(r[13]),
        n_has_spec=int(r[14]),
        starred=bool(r[15]),
    )


def _isofmt(value: Any) -> str:
    """Stringify a DuckDB-returned timestamp value (datetime or str)."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
