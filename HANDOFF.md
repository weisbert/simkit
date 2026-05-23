# Handoff — 2026-05-23 (PVT Corner Generator + live Cadence verification)

For the next conversation. Read this first, then
`docs/corner_manager_user_story.md` (痛点 a + h — VCO bond is the reason a
generator is needed at all) and `python/simkit/gui/views/corner_generator.py`.

## Branch & state

`main`. **Six files uncommitted** — the PVT Corner Generator landed this
session; the user has not yet given the commit go-ahead. Run the suite to
see it green:

```
.venv/bin/python -m pytest -q   # → 1954 passed
```

Uncommitted:

- `python/simkit/corner_model.py` — `axis_is_composite()`, `generate_pattern_columns()`.
- `python/simkit/gui/views/corner_generator.py` *(new)* — the generator dialog.
- `python/simkit/gui/views/corner_manager.py` — Corners-tab toolbar: dropped `Dimensions…` + `New Corner`, added `Corner Generator…`.
- `python/simkit/skill_bridge.py` — `read_model_files()`.
- `tests/test_corner_generation.py` *(new)*.
- `tests/gui/test_corner_generator.py` *(new, M2 render test included)*.

Recent committed history (still in scope to remember):

- `2ef2e87` gui: remember the project opened mid-session.
- `4d14162` gui: corner manager — fix New Dimension `+ Variable` crash.
- `657fad9` examples: preset Beacon PVT corner template.
- `0e9f05d` gui: corner manager — New Mode dialog: narrower, resizable, clearer (process row → non-clickable tag).

## What the generator is

An independent dialog (`Corners` tab → `Corner Generator…`). Two halves:

1. **Level definitions** — three flexible grids (Process / Voltage /
   Temperature). Rows = named levels, columns = the variables each level
   sets. `+ Variable` / `- Variable` / `+ Level` / `- Level` per grid; double-click
   a variable header to rename. Process additionally carries a model file +
   `Browse…` + **`Read from Cadence`** (see Part A below).
2. **Pattern table** — four columns `Corner name | Process | Voltage |
   Temperature`. Each row is one corner. Double-click a P/V/T cell to open
   a checkable list of that axis's levels (also editable as comma-separated
   text). `Target mode` selector + `Generate → corner table`.

The dialog replaces the old `Dimensions…` and `New Corner` dialogs (the
user found them incomprehensible). The dialog *classes* `_DimensionsDialog` /
`_DimensionGridDialog` / `_NewCornerDialog` are kept for now (unreachable
from the toolbar) as a fallback until the generator survives a red-zone
dogfood; delete them in a follow-up.

## The composite vs. simple rule (痛点 h)

An axis is **composite** when its levels bind 2+ variables together (e.g.
Process bound to section + CT, Temperature bound to temperature + indfile).
A composite level cannot fit in one Maestro corner column — the binding
would be lost. So a pattern row **expands along composite axes**: one
output column per element of the composite cross-product, with that level's
values **baked** as scalars.

A **simple** axis controls one variable (or only a section). Its levels
**stay as a sweep inside one column** — no expansion. Voltage = NV/HV/LV
becomes `vdd: ("0.8","0.85","0.75")` swept in every generated column, and
never appears in column names.

Column naming: `<pattern>_<composite-level>…` in pattern-table order. No
suffix when nothing expands.

| Pattern | Process | Voltage | Temperature | Composite? | Result |
|---|---|---|---|---|---|
| `Beacon_PVT_45` | TT…SF | NV…LV | NT…HT | none | 1 column (45 sim points inside) |
| `VCO_PVT_45` | TT…SF (composite) | NV…LV | NT…HT (composite) | Process + Temp | 15 columns × voltage sweep 3 |

## Live Cadence verification — what was caught, what passed

The user **explicitly insisted** the round-trip be verified against live
Cadence — and it caught a real bug the offline suite missed entirely.

### The bug

The first implementation built each generated column with `correlated_axes`
+ `selected_levels`. `materialize` expands every `correlated_axes` crossing
into one union row per combination, so an all-simple `Beacon_PVT_45` became
**45 union rows / 45 Maestro corners** instead of 1. Offline tests passed
because `column_point_count` measures sim points, not union rows.

### The fix

`generate_pattern_columns` now produces **fully baked** columns: composite
axes pinned as scalar pvt_vars + scalar model section; simple axes baked
as swept pvt_vars / swept model section; **no `correlated_axes` on
generated columns**. `materialize` then yields exactly one union row per
generated column. Locked in with `test_all_simple_pattern_materialises_to_one_row`
and `test_composite_pattern_materialises_to_one_row_per_column`.

### Part B — end-to-end push verification ✓

Against the live `fnxSession0` (`sim_yusheng/Test/maestro`, project `1AXX`):

1. Backed up the user's current corner table (`/tmp/simkit_corner_backup.union.json`,
   rows `TT / TT_pvt / TT_2p5G`).
2. Built a generator-derived cornermodel; materialised → **1 union row**;
   `pvt_corners_push(dry_run=True)` → SKILL accepted.
3. Real `pvt_corners_push(replace=False)` → Maestro corner table became
   `[TT, TT_pvt, TT_2p5G, gencheck_pvt]`. Pulled back: `gencheck_pvt` is
   one corner with `VDD=["3","2.8"]`, `temperature=["55","125"]`,
   `EN="1"`, section `["tt","ss","ff"]` — exactly right.
4. `pvt_corners_push(backup, replace=True)` → table restored to the
   original 3 rows. **The user's session is exactly as we found it.**

### Part A — read model file from Cadence ✓

`simkit.skill_bridge.read_model_files()` pulls the live corner table and
extracts `{file: {"file_abs", "sections": [...]}}`. Verified live: returned
`rf018.scs` with abs path + sections `tt/ss/ff`. Wired into the generator's
Process grid as `Read from Cadence` next to `Browse…`; offers to seed the
returned sections as level rows.

## Known gap — model file abs path on generated corners

When the generator pushes `gencheck_pvt`, the corner's `_file_abs` ends up
empty in Maestro — Spectre may emit `include ""` (SFE-73, see
`union.py:ModelEntry` comment). The pulled real rows had `_file_abs`
populated because Maestro knew it. A freshly authored-not-pulled corner
needs the abs path threaded from the grid through to the `ModelEntry`. This
likely affects the old `_NewCornerDialog` corners too — not generator-specific.

Pending: when the user goes through `Read from Cadence`, `read_model_files`
already returns `file_abs`; carry it onto the axis and into generated
`ModelEntry.file_abs`. When they `Browse…`, the chosen path IS absolute —
also usable. Only manual-typing leaves no abs path.

## NEXT — user acceptance

1. Decide on commit + push (six files above).
2. Redeploy to red zone (`<DEPLOYS>/current` symlink) and dogfood the
   generator end to end: open `Corner Generator…`, fill the three grids
   (or `Read from Cadence` on Process), author a pattern, generate,
   confirm columns in the corner table, Push to Maestro, observe.
3. Decide on the `_file_abs` follow-up.
4. Delete the unreachable `_DimensionsDialog` / `_DimensionGridDialog` /
   `_NewCornerDialog` / `_on_dimensions` / `_on_new_corner` once the
   generator has survived a real dogfood pass.

## Red-zone usage note

Deploys live under `<DEPLOYS>/current` (a symlink). A terminal already
activated against an old deploy must **re-`cd <DEPLOYS>/current`** (not
just `deactivate`/re-`source`) to pick up a new deploy — or open a fresh
terminal.
