# Architectural Decision Log

Append-mostly. Each entry captures a decision and its rationale. Edit old entries only to correct errors. If a decision is reversed, append a new entry that supersedes it — never delete.

Format:
```
## #N — Short title
_Date: YYYY-MM-DD_
**Decision:** ...
**Why:** ...
**Alternatives considered:** ... (optional)
**Supersedes / superseded by:** ... (optional)
```

---

## #1 — Three-pillar architecture; build data pillar first
_Date: 2026-04-21_

**Decision:** Project is organized as three weakly-coupled pillars:
1. TB authoring helpers (SKILL-heavy, Python as template engine)
2. Simulation orchestrator (Python drives Maestro/OCEAN)
3. Data layer (structured results, queryable, versionable)

Build order: **data first**, then one isolated authoring helper, then orchestrator.

**Why:** The data layer is the only pillar with no manual workaround; it's the foundation that makes compliance tables and cross-version comparison possible. Authoring and orchestration can be done by hand if needed.

**Alternatives considered:** Monolithic "simulator assistant" — rejected as high-risk and all-or-nothing.

---

## #2 — Data layer: JSON exchange + DuckDB/SQLite query layer
_Date: 2026-04-21_

**Decision:** Simulation dumps produce JSON per run (human-readable, git-friendly archive). A Python ingester loads JSON into DuckDB for cross-run queries.

**Why:** JSON alone makes "TT worst-case across runs" and cross-version delta queries tedious. DuckDB gives SQL for free. Files + DB = both an archive and a query surface.

---

## #3 — Mixed Python↔SKILL bridges; do not force one style
_Date: 2026-04-21_

**Decision:** Different bridge styles for different concerns:
- **File exchange** (JSON out, `load()` in) → data dump path
- **Socket bridge** (skillBridge / CIW socket) → interactive authoring helpers
- **CLI subprocess** (`virtuoso -nograph -replay`) → batch orchestration

**Why:** Each fits a different scenario. A unified bridge adds complexity without benefit.

---

## #4 — No early GUI
_Date: 2026-04-21_

**Decision:** Early phases are CLI + config files + SKILL scripts only. GUI (likely PySide6) only after the useful action set stabilizes.

**Why:** Premature GUI work drains time from feature validation. The real shape of the tool isn't visible until the CLI is in daily use.

---

## #5 — Two ingest triggers, one data pipeline
_Date: 2026-04-21_

**Decision:**
- Interactive sims (user clicking in Maestro, possibly bad/iterative results) → user explicitly marks "save to DB" per run.
- Batch sims (Python orchestrator driven) → auto-ingested.

Both feed the same JSON → DB pipeline. The collector SKILL doesn't know which is which — the trigger decision lives one layer up.

**Why:** Interactive iteration produces noise; batch runs are deliberate. Conflating them pollutes the slice history.

---

## #6 — Project identity: `.pvtproject` file with layered auto-detect
_Date: 2026-04-22_

**Decision:** Identify the project of a Virtuoso session via layered lookup:
1. Env var `PVT_PROJECT`
2. `.pvtproject` YAML file found by walking up from cwd
3. Fallback: interactive → first-save dialog; batch → hard error

`PvtInit(?project ...)` retained as a manual override for rare cases.

**Why:** Every-session declaration is ceremony. Layered auto-detect mirrors git's `.git/` pattern — set up once per project tree, never think about it again.

**Alternatives considered:** Auto-inferring project from cellView path — rejected; unreliable when libs cross projects.

---

## #7 — Three-tier run identity
_Date: 2026-04-22_

**Decision:** Every run is identified at three levels:
- `project_id` — from `.pvtproject`
- `testbench_id` — auto-captured as the active Maestro setup's cellView path (`lib/cell/view`); readable aliases allowed in `.pvtproject`
- `run_id` — auto-generated per dump

**Why:** One project often has multiple testbenches (e.g. heavy vs. lite) open in parallel Maestro windows of the same Virtuoso session. Without `testbench_id`, their data would collapse together in reports and queries.

---

## #8 — Circuit slice = simulated netlist + optional schematic screenshot
_Date: 2026-04-22_

**Decision:** Capture the simulated netlist (`input.scs` or equivalent) as the canonical record of "what circuit was simulated." Schematic screenshots are optional supplementary artifacts.

**Why:** The netlist is what Spectre actually ran — textual, diffable, deterministic, reproducible. It beats editor-state snapshots (can be dirty) and full OA library copies (too heavy, binary, poor diff).

**Alternatives considered:** Full library snapshots (overkill), cellView path + mtime only (breaks when schematic later changes).

---

## #9 — Evidence artifacts as first-class schema citizen; post-hoc attach allowed
_Date: 2026-04-22_

**Decision:** Each run can carry attached non-structured files (waveform PNGs, table screenshots, sim logs, user-uploaded images, PDFs). Stored in a proper `artifacts` table (`run_id`, `type`, `relative_path`, `description`, `source`). Files live on filesystem; DB stores paths, not blobs. Users can attach artifacts to a run days or weeks after the dump.

**Why:** Solves concrete pain — waveform plot/annotation (user pain 3.d), screenshot→OCR→Excel loop (3.f), cross-version visual diff (3.g). First-class schema enables automated report generation.

---

## #10 — Dual-source run notes
_Date: 2026-04-22_

**Decision:** A run carries notes from two sources:
- Maestro's per-test note (semantic — "what this test measures"), pulled automatically by the collector.
- User-written dump-time note (run-level — "what changed vs. last run").

Both stored side-by-side.

**Why:** Different temporalities. Test notes describe stable intent; dump notes describe session-specific context.

---

## #11 — Run vs. slice: label upgrades a run to a slice
_Date: 2026-04-21_

**Decision:** Every dump produces a `run` (auto `run_id`, timestamp, full data). A user-applied `label` upgrades the run to a `slice` — a stable anchor for cross-version comparison. Unlabeled runs are drafts, GC-eligible; slices are retained permanently.

**Why:** Not every run is review-worthy. Labels give the user explicit control over the permanent history, while all runs remain queryable in the short term.

---

## #12 — `PvtDumpToJson.il` is a throwaway POC
_Date: 2026-04-21_

**Decision:** `../MyRunner/PvtDumpToJson.il` proved the dump path is feasible but is not the foundation. Phase 1 writes a new collector from scratch.

**Why:** The POC doesn't match the final schema (three-tier IDs, artifacts, netlist capture, `.pvtproject` identity, etc.). Extending it would cost more than rewriting.

---

## #13 — `.pvtproject` is JSON, not YAML
_Date: 2026-04-22_

**Decision:** The project-identity file is strict JSON. Original plan in #6 specified YAML; that is superseded here. Both Python (stdlib `json`) and SKILL (one shared minimal parser, to be written when §2 SKILL-side work lands) read the same file.

**Why:** The deciding factor is **one format across Python and SKILL with zero external dependencies**. The red-zone target is offline-only (no pip-on-demand), and SKILL has no YAML/TOML library. Alternatives evaluated:
- **Vendor pyyaml**: solves Python only; SKILL still needs a YAML-subset parser or a stale-prone JSON mirror. Hidden two-source-of-truth cost.
- **Switch to TOML**: `tomllib` is stdlib-read in 3.11, but SKILL would need a from-scratch TOML parser for a format that appears nowhere else in simkit. No reuse.
- **Switch to JSON** (chosen): stdlib on Python; SKILL already needs a JSON helper for dump-related work, so the parser amortizes. Aligns with #2 (JSON for dumps). UX cost — no comments, strict quoting — is bounded because `.pvtproject` is small, flat, and rarely hand-edited. Mitigated by the reserved `_doc` key and `_`-prefix-ignored convention (see schema.md §1).

**Alternatives considered:** see options (a)/(b) above. Fallback if JSON's hand-edit UX becomes painful in practice: revisit option (a) vendor pyyaml, and commit to either a SKILL YAML-subset parser or an explicit Python-generated JSON mirror at that time.

**Supersedes / superseded by:** Amends #6 (file format only; layered-lookup and fallback-order decisions in #6 stand unchanged).

---

## #14 — Target classic SKILL ('il), not SKILL++; idiom traps to avoid
_Date: 2026-05-08_

**Decision:** All `skill/*.il` code in simkit targets **classic SKILL ('il mode)**, not SKILL++ (`'ils`). Classic SKILL is what loads cleanly from `.cdsinit` on every Cadence install we care about (ICADVM18.1-64b primary; older IC6.1.x as a should-still-work secondary). SKILL++ is a non-goal; do not introduce it.

**Why:** During §2.2 (SKILL `.pvtproject` loader) we discovered the worker had silently used SKILL++ idioms in classic SKILL files. Several "looked right" but failed at load time with cryptic errors, or — worse — failed at runtime in ways that produced *plausible-looking wrong results* (e.g. parens-off-by-one that made single-key JSON objects parse correctly while multi-key objects silently truncated). 12 distinct bugs in 1800 lines. Pinning the target language explicitly removes the ambiguity.

**Idiom traps** (the canonical list — consult before / during any SKILL coding):

1. **Symbol names cannot contain `:`.** `'pvt:ok` does not tokenize as a single symbol. Use `'pvt_ok` form. ROD's `:` syntax is sugar elsewhere; do not try to extend.
2. **Hex literals are `0xFF`, not `16#FF`.** The `radix#value` form is rejected by the reader.
3. **`(prog ((var init) ...) body)` is rejected.** Classic SKILL `prog` accepts only bare-symbol bindings; vars init to `nil`. Non-`nil` init values must use `(setq var initVal)` at the top of the prog body.
4. **`let*` is not available** — use nested `let`. (Verified absent on ICADVM18.1.)
5. **`defvar` overwrites already-bound variables** (unlike Common Lisp). Don't rely on idempotency. To pre-seed a value before `defvar` runs, use `setShellEnvVar` and have the file read it via `getShellEnvVar` inside the defvar body.
6. **`cons` requires its 2nd arg to be a list** — no dotted pairs. Use `(list a b)` for 2-tuples.
7. **`return` only escapes a `prog` form**, not a `procedure`. Every procedure body that uses `return` must be wrapped in `(prog () ...)`.
8. **`getCurrentTime` returns a string** (e.g. `"May 8 14:37:37 2026"`), not an integer. Don't format with `%d`.
9. **`sprintf` format strings:** `%X` (uppercase hex) is rejected — only `%x`. `%c` does not accept an integer arg — there is no public `intChar`/`charString` on this Cadence; for byte synthesis use a precomputed octal-escape LUT (see `_pvtJsonByteString` in `pvtJson.il`).
10. **JSON booleans / null map to sentinel symbols, not `t`/`nil`.** Classic SKILL conflates `t` / `nil` / empty-list / false; without sentinels, validators can't distinguish "JSON true" from "integer 1" or "missing key" from "explicit null". `pvtJson.il` exports `pvt_json_true`, `pvt_json_false`, `pvt_json_null`, `pvt_absent`.
11. **Built-in name shadowing risk.** `symbolToString` is a write-protected built-in; redefining it errors at load time. `prog`, `cons`, etc. are similarly protected. Always namespace-prefix helpers (`_pvtSymToStr` rather than `symbolToString`).
12. **Whole-file paren count being balanced is not enough.** A misplaced `)` inside a `cond` arm of a `while` body can cause the post-`while` cleanup form to be parsed as an iteration body, silently changing semantics. Balance-only linters will not catch this. Use skillbridge to evaluate forms incrementally and check observable outputs against the Python reference.

**How to verify before / during SKILL coding:** `../skill_tools/skillbridge/` is installed; the bridge is up at `/tmp/skill-server-default.sock` whenever Virtuoso is running on the dev host. Use Python with `skillbridge.Workspace.open()` to evaluate suspect forms (`ws['evalstring']('...')`) before relying on them. Skillbridge over CIW is the canonical local test runner for `skill/tests/` — see `skill/tests/README.md`.

**Alternatives considered:** Targeting SKILL++ (`'ils`) — rejected because (a) it would force a `.cdsinit` change on every deploy and (b) the parts of SKILL++ we'd want (init-list `let`, generic functions) are not load-bearing for any current simkit feature.

---

## #15 — JSON parser uses precomputed byte LUT; NUL not supported
_Date: 2026-05-08_

**Decision:** `skill/pvtJson.il`'s string-decoding path synthesizes UTF-8 bytes by indexing into a precomputed 255-entry octal-escape lookup table (`_PVT_BYTE_LUT`). Codes 1–255 are supported; **code 0 (NUL, U+0000) is not** — classic SKILL strings are NUL-terminated, so a ` ` in JSON would truncate the surrounding string.

**Why:** Classic SKILL has no documented integer-to-byte primitive (`%c` rejects integers; no `intChar` / `charString`). The natural alternative — a per-byte if/else cascade — is verbose and slow. A 255-byte LUT trades 255 bytes of memory for O(1) byte synthesis and clear code.

**Implications:**
- Safe for `.pvtproject`: spec forbids NUL; no field will ever contain it.
- Safe for almost all collector outputs: Maestro test names, corner names, signal names are user-typed identifiers, no NUL.
- **Risk surface:** if §3 collector or §4 ingester ever needs to round-trip an arbitrary user-supplied string (e.g. a free-form note pulled from Maestro that some user pasted binary into), it will silently truncate. Document this if it surfaces.
- **Escape hatch if needed:** rewrite the parser to keep strings as integer-vector representations internally and only stringify at the API boundary, with explicit NUL-rejection or NUL-replacement. Estimated cost: ~half a day; not worth doing speculatively.

**Alternatives considered:** Rejecting ` ` at parse time (cleaner but breaks RFC 8259 compliance — chose silent truncation as the practical accept-everything-shaped-like-JSON path until use cases prove otherwise).

---

## #16 — Result-DB slot accessors return funobj; must invoke via funcall
_Date: 2026-05-10_

**Decision:** When accessing slots on live ADE-XL result-DB objects (`axlrdb`, `axlrdbd`, `axlrdbo`, `axlrdbt`, `axlrdbc`) returned by `maeReadResDB` / `maeOpenResults`, slots that **produce objects** are exposed as funobj closures and MUST be invoked with `funcall`. Slots that **produce values** (string / number / symbol) are accessed bare. Code in `skill/pvtCollect.il` follows this rule (and `_pvtCollEvalThunk` does NOT defend against violating it — it merely masks the throw as nil).

**Why:** Discovered during Phase 1 §3 verification on 2026-05-10. Tier-1 unit tests passed 168/0/0, but driving `PvtSave` against a real Maestro session via skillbridge silently produced **0 result rows on a history with 7 tests**. Root cause: 9 sites in `pvtCollect.il` wrote `(rdb->point pid)` / `(rdb->tests)` / `(out->test)` / `(testObj->corner)` / `(cornerObj->params)` / `(tst->corner)` / `(pt->outputs ?type 'expr)`. Bare access returned funobj instead of the intended object/list; subsequent `foreach` or function-application then threw, but errset inside `_pvtCollEvalThunk` swallowed the throw into nil, so the function returned a well-shaped empty result. Lines fixed: 558, 579, 595, 619, 624, 631, 664, 696, 702.

**Slot inventory (verified empirically via `/tmp/probe_slots.il` against `sim_yusheng/Test/maestro:simkit_verify`):**

| Object | Slot | Returns | Correct form |
|---|---|---|---|
| `axlrdb`  | `point pid` | `axlrdbd` (point) | `(funcall (rdb->point) pid)` |
| `axlrdb`  | `tests` | list of `axlrdbt` | `(funcall (rdb->tests))` |
| `axlrdbd` | `outputs ?type 'expr` | list of `axlrdbo` | `(funcall (pt->outputs) ?type 'expr)` |
| `axlrdbo` | `cornerName` / `testName` / `name` / `value` | string / number | bare |
| `axlrdbo` | `test` | `axlrdbt` | `(funcall out->test)` |
| `axlrdbt` | `name` / `status` / `cornerName` / `pointID` | string / symbol / number | bare |
| `axlrdbt` | `corner` | `axlrdbc` | `(funcall tst->corner)` |
| `axlrdbc` | `params` | list of `(name value)` pairs | `(funcall cornerObj->params)` |

**Verification idiom (mandatory for new ADE-XL slot access):** probe both forms via skillbridge first; if bare returns `funobj@…`, you need `funcall`. The probe pattern is preserved in `/tmp/probe_slots.il` for reference (will be ported into `skill/tests/` once a Tier-2 harness exists).

**Why `_pvtCollEvalThunk` is not a defense:** the wrapper is what *masks* this class of bug. Any throw inside the wrapped lambda becomes nil, and 0 results looks identical to "no data found" at the next layer. The fix must be at the call site (use `funcall`), not at the thunk wrapper. Changing the wrapper to re-throw was considered and rejected — many legitimate ADE-XL calls return nil for benign reasons (no current history, empty test list), and we need to keep tolerating those.

**Implications:**
- This is the operational extension of trap-list #14, specific to `axlrdb*`. Consider it the canonical reference when writing any §3+ code that touches result DBs.
- **Tier-1 tests covered all pure helpers and that mattered** (they caught structural bugs). But Tier-1 cannot exercise live-session bindings. **Tier-2 verification on a real session is mandatory before any §3-style "collector" feature is declared done.** Add to Phase 1 §6 acceptance.
- After the fix, `PvtSave` on the verification history (7 tests, 49 outputs, 1 corner "TT") produced 42 ok rows + correct `corner_vars` (`temperature/model/VDD`) + correct `testbench_alias` resolution + valid JSON envelope.

**Alternatives considered:** Switching to function-style getters (`axlGetTests`, `maeGetTests`, ...) — rejected because (a) several don't exist on ICADVM18.1 (`maeGetTests` threw in our probe), and (b) the ones that do exist return different shapes (`axlGetTests hsdb` returned `(1022 ("Test"))`, a count + name list, not a list of test objects), so they're not drop-in replacements.

**Supersedes / superseded by:** Extends #14 (trap inventory).
