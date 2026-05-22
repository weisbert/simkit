# Handoff — 2026-05-22 (Axes feature + retire Corner Sets)

For the next conversation. Read this first, then read
`docs/corner_manager_user_story.md` (痛点 a + h).

## Branch & state

`main`. Last commit **`46e8b38`** (`gui: corner manager — editing
fixes, Pull merge, drag-reorder`). **4 files are uncommitted** — two
logical changes that should be committed first, before the next task:

- **New Mode dialog fix** (`corner_manager.py` only): `_NewModeDialog`
  now lists every design variable (not just the source column's), so a
  sparse corner like TT can seed a full register set; multi-value vars
  are shown (default PVT-ticked) instead of skipped; blank = left out;
  `_new_mode_from_column` rejects a multi-valued register. + 1 test.
- **Axes feature** (`corner_model.py` + `corner_manager.py` + 2 test
  files): the new user-friendly correlated-axis feature — see below.

Offline tests green: corner + GUI suites **706 passed**
(`QT_QPA_PLATFORM=offscreen PYTHONPATH=python .venv/bin/python3 -m
pytest tests/gui tests/test_corner_model*.py tests/test_corners*.py
-p no:cacheprovider -q`; use `timeout`, a real modal hangs offscreen Qt
— mock `QMessageBox` in smoke tests).

## What the Axes feature is (already built, uncommitted)

A user-friendly replacement for the confusing free-text "Corner Set"
authoring. Toolbar button **"Axes…"** → `_AxesDialog`:

- **`_AxisGridDialog`** — author one correlated axis as a grid: member
  variables are columns (double-click header to rename), levels are
  rows. No syntax.
- **`_AxesDialog`** — axis list + New/Edit/Delete, plus an aggregated-
  corner builder: tick axes to cross, live point count
  (`5 × 3 × 3 = 45 corner points`), pick a mode, Create.
- Data layer (`corner_model.py`): `add_correlated_axis` (pre-existing),
  new `update_correlated_axis`, `remove_correlated_axis`, shared
  `_check_axis_well_formed`.
- Live-verified: 3 axes → aggregated column `VCO_PVT_45`, 45 points
  (not 405). Solves 痛点 h.

## NEXT TASK — retire "Corner Sets", make Axes multi-mode

User feedback (2026-05-22): the old "Corner Sets" feature and the new
"Axes" feature overlap — from a user's view they are the same thing
("可复用的 corner 设置"). Having both is the confusion. Decision
(user-approved): **unify into one — Axes.**

**1. Remove the old "Corner Sets" / PVT-template feature entirely.**
   - GUI (`corner_manager.py`): `btn_templates` button +
     `_build_templates_dialog`, `_on_new_template`, `_on_apply_template`,
     `_on_unbind_template`, `_refresh_templates_panel`,
     `_selected_template_name`, `_on_export_library`,
     `_on_import_library`, `_templates_dialog`, and the parsers
     `_parse_template_columns` / `_split_label_line` / `_parse_kv_comma`.
   - Data layer (`corner_model.py`): `PvtTemplate`, `TemplateColumn`,
     `TemplateBinding`, `add_pvt_template`, `apply_template`,
     `unbind_template`, the library export/import, the
     `template_bindings` field, and `Column.template` provenance.
   - Tests: `test_corner_model_stage2.py` (template tests),
     template tests in `test_corner_manager.py`, possibly stage3.
   - **RISK — scope carefully.** Templates are entangled: `apply_template`
     generates columns, Stage 3 variants may be template-applied,
     `Column.template` is provenance, the `+axis` template syntax was
     one way axes got authored. Axes are now authored directly via the
     Axes dialog, so removing templates does NOT orphan axes. Check what
     breaks before deleting; this is a big deletion.

**2. Make the Axes aggregated-corner builder multi-mode.**
   `_AxesDialog._on_create_corner` currently picks ONE mode via
   `_mode_combo`. Replace it with a checkable mode list — ticking 7
   modes and clicking Create stamps the aggregated corner onto all 7 at
   once. This makes Axes cover 痛点 a (reuse across modes), so the
   template feature is fully redundant.

After this, only one concept remains: **Axes**.

## Deferred / known gaps

- **Model-file axis members** — a `.s5p` inductor file that follows
  temperature is not supported; axes are var-only (`CorrelatedTuple`
  carries vars, not models). Temperature-as-a-var works. Extending
  `CorrelatedTuple` to carry model assignments is a follow-up.
- Variable-row order cannot be pushed to Maestro — Maestro's corner
  editor row order has no SKILL API (`axlPutVar` only appends; no
  `axl*Order`). Push carries per-corner var order; the editor's global
  order is owned by the per-test design-variable lists. Treat the
  simkit row order as a local display preference.

## Verification notes

- The acceptance gate is unchanged: a real signoff cycle inside the GUI
  on the live Maestro session. The user dogfoods; restart `pvt gui` to
  pick up new code.
- `.pvtproject` for live probes: `workarea/simkit_1AXX/.pvtproject`;
  session `fnxSession0`.
