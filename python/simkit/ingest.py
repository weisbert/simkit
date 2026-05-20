"""Run-dump JSON → DuckDB ingester.

Two entry points:

* :func:`ingest_run_json` — load a single ``run.json``. Atomic per call.
* :func:`ingest_dump_dir` — walk a dump directory (or a ``<dbRoot>``)
  and ingest every ``run.json`` found.

The JSON-shape contract is in ``docs/schema.md`` §2. Per DECISIONS #17,
``ingest_run_json`` runs :func:`simkit.validate.validate_dump` after JSON
load and BEFORE the transaction begins; any error-severity violation
raises :class:`simkit.errors.ValidationError` (a subclass of
``IngestError``). Warnings are logged via ``logging.getLogger(
'simkit.ingest')`` when ``on_warning='log'``.

Idempotency: ``on_conflict`` controls duplicate-``run_id`` handling —
``"error"`` (default) raises :class:`DuplicateRunError`, ``"skip"``
returns an ``IngestResult`` with ``action='skipped'``, ``"replace"``
deletes the prior rows for that ``run_id`` and re-inserts.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, List, Literal, Optional

import duckdb

from simkit.db import transaction
from simkit.errors import (
    DuplicateRunError,
    IngestError,
    MalformedDumpError,
    MissingDumpError,
    SchemaVersionError,
    ValidationError,
)
from simkit.validate import validate_dump


_LOG = logging.getLogger("simkit.ingest")


# v1.4 — schema_version 2 adds the optional top-level ``output_specs`` map
# carrying per-(test, output) spec strings from the collector. v1 envelopes
# still ingest cleanly; their result rows get spec=NULL, spec_status='no_spec'.
_INGESTER_SUPPORTED_DUMP_VERSIONS = frozenset({1, 2})

_OnConflict = Literal["error", "skip", "replace"]
_OnWarning = Literal["log", "ignore"]
_Action = Literal["inserted", "skipped", "replaced"]


@dataclass(frozen=True)
class IngestResult:
    """Outcome of one ingest call.

    ``n_warnings`` is non-zero only when the inline validator was active
    (``validate=True``) and surfaced ``W1`` / ``W2`` warnings.
    """

    run_id: str
    action: _Action
    n_results: int
    n_artifacts: int
    n_warnings: int
    source_path: Path


# ----------------------------------------------------------------------
# Public entry points
# ----------------------------------------------------------------------

def ingest_run_json(
    con: duckdb.DuckDBPyConnection,
    run_json_path: Path,
    *,
    on_conflict: _OnConflict = "error",
    validate: bool = True,
    on_warning: _OnWarning = "log",
    now: Optional[Callable[[], datetime]] = None,
) -> IngestResult:
    """Load a single ``run.json`` into the DB. Atomic per call.

    Steps:

    1. Open + JSON-decode (any decode error → :class:`MalformedDumpError`).
    2. ``schema_version`` dispatch (:class:`MalformedDumpError` or
       :class:`SchemaVersionError`).
    3. If ``validate=True``, call :func:`validate_dump`. Any
       ``severity='error'`` → raise :class:`ValidationError`. Warnings
       logged or ignored per ``on_warning``.
    4. Top-level shape sanity (already overlaps with validator; cheap).
    5. Begin transaction; conflict-check; insert ``runs`` then
       ``results`` then ``artifacts``; commit.

    Returns an :class:`IngestResult`.
    """
    path = Path(run_json_path).resolve()
    if not path.is_file():
        raise MissingDumpError(f"not a file: {path}")

    dump = _load_json(path)
    _check_schema_version(path, dump)

    n_warnings = 0
    if validate:
        violations = validate_dump(dump)
        errs = [v for v in violations if v.severity == "error"]
        if errs:
            raise ValidationError(violations)
        warns = [v for v in violations if v.severity == "warning"]
        n_warnings = len(warns)
        if warns and on_warning == "log":
            for w in warns:
                _LOG.warning(
                    "ingest validator: %s %s: %s",
                    w.code, w.path, w.message,
                )

    # Defensive shape check (validator already covers this when active,
    # but the API allows validate=False).
    _check_shape(path, dump)

    run = dump["run"]
    run_id = run["run_id"]
    results = dump["results"]
    artifacts = dump["artifacts"]
    # v1.4 — output_specs is v2-only. For v1 envelopes we leave the new
    # spec / spec_status columns NULL (= "unknown, predates spec capture").
    # For v2 envelopes we always run evaluate_spec — when the envelope's
    # output_specs is empty / missing the per-output spec, the verdict
    # lands as 'no_spec' (= "checked and there's no spec").
    dump_schema_version = int(dump["schema_version"])
    output_specs = (
        dump.get("output_specs") or {} if dump_schema_version >= 2 else None
    )

    now_fn = now if now is not None else (lambda: datetime.now(timezone.utc))
    ingested_at = _isoformat(now_fn())

    with transaction(con):
        existing = con.execute(
            "SELECT 1 FROM runs WHERE run_id = ?", [run_id]
        ).fetchone()

        action: _Action
        if existing is not None:
            if on_conflict == "error":
                raise DuplicateRunError(
                    f"run_id {run_id!r} is already present in the DB "
                    f"(source: {path}); pass on_conflict='replace' or "
                    f"--force to overwrite"
                )
            if on_conflict == "skip":
                return IngestResult(
                    run_id=run_id,
                    action="skipped",
                    n_results=0,
                    n_artifacts=0,
                    n_warnings=n_warnings,
                    source_path=path,
                )
            if on_conflict == "replace":
                con.execute(
                    "DELETE FROM artifacts WHERE run_id = ?", [run_id]
                )
                con.execute(
                    "DELETE FROM results WHERE run_id = ?", [run_id]
                )
                con.execute(
                    "DELETE FROM runs WHERE run_id = ?", [run_id]
                )
                action = "replaced"
            else:  # pragma: no cover - argparse should prevent this
                raise ValueError(f"unknown on_conflict={on_conflict!r}")
        else:
            action = "inserted"

        # G-5 — optional top-level provenance object, serialised to a
        # JSON string for the runs.provenance column. Absent on a manual
        # PvtSave that bypassed the orchestrator → column stays NULL.
        provenance = dump.get("provenance")
        provenance_json = (
            json.dumps(provenance, sort_keys=True)
            if isinstance(provenance, dict) and provenance
            else None
        )
        _insert_run_row(
            con, run, ingested_at, dump["schema_version"], provenance_json,
        )
        _insert_results(con, run_id, results, output_specs)
        _insert_artifacts(con, run_id, artifacts)

    return IngestResult(
        run_id=run_id,
        action=action,
        n_results=len(results),
        n_artifacts=len(artifacts),
        n_warnings=n_warnings,
        source_path=path,
    )


def ingest_dump_dir(
    con: duckdb.DuckDBPyConnection,
    dump_dir: Path,
    *,
    on_conflict: _OnConflict = "error",
    validate: bool = True,
    on_warning: _OnWarning = "log",
    continue_on_error: bool = False,
    now: Optional[Callable[[], datetime]] = None,
) -> List[IngestResult]:
    """Walk ``dump_dir`` and ingest every ``run.json`` found.

    Two acceptable layouts:

    * ``dump_dir/run.json`` — single-run convenience.
    * ``dump_dir/runs/<run_id>/run.json`` — full dbRoot convention.

    Files are processed in lexicographic order for deterministic test
    output. Each ``run.json`` is its own transaction (DECISIONS #20 —
    partial-success semantics are intentional).
    """
    base = Path(dump_dir).resolve()
    if not base.is_dir():
        raise MissingDumpError(f"not a directory: {base}")

    candidates: List[Path] = []
    single = base / "run.json"
    if single.is_file():
        candidates.append(single)
    else:
        runs_root = base / "runs"
        if runs_root.is_dir():
            for entry in sorted(runs_root.iterdir()):
                rj = entry / "run.json"
                if rj.is_file():
                    candidates.append(rj)

    results: List[IngestResult] = []
    for path in candidates:
        try:
            res = ingest_run_json(
                con,
                path,
                on_conflict=on_conflict,
                validate=validate,
                on_warning=on_warning,
                now=now,
            )
        except IngestError:
            if continue_on_error:
                _LOG.exception("ingest failed for %s", path)
                continue
            raise
        results.append(res)
    return results


# ----------------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------------

def _load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        raise MalformedDumpError(
            f"{path}: invalid JSON — {exc.msg} (line {exc.lineno} "
            f"col {exc.colno})"
        ) from exc
    except OSError as exc:
        raise MissingDumpError(f"{path}: cannot read — {exc}") from exc


def _check_schema_version(path: Path, dump: Any) -> None:
    if not isinstance(dump, dict):
        raise MalformedDumpError(
            f"{path}: top-level must be a JSON object, got "
            f"{type(dump).__name__}"
        )
    if "schema_version" not in dump:
        raise MalformedDumpError(
            f"{path}: missing required key 'schema_version' "
            "(see docs/schema.md §2)"
        )
    sv = dump["schema_version"]
    if isinstance(sv, bool) or not isinstance(sv, int):
        raise MalformedDumpError(
            f"{path}: 'schema_version' must be an integer, got "
            f"{type(sv).__name__}: {sv!r}"
        )
    if sv < 1:
        raise MalformedDumpError(
            f"{path}: 'schema_version' must be a positive integer, "
            f"got {sv}"
        )
    if sv not in _INGESTER_SUPPORTED_DUMP_VERSIONS:
        raise SchemaVersionError(
            f"{path}: schema_version {sv} not supported by this ingester "
            f"(supported: {sorted(_INGESTER_SUPPORTED_DUMP_VERSIONS)}); "
            "upgrade the ingester, not the dump"
        )


def _check_shape(path: Path, dump: dict) -> None:
    """Top-level + run / results / artifacts shape sanity.

    Overlaps with the validator but is cheap; required when
    ``validate=False`` so we still fail loudly on malformed dumps.
    """
    for key in ("run", "results", "artifacts"):
        if key not in dump:
            raise MalformedDumpError(
                f"{path}: missing required top-level key {key!r}"
            )
    if not isinstance(dump["run"], dict):
        raise MalformedDumpError(
            f"{path}: 'run' must be a JSON object, got "
            f"{type(dump['run']).__name__}"
        )
    if not isinstance(dump["results"], list):
        raise MalformedDumpError(
            f"{path}: 'results' must be a JSON array, got "
            f"{type(dump['results']).__name__}"
        )
    if not isinstance(dump["artifacts"], list):
        raise MalformedDumpError(
            f"{path}: 'artifacts' must be a JSON array, got "
            f"{type(dump['artifacts']).__name__}"
        )

    run = dump["run"]
    required_run = (
        "run_id", "project_id", "testbench_id", "testbench_alias",
        "timestamp", "author", "label", "note",
        "netlist_path", "history_name",
    )
    for key in required_run:
        if key not in run:
            raise MalformedDumpError(
                f"{path}: missing required field 'run.{key}'"
            )

    for idx, row in enumerate(dump["results"]):
        if not isinstance(row, dict):
            raise MalformedDumpError(
                f"{path}: results[{idx}] must be a JSON object"
            )
        for key in (
            "point", "corner", "test", "output", "value",
            "status", "sweep", "corner_vars", "test_note",
        ):
            if key not in row:
                raise MalformedDumpError(
                    f"{path}: results[{idx}] missing field {key!r}"
                )

    for idx, art in enumerate(dump["artifacts"]):
        if not isinstance(art, dict):
            raise MalformedDumpError(
                f"{path}: artifacts[{idx}] must be a JSON object"
            )
        for key in (
            "type", "relative_path", "description", "source", "created_at",
        ):
            if key not in art:
                raise MalformedDumpError(
                    f"{path}: artifacts[{idx}] missing field {key!r}"
                )


def _isoformat(dt: datetime) -> str:
    """Stringify a datetime to a DuckDB-castable TIMESTAMPTZ string."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _split_value(value: Any) -> tuple:
    """Split a result row's ``value`` into ``(value_num, value_str)``.

    * number (int or float, not bool) → (float(value), None)
    * non-empty string                 → (None, value)
    * None                             → (None, None)
    """
    if value is None:
        return (None, None)
    if isinstance(value, bool):
        # Defensive: validator should have rejected, but if validate=False
        # let the FK / type enforcement carry; treat as int.
        return (float(int(value)), None)
    if isinstance(value, (int, float)):
        return (float(value), None)
    if isinstance(value, str):
        return (None, value)
    # Last-ditch: stringify; should have been rejected by validator.
    return (None, str(value))


def _insert_run_row(
    con: duckdb.DuckDBPyConnection,
    run: dict,
    ingested_at: str,
    schema_version: int,
    provenance_json: Optional[str] = None,
) -> None:
    con.execute(
        """
        INSERT INTO runs (
          run_id, project_id, testbench_id, testbench_alias,
          timestamp, author, label, note,
          netlist_path, history_name, schema_version, ingested_at,
          provenance
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            run["run_id"],
            run["project_id"],
            run["testbench_id"],
            run.get("testbench_alias"),
            run["timestamp"],
            run["author"],
            run.get("label"),
            run.get("note"),
            run.get("netlist_path"),
            run["history_name"],
            schema_version,
            ingested_at,
            provenance_json,
        ],
    )


def _insert_results(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    results: list,
    output_specs: dict | None = None,
) -> None:
    """Insert one run's result rows. v1.4 — also computes spec_status per row.

    ``output_specs`` semantics:

    * ``None``   — pre-v2 envelope (collector predates spec capture). Every
      row's spec / spec_status columns land as NULL ("unknown").
    * ``{}``     — v2 envelope with zero specs set. Every row resolves to
      spec_status='no_spec' ("checked, no spec on this output").
    * populated — v2 envelope; per-row spec string looked up by
      ``{<test>: {<output>: <spec>}}``. Verdict computed via
      :mod:`simkit.spec_eval`.

    Sentinel output names (``__sim_status__``) and rows whose ``value`` was
    a non-number (eval_err, sim_err) automatically land in the 'no_value'
    verdict branch via ``evaluate_spec``.
    """
    if not results:
        return
    rows = []
    if output_specs is None:
        # v1: every row gets NULL/NULL — no eval, no allocation.
        for r in results:
            value_num, value_str = _split_value(r.get("value"))
            rows.append([
                run_id, r["point"], r["corner"], r["test"], r["output"],
                value_num, value_str, r["status"],
                json.dumps(r["sweep"]),
                json.dumps(r["corner_vars"]),
                r.get("test_note"),
                None, None,
            ])
    else:
        from simkit.spec_eval import evaluate_spec
        for r in results:
            value_num, value_str = _split_value(r.get("value"))
            test_name = r["test"]
            out_name = r["output"]
            spec_str = (output_specs.get(test_name) or {}).get(out_name)
            if isinstance(spec_str, str) and spec_str.strip() == "":
                spec_str = None
            spec_status = evaluate_spec(spec_str, value_num)
            rows.append([
                run_id, r["point"], r["corner"], test_name, out_name,
                value_num, value_str, r["status"],
                json.dumps(r["sweep"]),
                json.dumps(r["corner_vars"]),
                r.get("test_note"),
                spec_str, spec_status,
            ])
    con.executemany(
        """
        INSERT INTO results (
          run_id, point, corner, test, output,
          value_num, value_str, status,
          sweep, corner_vars, test_note,
          spec, spec_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def _insert_artifacts(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    artifacts: list,
) -> None:
    if not artifacts:
        return
    rows = []
    for a in artifacts:
        rows.append([
            run_id,
            a["type"],
            a["relative_path"],
            a.get("description"),
            a["source"],
            a["created_at"],
        ])
    con.executemany(
        """
        INSERT INTO artifacts (
          run_id, type, relative_path, description, source, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
