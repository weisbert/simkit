# Handoff — 2026-05-24 (mode empty-registers, pull mirror, dialog parenting)

For the next conversation. Read this first, then the previous handoff at
git `5c5bafb` for context on the PVT Corner Generator that this session
follows up on.

## Branch & state

`main`, clean. One functional commit landed this session:

```
41116e0 gui: corner manager — empty registers, mirror pull, dialog parenting
```

Suite green:

```
.venv/bin/python -m pytest -q   # → 1900 passed, 93 subtests
```

NOT deployed to red zone yet — needs live dogfood (see NEXT).

## What landed (three independent user reports)

### 1 · New Mode no longer overwrites the reference corner

User report: "Mode 只是一个概念，只有在 Corner Generator 之后才出现在 corner
表里面的。看起来好像把原本的 TT corner 覆写了，这不是我希望的。" Plus: "参考的 TT
corner 生成的 mode 里面，一个 registered 的变量我都看不见，哪怕没有被定义，
也应该有的，用户可以再改的。"

Changes (`python/simkit/gui/views/corner_manager.py`,
`python/simkit/corner_model.py`):

- **`mode_from_column()` deleted.** The "From a corner column" flow now
  calls `add_mode()` directly. The reference corner is NOT touched —
  modes are pure-concept entities; columns enter the table only via the
  Corner Generator.
- `_NewModeDialog` reworded as "harvest from a corner column" — drops the
  "Column label" field (no column being created), drops the
  `pvt_label()` accessor.
- `register_vars()` now includes EVERY unticked row, even blank-valued
  ones (intentionally-unset register; design default applies at sim time;
  user can fill in later from the modes panel).
- `_var_contribution` skips empty-string mode register values when
  materialising a row (= no `axlPutVar` at push time). A column override
  or variant override can still fill it in.
- Three validators relaxed to allow empty mode-vars: `_validate_modes`,
  `add_mode`, `reclassify_mode`.
- Modes panel allows editing a register back to empty (was reverted before).

Tests: 3 old `mode_from_column` tests deleted, 3 new tests added covering
empty-register persistence, materialisation skip, and override-fills-unset.

### 2 · Pull mirrors Maestro

User report: "我希望和 cadence 里面完全一样的，包括列和行的顺序". They want
Pull to mean "go back to Cadence's current state".

Changes (`python/simkit/corner_model.py:apply_pull`,
`python/simkit/gui/main_window.py:_on_corner_model_pulled`):

- `apply_pull` rebuilds `model.columns` in **pulled-row order** (matched
  + foreign interleaved as Cadence has them).
- **Local-only corners are DROPPED.** Modes / variants survive; run-set
  memberships pointing at dropped corners are cleaned automatically.
- Aggregated (correlated-axis) columns are kept intact at the FRONT of
  the result — they expand to multiple pulled rows and have no single
  position to mirror to. Common case (no aggregated cols) is pure mirror.
- GUI safety gate: when the pull would drop simkit-only corners, snapshot
  the cornermodel.json to
  `<project>/snapshots/cornermodel_<stamp>/<name>.cornermodel.json` and
  confirm with a list of the corners about to vanish.
  - Snapshot lives in its own timestamped subdir so the filename can be
    exactly `<name>.cornermodel.json` (the loader enforces basename ==
    `cm.name`) — File ▸ Open opens it directly.
  - "Don't ask again this session" checkbox mirrors the Push gate pattern.
  - Snapshots are always taken when destructive, regardless of the skip
    flag.
- Log line rewritten: "pull mirrored Maestro: N re-synced, M added, K
  dropped (rollback via …)".

Tests: 2 old apply_pull tests rewritten, 1 new (drops local-only).

**Confirmed unchanged (user asked):**

- **Push column order** already respects simkit's column order
  (`materialize` iterates `model.columns` → sidecar row order → SKILL
  `axlPutCorner` same order). No change needed.
- **Row (variable) order** plumbing is correct: `apply_pull` sets
  `var_order = union_var_order(pulled)`, table renders via
  `ordered_var_rows` which puts `var_order` first. If the user still sees
  a row-order mismatch after pull, that's a separate bug — need a live
  example.

### 3 · Dragging dialogs no longer drags the main window

User report: "弹出了 corner generator 的对话框，当我拖动 corner generator 的
对话框时，背后的 simkit 好像也在一起动".

Cause: X11 WM coupling. `super().__init__(view)` parented the dialog to
the `CornerManagerView` (a child widget inside QTabWidget), not to a
top-level window. Some WMs grouped them.

Changes — three dialogs now `super().__init__(view.window())`:

- `corner_generator.py:453` `CornerGeneratorDialog`
- `corner_manager.py:1936` `_DimensionsDialog`
- `corner_manager.py:2051` `_NewCornerDialog`

`self._view = view` is kept for data access. `_RunSetPanel` (also takes
`view`) is a real embedded child widget, NOT a dialog — correctly left
alone.

## NEXT — user acceptance (live dogfood)

1. **Redeploy** to red zone (`<DEPLOYS>/current` symlink) and re-`cd`
   from a stale terminal.
2. **New Mode from a corner column:** verify the reference corner is NOT
   modified after OK; verify all unticked vars (incl. blank) show up in
   the modes panel; verify the modes-panel cell accepts both filling AND
   re-clearing a register.
3. **Pull from Cadence** with a mix: existing matched corners, Cadence-
   only corners, simkit-only corners. Verify (a) column order = Cadence;
   (b) confirmation dialog fires; (c) snapshot is created and File ▸ Open
   restores; (d) row order matches Cadence (user said they didn't check
   last time).
4. **Drag the three dialogs** (Corner Generator, Dimensions, New Corner)
   on the red-zone X session — confirm the main window stays put.
5. If row order after Pull is wrong, capture a screenshot/example so we
   can dig.

## Still-pending from prior handoff (`5c5bafb`)

These were NOT touched this session:

- `_file_abs` for generator-authored corners (Spectre may emit
  `include ""`, SFE-73). Pending: thread abs path from grid →
  `ModelEntry.file_abs` for manually-typed file rows; `Read from Cadence`
  and `Browse…` paths already carry abs.
- Delete the unreachable `_DimensionsDialog` / `_DimensionGridDialog` /
  `_NewCornerDialog` / `_on_dimensions` / `_on_new_corner` once the
  Corner Generator has survived a full red-zone dogfood. (Note: their
  parent-fix from item 3 above means they're safe to keep around in the
  meantime.)
