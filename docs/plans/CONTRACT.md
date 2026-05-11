# Reconciled implementation contract — overnight 2026-05-10

This file records the director-level decisions reconciling Plan-A (§4 ingester) and Plan-B (§3 messy-data: validator part), made before launching the overnight implementer. The plans themselves are in:

- `§4_ingester.md` — full Python ingester plan (Plan-A)
- `§3_messy_data.md` — SKILL refactor + Python validator plan (Plan-B)
- `§2.2_dialog.md` — SKILL dialog design (Plan-D, deferred)

User authorized autonomous overnight execution. Morning review expected.

---

## Conflicts found between Plan-A and Plan-B, and how I resolved them

### C1. Does the ingester call the validator inline?

- **Plan-A position (§10):** ingester does NOT call validator. Different failure modes; validator is a peer module. `pvt validate` is a separate CLI verb.
- **Plan-B position (§4.3):** ingester SHOULD call `validate_dump_file` after JSON parse, before any DuckDB write. Errors block ingest, warnings log + continue.
- **My decision (overnight):** **Plan-B's behaviour, with Plan-A's seam.** Ingester calls validator by default (so the two analytic surfaces don't drift apart). But the call site is a single function the user can swap or disable: `--no-validate` CLI flag and `validate=True` keyword on the API. The validator stays a peer module, fully usable standalone via `python -m simkit.validate` for offline auditing.
- **Why:** Plan-B's argument is correct in practice — without the inline call, "I ingested this dump" doesn't imply "this dump is consistent." Plan-A's concern (decoupling) is preserved by keeping the validator independently invocable. Cost: every ingest pays for invariant checks. Acceptable on Phase-1-scale dumps (low thousands of rows).

### C2. `netlist_path` nullability

- **Plan-A position (§0 item 1):** schema.md says required, fixture has null (the §3 soft-miss). Resolution: nullable DDL + TODO comment to tighten when §3 netlist Spectre fix lands.
- **Plan-B position (W2):** soft warning when null.
- **No conflict** — both treat it as nullable. Combined behaviour: DDL `netlist_path VARCHAR` (nullable). Validator emits W2 warning when null. Add a note in `DECISIONS.md` (next decision number — likely #17) calling out the schema/impl mismatch with a pointer to §3 netlist Spectre TODO as the closure condition.

### C3. `status` enum strictness

- **Plan-A position (§0 item 5):** closed set `{ok, failed, running, no_convergence}`. Hard error otherwise.
- **Plan-B position (Bug A):** suggests changing line 679 SKILL fallback from `"running"` to `"unknown"`. With strict enum, that emit becomes a hard ingest failure — surfaces the gap.
- **My decision:** Bug A is a SKILL change. **Defer to morning.** Tonight: keep validator I12 strict (closed set). If/when Bug A lands and emits `"unknown"`, the validator will catch it as a schema violation, which is the intended diagnostic flow. No special-casing tonight.

### C4. SKILL refactor + bug fixes (Plan-B parts a / Bug A–D)

- **Plan-B has 7 implementation steps.** Steps 5 (validator) and the Python tests (Step 5) are pure-Python and safe to land tonight. Steps 1–4 (`_pvtCollWalkRdb`/`_pvtCollRowsFromTuples` split + Bug A/B/C/D fixes) are SKILL changes that **require Tier-2 verification on the user's live Maestro session** per Decision #16. Cannot be Tier-2-verified without the user.
- **Decision:** Defer all SKILL changes to morning. Tonight only delivers the Python validator (Plan-B Step 5).

### C5. Plan-D (SKILL dialog)

- Requires consulting the `virtuoso-skill` PDF index for `hi*` form construction (Plan-D §8 lists the references). Requires live Virtuoso UI testing for callback semantics (Plan-D Risk #1). Both are user-loop activities.
- **Decision:** Full deferral to morning.

---

## Tonight's implementation scope (locked)

### Files to create

```
python/simkit/
  errors.py                       (NEW — IngestError hierarchy + ValidationError)
  schema_sql.py                   (NEW — DDL constants only)
  db.py                           (NEW — connect / bootstrap / transaction)
  ingest.py                       (NEW — walker + JSON loader)
  validate.py                     (NEW — invariant checker, returns Violation list)
  cli/
    __init__.py                   (NEW)
    __main__.py                   (NEW — `pvt` dispatcher)
    ingest.py                     (NEW — `pvt ingest`)
    validate.py                   (NEW — `pvt validate`)

python/simkit/__init__.py         (UPDATE — re-export new public names)

tests/
  test_db.py                      (NEW)
  test_ingest.py                  (NEW)
  test_validate.py                (NEW)
  test_cli_ingest.py              (NEW)
  test_cli_validate.py            (NEW)
  fixtures/
    runs/
      bdc13f17-.../run.json       (already copied tonight from /tmp)
      synthetic_minimal/run.json  (NEW — hand-written)
      synthetic_messy/run.json    (NEW)
      synthetic_with_artifacts/run.json (NEW)
      bad_version/run.json        (NEW)
      bad_status/run.json         (NEW)
      bad_value_when_failed/run.json (NEW)
      malformed_json/run.json     (NEW)
```

### Files NOT to touch tonight

- `skill/*.il` — no SKILL changes
- `docs/schema.md` — no schema doc changes (the netlist_path mismatch + "unknown" status discussion goes in `DECISIONS.md` draft, not in the spec — user reviews before changing the spec)
- `DECISIONS.md` — leave a draft note in `MORNING_REVIEW.md` listing decisions to be appended

### API contract (the implementer agent should treat this as binding)

#### `simkit.errors`

```python
class SimkitError(Exception): ...

class IngestError(SimkitError): ...
class MalformedDumpError(IngestError): ...
class SchemaVersionError(IngestError): ...
class DuplicateRunError(IngestError): ...
class MissingDumpError(IngestError): ...

class ValidationError(SimkitError):
    """Raised by validate_dump when severity='error' violations exist
       AND the caller asked to raise (default returns the list)."""
    def __init__(self, violations: list["Violation"]): ...
```

#### `simkit.validate`

```python
@dataclass(frozen=True)
class Violation:
    code: str               # "I1" .. "I24", "W1", "W2"
    severity: Literal["error", "warning"]
    path: str               # e.g. "results[42].status"
    message: str

def validate_dump(dump: dict) -> list[Violation]: ...
def validate_dump_file(path: Path) -> list[Violation]: ...
```

Implements all 24 invariants from Plan-B §4.1 (I1–I24) and 2 warnings (W1–W2).

#### `simkit.ingest`

```python
@dataclass(frozen=True)
class IngestResult:
    run_id: str
    action: Literal["inserted", "skipped", "replaced"]
    n_results: int
    n_artifacts: int
    n_warnings: int             # NEW vs Plan-A — tracks W1/W2 surfaced during ingest
    source_path: Path

def ingest_run_json(
    con,
    run_json_path: Path,
    *,
    on_conflict: Literal["error", "skip", "replace"] = "error",
    validate: bool = True,                # NEW vs Plan-A
    on_warning: Literal["log", "ignore"] = "log",  # NEW
    now: Optional[Callable[[], datetime]] = None,
) -> IngestResult: ...

def ingest_dump_dir(
    con,
    dump_dir: Path,
    *,
    on_conflict: Literal["error", "skip", "replace"] = "error",
    validate: bool = True,
    on_warning: Literal["log", "ignore"] = "log",
    continue_on_error: bool = False,
    now: Optional[Callable[[], datetime]] = None,
) -> list[IngestResult]: ...
```

When `validate=True` (default), `ingest_run_json` runs `validate_dump(dump_dict)` after JSON load, before transaction begin. Any `severity="error"` Violation → raise `ValidationError(violations)` (subclass of `IngestError`, so `IngestError` catchers still match). Warnings logged via stdlib `logging.getLogger("simkit.ingest")` when `on_warning="log"`.

#### `simkit.db`

```python
def connect(db_path: Path | str, *, read_only: bool = False) -> duckdb.DuckDBPyConnection: ...
def bootstrap(con) -> None: ...
@contextmanager
def transaction(con): ...
```

`db_path == ":memory:"` (or `Path(":memory:")` is awkward; accept str too) for tests.

#### CLI

`pvt ingest <path> [--db PATH] [--force] [--no-validate] [--continue-on-error] [-v]`
`pvt validate <path> [--from-db DB --run-id ID] [-v]`

Exit codes:
- ingest: 0 success, 1 ingest error (incl. validation), 2 usage, 3 IO/DB
- validate: 0 clean, 1 warnings only, 2 errors

### Test obligations

Implementer must:
1. Implement every concrete test name listed in Plan-A §8 and Plan-B §5.2.
2. Confirm `tests/test_project_loader.py` (existing 30 tests) still passes — no regressions.
3. Run `PYTHONPATH=python python3.11 -m unittest discover -s tests -v` and capture stdout to `MORNING_REVIEW.md`.
4. **Do not commit anything.** Leave all changes uncommitted.

### Things implementer must NOT do

- No edits to `skill/*.il`
- No commits, no `git add`, no branch creation
- No edits to `docs/schema.md`
- No edits to `DECISIONS.md` or `PROJECT_STATE.md` or `TODO.md` (notes for those go into `MORNING_REVIEW.md`)
- No installation of new dependencies beyond `duckdb` (which the project already permits per Phase 1 plan)
- If a Plan-A or Plan-B decision turns out to be impossible during implementation, the implementer must STOP and write the issue to `MORNING_REVIEW.md` rather than improvising a different design

---

## Decisions queued for `DECISIONS.md` (for user to append after morning review)

These are draft entries the implementer surfaces in `MORNING_REVIEW.md` for user sign-off:

- **#17 (proposed):** Ingester wraps validator inline by default. `pvt ingest --no-validate` to skip. Rationale: avoid drift between "loaded" and "consistent." Reconciles Plan-A (decoupled) and Plan-B (inlined) — keeps validator standalone-invocable while making default ingest a quality gate.
- **#18 (proposed):** `netlist_path` is permanently nullable in DuckDB. Schema.md still declares it required. Reconciliation of this mismatch is gated on §3 netlist Spectre detection fix (separate TODO bullet). Validator emits W2 warning when null.
- **#19 (proposed):** `simkit_meta` table added (key/value) for tracking DB-side schema_version. Not in `docs/schema.md` (internal bookkeeping). Will be load-bearing the first time DDL changes.
- **#20 (proposed):** Per-file (per-run) ingest transactions, not one-big-transaction across `ingest_dump_dir`. Allows partial-success semantics with `--continue-on-error`. Tradeoff: a hostile concurrent reader could see partial state mid-walk; acceptable for offline single-user tool.
