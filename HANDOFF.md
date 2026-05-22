# Handoff — 2026-05-22 (Corner Sets retired, Axes unified)

For the next conversation. Read this first, then read
`docs/corner_manager_user_story.md` (痛点 a + h).

## Branch & state

`main`, all committed and clean. Recent commits:

- `dc39210` New Mode dialog lists every design variable.
- `0df5426` Axes feature — author a correlated axis as a grid, cross
  axes into an aggregated corner column.
- `cd476bf` docs handoff.
- `523e90a` **retire Corner Sets, unify on multi-mode Axes** — the
  task below, now done.

Offline tests green: full suite **1919 passed**; corner + GUI subset
**715 passed** (`QT_QPA_PLATFORM=offscreen PYTHONPATH=python
.venv/bin/python3 -m pytest tests/gui tests/test_corner_model*.py
tests/test_corners*.py -p no:cacheprovider -q`; use `timeout`, a real
modal hangs offscreen Qt — mock `QMessageBox` in smoke tests).

## What changed (commit 523e90a)

Corner Sets (PVT templates) and Axes overlapped — to a user they were
the same idea. Unified on one concept: **Axes**.

- **Removed the whole PVT-template feature.** Data layer: `PvtTemplate`,
  `TemplateColumn`, `TemplateBinding`, `add_pvt_template`,
  `apply_template`, `unbind_template`, `Column.template`,
  `pvt_templates` / `template_bindings` fields, and the cornerlib
  export/import (`CornerLibrary`, `load/export/import_library`,
  `library_to_dict`). GUI: the Corner Sets toolbar button + dialog, the
  free-text column parsers, the apply / unbind / library handlers.
- **Axes builder is now multi-mode.** `_AxesDialog` replaced the single
  mode combo with a checkable mode list — one Create stamps the crossed
  aggregated corner onto every ticked mode at once (痛点 a). Verified
  live-style by smoke test: ticking VCO + LO stamps `PVT3` onto both.

Only one reusable-corner concept remains: **Axes** (toolbar "Axes…").

## NEXT — user acceptance

The acceptance gate is unchanged: a real signoff cycle inside the GUI
on the live Maestro session. The user dogfoods; restart `pvt gui` to
pick up new code. `.pvtproject` for live probes:
`workarea/simkit_1AXX/.pvtproject`; session `fnxSession0`. The literal
6-stage GUI checklist (`docs/phase5_dogfood_checklist.md`) is still the
per-stage hands-on acceptance.

## Deferred / known gaps

- **Model-file axis members** — a `.s5p` inductor file that follows
  temperature is not supported; axes are var-only (`CorrelatedTuple`
  carries vars, not models). Temperature-as-a-var works. Extending
  `CorrelatedTuple` to carry model assignments is a follow-up.
- Variable-row order cannot be pushed to Maestro — Maestro's corner
  editor row order has no SKILL API (`axlPutVar` only appends). Treat
  the simkit row order as a local display preference.
