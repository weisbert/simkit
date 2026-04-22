# Schema Spec

**Status: placeholder — to be completed as Phase 1 task section 1.**

This doc defines three things:

## 1. `.pvtproject` YAML format

Fields to specify:
- `project` (str, required) — project identifier
- `dbRoot` (path, required) — where DuckDB + run dumps live
- `author` (str, optional) — defaults to `$USER`
- `testbench_aliases` (map, optional) — `lib/cell/view → readable_name`
- … (more as needs emerge)

## 2. JSON dump format (per run)

Per-run JSON written by the collector SKILL. Must include:
- `schema_version` (int) — for forward compat
- run-level metadata: `run_id`, `project_id`, `testbench_id`, `testbench_alias`, `timestamp`, `author`, `label` (nullable), `note` (nullable), `netlist_path` (relative)
- per-result records: `point`, `corner`, `test`, `output`, `value`, `status`, `sweep`, `corner_vars`, `test_note`
- `artifacts` list (initial set at dump time): `type`, `relative_path`, `description`, `source`

See `DECISIONS.md` #7, #8, #9, #10 for the driving rationale.

## 3. DuckDB tables

Rough sketch (types TBD):

```
runs(
  run_id PK,
  project_id, testbench_id, testbench_alias,
  timestamp, author,
  label nullable, note nullable,
  netlist_path,
  schema_version
)

results(
  run_id FK,
  point, corner, test, output,
  value, status,
  sweep JSON, corner_vars JSON, test_note
)

artifacts(
  run_id FK,
  type, relative_path, description,
  source (auto|manual), created_at
)
```

Indexes and slice-view (e.g., `runs WHERE label IS NOT NULL`) to be specified when the loader is written.

---

**When fleshing this out:**
- Be concrete about types, required vs. optional, defaults, validation rules.
- Version the schema from day one. Never break v1 compatibility without bumping `schema_version` and writing a migration note.
- Reference the relevant `DECISIONS.md` entry for every non-obvious field.
