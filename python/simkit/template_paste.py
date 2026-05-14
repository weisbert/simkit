"""Paste-importer: concrete Cadence expression → `Template` object.

Implements the Phase 3B §3.4 / DECISIONS #39 P3B.5b / DECISIONS #39 P3B.F2
contract. Pure-Python, stdlib-only.

Two parameterisations:

1. **Signal paths.** Quoted-path literals matching ``"(\\/[^"]*)"`` are
   auto-extracted and replaced with ``$SIG``. v1 enforces *exactly one*
   distinct signal path; multi-signal source expressions raise ``ValueError``.

2. **Numeric literals.** Each numeric literal outside any string-literal
   gets a prompt (``prompt(message) -> bool``). On ``True``, the literal is
   replaced by a generated placeholder; on ``False`` (or if no prompt
   callback is supplied), the literal is kept verbatim.

Auto-naming for parameterised numerics:
- 1 literal → ``$NUM_1``
- 2 literals → ``$V_LOW``, ``$V_HIGH`` (in source order — matches the common
  "lower/upper threshold pair" idiom; lets the Gate M1 example round-trip
  cleanly against `config/template_example.template.json`).
- N>2 literals → ``$NUM_1``, ``$NUM_2``, …, ``$NUM_N``.

``short_alias`` defaults to the capitalised first token of the outermost
function-call name (``average(...)`` → ``Average``). Override via the
``short_alias`` keyword.

``_pasted_from`` is set to the unmodified original expression.
"""

from __future__ import annotations

import re
from typing import Callable, Optional

from simkit.template import (
    Template,
    TemplateParam,
    _TEMPLATE_NAME_RE,
)


_SIGNAL_PATH_RE = re.compile(r'"(/[^"]*)"')
_NUMERIC_RE = re.compile(r"\b\d+(?:\.\d+)?(?:[eE][-+]?\d+)?\b")
_LEADING_FUNC_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_IDENT_HEAD_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)")


def paste_to_template(
    raw_expression: str,
    name: str,
    *,
    short_alias: Optional[str] = None,
    prompt: Optional[Callable[[str], bool]] = None,
) -> Template:
    """Convert a concrete Cadence calculator expression into a Template.

    Args:
        raw_expression: the concrete expression (as pasted from Maestro's
            Outputs/Calculator panel).
        name: template name (must match ``^[a-z][a-z0-9_]*$``; corresponds to
            the filename basename when saved).
        short_alias: optional override for the auto-derived alias. Must match
            ``^[A-Za-z][A-Za-z0-9_]*$``.
        prompt: optional callback ``(message: str) -> bool`` invoked for each
            numeric literal found outside quoted strings. Return ``True`` to
            parameterise. If ``None``, all numerics are retained verbatim.

    Returns:
        A fully-validated ``Template`` (constructed in-memory; ``source_path``
        is set to a synthetic ``<paste>`` sentinel since no file exists yet).

    Raises:
        ValueError: if ``name``/``short_alias`` is invalid, if the source
            contains zero or more than one distinct signal path, or if the
            resulting placeholder set collides with itself.
    """
    if not isinstance(raw_expression, str) or raw_expression == "":
        raise ValueError("raw_expression must be a non-empty string")
    if not _TEMPLATE_NAME_RE.match(name):
        raise ValueError(
            f"name {name!r} does not match ^[a-z][a-z0-9_]*$"
        )

    # 1) Signal-path extraction.
    distinct_paths: list[str] = []
    for m in _SIGNAL_PATH_RE.finditer(raw_expression):
        p = m.group(1)
        if p not in distinct_paths:
            distinct_paths.append(p)

    if len(distinct_paths) == 0:
        raise ValueError(
            "no quoted signal-path literal (matching \"/...\") found in "
            "expression; v1 paste-import requires exactly one"
        )
    if len(distinct_paths) > 1:
        raise ValueError(
            f"multi-signal templates not supported in v1; found "
            f"{len(distinct_paths)} distinct paths: {distinct_paths}"
        )

    only_path = distinct_paths[0]
    # Preserve the surrounding "..." so the template body keeps the caller's
    # quoting; render-side substitution is bare-string ($SIG → /path) so the
    # rendered output matches the source byte-for-byte.
    sig_replaced = raw_expression.replace(f'"{only_path}"', '"$SIG"')

    # 2) Numeric extraction (skip anything inside double-quoted strings, since
    #    those are bare-string args like "time" / "VDD" — but the signal path
    #    has already been removed above).
    numeric_positions = _find_numerics_outside_strings(sig_replaced)

    chosen: list[tuple[int, int, str]] = []  # (start, end, original_text)
    if prompt is not None:
        for start, end in numeric_positions:
            literal = sig_replaced[start:end]
            answered = prompt(
                f"parameterise literal {literal!r}? (y/N)"
            )
            if answered:
                chosen.append((start, end, literal))

    # Assign names based on count.
    num_names = _assign_numeric_names(len(chosen))

    # Walk back-to-front so positions stay valid as we splice.
    rewritten = sig_replaced
    num_params: list[tuple[str, str]] = []  # (key, original_default)
    for (start, end, literal), nm in zip(reversed(chosen), reversed(num_names)):
        rewritten = rewritten[:start] + f"${nm}" + rewritten[end:]
        num_params.append((nm, literal))

    # Build params list. SIG first, then numerics in source (left-to-right) order.
    params: list[TemplateParam] = [
        TemplateParam(
            key="SIG", kind="signal",
            doc="Signal path (auto-extracted from paste source)",
        )
    ]
    # Reverse `num_params` since we built it back-to-front.
    for nm, literal in reversed(num_params):
        params.append(
            TemplateParam(
                key=nm, kind="number", default=literal,
                doc="Parameterised numeric literal (auto-extracted from paste)",
            )
        )

    # 3) short_alias resolution.
    alias = _resolve_short_alias(raw_expression, short_alias)

    # 4) Build & validate the Template object via a minimal in-memory path.
    #    We deliberately skip going back through `load_template` (which wants a
    #    file on disk). Instead we reuse the same structural+placeholder
    #    validators.
    from simkit.template import (
        _validate_expression_structure,
        _validate_placeholders,
        _validate_single_signal_param,
    )
    from pathlib import Path

    synthetic_path = Path("<paste:" + name + ">")
    _validate_expression_structure(synthetic_path, rewritten)
    _validate_placeholders(synthetic_path, rewritten, tuple(params))
    _validate_single_signal_param(synthetic_path, tuple(params))

    template = Template(
        template_schema_version=1,
        name=name,
        short_alias=alias,
        expression=rewritten,
        params=tuple(params),
        eval_type="point",
        plot=True,
        save=False,
        unit=None,
        pasted_from=raw_expression,
        source_path=synthetic_path,
    )
    return template


def _find_numerics_outside_strings(s: str) -> list[tuple[int, int]]:
    """Return ``(start, end)`` spans of numeric literals NOT inside double-quoted
    strings. Single-quotes ('tran 'nil) are sigils, not delimiters — they do
    not bracket strings.
    """
    out: list[tuple[int, int]] = []
    in_double = False
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if in_double:
            if ch == '"':
                in_double = False
            i += 1
            continue
        if ch == '"':
            in_double = True
            i += 1
            continue
        # Try to match a numeric starting here.
        m = _NUMERIC_RE.match(s, i)
        if m is not None and m.start() == i:
            # Ensure we're at a word boundary on the left.
            if i == 0 or not (s[i - 1].isalnum() or s[i - 1] == "_"):
                out.append((m.start(), m.end()))
                i = m.end()
                continue
        i += 1
    return out


def _assign_numeric_names(count: int) -> list[str]:
    if count == 0:
        return []
    if count == 1:
        return ["NUM_1"]
    if count == 2:
        return ["V_LOW", "V_HIGH"]
    return [f"NUM_{k + 1}" for k in range(count)]


def _resolve_short_alias(
    raw_expression: str, override: Optional[str]
) -> str:
    if override is not None:
        if not _IDENT_HEAD_RE.match(override) or not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_]*", override
        ):
            raise ValueError(
                f"short_alias {override!r} does not match "
                f"^[A-Za-z][A-Za-z0-9_]*$"
            )
        return override

    m = _LEADING_FUNC_RE.match(raw_expression)
    if m is None:
        # Fallback: capitalise the first identifier-like token.
        m2 = _IDENT_HEAD_RE.match(raw_expression.lstrip())
        if m2 is None:
            raise ValueError(
                "cannot auto-derive short_alias: no leading identifier in "
                "expression — supply short_alias=..."
            )
        token = m2.group(1)
    else:
        token = m.group(1)

    capitalised = token[:1].upper() + token[1:]
    # Validate against the alias regex (almost always passes since token is
    # already an identifier).
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", capitalised):
        raise ValueError(
            f"auto-derived short_alias {capitalised!r} is invalid — "
            f"supply short_alias=..."
        )
    return capitalised
