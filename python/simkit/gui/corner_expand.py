"""Flat-row corner expansion + supply-coherence checks (G-9).

The Corners editor presents one ``.union.json`` row as a flat dict
(``row_name`` / ``process`` / ``temperature`` / ``vdd`` / ``model_file``
/ ``extra_vars``). A single such row can secretly stand for many
sub-corners — ``process = "tt,ss,ff"`` is a 3-process sweep — and the
flat grid gives no hint of that (friction #3 / #5 / #9).

This module is the pure (no-Qt) data layer behind the editor's
"expands to" column and its coherence-warning strip:

* :func:`expand_flat_row` reuses
  :func:`simkit.gui.loaders.editor_row_to_union_row` + the real
  :func:`simkit.union.explode`, so the preview cannot drift from what
  ``Send to Maestro`` actually pushes.
* :func:`coherence_warnings` flags the ``vdd``-column / ``extra_vars``
  supply split (friction #2) — supply hidden in free text, or defined
  in both places at once.
"""

from __future__ import annotations

from typing import List

from simkit.gui.loaders import editor_row_to_union_row, parse_extra_vars
from simkit.union import SubCorner, Union, UnionError, explode

# Var names that mean "supply" — kept in sync with loaders._MAESTRO_TO_COLUMN
# (which maps "vdd"/"supply" onto the vdd column) plus the common rail
# spellings an RF designer actually types into extra_vars.
_SUPPLY_ALIASES = frozenset({"vdd", "supply", "vsup", "vcc", "vdda", "vddio"})


def expand_flat_row(flat_row: dict) -> List[SubCorner]:
    """Materialise every sub-corner one flat editor row stands for.

    Returns ``[]`` for a row too incomplete to be a corner (no vars and
    no model_file, or missing row_name) — the editor renders that as
    "—" rather than a misleading "1".
    """
    try:
        union_row = editor_row_to_union_row(flat_row, where="corner")
    except UnionError:
        return []
    union = Union(
        union_schema_version=1,
        name="preview", project="preview", testbench_id="preview",
        rows=(union_row,),
    )
    return explode(union)


def expansion_count(flat_row: dict) -> int:
    """Number of sub-corners ``flat_row`` expands to (0 if incomplete)."""
    return len(expand_flat_row(flat_row))


def expansion_tooltip(flat_row: dict) -> str:
    """A multi-line description of the sub-corners, for a cell tooltip."""
    subs = expand_flat_row(flat_row)
    if not subs:
        return "This row is not a complete corner yet (missing vars or model_file)."
    if len(subs) == 1:
        return f"Expands to 1 corner: {subs[0].sub_corner_name}"
    lines = [f"Expands to {len(subs)} sub-corners:"]
    for sc in subs:
        lines.append(f"  {sc.sub_corner_name}  —  {_describe(sc)}")
    return "\n".join(lines)


def _describe(sc: SubCorner) -> str:
    parts = [f"{k}={v}" for k, v in sc.vars.items()]
    for i, m in enumerate(sc.models):
        label = "section" if len(sc.models) == 1 else f"model[{i}].section"
        parts.append(f"{label}={m.section}")
    return ", ".join(parts) if parts else "(no vars)"


def coherence_warnings(flat_row: dict) -> List[str]:
    """Flag the supply-definition split for one flat row.

    Non-blocking advisories (the editor shows them in an amber strip,
    distinct from the red push-blocking validation errors):

    * supply token present in ``extra_vars`` while the ``vdd`` column is
      empty — the supply is hidden in free text;
    * supply defined in both the ``vdd`` column *and* ``extra_vars`` —
      two sources of truth, and which one Maestro reads is non-obvious.
    """
    warnings: List[str] = []
    name = (flat_row.get("row_name") or "").strip() or "(unnamed row)"
    vdd_col = (flat_row.get("vdd") or "").strip()
    extra_keys = [k for k, _ in parse_extra_vars(flat_row.get("extra_vars") or "")]
    supply_in_extras = [k for k in extra_keys if k.lower() in _SUPPLY_ALIASES]

    if supply_in_extras and not vdd_col:
        warnings.append(
            f"{name}: supply is written in extra_vars "
            f"({', '.join(supply_in_extras)}) while the vdd column is empty "
            f"— move it to the vdd column, or the supply change is hard to see"
        )
    elif supply_in_extras and vdd_col:
        warnings.append(
            f"{name}: supply is defined in both the vdd column and "
            f"extra_vars ({', '.join(supply_in_extras)}) — keep just one "
            f"to avoid ambiguity"
        )
    return warnings
