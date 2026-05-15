"""Render a measurement bundle into a flat list of concrete output rows.

Implements the Phase 3B §3.4 render contract
(docs/phase3b_measure_template_spec.md). Pure-Python, stdlib-only.

Each ``MeasureApply`` entry becomes:
- 1 row per signal in the bound signal_group, when the template has a
  ``signal``-kind param;
- exactly 1 row, when the template has no signal-kind param.

Substitution is textual replacement of ``$NAME`` tokens. Priority:
``param_overrides`` > ``params[].default`` > error. The ``$SIG`` substitution
is the raw signal path (no surrounding quotes — the template owns those, e.g.
``vtime('tran "$SIG")`` for a quoted form, ``vtime('tran $SIG)`` for a bare
form). See DECISIONS #41 + spec §3.4.

Output name format:
- with signal:    ``<short_alias><alias_suffix>_<signal_basename>``
- without signal: ``<short_alias><alias_suffix>``

Collisions across the whole bundle render = ``RenderError`` (M4 case h).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from simkit.errors import SimkitError
from simkit.measure_bundle import MeasureApply, MeasureBundle
from simkit.signal_group import signal_basename
from simkit.template import Template


_PARAM_TOKEN_RE = re.compile(r"\$([A-Z][A-Z0-9_]*)")


class RenderError(SimkitError):
    """A bundle could not be rendered (missing param value, name collision)."""


@dataclass(frozen=True)
class RenderedRow:
    output_name: str
    expression: str
    eval_type: str
    plot: bool
    save: bool


def render_bundle(bundle: MeasureBundle) -> list[RenderedRow]:
    rows: list[RenderedRow] = []
    seen_names: dict[str, str] = {}
    for i, entry in enumerate(bundle.apply):
        kind_tag = (
            f"template={entry.template.name!r}"
            if entry.template is not None
            else "raw_expression"
        )
        for row in _render_entry(i, entry):
            if row.output_name in seen_names:
                raise RenderError(
                    f"bundle render: output_name {row.output_name!r} "
                    f"appears twice — first from {seen_names[row.output_name]}, "
                    f"second from apply[{i}] "
                    f"({kind_tag}). "
                    f"Use 'alias_suffix' or 'output_name' to disambiguate."
                )
            seen_names[row.output_name] = (
                f"apply[{_find_index(bundle, entry)}] ({kind_tag})"
            )
            rows.append(row)
    return rows


def _find_index(bundle: MeasureBundle, target: MeasureApply) -> int:
    for j, e in enumerate(bundle.apply):
        if e is target:
            return j
    return -1  # pragma: no cover


def _render_entry(idx: int, entry: MeasureApply) -> list[RenderedRow]:
    # v1.2 (f) — raw_expression entries bypass the template machinery.
    if entry.template is None:
        assert entry.raw_expression is not None
        assert entry.output_name is not None
        return [RenderedRow(
            output_name=entry.output_name,
            expression=entry.raw_expression,
            eval_type=entry.raw_eval_type,
            plot=entry.raw_plot,
            save=entry.raw_save,
        )]

    # v1.2 (e) — param_sweep expands into N rendered rows in parallel with
    # the entry's explicit output_names list.
    if entry.param_sweep is not None:
        return _render_swept_entry(idx, entry)

    template = entry.template
    signal_param = template.signal_param()
    out: list[RenderedRow] = []
    if signal_param is None:
        expression = _substitute(
            template, entry.param_overrides, signal_value=None, idx=idx
        )
        output_name = _resolve_output_name(entry, basename=None)
        out.append(
            RenderedRow(
                output_name=output_name,
                expression=expression,
                eval_type=template.eval_type,
                plot=template.plot,
                save=template.save,
            )
        )
        return out

    if entry.signal_group is None:
        # Defensive — measure_bundle.py already enforces this.
        raise RenderError(  # pragma: no cover
            f"apply[{idx}] template {template.name!r} has signal param "
            f"{signal_param.key!r} but signal_group is None"
        )

    for sig in entry.signal_group.signals:
        expression = _substitute(
            template, entry.param_overrides, signal_value=sig, idx=idx
        )
        basename = signal_basename(sig)
        output_name = _resolve_output_name(entry, basename=basename)
        out.append(
            RenderedRow(
                output_name=output_name,
                expression=expression,
                eval_type=template.eval_type,
                plot=template.plot,
                save=template.save,
            )
        )
    return out


_SIG_PLACEHOLDER = "${SIG}"


def _resolve_output_name(entry: MeasureApply, *, basename: Optional[str]) -> str:
    """v1.2 (a) — honor apply-entry output_name override.

    If entry.output_name is set, it fully replaces the default
    short_alias + alias_suffix [+ _basename] scheme. The literal
    ``${SIG}`` is substituted with the signal basename (when applicable).
    """
    template = entry.template
    if entry.output_name is not None:
        if basename is not None:
            return entry.output_name.replace(_SIG_PLACEHOLDER, basename)
        return entry.output_name
    if basename is None:
        return f"{template.short_alias}{entry.alias_suffix}"
    return f"{template.short_alias}{entry.alias_suffix}_{basename}"


def _substitute(
    template: Template,
    overrides: dict[str, str],
    *,
    signal_value: Optional[str],
    idx: int,
) -> str:
    params_by_key = template.params_by_key()
    signal_param = template.signal_param()
    signal_key = signal_param.key if signal_param else None

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in params_by_key:
            # Should never trigger — load_template rejects undeclared tokens.
            raise RenderError(  # pragma: no cover
                f"apply[{idx}] template {template.name!r}: expression "
                f"references undeclared placeholder ${key}"
            )
        if key == signal_key:
            if signal_value is None:
                raise RenderError(  # pragma: no cover
                    f"apply[{idx}] template {template.name!r}: missing "
                    f"signal value for ${key}"
                )
            return signal_value
        if key in overrides:
            return overrides[key]
        default = params_by_key[key].default
        if default is None:
            raise RenderError(
                f"apply[{idx}] template {template.name!r}: no value for "
                f"${key} (no override, no default)"
            )
        return default

    return _PARAM_TOKEN_RE.sub(repl, template.expression)


def _render_swept_entry(idx: int, entry: MeasureApply) -> list[RenderedRow]:
    """v1.2 (e) — expand a param_sweep entry into N rendered rows.

    Each iteration overrides one sweep key with its i-th value and emits
    a row named by ``output_names[i]``. Mirrors the no-sweep branches for
    {signal-bound, signal-less} templates but folds the per-i override
    into the substitution map.
    """
    assert entry.template is not None
    assert entry.param_sweep is not None
    assert entry.output_names is not None
    template = entry.template
    (sweep_key, sweep_values), = entry.param_sweep.items()
    signal_param = template.signal_param()
    rows: list[RenderedRow] = []

    for i, value in enumerate(sweep_values):
        merged = dict(entry.param_overrides)
        merged[sweep_key] = value
        name_tpl = entry.output_names[i]

        if signal_param is None:
            expr = _substitute(
                template, merged, signal_value=None, idx=idx
            )
            rows.append(RenderedRow(
                output_name=name_tpl,
                expression=expr,
                eval_type=template.eval_type,
                plot=template.plot,
                save=template.save,
            ))
            continue

        if entry.signal_group is None:  # pragma: no cover (loader rejects)
            raise RenderError(
                f"apply[{idx}] template {template.name!r} has signal "
                f"param {signal_param.key!r} but signal_group is None"
            )
        for sig in entry.signal_group.signals:
            expr = _substitute(
                template, merged, signal_value=sig, idx=idx
            )
            basename = signal_basename(sig)
            out_name = name_tpl.replace(_SIG_PLACEHOLDER, basename)
            rows.append(RenderedRow(
                output_name=out_name,
                expression=expr,
                eval_type=template.eval_type,
                plot=template.plot,
                save=template.save,
            ))
    return rows
