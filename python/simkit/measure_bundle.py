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
from dataclasses import dataclass
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

_SUPPORTED_MEASURE_SCHEMA_VERSIONS = frozenset({1})

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
    template: Template
    signal_group: Optional[SignalGroup]
    param_overrides: dict[str, str]
    alias_suffix: str


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
        p, data, templates_dir, signal_groups_dir
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
            path, i, raw_entry, templates_dir, signal_groups_dir
        )
        out.append(entry)
    return tuple(out)


def _validate_apply_entry(
    path: Path,
    idx: int,
    raw: object,
    templates_dir: Path,
    signal_groups_dir: Path,
) -> MeasureApply:
    where = f"{path}: apply[{idx}]"
    if not isinstance(raw, dict):
        raise MeasureBundleLoadError(f"{where}: must be a JSON object")

    if "template" not in raw:
        raise MeasureBundleLoadError(f"{where}: missing 'template'")
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

    if "signal_group" not in raw:
        raise MeasureBundleLoadError(
            f"{where}: missing 'signal_group' (use null when template has no signal param)"
        )
    sg_field = raw["signal_group"]
    signal_group: Optional[SignalGroup]
    signal_param = template.signal_param()

    if sg_field is None:
        # M4 case f: template has signal param but bundle gave null.
        if signal_param is not None:
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

    return MeasureApply(
        template=template,
        signal_group=signal_group,
        param_overrides=param_overrides,
        alias_suffix=alias_suffix,
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
    missing: list[str] = []
    for p in template.params:
        if p.kind == "signal":
            continue
        if p.default is not None:
            continue
        if p.key not in overrides:
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
