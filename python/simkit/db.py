"""DuckDB connection management for simkit.

Pure DB plumbing — knows nothing about JSON dump shape. Three primitives:

- :func:`connect` — open a DuckDB file or in-memory DB.
- :func:`bootstrap` — idempotent CREATE TABLE / CREATE INDEX for every
  v1 table; seeds the ``simkit_meta`` row for ``db_schema_version``.
- :func:`transaction` — context manager that wraps ``BEGIN`` / ``COMMIT`` /
  ``ROLLBACK`` around a block of DuckDB calls.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Union

import duckdb

from simkit.schema_sql import (
    ALL_DDL,
    ALL_INDEXES,
    DB_SCHEMA_VERSION,
    V2_MIGRATION_DDL,
    V3_MIGRATION_DDL,
    V4_MIGRATION_DDL,
    V5_MIGRATION_DDL,
)


_DbPath = Union[Path, str]


def connect(db_path: _DbPath, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open a DuckDB connection.

    ``db_path`` may be:

    * the literal string ``":memory:"`` (or ``Path(":memory:")``) for an
      in-memory DB used by tests, or
    * a filesystem path. Parent directory must already exist; we do not
      auto-mkdir to avoid masking caller mistakes.

    ``read_only=True`` is supported for inspection tools; the ingester uses
    the default writable mode.
    """
    if isinstance(db_path, Path):
        if str(db_path) == ":memory:":
            target = ":memory:"
        else:
            target = str(db_path)
    else:
        target = str(db_path)
    return duckdb.connect(target, read_only=read_only)


def bootstrap(con: duckdb.DuckDBPyConnection) -> None:
    """Create every simkit table and index, then seed/migrate ``simkit_meta``.

    Idempotent: safe to call on a fresh DB, a v1 DB needing migration, or
    one already at the current version.

    Migrations:

    * v1 → v2 (DECISIONS #46/#47): adds ``results.spec`` and
      ``results.spec_status`` columns (both nullable VARCHAR). Existing
      rows get NULL spec / NULL spec_status; queries treat NULL spec as
      ``'no_spec'``. The DB_SCHEMA_VERSION row is updated to ``'2'``.
    * v2 → v3 (DECISIONS #65): adds ``runs.starred`` (BOOLEAN NOT NULL
      DEFAULT FALSE). Existing rows are backfilled to FALSE; the user
      promotes a run to "starred" via ``pvt star`` and that flag is then
      pushed to Maestro's ``axlSetHistoryLock``.
    * v3 → v4 (Phase 4 §9a / §15.2): adds ``runs.milestone`` (VARCHAR,
      DEFAULT NULL — free-string Design-Review tag) and
      ``runs.partial_run`` (BOOLEAN, DEFAULT FALSE — cancel-mid-run
      flag). Existing rows pick up the DEFAULTs.
    * v4 → v5 (G-5): adds ``runs.provenance`` (VARCHAR, DEFAULT NULL —
      a JSON object recording host / captured_at / pdk_version /
      model-file fingerprints). Existing rows stay NULL.
    """
    for stmt in ALL_DDL:
        con.execute(stmt)
    for stmt in ALL_INDEXES:
        con.execute(stmt)
    # Read current DB-side version, default to v1 for pre-meta databases.
    row = con.execute(
        "SELECT value FROM simkit_meta WHERE key = 'db_schema_version'"
    ).fetchone()
    if row is None:
        # Fresh bootstrap: seed at the current version. No migration needed.
        con.execute(
            "INSERT INTO simkit_meta(key, value) VALUES (?, ?)",
            ["db_schema_version", str(DB_SCHEMA_VERSION)],
        )
        return
    current = int(row[0])
    if current == DB_SCHEMA_VERSION:
        return
    if current > DB_SCHEMA_VERSION:
        # Forward-incompatible — refuse rather than corrupt. Caller (CLI)
        # surfaces this as a user-facing "upgrade simkit" message.
        raise RuntimeError(
            f"DB schema_version={current} is newer than this simkit "
            f"build supports (max {DB_SCHEMA_VERSION}). Upgrade simkit, "
            f"or open with a newer build."
        )
    # Apply migrations in order; each step is idempotent (uses
    # ADD COLUMN IF NOT EXISTS) so re-running on a partially-migrated DB
    # is safe.
    if current < 2:
        for stmt in V2_MIGRATION_DDL:
            con.execute(stmt)
    if current < 3:
        for stmt in V3_MIGRATION_DDL:
            con.execute(stmt)
    if current < 4:
        for stmt in V4_MIGRATION_DDL:
            con.execute(stmt)
    if current < 5:
        for stmt in V5_MIGRATION_DDL:
            con.execute(stmt)
    con.execute(
        "UPDATE simkit_meta SET value = ? WHERE key = 'db_schema_version'",
        [str(DB_SCHEMA_VERSION)],
    )


@contextmanager
def transaction(con: duckdb.DuckDBPyConnection) -> Iterator[duckdb.DuckDBPyConnection]:
    """Wrap a DuckDB block in BEGIN / COMMIT / ROLLBACK.

    Usage::

        with transaction(con):
            con.execute(...)
            con.execute(...)

    Any exception inside the block triggers ROLLBACK and re-raises. Normal
    exit triggers COMMIT.
    """
    con.execute("BEGIN")
    try:
        yield con
    except BaseException:
        con.execute("ROLLBACK")
        raise
    else:
        con.execute("COMMIT")
