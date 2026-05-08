# SKILL code

Runs inside Cadence Virtuoso (target: ICADVM18.1-64b, classic SKILL). Pure
language-core, no `db*` / `hi*` dependency in this layer.

## Modules in this directory

| Module             | Phase     | Purpose                                                       |
|--------------------|-----------|---------------------------------------------------------------|
| `pvtError.il`      | Phase 1   | Discriminated-result error model used by all `pvt*` code.     |
| `pvtJson.il`       | Phase 1   | Strict JSON parser; sentinels for `null` / `true` / `false`.  |
| `pvtProject.il`    | Phase 1   | `.pvtproject` discovery, parsing, validation, defstruct.      |
| `collector.il`     | Phase 2   | `PvtSave` entry point. (Not yet written.)                     |

## Load order

The three Phase-1 modules MUST be loaded in this order:

```skill
load("pvtError.il")     ; defines pvtOk / pvtErr / pvtIsOk / pvtIsErr / etc.
load("pvtJson.il")      ; depends on pvtError; defines pvtJsonParseString / parseFile
load("pvtProject.il")   ; depends on pvtError + pvtJson; defines pvtParsePvtProject
                        ; / pvtFindPvtProject / pvtLoadPvtProject / pvtProjectAliasFor
```

`collector.il` (Phase 2) loads on top of these.

## API quick-reference

### `pvtError.il`

* `pvtOk(value)`, `pvtErr(category msg [source])` — constructors.
* `pvtIsOk(r)`, `pvtIsErr(r)` — predicates.
* `pvtUnwrap(r)`, `pvtErrCategory(r)`, `pvtErrMessage(r)`, `pvtErrSource(r)` — accessors.
* `pvtRaise(r)` — convert an `:err` into a SKILL `error()` signal.
* `pvtErrToValidation(r)` — collapse `pvt_json` / `pvt_io` to `pvt_validation`
  (matches Python's `PvtProjectValidationError` wrapping).

### `pvtJson.il`

* `pvtJsonParseString(text)`, `pvtJsonParseFile(path)` — return a `pvtError` result.
* Object handling: parsed objects are `makeTable` values with default
  `'pvt_absent` so callers can distinguish "missing key" from "explicit null".
  Use `pvtJsonObjectKeys(obj)` to enumerate keys in insertion order.
* Sentinels: `'pvt_json_true`, `'pvt_json_false`, `'pvt_json_null`.
  Predicates: `pvtJsonObjectp`, `pvtJsonNullp`, `pvtJsonTruep`, `pvtJsonFalsep`,
  `pvtJsonBoolp`, `pvtJsonAbsentp`.

### `pvtProject.il`

* `pvtFindPvtProject(@optional startDir)` — walker; returns absolute path or nil.
* `pvtLoadPvtProject(@key start env allowDialog)` — full layered lookup:
  env var → walker → optional first-save dialog (SKILL-only; gated by
  `boundp('pvtProjectFirstSaveDialog)` and `getShellEnvVar("DISPLAY")`).
  Returns a `pvtError` result.
* `pvtParsePvtProject(path)` — read+validate a single file.
* `pvtProjectAliasFor(proj testbenchId)` — string or nil.
* Record: `pvtProject` defstruct with slots `project`, `dbRoot`, `author`,
  `testbenchAliases`, `schemaVersion`, `sourcePath`.

## Testing

See `tests/README.md`. Short version, from the simkit repo root:

```sh
virtuoso -nograph -replay skill/tests/runTests.il
```

## Reference docs

Consult `../../SKILL_file/` (the 44-PDF Cadence corpus) before extending this
code. The Phase-1 modules cite specific manual pages inline. Relevant subdirs:
- `01_核心语言与数据库/` — SKILL language, I/O, file primitives
- `03_仿真与分析自动化/` — ADE-XL, MAE, OCEAN (used by Phase 2 `collector.il`)
- `05_其他参考/` — IPC sockets (for the socket bridge, later)
