"""Schema-invariant validator for simkit JSON dumps.

Pure-Python; stdlib only. Returns a list of :class:`Violation` records
(empty list = the dump is clean). Implements the I1–I24 / W1, W2 invariants
catalogued in ``docs/plans/§3_messy_data.md`` §4.1.

Two entry points:

* :func:`validate_dump` — dict in, list out. Pure.
* :func:`validate_dump_file` — load JSON from disk, then validate.

Severity policy:

* ``severity == "error"`` violations are blocking. The inline-validate path
  in :func:`simkit.ingest.ingest_run_json` raises
  :class:`simkit.errors.ValidationError` if any error-level violation
  surfaces.
* ``severity == "warning"`` violations are advisory; ``W1`` flags magic
  ``corner_vars`` markers (``_no_corner_vars`` etc.), ``W2`` flags a null
  ``netlist_path``.

Module structure mirrors the SKILL refactor: every check is a small
function appending to the violations list. No fail-fast — the caller wants
the full picture.

CLI: ``python -m simkit.validate <path>`` prints violations and exits
``0`` (clean), ``1`` (warnings only), or ``2`` (any error). The
``pvt validate`` subcommand provides the same surface from the unified
entry point.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Literal, Optional


_Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Violation:
    """A single schema invariant breach.

    Attributes:
        code: ``I1`` .. ``I24`` for hard errors, ``W1`` / ``W2`` for warnings.
        severity: ``"error"`` or ``"warning"``.
        path: dotted path into the dump, e.g. ``"results[42].status"``.
        message: human-readable explanation.
    """

    code: str
    severity: _Severity
    path: str
    message: str


# ----------------------------------------------------------------------
# Regex constants
# ----------------------------------------------------------------------

# UUIDv4 (loose: hex + dashes, 36 chars total). Same shape as the SKILL
# `_pvtCollIsUuidV4` accepts.
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_PROJECT_ID_RE = re.compile(r"^[a-z0-9_-]+$")
_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$"
)


# ----------------------------------------------------------------------
# Closed sets
# ----------------------------------------------------------------------

VALID_STATUSES = frozenset({"ok", "failed", "running", "no_convergence", "eval_err"})

# Statuses that record per-output measurement outcomes (vs. whole-test or
# whole-triple sentinels). `eval_err` is a per-output sentinel: a single
# output that came back from the rdb with an unshapable value (typically a
# (eval_err "...") list when the expression errored on this corner) but the
# surrounding test/corner is otherwise alive.
_DATA_ROW_STATUSES = frozenset({"ok", "eval_err"})
_SENTINEL_ROW_STATUSES = frozenset({"failed", "running", "no_convergence"})
VALID_ARTIFACT_TYPES = frozenset({
    "waveform", "results_table", "sim_log", "schematic",
    "netlist_diff", "image", "pdf", "other",
})
VALID_ARTIFACT_SOURCES = frozenset({"auto", "manual"})

_TOP_LEVEL_KEYS = frozenset({"schema_version", "run", "results", "artifacts"})

_RUN_REQUIRED = (
    "run_id", "project_id", "testbench_id", "testbench_alias",
    "timestamp", "author", "label", "note",
    "netlist_path", "history_name",
)

_RESULT_REQUIRED = (
    "point", "corner", "test", "output", "value",
    "status", "sweep", "corner_vars", "test_note",
)

_ARTIFACT_REQUIRED = (
    "type", "relative_path", "description", "source", "created_at",
)

_SENTINEL_OUTPUT = "__sim_status__"


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------

def validate_dump(dump: Any) -> List[Violation]:
    """Validate a parsed JSON dump dict.

    Returns the (possibly empty) list of violations. Empty list means the
    dump is clean. Caller decides policy (raise vs log).
    """
    violations: List[Violation] = []

    if not isinstance(dump, dict):
        violations.append(Violation(
            code="I24",
            severity="error",
            path="",
            message=(
                f"top-level must be a JSON object, got {type(dump).__name__}"
            ),
        ))
        return violations

    _check_top_level(dump, violations)

    # The next checks tolerate missing/wrong-typed sub-objects (already
    # flagged by I24) so we surface as much as we can in one pass.
    run = dump.get("run") if isinstance(dump.get("run"), dict) else None
    if run is not None:
        _check_run_meta(run, violations)

    results = dump.get("results") if isinstance(dump.get("results"), list) else None
    if results is not None:
        _check_results(results, violations)
        _check_triple_coverage(results, violations)

    artifacts = dump.get("artifacts") if isinstance(dump.get("artifacts"), list) else None
    if artifacts is not None:
        _check_artifacts(artifacts, violations)

    return violations


def validate_dump_file(path: Path) -> List[Violation]:
    """Load a run.json and validate it.

    JSON-decode errors surface as a single ``I24`` violation pointing at the
    file. Filesystem errors propagate (caller decides whether to mask).
    """
    path = Path(path)
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        return [Violation(
            code="I24",
            severity="error",
            path=str(path),
            message=f"invalid JSON: {exc.msg} (line {exc.lineno} col {exc.colno})",
        )]
    return validate_dump(data)


# ----------------------------------------------------------------------
# Per-section helpers
# ----------------------------------------------------------------------

def _check_top_level(dump: dict, violations: List[Violation]) -> None:
    # I23: schema_version == 1 (integer, not string, not bool).
    if "schema_version" not in dump:
        violations.append(Violation(
            "I23", "error", "schema_version",
            "missing required key 'schema_version'",
        ))
    else:
        sv = dump["schema_version"]
        if isinstance(sv, bool) or not isinstance(sv, int):
            violations.append(Violation(
                "I23", "error", "schema_version",
                f"must be integer 1, got {type(sv).__name__}: {sv!r}",
            ))
        elif sv != 1:
            violations.append(Violation(
                "I23", "error", "schema_version",
                f"unsupported schema_version {sv}; v1 expects 1",
            ))

    # I24: top-level keys exactly {schema_version, run, results, artifacts}.
    keys = set(dump.keys())
    missing = _TOP_LEVEL_KEYS - keys
    extra = keys - _TOP_LEVEL_KEYS
    for k in sorted(missing):
        violations.append(Violation(
            "I24", "error", k,
            f"missing required top-level key '{k}'",
        ))
    for k in sorted(extra):
        violations.append(Violation(
            "I24", "error", k,
            f"unknown top-level key '{k}'; expected exactly "
            f"{sorted(_TOP_LEVEL_KEYS)}",
        ))

    # Type checks for each top-level value (as far as we can tell here).
    if "run" in dump and not isinstance(dump["run"], dict):
        violations.append(Violation(
            "I24", "error", "run",
            f"'run' must be a JSON object, got {type(dump['run']).__name__}",
        ))
    if "results" in dump and not isinstance(dump["results"], list):
        violations.append(Violation(
            "I24", "error", "results",
            f"'results' must be a JSON array, got {type(dump['results']).__name__}",
        ))
    if "artifacts" in dump and not isinstance(dump["artifacts"], list):
        violations.append(Violation(
            "I24", "error", "artifacts",
            f"'artifacts' must be a JSON array, got "
            f"{type(dump['artifacts']).__name__}",
        ))


def _check_run_meta(run: dict, violations: List[Violation]) -> None:
    for key in _RUN_REQUIRED:
        if key not in run:
            violations.append(Violation(
                "I24", "error", f"run.{key}",
                f"missing required field 'run.{key}'",
            ))

    # I2: run_id is UUIDv4.
    rid = run.get("run_id")
    if rid is not None:
        if not isinstance(rid, str) or not _UUID_RE.match(rid):
            violations.append(Violation(
                "I2", "error", "run.run_id",
                f"run_id must match UUIDv4 hex/dash pattern, got {rid!r}",
            ))

    # I3: project_id matches ^[a-z0-9_-]+$.
    pid = run.get("project_id")
    if pid is not None:
        if not isinstance(pid, str) or not _PROJECT_ID_RE.match(pid):
            violations.append(Violation(
                "I3", "error", "run.project_id",
                f"project_id must match ^[a-z0-9_-]+$, got {pid!r}",
            ))

    # I4: testbench_id == lib/cell/view (three slash-separated non-empty tokens).
    tbid = run.get("testbench_id")
    if tbid is not None:
        if not isinstance(tbid, str):
            violations.append(Violation(
                "I4", "error", "run.testbench_id",
                f"testbench_id must be a string, got {type(tbid).__name__}",
            ))
        else:
            parts = tbid.split("/")
            if len(parts) != 3 or not all(parts):
                violations.append(Violation(
                    "I4", "error", "run.testbench_id",
                    f"testbench_id must be 'lib/cell/view', got {tbid!r}",
                ))

    # I5: testbench_alias is str (non-empty) or null.
    if "testbench_alias" in run:
        alias = run["testbench_alias"]
        if alias is not None:
            if not isinstance(alias, str) or alias == "":
                violations.append(Violation(
                    "I5", "error", "run.testbench_alias",
                    "testbench_alias must be a non-empty string or null",
                ))

    # I6: timestamp ISO 8601 with offset.
    ts = run.get("timestamp")
    if ts is not None:
        if not isinstance(ts, str) or not _TIMESTAMP_RE.match(ts):
            violations.append(Violation(
                "I6", "error", "run.timestamp",
                f"timestamp must match YYYY-MM-DDThh:mm:ss[+-]hh:mm, got {ts!r}",
            ))

    # I7: author non-empty string.
    author = run.get("author")
    if author is not None:
        if not isinstance(author, str) or author == "":
            violations.append(Violation(
                "I7", "error", "run.author",
                "author must be a non-empty string",
            ))

    # I8: label is str or null.
    if "label" in run:
        label = run["label"]
        if label is not None and not isinstance(label, str):
            violations.append(Violation(
                "I8", "error", "run.label",
                f"label must be string or null, got {type(label).__name__}",
            ))

    # I9: note is str or null.
    if "note" in run:
        note = run["note"]
        if note is not None and not isinstance(note, str):
            violations.append(Violation(
                "I9", "error", "run.note",
                f"note must be string or null, got {type(note).__name__}",
            ))

    # I10 / W2: netlist_path is str or null. Schema says required, but the
    # collector emits null on Spectre-detect soft-miss; accept null with a
    # warning until §3 closes.
    if "netlist_path" in run:
        np = run["netlist_path"]
        if np is None:
            violations.append(Violation(
                "W2", "warning", "run.netlist_path",
                "netlist_path is null (schema declares required; collector "
                "emits null on soft-miss — see DECISIONS #18)",
            ))
        elif not isinstance(np, str) or np == "":
            violations.append(Violation(
                "I10", "error", "run.netlist_path",
                "netlist_path must be a non-empty string (or null with "
                "warning)",
            ))

    # I11: history_name non-empty string.
    hn = run.get("history_name")
    if hn is not None:
        if not isinstance(hn, str) or hn == "":
            violations.append(Violation(
                "I11", "error", "run.history_name",
                "history_name must be a non-empty string",
            ))


def _check_results(results: list, violations: List[Violation]) -> None:
    for idx, row in enumerate(results):
        path = f"results[{idx}]"
        if not isinstance(row, dict):
            violations.append(Violation(
                "I24", "error", path,
                f"result row must be a JSON object, got {type(row).__name__}",
            ))
            continue

        for key in _RESULT_REQUIRED:
            if key not in row:
                violations.append(Violation(
                    "I24", "error", f"{path}.{key}",
                    f"missing required field '{key}'",
                ))

        # I12: status enum.
        status = row.get("status")
        if not isinstance(status, str) or status not in VALID_STATUSES:
            violations.append(Violation(
                "I12", "error", f"{path}.status",
                f"status must be one of {sorted(VALID_STATUSES)}, "
                f"got {status!r}",
            ))

        # I13: output non-empty string.
        output = row.get("output")
        if not isinstance(output, str) or output == "":
            violations.append(Violation(
                "I13", "error", f"{path}.output",
                f"output must be a non-empty string, got {output!r}",
            ))

        # I14: value typing vs status.
        if "value" in row and isinstance(status, str):
            value = row["value"]
            if status == "ok":
                # Sentinel must NOT be ok.
                if output == _SENTINEL_OUTPUT:
                    violations.append(Violation(
                        "I14", "error", f"{path}.value",
                        f"sentinel output {_SENTINEL_OUTPUT!r} cannot have "
                        f"status='ok'",
                    ))
                # value must be number (incl. bool? no — bool excluded) or
                # string and non-null.
                if value is None:
                    violations.append(Violation(
                        "I14", "error", f"{path}.value",
                        "value must be number or string when status='ok', "
                        "got null",
                    ))
                elif isinstance(value, bool):
                    violations.append(Violation(
                        "I14", "error", f"{path}.value",
                        "value must be number or string when status='ok', "
                        "got bool",
                    ))
                elif not isinstance(value, (int, float, str)):
                    violations.append(Violation(
                        "I14", "error", f"{path}.value",
                        f"value must be number or string when status='ok', "
                        f"got {type(value).__name__}",
                    ))
            elif status == "eval_err":
                # Per-output eval-err sentinel: must NOT use the
                # __sim_status__ output name (that name is reserved for
                # triple-level sentinels with status failed/running/
                # no_convergence). Value must be null.
                if output == _SENTINEL_OUTPUT:
                    violations.append(Violation(
                        "I14", "error", f"{path}.output",
                        f"status='eval_err' is per-output and must preserve "
                        f"the real output name, not the triple-level "
                        f"sentinel {_SENTINEL_OUTPUT!r}",
                    ))
                if value is not None:
                    violations.append(Violation(
                        "I14", "error", f"{path}.value",
                        f"value must be null when status='eval_err', "
                        f"got {type(value).__name__}: {value!r}",
                    ))
            elif status in VALID_STATUSES:
                # Non-ok / non-eval_err rows are triple-level sentinels;
                # value must be null.
                if value is not None:
                    violations.append(Violation(
                        "I14", "error", f"{path}.value",
                        f"value must be null when status={status!r}, "
                        f"got {type(value).__name__}: {value!r}",
                    ))

        # I15: point is non-negative int (and not bool).
        if "point" in row:
            point = row["point"]
            if isinstance(point, bool) or not isinstance(point, int):
                violations.append(Violation(
                    "I15", "error", f"{path}.point",
                    f"point must be a non-negative integer, got "
                    f"{type(point).__name__}: {point!r}",
                ))
            elif point < 0:
                violations.append(Violation(
                    "I15", "error", f"{path}.point",
                    f"point must be >= 0, got {point}",
                ))

        # I16: corner / test non-empty string.
        for fld in ("corner", "test"):
            if fld in row:
                v = row[fld]
                if not isinstance(v, str) or v == "":
                    violations.append(Violation(
                        "I16", "error", f"{path}.{fld}",
                        f"{fld} must be a non-empty string, got {v!r}",
                    ))

        # I17: sweep / corner_vars are dicts.
        for fld in ("sweep", "corner_vars"):
            if fld in row:
                v = row[fld]
                if not isinstance(v, dict):
                    violations.append(Violation(
                        "I17", "error", f"{path}.{fld}",
                        f"{fld} must be a JSON object, got "
                        f"{type(v).__name__}",
                    ))

        # I18: test_note is str or null.
        if "test_note" in row:
            tn = row["test_note"]
            if tn is not None and not isinstance(tn, str):
                violations.append(Violation(
                    "I18", "error", f"{path}.test_note",
                    f"test_note must be string or null, got "
                    f"{type(tn).__name__}",
                ))

        # W1: magic markers in corner_vars (keys starting with `_`).
        cvars = row.get("corner_vars")
        if isinstance(cvars, dict):
            for k in cvars.keys():
                if isinstance(k, str) and k.startswith("_"):
                    violations.append(Violation(
                        "W1", "warning", f"{path}.corner_vars.{k}",
                        f"magic marker key {k!r} in corner_vars (collector "
                        "soft-miss signal — see DECISIONS / §3 Bug D)",
                    ))


def _check_triple_coverage(results: list, violations: List[Violation]) -> None:
    """I1 — triple coverage.

    For each (point, corner, test) triple within this run:

    * Either >= 1 data row (status in {ok, eval_err}, output != '__sim_status__'), OR
    * Exactly one triple-level sentinel row (output == '__sim_status__',
      status in {failed, running, no_convergence}).
    * Never both, never neither.

    `eval_err` is per-output: it records that ONE output's expression
    failed on this corner while the test/corner is otherwise alive. From
    I1's perspective, an eval_err row is a measurement attempt (not a
    silent drop), so it counts as a data row for triple-coverage.

    Skips rows that aren't dicts or that lack the keys we'd partition on
    (those are flagged separately by I24 / per-field checks).
    """
    triples: dict = {}
    for idx, row in enumerate(results):
        if not isinstance(row, dict):
            continue
        try:
            key = (row["point"], row["corner"], row["test"])
        except KeyError:
            continue
        # Hashable check: skip if any field is unhashable (lists / dicts).
        try:
            hash(key)
        except TypeError:
            continue
        triples.setdefault(key, []).append((idx, row))

    for key, rows in triples.items():
        data_rows = [
            (i, r) for (i, r) in rows
            if r.get("status") in _DATA_ROW_STATUSES
               and r.get("output") != _SENTINEL_OUTPUT
        ]
        sentinels = [
            (i, r) for (i, r) in rows
            if r.get("output") == _SENTINEL_OUTPUT
        ]

        if data_rows and sentinels:
            sample_data = data_rows[0][0]
            sample_sent = sentinels[0][0]
            point, corner, test = key
            violations.append(Violation(
                "I1", "error",
                f"results[{sample_sent}]",
                f"triple (point={point}, corner={corner!r}, test={test!r}) "
                f"has both data rows (e.g. results[{sample_data}]) and a "
                f"sentinel row at results[{sample_sent}]; expected one or "
                f"the other, not both",
            ))
            continue

        if not data_rows and not sentinels:
            sample = rows[0][0]
            point, corner, test = key
            violations.append(Violation(
                "I1", "error",
                f"results[{sample}]",
                f"triple (point={point}, corner={corner!r}, test={test!r}) "
                f"has neither a data row nor a sentinel row",
            ))
            continue

        if sentinels and len(sentinels) > 1:
            point, corner, test = key
            indices = [i for (i, _r) in sentinels]
            violations.append(Violation(
                "I1", "error",
                f"results[{indices[0]}]",
                f"triple (point={point}, corner={corner!r}, test={test!r}) "
                f"has {len(sentinels)} sentinel rows at indices {indices}; "
                "expected exactly one",
            ))
            continue

        if sentinels:
            (i, r) = sentinels[0]
            status = r.get("status")
            if status not in {"failed", "running", "no_convergence"}:
                point, corner, test = key
                violations.append(Violation(
                    "I1", "error",
                    f"results[{i}].status",
                    f"sentinel row for triple (point={point}, "
                    f"corner={corner!r}, test={test!r}) has status "
                    f"{status!r}; expected one of "
                    "{failed, running, no_convergence}",
                ))


def _check_artifacts(artifacts: list, violations: List[Violation]) -> None:
    for idx, art in enumerate(artifacts):
        path = f"artifacts[{idx}]"
        if not isinstance(art, dict):
            violations.append(Violation(
                "I24", "error", path,
                f"artifact must be a JSON object, got {type(art).__name__}",
            ))
            continue

        for key in _ARTIFACT_REQUIRED:
            if key not in art:
                violations.append(Violation(
                    "I24", "error", f"{path}.{key}",
                    f"missing required field '{key}'",
                ))

        # I19: type enum.
        atype = art.get("type")
        if not isinstance(atype, str) or atype not in VALID_ARTIFACT_TYPES:
            violations.append(Violation(
                "I19", "error", f"{path}.type",
                f"artifact type must be one of {sorted(VALID_ARTIFACT_TYPES)}, "
                f"got {atype!r}",
            ))

        # I20: source enum.
        src = art.get("source")
        if not isinstance(src, str) or src not in VALID_ARTIFACT_SOURCES:
            violations.append(Violation(
                "I20", "error", f"{path}.source",
                f"artifact source must be one of "
                f"{sorted(VALID_ARTIFACT_SOURCES)}, got {src!r}",
            ))

        # I21: relative_path non-empty.
        rp = art.get("relative_path")
        if not isinstance(rp, str) or rp == "":
            violations.append(Violation(
                "I21", "error", f"{path}.relative_path",
                "artifact relative_path must be a non-empty string",
            ))

        # I22: created_at ISO 8601 with offset.
        ca = art.get("created_at")
        if ca is None:
            pass  # missing already flagged
        elif not isinstance(ca, str) or not _TIMESTAMP_RE.match(ca):
            violations.append(Violation(
                "I22", "error", f"{path}.created_at",
                f"created_at must match YYYY-MM-DDThh:mm:ss[+-]hh:mm, "
                f"got {ca!r}",
            ))


# ----------------------------------------------------------------------
# CLI bridging — module-level main for `python -m simkit.validate`
# ----------------------------------------------------------------------

def _format_violations(violations: List[Violation]) -> str:
    if not violations:
        return "OK: no violations."
    lines = []
    for v in violations:
        prefix = "ERROR" if v.severity == "error" else "WARN "
        loc = v.path or "<root>"
        lines.append(f"{prefix} {v.code} {loc}: {v.message}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    """Tiny ``python -m simkit.validate <path>`` entry. Mirrors ``pvt validate``."""
    args = sys.argv[1:] if argv is None else list(argv)
    if not args or args[0] in {"-h", "--help"}:
        print(
            "usage: python -m simkit.validate <path-to-run.json>",
            file=sys.stderr,
        )
        return 2
    path = Path(args[0])
    if not path.is_file():
        print(f"validate: not a file: {path}", file=sys.stderr)
        return 3
    violations = validate_dump_file(path)
    print(_format_violations(violations))
    if any(v.severity == "error" for v in violations):
        return 2
    if violations:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - module CLI
    sys.exit(main())
