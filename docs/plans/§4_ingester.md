# Phase 1 §4 — Python Ingester Plan

_Returned by Plan agent on 2026-05-10. Verbatim. See `CONTRACT.md` for director-level reconciliation with `§3_messy_data.md`._

## 0. Discrepancies / ambiguities found in spec (must be resolved before coding)

These are concrete places where `docs/schema.md` does not pin a single answer; the plan picks one in each case and flags it for sign-off.

1. **`netlist_path` nullability mismatch.** `docs/schema.md` §2.1 lists `netlist_path` as type `str` with no `| null` (i.e. required), and §3 declares `runs.netlist_path VARCHAR NOT NULL`. But the May-10 real-run fixture (`/tmp/pvt_smoke_db/runs/bdc13f17.../run.json`) carries `"netlist_path": null` because the Spectre-detection bug from §3 leaves it unresolved, and `PROJECT_STATE.md` explicitly calls this out as the soft-miss path that "is acceptable for §4 ingester development." **Plan resolution:** make the DDL `netlist_path VARCHAR` (nullable) for Phase 1; add a TODO note in `DECISIONS.md` (future entry) tying tightening back to NOT-NULL to §3 item-2 "Spectre detection" closure plus a §6 acceptance gate. Reasoning: shipping an ingester that hard-rejects every existing collector dump is dead on arrival.
2. **`runs` PK / artifact uniqueness.** Spec says `runs.run_id` is the PK and explicitly says `artifacts` has no PK; "(run_id, relative_path) is the natural uniqueness pair; enforce at ingest time." We do enforce it at ingest. Not ambiguous, just calling out that we do not declare a UNIQUE constraint at the DDL level.
3. **`results` PK.** Spec declares no PK on `results`. Implicit natural key is `(run_id, point, corner, test, output)`. We do **not** create a UNIQUE on this; the ingester deletes-then-inserts per `run_id` (see §3 below), so within a single run we trust the collector. Tradeoff: allows duplicate rows from a buggy collector to slip in. Mitigated by the validator (see §10).
4. **`results.value` for non-ok rows.** Spec says "`null` when `status != ok`". So both `value_num` and `value_str` are NULL whenever `status != "ok"`. We enforce this at ingest as a hard error, not a silent fix.
5. **`status` enum.** Spec: closed set `{ok, failed, running, no_convergence}`. Anything else is a hard error at ingest. The §3 TODO (line 37) notes the SKILL fallback might emit `"unknown"` for a nil-status row — if/when that lands, it's a §3 spec change, not an ingester change.
6. **`results.point` is `int` in JSON, `INTEGER` in DDL.** Spec is fine. We assert non-null and `>= 0` at ingest.
7. **`timestamp` and `created_at` ISO 8601 with offset → `TIMESTAMPTZ`.** DuckDB `TIMESTAMPTZ` accepts ISO 8601 strings with offset directly via `CAST(s AS TIMESTAMPTZ)`. We use that, not `datetime.fromisoformat` round-tripping.
8. **JSON dump `schema_version` vs `.pvtproject schema_version`.** Spec §5 says they share `1` in v1 but are separate version axes. The ingester only consumes JSON-dump `schema_version`. The `.pvtproject` version is the loader's concern (already handled in `project.py`). The ingester does **not** load `.pvtproject` itself — it works from a pure dump-dir argument (see §3, §7 below) so it's testable in isolation and runnable without project context.

---

## 1. File layout

New files under `python/simkit/`:

```
python/simkit/
  __init__.py            (UPDATE: re-export ingest API)
  project.py             (existing, untouched)
  db.py                  (NEW: connection management, DDL bootstrap)
  schema_sql.py          (NEW: DDL constants — single source of truth)
  ingest.py              (NEW: dump-dir walker + JSON → DB upsert)
  errors.py              (NEW: shared exception base + ingest-specific)
  cli/
    __init__.py          (NEW)
    __main__.py          (NEW: `python -m simkit.cli`)
    ingest.py            (NEW: `pvt ingest` argparse subcommand)
```

Module boundaries (rationale):

- **`schema_sql.py`** — string constants only: `RUNS_DDL`, `RESULTS_DDL`, `ARTIFACTS_DDL`, `SCHEMA_META_DDL`, plus a tuple `ALL_DDL` for ordered creation. No imports of `duckdb`. Lets tests assert DDL text equality without spinning up a connection. Also makes future schema_version=2 a diff at one location.
- **`db.py`** — owns `connect(db_path: Path) -> duckdb.DuckDBPyConnection`, `bootstrap(con) -> None` (idempotent CREATE TABLE IF NOT EXISTS for all tables + a `simkit_meta` table, see §2 below), and a `transaction(con)` context manager that wraps `BEGIN`/`COMMIT`/`ROLLBACK`. Pure DB plumbing. No knowledge of dump JSON shape.
- **`ingest.py`** — the work: walk a dump dir, load each `run.json`, validate its shape and `schema_version`, write rows. Knows nothing about argparse. Public API is functional, not class-based, to match `project.py` style.
- **`errors.py`** — `SimkitError` base; `IngestError` and subclasses (`SchemaVersionError`, `DuplicateRunError`, `MalformedDumpError`, `MissingDumpError`). Same shape as `PvtProjectError` hierarchy in `project.py`.
- **`cli/`** — package, not a module, so §5's full CLI (`attach`, `label`, `list`, `diff`) can each be its own file as it lands. `__main__.py` dispatches subcommands. `cli/ingest.py` only owns `pvt ingest`.

`python/simkit/__init__.py` re-exports: `ingest_dump_dir`, `ingest_run_json`, `IngestError`, `SchemaVersionError`, `DuplicateRunError`, `MalformedDumpError`, `connect`, `bootstrap` — alongside the existing `PvtProject*` names.

---

## 2. DuckDB schema (concrete DDL)

Verbatim — these go into `schema_sql.py`:

```sql
-- runs
CREATE TABLE IF NOT EXISTS runs (
  run_id          VARCHAR PRIMARY KEY,
  project_id      VARCHAR NOT NULL,
  testbench_id    VARCHAR NOT NULL,
  testbench_alias VARCHAR,
  timestamp       TIMESTAMPTZ NOT NULL,
  author          VARCHAR NOT NULL,
  label           VARCHAR,
  note            VARCHAR,
  netlist_path    VARCHAR,                  -- spec says NOT NULL but
                                            -- §3 soft-miss leaves null;
                                            -- see §0 item 1.
  history_name    VARCHAR NOT NULL,
  schema_version  INTEGER NOT NULL,
  ingested_at     TIMESTAMPTZ NOT NULL
);

-- results
CREATE TABLE IF NOT EXISTS results (
  run_id      VARCHAR NOT NULL REFERENCES runs(run_id),
  point       INTEGER NOT NULL,
  corner      VARCHAR NOT NULL,
  test        VARCHAR NOT NULL,
  output      VARCHAR NOT NULL,
  value_num   DOUBLE,
  value_str   VARCHAR,
  status      VARCHAR NOT NULL,
  sweep       JSON NOT NULL,
  corner_vars JSON NOT NULL,
  test_note   VARCHAR
);
CREATE INDEX IF NOT EXISTS results_run_id_idx ON results(run_id);
CREATE INDEX IF NOT EXISTS results_proj_corner_idx
  ON results(run_id, corner, test, output);

-- artifacts
CREATE TABLE IF NOT EXISTS artifacts (
  run_id        VARCHAR NOT NULL REFERENCES runs(run_id),
  type          VARCHAR NOT NULL,
  relative_path VARCHAR NOT NULL,
  description   VARCHAR,
  source        VARCHAR NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS artifacts_run_id_idx ON artifacts(run_id);

-- meta — internal bookkeeping; not part of public schema spec
CREATE TABLE IF NOT EXISTS simkit_meta (
  key   VARCHAR PRIMARY KEY,
  value VARCHAR NOT NULL
);
-- on first bootstrap, insert ('db_schema_version', '1') if absent.
```

**Notes on the choices, mapped to spec ambiguities:**

- Spec §3 says "Indexes and slice views. Deferred to the ingester/query-layer task." We're the ingester, so we declare them. The two indexes above cover (a) every per-run delete/select the ingester does and (b) the `pvt diff` / TT worst-case query patterns from §6 which always filter by run_id then group by corner/test/output. Keep these — they're cheap, DuckDB doesn't need many.
- `simkit_meta` is **not** in `docs/schema.md`. Justification: schema_version evolution needs a place to record which DB-side schema version the file is on. Without it, the only signal is "do the columns exist?" — fine for v1, painful for v2. Adding a meta table now is a one-row commitment that pays off the first time the DDL changes. I'll suggest noting this in `DECISIONS.md` as a new entry when the code lands.
- `REFERENCES runs(run_id)` — DuckDB does enforce foreign keys; the ordered DDL (runs first) plus our transactional ingest order (insert runs row first, then children) keep this safe.
- No `UNIQUE(run_id, point, corner, test, output)` on results — see §0 item 3.

---

## 3. Ingest algorithm

Two entry points (in `ingest.py`):

```python
def ingest_run_json(
    con: duckdb.DuckDBPyConnection,
    run_json_path: Path,
    *,
    on_conflict: Literal["error", "skip", "replace"] = "error",
) -> IngestResult: ...

def ingest_dump_dir(
    con: duckdb.DuckDBPyConnection,
    dump_dir: Path,
    *,
    on_conflict: Literal["error", "skip", "replace"] = "error",
) -> list[IngestResult]: ...
```

`IngestResult` is a small frozen dataclass: `run_id: str`, `action: Literal["inserted", "skipped", "replaced"]`, `n_results: int`, `n_artifacts: int`, `source_path: Path`.

### `ingest_dump_dir` (the walker)

1. Resolve `dump_dir`. If it doesn't exist or isn't a directory → `MissingDumpError`.
2. Two acceptable shapes (both supported because §6 will exercise the second):
   - `dump_dir/run.json` — single-run convenience (matches the §3 fixture layout `<dbRoot>/runs/<run_id>/run.json` when caller points at `<run_id>` dir).
   - `dump_dir/runs/<run_id>/run.json` — full dbRoot convention (caller points at `dbRoot`).
3. Find candidate files: glob `dump_dir/run.json` first; if zero hits, glob `dump_dir/runs/*/run.json`. Sort lexicographically (deterministic test output).
4. For each `run.json`: call `ingest_run_json(con, p, on_conflict=...)`, accumulate results. On any error, the per-file transaction (see below) has already rolled back; we re-raise unless caller passed `--continue-on-error` (see §7).

### `ingest_run_json` (the worker — one transaction per run)

1. **Read + JSON-decode.** `json.load(open(...))`. JSON errors → `MalformedDumpError(path, exc)`.
2. **Top-level shape check.** Must be `dict` with keys `schema_version` (int), `run` (dict), `results` (list), `artifacts` (list). Missing or wrong-typed → `MalformedDumpError`.
3. **`schema_version` dispatch.** See §5 below. v1 path: continue. Unknown major: `SchemaVersionError`. Unknown minor (none in v1, present-tense): warn-and-continue.
4. **Validate `run` block.** Required keys per `docs/schema.md` §2.1: `run_id`, `project_id`, `testbench_id`, `testbench_alias` (nullable), `timestamp`, `author`, `label` (nullable), `note` (nullable), `netlist_path` (treated as nullable per §0 item 1), `history_name`. UUIDv4 regex on `run_id` (loose: hex-and-dashes, length 36 — collector spec but we don't strictly enforce v4 bits). ISO-8601-with-offset regex on `timestamp`.
5. **Validate `results` rows.** For each row: required keys per spec §2.2; `status` ∈ closed set; `value` ↔ `status` invariant (when `status == "ok"`, `value` is number-or-string; when `status != "ok"`, `value` must be `null`); split `value` into `value_num` / `value_str`; `sweep` and `corner_vars` are dicts; `output == "__sim_status__"` rows must have `value is None` and one of the non-ok statuses (per §2.2 sentinel description). Each violation is a `MalformedDumpError` carrying row index and offending field.
6. **Validate `artifacts` rows.** `type` ∈ closed set, `source` ∈ `{auto, manual}`, `created_at` ISO-8601-with-offset. `relative_path` non-empty string.
7. **BEGIN TRANSACTION** on `con`.
8. **Conflict check.** `SELECT 1 FROM runs WHERE run_id = ?`. If hit:
   - `on_conflict="error"` → `DuplicateRunError(run_id, source_path)`. Rollback. Re-raise.
   - `on_conflict="skip"` → rollback (nothing was written), return `IngestResult(action="skipped", ...)`.
   - `on_conflict="replace"` → `DELETE FROM artifacts WHERE run_id = ?`, `DELETE FROM results WHERE run_id = ?`, `DELETE FROM runs WHERE run_id = ?`. Continue to insert path.
9. **Insert `runs` row.** Single parameterised `INSERT ... VALUES (?, ?, ...)`. `ingested_at = now()` computed Python-side as `datetime.now(timezone.utc).isoformat()` so tests can monkeypatch.
10. **Insert `results` rows.** Bulk: `con.executemany("INSERT INTO results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)`. JSON columns are passed as `json.dumps(...)` strings — DuckDB's JSON column accepts that.
11. **Insert `artifacts` rows.** Same pattern. Skip if list is empty.
12. **COMMIT.** Return `IngestResult(action="inserted" or "replaced", ...)`.

Any exception between BEGIN and COMMIT → ROLLBACK in a `finally` (or via the `transaction(con)` context manager). The DB never sees a half-loaded run.

**Why per-file transactions, not one big transaction:** lets `ingest_dump_dir` succeed partially (load 9 of 10 valid dumps, surface the 10th as a hard error) while keeping each loaded run atomic. Tradeoff: a hostile caller could observe a half-walked dir if they read mid-run. Acceptable for an offline single-user tool.

---

## 4. Idempotency decision

**Default: error on duplicate `run_id`. Override via `--force` (CLI) / `on_conflict="replace"` (API). No silent skip path is exposed by default; `on_conflict="skip"` is supported in the API for batch tooling but not surfaced as a CLI flag in §4 (could be added in §5 if a real use case arises).**

Justification, framed by how the user actually re-ingests:

- **Re-ingest after collector bugfix (the May-10 funobj scenario writ large).** User notices the collector lost data; fixes SKILL; re-runs `PvtSave`. The collector generates a **new `run_id`** (UUIDv4 per dump per spec §2.1), so this is not actually a duplicate-run-id case. No problem.
- **Re-ingest a stale dump** — operator manually re-runs `pvt ingest <path>` on a dir that's already been ingested (e.g. unsure whether last ingest succeeded; recovering from a crash). Default `error` keeps them honest: "this was already loaded; if you want to overwrite, pass `--force`." This is the most common case and a silent-skip would mask a "did anything actually change?" question that matters during debugging.
- **Re-ingest from corrupt DB** — operator wants the DB rebuilt from the JSON archive. They `rm simkit.duckdb` first (the dump dir is canonical, the DB is derived). No conflict scenario.
- **`--force` semantics** — full delete-then-insert for that `run_id`. Critically: this preserves the run's UUID, so any post-hoc `pvt label` or `pvt attach` already done against that `run_id` is destroyed. **The CLI `--force` flag must warn loudly** (see §7); in batch contexts the API caller is expected to know what they're doing.

Why not "silent no-op": the user can't easily distinguish a no-op from a successful change in DuckDB without running an extra query. The hard error forces an explicit decision and matches `project.py`'s existing "fail fast" stance for the env-var path.

Why not "always replace": destroys downstream state (labels, attached artifacts) without warning.

---

## 5. `schema_version` handling

Current is `1`. Policy:

```
JSON dump schema_version  →  ingester behaviour
==================================================================
1                         →  v1 path (current)
2, 3, …                   →  hard-fail with SchemaVersionError listing
                             supported versions and pointing at the
                             ingester version. UPGRADE THE INGESTER, not
                             the dump.
< 1, 0, negative, non-int →  MalformedDumpError
missing key               →  MalformedDumpError ("schema_version
                             required, see docs/schema.md §2")
```

No "minor" version axis in v1. If we ever introduce one (e.g. `1.1` for an additive field), the policy will be: accept higher minors silently if all keys we know are present and well-typed; reject lower minors. **This is a forward-looking note, not v1 behaviour** — v1 only sees integer `1`.

Concretely in code:

```python
_INGESTER_SUPPORTED_DUMP_VERSIONS = frozenset({1})
```

Same shape as `_SUPPORTED_SCHEMA_VERSIONS` in `project.py:22`. Symmetry across the two loaders is intentional — same code style means same review cost.

**Forward-compat hook for additive JSON fields within v1.** Spec §5: "Additive JSON changes (new optional field the ingester can tolerate as missing) are allowed within v1." We honour this by being **strict on required fields, lenient on unknown fields** — extra keys in `run`, `results[i]`, `artifacts[i]` are silently ignored (no warning; we're a downstream consumer, not a linter — the validator (§10) is where lint warnings live).

---

## 6. Public API

In `python/simkit/ingest.py`:

```python
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional
import duckdb

from simkit.errors import (
    IngestError, MalformedDumpError, SchemaVersionError,
    DuplicateRunError, MissingDumpError,
)


_INGESTER_SUPPORTED_DUMP_VERSIONS = frozenset({1})
_VALID_STATUSES = frozenset({"ok", "failed", "running", "no_convergence"})
_VALID_ARTIFACT_TYPES = frozenset({
    "waveform", "results_table", "sim_log", "schematic",
    "netlist_diff", "image", "pdf", "other",
})
_VALID_ARTIFACT_SOURCES = frozenset({"auto", "manual"})


@dataclass(frozen=True)
class IngestResult:
    run_id: str
    action: Literal["inserted", "skipped", "replaced"]
    n_results: int
    n_artifacts: int
    source_path: Path


def ingest_run_json(
    con: duckdb.DuckDBPyConnection,
    run_json_path: Path,
    *,
    on_conflict: Literal["error", "skip", "replace"] = "error",
    now: Optional[callable] = None,   # injectable for tests
) -> IngestResult:
    """Load a single run.json into the DB. Atomic per call."""


def ingest_dump_dir(
    con: duckdb.DuckDBPyConnection,
    dump_dir: Path,
    *,
    on_conflict: Literal["error", "skip", "replace"] = "error",
    continue_on_error: bool = False,
    now: Optional[callable] = None,
) -> list[IngestResult]:
    """Walk dump_dir for run.json files; ingest each."""
```

In `python/simkit/db.py`:

```python
def connect(db_path: Path, *, read_only: bool = False) -> duckdb.DuckDBPyConnection: ...
def bootstrap(con: duckdb.DuckDBPyConnection) -> None: ...
def transaction(con: duckdb.DuckDBPyConnection): ...   # context manager
```

In `python/simkit/__init__.py`, re-exports added:

```python
from simkit.ingest import (
    IngestResult, ingest_run_json, ingest_dump_dir,
)
from simkit.db import connect, bootstrap
from simkit.errors import (
    IngestError, MalformedDumpError, SchemaVersionError,
    DuplicateRunError, MissingDumpError,
)
```

(Existing `PvtProject*` names stay.)

---

## 7. CLI surface — `pvt ingest`

`pvt` is the entry point name (per `python/README.md`). For Phase 1 §4 we only build the `ingest` subcommand. The dispatcher lives in `cli/__main__.py` and is structured so `attach`/`label`/`list`/`diff` (§5) bolt on without churn.

### Argparse skeleton

```python
# python/simkit/cli/__main__.py
import argparse, sys
from simkit.cli import ingest as ingest_cmd

def main(argv=None):
    parser = argparse.ArgumentParser(prog="pvt", description="simkit CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ingest_cmd.add_subparser(sub)
    args = parser.parse_args(argv)
    return args.func(args)

if __name__ == "__main__":
    sys.exit(main())
```

```python
# python/simkit/cli/ingest.py
def add_subparser(sub):
    p = sub.add_parser(
        "ingest",
        help="Load collector dump JSON into DuckDB.",
        description="Walk <path> for run.json files and load each into <dbRoot>/simkit.duckdb.",
    )
    p.add_argument("path", type=Path,
        help="Either a dbRoot dir (containing runs/<run_id>/run.json), "
             "a single run dir (containing run.json), or a run.json file.")
    p.add_argument("--db", type=Path, default=None,
        help="Override DB path. Default: <dbRoot>/simkit.duckdb derived from "
             "the .pvtproject discovered via PVT_PROJECT or cwd-walker.")
    p.add_argument("--force", action="store_true",
        help="Replace existing rows for any run_id already in the DB. "
             "WARNING: destroys post-hoc labels and attached artifacts for "
             "those run_ids.")
    p.add_argument("--continue-on-error", action="store_true",
        help="Skip malformed dumps instead of aborting the whole walk.")
    p.add_argument("-v", "--verbose", action="count", default=0)
    p.set_defaults(func=run)


def run(args) -> int:
    # 1. Resolve DB path: --db wins; else load_pvtproject() to get db_root.
    # 2. db.connect(); db.bootstrap().
    # 3. Determine if path is run.json, run-dir, or dbRoot:
    #    - file ending /run.json → ingest_run_json()
    #    - dir containing run.json → ingest_run_json on dir/run.json
    #    - dir containing runs/ → ingest_dump_dir()
    # 4. on_conflict = "replace" if args.force else "error".
    # 5. Print summary table: action, run_id, n_results, n_artifacts, source.
    # 6. Exit 0 on full success, 1 on any IngestError.
```

Exit codes: `0` success, `1` ingest error (malformed / version / duplicate without `--force`), `2` argparse / usage error (default), `3` filesystem / DB IO error.

`pvt --help` shape (with subcommand) matches what §5 will extend.

---

## 8. Test strategy

Test framework: stdlib `unittest`, in line with `tests/test_project_loader.py`. Run command (already documented in `tests/README.md`): `PYTHONPATH=python python3.11 -m unittest discover -s tests -v`.

### New test files

```
tests/
  test_project_loader.py       (existing)
  test_db.py                   (NEW)
  test_ingest.py               (NEW)
  test_cli_ingest.py           (NEW)
  fixtures/
    runs/
      bdc13f17-d39b-4a13-b58e-846435996a29/run.json   (the 42-row real run)
      synthetic_minimal/run.json                       (1 result, no artifacts)
      synthetic_messy/run.json                         (mix of statuses)
      synthetic_with_artifacts/run.json                (1 auto artifact)
      bad_version/run.json                             (schema_version: 2)
      bad_status/run.json                              (status: "potato")
      bad_value_when_failed/run.json                   (status: failed, value: 42)
      malformed_json/run.json                          (truncated text)
```

The 42-row real fixture is **copied from** `/tmp/pvt_smoke_db/runs/bdc13f17-d39b-4a13-b58e-846435996a29/run.json` into `tests/fixtures/runs/bdc13f17-...`. Synthetic ones are hand-written from the spec.

### Concrete test names

`test_db.py`:

- `BootstrapTests.test_bootstrap_creates_all_tables`
- `BootstrapTests.test_bootstrap_is_idempotent`
- `BootstrapTests.test_bootstrap_writes_meta_version_1`
- `TransactionTests.test_commit_on_success`
- `TransactionTests.test_rollback_on_exception`

`test_ingest.py`:

- `RealRunFixtureTests.test_loads_42_row_real_run` — load `bdc13f17-...`; assert `runs` has 1 row matching expected fields, `results` has 42 rows, `artifacts` 0. Spot-check: row with `corner='TT_pvt_4', output='Rtime_clkout'` has `value_num == 1.91397e-11`.
- `RealRunFixtureTests.test_real_run_corner_vars_round_trip` — assert `corner_vars` JSON parses back to `{"temperature": "55", "model": "ff", "VDD": "3"}` for the expected row.
- `RealRunFixtureTests.test_real_run_netlist_path_null_loads` — explicit guard for §0 item 1; current fixture has `null` and must load.
- `IdempotencyTests.test_duplicate_run_id_errors_by_default`
- `IdempotencyTests.test_duplicate_run_id_replace_overwrites`
- `IdempotencyTests.test_duplicate_run_id_skip_returns_skipped_action`
- `IdempotencyTests.test_replace_does_not_affect_other_runs` — load A, load B, replace A, B's rows still there.
- `SchemaVersionTests.test_schema_version_2_rejected`
- `SchemaVersionTests.test_schema_version_missing_rejected`
- `SchemaVersionTests.test_schema_version_string_rejected`
- `SchemaVersionTests.test_schema_version_zero_rejected`
- `MalformedTests.test_truncated_json_raises_malformed`
- `MalformedTests.test_results_must_be_list`
- `MalformedTests.test_run_missing_required_field`
- `MalformedTests.test_status_outside_enum_rejected`
- `MalformedTests.test_value_set_when_status_failed_rejected`
- `MalformedTests.test_value_null_when_status_ok_rejected`
- `MalformedTests.test_artifact_type_outside_enum_rejected`
- `MalformedTests.test_artifact_source_outside_enum_rejected`
- `WalkerTests.test_walks_dbroot_layout` — `runs/<id>/run.json` × N.
- `WalkerTests.test_walks_single_run_dir`
- `WalkerTests.test_walks_zero_runs_returns_empty_list`
- `WalkerTests.test_continue_on_error_collects_results_and_failures`
- `PartialDumpTests.test_empty_results_array_loads` — collector emitted run with 0 result rows (the pre-fix 2026-05-10 case). Should load; `runs` has 1 row, `results` has 0.
- `PartialDumpTests.test_only_artifacts_no_results` — same shape but with 1 artifact.
- `TransactionTests.test_failure_mid_results_rolls_back_run` — feed a fixture where row 5 has bad status; assert no rows in `runs`, `results`, `artifacts` for that `run_id` after.

`test_cli_ingest.py`:

- `CliTests.test_pvt_ingest_real_fixture_exit_zero`
- `CliTests.test_pvt_ingest_duplicate_exits_one_without_force`
- `CliTests.test_pvt_ingest_force_replaces`
- `CliTests.test_pvt_ingest_continue_on_error`
- `CliTests.test_pvt_ingest_resolves_db_from_pvtproject`

In-memory DuckDB (`duckdb.connect(":memory:")`) for unit tests. `tempfile.TemporaryDirectory()` for tests that need an on-disk DB or `<dbRoot>` walker.

---

## 9. Forward compatibility — §5 (`pvt diff`) and §6 (TT worst-case across corners)

The two §6 acceptance queries this schema must serve cleanly:

**TT worst-case across corners (Phase 1 §6 item 2):**

```sql
SELECT corner, MIN(value_num) AS worst
FROM   results r
JOIN   runs    u ON r.run_id = u.run_id
WHERE  u.label = 'tt_baseline'
  AND  r.test = ?
  AND  r.output = ?
  AND  r.output <> '__sim_status__'
  AND  r.status = 'ok'
GROUP BY corner
ORDER BY worst;
```

Hits `runs.label`, `results.run_id`+`results.corner`+`results.test`+`results.output`+`results.status`. The composite index `(run_id, corner, test, output)` covers it. No schema changes needed.

**Slice diff (`pvt diff <slice_a> <slice_b>` — §5):**

```sql
WITH a AS (SELECT * FROM results WHERE run_id = (SELECT run_id FROM runs WHERE label = ?)),
     b AS (SELECT * FROM results WHERE run_id = (SELECT run_id FROM runs WHERE label = ?))
SELECT COALESCE(a.corner, b.corner) AS corner, ...
       a.value_num AS a_val, b.value_num AS b_val,
       (b.value_num - a.value_num) AS delta
FROM   a
FULL OUTER JOIN b
  ON   a.point=b.point AND a.corner=b.corner AND a.test=b.test AND a.output=b.output;
```

Works directly on the v1 schema. The `(point, corner, test, output)` natural join key is exactly the implicit row key we don't enforce as PK. Two follow-on notes for §5:

- **Netlist diff** (also §5) is filesystem-side: `runs.netlist_path` is the relative path to `input.scs` under the run dir. `pvt diff` will read the two files directly. No DB schema involvement.
- **Slice resolution by label.** `runs.label` is currently `VARCHAR` (no UNIQUE). Spec §3 footnote: "non-NULL = slice (retained permanently)." Two runs sharing a label is undefined. **Suggestion (Phase 1 §5):** when `pvt label` lands, enforce uniqueness app-side, with a friendly error if user tries to label two runs the same. Don't add a DDL UNIQUE constraint in §4 — that paints us into a corner if §6 surfaces a "label history" requirement (e.g. an old `tt_baseline` retired).

**Things in this schema that could paint §5/§6 into a corner — flagged:**

1. **`results.value` split into `value_num`/`value_str`.** Aggregations (`MIN`, `MAX`, `AVG`) are clean for numeric. A future "categorical worst-case" query (e.g. mode of pass/fail strings) needs different SQL; not blocked, just a different shape. Acceptable.
2. **`sweep` and `corner_vars` as JSON columns.** Filtering by `corner_vars.temperature = 55` requires `json_extract`. Workable in DuckDB but slower than dedicated columns. **Risk:** if §6 acceptance queries need to filter on temperature/VDD/process_corner specifically, JSON-extract overhead on 100k+ row tables may force a lateral split into `corner_vars_temperature` etc. Phase 1 row counts (a single sim is hundreds to low thousands of rows) make this a non-issue; flagging for Phase 2 ingest scaling.
3. **`testbench_id` / `testbench_alias` only on `runs`.** A query like "TT worst-case across all heavy-TB runs in the last week" requires the join to `runs` we already wrote. Fine.
4. **No `timestamp` on results.** `results` does not carry per-row timestamps; only `runs.timestamp`. If a future per-output rerun is loaded as one run row but with mixed-time results, this loses fidelity. Spec §2.2 doesn't allow this anyway, so non-issue.

---

## 10. Coordination notes — ingester ↔ validator (`python/simkit/validate.py`)

The validator is a separate effort (TODO §3 item, option (d) — "Python-side schema validator"). Recommended contract:

### Module layout — they are independent peers, both consumed by the CLI

```
ingest.py    → "load this dump into DB, fail loudly if shape-broken"
validate.py  → "audit this dump (or DB row set) for invariants
                that go beyond shape — return a list of violations
                with row pointers"
```

The ingester does **not** call the validator inline. Why:

- Different failure modes. The ingester rejects bad shape (status outside enum). The validator catches *consistency* problems (e.g. test/corner/point combos missing both ok and `__sim_status__` rows) that aren't shape-malformed and shouldn't block load. Coupling them forces one to drive the other; better to let `pvt validate <path>` and `pvt ingest <path>` be separately invokable.
- Re-running the validator on already-ingested data is valuable. If the ingester called validate inline, post-hoc validation against a long-loaded run would require re-decoding the original JSON.

> **DIRECTOR OVERRIDE (see CONTRACT.md C1):** the implementer DOES wire the validator inline by default in `ingest_run_json`, with `--no-validate` as the off-switch. Validator stays standalone-invocable. This reconciles with Plan-B §4.3.

### How they coexist in practice

- `pvt ingest <path>` does shape validation + load. Exits non-zero on shape errors. Does **not** invoke `validate`. _(See CONTRACT.md override — DOES invoke by default.)_
- `pvt validate <path>` (a §5 sibling) loads the JSON shape, then runs the validator's invariant checks, prints violations, exits non-zero on any.
- `pvt validate --from-db <run_id>` runs the same invariants against rows already in the DB. (Validator handles both sources behind one API.)

### Invariants the ingester relies on (and so the validator MUST guarantee for ingest-correctness)

These are the "safety net" items the ingester does **not** itself check, because catching them is the validator's job:

1. **Sentinel-row exclusivity per (run_id, point, corner, test).** For each tuple there is **either** ≥1 `ok` row across `output`s **or** exactly one row with `output='__sim_status__'` and a non-ok `status`, **never both, never neither**. (The TODO calls this out explicitly.)
2. **Per-output coverage within a converged test.** If a test is `'done` (all `ok` rows), every output expected by the testbench appears at least once. The ingester can't know which outputs are expected; the validator can cross-reference against, e.g., the testbench's expression list pulled from a `.pvtproject` extension or just the union of outputs across corners.
3. **Status / value union.** Already enforced as shape by ingester; validator can catch a duplicate "status=ok but value=null" if a future schema change relaxes shape.
4. **No duplicate (run_id, point, corner, test, output)** — the ingester does not enforce uniqueness (§0 item 3); validator should flag duplicates with row indices.
5. **Corner sets uniform across points.** Within one run, every point should have the same set of corners. Drift indicates a collector bug (e.g. funobj-in-loop returning nil mid-walk → §3 history).
6. **`corner_vars` consistency.** Same `corner` value across different rows must carry the same `corner_vars` dict. Ingester just stores them; validator audits.
7. **`testbench_alias` round-trip.** If `run.testbench_alias` is set, it must equal `.pvtproject.testbench_aliases[testbench_id]` — but the validator needs the project to check this; ingester runs without project context.
8. **`netlist_path` resolves to a real file** under the run dir (when non-null). Filesystem check; not a JSON shape thing. Belongs in validator.

### What the validator should NOT be responsible for (ingester's job)

- Top-level shape (keys present, types correct).
- Status enum membership.
- Artifact type / source enum membership.
- ISO-8601 format on timestamps.
- `schema_version` dispatch.

This split keeps the ingester narrow and fast, and the validator focused on the semantic-coherence layer where the May-10 funobj bug class would have been caught regardless of SKILL test coverage.

---

### Critical Files for Implementation

- `/home/yusheng/cadence_work/Test/workarea/simkit/python/simkit/ingest.py` (NEW — the worker)
- `/home/yusheng/cadence_work/Test/workarea/simkit/python/simkit/db.py` (NEW — connection / bootstrap / transaction)
- `/home/yusheng/cadence_work/Test/workarea/simkit/python/simkit/schema_sql.py` (NEW — DDL constants)
- `/home/yusheng/cadence_work/Test/workarea/simkit/python/simkit/cli/ingest.py` (NEW — `pvt ingest` CLI surface)
- `/home/yusheng/cadence_work/Test/workarea/simkit/tests/test_ingest.py` (NEW — unit tests anchored on the 42-row real-run fixture at `/tmp/pvt_smoke_db/runs/bdc13f17-d39b-4a13-b58e-846435996a29/run.json`, to be copied into `tests/fixtures/runs/`)
