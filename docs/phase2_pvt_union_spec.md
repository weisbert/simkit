# Phase 2 §1 — PVT-Union Builder Spec

**Schema version: 1** (Phase 2). Frozen surface for Phase 2. Any breaking change requires bumping `union_schema_version` and appending a migration note to `DECISIONS.md`.

Phase 2 of simkit builds **one** authoring helper end-to-end: the PVT-union builder. This document defines the contract; implementation lands in Phase 2 §2 onward.

This spec is informed by the Phase 1 build, the VCO LO 2026-05-11 motivating case (21 columns × 3 points = 63 corners that morally describe a single semantic PVT), and a 2026-05-12 skillbridge probe of the live `simkit_verify` setup which surfaced Maestro's existing declarative-vs-exploded corner model. Phase 2 adopts that model rather than inventing one.

---

## 1. Problem statement (one paragraph)

A semantic PVT — "this block at its TT process, across its supply/temp range, with the right model section per process" — can require Maestro to be configured with **N corner-table rows × M point-sweep points** when even one corner-var (an `.s2p` file path, a CT-tuning override, a model-section pointer) must vary along an axis Maestro's point-sweep mechanism can't express. The VCO LO case hit N=21, M=3. The authoring layer pain is *configuring 63 corners by hand to describe what is conceptually one PVT*. The PVT-union builder lets the engineer write the PVT once, in a declarative sidecar file, and emits the exploded corner table mechanically.

---

## 2. Data model

### 2.1 Maestro's native model (recovered from live probe)

Probed via `axlGetCorner` / `axlGetVars` / `axlGetVar` / `axlGetVarValue` (vars axis) and `axlGetModels` / `axlGetModel` / `axlGetModelFile` / `axlGetModelSection` / `axlGetModelBlock` / `axlGetModelTest` (models axis) on `simkit_verify` (DECISIONS #29):

| Layer | What it looks like | Example (simkit_verify, TT_pvt corner-group) |
|---|---|---|
| **Declarative corner row** | Two parallel axes, each carrying scalar-or-sweep assignments. (a) **Vars axis** — var name → value string. (b) **Models axis** — list of model entries, each with file/block/test/section. | Vars: `temperature="55"`, `VDD="3 2.8"`. Models: one entry, file=`rf018.scs`, block=`Global`, test=`All`, section=`'"tt" "ss" "ff"'`. |
| **Exploded sub-corner** | Cross-product over **every** sweep-shaped field (vars and models alike) → one materialised corner per cross-product point. Maestro names sub-corners `<rowName>_<index>` (0-indexed). | `TT_pvt_0` … `TT_pvt_5` (= 2 VDD × 3 sections × 1 temperature = 6). |

Sweep encoding is the same on both axes: **space-separated string** (model sections are additionally quoted, e.g. `'"tt" "ss"'`). Maestro speaks both the declarative and exploded forms natively; the simulator runs against the exploded form; the engineer authors in the declarative form via the panel. Phase 2 mirrors this exactly: the sidecar file serialises the declarative form; the SKILL bridge round-trips between sidecar and the live `axl*` setup.

**Why the models axis matters.** A model entry's `section` is the model-section (e.g. `ff`/`ss`/`tt`) — i.e. process corner identity. Process-corner sweeps go through this axis, not the vars axis. Skipping it would mean Phase 2 cannot express PVT unions whose process axis varies, which is half the motivating case (VCO LO).

### 2.2 Invariants

- A **union row** is one named corner-table entry with assignments on two parallel axes: **vars** and **models** (§2.3 defines schema for each).
- Every assignment on either axis is either a scalar (string) or a sweep (ordered list of strings).
- The **explode count** of a row = product of all sweep lengths across both axes. Scalars contribute factor 1. Empty sweep list `[]` = load error (row is unsimulable).
- Every assignment is **string-valued at the wire**. Numeric coercion happens at simulator level. The sidecar file does not parse units; `"3"` and `"3.0"` are different strings (matches Maestro behaviour — Maestro stores user-typed text).
- Model-section names emitted to Maestro must be wrapped in `"…"` (Maestro convention: `'"tt" "ss"'`). The sidecar stores them un-wrapped (`"tt"` as a string in JSON, not `"\"tt\""`). Wrap/unwrap is the loader's job, not the user's.
- Sub-corner index ordering: empirically observed on simkit_verify (DECISIONS #29) — fastest-changing axis is the alphabetically-first sweep field (var or model.section, treated as one combined namespace). This is the working **contract for v1**; flagged as Open Decision 8.6 to confirm against the VCO LO case before Phase 2 §2 implementation.

### 2.3 What a "union" is in this spec

A **union** is a named bundle of union rows that together describe one semantic PVT for one testbench. Multiple unions can coexist for one testbench (e.g. `tt_only`, `pvt_extended`, `mc_prep`). Each union is a separate sidecar file. A simulation run uses exactly one union at a time, applied to Maestro before kicking off.

---

## 3. Sidecar file format

### 3.1 Location and naming

| Item | Value | Notes |
|---|---|---|
| Format | strict JSON (no comments, no trailing commas) | Same rationale as `.pvtproject` per DECISIONS #13 — SKILL-side parser exists. |
| Per-union extension | `.union.json` | Two-part extension lets `find -name '*.union.json'` work, parallels the `.pvtproject` discipline. |
| Project-level directory | `<unionsDir>/` | New optional `.pvtproject` field, default `./unions` (relative to the `.pvtproject` file's directory). **Note**: adding this field requires an additive update to `docs/schema.md` §1 as part of Phase 2 §2 loader work; not a `schema_version` bump (additive new optional key, per the unknown-key policy in Phase 1 schema §1). |
| Conflict policy | One file = one union. Filename basename (sans `.union.json`) must equal the file's `name` field, else load error. | Avoids the "renamed file but old name inside" trap. |

### 3.2 Top-level structure

```json
{
  "_doc": "...",
  "union_schema_version": 1,
  "name": "pvt_extended",
  "project": "my_ldo",
  "testbench_id": "MY_LIB/ldo_top_tb/schematic",
  "rows": [
    { ... union row 1 ... },
    { ... union row 2 ... }
  ]
}
```

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `union_schema_version` | int | yes | — | Pinned to `1` for Phase 2. Distinct from `.pvtproject:schema_version` and JSON-dump `schema_version`. |
| `name` | str | yes | — | Union name. Must match `^[a-z0-9_-]+$` and equal filename basename. |
| `project` | str | yes | — | Must match the enclosing `.pvtproject:project`. Load error if mismatched. (Catches misplaced files.) |
| `testbench_id` | str | yes | — | `lib/cell/view`. The bench this union is configured for. (See `testbench_aliases` in `.pvtproject` for the human name.) |
| `rows` | array | yes | — | One or more union rows. Empty array = load error. |
| `_doc` | str \| object | no | — | Reserved for inline docs. Loader ignores. |

### 3.3 Union row object

```json
{
  "row_name": "TT_pvt",
  "vars": {
    "temperature": "55",
    "VDD":         ["2.8", "3"]
  },
  "models": [
    {
      "file":    "rf018.scs",
      "block":   "Global",
      "test":    "All",
      "section": ["ff", "ss", "tt"]
    }
  ]
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `row_name` | str | yes | Maestro corner-table row name. Must match `^[A-Za-z][A-Za-z0-9_]*$` (Maestro's identifier rule, observed). Unique within `rows`. |
| `vars` | object[str → (str \| array[str])] | no | Var name → assignment. Default `{}`. |
| `models` | array[model entry] | no | Per-model section/file/block/test selection. Default `[]`. |

**At least one of `vars` or `models` must be non-empty** — Maestro does not store a corner row with zero overrides (every corner observed live carries at least one setting). A fully-empty row is a load error, not a legal "nominal corner". If you need to declare "TT with everything at defaults", that's not a row; it's the absence of a row.

**Var-assignment shape (`vars` axis).** Identical for sweep encoding:
- `"55"` → scalar.
- `["2.8", "3"]` → sweep of length 2.
- Length-1 array is allowed and not collapsed (round-trip stability).
- Empty array `[]`, mixed types, or non-strings = load error.

**Model entry shape (`models` axis).**

| Field | Type | Required | Notes |
|---|---|---|---|
| `file` | str | yes | Model file basename as Maestro stores it (e.g. `rf018.scs`). Resolved against the model-file search path; absolute path is recovered via `axlGetModelFile` at push time. |
| `block` | str | no | Default `"Global"`. Per Maestro doc (DECISIONS #29 probe), MTS-mode blocks are out-of-scope for v1. |
| `test` | str | no | Default `"All"` (applies to every test). Per-test scoping out-of-scope for v1. |
| `section` | str \| array[str] | yes | Section name (e.g. `"tt"`) or sweep across sections. **Stored unquoted in JSON**; loader handles Maestro's `"..."` wrapping on push/pull. Length-1 array allowed; empty/mixed-type = error. |

Multiple model entries in a row are allowed (different `file` per entry). Within a single entry, `section` is the only sweepable field for v1 — block/test sweeps are deferred (Open Decision 8.4 ties to this).

### 3.4 Explode rules (informative, but normative for round-trip)

Let a row's sweep-shaped fields be enumerated as `f1, f2, ..., fk` (vars and model.section entries pooled into one list) with lengths `n1, n2, ..., nk` (scalars dropped from this list).

1. Materialise `n1 × n2 × ... × nk` sub-corners.
2. **Field ordering** for the index assignment: alphabetic by field key, where the key is the var name for vars-axis fields and `model[k].section` (with `k` being the model entry index) for models-axis fields. The alphabetically-first key is the innermost (fastest-changing) loop.
3. **Value ordering within a field**: sweep values are lexicographically sorted ascending before the index is assigned. Observed empirically on simkit_verify TT_pvt — user-typed `VDD="3 2.8"` is *stored* in declaration order, but *explodes* with values in sorted order `[2.8, 3]` (TT_pvt_0 gets VDD=2.8, TT_pvt_1 gets VDD=3). The sidecar's array order is therefore **not** load-bearing for sub-corner indexing — it round-trips through Maestro's storage, but Maestro re-sorts on explode.
4. Sub-corner index `i` (0-based) maps to the cross-product point `(i mod n1, (i / n1) mod n2, ...)` using the per-field sorted value lists from rule 3.
5. Sub-corner name is `<row_name>_<i>`. If the row has zero sweep fields (all scalars on both axes), the sub-corner inherits `row_name` directly (no `_0` suffix). Matches the live `simkit_verify` observation where the all-scalar `TT` row has no `_0` suffix.

> ⚠ Caveat for numeric strings: lex-sort on strings means `"10"` < `"2"`. If a sweep contains mixed-magnitude numeric strings (e.g. supply voltages `["3", "10", "12"]`), sub-corner indices will not be in numeric order. Two mitigations available to authors: (a) consistent leading-zero formatting (`["03", "10", "12"]`); (b) accept the indexing — names are still stable, just not numerically intuitive. Flagged in Open Decision 8.6.

### 3.5 What is NOT in the sidecar

- **In-scope axes for v1: vars and models.section only.** Not in v1: test enable/disable per corner, device-parameter overrides (`axlSetParameter`), per-corner model `block` / `test` sweeps (MTS-mode), reliability/MC config.
- **Sweep direction**: sub-corners are always simulated as an unordered set; this format does not declare a sweep direction. (Maestro's "from-low / from-high" is a sim-time choice, not a corner-config choice.)
- **Inheritance / templating across rows**: each row is self-contained. No `extends` or "fill from parent". (DECISIONS #31 — keep v1 dumb, layer templating in v2 if it pays off.)

---

## 4. Maestro round-trip surface

Two directions, two layers each (sidecar ↔ disk format ↔ live Maestro). Sidecar is the source of truth.

### 4.1 Write direction (sidecar → Maestro)

| Step | Mechanism | When to use |
|---|---|---|
| **1a. Sidecar → corners CSV** | Pure Python. Emits a CSV in the format Maestro's corners-panel import expects. | When you want to import via Maestro UI: `Tools → Corners → Import`. Offline-clean; no live Maestro needed. |
| **1b. Sidecar → live SKILL push** | SKILL bridge: per row, push vars via `axlPutVar(cornerHandle, name, valueOrSweep)`; push models via `axlPutModel(cornerHandle, fileBasename)` to attach the model, then `axlSetModelSection(modelHandle, sectionOrSweep)` / `axlSetModelBlock` / `axlSetModelTest` as needed. Sweep values emitted as space-separated strings (sections additionally `"..."`-wrapped per Maestro convention). Requires a live session. | When you don't want to leave Maestro to do an Import click. Faster, but cannot be done offline. |

Both paths end at the same Maestro state. The CSV format is the canonical interchange and is the one tested in CI; the SKILL push is a convenience.

### 4.2 Read direction (Maestro → sidecar)

| Step | Mechanism | When to use |
|---|---|---|
| **2a. Live SKILL pull** | SKILL bridge: walk `axlGetCorners(sdb)` → for each corner `axlGetVars(corner)` + `axlGetVarValue` to recover the declarative form. Write sidecar JSON. | First time importing an existing hand-authored corner table into the sidecar workflow. |
| **2b. CSV → sidecar** | Pure Python. Reverse of 1a. | When you have an exported CSV and no live Maestro. |

**Fidelity contract.** Round-trip (sidecar → Maestro → sidecar) must be **bit-identical** for the `vars` map AND `models` array of every row, modulo: (i) JSON whitespace; (ii) `_doc` and other `_`-prefixed keys; (iii) key insertion order within objects. Sweep-value order within an array is preserved through Maestro storage (per §3.4 rule 3, declaration order survives a push/pull cycle even though it does not determine the explode order). This is the §6 acceptance gate.

### 4.3 SKILL bridge API surface (Phase 2 §3)

Declarative spec for the SKILL side. Implementation in Phase 2 §3.

```skill
;; Push a union into the live ADE-XL setup. Overwrites named rows; leaves
;; unnamed rows alone. Writes both axes:
;;   - vars: axlPutVar(cornerHandle, name, valueOrSweep)
;;   - models: axlPutModel(cornerHandle, fileBasename), then
;;             axlSetModelSection(modelHandle, sectionOrSweep) +
;;             axlSetModelBlock / axlSetModelTest as needed
;; Returns pvtOk / pvtErr discriminated result.
(pvtCornersPush sess unionJsonPath [?dryRun nil])

;; Pull the current corner-table from a live session, emit a sidecar.
;; Reads vars via axlGetVars + axlGetVar + axlGetVarValue and models via
;; axlGetModels + axlGetModel + axlGetModelFile/Block/Test/Section.
;; If unionName is non-nil, restrict to rows matching that union spec
;; (idempotent re-pull). Else, write all rows.
(pvtCornersPull sess outPath [?unionName nil])
```

API conventions match Phase 1's `pvtError` / `pvtJson` modules (DECISIONS #14).

---

## 5. CLI surface (Phase 2 §5 — preview)

Mirrors Phase 1's `pvt` subcommand convention. All offline-runnable; live-Maestro variants delegate to the SKILL bridge.

| Command | Purpose | Offline? |
|---|---|---|
| `pvt corners build <union>.union.json [--out <path>]` | Validate sidecar + emit Maestro corners-CSV. Default output: `<union>.csv` next to the sidecar. | yes |
| `pvt corners push <union>.union.json` | Push to live session via skillbridge. Bench inferred from the union's `testbench_id`; mismatch with the active Maestro session = error. | no |
| `pvt corners pull <output>.union.json` | Pull current Maestro corners into a sidecar. Bench auto-detected from the live session (`axlGetSession{Lib,Cell,View}Name`). Union `name` derived from output filename basename. | no |
| `pvt corners list [--project P]` | List unions configured in `<unionsDir>/`. | yes |
| `pvt corners explode <union>.union.json [--json]` | Print exploded sub-corners (debug / inspection). | yes |
| `pvt corners diff <a>.union.json <b>.union.json` | Compare two unions row-by-row, axis-by-axis. | yes |

---

## 6. End-to-end acceptance gates (Phase 2 §6 preview)

Same shape as Phase 1 §6 gates. Fixtures at `tests/fixtures/unions/`.

1. **Gate U1 — Round-trip fidelity on simkit_verify shape.** Hand-author a sidecar matching the observed `TT` + `TT_pvt` (2 rows, 1 + 2×3 = 7 sub-corners). Push to live. Pull back. Bit-identical (per §4.2 fidelity contract).
2. **Gate U2 — VCO LO acceptance.** Real 21-col × 3-pt setup. Pull to sidecar, modify one corner's sweep (e.g. add a process corner), push back, simulate one test, verify the new sub-corner appears in the run dump.
3. **Gate U3 — Explode arithmetic.** For a synthetic union with 3 sweep vars of length 2, 3, 5: assert `pvt corners explode` returns 30 sub-corners with the documented lex-ordered names.
4. **Gate U4 — Cross-format round-trip.** Sidecar → CSV → Sidecar must be bit-identical (modulo §4.2 modulus). Catches CSV escaping bugs in model-file paths and section names (commas, quotes, spaces).

---

## 7. Versioning policy

- `union_schema_version` in every sidecar. Starts at `1`. Loader strict `== 1`; unknown major = `UnionSchemaVersionError`.
- Phase 2 §1 (this doc) is frozen at version 1. Adding new optional fields = no bump. Renaming or removing fields, or changing semantics of existing fields = major bump + migration note in DECISIONS.
- Same versioning discipline as Phase 1 (DECISIONS #2, #18).

---

## 8. Open decisions to resolve before Phase 2 §2 (loader)

These do **not** need to be answered to lock this spec, but Phase 2 §2 (Python loader) cannot start until each has an answer in DECISIONS.

| # | Question | Default in this draft | What to weigh |
|---|---|---|---|
| 8.1 | Multiple unions per bench in `<unionsDir>/` — does `pvt corners push` need to refuse if more than one targets the same bench, or accept and let the user pick? | Accept, require explicit `--union <name>` if ambiguous. | Pain frequency: how often will you have >1 union per bench? If "always" (TT-only / extended split), make explicit. |
| 8.2 | `unionsDir` default = `./unions` or `<dbRoot>/unions`? | `./unions` (under project root, not dbRoot). Inputs vs outputs separation. | If you commit unions to git but don't commit `dbRoot`, the default placement matters. |
| 8.3 | CSV format for §4.1a — adopt Maestro's existing corners-CSV export format verbatim, or use a simpler row-per-sub-corner layout? | Adopt Maestro's. Zero round-trip surprises. | Cost: gather a real Maestro CSV export and reverse-engineer the escaping. |
| 8.4 | What about `axlSetParameter` (device-level corner-var paths like `Library/Cell/View/Instance/Property`)? | Excluded from v1 per §3.5. | The VCO LO case may need this (per-corner instance overrides). Confirm before §2 if it's actually required. |
| 8.5 | Sub-corner index → name format — is `<row_name>_<i>` (with `_` separator) safe across all customer naming conventions, or should we expose a `sub_corner_format` field in the union? | Hard-coded `<row_name>_<i>`. Matches observed Maestro behaviour. | If any customer site uses `_<i>` in `row_name` itself, collision risk. |
| 8.6 | Sub-corner index assignment rule — the §3.4 contract says "alphabetic by field key, innermost = first". Verified on simkit_verify (TT_pvt: VDD inner, model.section outer); needs confirmation on the VCO LO 21-col case where more sweep axes interact. | Empirical contract from simkit_verify. | If Maestro uses a different rule at larger scale, every sub-corner name shifts and round-trip diff explodes. Mitigation: Gate U1 (round-trip on simkit_verify) plus a focused Tier-2 probe on the VCO LO before §2 lands. |

---

## 9. Worked example: simkit_verify (the live probe)

`config/pvt_union_example.union.json` provides a minimal sidecar matching the live `simkit_verify` corner table as observed on 2026-05-12 via skillbridge (DECISIONS #29). Reproduce by:

```sh
pvt corners explode config/pvt_union_example.union.json
```

Expected output (7 sub-corners — 1 from `TT`, 6 from `TT_pvt`):

```
TT              temperature=55, model.section=tt
TT_pvt_0        temperature=55, VDD=2.8, model.section=ff
TT_pvt_1        temperature=55, VDD=3,   model.section=ff
TT_pvt_2        temperature=55, VDD=2.8, model.section=ss
TT_pvt_3        temperature=55, VDD=3,   model.section=ss
TT_pvt_4        temperature=55, VDD=2.8, model.section=tt
TT_pvt_5        temperature=55, VDD=3,   model.section=tt
```

Index ordering per §3.4 step 2: alphabetic-by-key on the pooled sweep fields. Keys `VDD` (vars axis) and `model[0].section` (models axis); `VDD` < `model[0].section` alphabetically → `VDD` is innermost, model.section outer. Match against `axlGetCornersForATest` output observed live confirms this ordering for simkit_verify.

Round-trip via `pvt corners pull` against the live `fnxSession0` must reproduce this exact JSON (modulo `_doc` and key order). This becomes the §6 Gate U1 regression test.
