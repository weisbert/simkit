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
    """Create every simkit table and index, then seed ``simkit_meta``.

    Idempotent: safe to call on a fresh DB or one already initialised.
    Seeds ``('db_schema_version', '1')`` if the row is absent. Does not
    overwrite an existing row (forward-compat: a future migration will be
    explicit about updating the version).
    """
    for stmt in ALL_DDL:
        con.execute(stmt)
    for stmt in ALL_INDEXES:
        con.execute(stmt)
    # Seed the schema_version meta row only if absent.
    row = con.execute(
        "SELECT value FROM simkit_meta WHERE key = 'db_schema_version'"
    ).fetchone()
    if row is None:
        con.execute(
            "INSERT INTO simkit_meta(key, value) VALUES (?, ?)",
            ["db_schema_version", str(DB_SCHEMA_VERSION)],
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
