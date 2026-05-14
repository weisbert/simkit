"""`.template.json` sidecar loader for formula templates.

Implements the Phase 3B §3.2 contract (docs/phase3b_measure_template_spec.md).
Pure-Python, stdlib-only. Templates are parameterised calculator expressions
with ``$NAME`` placeholders. Templates have no signal binding and no test
binding — those are supplied at apply time via a measurement bundle.

Style mirrors simkit.union: dataclass-per-object, ``_assert_*`` helpers,
error classes inheriting from a single ``TemplateError`` base, schema_version
strict equality check.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from simkit.errors import SimkitError


TEMPLATE_FILE_SUFFIX = ".template.json"

_TEMPLATE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SHORT_ALIAS_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_PARAM_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_PARAM_TOKEN_RE = re.compile(r"\$([A-Z][A-Z0-9_]*)")

_SUPPORTED_TEMPLATE_SCHEMA_VERSIONS = frozenset({1})
_ALLOWED_PARAM_KINDS = frozenset({"signal", "number", "string"})
_ALLOWED_EVAL_TYPES = frozenset({"point", "corners", "sweeps", "maa"})


class TemplateError(SimkitError):
    """Base class for `.template.json` loader errors."""


class TemplateSchemaVersionError(TemplateError):
    """A sidecar declared a ``template_schema_version`` the loader does not support."""


class TemplateMalformedError(TemplateError):
    """A sidecar is unreadable / not parseable as JSON / not a JSON object."""


class TemplateLoadError(TemplateError):
    """A sidecar parsed cleanly but failed schema validation per spec §3.2."""


@dataclass(frozen=True)
class TemplateParam:
    key: str
    kind: str  # "signal" | "number" | "string"
    default: str | None = None
    doc: str | None = None


@dataclass(frozen=True)
class Template:
    template_schema_version: int
    name: str
    short_alias: str
    expression: str
    params: tuple[TemplateParam, ...]
    eval_type: str
    plot: bool
    save: bool
    unit: str | None
    pasted_from: str | None
    source_path: Path

    def signal_param(self) -> TemplateParam | None:
        """Return the (single) signal-kind param, or ``None`` if absent."""
        for p in self.params:
            if p.kind == "signal":
                return p
        return None

    def params_by_key(self) -> dict[str, TemplateParam]:
        return {p.key: p for p in self.params}


def load_template(path: Path | str) -> Template:
    p = Path(path).expanduser().resolve()

    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise TemplateMalformedError(f"{p}: invalid JSON — {exc}") from exc
    except OSError as exc:
        raise TemplateMalformedError(f"{p}: cannot read — {exc}") from exc

    if not isinstance(data, dict):
        raise TemplateMalformedError(
            f"{p}: top-level must be a JSON object, got {type(data).__name__}"
        )

    schema_version = _validate_schema_version(p, data)
    name = _validate_name(p, data)
    expression = _assert_str(p, data, "expression")
    short_alias = _validate_short_alias(p, data, name)
    params = _validate_params(p, data, expression)
    eval_type = _validate_eval_type(p, data)
    plot = _validate_optional_bool(p, data, "plot", default=True)
    save = _validate_optional_bool(p, data, "save", default=False)
    unit = _validate_optional_str(p, data, "unit")
    pasted_from = _validate_optional_str(p, data, "_pasted_from")

    _validate_expression_structure(p, expression)
    _validate_placeholders(p, expression, params)
    _validate_single_signal_param(p, params)

    return Template(
        template_schema_version=schema_version,
        name=name,
        short_alias=short_alias,
        expression=expression,
        params=params,
        eval_type=eval_type,
        plot=plot,
        save=save,
        unit=unit,
        pasted_from=pasted_from,
        source_path=p,
    )


# ---------------------------------------------------------------------------
# Field validators
# ---------------------------------------------------------------------------


def _validate_schema_version(path: Path, data: dict) -> int:
    if "template_schema_version" not in data:
        raise TemplateSchemaVersionError(
            f"{path}: missing required field 'template_schema_version'"
        )
    raw = data["template_schema_version"]
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise TemplateSchemaVersionError(
            f"{path}: 'template_schema_version' must be an integer"
        )
    if raw not in _SUPPORTED_TEMPLATE_SCHEMA_VERSIONS:
        raise TemplateSchemaVersionError(
            f"{path}: template_schema_version {raw} not supported "
            f"(supported: {sorted(_SUPPORTED_TEMPLATE_SCHEMA_VERSIONS)})"
        )
    return raw


def _validate_name(path: Path, data: dict) -> str:
    name = _assert_str(path, data, "name")
    if not _TEMPLATE_NAME_RE.match(name):
        raise TemplateLoadError(
            f"{path}: 'name' {name!r} does not match ^[a-z][a-z0-9_]*$"
        )
    basename = path.name
    if not basename.endswith(TEMPLATE_FILE_SUFFIX):
        raise TemplateLoadError(
            f"{path}: filename must end with '{TEMPLATE_FILE_SUFFIX}' "
            f"(got {basename!r})"
        )
    expected = basename[: -len(TEMPLATE_FILE_SUFFIX)]
    if expected != name:
        raise TemplateLoadError(
            f"{path}: 'name' {name!r} must equal filename basename "
            f"{expected!r}"
        )
    return name


def _validate_short_alias(path: Path, data: dict, name: str) -> str:
    if "short_alias" not in data:
        return name
    value = data["short_alias"]
    if not isinstance(value, str) or value == "":
        raise TemplateLoadError(
            f"{path}: 'short_alias' must be a non-empty string if set"
        )
    if not _SHORT_ALIAS_RE.match(value):
        raise TemplateLoadError(
            f"{path}: 'short_alias' {value!r} does not match ^[A-Za-z][A-Za-z0-9_]*$"
        )
    return value


def _validate_params(
    path: Path, data: dict, expression: str
) -> tuple[TemplateParam, ...]:
    if "params" not in data:
        raise TemplateLoadError(f"{path}: missing required field 'params'")
    raw = data["params"]
    if not isinstance(raw, list):
        raise TemplateLoadError(f"{path}: 'params' must be a JSON array")

    seen_keys: set[str] = set()
    out: list[TemplateParam] = []
    for i, raw_param in enumerate(raw):
        where = f"{path}: params[{i}]"
        if not isinstance(raw_param, dict):
            raise TemplateLoadError(f"{where}: must be a JSON object")

        if "key" not in raw_param:
            raise TemplateLoadError(f"{where}: missing 'key'")
        key = raw_param["key"]
        if not isinstance(key, str) or not _PARAM_KEY_RE.match(key):
            raise TemplateLoadError(
                f"{where}: 'key' {key!r} does not match ^[A-Z][A-Z0-9_]*$"
            )
        if key in seen_keys:
            raise TemplateLoadError(
                f"{where}: duplicate key {key!r}"
            )
        seen_keys.add(key)

        if "kind" not in raw_param:
            raise TemplateLoadError(f"{where}: missing 'kind'")
        kind = raw_param["kind"]
        if not isinstance(kind, str) or kind not in _ALLOWED_PARAM_KINDS:
            raise TemplateLoadError(
                f"{where}: 'kind' {kind!r} must be one of "
                f"{sorted(_ALLOWED_PARAM_KINDS)}"
            )

        default = raw_param.get("default")
        if default is not None and not isinstance(default, str):
            raise TemplateLoadError(
                f"{where}: 'default' must be a string if set "
                f"(got {type(default).__name__})"
            )

        doc = raw_param.get("doc")
        if doc is not None and not isinstance(doc, str):
            raise TemplateLoadError(
                f"{where}: 'doc' must be a string if set "
                f"(got {type(doc).__name__})"
            )

        out.append(
            TemplateParam(key=key, kind=kind, default=default, doc=doc)
        )

    return tuple(out)


def _validate_eval_type(path: Path, data: dict) -> str:
    if "eval_type" not in data:
        return "point"
    raw = data["eval_type"]
    if not isinstance(raw, str) or raw not in _ALLOWED_EVAL_TYPES:
        raise TemplateLoadError(
            f"{path}: 'eval_type' {raw!r} must be one of "
            f"{sorted(_ALLOWED_EVAL_TYPES)}"
        )
    return raw


def _validate_optional_bool(
    path: Path, data: dict, key: str, *, default: bool
) -> bool:
    if key not in data:
        return default
    value = data[key]
    if not isinstance(value, bool):
        raise TemplateLoadError(
            f"{path}: {key!r} must be a JSON boolean "
            f"(got {type(value).__name__})"
        )
    return value


def _validate_optional_str(path: Path, data: dict, key: str) -> str | None:
    if key not in data:
        return None
    value = data[key]
    if value is None:
        return None
    if not isinstance(value, str):
        raise TemplateLoadError(
            f"{path}: {key!r} must be a string if set "
            f"(got {type(value).__name__})"
        )
    return value


def _assert_str(path: Path, data: dict, key: str) -> str:
    if key not in data:
        raise TemplateLoadError(f"{path}: missing required field {key!r}")
    value = data[key]
    if not isinstance(value, str):
        raise TemplateLoadError(
            f"{path}: {key!r} must be a string (got {type(value).__name__})"
        )
    return value


# ---------------------------------------------------------------------------
# Structural validators (M4 cases a, b, c, d)
# ---------------------------------------------------------------------------


def _validate_expression_structure(path: Path, expression: str) -> None:
    """M4 a/d: balanced parens, quotes (`"` and `'`), and braces.

    Walk the expression once. Track open paren/brace counts, and double-quote
    + single-quote string-literal state. Inside a double-quoted string, single
    quotes are literal characters; inside a single-quoted string, double quotes
    are literal. Backslash-escaping is not honoured in v1 (none of the live
    Cadence expressions surveyed use it inside strings).
    """
    paren_depth = 0
    brace_depth = 0
    in_double = False
    in_single = False

    for idx, ch in enumerate(expression):
        if in_double:
            if ch == '"':
                in_double = False
            continue
        if in_single:
            if ch == "'":
                in_single = False
            continue
        if ch == '"':
            in_double = True
            continue
        if ch == "'":
            # Cadence quote: `'tran`, `'nil` — these are atoms, not strings.
            # We still track "in_single" for symmetry but Cadence single-quotes
            # do not bracket multi-char strings. For v1 we treat `'` as a
            # quote-token only when followed by whitespace/close-paren and
            # otherwise as the start of a one-token symbol. Simplification:
            # treat single-quotes as ALWAYS unpaired (they are sigils, not
            # delimiters). To keep "quote balance" symmetric we only fail on
            # mismatched double-quotes; single-quote sigils never pair.
            #
            # Spec M4(d) says "quote imbalance" — we enforce that for `"`.
            continue
        if ch == "(":
            paren_depth += 1
        elif ch == ")":
            paren_depth -= 1
            if paren_depth < 0:
                raise TemplateLoadError(
                    f"{path}: 'expression' has unbalanced ')' at position {idx}"
                )
        elif ch == "{":
            brace_depth += 1
        elif ch == "}":
            brace_depth -= 1
            if brace_depth < 0:
                raise TemplateLoadError(
                    f"{path}: 'expression' has unbalanced '}}' at position {idx}"
                )

    if in_double:
        raise TemplateLoadError(
            f"{path}: 'expression' has unbalanced double-quote (\")"
        )
    if paren_depth != 0:
        raise TemplateLoadError(
            f"{path}: 'expression' has unbalanced parentheses "
            f"(net depth {paren_depth:+d})"
        )
    if brace_depth != 0:
        raise TemplateLoadError(
            f"{path}: 'expression' has unbalanced braces "
            f"(net depth {brace_depth:+d})"
        )


def _validate_placeholders(
    path: Path, expression: str, params: tuple[TemplateParam, ...]
) -> None:
    """M4 b/c: every ``$NAME`` in expression is declared in params,
    and every params entry's key is referenced at least once in expression.
    """
    found_names = set(_PARAM_TOKEN_RE.findall(expression))
    declared = {p.key for p in params}

    undeclared = sorted(found_names - declared)
    if undeclared:
        raise TemplateLoadError(
            f"{path}: 'expression' references undeclared placeholder(s): "
            f"{', '.join('$' + n for n in undeclared)}"
        )

    unused = sorted(declared - found_names)
    if unused:
        raise TemplateLoadError(
            f"{path}: 'params' declares key(s) never referenced in expression: "
            f"{', '.join(unused)}"
        )


def _validate_single_signal_param(
    path: Path, params: tuple[TemplateParam, ...]
) -> None:
    signal_params = [p for p in params if p.kind == "signal"]
    if len(signal_params) > 1:
        raise TemplateLoadError(
            f"{path}: v1 allows at most one signal-kind param "
            f"(got {len(signal_params)}: {[p.key for p in signal_params]})"
        )


# ---------------------------------------------------------------------------
# Iteration helpers (used by template_render + measure_bundle validators)
# ---------------------------------------------------------------------------


def iter_placeholders(expression: str) -> Iterable[str]:
    """Yield the distinct ``$NAME`` keys found in ``expression`` in first-seen
    order. Used by render + paste-importer."""
    seen: set[str] = set()
    for m in _PARAM_TOKEN_RE.finditer(expression):
        name = m.group(1)
        if name in seen:
            continue
        seen.add(name)
        yield name
