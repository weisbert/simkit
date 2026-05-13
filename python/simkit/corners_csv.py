"""Maestro-native corners CSV emitter (Phase 2 §5 build path).

Reverse-engineered from a live ``Tools → Corners → Export`` against
``fnxSession0`` (sample committed at
``tests/fixtures/unions/fnxSession0_baseline.csv``). The emitted CSV
matches the byte format Maestro reads back via ``Tools → Corners →
Import``, so it serves as a skillbridge-independent backup file: after a
Cadence crash, the user re-imports via the GUI without needing the
Python wrapper or skillbridge to be functional.

# Format

Per the ground-truth fixture, Maestro CSV has the following row layout
(comma-separated, no quoting in the observed samples):

    Corner,<row_name>,<row_name>,...
    Enable,<f|t>,<f|t>,...
    <Var1>,<val|sweep|empty>,<val|sweep|empty>,...
    ...
    Modelfile::<abs_path>,<test_en> <sect1> <sect2> ...,...
    <test_en> <block>::<test_name>,<t|f>,<t|f>,...

Sweep values are space-separated within their cell
(e.g. ``3 2.8`` for a two-value VDD sweep).

# v1 limitations

* **Single-test happy path**: emits exactly one test row, deriving its
  name from ``union.testbench_id`` cell component. Multi-test setups
  need a follow-up extension to capture the per-test enable matrix.
* **Single model basename per row**: Maestro CSV layout supports
  multiple ``Modelfile::`` rows (one per distinct file path) but each
  row currently maps to a single model entry per corner. The emitter
  groups by absolute path; a corner with multiple distinct model files
  is supported, but the same file across corners is collapsed into one
  row.
* **No quoting / escaping**: if a row_name / var_name / section / file
  path contains commas or quotes, the emitter raises rather than
  emitting an ambiguous CSV. Real-world data has not exercised this.
* **`_file_abs` is required**: sidecars that pre-date the 2026-05-13
  pull extension only have ``file`` (basename). The emitter falls back
  to the basename and warns via a return-channel, but the resulting CSV
  will not be importable into Maestro because the GUI Import dialog
  needs an absolute path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from simkit.union import Union, UnionRow

# Maestro's GUI title-cases certain reserved design variables on display
# even though the SKILL API exposes them lowercase. Apply the known map
# so emitted CSV byte-matches what Tools → Corners → Export produces.
_DISPLAY_CASE = {
    "temperature": "Temperature",
}

_DISALLOWED_IN_CELL = (",", '"', "\n", "\r")


@dataclass(frozen=True)
class CsvBuildWarning:
    code: str
    message: str


@dataclass(frozen=True)
class CsvBuildResult:
    text: str
    warnings: tuple[CsvBuildWarning, ...]


class CsvBuildError(ValueError):
    """The union cannot be emitted as Maestro-importable CSV."""


def build_csv(union: Union) -> CsvBuildResult:
    """Render ``union`` as Maestro corners-CSV text.

    The return value carries both the emitted text and a tuple of
    non-fatal warnings. ``CsvBuildError`` is raised on conditions
    that would produce an ambiguous CSV (special chars in row names,
    var names, etc.) — callers must fix the union before re-trying.
    """
    rows = union.rows
    if len(rows) == 0:
        raise CsvBuildError("union has no rows — nothing to emit")

    _check_cell_safety(union)

    warnings: List[CsvBuildWarning] = []
    lines: List[str] = []

    lines.append("Corner," + ",".join(r.row_name for r in rows))
    lines.append("Enable," + ",".join("t" if r.enabled else "f" for r in rows))

    # Vars: insertion order across the row sequence; first appearance wins.
    var_order: List[str] = []
    seen: set[str] = set()
    for r in rows:
        for vname in r.vars:
            if vname not in seen:
                var_order.append(vname)
                seen.add(vname)

    for vname in var_order:
        display = _DISPLAY_CASE.get(vname, vname)
        cells = []
        for r in rows:
            v = r.vars.get(vname)
            cells.append("" if v is None else " ".join(v))
        lines.append(f"{display}," + ",".join(cells))

    # Models: one Modelfile:: row per distinct absolute path, in
    # first-encountered order. Each cell is "<test_en> <section ...>".
    abs_paths: List[str] = []
    seen_paths: set[str] = set()
    fallback_used = False
    for r in rows:
        for m in r.models:
            ap = m.file_abs
            if ap is None:
                ap = m.file
                fallback_used = True
            if ap not in seen_paths:
                abs_paths.append(ap)
                seen_paths.add(ap)

    if fallback_used:
        warnings.append(CsvBuildWarning(
            code="missing_file_abs",
            message=(
                "one or more model entries are missing '_file_abs' — "
                "fell back to basename. The emitted CSV is NOT "
                "Maestro-importable in this form. Re-pull with the "
                "2026-05-13+ skill/pvtCorners.il to capture absolute paths."
            ),
        ))

    for ap in abs_paths:
        cells = []
        for r in rows:
            sec_str = ""
            for m in r.models:
                ap_for_m = m.file_abs if m.file_abs is not None else m.file
                if ap_for_m == ap:
                    sec_str = "t " + " ".join(m.section)
                    break
            cells.append(sec_str)
        lines.append(f"Modelfile::{ap}," + ",".join(cells))

    # Test-enable row: single test for v1. Derive block / test name from
    # testbench cell, since SKILL block="Global" / test="All" defaults
    # don't reflect what Maestro CSV writes.
    tb_parts = union.testbench_id.split("/") if union.testbench_id else []
    cell = tb_parts[1] if len(tb_parts) > 1 else "Test"
    block = cell
    test_name = cell
    # In the observed sample every corner's per-test bit is `t`, even for
    # the corner whose Enable bit is `f`. Default to "all tests enabled".
    test_cells = ",".join(["t"] * len(rows))
    lines.append(f"t {block}::{test_name}," + test_cells)

    return CsvBuildResult(
        text="\n".join(lines) + "\n",
        warnings=tuple(warnings),
    )


def _check_cell_safety(union: Union) -> None:
    """Raise ``CsvBuildError`` for any value that would require CSV
    quoting/escaping. v1 emitter does not implement quoting."""

    def _check(label: str, value: str) -> None:
        for bad in _DISALLOWED_IN_CELL:
            if bad in value:
                raise CsvBuildError(
                    f"{label}={value!r} contains {bad!r} — v1 CSV "
                    f"emitter does not implement quoting"
                )

    for r in union.rows:
        _check(f"row[{r.row_name}].row_name", r.row_name)
        for vname, vals in r.vars.items():
            _check(f"row[{r.row_name}].vars key", vname)
            for v in vals:
                _check(f"row[{r.row_name}].vars[{vname}]", v)
        for k, m in enumerate(r.models):
            _check(f"row[{r.row_name}].models[{k}].file", m.file)
            if m.file_abs is not None:
                _check(f"row[{r.row_name}].models[{k}]._file_abs", m.file_abs)
            for s in m.section:
                _check(f"row[{r.row_name}].models[{k}].section", s)
