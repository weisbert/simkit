# Phase 3B §1 — Formula-Template Authoring Spec

**Schema versions: template `1`, signal-group `1`, measurement-bundle `1`** (Phase 3B). Frozen surface for Phase 3B. Any breaking change requires bumping the respective `*_schema_version` and appending a migration note to `DECISIONS.md`.

Phase 3B of simkit builds **one** authoring helper end-to-end: the formula-template framework. This document defines the contract; implementation lands in Phase 3B §2 onward.

This spec is informed by the Phase 1 + 2 builds, the Phase 2 PVT-union sidecar architecture (which Phase 3B mirrors structurally), and a 2026-05-14 skillbridge probe of the live `fnxSession0` Outputs table which surfaced both the available `axl*Output*` API and an absence — there is no Maestro-side parse-without-evaluate entry, so the "Cadence checks template legitimacy" rule has been re-rooted in Python (DECISIONS #39 P3B.F3, #40).

---

## 1. Problem statement (one paragraph)

A measurement formula — "rise_time at 0.5–0.8 of VDD on signal X", "average current into supply Y across the run" — is currently authored once per signal, per testbench, in Maestro's Calculator/Outputs panel. Reusing the same formula across many signals or across many TBs means manual copy-edit-paste, with the parameters (signal path, thresholds) re-typed each time. The framework pain is *re-authoring the same formula structure for each signal × testbench combination*. The formula-template authoring helper lets the engineer write the formula once, in a declarative sidecar template, parameterise its variable atoms (`$SIG`, `$V_LOW`, …), and apply it across a named signal-group and a named test via one CLI command. v1 ships the framework, not a pre-baked template library — the user authors their own templates against the scaffold (DECISIONS #38).

---

## 2. Data model

### 2.1 Maestro's native Outputs model (recovered from live probe)

Probed 2026-05-14 against `fnxSession0` (see DECISIONS #40 for full API map). Outputs in ADE-XL are stored per-test as a flat list of rows with the following columns (Maestro CSV export):

| Column | Type | Notes |
|---|---|---|
| `Test` | string | Test name in the session. |
| `Name` | string | Output name. Empty for signal-tap outputs (Type=net); always populated for expression outputs (Type=expr). |
| `Type` | enum | `net` (signal-tap) or `expr` (expression). Phase 3B operates exclusively on `expr` rows. |
| `Output` | string | For `net`: signal path (`/Vin`). For `expr`: full calculator expression. |
| `Plot` | enum | `t` or empty. |
| `Save` | enum | `t` or empty. |
| `Spec` | string | Optional spec-range constraint. Phase 3B v1 ignores; passthrough on pull/push. |

`evalType` (`point` / `corners` / `sweeps` / `maa`) is settable at row creation via `axlAddOutputExpr ?evalType` but **does not appear in the CSV export**, so the CSV is lossy for it. Phase 3B's authoritative source is therefore the **template sidecar** (which carries `evalType`); the CSV snapshot is the snapshot-and-recovery format, not the source of truth.

Real-world composite expressions from `fnxSession0` (concrete v1 reference cases):

```
Rtime_clkout = average(riseTime(vtime('tran "/Vout") 0 nil VAR("VDD") nil 10 90 t "time"))
PN_wave     = rfEdgePhaseNoise(?result "pnoise_sample_pm0" ?eventList 'nil)
PN_1M       = value(PN_wave 1000000)
```

These motivate the v1 contract: expressions are **arbitrary composite Cadence calculator strings**, not a hand-listed set of canonical functions. Templates parameterise the variable atoms of such strings; nothing about the expression structure is interpreted by Phase 3B beyond placeholder substitution.

### 2.2 Invariants

- A **template** is a named formula with zero or more `$PARAM` placeholders. Templates have no signal binding and no test binding — those are supplied at apply time.
- A **signal group** is a named ordered list of signal paths (strings starting with `/`). No metadata about voltage-vs-current type (DECISIONS #39 P3B.F1).
- A **measurement bundle** is a named application: it references one or more templates, one signal-group (or empty when no template needs `$SIG`), one test name, and optional per-(template, signal) parameter overrides. The bundle is the unit that `pvt measure apply` consumes.
- A template's expression is **string-valued at the wire**. No parsing of calculator semantics on the Python side beyond structural validation (balanced parens/quotes/braces; declared placeholders resolved).
- Substitution is **textual replacement of `$NAME` tokens**, where `NAME` matches `^[A-Z][A-Z0-9_]*$`. A `$` not followed by such a token is a literal `$`. No nested substitution; no escape syntax in v1 (`$$` not interpreted).
- An applied output's **name** is `<template.short_alias>_<signal_basename>` for templates with `$SIG`, or `<template.short_alias>` for templates without. `signal_basename` is the last `/`-separated segment of the signal path (e.g. `/buf/y` → `y`). Collisions across a bundle's render are a load error.

### 2.3 What is a "template", "signal group", "measurement bundle"

The skeleton uses **three weakly-coupled sidecar types**. Justification + alternatives in DECISIONS #41.

| Sidecar | What it is | Reuse axis |
|---|---|---|
| Template | One formula with placeholders | Reused across many signal-groups and many bundles |
| Signal group | One list of signal paths | Reused across many bundles |
| Measurement bundle | One application (templates × signal-group × test) | The "deliverable" — what you commit and re-run |

---

## 3. Sidecar file formats

### 3.1 Locations and naming

| Sidecar | Default dir (under `.pvtproject`) | Extension | `.pvtproject` override key |
|---|---|---|---|
| Template | `./templates/` | `.template.json` | `templatesDir` |
| Signal group | `./signal_groups/` | `.siggroup.json` | `signalGroupsDir` |
| Measurement bundle | `./measurements/` | `.measure.json` | `measurementsDir` |

All three are strict JSON (per DECISIONS #13). Two-part extension lets `find -name '*.template.json'` discriminate (parallels `.union.json`).

Filename basename (sans extension) must equal the file's `name` field. Load error on mismatch.

### 3.2 Template object

```json
{
  "_doc": "Rise-time at 10–90% of VDD on a single voltage signal.",
  "template_schema_version": 1,
  "name": "rise_time_10_90",
  "short_alias": "Rtime",
  "expression": "average(riseTime(vtime('tran \"$SIG\") 0 nil VAR(\"VDD\") nil $V_LOW $V_HIGH t \"time\"))",
  "params": [
    {"key": "SIG",     "kind": "signal",  "doc": "Voltage signal to measure"},
    {"key": "V_LOW",   "kind": "number",  "default": "10", "doc": "Lower threshold (%)"},
    {"key": "V_HIGH",  "kind": "number",  "default": "90", "doc": "Upper threshold (%)"}
  ],
  "eval_type": "point",
  "plot": true,
  "save": false,
  "unit": "s"
}
```

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `template_schema_version` | int | yes | — | Pinned to `1`. Distinct from the other two schemas. |
| `name` | str | yes | — | Match `^[a-z][a-z0-9_]*$`. Equals filename basename. |
| `short_alias` | str | no | `name` | Used in output naming. Match `^[A-Za-z][A-Za-z0-9_]*$`. Kept short (e.g. `Rtime`) since it prefixes every applied output. |
| `expression` | str | yes | — | Composite calculator expression. May contain `$NAME` placeholders. |
| `params` | array | yes | — | One entry per placeholder. Order is documentation only; substitution is by name. Empty array allowed (template with no placeholders). |
| `params[].key` | str | yes | — | Placeholder identifier (without `$`). Must appear in `expression` as `$<KEY>` at least once. Reverse: every `$<KEY>` in expression must be in `params`. |
| `params[].kind` | enum | yes | — | `signal`, `number`, or `string`. v1 substitution: `signal` substitutes raw (no quotes added — the template body owns surrounding quotes like `vtime('tran "$SIG")`); `number` substitutes raw; `string` substitutes raw. **There is exactly one** `signal` param per template in v1; multi-signal templates deferred to v2. |
| `params[].default` | str | no | — | Used when bundle's override does not specify. Required if any bundle wants to apply without an override for this param. |
| `params[].doc` | str | no | — | Free-form. |
| `eval_type` | enum | no | `"point"` | Passed to `axlAddOutputExpr ?evalType`. `point` / `corners` / `sweeps` / `maa`. |
| `plot` | bool | no | `true` | Passed to `?plot`. |
| `save` | bool | no | `false` | Passed to `?save`. |
| `unit` | str | no | — | Documentation only; not consumed by Maestro. |
| `_doc` | str \| object | no | — | Reserved for inline docs. Ignored by loader. |
| `_pasted_from` | str | no | — | Set by paste-importer to the original concrete expression. Ignored on load; preserved on save. |

### 3.3 Signal group object

```json
{
  "_doc": "Voltage outputs that need rise/fall measurements.",
  "signal_group_schema_version": 1,
  "name": "voltage_outs",
  "signals": ["/Vout", "/Vout2", "/buf/y"]
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `signal_group_schema_version` | int | yes | Pinned to `1`. |
| `name` | str | yes | Match `^[a-z][a-z0-9_]*$`. |
| `signals` | array[str] | yes | One or more paths. Each must start with `/`. Order preserved (deterministic apply ordering). Duplicates rejected. |

### 3.4 Measurement bundle object

```json
{
  "_doc": "Standard rise-time + average characterisation on voltage outs.",
  "measure_schema_version": 1,
  "name": "voltage_outs_review",
  "project": "my_block",
  "testbench_id": "MY_LIB/my_block_tb/schematic",
  "test_name": "Test",
  "apply": [
    {
      "template": "rise_time_10_90",
      "signal_group": "voltage_outs"
    },
    {
      "template": "rise_time_10_90",
      "signal_group": "voltage_outs",
      "param_overrides": { "V_LOW": "20", "V_HIGH": "80" },
      "alias_suffix": "_20_80"
    },
    {
      "template": "result_value_at_freq",
      "signal_group": null,
      "param_overrides": { "OUT_NAME": "PN_wave", "FREQ": "1000000" }
    }
  ]
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `measure_schema_version` | int | yes | Pinned to `1`. |
| `name` | str | yes | Match `^[a-z][a-z0-9_]*$`. Equals filename basename. |
| `project` | str | yes | Must match enclosing `.pvtproject:project`. |
| `testbench_id` | str | yes | `lib/cell/view`. |
| `test_name` | str | yes | Maestro test name (e.g. `"Test"` for `fnxSession0`). |
| `apply` | array | yes | One or more application entries. |
| `apply[].template` | str | yes | Template name. Resolved against `<templatesDir>/`. |
| `apply[].signal_group` | str \| null | conditional | Signal group name OR `null` when the template has no `signal`-kind param. Resolved against `<signalGroupsDir>/`. |
| `apply[].param_overrides` | object[str → str] | no | Map of `param.key` → string value. Required for any param without a `default` and not of kind `signal`. |
| `apply[].alias_suffix` | str | no | Appended to `template.short_alias` for output naming. Lets one template be applied with two different parameter sets in the same bundle (e.g. 10–90% and 20–80% rise-time). Match `^[A-Za-z0-9_]*$`. |

**Render contract:**
- For each entry, if template has a `signal`-kind param: render once per signal in the group → output name = `<short_alias><alias_suffix>_<signal_basename>`.
- If template has no `signal`-kind param: render once → output name = `<short_alias><alias_suffix>`.
- Substitution: every `$KEY` in expression is replaced with either `param_overrides[key]` (highest priority) or `params[key].default`. Missing both = load error.
- `$SIG` substitutes the raw path string (no surrounding quotes added by the substitution engine). The **template body** owns surrounding quotes / accessor wrapping. The fnxSession0 case shows the standard pattern: source `vtime('tran "/Vout")` paste-imports as `vtime('tran "$SIG")` — paste-importer keeps the `"..."` around the path and only the path content becomes the placeholder. Rendering with signal `/Vout` therefore reconstitutes `vtime('tran "/Vout")` byte-for-byte. v1 substitution is dumb (textual); see §3.6 Open Decisions.

### 3.5 What is NOT in the sidecars (v1 exclusions)

- **Specs** (`axlAddSpecToOutput`). The `Spec` CSV column is preserved on pull as a passthrough field but not part of templates.
- **User-defined Output columns** (`axlAddOutputsColumn` and friends). Out of scope.
- **Template inheritance / extension**. Each template is self-contained.
- **Multi-signal templates** (templates with two `signal`-kind params). v1 enforces exactly one. Defer to v2 once the single-signal flow is in daily use.
- **Cross-project template sharing**. v1 is project-local. User-home `~/.simkit/templates/` is a possible v2.
- **Pre-baked rise_time / dutyCycle / avg_current / overshoot etc. library**. v1 ships the framework. User authors against the scaffold; built-ins land later (a single `config/builtins/*.template.json` directory, gated by an explicit `pvt measure install-builtins` command).
- **calcVal-based post-sim retrieval**. Out of scope for Phase 3B (belongs in the Consume layer / Phase 3 report-generator candidate).

### 3.6 Open decisions to resolve before §2

| # | Question | Default in this draft | What to weigh |
|---|---|---|---|
| 9.1 | `$SIG` substitution semantics — substitute the raw path (`/Vout`), with the template owning surrounding quotes/accessor; OR substitute the full accessor expression (`VT("/Vout")`)? | **Resolved 2026-05-14 — raw path, template owns surrounding quotes/accessor.** Paste-importer preserves `"..."` around the path so the template body keeps the source's quoting (`vtime('tran "$SIG")`). | If users ever want one template body to work with both voltage and current accessors, "raw path" is wrong and "full accessor" is right. Defer until a real case appears. |
| 9.2 | Numeric placeholders — substitute as bare numbers (`0.5`) or as strings (`"0.5"`)? | Bare. Calculator expressions accept bare numerics in all positions seen. | If we hit a context where the calculator expects a quoted form, escalate. |
| 9.3 | `param_overrides` value type — restrict to string, or allow JSON number / bool? | String. Mirrors Phase 2 union format (all wire-string). User responsible for emitting `"0.5"` not `0.5`. | Slight ergonomic loss but uniformity wins (single substitution code path). |
| 9.4 | Output name collision policy within one bundle render — error or auto-suffix? | Error (load-time). | Auto-suffixing surprises users; explicit `alias_suffix` is the documented way to disambiguate. |
| 9.5 | Apply order across bundle's `apply[]` entries — deterministic = file order? | Yes, file order. | Stable round-trip; user controls grouping in the output table. |

---

## 4. Maestro round-trip surface

Two directions, two layers each. The **bundle sidecar is the source of truth**.

### 4.1 Write direction (sidecar → Maestro)

| Step | Mechanism | When to use |
|---|---|---|
| **1a. Bundle → DPL batch** | Python renders each `apply[]` entry into the DPL form `(nil 'outputName N 'expr E 'evalType T 'plot P 'save S)`. List passed to a single `axlAddOutputExpr` call with `?exprDPLs` for batch insert. | Primary v1 push path. Per-row error reporting, evalType preserved, dry-run trivial. |
| **1b. Bundle → CSV → axlOutputsImportFromFile** | Python renders, emits CSV, SKILL calls `axlOutputsImportFromFile ?operation "merge"` by default (DECISIONS #42 — overwrite mode replaces the entire Outputs table, not just same-named rows; `merge` is the safe default). | Snapshot-restore flow only (after crash). Lossy on evalType. Not exposed in `pvt measure apply`; reachable via `pvt measure restore <snapshot> [--operation overwrite\|merge\|retain]`. |

By default `pvt measure apply` is **additive** — uses per-row `axlAddOutputExpr` which silently overwrites same-named outputs but leaves all other rows alone. `--replace` first deletes each named output via `axlDeleteOutput`, then adds.

### 4.2 Read direction (Maestro → sidecar)

| Step | Mechanism | When to use |
|---|---|---|
| **2a. Live SKILL pull → snapshot** | `axlOutputsExportToFile` to a temp CSV, parse in Python, write `<name>.snapshot.json`. | Audit current state; capture for recovery. v1 snapshot is "raw" — no template match-back. |
| **2b. (deferred to v2) Snapshot → bundle reverse engineering** | Heuristic match each expr row back to a (template + param values) pair using the templates library. Unmatched rows logged as orphans. | Migrating an existing hand-authored Outputs table into the bundle workflow. v1 ships without this. |

**Fidelity contract.** Round-trip of a snapshot file (snapshot → push via 1b → pull via 2a) must be bit-identical, modulo: (i) JSON whitespace; (ii) `_doc` and `_`-prefixed keys; (iii) key insertion order. Round-trip of a bundle through render → push → pull is **not** bit-identical to the original bundle in v1 (no match-back); the contract is that the rendered expressions appear unchanged in the snapshot. §6 Gates M2 / M3 nail this down.

### 4.3 SKILL bridge API surface (Phase 3B §3)

```skill
;; Push pre-rendered output rows (template instances) into the live ADE-XL setup.
;; Reads a JSON sidecar containing pre-rendered DPL entries (Python does the
;; render so SKILL stays dumb). Each entry = (outputName expr evalType plot save).
;; Returns pvtOk / pvtErr discriminated result with per-row outcome list.
(pvtMeasurePush sess renderedJsonPath [?testName "Test"] [?dryRun nil] [?replace nil])

;; Snapshot the live Outputs table to a sidecar JSON.
;; Calls axlOutputsExportToFile to a temp CSV, parses, writes snapshot JSON.
;; Filter to ?type "expr" by default; pass ?includeSignals t to include net rows.
(pvtMeasurePull sess outPath [?testName "Test"] [?includeSignals nil])
```

API conventions match Phase 1/2 — `pvtError` / `pvtJson` modules (DECISIONS #14).

---

## 5. CLI surface (Phase 3B §5 — preview)

Mirrors Phase 1/2's `pvt` subcommand convention. Authoring is offline; apply/pull need a live Maestro session.

| Command | Purpose | Offline? |
|---|---|---|
| `pvt measure new-template <name> --from-expr "<concrete>" [--interactive]` | Paste-import flow. Auto-extract signal paths to `$SIG`; interactively prompt for each numeric literal. Writes `<templatesDir>/<name>.template.json`. | yes |
| `pvt measure list-templates [--project P]` | Enumerate templates. | yes |
| `pvt measure show-template <name>` | Pretty-print the template body. | yes |
| `pvt measure new-signal-group <name> --signals path,path,...` | Create signal group. | yes |
| `pvt measure list-signal-groups [--project P]` | Enumerate signal groups. | yes |
| `pvt measure new-bundle <name> --templates t1,t2 --signal-group sg --test T` | Create measurement bundle scaffold. User edits to add overrides / aliases. | yes |
| `pvt measure list-bundles [--project P]` | Enumerate bundles. | yes |
| `pvt measure render <bundle> [--out path]` | Offline render of a bundle to a flat table (debug / inspection). Default output: `<bundle>.rendered.csv` next to the sidecar. | yes |
| `pvt measure apply <bundle> [--session S] [--dry-run] [--replace]` | Push to live Maestro via skillbridge. | no |
| `pvt measure pull <out>.snapshot.json [--session S] [--include-signals]` | Pull current Maestro Outputs to a snapshot file. | no |
| `pvt measure diff <a> <b>` | Diff two bundles, or two snapshots, or bundle vs snapshot (after render). | yes |
| `pvt measure restore <snapshot> [--session S]` | Push a snapshot back via `axlOutputsImportFromFile overwrite`. Crash recovery. | no |

---

## 6. End-to-end acceptance gates (Phase 3B §6 preview)

Same shape as Phase 1 / 2 gates. Fixtures at `tests/fixtures/measure/`.

1. **Gate M1 — Paste-import faithfulness.** Take `fnxSession0`'s real `Rtime_clkout` expression verbatim; paste-import to a template via the non-interactive code path (only `$SIG` auto-extracted, numeric literals retained); render with `signal_path="/Vout"`; emitted expression equals the original modulo whitespace. Pinned as 3-case `TestGateM1PasteRoundTrip`.

2. **Gate M2 — Apply round-trip.** Author template (the rise_time_10_90 example above) + signal group (`["/Vout"]`) + bundle from scratch; apply to live `fnxSession0`'s "Test" test in `--dry-run` mode and assert the rendered DPL list is correct; live-apply once for real and verify via `pvt measure pull` that the new output landed with the expected name (`Rtime_Vout`) and expression; the existing 11 rows untouched; cleanup via `pvt measure apply --replace` against an empty bundle (or `axlDeleteOutput` directly). Pinned as a 4-case live test gated on the bridge being up.

3. **Gate M3 — Snapshot bit-identical.** `pvt measure pull` against `fnxSession0` → snapshot file; `pvt measure restore` that snapshot via `axlOutputsImportFromFile overwrite`; pull again; bit-identical (per §4.2 fidelity contract). Pinned as a 3-case live test.

4. **Gate M4 — Python validation surface.** Load-time rejection of: (a) unbalanced parens in expression; (b) `$PARAM` in expression not declared in `params`; (c) `params` entry never referenced in expression; (d) quote imbalance; (e) `apply[].signal_group` non-null but template has no signal param; (f) `apply[].signal_group` null but template has signal param; (g) missing required `param_overrides` for a param with no default; (h) output name collision across bundle render. 8 negative test cases pinned offline (no live Maestro needed).

---

## 7. Versioning policy

- Three independent `*_schema_version` fields, one per sidecar type. All start at `1`. Loaders strict `== 1`; unknown major = `<Type>SchemaVersionError`.
- Phase 3B §1 (this doc) is frozen at v1. Adding new optional fields = no bump. Renaming, removing, or changing semantics = major bump + DECISIONS entry.
- Same versioning discipline as Phase 1 (DECISIONS #2, #18) and Phase 2.

---

## 8. Worked example: `fnxSession0` Rtime_clkout

`config/template_example.template.json` reverse-engineers the live `Rtime_clkout` into a v1 template, with the threshold numbers parameterised:

```json
{
  "template_schema_version": 1,
  "name": "rise_time_threshold",
  "short_alias": "Rtime",
  "expression": "average(riseTime(vtime('tran \"$SIG\") 0 nil VAR(\"VDD\") nil $V_LOW $V_HIGH t \"time\"))",
  "params": [
    {"key": "SIG",    "kind": "signal"},
    {"key": "V_LOW",  "kind": "number", "default": "10"},
    {"key": "V_HIGH", "kind": "number", "default": "90"}
  ],
  "eval_type": "point",
  "plot": true,
  "save": false,
  "unit": "s",
  "_pasted_from": "average(riseTime(vtime('tran \"/Vout\") 0 nil VAR(\"VDD\") nil 10 90 t \"time\"))"
}
```

`config/signal_group_example.siggroup.json`:

```json
{
  "signal_group_schema_version": 1,
  "name": "voltage_outs",
  "signals": ["/Vout"]
}
```

`config/measure_bundle_example.measure.json`:

```json
{
  "measure_schema_version": 1,
  "name": "voltage_outs_rise",
  "project": "my_block",
  "testbench_id": "fnxLib/my_block_tb/schematic",
  "test_name": "Test",
  "apply": [
    { "template": "rise_time_threshold", "signal_group": "voltage_outs" }
  ]
}
```

Reproduce by:

```sh
pvt measure render config/measure_bundle_example.measure.json
```

Expected output (1 row — 1 signal × 1 template):

```
test  output_name   expression
Test  Rtime_Vout    average(riseTime(vtime('tran "/Vout") 0 nil VAR("VDD") nil 10 90 t "time"))
```

When the actual `_pasted_from` is supplied and signal_path is `/Vout`, the rendered expression equals `_pasted_from` exactly. This is the Gate M1 contract.

Live apply (`pvt measure apply ...`) on `fnxSession0` adds a row to Test with name `Rtime_Vout` (would collide with the existing `Rtime_clkout` only if signal paths matched). Subsequent `pvt measure pull` captures the new row in a snapshot. Both round-trips are §6 acceptance gates.
