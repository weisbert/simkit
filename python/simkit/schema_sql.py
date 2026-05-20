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
- v1.8 #4 (DECISIONS #65): added ``runs.starred`` (BOOLEAN DEFAULT FALSE) so
  the user can mark a run as a permanent reference. The DB flag is also
  synced to Maestro's ``axlSetHistoryLock`` via ``pvt sync-stars`` so the
  GUI history entry is protected from deletion.
- Phase 4 §9a / §15.2: added ``runs.milestone`` (VARCHAR DEFAULT NULL)
  for free-string Design-Review tagging (``PDR`` / ``CDR`` / ``FDR`` /
  ``ECO_1`` / …), and ``runs.partial_run`` (BOOLEAN DEFAULT FALSE) for
  cancel-mid-run tagging (§9.3). Both additive; existing rows get the
  DEFAULTs.
"""

from __future__ import annotations


# v1.8 #4 bump: schema_version 2 → 3. Migration in ``simkit.db.bootstrap``
# adds the ``runs.starred`` column when an existing v2 DB is opened.
# Phase 4 §9a bump: schema_version 3 → 4. Migration adds
# ``runs.milestone`` (VARCHAR, free-string DR tag; §15.2) and
# ``runs.partial_run`` (BOOLEAN, cancel-mid-run flag; §9.3).
# G-5 bump: schema_version 4 → 5. Migration adds ``runs.provenance``
# (VARCHAR holding a JSON object: host / captured_at / pdk_version /
# model_files) so a signoff number can be proved against the conditions
# it was produced under (FDR-5, E-3, E-5).
DB_SCHEMA_VERSION = 5


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
  ingested_at     TIMESTAMPTZ NOT NULL,
  starred         BOOLEAN DEFAULT FALSE,
  milestone       VARCHAR DEFAULT NULL,
  partial_run     BOOLEAN DEFAULT FALSE,
  provenance      VARCHAR DEFAULT NULL
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
# v1.8 #4 — migration steps for v2 → v3. The column is nullable in the DDL
# (DuckDB's `ALTER TABLE ADD COLUMN` does not support NOT NULL constraints
# alongside DEFAULT — "Adding columns with constraints not yet supported"),
# so we rely on the DEFAULT FALSE to backfill existing rows and on Python's
# bool() to coerce any future NULL read to False. New inserts that omit the
# column also pick up the DEFAULT.
V3_MIGRATION_DDL = (
    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS starred BOOLEAN DEFAULT FALSE",
)
# Phase 4 §9a — migration steps for v3 → v4. Same DuckDB constraint as v3:
# ``ALTER TABLE ADD COLUMN`` does not combine NOT NULL with DEFAULT, so the
# new columns are nullable in DDL and we rely on the DEFAULT to backfill
# existing rows plus Python-side coercion at read time. ``milestone`` is
# semantically nullable anyway (NULL = no DR tag). ``partial_run`` is
# coerced via ``bool()`` so a NULL read maps to ``False``.
V4_MIGRATION_DDL = (
    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS milestone VARCHAR DEFAULT NULL",
    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS partial_run BOOLEAN DEFAULT FALSE",
)
# G-5 — migration steps for v4 → v5. ``provenance`` is a JSON object
# serialised to a VARCHAR (host / captured_at / pdk_version /
# model_files). Nullable: NULL = a run ingested before provenance
# capture, or a manual PvtSave that bypassed the orchestrator.
V5_MIGRATION_DDL = (
    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS provenance VARCHAR DEFAULT NULL",
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
