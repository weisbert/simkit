"""`.cornermodel.json` sidecar loader + Phase 5 Stage 1 corner-manager model.

Implements ``docs/phase5_stage1_spec.md``. Pure-Python, stdlib-only.

A *cornermodel* is a model layer on top of Phase 2's ``Union``: it adds the
**mode** abstraction (a named, single-source bag of register vars) and
materialises down to a ``Union`` for explode / CSV / push. See ``materialize``.

Stage 1 scope: modes + columns + global edit + auto-naming + reconciliation.
Variants / correlated axes are Stage 2-5 and absent here.
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
    """One level of a dimension — assigns every member var, and for a
    section-bearing dimension the model-file section, all at once."""

    label: str
    values: dict[str, str]
    section: str | None = None


@dataclass(frozen=True)
class CorrelatedAxis:
    """A dimension — a reusable list of levels whose member vars vary together
    (spec §2.1, 痛点 h). Crossing treats the whole dimension as ONE axis of
    length ``len(tuples)``, not the Cartesian product of its members.

    ``model_file`` set → a section-bearing dimension: every level also picks
    a section of that model file (the process-corner case, TT/SS/FF…).
    """

    name: str
    members: tuple[str, ...]
    tuples: tuple[CorrelatedTuple, ...]
    model_file: str | None = None


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

    Authored once at project kickoff. Columns reference semantic
    ``axis:level`` tokens; ``materialize`` resolves them through the profile,
    so the same cornermodel ports to another project under a different
    profile.
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
    # Stage 2: project-library dimensions this corner crosses, by name.
    correlated_axes: tuple[str, ...] = ()
    # Per crossed dimension, the subset of level labels this corner uses —
    # a dimension absent here (or mapped to ()) crosses all its levels.
    selected_levels: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Dimensions defined inline on this corner (not in the project library).
    inline_axes: tuple[CorrelatedAxis, ...] = ()
    # Stage 3: the variant this column hangs on (its mode = variant.base_mode).
    variant: str | None = None
    # Stage 6: semantic axis→level tokens resolved through the PVT profile.
    axis_levels: dict[str, str] = field(default_factory=dict)
    # 2026 UX: tests this corner is scoped to. Empty = all tests (the Maestro
    # default); non-empty mirrors Cadence's per-corner Tests selection.
    tests: tuple[str, ...] = ()

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
    # Stage 2 addition — default-empty so Stage 1 sidecars load unchanged.
    correlated_axes: dict[str, CorrelatedAxis] = field(default_factory=dict)
    # Stage 3 addition.
    variants: dict[str, Variant] = field(default_factory=dict)
    # Stage 4 addition.
    run_sets: dict[str, RunSet] = field(default_factory=dict)
    # Stage 5 addition — explicit variable-row display order.
    var_order: tuple[str, ...] = ()
    # Stage 6 addition — name of the bound PVT profile (resolved at materialize).
    pvt_profile: str | None = None
    # 2026 UX — the master list of every test name in the testbench, in
    # Maestro order. Captured on pull; the Tests grid renders one row per
    # entry. Empty until a pull has populated it.
    tests: tuple[str, ...] = ()


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
    columns = _validate_columns(p, data, modes, correlated_axes, variants)
    run_sets = _validate_run_sets(p, data)
    var_order = _validate_var_order(p, data)
    pvt_profile = data.get("pvt_profile")
    if pvt_profile is not None and not isinstance(pvt_profile, str):
        raise CornerModelValidationError(
            f"{p}: 'pvt_profile' must be a string or absent"
        )
    master_tests = _parse_tests(str(p), data)

    return CornerModel(
        cornermodel_schema_version=schema_version,
        name=name,
        project=project,
        testbench_id=testbench_id,
        modes=modes,
        columns=columns,
        correlated_axes=correlated_axes,
        variants=variants,
        run_sets=run_sets,
        var_order=var_order,
        pvt_profile=pvt_profile,
        tests=master_tests,
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
    selected_levels = _parse_selected_levels(
        where, raw, col_axes, correlated_axes
    )
    inline_axes = _parse_inline_axes(where, raw)

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
        col, correlated_axes=col_axes, selected_levels=selected_levels,
        inline_axes=inline_axes, variant=variant,
        axis_levels=_parse_axis_levels(where, raw),
        tests=_parse_tests(where, raw),
    )

    # Spec §2.4 / §6.3 — dimension members must not collide with the column's
    # PVT vars or its mode's register vars.
    mode_var_keys = set(modes[mode_name].vars) if mode_name in modes else set()
    crossed = [correlated_axes[a] for a in col_axes] + list(inline_axes)
    for axis in crossed:
        members = set(axis.members)
        if members & set(pvt_vars):
            raise CornerModelValidationError(
                f"{where}: dimension {axis.name!r} members "
                f"{sorted(members & set(pvt_vars))} collide with pvt_vars"
            )
        if members & mode_var_keys:
            raise CornerModelValidationError(
                f"{where}: dimension {axis.name!r} members "
                f"{sorted(members & mode_var_keys)} collide with mode registers"
            )

    # Spec §5 rule 7 — every column must materialise to a legal Phase 2 row.
    if (len(pvt_vars) == 0 and len(models) == 0 and not col.is_managed
            and not col_axes and not inline_axes):
        raise CornerModelValidationError(
            f"{where}: unmanaged column has neither vars, models nor axes"
        )
    return col


def _parse_tests(where: str, raw: dict) -> tuple[str, ...]:
    """Parse the ``tests`` field — the tests a corner is scoped to. Absent or
    empty means the corner applies to all tests (the Maestro default)."""
    raw_t = raw.get("tests", [])
    if not isinstance(raw_t, list):
        raise CornerModelValidationError(
            f"{where}: 'tests' must be a JSON array of strings"
        )
    out: list[str] = []
    for t in raw_t:
        if not isinstance(t, str) or not t.strip():
            raise CornerModelValidationError(
                f"{where}: each 'tests' entry must be a non-empty string"
            )
        out.append(t)
    return tuple(out)


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


def _parse_selected_levels(
    where: str, raw: dict, col_axes: tuple[str, ...],
    correlated_axes: dict[str, CorrelatedAxis],
) -> dict[str, tuple[str, ...]]:
    """Per crossed dimension, the subset of level labels this corner uses."""
    raw_sel = raw.get("selected_levels", {})
    if not isinstance(raw_sel, dict):
        raise CornerModelValidationError(
            f"{where}: 'selected_levels' must be a JSON object"
        )
    out: dict[str, tuple[str, ...]] = {}
    for axis_name, labels in raw_sel.items():
        if axis_name not in col_axes:
            raise CornerModelValidationError(
                f"{where}: selected_levels references dimension "
                f"{axis_name!r} which this corner does not cross"
            )
        if not isinstance(labels, list):
            raise CornerModelValidationError(
                f"{where}: selected_levels[{axis_name!r}] must be a JSON array"
            )
        valid = {ct.label for ct in correlated_axes[axis_name].tuples}
        for lab in labels:
            if lab not in valid:
                raise CornerModelValidationError(
                    f"{where}: selected_levels[{axis_name!r}] level {lab!r} "
                    f"is not a level of that dimension"
                )
        out[axis_name] = tuple(labels)
    return out


def _parse_inline_axes(where: str, raw: dict) -> tuple[CorrelatedAxis, ...]:
    """Dimensions defined inline on a corner (not in the project library)."""
    raw_inline = raw.get("inline_axes", [])
    if not isinstance(raw_inline, list):
        raise CornerModelValidationError(
            f"{where}: 'inline_axes' must be a JSON array"
        )
    out: list[CorrelatedAxis] = []
    seen: set[str] = set()
    for i, raw_axis in enumerate(raw_inline):
        if not isinstance(raw_axis, dict):
            raise CornerModelValidationError(
                f"{where}: inline_axes[{i}] must be a JSON object"
            )
        axis = _validate_one_axis(
            f"{where}: inline_axes[{i}]", raw_axis.get("name"), raw_axis
        )
        if axis.name in seen:
            raise CornerModelValidationError(
                f"{where}: inline_axes has a duplicate name {axis.name!r}"
            )
        seen.add(axis.name)
        out.append(axis)
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
# Stage 2 loaders — correlated axes (spec §2)
# ---------------------------------------------------------------------------


def _validate_one_axis(
    where: str, axis_name: object, raw_axis: object
) -> CorrelatedAxis:
    """Validate one dimension (a project-library or an inline one)."""
    if not isinstance(axis_name, str) or not _VAR_NAME_RE.match(axis_name):
        raise CornerModelValidationError(
            f"{where}: dimension name {axis_name!r} must match "
            f"^[A-Za-z][A-Za-z0-9_]*$"
        )
    if not isinstance(raw_axis, dict):
        raise CornerModelValidationError(f"{where}: must be a JSON object")
    model_file = raw_axis.get("model_file")
    if model_file is not None and not isinstance(model_file, str):
        raise CornerModelValidationError(
            f"{where}: 'model_file' must be a string or absent"
        )
    raw_members = raw_axis.get("members", [])
    if not isinstance(raw_members, list):
        raise CornerModelValidationError(
            f"{where}: 'members' must be a JSON array"
        )
    for m in raw_members:
        if not isinstance(m, str) or not _VAR_NAME_RE.match(m):
            raise CornerModelValidationError(
                f"{where}: member {m!r} must match ^[A-Za-z][A-Za-z0-9_]*$"
            )
    members = tuple(raw_members)
    member_set = set(members)
    if not members and model_file is None:
        raise CornerModelValidationError(
            f"{where}: a dimension needs at least one member variable or a "
            f"model file"
        )
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
                f"{tw}: duplicate level label {label!r}"
            )
        seen_labels.add(label)
        values = raw_t.get("values", {})
        if not isinstance(values, dict) or set(values) != member_set:
            raise CornerModelValidationError(
                f"{tw}: 'values' keys must be exactly the members "
                f"{sorted(member_set)}"
            )
        for vk, vv in values.items():
            if not isinstance(vv, str):
                raise CornerModelValidationError(
                    f"{tw}: value for {vk!r} must be a scalar string"
                )
        section = raw_t.get("section")
        if section is not None and not isinstance(section, str):
            raise CornerModelValidationError(
                f"{tw}: 'section' must be a string or absent"
            )
        if (section is not None) != (model_file is not None):
            raise CornerModelValidationError(
                f"{tw}: 'section' and the dimension's 'model_file' must "
                f"both be set or both absent"
            )
        tuples.append(CorrelatedTuple(
            label=label, values=dict(values), section=section
        ))
    return CorrelatedAxis(
        name=axis_name, members=members, tuples=tuple(tuples),
        model_file=model_file,
    )


def _validate_correlated_axes(
    path: Path, data: dict
) -> dict[str, CorrelatedAxis]:
    raw = data.get("correlated_axes", {})
    if not isinstance(raw, dict):
        raise CornerModelValidationError(
            f"{path}: 'correlated_axes' must be a JSON object"
        )
    return {
        axis_name: _validate_one_axis(
            f"{path}: correlated_axes[{axis_name!r}]", axis_name, raw_axis
        )
        for axis_name, raw_axis in raw.items()
    }


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


def _set_model_section(
    models: list[ModelEntry], model_file: str, section: tuple[str, ...]
) -> list[ModelEntry]:
    """Set ``section`` on the model entry whose file matches ``model_file``,
    appending a fresh entry if none does."""
    out = list(models)
    for i, m in enumerate(out):
        if m.file == model_file:
            out[i] = replace(m, section=section)
            return out
    out.append(ModelEntry(
        file=model_file, block=_union._DEFAULT_MODEL_BLOCK,
        test=_union._DEFAULT_MODEL_TEST, section=section,
    ))
    return out


def _crossed_axes(
    model: CornerModel, column: Column
) -> list[CorrelatedAxis]:
    """The dimensions a corner crosses — library dimensions narrowed to the
    corner's selected level subset, then any inline dimensions."""
    out: list[CorrelatedAxis] = []
    for name in column.correlated_axes:
        ax = model.correlated_axes[name]
        sel = column.selected_levels.get(name)
        if sel:
            keep = set(sel)
            ax = replace(ax, tuples=tuple(
                t for t in ax.tuples if t.label in keep
            ))
        out.append(ax)
    out.extend(column.inline_axes)
    return out


def _column_models(
    column: Column, profile: "PvtProfile | None" = None,
    model: "CornerModel | None" = None,
) -> tuple[ModelEntry, ...]:
    """The column's model entries, with process-axis levels resolved (Stage 6).

    A profile process level's no-``file`` assignment sets the section on every
    existing model entry; a ``file``-bearing one targets / appends that model.
    When ``model`` is given, a crossed section-bearing dimension also folds in
    — its selected levels' sections become the model file's swept section.
    """
    models: list[ModelEntry] = list(column.models)
    if profile is not None:
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
                            test=_union._DEFAULT_MODEL_TEST,
                            section=(msa.section,),
                        ))
    if model is not None:
        for ax in _crossed_axes(model, column):
            if ax.model_file is None:
                continue
            sections = tuple(
                ct.section for ct in ax.tuples if ct.section is not None
            )
            if sections:
                models = _set_model_section(models, ax.model_file, sections)
    return tuple(models)


def column_models(
    column: Column, profile: "PvtProfile | None" = None,
    model: "CornerModel | None" = None,
) -> tuple[ModelEntry, ...]:
    """Public view of a column's resolved process-model entries — what the
    corner table renders as its Model Files rows (one row per file, the cell
    showing that model's section / process corner). Pass ``model`` so a
    crossed section dimension's sections show in the cell."""
    return _column_models(column, profile, model)


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
    base_models = _column_models(column, profile)
    axes = _crossed_axes(model, column)

    if not axes:
        return [UnionRow(
            row_name=base_name, vars=base_vars, models=base_models,
            sweep_var_keys=frozenset(sweep),
            sweep_model_indices=column.model_sweep_indices,
            enabled=column.enabled,
            tests=column.tests,
        )]

    rows: list[UnionRow] = []
    for combo in itertools.product(*[ax.tuples for ax in axes]):
        row_vars = dict(base_vars)
        row_models = list(base_models)
        labels: list[str] = []
        for ax, ct in zip(axes, combo):
            labels.append(ct.label)
            for vk, vv in ct.values.items():
                row_vars[vk] = (vv,)
            if ax.model_file is not None and ct.section is not None:
                row_models = _set_model_section(
                    row_models, ax.model_file, (ct.section,)
                )
        rows.append(UnionRow(
            row_name=f"{base_name}__{'_'.join(labels)}",
            vars=row_vars, models=tuple(row_models),
            sweep_var_keys=frozenset(sweep),
            sweep_model_indices=column.model_sweep_indices,
            enabled=column.enabled,
            tests=column.tests,
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


def _order_row_vars(
    row: UnionRow, global_order: list[str]
) -> UnionRow:
    """Re-key a row's ``vars`` dict to follow the cornermodel's global
    variable-row order — so a Push carries the user's row ordering through
    to Maestro (SKILL ``axlPutVar`` writes vars in JSON key order)."""
    if not row.vars:
        return row
    ordered: dict[str, tuple[str, ...]] = {
        v: row.vars[v] for v in global_order if v in row.vars
    }
    for v, val in row.vars.items():   # any var outside global_order — keep
        ordered.setdefault(v, val)
    return replace(row, vars=ordered)


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
    # Each row's vars follow the cornermodel's global variable-row order so
    # a Push reproduces the user's row ordering in Maestro.
    all_vars: set[str] = set()
    for row in rows:
        all_vars |= set(row.vars)
    global_order = ordered_var_rows(model, all_vars)
    rows = [_order_row_vars(row, global_order) for row in rows]
    return Union(
        union_schema_version=1,
        name=model.name,
        project=model.project,
        testbench_id=model.testbench_id,
        rows=tuple(rows),
        tests=model.tests,
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
        tests=row.tests,
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


def union_var_order(union: Union) -> tuple[str, ...]:
    """Reconstruct Maestro's global variable-row order from a pulled union.

    ``axlGetVars`` lists each corner's variables in Maestro's display order,
    so merging the per-row sequences — keeping every row's relative order —
    recovers the global order even when no single corner has every variable.
    """
    order: list[str] = []
    for row in union.rows:
        prev = -1
        for var in row.vars:
            if var in order:
                prev = order.index(var)
            else:
                prev += 1
                order.insert(prev, var)
    return tuple(order)


def cornermodel_from_union(union: Union, name: str = "corners") -> CornerModel:
    """Seed a cornermodel from a ``Union`` — every union row becomes an
    unmanaged column. Used to populate the Corners tab from Maestro's
    current corners when the project has no ``.cornermodel.json`` yet. The
    variable-row order is adopted from Maestro (2026 UX item 3)."""
    return CornerModel(
        cornermodel_schema_version=1,
        name=name,
        project=union.project,
        testbench_id=union.testbench_id,
        modes={},
        columns=tuple(make_unmanaged_column(r) for r in union.rows),
        var_order=union_var_order(union),
        tests=union.tests,
    )


def apply_pull(
    model: CornerModel, pulled: Union, result: ReconcileResult,
    profile: "PvtProfile | None" = None,
) -> CornerModel:
    """Merge a classified Maestro pull back into a non-empty cornermodel.

    Pull means "make simkit reflect Maestro": every matched corner is
    re-synced to its pulled state — new variables appear, changed values
    update. An unmanaged column is rebuilt wholesale from its pulled row;
    a managed column keeps its mode and routes pulled register values to
    overrides. Foreign pulled rows (corners added in Maestro) become new
    unmanaged columns. Columns missing from the pull are left in place —
    a pull never deletes a corner. Modes / corner sets / run sets are
    untouched. The variable-row order follows Maestro.

    An aggregated (correlated-axis) column expands to several pulled rows
    and is left as-is — there is no 1:1 row to re-sync it from.
    """
    cm = model
    by_name = {row.row_name: row for row in pulled.rows}
    for ci in range(len(model.columns)):
        column = model.columns[ci]
        name = effective_name(column)
        if name not in result.matched:
            continue            # foreign / missing / aggregated — skip
        pulled_row = by_name.get(name)
        if pulled_row is None:
            continue
        if not column.is_managed:
            cm = _replace_column(cm, ci, replace(
                make_unmanaged_column(pulled_row), name=column.name
            ))
        else:
            mode_vars = model.modes[column.mode].vars
            for d in result.matched[name]:
                if d.maestro_value == ():
                    continue    # var dropped in Maestro — left in place
                if d.var in mode_vars:
                    cm = set_column_override(
                        cm, ci, d.var, d.maestro_value[0]
                    )
                else:
                    cm = set_pvt_var(cm, ci, d.var, d.maestro_value)
    for row in result.foreign:
        cm = add_column(cm, make_unmanaged_column(row))
    order = union_var_order(pulled)
    if order:
        cm = set_var_order(cm, order)
    return cm


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
    model: CornerModel, column_index: int, var: str,
    value: "str | tuple[str, ...]",
) -> CornerModel:
    """Return a new cornermodel with one column's PVT var set.

    ``value`` is a scalar string or a tuple of strings — a multi-value
    cell such as ``("3", "2.8")``. The var is added to the column when absent,
    so this also serves the GUI's "type into a blank corner cell" edit.
    """
    column = model.columns[column_index]
    values = (value,) if isinstance(value, str) else tuple(value)
    new_pvt = dict(column.pvt_vars)
    new_pvt[var] = values
    new_sweep = column.pvt_sweep_keys - {var}
    return _replace_column(model, column_index, replace(
        column, pvt_vars=new_pvt, pvt_sweep_keys=new_sweep
    ))


def set_column_model_section(
    model: CornerModel, column_index: int, file: str, section: str
) -> CornerModel:
    """Return a new cornermodel with one column's process-model section set.

    This is the GUI's "edit a process-corner cell" action — it retargets the
    section of model file ``file`` on this column only, leaving every other
    column untouched.
    """
    column = model.columns[column_index]
    new_models: list[ModelEntry] = []
    found = False
    for m in column.models:
        if m.file == file:
            new_models.append(replace(m, section=(section,)))
            found = True
        else:
            new_models.append(m)
    if not found:
        raise CornerModelValidationError(
            f"set_column_model_section: column has no model file {file!r}"
        )
    new_sweep = frozenset(
        i for i in column.model_sweep_indices
        if column.models[i].file != file
    )
    return _replace_column(model, column_index, replace(
        column, models=tuple(new_models), model_sweep_indices=new_sweep
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


def remove_mode(model: CornerModel, name: str) -> CornerModel:
    """Return a new cornermodel with mode ``name`` removed — together with
    every corner column on it, every variant based on it, and those columns'
    run-set memberships (a corner cannot outlive its mode)."""
    if name not in model.modes:
        raise CornerModelValidationError(
            f"remove_mode: mode {name!r} is not defined"
        )
    dropped = {
        effective_name(c) for c in model.columns if c.mode == name
    }
    new_columns = tuple(c for c in model.columns if c.mode != name)
    new_modes = {k: v for k, v in model.modes.items() if k != name}
    new_variants = {
        k: v for k, v in model.variants.items() if v.base_mode != name
    }
    new_sets = {
        sn: replace(rs, columns=tuple(
            c for c in rs.columns if c not in dropped
        ))
        for sn, rs in model.run_sets.items()
    }
    return replace(
        model, modes=new_modes, columns=new_columns,
        variants=new_variants, run_sets=new_sets,
    )


def columns_of_mode(model: CornerModel, name: str) -> int:
    """How many corner columns belong to mode ``name`` — the GUI shows this
    in the Delete-mode confirm."""
    return sum(1 for c in model.columns if c.mode == name)


def rename_mode(
    model: CornerModel, old_name: str, new_name: str
) -> CornerModel:
    """Return a new cornermodel with mode ``old_name`` renamed to
    ``new_name``. Its corner columns, variants based on it, and run-set
    memberships all follow the rename."""
    if old_name not in model.modes:
        raise CornerModelValidationError(
            f"rename_mode: mode {old_name!r} is not defined"
        )
    new_name = new_name.strip()
    if not _MODE_NAME_RE.match(new_name):
        raise CornerModelValidationError(
            f"rename_mode: name {new_name!r} must match "
            f"^[A-Za-z][A-Za-z0-9_]*$"
        )
    if new_name == old_name:
        return model
    if new_name in model.modes:
        raise CornerModelValidationError(
            f"rename_mode: mode {new_name!r} already exists"
        )
    new_modes = {
        (new_name if k == old_name else k):
        (replace(v, name=new_name) if k == old_name else v)
        for k, v in model.modes.items()
    }
    new_variants = {
        k: (replace(v, base_mode=new_name)
            if v.base_mode == old_name else v)
        for k, v in model.variants.items()
    }
    new_columns = tuple(
        replace(c, mode=new_name) if c.mode == old_name else c
        for c in model.columns
    )
    # Run-set memberships follow any effective-name change (a plain
    # managed column's name is mode-derived; aliased / variant ones aren't).
    eff_map = {
        effective_name(old): effective_name(new)
        for old, new in zip(model.columns, new_columns)
        if effective_name(old) != effective_name(new)
    }
    new_sets = {
        name: replace(rs, columns=tuple(
            eff_map.get(c, c) for c in rs.columns
        ))
        for name, rs in model.run_sets.items()
    }
    return replace(
        model, modes=new_modes, variants=new_variants,
        columns=new_columns, run_sets=new_sets,
    )


def mode_from_column(
    model: CornerModel, column_index: int, mode_name: str,
    register_vars: dict[str, str], pvt_label: str,
) -> CornerModel:
    """Create a new mode by classifying an existing column's variables.

    ``register_vars`` (var -> scalar value) becomes the new mode's register
    set; the source column is converted to a managed column of that mode,
    keeping only its non-register variables as per-column PVT vars. This is
    the GUI's "New Mode from a column" action — the user has already defined
    every variable in Cadence, so a mode is *derived* from a corner rather
    than retyped (spec §7.2, 2026 UX feedback).
    """
    if not _PVT_LABEL_RE.match(pvt_label):
        raise CornerModelValidationError(
            f"mode_from_column: pvt_label {pvt_label!r} must match "
            f"^[A-Za-z0-9_]+$"
        )
    column = model.columns[column_index]
    # add_mode validates the mode name + register var names / values.
    new_model = add_mode(model, mode_name, register_vars)
    kept_pvt = {
        v: tup for v, tup in column.pvt_vars.items()
        if v not in register_vars
    }
    kept_sweep = frozenset(column.pvt_sweep_keys & set(kept_pvt))
    managed = Column(
        mode=mode_name, enabled=column.enabled, pvt_vars=kept_pvt,
        models=column.models, pvt_label=pvt_label,
        pvt_sweep_keys=kept_sweep,
        model_sweep_indices=column.model_sweep_indices,
        correlated_axes=column.correlated_axes,
    )
    new_name = effective_name(managed)
    others = {
        effective_name(c) for i, c in enumerate(new_model.columns)
        if i != column_index
    }
    if new_name in others:
        raise CornerModelValidationError(
            f"mode_from_column: column name {new_name!r} collides with an "
            f"existing column"
        )
    cols = list(new_model.columns)
    cols[column_index] = managed
    return replace(new_model, columns=tuple(cols))


def reclassify_mode(
    model: CornerModel, mode_name: str, register_vars: dict[str, str]
) -> CornerModel:
    """Re-set which variables of ``mode_name`` are registers vs per-column
    PVT vars (2026 UX — the New-Mode classification was previously frozen).

    ``register_vars`` (var -> scalar value) is the complete desired register
    set. A variable moving register→PVT is pushed onto every managed column
    of the mode, seeded with its old register value; a variable moving
    PVT→register is lifted off those columns. Columns of other modes are
    untouched.
    """
    if mode_name not in model.modes:
        raise CornerModelValidationError(
            f"reclassify_mode: no such mode {mode_name!r}"
        )
    if not register_vars:
        raise CornerModelValidationError(
            f"reclassify_mode: mode {mode_name!r} needs at least one register"
        )
    for vname, vval in register_vars.items():
        if not _VAR_NAME_RE.match(vname):
            raise CornerModelValidationError(
                f"reclassify_mode: var name {vname!r} must match "
                f"^[A-Za-z][A-Za-z0-9_]*$"
            )
        if not isinstance(vval, str):
            raise CornerModelValidationError(
                f"reclassify_mode: register {vname!r} value must be a string"
            )
    old = model.modes[mode_name].vars
    to_pvt = set(old) - set(register_vars)        # register -> PVT
    to_register = set(register_vars) - set(old)   # PVT -> register

    new_modes = dict(model.modes)
    new_modes[mode_name] = Mode(name=mode_name, vars=dict(register_vars))

    new_cols: list[Column] = []
    for col in model.columns:
        if col.mode != mode_name:
            new_cols.append(col)
            continue
        pvt = dict(col.pvt_vars)
        overrides = dict(col.overrides)
        sweep = set(col.pvt_sweep_keys)
        for v in to_pvt:
            pvt.setdefault(v, (old[v],))
        for v in to_register:
            pvt.pop(v, None)
            overrides.pop(v, None)
            sweep.discard(v)
        new_cols.append(replace(
            col, pvt_vars=pvt, overrides=overrides,
            pvt_sweep_keys=frozenset(sweep),
        ))
    return replace(model, modes=new_modes, columns=tuple(new_cols))


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


def assign_mode_to_column(
    model: CornerModel, column_index: int, mode_name: str
) -> CornerModel:
    """Fold a raw / foreign (unmanaged) column into a mode. The column keeps
    its vars and models; its name becomes ``<mode>_<old name>``."""
    if not (0 <= column_index < len(model.columns)):
        raise CornerModelValidationError(
            f"assign_mode_to_column: index {column_index} out of range"
        )
    col = model.columns[column_index]
    if col.is_managed:
        raise CornerModelValidationError(
            f"assign_mode_to_column: column {effective_name(col)!r} is "
            f"already in a mode"
        )
    if mode_name not in model.modes:
        raise CornerModelValidationError(
            f"assign_mode_to_column: mode {mode_name!r} is not defined"
        )
    new_col = replace(col, mode=mode_name, name=None, pvt_label=col.name)
    new_name = effective_name(new_col)
    others = {
        effective_name(c) for i, c in enumerate(model.columns)
        if i != column_index
    }
    if new_name in others:
        raise CornerModelValidationError(
            f"assign_mode_to_column: effective name {new_name!r} collides "
            f"with an existing column"
        )
    cols = tuple(
        new_col if i == column_index else c
        for i, c in enumerate(model.columns)
    )
    return replace(model, columns=cols)


# ---------------------------------------------------------------------------
# 2026 UX — column delete / reorder / rename, variable rename / remove
# ---------------------------------------------------------------------------


def delete_column(model: CornerModel, column_index: int) -> CornerModel:
    """Return a new cornermodel with the column at ``column_index`` removed.
    Any run set that referenced the column drops it from its membership."""
    if not (0 <= column_index < len(model.columns)):
        raise CornerModelValidationError(
            f"delete_column: index {column_index} out of range"
        )
    dropped = effective_name(model.columns[column_index])
    cols = tuple(
        c for i, c in enumerate(model.columns) if i != column_index
    )
    new_sets = {
        name: replace(rs, columns=tuple(
            c for c in rs.columns if c != dropped
        ))
        for name, rs in model.run_sets.items()
    }
    return replace(model, columns=cols, run_sets=new_sets)


def reorder_columns(
    model: CornerModel, new_order: tuple[int, ...]
) -> CornerModel:
    """Return a new cornermodel with columns permuted by ``new_order`` — a
    permutation of ``range(len(columns))`` (corner reordering, 2026 UX)."""
    n = len(model.columns)
    if sorted(new_order) != list(range(n)):
        raise CornerModelValidationError(
            f"reorder_columns: {new_order} is not a permutation of "
            f"0..{n - 1}"
        )
    return replace(model, columns=tuple(model.columns[i] for i in new_order))


def move_column(
    model: CornerModel, column_index: int, delta: int
) -> CornerModel:
    """Return a new cornermodel with one column nudged ``delta`` positions
    (-1 = left, +1 = right). An out-of-range move is a no-op."""
    n = len(model.columns)
    target = column_index + delta
    if not (0 <= column_index < n) or not (0 <= target < n):
        return model
    order = list(range(n))
    order[column_index], order[target] = order[target], order[column_index]
    return reorder_columns(model, tuple(order))


def set_column_enabled(
    model: CornerModel, column_index: int, enabled: bool
) -> CornerModel:
    """Return a new cornermodel with one column's enable flag set."""
    column = model.columns[column_index]
    return _replace_column(
        model, column_index, replace(column, enabled=bool(enabled))
    )


def set_column_tests(
    model: CornerModel, column_index: int, tests: tuple[str, ...]
) -> CornerModel:
    """Return a new cornermodel with one column's test scope set. An empty
    tuple means the corner applies to all tests (the Maestro default)."""
    column = model.columns[column_index]
    clean = tuple(t.strip() for t in tests if t and t.strip())
    return _replace_column(
        model, column_index, replace(column, tests=clean)
    )


def set_column_test_enabled(
    model: CornerModel, column_index: int, test: str, enabled: bool
) -> CornerModel:
    """Toggle one test on/off for one corner column — the Tests-grid
    checkbox action. ``Column.tests`` stays normalised: empty means the
    column runs every test (the master list), so when the checkbox grid
    ends up fully ticked the scope collapses back to empty."""
    master = model.tests
    if not master:
        return model
    column = model.columns[column_index]
    current = set(column.tests) if column.tests else set(master)
    if enabled:
        current.add(test)
    else:
        current.discard(test)
    if current >= set(master):
        new_tests: tuple[str, ...] = ()
    else:
        new_tests = tuple(t for t in master if t in current)
    return _replace_column(
        model, column_index, replace(column, tests=new_tests)
    )


def rename_column(
    model: CornerModel, column_index: int, new_name: str
) -> CornerModel:
    """Return a new cornermodel with one column's effective name set to
    ``new_name``. A managed column keeps its mode / label and gets an
    ``alias``; an unmanaged column's stored ``name`` is changed. Run sets
    that referenced the old name follow the rename."""
    if not (0 <= column_index < len(model.columns)):
        raise CornerModelValidationError(
            f"rename_column: index {column_index} out of range"
        )
    new_name = new_name.strip()
    if not new_name:
        raise CornerModelValidationError("rename_column: name is empty")
    column = model.columns[column_index]
    old_name = effective_name(column)
    if new_name == old_name:
        return model
    others = {
        effective_name(c) for i, c in enumerate(model.columns)
        if i != column_index
    }
    if new_name in others:
        raise CornerModelValidationError(
            f"rename_column: name {new_name!r} collides with an existing "
            f"column"
        )
    if column.is_managed:
        new_col = replace(column, alias=new_name)
    else:
        new_col = replace(column, name=new_name)
    renamed = _replace_column(model, column_index, new_col)
    new_sets = {
        name: replace(rs, columns=tuple(
            new_name if c == old_name else c for c in rs.columns
        ))
        for name, rs in renamed.run_sets.items()
    }
    return replace(renamed, run_sets=new_sets)


def rename_variable(
    model: CornerModel, old_name: str, new_name: str
) -> CornerModel:
    """Return a new cornermodel with variable ``old_name`` renamed to
    ``new_name`` everywhere — every column's PVT vars / overrides / sweep
    keys, every mode's registers, and the explicit row order."""
    new_name = new_name.strip()
    if not _VAR_NAME_RE.match(new_name):
        raise CornerModelValidationError(
            f"rename_variable: name {new_name!r} must match "
            f"^[A-Za-z][A-Za-z0-9_]*$"
        )
    if new_name == old_name:
        return model
    for axis in model.correlated_axes.values():
        if old_name in axis.members or new_name in axis.members:
            raise CornerModelValidationError(
                f"rename_variable: {old_name!r}/{new_name!r} touches "
                f"correlated axis {axis.name!r} — rename via the Axes editor"
            )

    def _rename(d: dict) -> dict:
        return {(new_name if k == old_name else k): v for k, v in d.items()}

    new_modes = {
        name: Mode(name=name, vars=_rename(mode.vars))
        for name, mode in model.modes.items()
    }
    new_cols: list[Column] = []
    for col in model.columns:
        if old_name in col.pvt_vars and new_name in col.pvt_vars:
            raise CornerModelValidationError(
                f"rename_variable: column {effective_name(col)!r} already "
                f"has a variable {new_name!r}"
            )
        new_cols.append(replace(
            col,
            pvt_vars=_rename(col.pvt_vars),
            overrides=_rename(col.overrides),
            pvt_sweep_keys=frozenset(
                new_name if k == old_name else k
                for k in col.pvt_sweep_keys
            ),
        ))
    new_var_order = tuple(
        new_name if v == old_name else v for v in model.var_order
    )
    return replace(
        model, modes=new_modes, columns=tuple(new_cols),
        var_order=new_var_order,
    )


def remove_variable(model: CornerModel, var: str) -> CornerModel:
    """Return a new cornermodel with variable ``var`` removed everywhere —
    every column's PVT vars / overrides and every mode's registers."""
    for axis in model.correlated_axes.values():
        if var in axis.members:
            raise CornerModelValidationError(
                f"remove_variable: {var!r} is a member of correlated axis "
                f"{axis.name!r} — remove it via the Axes editor"
            )
    for name, mode in model.modes.items():
        if var in mode.vars and len(mode.vars) == 1:
            raise CornerModelValidationError(
                f"remove_variable: {var!r} is the only register of mode "
                f"{name!r} — a mode needs at least one register"
            )

    def _drop(d: dict) -> dict:
        return {k: v for k, v in d.items() if k != var}

    new_modes = {
        name: Mode(name=name, vars=_drop(mode.vars))
        for name, mode in model.modes.items()
    }
    new_cols = tuple(
        replace(
            col,
            pvt_vars=_drop(col.pvt_vars),
            overrides=_drop(col.overrides),
            pvt_sweep_keys=col.pvt_sweep_keys - {var},
        )
        for col in model.columns
    )
    new_var_order = tuple(v for v in model.var_order if v != var)
    return replace(
        model, modes=new_modes, columns=new_cols, var_order=new_var_order,
    )


# ---------------------------------------------------------------------------
# Stage 2 operations — correlated axes (spec §4)
# ---------------------------------------------------------------------------


def _check_axis_well_formed(axis: CorrelatedAxis, fn: str) -> None:
    """Shared validation for add / update of a dimension."""
    if not _VAR_NAME_RE.match(axis.name):
        raise CornerModelValidationError(
            f"{fn}: dimension name {axis.name!r} must match "
            f"^[A-Za-z][A-Za-z0-9_]*$"
        )
    if not axis.members and axis.model_file is None:
        raise CornerModelValidationError(
            f"{fn}: dimension {axis.name!r} needs at least one member "
            f"variable or a model file"
        )
    if not axis.tuples:
        raise CornerModelValidationError(
            f"{fn}: dimension {axis.name!r} needs at least one level"
        )
    member_set = set(axis.members)
    for ct in axis.tuples:
        if set(ct.values) != member_set:
            raise CornerModelValidationError(
                f"{fn}: level {ct.label!r} must give a value for exactly "
                f"the members {sorted(member_set)}"
            )
        if (ct.section is not None) != (axis.model_file is not None):
            raise CornerModelValidationError(
                f"{fn}: level {ct.label!r} section and dimension "
                f"{axis.name!r} model file must both be set or both absent"
            )


def add_correlated_axis(
    model: CornerModel, axis: CorrelatedAxis
) -> CornerModel:
    """Return a new cornermodel with a correlated axis added."""
    if axis.name in model.correlated_axes:
        raise CornerModelValidationError(
            f"add_correlated_axis: {axis.name!r} already exists"
        )
    _check_axis_well_formed(axis, "add_correlated_axis")
    new_axes = dict(model.correlated_axes)
    new_axes[axis.name] = axis
    return replace(model, correlated_axes=new_axes)


def update_correlated_axis(
    model: CornerModel, axis: CorrelatedAxis
) -> CornerModel:
    """Return a new cornermodel with correlated axis ``axis.name`` replaced."""
    if axis.name not in model.correlated_axes:
        raise CornerModelValidationError(
            f"update_correlated_axis: {axis.name!r} does not exist"
        )
    _check_axis_well_formed(axis, "update_correlated_axis")
    new_axes = dict(model.correlated_axes)
    new_axes[axis.name] = axis
    return replace(model, correlated_axes=new_axes)


def remove_correlated_axis(model: CornerModel, name: str) -> CornerModel:
    """Return a new cornermodel with correlated axis ``name`` removed.

    Raises if a column still crosses the axis — delete those columns first."""
    if name not in model.correlated_axes:
        raise CornerModelValidationError(
            f"remove_correlated_axis: {name!r} does not exist"
        )
    users = [
        effective_name(c) for c in model.columns
        if name in c.correlated_axes
    ]
    if users:
        raise CornerModelValidationError(
            f"remove_correlated_axis: {name!r} is still used by "
            f"{', '.join(users)}"
        )
    new_axes = dict(model.correlated_axes)
    del new_axes[name]
    return replace(model, correlated_axes=new_axes)


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


def remove_run_set(model: CornerModel, name: str) -> CornerModel:
    """Return a new cornermodel with run-set ``name`` removed."""
    if name not in model.run_sets:
        raise CornerModelValidationError(
            f"remove_run_set: no such run-set {name!r}"
        )
    return replace(model, run_sets={
        k: v for k, v in model.run_sets.items() if k != name
    })


def apply_run_set(
    model: CornerModel, set_name: str, additive: bool = False
) -> CornerModel:
    """Switch to a run-set (spec §2.2).

    Exclusive (default): ``column.enabled = effective_name ∈ set`` for every
    column — non-members are disabled. Additive: members are enabled, every
    other column keeps its current enabled state."""
    if set_name not in model.run_sets:
        raise CornerModelValidationError(
            f"apply_run_set: no such run-set {set_name!r}"
        )
    members = set(model.run_sets[set_name].columns)
    new_columns = tuple(
        replace(c, enabled=True) if effective_name(c) in members
        else (c if additive else replace(c, enabled=False))
        for c in model.columns
    )
    return replace(model, columns=new_columns)


def set_columns_enabled(
    model: CornerModel, indices: tuple[int, ...], enabled: bool
) -> CornerModel:
    """Batch-set ``enabled`` on the columns at ``indices`` (ad-hoc, no named
    run-set) — the GUI's right-click Enable / Disable selected corners."""
    idx = set(indices)
    for i in idx:
        if not (0 <= i < len(model.columns)):
            raise CornerModelValidationError(
                f"set_columns_enabled: index {i} out of range"
            )
    new_columns = tuple(
        replace(c, enabled=enabled) if i in idx else c
        for i, c in enumerate(model.columns)
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
# Stage 5 — var order, soft validation (spec §2/§4)
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
        if col.selected_levels:
            entry["selected_levels"] = {
                a: list(labels) for a, labels in col.selected_levels.items()
            }
        if col.inline_axes:
            entry["inline_axes"] = [
                {"name": ax.name, **_axis_to_dict(ax)}
                for ax in col.inline_axes
            ]
        if col.variant is not None:
            entry["variant"] = col.variant
        if col.axis_levels:
            entry["axis_levels"] = dict(col.axis_levels)
        if col.tests:
            entry["tests"] = list(col.tests)
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
            ax.name: _axis_to_dict(ax)
            for ax in model.correlated_axes.values()
        }
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
    if model.tests:
        out["tests"] = list(model.tests)
    return out


def _axis_to_dict(ax: CorrelatedAxis) -> dict:
    tuples: list[dict] = []
    for t in ax.tuples:
        td: dict = {"label": t.label, "values": dict(t.values)}
        if t.section is not None:
            td["section"] = t.section
        tuples.append(td)
    out: dict = {"members": list(ax.members), "tuples": tuples}
    if ax.model_file is not None:
        out["model_file"] = ax.model_file
    return out


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
