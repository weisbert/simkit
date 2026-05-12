"""`.union.json` sidecar loader and PVT-union explode logic.

Implements the Phase 2 §1 spec (docs/phase2_pvt_union_spec.md). Pure-Python,
stdlib-only. Loads the declarative sidecar form; ``explode`` materialises
sub-corners per spec §3.4.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from simkit.errors import SimkitError


UNION_FILE_SUFFIX = ".union.json"

_UNION_NAME_RE = re.compile(r"^[a-z0-9_-]+$")
_ROW_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_VAR_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_SUPPORTED_UNION_SCHEMA_VERSIONS = frozenset({1})

_DEFAULT_MODEL_BLOCK = "Global"
_DEFAULT_MODEL_TEST = "All"


class UnionError(SimkitError):
    """Base class for `.union.json` loader errors."""


class UnionSchemaVersionError(UnionError):
    """A sidecar declared a ``union_schema_version`` the loader does not support."""


class UnionMalformedError(UnionError):
    """A sidecar is unreadable / not parseable as JSON / not a JSON object."""


class UnionValidationError(UnionError):
    """A sidecar parsed cleanly but failed schema validation per spec §3."""


@dataclass(frozen=True)
class ModelEntry:
    file: str
    block: str
    test: str
    section: tuple[str, ...]


@dataclass(frozen=True)
class UnionRow:
    row_name: str
    vars: dict[str, tuple[str, ...]]
    models: tuple[ModelEntry, ...]
    sweep_var_keys: frozenset[str] = field(default_factory=frozenset)
    sweep_model_indices: frozenset[int] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Union:
    union_schema_version: int
    name: str
    project: str
    testbench_id: str
    rows: tuple[UnionRow, ...]


@dataclass(frozen=True)
class FrozenModelEntry:
    file: str
    block: str
    test: str
    section: str


@dataclass(frozen=True)
class SubCorner:
    row_name: str
    sub_corner_name: str
    vars: dict[str, str]
    models: tuple[FrozenModelEntry, ...]


def load_union(path: Path | str) -> Union:
    p = Path(path).expanduser().resolve()

    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise UnionMalformedError(f"{p}: invalid JSON — {exc}") from exc
    except OSError as exc:
        raise UnionMalformedError(f"{p}: cannot read — {exc}") from exc

    if not isinstance(data, dict):
        raise UnionMalformedError(
            f"{p}: top-level must be a JSON object, got {type(data).__name__}"
        )

    schema_version = _validate_schema_version(p, data)
    name = _validate_name(p, data)
    project = _validate_required_str(p, data, "project")
    testbench_id = _validate_required_str(p, data, "testbench_id")
    rows = _validate_rows(p, data)

    return Union(
        union_schema_version=schema_version,
        name=name,
        project=project,
        testbench_id=testbench_id,
        rows=rows,
    )


def _validate_schema_version(path: Path, data: dict) -> int:
    if "union_schema_version" not in data:
        raise UnionSchemaVersionError(
            f"{path}: missing required field 'union_schema_version'"
        )
    raw = data["union_schema_version"]
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise UnionSchemaVersionError(
            f"{path}: 'union_schema_version' must be an integer"
        )
    if raw not in _SUPPORTED_UNION_SCHEMA_VERSIONS:
        raise UnionSchemaVersionError(
            f"{path}: union_schema_version {raw} not supported "
            f"(supported: {sorted(_SUPPORTED_UNION_SCHEMA_VERSIONS)})"
        )
    return raw


def _validate_name(path: Path, data: dict) -> str:
    name = _validate_required_str(path, data, "name")
    if not _UNION_NAME_RE.match(name):
        raise UnionValidationError(
            f"{path}: 'name' {name!r} does not match ^[a-z0-9_-]+$"
        )
    basename = path.name
    if not basename.endswith(UNION_FILE_SUFFIX):
        raise UnionValidationError(
            f"{path}: filename must end with '{UNION_FILE_SUFFIX}' "
            f"(got {basename!r})"
        )
    expected = basename[: -len(UNION_FILE_SUFFIX)]
    if expected != name:
        raise UnionValidationError(
            f"{path}: 'name' {name!r} must equal filename basename "
            f"{expected!r}"
        )
    return name


def _validate_required_str(path: Path, data: dict, key: str) -> str:
    if key not in data:
        raise UnionValidationError(f"{path}: missing required field {key!r}")
    value = data[key]
    if not isinstance(value, str) or value == "":
        raise UnionValidationError(
            f"{path}: {key!r} must be a non-empty string"
        )
    return value


def _validate_rows(path: Path, data: dict) -> tuple[UnionRow, ...]:
    if "rows" not in data:
        raise UnionValidationError(f"{path}: missing required field 'rows'")
    raw = data["rows"]
    if not isinstance(raw, list):
        raise UnionValidationError(f"{path}: 'rows' must be a JSON array")
    if len(raw) == 0:
        raise UnionValidationError(f"{path}: 'rows' must be non-empty")

    seen_names: set[str] = set()
    out: list[UnionRow] = []
    for i, raw_row in enumerate(raw):
        row = _validate_row(path, i, raw_row)
        if row.row_name in seen_names:
            raise UnionValidationError(
                f"{path}: rows[{i}] duplicates row_name {row.row_name!r}"
            )
        seen_names.add(row.row_name)
        out.append(row)
    return tuple(out)


def _validate_row(path: Path, idx: int, raw: object) -> UnionRow:
    where = f"{path}: rows[{idx}]"
    if not isinstance(raw, dict):
        raise UnionValidationError(f"{where}: must be a JSON object")

    if "row_name" not in raw:
        raise UnionValidationError(f"{where}: missing 'row_name'")
    row_name = raw["row_name"]
    if not isinstance(row_name, str) or not _ROW_NAME_RE.match(row_name):
        raise UnionValidationError(
            f"{where}: 'row_name' {row_name!r} does not match ^[A-Za-z][A-Za-z0-9_]*$"
        )

    raw_vars = raw.get("vars", {})
    if not isinstance(raw_vars, dict):
        raise UnionValidationError(f"{where}: 'vars' must be a JSON object")

    raw_models = raw.get("models", [])
    if not isinstance(raw_models, list):
        raise UnionValidationError(f"{where}: 'models' must be a JSON array")

    if len(raw_vars) == 0 and len(raw_models) == 0:
        raise UnionValidationError(
            f"{where}: at least one of 'vars' or 'models' must be non-empty"
        )

    vars_out: dict[str, tuple[str, ...]] = {}
    sweep_var_keys: set[str] = set()
    for vname, vval in raw_vars.items():
        if not isinstance(vname, str) or not _VAR_NAME_RE.match(vname):
            raise UnionValidationError(
                f"{where}: var name {vname!r} does not match "
                f"^[A-Za-z][A-Za-z0-9_]*$"
            )
        tup, is_sweep = _coerce_string_or_array(
            vval, f"{where}: vars[{vname!r}]"
        )
        vars_out[vname] = tup
        if is_sweep:
            sweep_var_keys.add(vname)

    models_out: list[ModelEntry] = []
    sweep_model_indices: set[int] = set()
    for j, raw_model in enumerate(raw_models):
        entry, is_sweep = _validate_model_entry(
            f"{where}: models[{j}]", raw_model
        )
        models_out.append(entry)
        if is_sweep:
            sweep_model_indices.add(j)

    return UnionRow(
        row_name=row_name,
        vars=vars_out,
        models=tuple(models_out),
        sweep_var_keys=frozenset(sweep_var_keys),
        sweep_model_indices=frozenset(sweep_model_indices),
    )


def _validate_model_entry(
    where: str, raw: object
) -> tuple[ModelEntry, bool]:
    if not isinstance(raw, dict):
        raise UnionValidationError(f"{where}: must be a JSON object")

    if "file" not in raw:
        raise UnionValidationError(f"{where}: missing 'file'")
    file_ = raw["file"]
    if not isinstance(file_, str) or file_ == "":
        raise UnionValidationError(f"{where}: 'file' must be a non-empty string")

    block = raw.get("block", _DEFAULT_MODEL_BLOCK)
    if not isinstance(block, str) or block == "":
        raise UnionValidationError(f"{where}: 'block' must be a non-empty string")

    test = raw.get("test", _DEFAULT_MODEL_TEST)
    if not isinstance(test, str) or test == "":
        raise UnionValidationError(f"{where}: 'test' must be a non-empty string")

    if "section" not in raw:
        raise UnionValidationError(f"{where}: missing 'section'")
    section_tup, is_sweep = _coerce_string_or_array(
        raw["section"], f"{where}: 'section'"
    )

    return (
        ModelEntry(file=file_, block=block, test=test, section=section_tup),
        is_sweep,
    )


def _coerce_string_or_array(
    raw: object, where: str
) -> tuple[tuple[str, ...], bool]:
    if isinstance(raw, str):
        return ((raw,), False)
    if isinstance(raw, list):
        if len(raw) == 0:
            raise UnionValidationError(f"{where}: empty array is not allowed")
        for v in raw:
            if not isinstance(v, str):
                raise UnionValidationError(
                    f"{where}: every element must be a string "
                    f"(got {type(v).__name__})"
                )
        return (tuple(raw), True)
    raise UnionValidationError(
        f"{where}: must be a string or array of strings "
        f"(got {type(raw).__name__})"
    )


def explode(union: Union) -> list[SubCorner]:
    out: list[SubCorner] = []
    for row in union.rows:
        out.extend(_explode_row(row))
    return out


def _explode_row(row: UnionRow) -> list[SubCorner]:
    field_keys: list[str] = []
    field_values: list[tuple[str, ...]] = []
    field_setters: list = []

    for vname in row.vars:
        field_keys.append(vname)
        sorted_vals = tuple(sorted(row.vars[vname]))
        field_values.append(sorted_vals)
        field_setters.append(("var", vname))

    for k, model in enumerate(row.models):
        field_keys.append(f"model[{k}].section")
        sorted_vals = tuple(sorted(model.section))
        field_values.append(sorted_vals)
        field_setters.append(("model_section", k))

    order = sorted(range(len(field_keys)), key=lambda i: field_keys[i])

    has_sweep = bool(row.sweep_var_keys) or bool(row.sweep_model_indices)

    sorted_values = [field_values[i] for i in order]
    sorted_setters = [field_setters[i] for i in order]

    out: list[SubCorner] = []
    indices_iter = _cross_product_indices([len(v) for v in sorted_values])
    for sub_idx, idx_tuple in enumerate(indices_iter):
        chosen = [sorted_values[i][idx_tuple[i]] for i in range(len(order))]
        chosen_vars: dict[str, str] = {}
        sub_model_sections: dict[int, str] = {}
        for pos, setter in enumerate(sorted_setters):
            kind, key = setter
            if kind == "var":
                chosen_vars[key] = chosen[pos]
            else:
                sub_model_sections[key] = chosen[pos]
        sub_vars: dict[str, str] = {}
        for vname, tup in row.vars.items():
            sub_vars[vname] = chosen_vars.get(vname, tup[0])
        sub_models: list[FrozenModelEntry] = []
        for k, model in enumerate(row.models):
            sec = sub_model_sections.get(k, model.section[0])
            sub_models.append(
                FrozenModelEntry(
                    file=model.file,
                    block=model.block,
                    test=model.test,
                    section=sec,
                )
            )
        if has_sweep:
            sub_name = f"{row.row_name}_{sub_idx}"
        else:
            sub_name = row.row_name
        out.append(
            SubCorner(
                row_name=row.row_name,
                sub_corner_name=sub_name,
                vars=sub_vars,
                models=tuple(sub_models),
            )
        )
    return out


def _cross_product_indices(lengths: Sequence[int]) -> list[tuple[int, ...]]:
    if not lengths:
        return [tuple()]
    total = 1
    for n in lengths:
        total *= n
    out: list[tuple[int, ...]] = []
    for i in range(total):
        coords = []
        x = i
        for n in lengths:
            coords.append(x % n)
            x //= n
        out.append(tuple(coords))
    return out


def _format_sub_corner(sc: SubCorner) -> str:
    parts: list[str] = []
    for vname in sc.vars:
        parts.append(f"{vname}={sc.vars[vname]}")
    for k, m in enumerate(sc.models):
        label = "model.section" if len(sc.models) == 1 else f"model[{k}].section"
        parts.append(f"{label}={m.section}")
    return f"{sc.sub_corner_name:<16}{', '.join(parts)}"


def _cli_explode(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m simkit.union explode")
    parser.add_argument("path", help="path to a .union.json sidecar")
    args = parser.parse_args(argv)
    try:
        union = load_union(args.path)
    except UnionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for sc in explode(union):
        print(_format_sub_corner(sc))
    return 0


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(
            "usage: python -m simkit.union explode <path-to-.union.json>",
            file=sys.stderr,
        )
        return 0 if argv else 2
    sub = argv[0]
    rest = argv[1:]
    if sub == "explode":
        return _cli_explode(rest)
    print(f"error: unknown subcommand {sub!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
