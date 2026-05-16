"""`.measure.json` sidecar loader for measurement bundles.

Implements the Phase 3B §3.4 contract (docs/phase3b_measure_template_spec.md).
Pure-Python, stdlib-only. A measurement bundle is a named application:
templates × signal-group × test → N concrete output rows.

Resolves template + signal-group references against the project's
``templatesDir`` and ``signalGroupsDir`` (defaults `./templates`, `./signal_groups`
under the `.pvtproject` directory; per-project override keys per DECISIONS #41).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from simkit.errors import SimkitError
from simkit.project import PvtProject
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


MEASURE_FILE_SUFFIX = ".measure.json"

_MEASURE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_ALIAS_SUFFIX_RE = re.compile(r"^[A-Za-z0-9_]*$")

_SUPPORTED_MEASURE_SCHEMA_VERSIONS = frozenset({1, 2})
# v1.2 features that require schema_version >= 2. v1 bundles that touch
# these fields are rejected with a "bump to 2" error.
_V2_ONLY_APPLY_FIELDS = frozenset({
    "output_name",
    "param_sweep",
    "output_names",
    "raw_expression",
    "plot",
    "save",
    "eval_type",
    # v1.3 spec passthrough — still under schema_version: 2 (additive).
    "spec",
})

_DEFAULT_TEMPLATES_DIR = "templates"
_DEFAULT_SIGNAL_GROUPS_DIR = "signal_groups"
_DEFAULT_MEASUREMENTS_DIR = "measurements"


class MeasureBundleError(SimkitError):
    """Base class for `.measure.json` loader errors."""


class MeasureBundleSchemaVersionError(MeasureBundleError):
    """A sidecar declared a ``measure_schema_version`` the loader does not support."""


class MeasureBundleMalformedError(MeasureBundleError):
    """A sidecar is unreadable / not parseable as JSON / not a JSON object."""


class MeasureBundleLoadError(MeasureBundleError):
    """A sidecar parsed cleanly but failed schema validation per spec §3.4."""


@dataclass(frozen=True)
class MeasureApply:
    # Either ``template`` is set (the entry is a template-driven apply) or
    # ``raw_expression`` is set (v1.2 (f) — a one-off literal expression).
    # The loader enforces exactly-one-of; downstream code dispatches on
    # ``template is None``.
    template: Optional[Template] = None
    signal_group: Optional[SignalGroup] = None
    param_overrides: dict[str, str] = field(default_factory=dict)
    alias_suffix: str = ""
    output_name: Optional[str] = None
    # v1.2 (e) param-sweep: when set, the entry expands into N rows where N
    # is the length of every parallel array. Each row overrides exactly the
    # sweep key with its i-th value and is named by the i-th element of
    # output_names. v1.2 enforces a single sweep axis (one key in the dict).
    param_sweep: Optional[dict[str, tuple[str, ...]]] = None
    output_names: Optional[tuple[str, ...]] = None
    # v1.2 (f) raw_expression branch — only used when template is None.
    raw_expression: Optional[str] = None
    raw_plot: bool = True
    raw_save: bool = False
    raw_eval_type: str = "point"
    # v1.3 — Cadence-native spec string passthrough (e.g. "<100p", "> -140",
    # "range -150 -100"). SKILL push parses via evalstring + dispatches to
    # axlAddSpecToOutput's exclusive ?lt/?gt/?range/?min/?max/?tol keyword.
    # A single spec string applies uniformly to every row the entry yields
    # (sweep × signal_group expansion all share it).
    spec: Optional[str] = None


@dataclass(frozen=True)
class MeasureBundle:
    measure_schema_version: int
    name: str
    project: str
    testbench_id: str
    test_name: str
    apply: tuple[MeasureApply, ...]
    source_path: Path


def resolve_templates_dir(project: PvtProject) -> Path:
    return _resolve_project_dir(project, "templatesDir", _DEFAULT_TEMPLATES_DIR)


def resolve_signal_groups_dir(project: PvtProject) -> Path:
    return _resolve_project_dir(
        project, "signalGroupsDir", _DEFAULT_SIGNAL_GROUPS_DIR
    )


def resolve_measurements_dir(project: PvtProject) -> Path:
    return _resolve_project_dir(
        project, "measurementsDir", _DEFAULT_MEASUREMENTS_DIR
    )


def _resolve_project_dir(
    project: PvtProject, key: str, default: str
) -> Path:
    """Re-read the `.pvtproject` JSON for an optional override key.

    PvtProject's dataclass does not (yet) expose the Phase 3B dir keys, so we
    inspect the file directly. This mirrors `simkit.corners.resolve_unions_dir`
    and avoids bumping the PvtProject schema for what is an additive Phase 3B
    field set (per DECISIONS #41).
    """
    src = project.source_path
    raw_dir: Optional[str] = None
    try:
        with src.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        data = None
    if isinstance(data, dict):
        candidate = data.get(key)
        if isinstance(candidate, str) and candidate != "":
            raw_dir = candidate
    if raw_dir is None:
        raw_dir = default
    resolved = Path(raw_dir).expanduser()
    if not resolved.is_absolute():
        resolved = (src.parent / resolved).resolve()
    else:
        resolved = resolved.resolve()
    return resolved


def load_measure_bundle(
    path: Path | str, *, project: PvtProject
) -> MeasureBundle:
    p = Path(path).expanduser().resolve()

    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise MeasureBundleMalformedError(
            f"{p}: invalid JSON — {exc}"
        ) from exc
    except OSError as exc:
        raise MeasureBundleMalformedError(
            f"{p}: cannot read — {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise MeasureBundleMalformedError(
            f"{p}: top-level must be a JSON object, got {type(data).__name__}"
        )

    schema_version = _validate_schema_version(p, data)
    name = _validate_name(p, data)
    project_field = _assert_str(p, data, "project")
    testbench_id = _assert_str(p, data, "testbench_id")
    test_name = _assert_str(p, data, "test_name")

    if project_field != project.project:
        raise MeasureBundleLoadError(
            f"{p}: 'project' {project_field!r} does not match enclosing "
            f"PvtProject.project {project.project!r}"
        )

    templates_dir = resolve_templates_dir(project)
    signal_groups_dir = resolve_signal_groups_dir(project)

    apply_entries = _validate_apply(
        p, data, templates_dir, signal_groups_dir,
        schema_version=schema_version,
    )

    return MeasureBundle(
        measure_schema_version=schema_version,
        name=name,
        project=project_field,
        testbench_id=testbench_id,
        test_name=test_name,
        apply=apply_entries,
        source_path=p,
    )


# ---------------------------------------------------------------------------
# Field validators
# ---------------------------------------------------------------------------


def _validate_schema_version(path: Path, data: dict) -> int:
    if "measure_schema_version" not in data:
        raise MeasureBundleSchemaVersionError(
            f"{path}: missing required field 'measure_schema_version'"
        )
    raw = data["measure_schema_version"]
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise MeasureBundleSchemaVersionError(
            f"{path}: 'measure_schema_version' must be an integer"
        )
    if raw not in _SUPPORTED_MEASURE_SCHEMA_VERSIONS:
        raise MeasureBundleSchemaVersionError(
            f"{path}: measure_schema_version {raw} not supported "
            f"(supported: {sorted(_SUPPORTED_MEASURE_SCHEMA_VERSIONS)})"
        )
    return raw


def _validate_name(path: Path, data: dict) -> str:
    name = _assert_str(path, data, "name")
    if not _MEASURE_NAME_RE.match(name):
        raise MeasureBundleLoadError(
            f"{path}: 'name' {name!r} does not match ^[a-z][a-z0-9_]*$"
        )
    basename = path.name
    if not basename.endswith(MEASURE_FILE_SUFFIX):
        raise MeasureBundleLoadError(
            f"{path}: filename must end with '{MEASURE_FILE_SUFFIX}' "
            f"(got {basename!r})"
        )
    expected = basename[: -len(MEASURE_FILE_SUFFIX)]
    if expected != name:
        raise MeasureBundleLoadError(
            f"{path}: 'name' {name!r} must equal filename basename "
            f"{expected!r}"
        )
    return name


def _assert_str(path: Path, data: dict, key: str) -> str:
    if key not in data:
        raise MeasureBundleLoadError(
            f"{path}: missing required field {key!r}"
        )
    value = data[key]
    if not isinstance(value, str) or value == "":
        raise MeasureBundleLoadError(
            f"{path}: {key!r} must be a non-empty string"
        )
    return value


def _validate_apply(
    path: Path,
    data: dict,
    templates_dir: Path,
    signal_groups_dir: Path,
    *,
    schema_version: int,
) -> tuple[MeasureApply, ...]:
    if "apply" not in data:
        raise MeasureBundleLoadError(
            f"{path}: missing required field 'apply'"
        )
    raw = data["apply"]
    if not isinstance(raw, list):
        raise MeasureBundleLoadError(f"{path}: 'apply' must be a JSON array")
    if len(raw) == 0:
        raise MeasureBundleLoadError(f"{path}: 'apply' must be non-empty")

    out: list[MeasureApply] = []
    for i, raw_entry in enumerate(raw):
        entry = _validate_apply_entry(
            path, i, raw_entry, templates_dir, signal_groups_dir,
            schema_version=schema_version,
        )
        out.append(entry)
    return tuple(out)


def _validate_apply_entry(
    path: Path,
    idx: int,
    raw: object,
    templates_dir: Path,
    signal_groups_dir: Path,
    *,
    schema_version: int,
) -> MeasureApply:
    where = f"{path}: apply[{idx}]"
    if not isinstance(raw, dict):
        raise MeasureBundleLoadError(f"{where}: must be a JSON object")

    # v1.2 schema gate — reject v2-only fields when the bundle declares v1.
    if schema_version < 2:
        used_v2 = [k for k in raw.keys() if k in _V2_ONLY_APPLY_FIELDS]
        if used_v2:
            raise MeasureBundleLoadError(
                f"{where}: field(s) {sorted(used_v2)} require "
                f"'measure_schema_version': 2 (got {schema_version})"
            )

    has_template = "template" in raw
    has_raw = "raw_expression" in raw
    if has_template and has_raw:
        raise MeasureBundleLoadError(
            f"{where}: cannot set both 'template' and 'raw_expression' "
            f"(an apply entry is exactly one kind)"
        )
    if not has_template and not has_raw:
        raise MeasureBundleLoadError(
            f"{where}: missing 'template' or 'raw_expression'"
        )
    if has_raw:
        return _validate_raw_apply_entry(where, raw)

    tmpl_name = raw["template"]
    if not isinstance(tmpl_name, str) or tmpl_name == "":
        raise MeasureBundleLoadError(
            f"{where}: 'template' must be a non-empty string"
        )

    tmpl_path = templates_dir / f"{tmpl_name}{TEMPLATE_FILE_SUFFIX}"
    if not tmpl_path.is_file():
        raise MeasureBundleLoadError(
            f"{where}: template {tmpl_name!r} not found at {tmpl_path}"
        )
    try:
        template = load_template(tmpl_path)
    except TemplateError as exc:
        raise MeasureBundleLoadError(
            f"{where}: failed to load template {tmpl_name!r} — {exc}"
        ) from exc

    # v1.2 (c): if 'signal_group' is omitted, infer from template — equivalent
    # to explicit null when the template has no signal-kind param.
    sg_implicit = "signal_group" not in raw
    sg_field = None if sg_implicit else raw["signal_group"]
    signal_group: Optional[SignalGroup]
    signal_param = template.signal_param()

    if sg_field is None:
        # M4 case f: template has signal param but bundle gave null / omitted.
        if signal_param is not None:
            if sg_implicit:
                raise MeasureBundleLoadError(
                    f"{where}: missing 'signal_group' — template {tmpl_name!r} "
                    f"declares signal-kind param {signal_param.key!r}"
                )
            raise MeasureBundleLoadError(
                f"{where}: 'signal_group' is null but template {tmpl_name!r} "
                f"declares signal-kind param {signal_param.key!r}; "
                f"signal_group is required"
            )
        signal_group = None
    elif isinstance(sg_field, str):
        if sg_field == "":
            raise MeasureBundleLoadError(
                f"{where}: 'signal_group' must be a non-empty string or null"
            )
        # M4 case e: template has no signal param but bundle gave a group.
        if signal_param is None:
            raise MeasureBundleLoadError(
                f"{where}: 'signal_group' {sg_field!r} given but template "
                f"{tmpl_name!r} has no signal-kind param; must be null"
            )
        sg_path = signal_groups_dir / f"{sg_field}{SIGNAL_GROUP_FILE_SUFFIX}"
        if not sg_path.is_file():
            raise MeasureBundleLoadError(
                f"{where}: signal_group {sg_field!r} not found at {sg_path}"
            )
        try:
            signal_group = load_signal_group(sg_path)
        except SignalGroupError as exc:
            raise MeasureBundleLoadError(
                f"{where}: failed to load signal_group {sg_field!r} — {exc}"
            ) from exc
    else:
        raise MeasureBundleLoadError(
            f"{where}: 'signal_group' must be a string or null "
            f"(got {type(sg_field).__name__})"
        )

    param_overrides = _validate_param_overrides(where, raw, template)
    alias_suffix = _validate_alias_suffix(where, raw)
    output_name = _validate_output_name(where, raw, template)
    sweep, sweep_names = _validate_param_sweep(
        where, raw, template, param_overrides, output_name
    )
    spec = _validate_spec(where, raw)

    return MeasureApply(
        template=template,
        signal_group=signal_group,
        param_overrides=param_overrides,
        alias_suffix=alias_suffix,
        output_name=output_name,
        param_sweep=sweep,
        output_names=sweep_names,
        spec=spec,
    )


def _validate_param_overrides(
    where: str, raw: dict, template: Template
) -> dict[str, str]:
    raw_overrides = raw.get("param_overrides", {})
    if not isinstance(raw_overrides, dict):
        raise MeasureBundleLoadError(
            f"{where}: 'param_overrides' must be a JSON object"
        )

    declared_keys = {p.key for p in template.params}
    overrides: dict[str, str] = {}
    for k, v in raw_overrides.items():
        if not isinstance(k, str):
            raise MeasureBundleLoadError(
                f"{where}: 'param_overrides' keys must be strings"
            )
        if k not in declared_keys:
            raise MeasureBundleLoadError(
                f"{where}: 'param_overrides' key {k!r} is not declared in "
                f"template {template.name!r} params"
            )
        if not isinstance(v, str):
            raise MeasureBundleLoadError(
                f"{where}: 'param_overrides[{k!r}]' must be a string "
                f"(got {type(v).__name__})"
            )
        overrides[k] = v

    # M4 case g: every non-signal param with no default must be overridden.
    # v1.2 (e) — params declared in param_sweep also count as supplied,
    # since the sweep array provides one value per iteration.
    sweep_keys: set[str] = set()
    if isinstance(raw.get("param_sweep"), dict):
        sweep_keys = {k for k in raw["param_sweep"].keys() if isinstance(k, str)}
    missing: list[str] = []
    for p in template.params:
        if p.kind == "signal":
            continue
        if p.default is not None:
            continue
        if p.key in overrides or p.key in sweep_keys:
            continue
        missing.append(p.key)
    if missing:
        raise MeasureBundleLoadError(
            f"{where}: template {template.name!r} requires "
            f"param_overrides for: {', '.join(missing)} "
            f"(no default declared)"
        )

    return overrides


def _validate_alias_suffix(where: str, raw: dict) -> str:
    if "alias_suffix" not in raw:
        return ""
    value = raw["alias_suffix"]
    if not isinstance(value, str):
        raise MeasureBundleLoadError(
            f"{where}: 'alias_suffix' must be a string "
            f"(got {type(value).__name__})"
        )
    if not _ALIAS_SUFFIX_RE.match(value):
        raise MeasureBundleLoadError(
            f"{where}: 'alias_suffix' {value!r} does not match "
            f"^[A-Za-z0-9_]*$"
        )
    return value


_OUTPUT_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SIG_PLACEHOLDER = "${SIG}"

_RAW_APPLY_KNOWN_KEYS = frozenset({
    "raw_expression", "output_name", "plot", "save", "eval_type", "spec",
})
_RAW_EVAL_TYPES = frozenset({"point", "wave"})


def _validate_raw_apply_entry(where: str, raw: dict) -> MeasureApply:
    """v1.2 (f) — raw_expression apply entry.

    Bypasses templates: the rendered row's expression is the literal
    ``raw_expression`` string. Useful for one-off composite waves
    (e.g. ``rfEdgePhaseNoise(...)``) that don't match any builtin shape.
    """
    unknown = set(raw.keys()) - _RAW_APPLY_KNOWN_KEYS
    if unknown:
        raise MeasureBundleLoadError(
            f"{where}: raw_expression entry has unknown keys: "
            f"{sorted(unknown)}"
        )

    expr = raw["raw_expression"]
    if not isinstance(expr, str) or expr == "":
        raise MeasureBundleLoadError(
            f"{where}: 'raw_expression' must be a non-empty string"
        )

    if "output_name" not in raw:
        raise MeasureBundleLoadError(
            f"{where}: raw_expression entry requires 'output_name'"
        )
    output_name = raw["output_name"]
    if not isinstance(output_name, str) or output_name == "":
        raise MeasureBundleLoadError(
            f"{where}: 'output_name' must be a non-empty string"
        )
    if _SIG_PLACEHOLDER in output_name:
        raise MeasureBundleLoadError(
            f"{where}: raw_expression 'output_name' must not contain "
            f"${{SIG}} — no signal context in raw entries"
        )
    if not _OUTPUT_NAME_RE.match(output_name):
        raise MeasureBundleLoadError(
            f"{where}: 'output_name' {output_name!r} must match "
            f"^[A-Za-z_][A-Za-z0-9_]*$"
        )

    plot = _validate_bool(where, raw, "plot", default=True)
    save = _validate_bool(where, raw, "save", default=False)
    eval_type = raw.get("eval_type", "point")
    if eval_type not in _RAW_EVAL_TYPES:
        raise MeasureBundleLoadError(
            f"{where}: 'eval_type' must be one of "
            f"{sorted(_RAW_EVAL_TYPES)} (got {eval_type!r})"
        )
    spec = _validate_spec(where, raw)

    return MeasureApply(
        template=None,
        raw_expression=expr,
        output_name=output_name,
        raw_plot=plot,
        raw_save=save,
        raw_eval_type=eval_type,
        spec=spec,
    )


def _validate_bool(where: str, raw: dict, key: str, *, default: bool) -> bool:
    if key not in raw:
        return default
    v = raw[key]
    if not isinstance(v, bool):
        raise MeasureBundleLoadError(
            f"{where}: {key!r} must be true or false (got {type(v).__name__})"
        )
    return v


def _validate_output_name(
    where: str, raw: dict, template: Template
) -> Optional[str]:
    """v1.2 (a) — apply-entry-level output_name override.

    When present, the rendered output name is exactly this string (no
    short_alias / alias_suffix / basename concatenation). The literal
    ``${SIG}`` is the only placeholder; it expands to the signal basename
    at render time and is only legal when the template has a signal-kind
    param.
    """
    if "output_name" not in raw:
        return None
    value = raw["output_name"]
    if not isinstance(value, str) or value == "":
        raise MeasureBundleLoadError(
            f"{where}: 'output_name' must be a non-empty string"
        )
    has_sig = _SIG_PLACEHOLDER in value
    signal_param = template.signal_param()
    if has_sig and signal_param is None:
        raise MeasureBundleLoadError(
            f"{where}: 'output_name' uses ${{SIG}} but template "
            f"{template.name!r} has no signal-kind param"
        )
    # Strip the placeholder before character-set validation so e.g.
    # "Rtime_${SIG}" passes — the ${} braces are otherwise rejected.
    bare = value.replace(_SIG_PLACEHOLDER, "X") if has_sig else value
    if not _OUTPUT_NAME_RE.match(bare):
        raise MeasureBundleLoadError(
            f"{where}: 'output_name' {value!r} must match "
            f"^[A-Za-z_][A-Za-z0-9_]*$ (after ${{SIG}} substitution)"
        )
    return value


def _validate_param_sweep(
    where: str,
    raw: dict,
    template: Template,
    overrides: dict[str, str],
    output_name: Optional[str],
) -> tuple[Optional[dict[str, tuple[str, ...]]], Optional[tuple[str, ...]]]:
    """v1.2 (e) — single-axis param_sweep + parallel output_names list.

    An entry that declares ``param_sweep`` expands into N rendered rows
    where N is the length of the sweep's value array (and of the
    ``output_names`` array). The sweep key must:

    * be a declared template param that is *not* signal-kind;
    * not appear in ``param_overrides`` (no contradiction);
    * be exactly one key (multi-axis sweep is deferred to v1.3).
    """
    has_sweep = "param_sweep" in raw
    has_names = "output_names" in raw
    if has_sweep != has_names:
        raise MeasureBundleLoadError(
            f"{where}: 'param_sweep' and 'output_names' must appear "
            f"together (one without the other is invalid)"
        )
    if not has_sweep:
        return None, None

    if output_name is not None:
        raise MeasureBundleLoadError(
            f"{where}: 'output_name' and 'output_names' are mutually "
            f"exclusive — use the latter for swept entries"
        )

    sweep_raw = raw["param_sweep"]
    if not isinstance(sweep_raw, dict) or not sweep_raw:
        raise MeasureBundleLoadError(
            f"{where}: 'param_sweep' must be a non-empty JSON object"
        )
    if len(sweep_raw) != 1:
        raise MeasureBundleLoadError(
            f"{where}: 'param_sweep' must declare exactly one axis "
            f"(multi-axis is a future feature); got "
            f"{sorted(sweep_raw.keys())}"
        )
    sweep_key, sweep_values = next(iter(sweep_raw.items()))
    if not isinstance(sweep_key, str):
        raise MeasureBundleLoadError(
            f"{where}: 'param_sweep' key must be a string"
        )

    declared = {p.key: p for p in template.params}
    if sweep_key not in declared:
        raise MeasureBundleLoadError(
            f"{where}: 'param_sweep' key {sweep_key!r} is not declared "
            f"in template {template.name!r}"
        )
    if declared[sweep_key].kind == "signal":
        raise MeasureBundleLoadError(
            f"{where}: 'param_sweep' cannot sweep a signal-kind param "
            f"({sweep_key!r}); use a signal_group with multiple signals "
            f"instead"
        )
    if sweep_key in overrides:
        raise MeasureBundleLoadError(
            f"{where}: 'param_sweep' key {sweep_key!r} is also listed "
            f"in 'param_overrides' — pick one"
        )

    if not isinstance(sweep_values, list) or not sweep_values:
        raise MeasureBundleLoadError(
            f"{where}: 'param_sweep[{sweep_key!r}]' must be a non-empty "
            f"JSON array"
        )
    for j, v in enumerate(sweep_values):
        if not isinstance(v, str):
            raise MeasureBundleLoadError(
                f"{where}: 'param_sweep[{sweep_key!r}][{j}]' must be a "
                f"string (got {type(v).__name__})"
            )

    names_raw = raw["output_names"]
    if not isinstance(names_raw, list) or not names_raw:
        raise MeasureBundleLoadError(
            f"{where}: 'output_names' must be a non-empty JSON array"
        )
    if len(names_raw) != len(sweep_values):
        raise MeasureBundleLoadError(
            f"{where}: 'output_names' has {len(names_raw)} entries but "
            f"'param_sweep[{sweep_key!r}]' has {len(sweep_values)} "
            f"(parallel arrays must match length)"
        )
    signal_param = template.signal_param()
    for j, name in enumerate(names_raw):
        if not isinstance(name, str) or name == "":
            raise MeasureBundleLoadError(
                f"{where}: 'output_names[{j}]' must be a non-empty string"
            )
        if _SIG_PLACEHOLDER in name and signal_param is None:
            raise MeasureBundleLoadError(
                f"{where}: 'output_names[{j}]' uses ${{SIG}} but template "
                f"{template.name!r} has no signal-kind param"
            )
        bare = name.replace(_SIG_PLACEHOLDER, "X")
        if not _OUTPUT_NAME_RE.match(bare):
            raise MeasureBundleLoadError(
                f"{where}: 'output_names[{j}]' {name!r} must match "
                f"^[A-Za-z_][A-Za-z0-9_]*$ (after ${{SIG}} substitution)"
            )

    return ({sweep_key: tuple(sweep_values)}, tuple(names_raw))


# v1.3 — bundle-side spec validation. We accept the Cadence-native string
# forms verified live against fnxSession0 (see DECISIONS #45):
#   "<X"     → strict less-than           → SKILL ?lt
#   ">X"     → strict greater-than        → SKILL ?gt
#   "<=X"    → inclusive upper bound      → SKILL ?max
#   ">=X"    → inclusive lower bound      → SKILL ?min
#   "range X Y" or "[X:Y]" or "X..Y"      → SKILL ?range
#   "tol X"  → tolerance form             → SKILL ?tol
# The framework only does light prefix sanity at load time. The number tokens
# (e.g. "100p", "2.4G") are passed opaque to SKILL, which uses evalstring to
# resolve SI suffixes against Cadence's native parser.
_SPEC_PREFIX_RE = re.compile(
    r"^\s*("
    r"<=|>=|<|>|"           # comparison operators
    r"range\b|tol\b|"        # named forms
    r"\[|[-+0-9.]"            # bracket form or naked number
    r")"
)


def _validate_spec(where: str, raw: dict) -> Optional[str]:
    """v1.3 — optional Cadence-native spec passthrough string on an apply entry."""
    if "spec" not in raw:
        return None
    value = raw["spec"]
    if value is None:
        return None
    if not isinstance(value, str):
        raise MeasureBundleLoadError(
            f"{where}: 'spec' must be a string or null "
            f"(got {type(value).__name__})"
        )
    if value.strip() == "":
        raise MeasureBundleLoadError(
            f"{where}: 'spec' must be a non-empty string when present "
            f"(omit the field or set null for no spec)"
        )
    if not _SPEC_PREFIX_RE.match(value):
        raise MeasureBundleLoadError(
            f"{where}: 'spec' {value!r} does not look like a Cadence spec — "
            f"expected start with one of: < > <= >= range tol [ digit"
        )
    return value
