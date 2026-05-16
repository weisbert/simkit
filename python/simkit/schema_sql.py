"""DuckDB DDL constants for the simkit data layer.

Single source of truth for the schema. Pure string constants — no
``duckdb`` import — so unit tests can assert DDL text equality without
spinning up a connection.

See ``docs/schema.md`` §3 for column documentation. Three deviations from
the spec:

- ``runs.netlist_path`` is nullable here. Spec declares NOT NULL; collector
  emits null on the §3 Spectre-detection soft-miss. Validator emits ``W2``
  warning when null. (DECISIONS #18.)
- ``simkit_meta`` table is added (key/value) for tracking the DB-side
  schema_version. Not part of the public schema spec. (DECISIONS #19.)
- v1.4 (DECISIONS #46/#47): added ``results.spec`` (Cadence-native string
  captured from ``axlOutputsExportToFile``) + ``results.spec_status`` (enum
  computed at ingest time by :mod:`simkit.spec_eval`). Both nullable; old
  v1 envelopes ingest as ``spec=NULL, spec_status='no_spec'``.
"""

from __future__ import annotations


# v1.4 bump: schema_version 1 → 2. Migration in ``simkit.db.bootstrap``
# adds the two new columns when an existing v1 DB is opened.
DB_SCHEMA_VERSION = 2


RUNS_DDL = """
CREATE TABLE IF NOT EXISTS runs (
  run_id          VARCHAR PRIMARY KEY,
  project_id      VARCHAR NOT NULL,
  testbench_id    VARCHAR NOT NULL,
  testbench_alias VARCHAR,
  timestamp       TIMESTAMPTZ NOT NULL,
  author          VARCHAR NOT NULL,
  label           VARCHAR,
  note            VARCHAR,
  netlist_path    VARCHAR,
  history_name    VARCHAR NOT NULL,
  schema_version  INTEGER NOT NULL,
  ingested_at     TIMESTAMPTZ NOT NULL
)
""".strip()


RESULTS_DDL = """
CREATE TABLE IF NOT EXISTS results (
  run_id      VARCHAR NOT NULL,
  point       INTEGER NOT NULL,
  corner      VARCHAR NOT NULL,
  test        VARCHAR NOT NULL,
  output      VARCHAR NOT NULL,
  value_num   DOUBLE,
  value_str   VARCHAR,
  status      VARCHAR NOT NULL,
  sweep       JSON NOT NULL,
  corner_vars JSON NOT NULL,
  test_note   VARCHAR,
  spec        VARCHAR,
  spec_status VARCHAR
)
""".strip()


# v1.4 — migration steps for already-bootstrapped v1 databases. Executed by
# ``simkit.db.bootstrap`` when ``simkit_meta.db_schema_version='1'``. Each
# step is a single ALTER; ordering matters only insofar as repeated bootstrap
# on an already-migrated DB must be idempotent — which DuckDB's
# ``ADD COLUMN IF NOT EXISTS`` (≥0.9) handles cleanly.
V2_MIGRATION_DDL = (
    "ALTER TABLE results ADD COLUMN IF NOT EXISTS spec VARCHAR",
    "ALTER TABLE results ADD COLUMN IF NOT EXISTS spec_status VARCHAR",
)
# NOTE: spec writes ``run_id ... REFERENCES runs(run_id)`` but DuckDB
# enforces FKs per-statement (no within-transaction relaxation), so a
# delete-then-reinsert ``replace`` flow against an FK-protected child
# table errors at the parent DELETE because the prior child DELETE in
# the same tx isn't yet visible. Application layer (ingester +
# validator) enforces the integrity. See DECISIONS #21.


RESULTS_INDEXES = (
    "CREATE INDEX IF NOT EXISTS results_run_id_idx ON results(run_id)",
    "CREATE INDEX IF NOT EXISTS results_proj_corner_idx "
    "ON results(run_id, corner, test, output)",
)


ARTIFACTS_DDL = """
CREATE TABLE IF NOT EXISTS artifacts (
  run_id        VARCHAR NOT NULL,
  type          VARCHAR NOT NULL,
  relative_path VARCHAR NOT NULL,
  description   VARCHAR,
  source        VARCHAR NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL
)
""".strip()
# Same FK note as results — see RESULTS_DDL.


ARTIFACTS_INDEXES = (
    "CREATE INDEX IF NOT EXISTS artifacts_run_id_idx ON artifacts(run_id)",
)


SIMKIT_META_DDL = """
CREATE TABLE IF NOT EXISTS simkit_meta (
  key   VARCHAR PRIMARY KEY,
  value VARCHAR NOT NULL
)
""".strip()


# Ordered; runs first because results / artifacts FK to it.
ALL_DDL = (
    RUNS_DDL,
    RESULTS_DDL,
    ARTIFACTS_DDL,
    SIMKIT_META_DDL,
)


ALL_INDEXES = RESULTS_INDEXES + ARTIFACTS_INDEXES


# Public table names — used by tests and the bootstrap idempotency check.
TABLE_NAMES = ("runs", "results", "artifacts", "simkit_meta")
