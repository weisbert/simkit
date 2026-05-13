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

from simkit.union import ModelEntry, Union, UnionRow

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


def parse_csv(
    text: str,
    *,
    testbench_id: str,
    union_name: str = "from_csv",
    project: str = "from_csv",
) -> Union:
    """Reverse of ``build_csv`` — parse Maestro corners-CSV text into a
    :class:`Union`. The CSV does not carry the ``testbench_id`` /
    ``project`` / ``name`` fields (the GUI export omits them), so the
    caller must supply them; ``pvt corners restore`` resolves
    ``testbench_id`` from the live session and ``project`` from
    ``.pvtproject``.

    Lossy fields:

    * ``block`` / ``test`` per model — CSV emits the testbench-cell
      shortcut; we restore the SKILL-side defaults (``Global`` / ``All``)
      so the pushed corners match what a fresh ``pvtCornersPull`` would
      produce. Maestro accepts both forms.
    * Per-(corner, test) enable matrix — not currently surfaced in the
      Union schema. v1 ignores the trailing test row.
    * Multi-test setups — v1 only honours the first test row.
    """
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) < 3:
        raise CsvBuildError(
            f"CSV too short to be a Maestro corners export ({len(lines)} non-empty lines)"
        )

    # 1) Header: Corner,<row_name>,<row_name>,...
    head = lines[0].split(",")
    if len(head) < 2 or head[0] != "Corner":
        raise CsvBuildError(
            f"first row must start with 'Corner,'; got {lines[0]!r}"
        )
    row_names = head[1:]
    n_cols = len(row_names)

    # 2) Enable row
    if not lines[1].startswith("Enable,"):
        raise CsvBuildError(
            f"second row must start with 'Enable,'; got {lines[1]!r}"
        )
    enable_cells = lines[1].split(",")[1:]
    if len(enable_cells) != n_cols:
        raise CsvBuildError(
            f"Enable row has {len(enable_cells)} cells, expected {n_cols}"
        )
    enabled_per_row = [cell == "t" for cell in enable_cells]

    # 3..M) Var rows. They run from line 2 up to the first 'Modelfile::' line.
    var_lines: list[tuple[str, list[str]]] = []
    model_lines: list[tuple[str, list[str]]] = []
    test_lines: list[tuple[str, list[str]]] = []

    for line in lines[2:]:
        parts = line.split(",")
        header_cell = parts[0]
        body = parts[1:]
        if len(body) != n_cols:
            raise CsvBuildError(
                f"row {header_cell!r} has {len(body)} cells, expected {n_cols}"
            )
        if header_cell.startswith("Modelfile::"):
            model_lines.append((header_cell[len("Modelfile::"):], body))
        elif (header_cell.startswith("t ") or header_cell.startswith("f ")) \
                and "::" in header_cell:
            test_lines.append((header_cell, body))
        else:
            var_lines.append((header_cell, body))

    # Build per-row data structures.
    per_row_vars: list[dict[str, tuple[str, ...]]] = [
        {} for _ in range(n_cols)
    ]
    sweep_keys_per_row: list[set[str]] = [set() for _ in range(n_cols)]

    for display_name, body in var_lines:
        # Reverse the Maestro display-case rule (Temperature → temperature).
        canon_name = _reverse_display_case(display_name)
        for k, cell in enumerate(body):
            cell = cell.strip()
            if cell == "":
                continue  # var not set for this corner
            vals = tuple(cell.split(" "))
            per_row_vars[k][canon_name] = vals
            if len(vals) > 1:
                sweep_keys_per_row[k].add(canon_name)

    per_row_models: list[list[ModelEntry]] = [[] for _ in range(n_cols)]
    per_row_sweep_model_idx: list[set[int]] = [set() for _ in range(n_cols)]

    for abs_path, body in model_lines:
        basename = abs_path.rsplit("/", 1)[-1] if "/" in abs_path else abs_path
        for k, cell in enumerate(body):
            cell = cell.strip()
            if cell == "":
                continue
            tokens = cell.split(" ")
            # First token is the per-corner per-model 'enabled' marker
            # (t/f). v1 emitter always emits 't' so we accept either but
            # don't surface it.
            if tokens and tokens[0] in ("t", "f"):
                section_tokens = tuple(tokens[1:])
            else:
                section_tokens = tuple(tokens)
            if not section_tokens:
                continue
            model_idx = len(per_row_models[k])
            per_row_models[k].append(ModelEntry(
                file=basename,
                block=_DEFAULT_MODEL_BLOCK,
                test=_DEFAULT_MODEL_TEST,
                section=section_tokens,
                file_abs=abs_path,
            ))
            if len(section_tokens) > 1:
                per_row_sweep_model_idx[k].add(model_idx)

    rows: list[UnionRow] = []
    for k, row_name in enumerate(row_names):
        if not per_row_vars[k] and not per_row_models[k]:
            # Skip empty corners — Union schema rejects them. Maestro can
            # emit corners with neither vars nor models for default-bench
            # nominal rows; we treat those as out-of-scope for the v1 round-trip.
            continue
        rows.append(UnionRow(
            row_name=row_name,
            vars=per_row_vars[k],
            models=tuple(per_row_models[k]),
            sweep_var_keys=frozenset(sweep_keys_per_row[k]),
            sweep_model_indices=frozenset(per_row_sweep_model_idx[k]),
            enabled=enabled_per_row[k],
        ))

    if not rows:
        raise CsvBuildError("CSV produced zero non-empty corner rows")

    return Union(
        union_schema_version=1,
        name=union_name,
        project=project,
        testbench_id=testbench_id,
        rows=tuple(rows),
    )


_REVERSE_DISPLAY_CASE = {v: k for k, v in _DISPLAY_CASE.items()}
_DEFAULT_MODEL_BLOCK = "Global"
_DEFAULT_MODEL_TEST = "All"


def _reverse_display_case(display_name: str) -> str:
    return _REVERSE_DISPLAY_CASE.get(display_name, display_name)


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
