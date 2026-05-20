# Handoff — 2026-05-20 (session 6)

For the next conversation window. This session: **worked gap-matrix
bundle B — shipped G-6 / G-9 / G-14 / G-5, all committed**. Read this
first.

## Branch & state

`overnight-2026-05-19` (cut from `main`; nothing pushed, nothing
merged). Last commit: **1f9c287**. Working tree is **clean** —
everything below is committed.

`.cadence/` and `logs_yusheng/logs0/` are untracked but **not ours** —
leave them out of any commit.

Tests: full suite **1769 passed, 93 subtests, 0 failed** (was 1660 at
the start of the session; +109 across the new work). Run with
`QT_QPA_PLATFORM=offscreen PYTHONPATH=python .venv/bin/python3 -m pytest`.

## What this session did

Worked gap-matrix "bundle B" (G-5 / G-6 / G-9 / G-14). The user chose
to do the whole bundle; G-2 (plotting) stays deferred. Each item is a
self-contained commit, full suite green after each.

1. **G-6 — cross-milestone trend** — commit `6f12f6c`. `simkit.trend`
   data layer aligns N slices on (test, corner, point, output) with a
   per-row monotonic-direction verdict; `resolve_trend_column` adds a
   milestone fallback (a tag matching several runs → newest, not
   ambiguous). `pvt trend` CLI (variadic, `--changed-only`, `--json`).
   GUI: `TrendTab` + `TrendTableModel` (dynamic column count),
   `MultiRunPickerDialog`, `DiffController.open_trend`, and a "里程碑趋势"
   entry on the History/Milestones tree-group context menu.
2. **G-9 — corner-model coherence** — commit `0dcaa1f`. New
   `gui/corner_expand.py` reuses the real `simkit.union.explode` (via a
   refactored `loaders.editor_row_to_union_row`) so the preview can't
   drift from push. Corners editor gains a read-only "expands to ×N"
   column (tinted + per-row sub-corner tooltip) and an amber
   supply-coherence strip (vdd column vs extra_vars). `editor_row_to_
   union_row` now derives sweep flags from value cardinality.
3. **G-14 — measures-editor affordances** — commit `13708d4`. Added an
   "Edit entry…" button (was double-click-only); replaced cryptic
   `[raw]`/`[template]` list labels with spelled-out kinds; raw-entry
   dialog gets an example placeholder + plain-language hint.
4. **G-5 — run-condition provenance** — commit `1f9c287`. New
   `simkit.provenance` (build/inject/load/compare). The `pvt run`
   orchestrator injects a top-level `provenance` block into run.json
   before ingest (host / captured_at / pdk_version / model-file
   fingerprints). DuckDB **schema v4 → v5** (`runs.provenance`). GUI:
   Summary tab shows a run's conditions (amber when unrecorded); Trend
   tab flags columns that ran under mismatched host/PDK/models.

## New files this session

- `python/simkit/trend.py`, `python/simkit/cli/trend.py`
- `python/simkit/gui/trend_model.py`, `python/simkit/gui/views/trend_tab.py`
- `python/simkit/gui/corner_expand.py`
- `python/simkit/provenance.py`
- Test files: `test_trend.py`, `test_cli_trend.py`, `test_provenance.py`,
  `gui/test_trend_tab.py`, `gui/test_corner_expand.py`, plus additions to
  `gui/test_run_picker.py` / `gui/test_diff_controller.py` /
  `gui/test_corners_editor.py` / `gui/test_measures_editor.py` /
  `gui/test_summary_tab.py` / `test_db.py`.

## Verification gap — G-5 needs a live `pvt run`

`simkit.provenance` (inject/ingest/compare) and the GUI surfaces are
all unit-verified. But the **orchestrator injection step**
(`_inject_provenance` inside `_execute_batch_item` / `_run_strategy_
chain`) only executes during a real `pvt run` against live Maestro.
Next dogfood must confirm:

1. A fresh `pvt run` produces a run whose Summary tab shows host +
   model files (not "未记录").
2. `PVT_PDK_VERSION` env var (or a `pdk_version` field in `.pvtproject`)
   is the only way to populate the PDK field — it shows "未知" otherwise.

## What's next — gap matrix, remaining items

`docs/simkit_gap_matrix.md` — bundle B (G-5/G-6/G-9/G-14) is now done;
its §5 "suggested order" is stale for those rows. Remaining gaps:

- **G-2 (plotting / curves)** — the matrix's only structural GUI item.
  **User deferred it to a future conversation** — do not start without
  checking in. A design-research pass first is recommended.
- **G-13 (UI language consistency)** — small; mixed CN/EN. Note this
  session deliberately did *not* chase language consistency (out of
  scope per item) — new strings are bilingual-light as-is.
- **G-10 (multi-session)** / **G-11 (Monte Carlo)** — structural
  mini-phases; Parts D/E of the scenario catalog still unaddressed.

## Verification notes

- GUI tests: `QT_QPA_PLATFORM=offscreen`; venv at `.venv/` has PyQt5
  (the system python3 does not — always use `.venv/bin/python3`).
- `1AXX`'s DB (`workarea/simkit_1AXX/.db/simkit.duckdb`) has 3 runs,
  none with provenance — a good live case for the Summary tab's
  "未记录" amber path and for `pvt trend`.
- Acceptance gate unchanged: Phase 4 is done only when the user
  completes one real signoff cycle entirely inside the GUI on red zone.
