"""GUI-side disk loaders + presentation adapters (Phase 4 Stage 3).

Pure Python — no Qt imports. The right-panel editors (Results /
Corners / Measures) take typed inputs; this module is the bridge
from on-disk `.pvtproject` / `.review.json` / `.union.json` /
`.measure.json` shapes into those inputs.

The three flavours of public surface:

* :func:`load_module` — one-shot project walker; returns a
  :class:`LoadedModule` snapshot that ``MainWindow`` hands to
  :class:`simkit.gui.tree_model.ProjectTreeModel.populate` and to the
  Results / Corners / Measures controllers downstream.
* :func:`union_to_editor_rows` / :func:`editor_rows_to_union_rows` —
  flat-dict adapters between :class:`simkit.union.Union` and the
  presentation shape :class:`~simkit.gui.views.corners_editor.CornersEditor`
  consumes. Round-trip is faithful for single-axis unions; multi-axis
  vars go through ``extra_vars`` (lossy by design).
* :func:`load_bundle_for_editor` — one-shot bundle + templates +
  signal-groups walker, returns a triple ready to feed into
  :class:`~simkit.gui.views.measures_editor.MeasuresEditor`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, TYPE_CHECKING

import duckdb

from simkit.db import connect
from simkit.measure_bundle import (
    MEASURE_FILE_SUFFIX,
    resolve_measurements_dir,
    resolve_signal_groups_dir,
    resolve_templates_dir,
)
from simkit.project import _parse_pvtproject, PvtProject
from simkit.signal_group import (
    SIGNAL_GROUP_FILE_SUFFIX,
    SignalGroup,
    SignalGroupError,
    load_signal_group,
)
from simkit.template import (
    TEMPLATE_FILE_SUFFIX,
    Template,
    TemplateError,
    load_template,
)
from simkit.union import (
    ModelEntry,
    Union,
    UnionRow,
    UnionValidationError,
)

if TYPE_CHECKING:
    pass


# Default sidecar dirs under a `.pvtproject` parent. Match what
# ``simkit.project`` advertises but the loader does not import — the
# project file is the only authority on whether these exist.
# Bundles live under the project's ``measurementsDir`` (resolved via
# ``resolve_measurements_dir``), not a fixed subdir — CLI and GUI must agree.
_REVIEWS_SUBDIR = "reviews"
_UNIONS_SUBDIR = "unions"
_DB_FILENAME = "simkit.duckdb"


@dataclass(frozen=True)
class LoadedReview:
    review_path: Path
    review_name: str
    item_count: int
    # When the .review.json fails to parse (invalid JSON, missing schema, etc),
    # parse_error carries the human-readable cause + the tree label flags it
    # visibly so the user knows their on-disk edit is broken.
    parse_error: Optional[str] = None


@dataclass(frozen=True)
class LoadedBundle:
    bundle_path: Path
    bundle_name: str
    apply_count: int
    parse_error: Optional[str] = None


@dataclass(frozen=True)
class LoadedHistoryRun:
    run_id: str
    short_id: str
    timestamp: str
    label: str | None
    starred: bool
    milestone: str | None
    history_name: str | None


@dataclass(frozen=True)
class LoadedModule:
    project_path: Path
    project_root: Path
    project_name: str
    db_path: Path
    reviews: tuple[LoadedReview, ...]
    history: tuple[LoadedHistoryRun, ...]
    milestones: tuple[str, ...]
    union_default: Path | None
    bundle_default: Path | None
    measurements_dir: Path
    bundles: tuple[LoadedBundle, ...] = ()


# ---------------------------------------------------------------------------
# Project walker
# ---------------------------------------------------------------------------


def load_module(project_path: Path) -> LoadedModule:
    """Walk one ``.pvtproject`` and return a snapshot."""
    project_path = Path(project_path).expanduser().resolve()
    pvtproject = _parse_pvtproject(project_path)
    project_root = project_path.parent

    reviews = _scan_reviews(project_root)
    db_path = (pvtproject.db_root / _DB_FILENAME).resolve()
    history = _read_history(db_path, project_name=pvtproject.project)
    milestones = _distinct_milestones(history)
    union_default = _single_default(project_root / _UNIONS_SUBDIR, ".union.json")
    measurements_dir = resolve_measurements_dir(pvtproject)
    bundle_default = _single_default(measurements_dir, MEASURE_FILE_SUFFIX)

    bundles = _scan_bundles(measurements_dir)

    return LoadedModule(
        project_path=project_path,
        project_root=project_root,
        project_name=pvtproject.project,
        db_path=db_path,
        reviews=reviews,
        history=history,
        milestones=milestones,
        union_default=union_default,
        bundle_default=bundle_default,
        measurements_dir=measurements_dir,
        bundles=bundles,
    )


def _scan_reviews(project_root: Path) -> tuple[LoadedReview, ...]:
    reviews_dir = project_root / _REVIEWS_SUBDIR
    if not reviews_dir.is_dir():
        return tuple()
    out: list[LoadedReview] = []
    for path in sorted(reviews_dir.glob("*.review.json")):
        name = path.name[: -len(".review.json")]
        count, err = _count_items_or_error(path)
        out.append(
            LoadedReview(
                review_path=path.resolve(),
                review_name=name,
                item_count=count,
                parse_error=err,
            )
        )
    return tuple(out)


def _scan_bundles(measurements_dir: Path) -> tuple[LoadedBundle, ...]:
    """Walk the measurements dir for *.measure.json files (mirror of _scan_reviews)."""
    bundles_dir = measurements_dir
    if not bundles_dir.is_dir():
        return tuple()
    out: list[LoadedBundle] = []
    for path in sorted(bundles_dir.glob(f"*{MEASURE_FILE_SUFFIX}")):
        name = path.name[: -len(MEASURE_FILE_SUFFIX)]
        count, err = _count_apply_or_error(path)
        out.append(
            LoadedBundle(
                bundle_path=path.resolve(),
                bundle_name=name,
                apply_count=count,
                parse_error=err,
            )
        )
    return tuple(out)


def _count_apply_or_error(bundle_path: Path) -> tuple[int, Optional[str]]:
    try:
        with bundle_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except OSError as exc:
        return 0, f"cannot read: {exc}"
    except json.JSONDecodeError as exc:
        return 0, f"invalid JSON: {exc.msg} (line {exc.lineno})"
    if not isinstance(data, dict):
        return 0, "top-level must be a JSON object"
    apply_list = data.get("apply")
    if not isinstance(apply_list, list):
        return 0, "missing or invalid 'apply' array"
    return len(apply_list), None


@dataclass(frozen=True)
class SpecWriteResult:
    """Outcome of writing a spec back into a project's measure bundles.

    ``status`` is one of:
      * ``"written"``   — spec set on exactly one entry; ``bundle_path`` set.
      * ``"no_match"``  — no apply entry across the project produces ``output``.
      * ``"ambiguous"`` — more than one entry produces ``output``; not written.
    """
    status: str
    bundle_path: Optional[Path]
    detail: str


def _output_match_index(entry: dict, output: str) -> Optional[int]:
    """How (if at all) an apply entry produces ``output``.

    Returns ``-1`` for an ``output_name`` (single-output) match, the
    list index for an ``output_names`` (sweep) match, or ``None``.
    """
    if entry.get("output_name") == output:
        return -1
    names = entry.get("output_names")
    if isinstance(names, list) and output in names:
        return names.index(output)
    return None


def set_spec_in_project_bundles(
    bundle_paths: Iterable[Path], output: str, spec: Optional[str]
) -> SpecWriteResult:
    """Write ``spec`` onto the apply entry that produces ``output``.

    Scans every bundle for an apply entry naming ``output`` (via
    ``output_name`` or sweep ``output_names``). Writes only when exactly
    one entry matches — 0 or >1 are reported back untouched so the caller
    can tell the user. An empty/``None`` spec clears the field instead.

    Template entries with no explicit output name cannot be matched here
    (the name comes from the template); those count as ``no_match``.
    """
    spec_clean = (spec or "").strip() or None
    matches: list[tuple[Path, dict, dict, int]] = []
    for path in bundle_paths:
        try:
            with Path(path).open("r", encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(doc, dict) or not isinstance(doc.get("apply"), list):
            continue
        for entry in doc["apply"]:
            if not isinstance(entry, dict):
                continue
            idx = _output_match_index(entry, output)
            if idx is not None:
                matches.append((Path(path), doc, entry, idx))

    if not matches:
        return SpecWriteResult(
            "no_match", None,
            f"no bundle entry produces output {output!r}",
        )
    if len(matches) > 1:
        paths = ", ".join(sorted({m[0].name for m in matches}))
        return SpecWriteResult(
            "ambiguous", None,
            f"{len(matches)} entries produce {output!r} ({paths})",
        )

    path, doc, entry, idx = matches[0]
    if idx == -1:
        if spec_clean is None:
            entry.pop("spec", None)
        else:
            entry["spec"] = spec_clean
    else:
        names = entry.get("output_names") or []
        specs = list(entry.get("specs") or [None] * len(names))
        # Normalise length in case `specs` drifted from `output_names`.
        specs = (specs + [None] * len(names))[: len(names)]
        specs[idx] = spec_clean
        if any(s is not None for s in specs):
            entry["specs"] = specs
        else:
            entry.pop("specs", None)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    verb = "cleared spec on" if spec_clean is None else "wrote spec to"
    return SpecWriteResult("written", path, f"{verb} {path.name}")


def snapshot_to_bundle_dict(
    snapshot: dict,
    *,
    name: str,
    project: str,
    testbench_id: str,
) -> dict:
    """Convert a ``pvt_measure_pull`` snapshot into a `.measure.json` bundle.

    Each Outputs row becomes either:
      * a ``raw_expression`` apply entry (Type=expr — named measurements)
      * a signal-tap entry (Type=net — anonymous, save/plot flags only).
        Currently encoded as raw_expression too so the round-trip stays
        lossless; template-aware reverse engineering (P3B v2 deferred)
        will eventually fold signal taps into a SignalGroup.

    Result is ready for `simkit.measure_bundle.load_measure_bundle` and
    `MeasuresEditor.load_bundle`.
    """
    rows = snapshot.get("rows") or []
    test_name = snapshot.get("test") or "Test"
    apply_list: list[dict] = []
    anon_counter = 0
    for row in rows:
        expr = row.get("expression") or ""
        if not expr:
            continue
        output_name = row.get("name") or ""
        if not output_name:
            anon_counter += 1
            output_name = f"sigtap_{anon_counter}"
        # Sanitize: output_name regex requires ^[A-Za-z_][A-Za-z0-9_]*$
        cleaned = "".join(
            (ch if ch.isalnum() or ch == "_" else "_") for ch in output_name
        )
        if not cleaned or not (cleaned[0].isalpha() or cleaned[0] == "_"):
            cleaned = "_" + cleaned
        entry: dict = {
            "raw_expression": expr,
            "output_name": cleaned,
            "plot": bool(row.get("plot", False)),
            "save": bool(row.get("save", False)),
            "eval_type": "point",
        }
        spec = row.get("spec") or ""
        if spec:
            entry["spec"] = spec
        apply_list.append(entry)
    return {
        "measure_schema_version": 1,
        "name": name,
        "project": project,
        "testbench_id": testbench_id,
        "test_name": test_name,
        "apply": apply_list,
    }


def _count_items_or_error(review_path: Path) -> tuple[int, Optional[str]]:
    """Plain JSON parse → (item_count, None) on success, (0, error_msg) on
    failure. The full review loader cross-resolves union/bundle paths which
    we do NOT want to do here — the tree only needs the item count for the
    display label.
    """
    try:
        with review_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except OSError as exc:
        return 0, f"cannot read: {exc}"
    except json.JSONDecodeError as exc:
        return 0, f"invalid JSON: {exc.msg} (line {exc.lineno})"
    if not isinstance(data, dict):
        return 0, "top-level must be a JSON object"
    items = data.get("items")
    if not isinstance(items, list):
        return 0, "missing or invalid 'items' array"
    return len(items), None


def _read_history(
    db_path: Path, *, project_name: str
) -> tuple[LoadedHistoryRun, ...]:
    if not db_path.is_file():
        return tuple()
    con: duckdb.DuckDBPyConnection | None = None
    try:
        con = connect(db_path, read_only=True)
        # Discover columns so this query keeps working against
        # pre-v3 / pre-v4 DBs that don't have ``starred`` / ``milestone``.
        cols = {
            r[1] for r in con.execute("PRAGMA table_info('runs')").fetchall()
        }
        has_starred = "starred" in cols
        has_milestone = "milestone" in cols
        select_parts = [
            "run_id",
            "CAST(timestamp AS VARCHAR) AS ts",
            "label",
            "history_name",
        ]
        select_parts.append("starred" if has_starred else "FALSE AS starred")
        select_parts.append(
            "milestone" if has_milestone else "NULL AS milestone"
        )
        sql = (
            f"SELECT {', '.join(select_parts)} FROM runs "
            "WHERE project_id = ? "
            "ORDER BY timestamp DESC, run_id"
        )
        rows = con.execute(sql, [project_name]).fetchall()
    except (duckdb.Error, OSError):
        return tuple()
    finally:
        if con is not None:
            try:
                con.close()
            except duckdb.Error:
                pass

    out: list[LoadedHistoryRun] = []
    for run_id, ts, label, history_name, starred, milestone in rows:
        out.append(
            LoadedHistoryRun(
                run_id=str(run_id),
                short_id=str(run_id)[:8],
                timestamp=str(ts) if ts is not None else "",
                label=label if label else None,
                starred=bool(starred),
                milestone=milestone if milestone else None,
                history_name=history_name if history_name else None,
            )
        )
    return tuple(out)


def _distinct_milestones(
    history: tuple[LoadedHistoryRun, ...],
) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for run in history:
        if run.milestone:
            seen.setdefault(run.milestone, None)
    return tuple(seen.keys())


def _single_default(dir_path: Path, suffix: str) -> Path | None:
    # Return a path only if exactly one candidate sits in the dir — any
    # ambiguity must be resolved by an explicit user pick.
    if not dir_path.is_dir():
        return None
    matches = sorted(dir_path.glob(f"*{suffix}"))
    if len(matches) == 1:
        return matches[0].resolve()
    return None


# ---------------------------------------------------------------------------
# Union <-> editor-row adapters
# ---------------------------------------------------------------------------


# Column keys recognised by ``CornersEditor.load_union``. Kept as a tuple
# rather than importing from corners_editor to keep this module Qt-free.
_NAMED_VAR_COLS = ("process", "temperature", "vdd")


def union_to_editor_rows(union: Union) -> list[dict]:
    """Convert a :class:`Union` to the flat-dict shape the editor consumes."""
    out: list[dict] = []
    for row in union.rows:
        named: dict[str, str] = {}
        extras: list[str] = []
        for var_name, values in row.vars.items():
            target_col = _MAESTRO_TO_COLUMN.get(var_name.lower())
            if target_col is not None and len(values) == 1 and target_col not in named:
                named[target_col] = values[0]
                continue
            if len(values) == 1:
                extras.append(f"{var_name}={values[0]}")
            else:
                extras.append(f"{var_name}={','.join(values)}")
        model_file_text = ""
        # Process variation in real Maestro setups is almost always expressed
        # as model.section (tt/ss/ff/sf/fs/...) rather than a var named
        # "process". When no `process` var exists, fall back to the first
        # model's section so the process column shows what the user expects.
        section_for_process = ""
        if row.models:
            first = row.models[0]
            # Show the absolute path (what the sim actually loads and what
            # the model_file existence check resolves). `file` alone is a
            # bare model name and would lose the path on round-trip (SFE-73).
            model_file_text = first.file_abs or first.file
            if first.section:
                section_for_process = ",".join(first.section)
            if len(row.models) > 1:
                tail_files = ", ".join(m.file for m in row.models[1:])
                extras.append(f"models[1..]={tail_files}")
                # Surface sections of secondary models too (multi-model rows
                # are rare but if present they encode multi-process sweeps).
                for k, m in enumerate(row.models[1:], start=1):
                    if m.section:
                        extras.append(
                            f"model[{k}].section={','.join(m.section)}"
                        )
        process_text = named.get("process") or section_for_process
        entry: dict = {
            "row_name": row.row_name,
            "process": process_text,
            "temperature": named.get("temperature", ""),
            "vdd": named.get("vdd", ""),
            "model_file": model_file_text,
            "extra_vars": "; ".join(extras),
            "_enabled": row.enabled,
        }
        out.append(entry)
    return out


# Maestro-side var names map to editor columns. The match is case-insensitive
# because Maestro var-names are often UPPER (TEMP, VDD) while the spec
# columns use lower-case.
_MAESTRO_TO_COLUMN = {
    "process": "process",
    "temp": "temperature",
    "temperature": "temperature",
    "vdd": "vdd",
    "supply": "vdd",
}


def editor_row_to_union_row(raw: dict, *, where: str = "row") -> UnionRow:
    """Translate a single flat editor row dict into a :class:`UnionRow`.

    Single source of truth for the flat-row → union semantics:

    * ``process`` is a comma-separated model.section sweep (``tt,ss,ff``);
    * ``temperature`` / ``vdd`` are single-valued named vars;
    * ``extra_vars`` (``K=v1,v2; K2=v``) becomes one var each, a
      comma-list value being a sweep axis.

    Used both by :func:`editor_rows_to_union_rows` (push) and by the
    Corners-editor expansion preview (G-9), so the two cannot drift.
    Raises :class:`UnionValidationError` on a row that can't be a union row.
    """
    row_name = (raw.get("row_name") or "").strip()
    if not row_name:
        raise UnionValidationError(f"{where}: missing row_name")
    vars_dict: dict[str, tuple[str, ...]] = {}
    # Process column maps to model.section, not to a `process` var —
    # this is how Maestro actually encodes process variation. Comma-
    # separated input (e.g. "tt,ss,ff") becomes a multi-section sweep.
    process_raw = (raw.get("process") or "").strip()
    process_sections: tuple[str, ...] = ()
    if process_raw:
        process_sections = tuple(
            p.strip() for p in process_raw.split(",") if p.strip()
        )
    temperature = (raw.get("temperature") or "").strip()
    if temperature:
        vars_dict["temp"] = (temperature,)
    vdd = (raw.get("vdd") or "").strip()
    if vdd:
        vars_dict["vdd"] = (vdd,)
    extras_text = raw.get("extra_vars") or ""
    for extra in parse_extra_vars(extras_text):
        k, values = extra
        # Skip the synthetic "models[1..]" / "model[N].section" hints —
        # those are presentation-only echos from union_to_editor_rows.
        if k.startswith("models[") or k.startswith("model["):
            continue
        vars_dict[k] = values

    model_file = (raw.get("model_file") or "").strip()
    models: tuple[ModelEntry, ...] = ()
    if model_file:
        # If the editor put a section in the process column, carry it
        # into the model entry. Otherwise leave section as a single empty
        # string (legacy behavior — Maestro will use whatever default the
        # model file ships with).
        section = process_sections if process_sections else ("",)
        # `model_file` is the absolute path the user sees in the editor.
        # `file` must stay a bare model name (axlPutModel keys on it);
        # `file_abs` carries the path push needs for axlSetModelFile so
        # Spectre does not emit `include ""` (SFE-73). A bare basename
        # has no path to preserve, so file_abs stays None there.
        has_path = "/" in model_file
        models = (
            ModelEntry(
                file=Path(model_file).name if has_path else model_file,
                block="Global",
                test="All",
                section=section,
                file_abs=model_file if has_path else None,
            ),
        )

    if not vars_dict and not models:
        raise UnionValidationError(
            f"{where}: row {row_name!r} has no vars and no model_file"
        )

    # Derive the sweep flags from value cardinality. On the push path
    # these are re-computed at .union.json load time (a multi-value cell
    # round-trips as a JSON array), but an in-memory UnionRow needs them
    # set explicitly so simkit.union.explode names sub-corners correctly.
    sweep_var_keys = frozenset(
        k for k, v in vars_dict.items() if len(v) > 1
    )
    sweep_model_indices = frozenset(
        i for i, m in enumerate(models) if len(m.section) > 1
    )

    return UnionRow(
        row_name=row_name,
        vars=vars_dict,
        models=models,
        sweep_var_keys=sweep_var_keys,
        sweep_model_indices=sweep_model_indices,
        enabled=bool(raw.get("_enabled", True)),
    )


def editor_rows_to_union_rows(
    rows: list[dict],
    *,
    name: str,
    project: str,
    testbench_id: str,
) -> Union:
    """Reverse adapter — used by 'Send to Maestro'."""
    if not rows:
        raise UnionValidationError("editor_rows_to_union_rows: rows is empty")

    union_rows: list[UnionRow] = []
    for i, raw in enumerate(rows):
        union_rows.append(editor_row_to_union_row(
            raw, where=f"editor_rows_to_union_rows: row {i + 1}",
        ))

    seen: set[str] = set()
    for r in union_rows:
        if r.row_name in seen:
            raise UnionValidationError(
                f"editor_rows_to_union_rows: duplicate row_name {r.row_name!r}"
            )
        seen.add(r.row_name)

    return Union(
        union_schema_version=1,
        name=name,
        project=project,
        testbench_id=testbench_id,
        rows=tuple(union_rows),
    )


def parse_extra_vars(text: str) -> list[tuple[str, tuple[str, ...]]]:
    """Parse the editor's ``extra_vars`` semi-colon list back into pairs."""
    out: list[tuple[str, tuple[str, ...]]] = []
    for piece in (text or "").split(";"):
        piece = piece.strip()
        if not piece or "=" not in piece:
            continue
        k, v = piece.split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        if "," in v:
            values = tuple(s.strip() for s in v.split(",") if s.strip())
        else:
            values = (v,)
        out.append((k, values))
    return out


# ---------------------------------------------------------------------------
# Measure-bundle loader
# ---------------------------------------------------------------------------


def load_bundle_for_editor(
    bundle_path: Path,
    project_root: Path,
) -> tuple[dict, dict[str, Template], dict[str, SignalGroup]]:
    """Read a bundle + walk templates / signal-groups dirs.

    Returns ``(raw_bundle_dict, templates_by_name, signal_groups_by_name)``
    ready to feed into the measures editor surface. ``raw_bundle_dict`` is
    the literal JSON shape (matches ``MeasuresEditor.load_bundle``). The
    other two dicts include every sidecar under the resolved
    templatesDir / signalGroupsDir — the editor's picker dropdowns surface
    all of them, not just the ones a given bundle currently references.
    """
    bundle_path = Path(bundle_path).expanduser().resolve()
    with bundle_path.open("r", encoding="utf-8") as f:
        raw_bundle = json.load(f)
    if not isinstance(raw_bundle, dict):
        raise ValueError(
            f"{bundle_path}: top-level must be a JSON object"
        )

    project_root = Path(project_root).expanduser().resolve()
    pvtproject_path = project_root / ".pvtproject"
    if pvtproject_path.is_file():
        pvtproject = _parse_pvtproject(pvtproject_path)
        templates_dir = resolve_templates_dir(pvtproject)
        signal_groups_dir = resolve_signal_groups_dir(pvtproject)
    else:
        # Caller supplied a project root without a .pvtproject — fall back
        # to the default subdir layout. Keeps the loader testable on bare
        # temp dirs.
        templates_dir = project_root / "templates"
        signal_groups_dir = project_root / "signal_groups"

    templates = _load_templates_dir(templates_dir)
    signal_groups = _load_signal_groups_dir(signal_groups_dir)
    return raw_bundle, templates, signal_groups


def _load_templates_dir(templates_dir: Path) -> dict[str, Template]:
    if not templates_dir.is_dir():
        return {}
    out: dict[str, Template] = {}
    for path in sorted(templates_dir.glob(f"*{TEMPLATE_FILE_SUFFIX}")):
        try:
            tmpl = load_template(path)
        except TemplateError:
            continue
        out[tmpl.name] = tmpl
    return out


def _load_signal_groups_dir(
    signal_groups_dir: Path,
) -> dict[str, SignalGroup]:
    if not signal_groups_dir.is_dir():
        return {}
    out: dict[str, SignalGroup] = {}
    for path in sorted(signal_groups_dir.glob(f"*{SIGNAL_GROUP_FILE_SUFFIX}")):
        try:
            sg = load_signal_group(path)
        except SignalGroupError:
            continue
        out[sg.name] = sg
    return out
