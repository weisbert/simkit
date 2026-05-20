"""`.cornermodel.json` sidecar loader + Phase 5 Stage 1 corner-manager model.

Implements ``docs/phase5_stage1_spec.md``. Pure-Python, stdlib-only.

A *cornermodel* is a model layer on top of Phase 2's ``Union``: it adds the
**mode** abstraction (a named, single-source bag of register vars) and
materialises down to a ``Union`` for explode / CSV / push. See ``materialize``.

Stage 1 scope: modes + columns + global edit + auto-naming + reconciliation.
Variants / PVT templates / sets / correlated axes are Stage 2-5 and absent here.
"""

from __future__ import annotations

import itertools
import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from simkit import union as _union
from simkit.errors import SimkitError
from simkit.union import ModelEntry, Union, UnionRow


CORNERMODEL_FILE_SUFFIX = ".cornermodel.json"

_MODEL_NAME_RE = re.compile(r"^[a-z0-9_-]+$")          # sidecar `name`
_MODE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")  # mode + alias + Maestro row
_PVT_LABEL_RE = re.compile(r"^[A-Za-z0-9_]+$")
_VAR_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_SUPPORTED_SCHEMA_VERSIONS = frozenset({1})


class CornerModelError(SimkitError):
    """Base class for `.cornermodel.json` loader errors."""


class CornerModelSchemaVersionError(CornerModelError):
    """A sidecar declared a ``cornermodel_schema_version`` we do not support."""


class CornerModelMalformedError(CornerModelError):
    """A sidecar is unreadable / not parseable as JSON / not a JSON object."""


class CornerModelValidationError(CornerModelError):
    """A sidecar parsed cleanly but failed schema validation per spec §5."""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Mode:
    """A named register-config: single source of truth for a set of vars."""

    name: str
    vars: dict[str, str]  # var name -> scalar value


@dataclass(frozen=True)
class CorrelatedTuple:
    """One point on a correlated axis — assigns every member var at once."""

    label: str
    values: dict[str, str]


@dataclass(frozen=True)
class CorrelatedAxis:
    """A bundle of vars that must vary together (spec §2.1, 痛点 h).

    Cross-products treat the whole axis as ONE axis of length ``len(tuples)``,
    not the Cartesian product of its members.
    """

    name: str
    members: tuple[str, ...]
    tuples: tuple[CorrelatedTuple, ...]


@dataclass(frozen=True)
class TemplateColumn:
    """One column-spec inside a PVT template (spec §2.2)."""

    pvt_label: str
    pvt_vars: dict[str, tuple[str, ...]] = field(default_factory=dict)
    pvt_sweep_keys: frozenset[str] = field(default_factory=frozenset)
    correlated_axes: tuple[str, ...] = ()
    # Stage 6: semantic axis→level tokens resolved through the PVT profile.
    axis_levels: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PvtTemplate:
    """A reusable list of column-specs (spec §2.2, 痛点 a)."""

    name: str
    columns: tuple[TemplateColumn, ...]


@dataclass(frozen=True)
class TemplateBinding:
    """An active template binding (Stage 2 §2.3 / Stage 3 §2.3).

    ``variant`` is set when the binding targets a variant rather than the
    plain mode — variants are first-class and can be template-applied.
    """

    mode: str
    template: str
    variant: str | None = None


@dataclass(frozen=True)
class Variant:
    """A mode's diff-overlay (Stage 3 §2.1, 痛点 c).

    ``vars`` holds *absolute* values for the registers the variant covers
    (D2); registers it does not cover inherit the base mode.
    """

    name: str
    base_mode: str
    vars: dict[str, str]


@dataclass(frozen=True)
class RunSet:
    """A named cross-mode corner checklist (Stage 4 §2.1, 痛点 d).

    ``columns`` is a list of column effective names; switching to the set
    enables exactly those columns.
    """

    name: str
    columns: tuple[str, ...]


@dataclass(frozen=True)
class ModelSectionAssignment:
    """One model→section assignment inside a process-axis level (Stage 6).

    ``file`` ``None`` = apply ``section`` to every model of the column;
    a concrete file = target / add that specific model entry.
    """

    section: str
    file: str | None = None


@dataclass(frozen=True)
class AxisLevel:
    """One level of a PVT-profile axis (Stage 6 §2.1) — resolves to a set of
    var assignments and/or model-section assignments."""

    name: str
    vars: dict[str, tuple[str, ...]] = field(default_factory=dict)
    vars_sweep_keys: frozenset[str] = field(default_factory=frozenset)
    models: tuple[ModelSectionAssignment, ...] = ()


@dataclass(frozen=True)
class PvtAxis:
    """A named PVT-profile axis with an open set of levels (Stage 6 §2)."""

    name: str
    levels: dict[str, AxisLevel]


@dataclass(frozen=True)
class PvtProfile:
    """Per-project PVT semantic→concrete mapping (Stage 6 §2).

    Authored once at project kickoff. Templates reference semantic
    ``axis:level`` tokens; ``materialize`` resolves them through the profile,
    so the same template ports to another project under a different profile.
    """

    pvtprofile_schema_version: int
    name: str
    project: str
    axes: dict[str, PvtAxis]


@dataclass(frozen=True)
class Column:
    """One corner column.

    Managed column: ``mode`` set, ``pvt_label`` set, ``name`` None. Its
    Maestro row name is *derived* (see ``effective_name``).
    Unmanaged column: ``mode`` None, ``name`` set, no ``pvt_label`` / ``alias``
    / ``overrides`` — it is a reverse-engineered foreign column (spec §6).
    """

    mode: str | None
    enabled: bool
    pvt_vars: dict[str, tuple[str, ...]]
    models: tuple[ModelEntry, ...]
    pvt_label: str | None = None
    name: str | None = None
    alias: str | None = None
    overrides: dict[str, str] = field(default_factory=dict)
    pvt_sweep_keys: frozenset[str] = field(default_factory=frozenset)
    model_sweep_indices: frozenset[int] = field(default_factory=frozenset)
    # Stage 2: correlated axes referenced by this column + template provenance.
    correlated_axes: tuple[str, ...] = ()
    template: str | None = None
    # Stage 3: the variant this column hangs on (its mode = variant.base_mode).
    variant: str | None = None
    # Stage 6: semantic axis→level tokens resolved through the PVT profile.
    axis_levels: dict[str, str] = field(default_factory=dict)

    @property
    def is_managed(self) -> bool:
        return self.mode is not None


@dataclass(frozen=True)
class CornerModel:
    cornermodel_schema_version: int
    name: str
    project: str
    testbench_id: str
    modes: dict[str, Mode]
    columns: tuple[Column, ...]
    # Stage 2 additions — all default-empty so Stage 1 sidecars load unchanged.
    correlated_axes: dict[str, CorrelatedAxis] = field(default_factory=dict)
    pvt_templates: dict[str, PvtTemplate] = field(default_factory=dict)
    template_bindings: tuple[TemplateBinding, ...] = ()
    # Stage 3 addition.
    variants: dict[str, Variant] = field(default_factory=dict)
    # Stage 4 addition.
    run_sets: dict[str, RunSet] = field(default_factory=dict)
    # Stage 5 addition — explicit variable-row display order.
    var_order: tuple[str, ...] = ()
    # Stage 6 addition — name of the bound PVT profile (resolved at materialize).
    pvt_profile: str | None = None


# ---------------------------------------------------------------------------
# Effective name (spec §2.4)
# ---------------------------------------------------------------------------


def effective_name(column: Column) -> str:
    """The Maestro row name a column maps to. Derived for managed columns;
    stored for unmanaged ones. Never persisted for managed columns."""
    if column.alias:
        return column.alias
    if column.is_managed:
        root = column.variant if column.variant else column.mode
        return f"{root}_{column.pvt_label}"
    assert column.name is not None  # guaranteed by load validation
    return column.name


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_cornermodel(
    path: Path | str, expected_project: str | None = None
) -> CornerModel:
    p = Path(path).expanduser().resolve()

    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise CornerModelMalformedError(f"{p}: invalid JSON — {exc}") from exc
    except OSError as exc:
        raise CornerModelMalformedError(f"{p}: cannot read — {exc}") from exc

    if not isinstance(data, dict):
        raise CornerModelMalformedError(
            f"{p}: top-level must be a JSON object, got {type(data).__name__}"
        )

    schema_version = _validate_schema_version(p, data)
    name = _validate_name(p, data)
    project = _require_str(p, data, "project")
    testbench_id = _require_str(p, data, "testbench_id")
    if expected_project is not None and project != expected_project:
        raise CornerModelValidationError(
            f"{p}: 'project' {project!r} does not match the enclosing "
            f".pvtproject project {expected_project!r}"
        )

    modes = _validate_modes(p, data)
    variants = _validate_variants(p, data, modes)
    correlated_axes = _validate_correlated_axes(p, data)
    pvt_templates = _validate_templates(p, data, correlated_axes)
    columns = _validate_columns(p, data, modes, correlated_axes, variants)
    bindings = _validate_bindings(p, data, modes, pvt_templates, variants)
    run_sets = _validate_run_sets(p, data)
    var_order = _validate_var_order(p, data)
    pvt_profile = data.get("pvt_profile")
    if pvt_profile is not None and not isinstance(pvt_profile, str):
        raise CornerModelValidationError(
            f"{p}: 'pvt_profile' must be a string or absent"
        )

    return CornerModel(
        cornermodel_schema_version=schema_version,
        name=name,
        project=project,
        testbench_id=testbench_id,
        modes=modes,
        columns=columns,
        correlated_axes=correlated_axes,
        pvt_templates=pvt_templates,
        template_bindings=bindings,
        variants=variants,
        run_sets=run_sets,
        var_order=var_order,
        pvt_profile=pvt_profile,
    )


def _validate_var_order(path: Path, data: dict) -> tuple[str, ...]:
    raw = data.get("var_order", [])
    if not isinstance(raw, list) or not all(isinstance(v, str) for v in raw):
        raise CornerModelValidationError(
            f"{path}: 'var_order' must be a JSON array of strings"
        )
    return tuple(raw)


def _validate_run_sets(path: Path, data: dict) -> dict[str, RunSet]:
    raw = data.get("run_sets", {})
    if not isinstance(raw, dict):
        raise CornerModelValidationError(
            f"{path}: 'run_sets' must be a JSON object"
        )
    out: dict[str, RunSet] = {}
    for set_name, raw_set in raw.items():
        where = f"{path}: run_sets[{set_name!r}]"
        if not _MODE_NAME_RE.match(set_name):
            raise CornerModelValidationError(
                f"{where}: run-set name must match ^[A-Za-z][A-Za-z0-9_]*$"
            )
        if not isinstance(raw_set, dict):
            raise CornerModelValidationError(f"{where}: must be a JSON object")
        raw_cols = raw_set.get("columns", [])
        if not isinstance(raw_cols, list) \
                or not all(isinstance(c, str) for c in raw_cols):
            raise CornerModelValidationError(
                f"{where}: 'columns' must be a JSON array of strings"
            )
        # Names pointing at columns that do not exist are tolerated here
        # (forward-compat — a run-set may be authored before its columns).
        out[set_name] = RunSet(name=set_name, columns=tuple(raw_cols))
    return out


def _validate_variants(
    path: Path, data: dict, modes: dict[str, Mode]
) -> dict[str, Variant]:
    raw = data.get("variants", {})
    if not isinstance(raw, dict):
        raise CornerModelValidationError(
            f"{path}: 'variants' must be a JSON object"
        )
    out: dict[str, Variant] = {}
    for var_name, raw_var in raw.items():
        where = f"{path}: variants[{var_name!r}]"
        if not _MODE_NAME_RE.match(var_name):
            raise CornerModelValidationError(
                f"{where}: variant name must match ^[A-Za-z][A-Za-z0-9_]*$"
            )
        if not isinstance(raw_var, dict):
            raise CornerModelValidationError(f"{where}: must be a JSON object")
        base_mode = raw_var.get("base_mode")
        if base_mode not in modes:
            raise CornerModelValidationError(
                f"{where}: base_mode {base_mode!r} is not a defined mode"
            )
        base_keys = set(modes[base_mode].vars)
        raw_vars = raw_var.get("vars")
        if not isinstance(raw_vars, dict):
            raise CornerModelValidationError(
                f"{where}: 'vars' must be a JSON object"
            )
        var_vars: dict[str, str] = {}
        for vk, vv in raw_vars.items():
            if vk not in base_keys:
                raise CornerModelValidationError(
                    f"{where}: var {vk!r} is not a register of base mode "
                    f"{base_mode!r} — a variant only overrides existing regs"
                )
            if not isinstance(vv, str):
                raise CornerModelValidationError(
                    f"{where}: var {vk!r} must be a scalar string"
                )
            var_vars[vk] = vv
        out[var_name] = Variant(
            name=var_name, base_mode=base_mode, vars=var_vars
        )
    return out


def _validate_schema_version(path: Path, data: dict) -> int:
    if "cornermodel_schema_version" not in data:
        raise CornerModelSchemaVersionError(
            f"{path}: missing required field 'cornermodel_schema_version'"
        )
    raw = data["cornermodel_schema_version"]
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise CornerModelSchemaVersionError(
            f"{path}: 'cornermodel_schema_version' must be an integer"
        )
    if raw not in _SUPPORTED_SCHEMA_VERSIONS:
        raise CornerModelSchemaVersionError(
            f"{path}: cornermodel_schema_version {raw} not supported "
            f"(supported: {sorted(_SUPPORTED_SCHEMA_VERSIONS)})"
        )
    return raw


def _validate_name(path: Path, data: dict) -> str:
    name = _require_str(path, data, "name")
    if not _MODEL_NAME_RE.match(name):
        raise CornerModelValidationError(
            f"{path}: 'name' {name!r} does not match ^[a-z0-9_-]+$"
        )
    basename = path.name
    if not basename.endswith(CORNERMODEL_FILE_SUFFIX):
        raise CornerModelValidationError(
            f"{path}: filename must end with '{CORNERMODEL_FILE_SUFFIX}'"
        )
    expected = basename[: -len(CORNERMODEL_FILE_SUFFIX)]
    if expected != name:
        raise CornerModelValidationError(
            f"{path}: 'name' {name!r} must equal filename basename {expected!r}"
        )
    return name


def _require_str(path: Path, data: dict, key: str) -> str:
    if key not in data:
        raise CornerModelValidationError(f"{path}: missing required field {key!r}")
    value = data[key]
    if not isinstance(value, str) or value == "":
        raise CornerModelValidationError(
            f"{path}: {key!r} must be a non-empty string"
        )
    return value


def _validate_modes(path: Path, data: dict) -> dict[str, Mode]:
    if "modes" not in data:
        raise CornerModelValidationError(f"{path}: missing required field 'modes'")
    raw = data["modes"]
    if not isinstance(raw, dict):
        raise CornerModelValidationError(f"{path}: 'modes' must be a JSON object")

    out: dict[str, Mode] = {}
    for mode_name, raw_mode in raw.items():
        where = f"{path}: modes[{mode_name!r}]"
        if not _MODE_NAME_RE.match(mode_name):
            raise CornerModelValidationError(
                f"{where}: mode name does not match ^[A-Za-z][A-Za-z0-9_]*$"
            )
        if not isinstance(raw_mode, dict):
            raise CornerModelValidationError(f"{where}: must be a JSON object")
        raw_vars = raw_mode.get("vars")
        if not isinstance(raw_vars, dict) or len(raw_vars) == 0:
            raise CornerModelValidationError(
                f"{where}: 'vars' must be a non-empty JSON object"
            )
        mode_vars: dict[str, str] = {}
        for vname, vval in raw_vars.items():
            if not isinstance(vname, str) or not _VAR_NAME_RE.match(vname):
                raise CornerModelValidationError(
                    f"{where}: var name {vname!r} does not match "
                    f"^[A-Za-z][A-Za-z0-9_]*$"
                )
            if not isinstance(vval, str):
                raise CornerModelValidationError(
                    f"{where}: var {vname!r} must be a scalar string "
                    f"(got {type(vval).__name__}); mode vars cannot sweep in "
                    f"Stage 1"
                )
            mode_vars[vname] = vval
        out[mode_name] = Mode(name=mode_name, vars=mode_vars)
    return out


def _validate_columns(
    path: Path, data: dict, modes: dict[str, Mode],
    correlated_axes: dict[str, CorrelatedAxis],
    variants: dict[str, Variant],
) -> tuple[Column, ...]:
    if "columns" not in data:
        raise CornerModelValidationError(
            f"{path}: missing required field 'columns'"
        )
    raw = data["columns"]
    if not isinstance(raw, list):
        raise CornerModelValidationError(f"{path}: 'columns' must be a JSON array")
    if len(raw) == 0:
        raise CornerModelValidationError(f"{path}: 'columns' must be non-empty")

    out: list[Column] = []
    seen_names: dict[str, int] = {}
    for i, raw_col in enumerate(raw):
        col = _validate_column(
            path, i, raw_col, modes, correlated_axes, variants
        )
        eff = effective_name(col)
        if eff in seen_names:
            raise CornerModelValidationError(
                f"{path}: columns[{i}] effective name {eff!r} collides with "
                f"columns[{seen_names[eff]}]"
            )
        seen_names[eff] = i
        out.append(col)
    return tuple(out)


def _validate_column(
    path: Path, idx: int, raw: object, modes: dict[str, Mode],
    correlated_axes: dict[str, CorrelatedAxis],
    variants: dict[str, Variant],
) -> Column:
    where = f"{path}: columns[{idx}]"
    if not isinstance(raw, dict):
        raise CornerModelValidationError(f"{where}: must be a JSON object")

    if "mode" not in raw:
        raise CornerModelValidationError(f"{where}: missing 'mode'")
    mode_name = raw["mode"]
    if mode_name is not None and not isinstance(mode_name, str):
        raise CornerModelValidationError(
            f"{where}: 'mode' must be a string or null"
        )

    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise CornerModelValidationError(
            f"{where}: 'enabled' must be a JSON boolean"
        )

    pvt_vars, pvt_sweep_keys = _parse_var_block(
        where, raw.get("pvt_vars", {}), "pvt_vars"
    )
    models, model_sweep_indices = _parse_models(where, raw.get("models", []))
    col_axes = _parse_column_axes(where, raw, correlated_axes)
    template = raw.get("template")
    if template is not None and not isinstance(template, str):
        raise CornerModelValidationError(
            f"{where}: 'template' provenance must be a string or absent"
        )

    variant = raw.get("variant")
    if variant is not None:
        if mode_name is None:
            raise CornerModelValidationError(
                f"{where}: unmanaged column (mode=null) must not carry "
                f"'variant'"
            )
        if variant not in variants:
            raise CornerModelValidationError(
                f"{where}: variant {variant!r} is not defined"
            )
        if variants[variant].base_mode != mode_name:
            raise CornerModelValidationError(
                f"{where}: column mode {mode_name!r} != variant "
                f"{variant!r} base_mode {variants[variant].base_mode!r}"
            )

    if mode_name is not None:
        col = _validate_managed_column(
            where, raw, mode_name, modes, enabled, pvt_vars,
            pvt_sweep_keys, models, model_sweep_indices,
        )
    else:
        col = _validate_unmanaged_column(
            where, raw, enabled, pvt_vars, pvt_sweep_keys,
            models, model_sweep_indices,
        )
    col = replace(
        col, correlated_axes=col_axes, template=template, variant=variant,
        axis_levels=_parse_axis_levels(where, raw),
    )

    # Spec §2.4 / §6.3 — correlated-axis members must not collide with the
    # column's PVT vars or its mode's register vars.
    mode_var_keys = set(modes[mode_name].vars) if mode_name in modes else set()
    for axis_name in col_axes:
        members = set(correlated_axes[axis_name].members)
        if members & set(pvt_vars):
            raise CornerModelValidationError(
                f"{where}: correlated axis {axis_name!r} members "
                f"{sorted(members & set(pvt_vars))} collide with pvt_vars"
            )
        if members & mode_var_keys:
            raise CornerModelValidationError(
                f"{where}: correlated axis {axis_name!r} members "
                f"{sorted(members & mode_var_keys)} collide with mode registers"
            )

    # Spec §5 rule 7 — every column must materialise to a legal Phase 2 row.
    if (len(pvt_vars) == 0 and len(models) == 0 and not col.is_managed
            and not col_axes):
        raise CornerModelValidationError(
            f"{where}: unmanaged column has neither vars, models nor axes"
        )
    return col


def _parse_axis_levels(where: str, raw: dict) -> dict[str, str]:
    """Parse the ``axis_levels`` token map (Stage 6). Whether the axis/level
    exist is a profile-time check (the loader has no profile in hand)."""
    raw_al = raw.get("axis_levels", {})
    if not isinstance(raw_al, dict):
        raise CornerModelValidationError(
            f"{where}: 'axis_levels' must be a JSON object"
        )
    out: dict[str, str] = {}
    for axis_name, level_name in raw_al.items():
        if not isinstance(axis_name, str) or not isinstance(level_name, str):
            raise CornerModelValidationError(
                f"{where}: 'axis_levels' keys and values must be strings"
            )
        out[axis_name] = level_name
    return out


def _parse_column_axes(
    where: str, raw: dict, correlated_axes: dict[str, CorrelatedAxis]
) -> tuple[str, ...]:
    raw_axes = raw.get("correlated_axes", [])
    if not isinstance(raw_axes, list):
        raise CornerModelValidationError(
            f"{where}: 'correlated_axes' must be a JSON array"
        )
    out: list[str] = []
    for axis_name in raw_axes:
        if not isinstance(axis_name, str) or axis_name not in correlated_axes:
            raise CornerModelValidationError(
                f"{where}: correlated axis {axis_name!r} is not defined"
            )
        out.append(axis_name)
    return tuple(out)


def _validate_managed_column(
    where, raw, mode_name, modes, enabled, pvt_vars,
    pvt_sweep_keys, models, model_sweep_indices,
) -> Column:
    if mode_name not in modes:
        raise CornerModelValidationError(
            f"{where}: 'mode' {mode_name!r} is not a defined mode"
        )
    if "name" in raw:
        raise CornerModelValidationError(
            f"{where}: managed column must not carry an explicit 'name' "
            f"(the name is derived from mode + pvt_label)"
        )
    pvt_label = raw.get("pvt_label")
    if not isinstance(pvt_label, str) or not _PVT_LABEL_RE.match(pvt_label):
        raise CornerModelValidationError(
            f"{where}: managed column needs a 'pvt_label' matching "
            f"^[A-Za-z0-9_]+$"
        )
    alias = raw.get("alias")
    if alias is not None:
        if not isinstance(alias, str) or not _MODE_NAME_RE.match(alias):
            raise CornerModelValidationError(
                f"{where}: 'alias' must match ^[A-Za-z][A-Za-z0-9_]*$"
            )

    mode_vars = modes[mode_name].vars
    raw_overrides = raw.get("overrides", {})
    if not isinstance(raw_overrides, dict):
        raise CornerModelValidationError(
            f"{where}: 'overrides' must be a JSON object"
        )
    overrides: dict[str, str] = {}
    for ovar, oval in raw_overrides.items():
        if ovar not in mode_vars:
            raise CornerModelValidationError(
                f"{where}: override {ovar!r} is not a var of mode "
                f"{mode_name!r} — only mode-managed vars can be overridden"
            )
        if not isinstance(oval, str):
            raise CornerModelValidationError(
                f"{where}: override {ovar!r} must be a scalar string"
            )
        overrides[ovar] = oval

    collisions = set(pvt_vars) & set(mode_vars)
    if collisions:
        raise CornerModelValidationError(
            f"{where}: pvt_vars {sorted(collisions)} collide with mode "
            f"{mode_name!r} register vars — a var is either mode-managed or "
            f"per-column, not both"
        )

    return Column(
        mode=mode_name, enabled=enabled, pvt_vars=pvt_vars, models=models,
        pvt_label=pvt_label, name=None, alias=alias, overrides=overrides,
        pvt_sweep_keys=pvt_sweep_keys, model_sweep_indices=model_sweep_indices,
    )


def _validate_unmanaged_column(
    where, raw, enabled, pvt_vars, pvt_sweep_keys, models, model_sweep_indices,
) -> Column:
    for forbidden in ("pvt_label", "alias", "overrides"):
        if forbidden in raw:
            raise CornerModelValidationError(
                f"{where}: unmanaged column (mode=null) must not carry "
                f"{forbidden!r}"
            )
    name = raw.get("name")
    if not isinstance(name, str) or not _MODE_NAME_RE.match(name):
        raise CornerModelValidationError(
            f"{where}: unmanaged column needs a 'name' matching "
            f"^[A-Za-z][A-Za-z0-9_]*$"
        )
    return Column(
        mode=None, enabled=enabled, pvt_vars=pvt_vars, models=models,
        pvt_label=None, name=name, alias=None,
        pvt_sweep_keys=pvt_sweep_keys, model_sweep_indices=model_sweep_indices,
    )


def _parse_var_block(
    where: str, raw: object, label: str
) -> tuple[dict[str, tuple[str, ...]], frozenset[str]]:
    if not isinstance(raw, dict):
        raise CornerModelValidationError(
            f"{where}: {label!r} must be a JSON object"
        )
    out: dict[str, tuple[str, ...]] = {}
    sweep_keys: set[str] = set()
    for vname, vval in raw.items():
        if not isinstance(vname, str) or not _VAR_NAME_RE.match(vname):
            raise CornerModelValidationError(
                f"{where}: var name {vname!r} does not match "
                f"^[A-Za-z][A-Za-z0-9_]*$"
            )
        tup, is_sweep = _union._coerce_string_or_array(
            vval, f"{where}: {label}[{vname!r}]"
        )
        out[vname] = tup
        if is_sweep:
            sweep_keys.add(vname)
    return out, frozenset(sweep_keys)


def _parse_models(
    where: str, raw: object
) -> tuple[tuple[ModelEntry, ...], frozenset[int]]:
    if not isinstance(raw, list):
        raise CornerModelValidationError(f"{where}: 'models' must be a JSON array")
    out: list[ModelEntry] = []
    sweep_indices: set[int] = set()
    for j, raw_model in enumerate(raw):
        entry, is_sweep = _union._validate_model_entry(
            f"{where}: models[{j}]", raw_model
        )
        out.append(entry)
        if is_sweep:
            sweep_indices.add(j)
    return tuple(out), frozenset(sweep_indices)


# ---------------------------------------------------------------------------
# Stage 2 loaders — correlated axes, PVT templates, bindings (spec §2)
# ---------------------------------------------------------------------------


def _validate_correlated_axes(
    path: Path, data: dict
) -> dict[str, CorrelatedAxis]:
    raw = data.get("correlated_axes", {})
    if not isinstance(raw, dict):
        raise CornerModelValidationError(
            f"{path}: 'correlated_axes' must be a JSON object"
        )
    out: dict[str, CorrelatedAxis] = {}
    for axis_name, raw_axis in raw.items():
        where = f"{path}: correlated_axes[{axis_name!r}]"
        if not _VAR_NAME_RE.match(axis_name):
            raise CornerModelValidationError(
                f"{where}: axis name must match ^[A-Za-z][A-Za-z0-9_]*$"
            )
        if not isinstance(raw_axis, dict):
            raise CornerModelValidationError(f"{where}: must be a JSON object")
        raw_members = raw_axis.get("members")
        if not isinstance(raw_members, list) or len(raw_members) == 0:
            raise CornerModelValidationError(
                f"{where}: 'members' must be a non-empty JSON array"
            )
        for m in raw_members:
            if not isinstance(m, str) or not _VAR_NAME_RE.match(m):
                raise CornerModelValidationError(
                    f"{where}: member {m!r} must match ^[A-Za-z][A-Za-z0-9_]*$"
                )
        members = tuple(raw_members)
        member_set = set(members)
        raw_tuples = raw_axis.get("tuples")
        if not isinstance(raw_tuples, list) or len(raw_tuples) == 0:
            raise CornerModelValidationError(
                f"{where}: 'tuples' must be a non-empty JSON array"
            )
        tuples: list[CorrelatedTuple] = []
        seen_labels: set[str] = set()
        for t_i, raw_t in enumerate(raw_tuples):
            tw = f"{where}: tuples[{t_i}]"
            if not isinstance(raw_t, dict):
                raise CornerModelValidationError(f"{tw}: must be a JSON object")
            label = raw_t.get("label")
            if not isinstance(label, str) or not _PVT_LABEL_RE.match(label):
                raise CornerModelValidationError(
                    f"{tw}: 'label' must match ^[A-Za-z0-9_]+$"
                )
            if label in seen_labels:
                raise CornerModelValidationError(
                    f"{tw}: duplicate tuple label {label!r}"
                )
            seen_labels.add(label)
            values = raw_t.get("values")
            if not isinstance(values, dict) or set(values) != member_set:
                raise CornerModelValidationError(
                    f"{tw}: 'values' keys must be exactly the axis members "
                    f"{sorted(member_set)}"
                )
            for vk, vv in values.items():
                if not isinstance(vv, str):
                    raise CornerModelValidationError(
                        f"{tw}: value for {vk!r} must be a scalar string"
                    )
            tuples.append(CorrelatedTuple(label=label, values=dict(values)))
        out[axis_name] = CorrelatedAxis(
            name=axis_name, members=members, tuples=tuple(tuples)
        )
    return out


def _validate_templates(
    path: Path, data: dict, correlated_axes: dict[str, CorrelatedAxis]
) -> dict[str, PvtTemplate]:
    raw = data.get("pvt_templates", {})
    if not isinstance(raw, dict):
        raise CornerModelValidationError(
            f"{path}: 'pvt_templates' must be a JSON object"
        )
    out: dict[str, PvtTemplate] = {}
    for tmpl_name, raw_tmpl in raw.items():
        where = f"{path}: pvt_templates[{tmpl_name!r}]"
        if not _MODEL_NAME_RE.match(tmpl_name):
            raise CornerModelValidationError(
                f"{where}: template name must match ^[a-z0-9_-]+$"
            )
        if not isinstance(raw_tmpl, dict):
            raise CornerModelValidationError(f"{where}: must be a JSON object")
        raw_cols = raw_tmpl.get("columns")
        if not isinstance(raw_cols, list) or len(raw_cols) == 0:
            raise CornerModelValidationError(
                f"{where}: 'columns' must be a non-empty JSON array"
            )
        cols: list[TemplateColumn] = []
        seen_labels: set[str] = set()
        for c_i, raw_c in enumerate(raw_cols):
            cw = f"{where}: columns[{c_i}]"
            if not isinstance(raw_c, dict):
                raise CornerModelValidationError(f"{cw}: must be a JSON object")
            pvt_label = raw_c.get("pvt_label")
            if not isinstance(pvt_label, str) \
                    or not _PVT_LABEL_RE.match(pvt_label):
                raise CornerModelValidationError(
                    f"{cw}: 'pvt_label' must match ^[A-Za-z0-9_]+$"
                )
            if pvt_label in seen_labels:
                raise CornerModelValidationError(
                    f"{cw}: duplicate pvt_label {pvt_label!r}"
                )
            seen_labels.add(pvt_label)
            pvt_vars, sweep = _parse_var_block(
                cw, raw_c.get("pvt_vars", {}), "pvt_vars"
            )
            axes = _parse_column_axes(cw, raw_c, correlated_axes)
            cols.append(TemplateColumn(
                pvt_label=pvt_label, pvt_vars=pvt_vars,
                pvt_sweep_keys=sweep, correlated_axes=axes,
                axis_levels=_parse_axis_levels(cw, raw_c),
            ))
        out[tmpl_name] = PvtTemplate(name=tmpl_name, columns=tuple(cols))
    return out


def _validate_bindings(
    path: Path, data: dict, modes: dict[str, Mode],
    templates: dict[str, PvtTemplate], variants: dict[str, Variant],
) -> tuple[TemplateBinding, ...]:
    raw = data.get("template_bindings", [])
    if not isinstance(raw, list):
        raise CornerModelValidationError(
            f"{path}: 'template_bindings' must be a JSON array"
        )
    out: list[TemplateBinding] = []
    for i, raw_b in enumerate(raw):
        where = f"{path}: template_bindings[{i}]"
        if not isinstance(raw_b, dict):
            raise CornerModelValidationError(f"{where}: must be a JSON object")
        mode_name = raw_b.get("mode")
        tmpl_name = raw_b.get("template")
        variant = raw_b.get("variant")
        if mode_name not in modes:
            raise CornerModelValidationError(
                f"{where}: mode {mode_name!r} is not defined"
            )
        if tmpl_name not in templates:
            raise CornerModelValidationError(
                f"{where}: template {tmpl_name!r} is not defined"
            )
        if variant is not None:
            if variant not in variants:
                raise CornerModelValidationError(
                    f"{where}: variant {variant!r} is not defined"
                )
            if variants[variant].base_mode != mode_name:
                raise CornerModelValidationError(
                    f"{where}: binding mode {mode_name!r} != variant "
                    f"{variant!r} base_mode"
                )
        out.append(TemplateBinding(
            mode=mode_name, template=tmpl_name, variant=variant
        ))
    return tuple(out)


# ---------------------------------------------------------------------------
# Materialise: cornermodel -> Phase 2 Union (spec §2.2 / §3.4)
# ---------------------------------------------------------------------------


def _column_base_vars(
    model: CornerModel, column: Column, profile: "PvtProfile | None" = None
) -> tuple[dict[str, tuple[str, ...]], set[str]]:
    """Non-correlated var contribution + its sweep keys: mode regs (overlaid
    by overrides / variant), the column's own PVT vars, and — Stage 6 — any
    vars resolved from the bound profile's ``axis_levels``."""
    row_vars: dict[str, tuple[str, ...]] = {}
    sweep: set[str] = set()
    if column.is_managed:
        mode = model.modes[column.mode]
        variant_vars = (
            model.variants[column.variant].vars if column.variant else {}
        )
        # Three-layer fallback (spec §3): 手改 > 变体 > 模式 base.
        for vname, vval in mode.vars.items():
            below = variant_vars.get(vname, vval)
            row_vars[vname] = (column.overrides.get(vname, below),)
    for vname, tup in column.pvt_vars.items():
        row_vars[vname] = tup
    sweep |= set(column.pvt_sweep_keys)
    # Stage 6 — resolve axis_levels through the profile.
    if profile is not None:
        for axis_name, level_name in column.axis_levels.items():
            level = _resolve_level(profile, axis_name, level_name)
            if level is None:
                continue
            for vname, tup in level.vars.items():
                row_vars[vname] = tup
            sweep |= set(level.vars_sweep_keys)
    return row_vars, sweep


def _resolve_level(
    profile: "PvtProfile", axis_name: str, level_name: str
) -> "AxisLevel | None":
    axis = profile.axes.get(axis_name)
    if axis is None:
        return None
    return axis.levels.get(level_name)


def _column_models(
    column: Column, profile: "PvtProfile | None" = None
) -> tuple[ModelEntry, ...]:
    """The column's model entries, with process-axis levels resolved (Stage 6).

    A profile process level's no-``file`` assignment sets the section on every
    existing model entry; a ``file``-bearing one targets / appends that model.
    """
    models: list[ModelEntry] = list(column.models)
    if profile is None:
        return tuple(models)
    for axis_name, level_name in column.axis_levels.items():
        level = _resolve_level(profile, axis_name, level_name)
        if level is None:
            continue
        for msa in level.models:
            if msa.file is None:
                models = [
                    replace(m, section=(msa.section,)) for m in models
                ]
            else:
                hit = False
                for i, m in enumerate(models):
                    if m.file == msa.file:
                        models[i] = replace(m, section=(msa.section,))
                        hit = True
                if not hit:
                    models.append(ModelEntry(
                        file=msa.file, block=_union._DEFAULT_MODEL_BLOCK,
                        test=_union._DEFAULT_MODEL_TEST, section=(msa.section,),
                    ))
    return tuple(models)


def materialize_column_rows(
    model: CornerModel, column: Column, profile: "PvtProfile | None" = None
) -> list[UnionRow]:
    """Lower one column to one *or more* Phase 2 ``UnionRow`` (spec §3).

    No correlated axes → one row. Correlated axes → one row per combination of
    the axes' tuples (Maestro has no correlated concept, so the bundle cross-
    product is expanded here). ``profile`` resolves Stage 6 ``axis_levels``.
    """
    base_vars, sweep = _column_base_vars(model, column, profile)
    base_name = effective_name(column)
    models = _column_models(column, profile)
    axes = [model.correlated_axes[a] for a in column.correlated_axes]

    if not axes:
        return [UnionRow(
            row_name=base_name, vars=base_vars, models=models,
            sweep_var_keys=frozenset(sweep),
            sweep_model_indices=column.model_sweep_indices,
            enabled=column.enabled,
        )]

    rows: list[UnionRow] = []
    for combo in itertools.product(*[ax.tuples for ax in axes]):
        row_vars = dict(base_vars)
        labels: list[str] = []
        for ct in combo:
            labels.append(ct.label)
            for vk, vv in ct.values.items():
                row_vars[vk] = (vv,)
        rows.append(UnionRow(
            row_name=f"{base_name}__{'_'.join(labels)}",
            vars=row_vars, models=models,
            sweep_var_keys=frozenset(sweep),
            sweep_model_indices=column.model_sweep_indices,
            enabled=column.enabled,
        ))
    return rows


def materialize_column(
    model: CornerModel, column: Column, profile: "PvtProfile | None" = None
) -> UnionRow:
    """The single materialised row for a column with no correlated axes.

    Convenience for the Stage 1 path; for a correlated column it returns the
    first expanded row — callers that need the full set use
    :func:`materialize_column_rows`.
    """
    return materialize_column_rows(model, column, profile)[0]


def materialize(
    model: CornerModel, profile: "PvtProfile | None" = None
) -> Union:
    """Lower a whole cornermodel to a Phase 2 ``Union`` for explode / push.

    ``profile`` resolves Stage 6 ``axis_levels``; omit it for a cornermodel
    that uses only literal Stage 1-5 values."""
    rows: list[UnionRow] = []
    for column in model.columns:
        rows.extend(materialize_column_rows(model, column, profile))
    seen: set[str] = set()
    for row in rows:
        if row.row_name in seen:
            raise CornerModelValidationError(
                f"materialize: duplicate row name {row.row_name!r} — two "
                f"columns / correlated expansions collide"
            )
        seen.add(row.row_name)
    return Union(
        union_schema_version=1,
        name=model.name,
        project=model.project,
        testbench_id=model.testbench_id,
        rows=tuple(rows),
    )


def column_display_vars(
    model: CornerModel, column: Column, profile: "PvtProfile | None" = None
) -> dict[str, tuple[str, ...]]:
    """Per-var distinct values across a column's full expansion — what the
    corner-table cell shows for a (possibly aggregated) column."""
    merged: dict[str, list[str]] = {}
    for row in materialize_column_rows(model, column, profile):
        for var, tup in row.vars.items():
            bucket = merged.setdefault(var, [])
            for x in tup:
                if x not in bucket:
                    bucket.append(x)
    return {var: tuple(vals) for var, vals in merged.items()}


def column_point_count(
    model: CornerModel, column: Column, profile: "PvtProfile | None" = None
) -> int:
    """Number of simulation points a column expands to (sub-corner count)."""
    return sum(
        len(_union._explode_row(row))
        for row in materialize_column_rows(model, column, profile)
    )


# ---------------------------------------------------------------------------
# Override conflict — spec §6.4 (D1, Stage 1 subset)
# ---------------------------------------------------------------------------


def is_cell_red(model: CornerModel, column: Column, var: str) -> bool:
    """True if ``var`` on ``column`` is a manual override that diverges from
    the layer below it — the D1 red-flag. The layer below is the variant
    value (if the column is on a variant that covers ``var``), else the mode
    base. ``False`` for unmanaged columns and non-overridden vars."""
    if not column.is_managed or var not in column.overrides:
        return False
    below = model.modes[column.mode].vars.get(var)
    if column.variant:
        below = model.variants[column.variant].vars.get(var, below)
    return column.overrides[var] != below


# ---------------------------------------------------------------------------
# Reconciliation — spec §6
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VarDiff:
    var: str
    cornermodel_value: tuple[str, ...]
    maestro_value: tuple[str, ...]


@dataclass(frozen=True)
class ReconcileResult:
    """Outcome of classifying a Maestro pull against a cornermodel (spec §6.1).

    ``matched`` maps an existing column's effective name to the per-var diffs
    found (empty list = matched and identical). ``foreign`` are pulled rows
    that match no column. ``missing`` are cornermodel columns absent from the
    pull.
    """

    matched: dict[str, list[VarDiff]]
    foreign: tuple[UnionRow, ...]
    missing: tuple[str, ...]


def classify_pull(
    model: CornerModel, pulled: Union, profile: "PvtProfile | None" = None
) -> ReconcileResult:
    # Build the map from every materialised row name (a correlated column
    # expands to several) back to its source UnionRow.
    by_name: dict[str, UnionRow] = {}
    for column in model.columns:
        for row in materialize_column_rows(model, column, profile):
            by_name[row.row_name] = row
    pulled_names = {row.row_name for row in pulled.rows}

    matched: dict[str, list[VarDiff]] = {}
    foreign: list[UnionRow] = []
    for row in pulled.rows:
        ours = by_name.get(row.row_name)
        if ours is None:
            foreign.append(row)
        else:
            matched[row.row_name] = _diff_rows(ours, row)

    missing = tuple(name for name in by_name if name not in pulled_names)
    return ReconcileResult(
        matched=matched, foreign=tuple(foreign), missing=missing
    )


def _diff_rows(ours: UnionRow, pulled_row: UnionRow) -> list[VarDiff]:
    diffs: list[VarDiff] = []
    for var in sorted(set(ours.vars) | set(pulled_row.vars)):
        a = ours.vars.get(var, ())
        b = pulled_row.vars.get(var, ())
        if a != b:
            diffs.append(
                VarDiff(var=var, cornermodel_value=a, maestro_value=b)
            )
    return diffs


def make_unmanaged_column(row: UnionRow) -> Column:
    """Turn a foreign Maestro row into an unmanaged column (spec §6.1)."""
    return Column(
        mode=None,
        enabled=row.enabled,
        pvt_vars=dict(row.vars),
        models=row.models,
        name=row.row_name,
        pvt_sweep_keys=row.sweep_var_keys,
        model_sweep_indices=row.sweep_model_indices,
    )


def empty_cornermodel(
    name: str = "corners", project: str = "", testbench_id: str = "unset"
) -> CornerModel:
    """A blank but valid cornermodel — zero modes, zero columns.

    The GUI's Corners tab opens on one of these so the corner manager is
    always present and usable without a load step (the user can start
    creating modes / columns immediately). ``testbench_id`` defaults to the
    ``"unset"`` sentinel so the model stays schema-valid and round-trips
    through ``save_cornermodel`` / ``load_cornermodel`` before the real
    Maestro testbench is known (a Pull replaces it).
    """
    return CornerModel(
        cornermodel_schema_version=1,
        name=name,
        project=project,
        testbench_id=testbench_id or "unset",
        modes={},
        columns=(),
    )


def cornermodel_from_union(union: Union, name: str = "corners") -> CornerModel:
    """Seed a cornermodel from a ``Union`` — every union row becomes an
    unmanaged column. Used to populate the Corners tab from Maestro's
    current corners when the project has no ``.cornermodel.json`` yet."""
    return CornerModel(
        cornermodel_schema_version=1,
        name=name,
        project=union.project,
        testbench_id=union.testbench_id,
        modes={},
        columns=tuple(make_unmanaged_column(r) for r in union.rows),
    )


@dataclass(frozen=True)
class AdoptionSplit:
    """Preview of a §6.3 收编: how an unmanaged column's vars split when
    adopted into a mode."""

    inherited: tuple[str, ...]          # var == mode base -> mode takes over
    overrides: dict[str, str]           # var present but differs -> override
    pvt_vars_kept: tuple[str, ...]      # var not in mode -> stays per-column


def adopt_column(
    column: Column, mode: Mode, pvt_label: str
) -> tuple[Column, AdoptionSplit]:
    """Adopt an unmanaged ``column`` into ``mode`` (spec §6.3).

    Three-way split of the column's vars against the mode's register set.
    Raises if a var that collides with a mode var is a sweep — an override
    must be scalar, so a swept register collision cannot be auto-resolved.
    """
    if column.is_managed:
        raise CornerModelValidationError(
            "adopt_column: column is already managed"
        )
    if not _PVT_LABEL_RE.match(pvt_label):
        raise CornerModelValidationError(
            f"adopt_column: pvt_label {pvt_label!r} does not match "
            f"^[A-Za-z0-9_]+$"
        )

    inherited: list[str] = []
    overrides: dict[str, str] = {}
    kept: dict[str, tuple[str, ...]] = {}
    kept_sweep: set[str] = set()

    for vname, tup in column.pvt_vars.items():
        if vname not in mode.vars:
            kept[vname] = tup
            if vname in column.pvt_sweep_keys:
                kept_sweep.add(vname)
            continue
        if vname in column.pvt_sweep_keys:
            raise CornerModelValidationError(
                f"adopt_column: var {vname!r} collides with a mode register "
                f"but is a sweep — resolve it manually before adopting"
            )
        if tup[0] == mode.vars[vname]:
            inherited.append(vname)
        else:
            overrides[vname] = tup[0]

    adopted = Column(
        mode=mode.name,
        enabled=column.enabled,
        pvt_vars=kept,
        models=column.models,
        pvt_label=pvt_label,
        name=None,
        alias=None,
        overrides=overrides,
        pvt_sweep_keys=frozenset(kept_sweep),
        model_sweep_indices=column.model_sweep_indices,
    )
    split = AdoptionSplit(
        inherited=tuple(sorted(inherited)),
        overrides=overrides,
        pvt_vars_kept=tuple(sorted(kept)),
    )
    return adopted, split


# ---------------------------------------------------------------------------
# Global edit (spec §2.3)
# ---------------------------------------------------------------------------


def set_mode_var(model: CornerModel, mode_name: str, var: str, value: str) -> CornerModel:
    """Return a new cornermodel with ``mode_name``'s ``var`` set to ``value``.

    This is the痛点-b single-source edit: every managed column referencing the
    mode picks up the new value at materialise time, except columns that have
    an override on ``var`` (which keep their override — and may now go red).
    """
    if mode_name not in model.modes:
        raise CornerModelValidationError(
            f"set_mode_var: no such mode {mode_name!r}"
        )
    mode = model.modes[mode_name]
    if var not in mode.vars:
        raise CornerModelValidationError(
            f"set_mode_var: {var!r} is not a var of mode {mode_name!r}"
        )
    new_modes = dict(model.modes)
    new_vars = dict(mode.vars)
    new_vars[var] = value
    new_modes[mode_name] = Mode(name=mode_name, vars=new_vars)
    return replace(model, modes=new_modes)


def set_column_override(
    model: CornerModel, column_index: int, var: str, value: str
) -> CornerModel:
    """Return a new cornermodel with a manual override on one managed column.

    This is the GUI's "edit a mode-managed cell" action: the edit does not
    touch the mode base — it pins ``var`` on this column only (spec §6.4 / D1).
    """
    column = model.columns[column_index]
    if not column.is_managed:
        raise CornerModelValidationError(
            "set_column_override: column is unmanaged"
        )
    if var not in model.modes[column.mode].vars:
        raise CornerModelValidationError(
            f"set_column_override: {var!r} is not a var of mode "
            f"{column.mode!r}"
        )
    new_overrides = dict(column.overrides)
    new_overrides[var] = value
    return _replace_column(model, column_index, replace(
        column, overrides=new_overrides
    ))


def set_pvt_var(
    model: CornerModel, column_index: int, var: str, value: str
) -> CornerModel:
    """Return a new cornermodel with one column's scalar PVT var set."""
    column = model.columns[column_index]
    if var not in column.pvt_vars:
        raise CornerModelValidationError(
            f"set_pvt_var: {var!r} is not a PVT var of this column"
        )
    new_pvt = dict(column.pvt_vars)
    new_pvt[var] = (value,)
    new_sweep = column.pvt_sweep_keys - {var}
    return _replace_column(model, column_index, replace(
        column, pvt_vars=new_pvt, pvt_sweep_keys=new_sweep
    ))


def _replace_column(
    model: CornerModel, index: int, new_column: Column
) -> CornerModel:
    cols = list(model.columns)
    cols[index] = new_column
    return replace(model, columns=tuple(cols))


def add_mode(
    model: CornerModel, name: str, mode_vars: dict[str, str]
) -> CornerModel:
    """Return a new cornermodel with a fresh mode added (spec §7.2 New mode)."""
    if not _MODE_NAME_RE.match(name):
        raise CornerModelValidationError(
            f"add_mode: mode name {name!r} must match ^[A-Za-z][A-Za-z0-9_]*$"
        )
    if name in model.modes:
        raise CornerModelValidationError(f"add_mode: mode {name!r} already exists")
    if not mode_vars:
        raise CornerModelValidationError(
            f"add_mode: mode {name!r} needs at least one register var"
        )
    for vname, vval in mode_vars.items():
        if not _VAR_NAME_RE.match(vname):
            raise CornerModelValidationError(
                f"add_mode: var name {vname!r} must match ^[A-Za-z][A-Za-z0-9_]*$"
            )
        if not isinstance(vval, str):
            raise CornerModelValidationError(
                f"add_mode: var {vname!r} value must be a scalar string"
            )
    new_modes = dict(model.modes)
    new_modes[name] = Mode(name=name, vars=dict(mode_vars))
    return replace(model, modes=new_modes)


def add_column(model: CornerModel, column: Column) -> CornerModel:
    """Return a new cornermodel with ``column`` appended (spec §7.2 New column).

    Re-checks the cross-column invariants the loader enforces: managed columns
    reference an existing mode, and effective names stay unique.
    """
    if column.is_managed and column.mode not in model.modes:
        raise CornerModelValidationError(
            f"add_column: mode {column.mode!r} is not defined"
        )
    new_name = effective_name(column)
    existing = {effective_name(c) for c in model.columns}
    if new_name in existing:
        raise CornerModelValidationError(
            f"add_column: effective name {new_name!r} collides with an "
            f"existing column"
        )
    return replace(model, columns=model.columns + (column,))


# ---------------------------------------------------------------------------
# Stage 2 operations — correlated axes, templates, bind/unbind (spec §4)
# ---------------------------------------------------------------------------


def add_correlated_axis(
    model: CornerModel, axis: CorrelatedAxis
) -> CornerModel:
    """Return a new cornermodel with a correlated axis added."""
    if axis.name in model.correlated_axes:
        raise CornerModelValidationError(
            f"add_correlated_axis: {axis.name!r} already exists"
        )
    member_set = set(axis.members)
    for ct in axis.tuples:
        if set(ct.values) != member_set:
            raise CornerModelValidationError(
                f"add_correlated_axis: tuple {ct.label!r} values must cover "
                f"exactly the members {sorted(member_set)}"
            )
    new_axes = dict(model.correlated_axes)
    new_axes[axis.name] = axis
    return replace(model, correlated_axes=new_axes)


def add_pvt_template(
    model: CornerModel, template: PvtTemplate
) -> CornerModel:
    """Return a new cornermodel with a PVT template added."""
    if template.name in model.pvt_templates:
        raise CornerModelValidationError(
            f"add_pvt_template: {template.name!r} already exists"
        )
    for tc in template.columns:
        for axis_name in tc.correlated_axes:
            if axis_name not in model.correlated_axes:
                raise CornerModelValidationError(
                    f"add_pvt_template: column {tc.pvt_label!r} references "
                    f"undefined correlated axis {axis_name!r}"
                )
    new_templates = dict(model.pvt_templates)
    new_templates[template.name] = template
    return replace(model, pvt_templates=new_templates)


def apply_template(
    model: CornerModel, mode_name: str, template_name: str,
    variant: str | None = None,
) -> CornerModel:
    """Generate the columns of ``template_name`` for ``mode_name`` (or for a
    variant of it) and bind them (spec §4). A column whose effective name
    already exists is reused — re-applying is idempotent on names."""
    if mode_name not in model.modes:
        raise CornerModelValidationError(
            f"apply_template: mode {mode_name!r} is not defined"
        )
    if template_name not in model.pvt_templates:
        raise CornerModelValidationError(
            f"apply_template: template {template_name!r} is not defined"
        )
    if variant is not None:
        if variant not in model.variants:
            raise CornerModelValidationError(
                f"apply_template: variant {variant!r} is not defined"
            )
        if model.variants[variant].base_mode != mode_name:
            raise CornerModelValidationError(
                f"apply_template: variant {variant!r} base_mode != "
                f"{mode_name!r}"
            )
    template = model.pvt_templates[template_name]
    mode_var_keys = set(model.modes[mode_name].vars)
    existing = {effective_name(c) for c in model.columns}
    new_columns: list[Column] = list(model.columns)

    for tc in template.columns:
        for axis_name in tc.correlated_axes:
            members = set(model.correlated_axes[axis_name].members)
            if members & (set(tc.pvt_vars) | mode_var_keys):
                raise CornerModelValidationError(
                    f"apply_template: axis {axis_name!r} members collide with "
                    f"template column {tc.pvt_label!r} pvt_vars / mode regs"
                )
        column = Column(
            mode=mode_name, enabled=True,
            pvt_vars=dict(tc.pvt_vars), models=(),
            pvt_label=tc.pvt_label,
            pvt_sweep_keys=tc.pvt_sweep_keys,
            correlated_axes=tc.correlated_axes,
            template=template_name,
            variant=variant,
            axis_levels=dict(tc.axis_levels),
        )
        name = effective_name(column)
        if name in existing:
            continue  # already present — reuse, do not duplicate
        existing.add(name)
        new_columns.append(column)

    new_bindings = list(model.template_bindings)
    binding = TemplateBinding(
        mode=mode_name, template=template_name, variant=variant
    )
    if binding not in new_bindings:
        new_bindings.append(binding)
    return replace(
        model, columns=tuple(new_columns),
        template_bindings=tuple(new_bindings),
    )


def unbind_template(
    model: CornerModel, mode_name: str, template_name: str,
    variant: str | None = None,
) -> CornerModel:
    """Drop the (mode/variant, template) binding and freeze its generated
    columns — D3: columns stay, values kept, only ``template`` provenance is
    cleared so they become plain managed columns."""
    new_bindings = tuple(
        b for b in model.template_bindings
        if not (b.mode == mode_name and b.template == template_name
                and b.variant == variant)
    )
    new_columns = tuple(
        replace(c, template=None)
        if (c.mode == mode_name and c.template == template_name
            and c.variant == variant)
        else c
        for c in model.columns
    )
    return replace(
        model, columns=new_columns, template_bindings=new_bindings
    )


# ---------------------------------------------------------------------------
# Stage 3 operations — variants (spec §4)
# ---------------------------------------------------------------------------


def add_variant(model: CornerModel, variant: Variant) -> CornerModel:
    """Return a new cornermodel with a variant added (spec §4)."""
    if variant.name in model.variants:
        raise CornerModelValidationError(
            f"add_variant: variant {variant.name!r} already exists"
        )
    if variant.base_mode not in model.modes:
        raise CornerModelValidationError(
            f"add_variant: base_mode {variant.base_mode!r} is not defined"
        )
    base_keys = set(model.modes[variant.base_mode].vars)
    bad = set(variant.vars) - base_keys
    if bad:
        raise CornerModelValidationError(
            f"add_variant: vars {sorted(bad)} are not registers of base mode "
            f"{variant.base_mode!r}"
        )
    new_variants = dict(model.variants)
    new_variants[variant.name] = variant
    return replace(model, variants=new_variants)


def set_variant_var(
    model: CornerModel, variant_name: str, var: str, value: str
) -> CornerModel:
    """Return a new cornermodel with one variant's overlay var set — global
    edit for the variant layer (spec §4). ``var`` must be a register of the
    base mode; setting it adds variant coverage if not already covered."""
    if variant_name not in model.variants:
        raise CornerModelValidationError(
            f"set_variant_var: no such variant {variant_name!r}"
        )
    variant = model.variants[variant_name]
    if var not in model.modes[variant.base_mode].vars:
        raise CornerModelValidationError(
            f"set_variant_var: {var!r} is not a register of base mode "
            f"{variant.base_mode!r}"
        )
    new_vars = dict(variant.vars)
    new_vars[var] = value
    new_variants = dict(model.variants)
    new_variants[variant_name] = replace(variant, vars=new_vars)
    return replace(model, variants=new_variants)


# ---------------------------------------------------------------------------
# Stage 4 operations — run-sets (spec §3)
# ---------------------------------------------------------------------------


def add_run_set(
    model: CornerModel, name: str, columns: tuple[str, ...]
) -> CornerModel:
    """Return a new cornermodel with a run-set added (spec §3)."""
    if not _MODE_NAME_RE.match(name):
        raise CornerModelValidationError(
            f"add_run_set: name {name!r} must match ^[A-Za-z][A-Za-z0-9_]*$"
        )
    if name in model.run_sets:
        raise CornerModelValidationError(
            f"add_run_set: run-set {name!r} already exists"
        )
    new_sets = dict(model.run_sets)
    new_sets[name] = RunSet(name=name, columns=tuple(columns))
    return replace(model, run_sets=new_sets)


def apply_run_set(model: CornerModel, set_name: str) -> CornerModel:
    """Switch to a run-set: ``column.enabled = effective_name ∈ set`` for
    every column (spec §2.2). Columns not in the set are disabled."""
    if set_name not in model.run_sets:
        raise CornerModelValidationError(
            f"apply_run_set: no such run-set {set_name!r}"
        )
    members = set(model.run_sets[set_name].columns)
    new_columns = tuple(
        replace(c, enabled=(effective_name(c) in members))
        for c in model.columns
    )
    return replace(model, columns=new_columns)


def run_set_membership(model: CornerModel, set_name: str) -> set[str]:
    """The set of column effective names a run-set selects (GUI filter)."""
    if set_name not in model.run_sets:
        raise CornerModelValidationError(
            f"run_set_membership: no such run-set {set_name!r}"
        )
    return set(model.run_sets[set_name].columns)


# ---------------------------------------------------------------------------
# Stage 5 — var order, soft validation, cross-project library (spec §2/§4/§5)
# ---------------------------------------------------------------------------


def ordered_var_rows(model: CornerModel, all_vars: set[str]) -> list[str]:
    """Order variable-row names: ``var_order`` entries first (in order), then
    the rest in the default register-before-PVT alphabetical order."""
    register_vars: set[str] = set()
    for mode in model.modes.values():
        register_vars |= set(mode.vars)
    leading = [v for v in model.var_order if v in all_vars]
    rest = all_vars - set(leading)
    tail = (
        sorted(v for v in rest if v in register_vars)
        + sorted(v for v in rest if v not in register_vars)
    )
    return leading + tail


def set_var_order(
    model: CornerModel, ordered_vars: tuple[str, ...]
) -> CornerModel:
    """Return a new cornermodel with an explicit variable-row order (spec §2)."""
    return replace(model, var_order=tuple(ordered_vars))


_MODEL_FILE_EXTS = (".s5p", ".scs", ".mod", ".sp", ".cir")


@dataclass(frozen=True)
class CheckIssue:
    """A non-blocking soft-validation finding (spec §4)."""

    severity: str   # "error" | "warning"
    code: str       # "missing_file" | "dangling_column"
    where: str
    message: str


def check_cornermodel(
    model: CornerModel, base_dir: Path | str | None = None,
    profile: "PvtProfile | None" = None,
) -> list[CheckIssue]:
    """Run the soft checks — file existence, dangling run-set refs, and
    (Stage 6) ``axis_levels`` that point at axes/levels missing from the
    bound profile.

    Never raises: returns a (possibly empty) issue list for the GUI to surface.
    """
    issues: list[CheckIssue] = []
    base = Path(base_dir).expanduser() if base_dir is not None else None

    def _check_file(value: str, where: str) -> None:
        if not value.lower().endswith(_MODEL_FILE_EXTS):
            return
        if base is None:
            return
        path = Path(value)
        resolved = path if path.is_absolute() else base / path
        if not resolved.exists():
            issues.append(CheckIssue(
                severity="warning", code="missing_file", where=where,
                message=f"referenced model file does not exist: {value}",
            ))

    for axis in model.correlated_axes.values():
        for ct in axis.tuples:
            for vk, vv in ct.values.items():
                _check_file(
                    vv, f"correlated_axes[{axis.name}].{ct.label}.{vk}"
                )

    for column in model.columns:
        for j, m in enumerate(column.models):
            if m.file_abs and base is not None:
                if not Path(m.file_abs).exists():
                    issues.append(CheckIssue(
                        severity="warning", code="missing_file",
                        where=f"column {effective_name(column)} models[{j}]",
                        message=f"_file_abs does not exist: {m.file_abs}",
                    ))

    known = {effective_name(c) for c in model.columns}
    for rs in model.run_sets.values():
        for col_name in rs.columns:
            if col_name not in known:
                issues.append(CheckIssue(
                    severity="warning", code="dangling_column",
                    where=f"run_sets[{rs.name}]",
                    message=f"references unknown column {col_name!r}",
                ))

    # Stage 6 — axis_levels that the bound profile cannot resolve.
    if model.pvt_profile is not None and profile is None:
        issues.append(CheckIssue(
            severity="warning", code="missing_profile",
            where="pvt_profile",
            message=f"bound profile {model.pvt_profile!r} is not loaded",
        ))
    if profile is not None:
        for column in model.columns:
            for axis_name, level_name in column.axis_levels.items():
                if _resolve_level(profile, axis_name, level_name) is None:
                    issues.append(CheckIssue(
                        severity="error", code="unknown_axis_level",
                        where=f"column {effective_name(column)}",
                        message=(
                            f"axis_levels {axis_name}:{level_name} not in "
                            f"profile {profile.name!r}"
                        ),
                    ))
    return issues


CORNERLIB_FILE_SUFFIX = ".cornerlib.json"
_SUPPORTED_LIB_VERSIONS = frozenset({1})


@dataclass(frozen=True)
class CornerLibrary:
    """A reusable, testbench-independent library of templates + axes (spec §5)."""

    cornerlib_schema_version: int
    name: str
    correlated_axes: dict[str, CorrelatedAxis]
    pvt_templates: dict[str, PvtTemplate]


def load_library(path: Path | str) -> CornerLibrary:
    p = Path(path).expanduser().resolve()
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise CornerModelMalformedError(f"{p}: invalid JSON — {exc}") from exc
    except OSError as exc:
        raise CornerModelMalformedError(f"{p}: cannot read — {exc}") from exc
    if not isinstance(data, dict):
        raise CornerModelMalformedError(f"{p}: top-level must be a JSON object")

    raw_ver = data.get("cornerlib_schema_version")
    if raw_ver not in _SUPPORTED_LIB_VERSIONS:
        raise CornerModelSchemaVersionError(
            f"{p}: cornerlib_schema_version {raw_ver!r} not supported"
        )
    lib_name = _require_str(p, data, "name")
    axes = _validate_correlated_axes(p, data)
    templates = _validate_templates(p, data, axes)
    return CornerLibrary(
        cornerlib_schema_version=raw_ver, name=lib_name,
        correlated_axes=axes, pvt_templates=templates,
    )


def export_library(model: CornerModel, name: str) -> CornerLibrary:
    """Extract the cornermodel's templates + axes into a reusable library."""
    if not _MODEL_NAME_RE.match(name):
        raise CornerModelValidationError(
            f"export_library: name {name!r} must match ^[a-z0-9_-]+$"
        )
    return CornerLibrary(
        cornerlib_schema_version=1, name=name,
        correlated_axes=dict(model.correlated_axes),
        pvt_templates=dict(model.pvt_templates),
    )


def library_to_dict(library: CornerLibrary) -> dict:
    out: dict = {
        "cornerlib_schema_version": library.cornerlib_schema_version,
        "name": library.name,
    }
    if library.correlated_axes:
        out["correlated_axes"] = {
            ax.name: {
                "members": list(ax.members),
                "tuples": [
                    {"label": t.label, "values": dict(t.values)}
                    for t in ax.tuples
                ],
            }
            for ax in library.correlated_axes.values()
        }
    if library.pvt_templates:
        out["pvt_templates"] = {
            t.name: {"columns": [
                _template_column_to_dict(tc) for tc in t.columns
            ]}
            for t in library.pvt_templates.values()
        }
    return out


def import_library(
    model: CornerModel, library: CornerLibrary
) -> CornerModel:
    """Merge a library's axes + templates into a cornermodel (spec §5).

    A name collision on either an axis or a template is a hard error — the
    library never silently overwrites the project's own definitions.
    """
    axis_clash = set(library.correlated_axes) & set(model.correlated_axes)
    if axis_clash:
        raise CornerModelValidationError(
            f"import_library: correlated axes {sorted(axis_clash)} already "
            f"exist in the cornermodel"
        )
    tmpl_clash = set(library.pvt_templates) & set(model.pvt_templates)
    if tmpl_clash:
        raise CornerModelValidationError(
            f"import_library: templates {sorted(tmpl_clash)} already exist"
        )
    new_axes = {**model.correlated_axes, **library.correlated_axes}
    new_templates = {**model.pvt_templates, **library.pvt_templates}
    return replace(
        model, correlated_axes=new_axes, pvt_templates=new_templates
    )


# ---------------------------------------------------------------------------
# Stage 6 — PVT Profile loader + serialisation (spec §2 / §6)
# ---------------------------------------------------------------------------


PVTPROFILE_FILE_SUFFIX = ".pvtprofile.json"
_SUPPORTED_PROFILE_VERSIONS = frozenset({1})


def load_pvtprofile(
    path: Path | str, expected_project: str | None = None
) -> PvtProfile:
    """Load a ``.pvtprofile.json`` — the per-project PVT semantic map (§2)."""
    p = Path(path).expanduser().resolve()
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise CornerModelMalformedError(f"{p}: invalid JSON — {exc}") from exc
    except OSError as exc:
        raise CornerModelMalformedError(f"{p}: cannot read — {exc}") from exc
    if not isinstance(data, dict):
        raise CornerModelMalformedError(f"{p}: top-level must be a JSON object")

    raw_ver = data.get("pvtprofile_schema_version")
    if raw_ver not in _SUPPORTED_PROFILE_VERSIONS:
        raise CornerModelSchemaVersionError(
            f"{p}: pvtprofile_schema_version {raw_ver!r} not supported"
        )
    name = _require_str(p, data, "name")
    if not p.name.endswith(PVTPROFILE_FILE_SUFFIX):
        raise CornerModelValidationError(
            f"{p}: filename must end with '{PVTPROFILE_FILE_SUFFIX}'"
        )
    if p.name[: -len(PVTPROFILE_FILE_SUFFIX)] != name:
        raise CornerModelValidationError(
            f"{p}: 'name' {name!r} must equal filename basename"
        )
    project = _require_str(p, data, "project")
    if expected_project is not None and project != expected_project:
        raise CornerModelValidationError(
            f"{p}: 'project' {project!r} != enclosing project "
            f"{expected_project!r}"
        )
    return PvtProfile(
        pvtprofile_schema_version=raw_ver, name=name, project=project,
        axes=_validate_axes(p, data),
    )


def _validate_axes(path: Path, data: dict) -> dict[str, PvtAxis]:
    raw = data.get("axes")
    if not isinstance(raw, dict) or len(raw) == 0:
        raise CornerModelValidationError(
            f"{path}: 'axes' must be a non-empty JSON object"
        )
    out: dict[str, PvtAxis] = {}
    for axis_name, raw_axis in raw.items():
        where = f"{path}: axes[{axis_name!r}]"
        if not _VAR_NAME_RE.match(axis_name):
            raise CornerModelValidationError(
                f"{where}: axis name must match ^[A-Za-z][A-Za-z0-9_]*$"
            )
        if not isinstance(raw_axis, dict):
            raise CornerModelValidationError(f"{where}: must be a JSON object")
        raw_levels = raw_axis.get("levels")
        if not isinstance(raw_levels, dict) or len(raw_levels) == 0:
            raise CornerModelValidationError(
                f"{where}: 'levels' must be a non-empty JSON object"
            )
        levels: dict[str, AxisLevel] = {}
        for level_name, raw_level in raw_levels.items():
            lw = f"{where}: levels[{level_name!r}]"
            if not _PVT_LABEL_RE.match(level_name):
                raise CornerModelValidationError(
                    f"{lw}: level name must match ^[A-Za-z0-9_]+$"
                )
            if not isinstance(raw_level, dict):
                raise CornerModelValidationError(f"{lw}: must be a JSON object")
            lvl_vars, sweep = _parse_var_block(
                lw, raw_level.get("vars", {}), "vars"
            )
            models = _parse_level_models(lw, raw_level.get("models", []))
            if not lvl_vars and not models:
                raise CornerModelValidationError(
                    f"{lw}: a level needs at least one of 'vars' / 'models'"
                )
            levels[level_name] = AxisLevel(
                name=level_name, vars=lvl_vars, vars_sweep_keys=sweep,
                models=models,
            )
        out[axis_name] = PvtAxis(name=axis_name, levels=levels)
    return out


def _parse_level_models(
    where: str, raw: object
) -> tuple[ModelSectionAssignment, ...]:
    if not isinstance(raw, list):
        raise CornerModelValidationError(f"{where}: 'models' must be an array")
    out: list[ModelSectionAssignment] = []
    for j, rm in enumerate(raw):
        mw = f"{where}: models[{j}]"
        if not isinstance(rm, dict):
            raise CornerModelValidationError(f"{mw}: must be a JSON object")
        section = rm.get("section")
        if not isinstance(section, str) or section == "":
            raise CornerModelValidationError(
                f"{mw}: 'section' must be a non-empty string"
            )
        file_ = rm.get("file")
        if file_ is not None and (not isinstance(file_, str) or file_ == ""):
            raise CornerModelValidationError(
                f"{mw}: 'file' must be a non-empty string if present"
            )
        out.append(ModelSectionAssignment(section=section, file=file_))
    return tuple(out)


def profile_to_dict(profile: PvtProfile) -> dict:
    axes: dict = {}
    for axis in profile.axes.values():
        levels: dict = {}
        for level in axis.levels.values():
            entry: dict = {}
            if level.vars:
                entry["vars"] = {
                    v: (list(tup) if v in level.vars_sweep_keys else tup[0])
                    for v, tup in level.vars.items()
                }
            if level.models:
                entry["models"] = [
                    ({"section": m.section} if m.file is None
                     else {"file": m.file, "section": m.section})
                    for m in level.models
                ]
            levels[level.name] = entry
        axes[axis.name] = {"levels": levels}
    return {
        "pvtprofile_schema_version": profile.pvtprofile_schema_version,
        "name": profile.name,
        "project": profile.project,
        "axes": axes,
    }


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def to_dict(model: CornerModel) -> dict:
    """Serialise a cornermodel to a JSON-ready dict (round-trips load)."""
    columns: list[dict] = []
    for col in model.columns:
        entry: dict = {"mode": col.mode, "enabled": col.enabled}
        if col.is_managed:
            entry["pvt_label"] = col.pvt_label
            if col.alias is not None:
                entry["alias"] = col.alias
            if col.overrides:
                entry["overrides"] = dict(col.overrides)
        else:
            entry["name"] = col.name
        if col.pvt_vars:
            entry["pvt_vars"] = {
                v: (list(tup) if v in col.pvt_sweep_keys else tup[0])
                for v, tup in col.pvt_vars.items()
            }
        if col.models:
            entry["models"] = [
                _model_to_dict(m, j in col.model_sweep_indices)
                for j, m in enumerate(col.models)
            ]
        if col.correlated_axes:
            entry["correlated_axes"] = list(col.correlated_axes)
        if col.template is not None:
            entry["template"] = col.template
        if col.variant is not None:
            entry["variant"] = col.variant
        if col.axis_levels:
            entry["axis_levels"] = dict(col.axis_levels)
        columns.append(entry)

    out: dict = {
        "cornermodel_schema_version": model.cornermodel_schema_version,
        "name": model.name,
        "project": model.project,
        "testbench_id": model.testbench_id,
        "modes": {
            name: {"vars": dict(mode.vars)}
            for name, mode in model.modes.items()
        },
        "columns": columns,
    }
    if model.correlated_axes:
        out["correlated_axes"] = {
            ax.name: {
                "members": list(ax.members),
                "tuples": [
                    {"label": t.label, "values": dict(t.values)}
                    for t in ax.tuples
                ],
            }
            for ax in model.correlated_axes.values()
        }
    if model.pvt_templates:
        out["pvt_templates"] = {
            tmpl.name: {
                "columns": [
                    _template_column_to_dict(tc) for tc in tmpl.columns
                ]
            }
            for tmpl in model.pvt_templates.values()
        }
    if model.template_bindings:
        binds: list[dict] = []
        for b in model.template_bindings:
            entry = {"mode": b.mode, "template": b.template}
            if b.variant is not None:
                entry["variant"] = b.variant
            binds.append(entry)
        out["template_bindings"] = binds
    if model.variants:
        out["variants"] = {
            v.name: {"base_mode": v.base_mode, "vars": dict(v.vars)}
            for v in model.variants.values()
        }
    if model.run_sets:
        out["run_sets"] = {
            s.name: {"columns": list(s.columns)}
            for s in model.run_sets.values()
        }
    if model.var_order:
        out["var_order"] = list(model.var_order)
    if model.pvt_profile is not None:
        out["pvt_profile"] = model.pvt_profile
    return out


def _template_column_to_dict(tc: TemplateColumn) -> dict:
    entry: dict = {"pvt_label": tc.pvt_label}
    if tc.pvt_vars:
        entry["pvt_vars"] = {
            v: (list(tup) if v in tc.pvt_sweep_keys else tup[0])
            for v, tup in tc.pvt_vars.items()
        }
    if tc.correlated_axes:
        entry["correlated_axes"] = list(tc.correlated_axes)
    if tc.axis_levels:
        entry["axis_levels"] = dict(tc.axis_levels)
    return entry


def _model_to_dict(entry: ModelEntry, is_sweep: bool) -> dict:
    out: dict = {
        "file": entry.file,
        "block": entry.block,
        "test": entry.test,
        "section": list(entry.section) if is_sweep else entry.section[0],
    }
    if entry.file_abs is not None:
        out["_file_abs"] = entry.file_abs
    return out


def save_cornermodel(model: CornerModel, path: Path | str) -> None:
    p = Path(path).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(to_dict(model), f, indent=2, ensure_ascii=False)
        f.write("\n")
