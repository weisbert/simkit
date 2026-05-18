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

---

## #17 — Ingester wraps the dump validator inline by default
_Date: 2026-05-11_

**Decision:** `simkit.ingest.ingest_run_json` runs `simkit.validate.validate_dump` immediately after JSON parse and before BEGIN. Any `severity="error"` violation raises `ValidationError` (subclass of `IngestError` so existing catchers still match). Warnings are logged via `logging.getLogger("simkit.ingest")` when `on_warning="log"` (default). The seam: `validate=False` on the API and `pvt ingest --no-validate` on the CLI disable the inline call. The validator stays independently invocable as `pvt validate <path>` and `python -m simkit.validate <path>`.

**Why:** Reconciles two planning positions. Plan-A (`docs/plans/§4_ingester.md` §10) wanted strict decoupling — ingester does shape checks only, validator runs separately. Plan-B (`docs/plans/§3_messy_data.md` §4.3) wanted the validator inlined so "loaded" implies "consistent." Without the inline call, the ingester surface and the validator surface drift apart silently; with the call always-on, the ingester pays for invariant checks every run (acceptable on Phase-1 row counts) and the validator's W1/W2 warnings become visible in the ingest log. The `--no-validate` flag preserves Plan-A's escape hatch for bulk-load scenarios where the dumps are known-clean.

**Alternatives considered:**
- Strict decoupling (Plan-A literal). Rejected: validator and ingester would diverge on every schema change.
- Validator owns the transaction boundary too (call validator inside ingester, have validator decide error vs warning). Rejected: blurs which module fails which way.

---

## #18 — `runs.netlist_path` permanently nullable in DuckDB
_Date: 2026-05-11_

**Decision:** The `runs.netlist_path` column in DuckDB is `VARCHAR` (nullable). `docs/schema.md` §3 still declares `VARCHAR NOT NULL`; this is a known deviation. The validator emits the `W2` warning whenever the field is null on ingest. Closing the spec/impl gap (tightening to NOT NULL) is gated on the §3 SKILL collector's Spectre-detection fix landing — at that point the collector will no longer emit `null` for spectre runs and the DDL can be tightened in the same diff.

**Why:** The May-10 §3 verification dump carries `"netlist_path": null` (the soft-miss path: `axlGetMainSetupDB`-based simulator probe heuristic returned `simulator nil is not Spectre`). Shipping an ingester that hard-rejects every existing collector dump because of the spec mismatch is dead on arrival; the W2 warning surfaces the gap without blocking the data path.

**Alternatives considered:**
- Tighten DDL to NOT NULL and force the collector fix first. Rejected as ordering — the ingester is the consumer of dumps, including pre-fix dumps already on disk; the spec change has to wait for the producer fix.
- Treat null as an error in the ingester. Rejected for the same reason.

---

## #19 — Internal `simkit_meta` table for DB-side schema bookkeeping
_Date: 2026-05-11_

**Decision:** `bootstrap()` creates a `simkit_meta(key VARCHAR PRIMARY KEY, value VARCHAR NOT NULL)` table and seeds it on first bootstrap with `('db_schema_version', '1')`. The table is **not** part of the public schema documented in `docs/schema.md` — it is internal bookkeeping for the data layer.

**Why:** Future schema changes (DDL evolution beyond v1) need a signal that distinguishes "fresh DB at v1" from "old DB at v0.x missing columns." Column-presence sniffing works for v1 but degrades with every DDL change. A one-row commitment now is cheap and load-bearing the first time DDL evolves.

**Alternatives considered:**
- Sniff column presence at bootstrap time. Rejected: scales poorly past 1 schema version and obscures the version intent.
- Store the version in `docs/schema.md` only. Rejected: that's documentation, not introspection — the DB can't self-describe.

---

## #20 — Per-file (per-run) ingest transactions
_Date: 2026-05-11_

**Decision:** `ingest_dump_dir` opens a fresh transaction per `run.json` instead of one transaction wrapping the whole walk. Combined with `--continue-on-error` (CLI) / `continue_on_error=True` (API), partial-success ingestion is supported: malformed dumps are surfaced and skipped, valid ones land.

**Why:** simkit is an offline single-user tool; a hostile concurrent reader is not a threat model. The cost of one transaction per run is amortized over the relatively small Phase-1 row counts (hundreds to low thousands per run). The benefit is that a 10-run walk where dump 5 is malformed loads 9 valid runs instead of rolling back the whole batch. Matches how `git fetch` handles per-remote failures.

**Alternatives considered:**
- One transaction across the entire walk. Rejected: a single malformed dump anywhere in a 100-run archive would force re-ingestion of the other 99, with no upside for a single-user tool.

---

## #21 — Drop DuckDB FK declarations on `results` / `artifacts`; application-layer integrity
_Date: 2026-05-11_

**Decision:** The `RESULTS_DDL` and `ARTIFACTS_DDL` constants in `python/simkit/schema_sql.py` do **not** declare `REFERENCES runs(run_id)`, even though `docs/schema.md` §3 describes the relationship. The ingester and validator enforce the integrity at the application layer (per-run transactional insert ordering; validator catches dangling references during audit).

**Why:** Surfaced during overnight implementation of §4. DuckDB enforces foreign keys per-statement and does **not** relax constraint checks for prior DELETEs within the same transaction — a documented DuckDB limitation, not a bug we can route around. The `on_conflict="replace"` flow deletes child rows (`artifacts`, `results`) then the parent row (`runs`) inside one transaction; the parent DELETE fails with `Constraint Error: ... still referenced by a foreign key in a different table` because the prior child DELETEs aren't yet visible. Two workarounds: (a) drop FK declarations and rely on application-layer integrity, or (b) split replace into two transactions (breaks atomicity). (a) is cleaner, matches DuckDB ETL idioms, and is consistent with what the ingester + validator already enforce.

The spec text in `docs/schema.md` §3 is left descriptive ("results.run_id references runs.run_id") because it documents the conceptual relationship even if DuckDB DDL doesn't carry the constraint. Source comments in `schema_sql.py` (after `RESULTS_DDL` and `ARTIFACTS_DDL`) document the deviation in-line.

**Alternatives considered:**
- Two-transaction replace. Rejected: replacing a run becomes non-atomic; a failure between the two transactions leaves the DB in a half-replaced state.
- Stick with FK and disallow `--force` replace. Rejected: the replace path is needed for the realistic "re-ingest after collector bugfix" scenario.
- Wait for DuckDB to grow MATCH SIMPLE / deferred constraints. Open issue upstream; not Phase-1 timeline.

**Implications:**
- `pvt validate --from-db` (deferred to §5) becomes more important — it's the application-side enforcement of what FK would have caught.
- Any future tool that writes directly to the DB without going through `ingest.py` MUST replicate the integrity checks. Document this in `python/README.md` when §5 CLI lands.

---

## #22 — Unify corner_vars failure markers on `_no_corner_vars` across all passes
_Date: 2026-05-11_

**Decision:** When `_pvtCollRowsFromTuples` cannot resolve `corner_vars` for a row, it emits the single marker key `_no_corner_vars` regardless of pass (1/2/3) or failure mode (cornerParamCache miss vs. cornerObj parse failure). Pre-fix the markers were mixed: pass 1/2 used `_parse_error` on parse failure and `_no_cache` on miss; pass 3 used `_no_cache` for both — three distinct keys for essentially one schema condition ("we didn't get corner_vars").

**Why:** Surfaced during §3 Step 4 Bug D implementation. TODO phrased the fix as "unify pass-3 marker on `_no_corner_vars`", but inspection showed the inconsistency spanned all three passes. Unifying only pass-3 would leave the cross-pass schema mixed. The validator's W1 invariant warns on any `"_"`-prefixed key in `corner_vars` and already documents `_no_corner_vars` as the canonical marker (per `python/simkit/validate.py` lines 18–19) — so the consumer side was already shaped for a single marker. The collector now matches.

**Implications:**
- The 42-row reference fixture (`tests/fixtures/runs/bdc13f17-…/run.json`) does not exercise any marker site (all corners converged), so the change is invisible to the byte-identical Tier-2 regression.
- Any downstream consumer that hand-checks for `_no_cache` or `_parse_error` keys must be updated. Within this repo, none do (only `validate.py` documents `_no_corner_vars`, and it warns generically on `_*`).

**Alternatives considered:**
- Strict literal reading of TODO ("only pass-3"). Rejected: leaves pass 1/2 still inconsistent and continues to require two-name handling downstream.
- Preserve distinct semantics (`_no_cache` = cache miss, `_parse_error` = parse failure, `_no_corner_vars` = either). Rejected: the consumer hierarchy doesn't act on the distinction; the only useful information is "corner_vars unavailable for this row". If a future use case needs the cause, a separate field can record it without polluting the corner_vars key namespace.

---

## #23 — Walker-level Tier-1 testing of `_pvtCollWalkRdb` deferred (partially closed 2026-05-12)
_Date: 2026-05-11; updated 2026-05-12_

**Decision:** `_pvtCollWalkRdb` does not have direct Tier-1 unit tests covering its live-rdb iteration paths (Section 2 pidList construction, Section 5 per-point walk). The pure `_pvtCollRowsFromTuples` shaper has full Tier-1 coverage (215+ tests). Bug B (walker pid set from `tst->pointID`) is verified solely by Tier-2 byte-identical regression on `simkit_verify` (a converged, contiguous-pid run) plus the shaper's existing gappy-pid Scenario E1 test which proves the shaper survives gap inputs.

**Update 2026-05-12 — partial closure via helper extraction.** The pidList build (Section 2) has been factored out of the walker into a pure helper, `_pvtCollBuildPidListFromTests(tstsForPids)`. The helper reads `tst->pointID` via `_pvtCollEvalThunk`, so a defstruct mock-tst (`pvtMockTst` in `testPvtCollect.il`) drives it directly without needing a live `rdb`. Nine new Tier-1 tests cover empty input, single pid, contiguous, **gappy (the Bug B witness)**, unsorted, duplicates, nil-pointID skip, all-nil, and a scrambled+gappy+dup+nil large case. SKILL Tier-1 grew 215 → 224.

What remains deferred: the **live-walker integration** test (driving `_pvtCollWalkRdb` end-to-end against synthetic data) is still blocked. The skillbridge probing run on 2026-05-12 confirmed `maeReadResDB` is write-protected in this Virtuoso build (`putd: function is write protected and cannot be redefined`), so a `putd` shadow of the live data-access APIs is not possible. `flet` is also unavailable in classic SKILL scope (it requires SCHEME). The remaining paths to full walker coverage are: (1) build a synthetic-rdb defstruct mock and refactor the walker to take the rdb as a parameter (medium-cost API change), or (2) wait for a real gappy-pid sim and pin it as a Tier-2 fixture.

**Why:** Surfaced during §3 Step 4 Bug B implementation. The walker calls slot-accessor funobjs (`rdb->point`, `rdb->tests`, `tst->pointID`, etc.) — see Decision #16. Constructing a SKILL mock-rdb whose accessors are funobj-bearing slots and behave identically to live axlrdb objects is non-trivial: it touches every classic-SKILL trap in #14 (struct/array discipline, funobj construction, `defstruct` access semantics). The estimated cost is 0.5–1 session of pure infrastructure work that buys exactly one test surface; the alternative is a real sim with gappy pids (zero infra cost, low probability of one being produced organically).

The 2026-05-12 helper extraction (above) buys the pidList-build coverage at the unit-test level without touching the live-walker mocking problem — at the cost of also not catching a future regression where the caller iterates the wrong data shape (since the helper itself is correct by test, and the call site is correct by Tier-2). The trade is judged acceptable; the bug surface "we iterate the wrong thing" is small (one line in the walker) and Tier-2 byte-equality catches it.

**Implications:**
- Bug B fix is verified by zero-regression on the happy path (Tier-2) plus inspection of the new pidList construction logic — NOT by direct exercise of the gappy-pid path. A future real sim with non-contiguous pids will validate the fix empirically; until then, the path is correct-by-reasoning, not correct-by-test.
- The `TODO.md` §3 messy-data section retains a flagged sub-item for "synthetic-rdb harness OR real gappy-pid sim" so the gap remains visible.

**Alternatives considered:**
- Build the synthetic-rdb harness now. Rejected: high infra cost for one test surface; the funobj-mocking sub-skill might not generalize beyond this case.
- Refactor the walker further to extract a pure pidList-from-tst-list helper. Possible but the helper would be trivial (one `foreach` + `sort`) and the bug is in *what data it iterates*, not in *how it deduplicates*. Refactor-for-test wouldn't move the test surface closer to the bug.
- Drive a real "kill spectre mid-sweep" sim from skillbridge. Deferred — better belongs to a Tier-2 scenarios doc (`skill/tests/tier2/scenarios.md`) when that file lands per TODO §3 messy-data (c).

---

## #24 — `pvt diff` slice resolution: exact label, then run_id prefix
_Date: 2026-05-12_

**Decision:** `pvt diff <slice_a> <slice_b>` resolves each identifier with a two-step lookup against `runs`: (1) exact match on `runs.label`; (2) if no label match, `run_id` prefix match. Each step must yield exactly one row. Zero matches → `SliceNotFoundError` (exit 1); two or more → `AmbiguousSliceError` (exit 1). Label match always wins over prefix even when the same string would also prefix-match a different run.

**Why:** Two ergonomics observations from §5 design:
1. Users name slices via `pvt label` precisely so they can refer to them by mnemonic later. Forcing them to type or copy run_ids for diff defeats the slice abstraction.
2. Run_ids are UUIDv4 hex; a 7-8 char prefix is almost always unique within a project. Falling back to prefix lets users diff arbitrary runs (including unlabeled drafts) without re-labeling.

**Implications:**
- A label that collides with a hex-only prefix string (e.g., the user labels a run `"abc1234"`) still resolves to the labeled run, not whatever else happened to start with `abc1234`. The label-first ordering makes this deterministic.
- Ambiguity surfaces as exit 1 with a message listing all matches; no silent "pick the newest" fallback.
- `resolve_slice` is a public helper (`simkit.diff.resolve_slice`) so future commands can reuse the same resolution rule.

---

## #25 — `pvt label` re-label policy: error without `--force`; `--clear` unconditional
_Date: 2026-05-12_

**Decision:** Setting `runs.label` over an existing non-null label without `--force` raises `LabelConflictError` (exit 1). With `--force`, the previous value is overwritten and the result carries `previous=<old>` for the CLI to surface in the success line. Clearing (`--clear`) sets `runs.label = NULL` unconditionally — no `--force` required — and is a noop (still exit 0) when the row was already null.

**Why:** Labels are the slice retention signal (DECISIONS #11). A silent overwrite would let a typo demote a known-good slice's identifier; making the user opt in with `--force` keeps the gesture visible. Clearing, by contrast, is symmetric with the implicit "draft" state every fresh run begins in — there is no information loss the user could be surprised by.

**Implications:**
- `pvt label X new --force` succeeds against both null and non-null prior states; the user does not have to know in advance which case they're in. The CLI distinguishes the two via `action='set'` vs `'overwritten'` in the success line.
- Repeated `pvt label X --clear` is safe — it never errors, never overwrites a non-null label by accident.
- Empty / whitespace-only / multiline labels are rejected at the validation layer (`_validate_label`) regardless of `--force`.

---

## #26 — DuckDB TIMESTAMPTZ ↔ Python: CAST to VARCHAR + ISO normalisation
_Date: 2026-05-12_

**Decision:** When reading TIMESTAMPTZ columns from DuckDB in §5 query code, `CAST(col AS VARCHAR)` in SQL and re-normalise to strict ISO 8601 in Python via `datetime.fromisoformat(s.replace(' ', 'T', 1)).isoformat()`. Applied in `simkit.list_runs` (the list query) and `simkit.from_db` (run / artifact reconstruction). The ingester continues to send TIMESTAMPTZ-castable ISO strings on write.

**Why:** Two compounding constraints surfaced during §5:
1. DuckDB's Python binding routes TIMESTAMPTZ → Python `datetime` conversion through the `pytz` module. The deployment target is offline stdlib-only (no pip wheels); reading any TIMESTAMPTZ via `fetchall()` triggers `ModuleNotFoundError: No module named 'pytz'` and the query fails.
2. DuckDB's `CAST(... AS VARCHAR)` emits `YYYY-MM-DD HH:MM:SS±hh` (space separator, hour-only offset). The validator's I6 invariant accepts only `YYYY-MM-DDTHH:MM:SS±hh:mm`, so a raw round-trip through the DB would fail validation when `pvt validate --from-db` is the consumer.

The CAST-and-normalise pattern threads both needles: SQL-side avoids the pytz path, Python-side restores the strict form. `datetime.fromisoformat` is permissive in 3.11+ (matches stated minimum) and `isoformat()` emits the canonical `T` + `±HH:MM`.

**Implications:**
- Any new §5/§6 query touching `timestamp` / `ingested_at` / `created_at` must follow the same pattern; raw `SELECT timestamp` will start failing again. Helper `_normalize_iso` in `simkit.from_db` is reusable.
- Sub-second precision is preserved (`fromisoformat` reads `.ffffff`); locale-specific DuckDB rendering (e.g. `+08` vs `-04:30`) is normalised to `±HH:MM` in both cases.
- The DDL writes TIMESTAMPTZ on the ingest path with strings shaped `YYYY-MM-DDTHH:MM:SS±HH:MM` already, so no migration is needed.

**Alternatives considered:**
- Ship `pytz` as a vendored module in the offline bundle. Rejected: adds a transitive dependency surface for one decoding behaviour we control entirely on the read side.
- Change the column type to `VARCHAR` and drop TIMESTAMPTZ. Rejected: would forfeit DuckDB's `EPOCH(...)` and time arithmetic for any future query layer; the CAST cost is constant per row and only paid in §5 listing/reconstruction paths.

---

## #27 — Netlist Spectre detection: file presence is the probe
_Date: 2026-05-12_

**Decision:** `_pvtCollCopyNetlist` no longer probes the simulator type via `asiGetAnalogSimulator` (or any other API). It resolves `axlGetPointNetlistDir(histID, testName)` and then tests `(isFile "<dir>/input.scs")`. If the file exists, the simulator was Spectre and we copy it. If not, the run was non-Spectre (or the netlist wasn't emitted yet), and we soft-miss with a clear warning listing the path that was checked.

**Why:** The pre-fix probe `(asiGetAnalogSimulator sess)` was the right API for ADE-L but wrong for Maestro / ADE-XL: `asi*` is a generic that dispatches on a *tool handle*, and a Maestro session is not a tool. The call hit the no-dispatch path and returned nil on every spectre run, tripping the "simulator nil is not Spectre" warning. A 2026-05-12 skillbridge probing run against `fnxSession0` exhaustively searched the `axl* / asi* / fnx* / mmi* / ade*` namespaces for a Maestro-aware simulator getter and found none — the only test-handle-aware accessors that exist (`axlGetEnabledTests`, `axlGetVars`, `axlGetVar`) don't carry simulator info either.

Rather than wrestle with an absent API, the fix flips the gate: `input.scs` is **the** Spectre netlist filename in the per-point netlist dir; HSPICE writes `input.cir`, Verilog-A simulators emit `.va`, etc. The pre-existing `(isFile srcPath)` check at step 4 was already doing the actual work — the broken simulator probe at step 2 was just a redundant pre-filter. Deleting it collapses the simulator detection and the file presence test into one step, with a more informative warning.

**Implications:**
- Tier-2 verified on the live `simkit_verify` session 2026-05-12: PvtSave now emits `run.netlist_path="input.scs"` (was null on the 2026-05-10 reference fixture) and copies the 2938-byte `input.scs` into the run dir. `pvt validate` exits 0 (W2 warning gone); `pvt ingest` populates `runs.netlist_path` non-null. 42-row count + first-row value (`Rtime_clkout=2.13452e-11`) match the 2026-05-10 reference, so no other behaviour shifted.
- The validator's `W2` warning ("netlist_path is null — collector soft-miss") and the schema-vs-impl gap noted in DECISIONS #18 are now reachable only when the simulator is genuinely non-Spectre (HSPICE etc.) or when the netlist dir is missing — the bug-free outcome instead of a false alarm.
- The signature of `_pvtCollCopyNetlist` is unchanged; callers don't need to update. The `sess` parameter is now unused but kept on the prototype to preserve the call-site (no semantic change).
- No new Tier-1 coverage: the function only calls live axl/ipc APIs that aren't unit-testable without skillbridge; Tier-2 byte-equal regression on `simkit_verify` is the durable witness.

**Alternatives considered:**
- Keep the explicit probe but find the correct Maestro API. Rejected after the namespace probe came up empty; the cost (further probing across Cadence release-specific APIs) outweighs the marginal benefit of having a second confirmation alongside file presence.
- Parameterise by simulator (look for `input.scs`, fall back to `input.cir`, etc.). Rejected for v1 — non-Spectre simulators are out of scope per `docs/schema.md` §2.1 (`netlist_path` doc strings refer specifically to Spectre's `input.scs`). When a non-Spectre use case arrives we can extend the file list and bump `schema_version`.

---

## #28 — First-save dialog v1 scope; `?unmapAfterCB t` + `hiSetCallbackStatus` pattern
_Date: 2026-05-12_

**Decision:** §2.2 first-save dialog (`skill/pvtProjectDialog.il`) ships in two parts. (1) v1 scope is **four fields, all in one modal `hiCreateAppForm`**: Project name (required), DB root (optional, blank → `./simkit_data`), Author (optional, blank omits the JSON key entirely), Save target path (optional+editable, blank → `<cwd>/.pvtproject`). The testbench-alias checkbox in the original plan mockup is deferred to v1.1 — it required an additional `axl*` session probe + conditional widget on top of the four primary fields, none of which is load-bearing for the "no `.pvtproject` → one click, file written" main path. (2) Validation-fail UX = form stays open: `hiCreateAppForm ?unmapAfterCB t` combined with `(hiSetCallbackStatus form nil)` inside the OK callback whenever any validator rejects. CIW `warn` carries the per-field error message; default callback status (`t`) is preserved on success so the form unmaps normally.

**Why:**
- IC user pushed for minimum-viable v1: "if I can hand-edit `.pvtproject` to add an alias later, the dialog can stay simple now." Pre-empting alias UI in v1 saves one widget, one conditional, one validation arm — directly proportional to the bug surface.
- The `?unmapAfterCB t` pattern is documented at skuiref.pdf p.506 (`hiCreateAppForm`) and p.801 (`hiSetCallbackStatus`). Default `?unmapAfterCB nil` would close the form before the callback ran, forcing a re-spawn on every validation failure (user retypes everything). The chosen pattern keeps the user's partially-correct input on screen and is the canonical Cadence idiom for "form with content validation."
- Tier-1 covers all pure layers (defaults, validators, JSON build/write, round-trip). Tier-2 (`skill/tests/tier2/scenarios.md`) covers the 5 UI-only scenarios: Happy / Cancel / Validation feedback / Re-entrancy / Headless suppression. The sandbox lives at `/home/yusheng/cadence_work/dialog_sandbox/` — outside any `.pvtproject` walking-up chain.

**Implications:**
- `pvtLoadPvtProject` (existing) automatically picks up the dialog the moment `pvtProjectDialog.il` is loaded — the `boundp` gate flips and the `?allowDialog t` default fires the dialog when the walker comes up empty. Batch / scripted callers still pass `?allowDialog nil` to opt out.
- Failure modes: round-trip self-check (`_pvtDlgWriteFile` calls `pvtParsePvtProject` after write) means a corrupt `.pvtproject` is auto-deleted; the loader's hard-fail path sees "no file found" rather than a confusing "your just-written file is invalid".
- `_pvtTestProbeSession` is impure and can't be Tier-1-tested, but `_pvtDlgDeriveDefaults` takes the session tuple as a parameter, so the entire defaults logic is exercised in Tier-1 with synthetic `("LIB" "cell" "view")` inputs.

**Idiom traps caught while implementing — additions to #14's list:**

13. **`(procedure (name ()) ...)` and `(procedure (name (arg)) ...)` are wrong.** Classic SKILL reads the inner parens as an arg-name list. Zero-arg procedures use `(procedure (name) ...)` and one-arg procedures use `(procedure (name arg) ...)`. The `(procedure (name (arg "r")) ...)` form is valid but means "arg with type-spec", not a workaround for plain args. Skuiref p.703's `procedure ( myCB ( form "r" ) ...)` example is the canonical "with type" shape.
14. **`?okButtonText` does not exist on `hiCreateAppForm`.** The signature on skuiref.pdf p.502 has no such keyword. The button labels for the `?buttonLayout 'OKCancel` set are fixed at "OK" / "Cancel" / etc. Renaming OK to "Save" requires custom buttons via the `'(s_buttonLayout (s_customButtonText s_customButtonCB) ...)` shape (p.508), which is more work than v1 needed.
15. **`(boundp 'sym)` is false for procedures.** Classic SKILL's `boundp` only inspects the value cell; `(procedure (foo) ...)` writes to the function cell, which `boundp` cannot see. Loading a file full of procedures does NOT flip `(boundp 'someProcInside)` to t. The right presence check for an optionally-loaded entry point is `(getd 'sym)` — returns the function object (truthy) or nil. **This was a live pre-existing bug** in `pvtProject.il`'s step-3 dialog gate: the boundp check meant the gate never fired even after `pvtProjectDialog.il` was loaded. Caught during Tier-2 smoke (the gate flipped to `yes` after switching to `getd`, and the form then actually popped).

**Alternatives considered:**
- Full plan B scope (4 fields + alias checkbox + alias name). Rejected as v1: extra widget, conditional rendering on session probe success, second-keyed `testbench_aliases` table in the JSON-write step. None of it changes whether the main path works; all of it grows the surface to maintain.
- `?unmapAfterCB nil` (default) + show validation error via a sub-dialog (`hiDisplayAppDBox`). Rejected: nests a modal inside a modal, easy to get re-entrancy wrong, and forces the user to redo all four fields on each correction.

---

## #29 — Phase 2 data model recovered via skillbridge: corners have two parallel sweepable axes (vars + models)
_Date: 2026-05-12_

**Decision:** The Phase 2 PVT-union sidecar mirrors Maestro's native corner-table model rather than inventing one. The native model has two parallel axes per corner row:
1. **vars axis** — corner-scoped design variables; accessed via `axlGetVars(cornerHandle)` → `axlGetVar(corner, name)` → `axlGetVarValue(handle)`; written via `axlPutVar(corner, name, valueOrSweep)`.
2. **models axis** — corner-scoped model selections (file/section/block/test); accessed via `axlGetModels(corner)` → `axlGetModel(corner, fileBasename)` → `axlGetModelFile / axlGetModelSection / axlGetModelBlock / axlGetModelTest`; written via `axlPutModel(corner, fileBasename)` + `axlSetModelSection / axlSetModelBlock / axlSetModelTest`.

Sweep encoding is identical on both axes: a space-separated string. Model-section sweeps additionally wrap each section in `"..."` (Maestro convention; observed as `'"tt" "ss" "ff"'` on the live TT_pvt corner-group).

**Why:** Initial spec draft tried to express all sweeps via the vars axis alone — discovered during live skillbridge probe of `simkit_verify` (fnxSession0) that `axlGetVars(TT_pvt)` returns only `[temperature, VDD]` whereas the exploded TT_pvt_0..5 sub-corners each have a distinct model section (`ff`/`ss`/`tt`). Followed the trail through `axlGetModels` → `axlGetModelSection` and found the section sweep stored as `'"tt" "ss" "ff"'` on the model handle, not as a corner var. Without this distinction Phase 2 cannot express the process-corner axis of any PVT union — which is half of the motivating VCO LO case.

The "mirror Maestro" decision (don't invent) is load-bearing: it lets push/pull be a near-trivial walk of the SDB, makes the round-trip fidelity contract realistic, and avoids a translation layer between "our model" and "Maestro's model" that would inevitably leak.

**Implications:**
- The sidecar JSON has `vars: {name: scalar|array}` and `models: [{file, block, test, section}]` per row; section is the sweepable model field for v1, block/test default to `"Global"`/`"All"` (MTS-mode out of scope).
- Section names are stored *unquoted* in JSON (`"tt"`); the loader handles Maestro's `"..."` wrap/unwrap on push/pull.
- Sub-corner explode count is the product of sweep lengths across BOTH axes (e.g. simkit_verify TT_pvt: 2 VDD × 3 sections = 6 sub-corners).

**Alternatives considered:**
- Single-axis (vars-only) sidecar. Rejected after probe: cannot express process-corner axis.
- Synthetic `corModelSpec` field in the sidecar mirroring `axlGetCornersForATest` output. Rejected: that field is Maestro's *output* (a merged view per-test); we should write the *inputs* (vars + models entries), not the merged outputs.

---

## #30 — Phase 2 explode-order rule: alphabetic-by-field-key, lex-sorted-values; sub-corner names `<row>_<i>`
_Date: 2026-05-12_

**Decision:** When a union row explodes into sub-corners, the field-ordering for index assignment is **alphabetic by field key**, where the key is the var name for vars-axis fields and `model[k].section` (with `k` being the model entry index) for models-axis fields. The alphabetically-first key is the innermost (fastest-changing) loop. Values within a sweep are **lexicographically sorted ascending** before index assignment — the JSON array order is *not* load-bearing for sub-corner indexing (it round-trips through Maestro's storage but Maestro re-sorts on explode). Sub-corner naming is `<row_name>_<i>` (0-indexed), inheriting `row_name` directly when the row has zero sweep fields (no `_0` suffix).

**Why:** Observed empirically on `simkit_verify` TT_pvt: VDD was declared as `"3 2.8"` but the exploded TT_pvt_0..5 sub-corners assigned VDD=2.8 to even indices and VDD=3 to odd indices — i.e. Maestro sorted `[2.8, 3]` before indexing. Section likewise sorted `[ff, ss, tt]` for the outer loop. VDD-inner vs section-outer also follows alphabetic-by-name (`V` < `m` in ASCII, treating `model[0].section` as a composite key). Phase 2 adopts this rule as a hard contract so the round-trip works.

**Implications:**
- Numeric-string sweeps (`["3", "10", "12"]`) sort lexicographically: `["10", "12", "3"]`. Author-side mitigation: consistent leading-zero formatting (`["03", "10", "12"]`). Flagged as a caveat in spec §3.4 — does not block v1 but may surface in domain feedback once `pvt corners explode` is in daily use.
- Sub-corner names are deterministic given the union content — round-trip pull will reproduce the same names Maestro shows in the panel.
- Open Decision 8.6 in the spec hedges this against the VCO LO case: more complex sweep sets (process × ind-temp × CT) may reveal that Maestro's rule diverges at scale. Mitigation: Gate U1 (round-trip on simkit_verify) plus a focused Tier-2 probe on VCO LO before Phase 2 §2 lands.

**Alternatives considered:**
- Preserve declaration order through explode. Rejected: Maestro re-sorts at explode time; we cannot force a different sub-corner numbering without monkey-patching Maestro.
- Custom user-supplied ordering via a `sweep_order` field. Rejected for v1: adds surface area for the single observed case where it might matter (numeric strings), and the leading-zero workaround is good enough.

---

## #31 — Phase 2 v1 scope freeze: no templating, no `axlSetParameter`, no MTS-mode model fields
_Date: 2026-05-12_

**Decision:** Phase 2 v1 spec is intentionally narrow. Excluded from the sidecar:
- **Inheritance / templating between rows** (no `extends`, no "fill from parent"). Each row is self-contained.
- **Device-parameter overrides via `axlSetParameter`** (paths like `Library/Cell/View/Instance/Property`). The VCO LO case may need these for per-corner instance overrides — flagged as Open Decision 8.4, blocking Phase 2 §2 only if confirmed required.
- **Per-corner model `block` / `test` fields** (MTS-mode — multi-test setup). Stay at defaults `"Global"` / `"All"`.
- **Reliability / Monte Carlo configuration.**
- **Sweep direction declaration** (Maestro's "from-low / from-high" is a sim-time choice).

**Why:** Phase 1 §1 was deliberately narrow ("five things" list at top of `docs/schema.md`) and stayed shippable. Phase 2 v1 keeps the same discipline. The vars + models.section pair covers the observed simkit_verify shape and is enough to verify the round-trip pipeline end-to-end on a real Maestro session. Once that loop is in daily use, v1.1 / v2 expansions can stack on top with concrete user pain pulling them in.

**Implications:**
- Open Decision 8.4 must be answered (probably "no" for v1, but contingent on inspecting the real VCO LO setup) before §2 loader lands. If "yes", that bumps v1 scope by one more axis.
- Templating is genuinely useful for the 21-col VCO LO case (most rows share temperature, differ in process+CT); we accept the verbosity cost in v1 in exchange for a simpler loader. If repetition becomes painful, v1.1 introduces a `defaults` block at the union level.
- Forward-compat: the unknown-key policy from Phase 1 (warn, don't error) applies to Phase 2 sidecars too. Newer templates carrying v1.1 fields won't crash a v1 loader.

**Alternatives considered:**
- Ship everything from VCO LO in v1: includes templating + `axlSetParameter`. Rejected as scope creep; first-deploy needs to fit in a few weeks.
- Defer Phase 2 §1 freeze until VCO LO probed. Rejected: simkit_verify is rich enough to land §1, and the VCO LO case is the §6 acceptance target — we'd be blocking ourselves on the test before writing the code.

---

## #32 — Classic SKILL: use named arithmetic / comparison functions, NOT operator shorthand
_Date: 2026-05-12_

**Decision:** Classic SKILL code in `skill/*.il` uses the **named-function** forms for arithmetic and comparison:
- `plus` / `difference` / `times` / `quotient` (not `+` / `-` / `*` / `/`)
- `lessp` / `greaterp` / `leqp` / `geqp` (not `<` / `>` / `<=` / `>=`)

Operator shorthand may parse as a symbol token, but when invoked in a prefix call like `(<= i n)` or `(+ a b)` after a complex chain of file loads (e.g. as `pvtCorners.il` was loaded last in the `runTests.il` chain after pvtError / pvtJson / pvtProject / pvtProjectDialog / pvtCollect), the SKILL reader rejects it as a syntax error at the operator position. The first `pvtCorners.il` draft used `<=` / `+` / `-` freely; Tier-1 / Tier-2 verification on 2026-05-12 failed with "*Error* load: error while loading file at line N" where line N was the operator-using form. Five distinct call sites had to be migrated to named functions before the file would load.

**Why:** Phase 1's `pvtJson.il`, `pvtProject.il`, `pvtCollect.il` already used the named-function forms throughout — that style was already the project convention; the discovery on 2026-05-12 was that it's NOT just stylistic but **required** for classic SKILL prefix calls in some contexts. (The exact reader / evaluator interaction that produces the syntax error wasn't tracked down further; switching to the named forms makes the issue moot.)

**Implications:**
- Augments the DECISIONS #14 idiom-trap list as trap #16 (counting traps 1-12 from the original list, 13-15 from DECISIONS #28). A formal rewrite of the consolidated list could fold them into one canonical place; for now they accumulate by date.
- New SKILL files should grep their own contents for ` [+\-*/=] ` (operator shorthand) before declaring done.
- `pvtCorners.il` and any future SKILL module follows this rule; existing `pvtCollect.il` / `pvtJson.il` / `pvtProject.il` already comply.

**Alternatives considered:**
- Tracking down the exact reader state that rejects operators — rejected as low-value given the simple workaround.
- Configuring SKILL to accept the shorthand — no documented switch.

---

## #33 — Phase 2 §3 SKILL pull side verified end-to-end against `fnxSession0`
_Date: 2026-05-12_

**Decision:** `pvtCornersPull` is the canonical pull entry point for Phase 2 §3. Tier-1: 30 new pure-helper cases pass (suite 256 → 286 / 1 / 0; the 1 baseline FAIL is the Maestro-open no-session test). Tier-2: live pull from `fnxSession0` (simkit_verify, 2 corner rows → 7 sub-corners after explode) produces a sidecar that Python `simkit.union.load_union` + `explode` reads cleanly and exposes the spec §9 7-sub-corner table.

**Why:** Closes the §3.V verification debt flagged in commit `e5f9a8f`. End-to-end verification path:
1. SKILL `pvtCornersPull` reads live SDB → emits JSON to disk
2. Python `load_union` parses the file → typed `Union` dataclass
3. `explode` materialises sub-corners

All three layers in one round-trip; the Python step would have rejected any malformed JSON, and the explode would have produced wrong sub-corner counts if the vars/models axes hadn't been faithfully captured. **The Phase 2 data model (DECISIONS #29) survives a real live-Maestro round-trip.**

During verification, four SKILL bugs were caught and fixed:
1. Argument order on `pvtJsonEmitToPort` — the proc takes `(value port)` but the call had `(port value)`. Symptom: `fprintf: argument #1 should be an I/O port — got table:pvtUnionEnvelope`.
2-5. Operator shorthand → named function (DECISIONS #32 above): `<=` × 2 → `leqp`, `<` → `lessp`, `+` × 4 → `plus`, `-` × 4 → `difference`.

**Implications:**
- §3 push side (`pvtCornersPush`) can land next. Its design mirrors pull's axl-walk pattern; same idiom traps apply.
- `pvt corners pull` CLI subcommand (deferred from Stage E) can be wired now that the SKILL backend is verified.
- §6 Gate U1 (round-trip fidelity on simkit_verify) is implicitly proven by this probe; just needs a formal pytest wrapper that re-runs the round-trip and asserts byte-equality.

**Alternatives considered:** Defer verification to push side (since push subsumes pull's surface). Rejected — pull-only is testable on the LIVE session without state mutation, whereas push must use a sandbox session. Pull-first is the right ordering.




---

## #34 — Phase 2 §3 SKILL push side verified end-to-end against fnxSession0
_Date: 2026-05-12_

**Decision:** `pvtCornersPush` ships as the push-direction counterpart to `pvtCornersPull`. Together they close the `.union.json` ↔ live ADE-XL round-trip. Tier-1: 30 new pure-helper cases pass (suite 256 → 300 / 0); Tier-2: pull-push-pull round-trip on `fnxSession0` (3 corners: TT scalar / TT_pvt with VDD-sweep + section-sweep / TT_2p5G with 3 scalar vars) is **semantically byte-identical** modulo the per-pull `name` field (which is derived from output filename basename).

**Why:** Closes Phase 2 §3 entirely. The Tier-2 round-trip verifies that both axes (vars + models) emit/round-trip correctly:
- vars: `axlPutVar(c, name, "3 2.8")` for sweep, `(c, name, "3")` for scalar — round-trips via space-separated string storage.
- models.section: `axlSetModelSection(m, '"tt" "ss" "ff"')` for sweep, `'"tt"'` for scalar — round-trips via Maestro's quoted-each-token convention (DECISIONS #29).

**Implications:**
- §6 Gate U1 (round-trip fidelity on simkit_verify) is **manually verified** by this commit (`263adb0`). Pin as an offline pytest once a pre/post fixture pair is captured for regression — open follow-up.
- `pvt corners push` and `pvt corners pull` Python CLI subcommands can now wrap the SKILL functions safely. Currently deferred from Stage E pending the §6 Gate U1 formal pinning + a sandbox-session strategy for CI.
- Idempotent push contract holds: pushing IDENTICAL content to an already-populated session leaves the SDB content semantically unchanged. Maestro will mark the session dirty during the write (axlPutVar rewrites internally), but semantic content is preserved.

**Open caveats for v1.1:**
- Length-1 sweep arrays collapse to scalar on a push -> pull cycle (Maestro storage does not distinguish a 1-element sweep from a scalar). Documented as fidelity-modulus item (iv) in spec §4.2.
- Single sandbox session for verification is hard to come by without restarting Maestro. The 2026-05-12 verification used `fnxSession0` directly with IDENTICAL-content push (user-approved, since identical push leaves content unchanged).

**Caught-and-fixed during verification:**
- One `let*` use in `_pvtCornersPushRow` (DECISIONS #14 trap #4 — classic SKILL has no `let*`). Fixed to nested `let`. Three test cases used `let*` too; same fix.

**Alternatives considered:** Push to a fresh sandbox session created via `axlCreateSession` and the existing test bench template. Rejected for v1 — overhead of session creation outweighs verification value once IDENTICAL-content push is shown safe.


---

## #35 — `eval_err` per-output sentinel: pass-1 emits a row when the rdb value is unshapable
_Date: 2026-05-12_

**Decision:** When `_pvtCollRowsFromTuples` pass-1 enumerates an output whose rdb-side value is neither a number nor a non-`"wave"` string (typically a list like `(eval_err "Rtime_clkout: error")` when the measurement expression failed eval on a specific corner), the collector now emits a row with the **real output name preserved**, `value=null`, and `status="eval_err"`. The row also marks `writtenSet`/`writtenByTest` so the triple-level pass-3 (`no_convergence`) and pass-2 (`failed`/`running`) sentinels do not double-fire.

`VALID_STATUSES` grows to `{ok, failed, running, no_convergence, eval_err}`. The validator treats `eval_err` as a **data row** for triple-coverage purposes (I1) alongside `ok`; it shares the "value must be null" rule with the triple-level sentinels (I14) and additionally enforces "output must not be `__sim_status__`" (since eval_err is per-output, not triple-level).

**Why:** Discovered by probing the user-pre-staged `simkit_Rtime_err` history on 2026-05-12 (one corner's `Rtime_clkout` expression errored on eval; rest of the test's outputs computed normally). The pre-fix collector silently dropped that output's row — pass-1 rejected it (value was a list, not numberp/stringp), pass-2 didn't fire (`tst->status='done`), and pass-3's `(cname,pid)` key was already marked written by the other ok rows in the same triple. The validator didn't catch it either: I1 only checks `(point, corner, test)` triple-coverage, and the triple had ok rows → satisfied.

This was the exact "per-output convergence inside a converged test" scenario flagged in TODO §3 (last bullet of the messy-data list) as "**Untested**". The new histories made it testable; the bug was real.

**Why the fix is per-output, not via a coverage-set oracle:**
- The walker already enumerates the eval-err output (it's in `pt->outputs`, with value=list). Pass-1 was *seeing* it; the bug was in the shape-then-drop logic.
- Emitting a per-output `eval_err` row preserves the output name, lets I1 see per-output coverage without a separate "expected output set" oracle, and avoids a schema-level concept of "expected outputs per (test, corner)" that Maestro doesn't reify.
- Triple-level sentinels (`__sim_status__` with `failed`/`running`/`no_convergence`) are unchanged. Pass-2 / pass-3 still own the "entire test/triple is broken" path.

**Implications:**
- Schema v1 contract grows by one status value but no field. Forward-compat: ingester / DB / CLI handle the new status automatically because status is a string column (no enum constraint at the DuckDB layer).
- Tier-1 SKILL: +3 cases (`collect/rows-list-value-emits-eval-err`, `collect/rows-nil-value-emits-eval-err`, `collect/rows-eval-err-coexists-with-ok-in-same-triple`). Cumulative 300 → 313 / 1 baseline FAIL.
- Tier-1 Python: +7 cases in `EvalErrTests` (status enum acceptance, I14 value-null + output-not-sentinel, I1 data-row treatment, I1 mutual-exclusion with triple sentinels). Cumulative 331 → 338 / 0.
- Tier-2: `simkit_Rtime_err` PvtSave now yields 42 rows (was 41) — 41 ok + 1 eval_err for `(test='Test', corner='TT_2p5G', point=1, output='Rtime_clkout', status='eval_err')`. Validator: 0 errors, 0 warnings. simkit_simerr is unchanged (pass-2 territory only).

**Alternatives considered:**
- Adding a coverage-set invariant ("every (test, corner) must cover the same output set"). Rejected — the user can intentionally vary outputs per corner (different bench wiring); a hard invariant would over-constrain.
- Mapping the eval-err row to a triple-level `__sim_status__` sentinel with a synthetic encoded output name. Rejected as clunky and violates the I1 contract that says one sentinel per triple.
- Surfacing the raw eval-err message (the list payload often carries diagnostic text). Rejected for v1: the value field is null; the diagnostic stays in Maestro's results log. Future v1.1 could add a `error_message` optional field to eval_err rows if there's demand.

**Open: column constraint at the DB layer.** DuckDB `results.status` is currently a free string per DECISIONS #21. If a future schema bump introduces a constraint, it must include `eval_err`. Not blocking v1.


---

## #36 — Phase 2 §6 Gate U2 verified via synthesised 21×3 VCO shape
_Date: 2026-05-13_

**Decision:** Gate U2 (Phase 2 §6 VCO LO acceptance) is verified end-to-end via a synthesised 21-row × 3-pt union shape pushed into the live `fnxSession0`. User did not have an actual VCO LO setup loaded on the test machine; per their request, I built the shape from the PHASE_PLAN.md / DECISIONS #29 description (7 process corners × 3 inductor-temperature bins = 21 corner-table rows; each row has a 3-value temperature sweep = 63 total sub-corners) as `tests/fixtures/unions/vco_lo_21x3.union.json`.

**Push result**: session went 3 → 24 corner rows. All 21 pushed rows pull back byte-identical for `vars` + `models`. Zero mismatches, zero missing. Gate U2 PASS at the round-trip level.

**Why synthesised, not actual VCO LO:** Acceptance-gate purpose is to verify the toolchain handles the 21-row scale. Whether the rows correspond to a real circuit or a synthetic name set doesn't change what the SKILL push / pull / explode logic exercises — they walk the same axl* APIs regardless. Synthetic fixture also lets the offline pytest portion (`TestGateU2VCOLoAcceptance`, 5 cases) run in CI without a live session.

**Implications:**
- `tests/fixtures/unions/vco_lo_21x3.union.json` is the permanent Gate U2 reference. Future regressions on the explode order or row-shape would surface here.
- The pushed rows persist in `fnxSession0` until the user manually removes them (push is additive; no auto-cleanup). Acceptable per user's explicit request to "add them yourself".
- Open Decision 8.6 (alphabetic-by-field-key explode order at scale) is **NOT fully validated** by this gate — each row sweeps only ONE field (`temperature`), so the multi-axis interaction case (e.g., temperature + VDD + section all sweeping inside one row) remains untested at scale. The Gate U3 synthetic 2×3×5 covers it at small scale (3 sweep fields per row); a future real bench with multi-axis sweeps inside one corner-group would close 8.6.
- Caveat surfaced (already documented in spec §3.4): `temperature` sweep `["-40", "25", "105"]` explodes in **lex order** = `["-40", "105", "25"]`, so sub-corner indexing is NOT numerically intuitive (sub_0 = -40, sub_1 = 105, sub_2 = 25). User-side workaround is consistent leading-zero formatting like `["m40", "025", "105"]` or numeric prefixes; tool-side fix would require a per-field "sort key" override which is not in v1 scope.

**Alternatives considered:**
- Wait until user has actual VCO LO loaded. Rejected: the synthesised shape exercises the same code paths and acts as a permanent regression fixture for the 21-row scale.
- Push to a sandbox session instead of `fnxSession0`. Rejected: user explicitly authorised pushing to the working session; pull-then-compare proves no data loss.


---

## #37 — Maestro corners-CSV is the v1 backup format; `pvt corners build` is skillbridge-independent recovery (resolves Open Decision 8.3)
_Date: 2026-05-13_

**Decision:** `pvt corners build <union.json>` emits a Maestro-native corners CSV (the same format that `Tools → Corners → Export` produces). The recovery flow after a Cadence crash is: restart Maestro → `Tools → Corners → Import → <file>.csv` — explicitly NOT requiring `skillbridge` or `sbStart.il` to be functional.

**Why CSV and not the `.union.json` itself:** The user pushed back on the original Phase 2 framing where `.union.json` + `pvt corners push` was assumed to be the recovery path. Real concern: "公司 Cadence 经常闪退" — after a crash, the `skillbridge` socket may be gone and reloading `sbStart.il` is one more failure point. A pure-GUI recovery via Maestro's own Import dialog has no `skillbridge` dependency.

**CSV format** (reverse-engineered from a live `Tools → Corners → Export` on `fnxSession0`; ground truth pinned at `tests/fixtures/unions/fnxsession0_baseline.csv`):

```
Corner,<row_name>,<row_name>,...
Enable,<f|t>,<f|t>,...
<VarName>,<val|sweep|empty>,...                   ← one row per var
Modelfile::<abs_path>,<test_en> <sect1> <sect2>,...
<test_en> <block>::<test_name>,<t|f>,<t|f>,...    ← one row per test
```

Key empirical facts:
- Sweep values within a cell: space-separated (e.g. `3 2.8`).
- `temperature` (lowercase in SKILL API) appears as `Temperature` in the GUI/CSV — Maestro display rule. Other var names preserved as-is.
- `block` / `test` in the CSV's last row use the **testbench cell name**, NOT the SKILL-side `axlGetModelBlock` / `axlGetModelTest` defaults (`"Global"` / `"All"`).
- Per-corner `Enable` flag is GUI-visible and `axlGetEnabled` exposes it (this is a different API than the historical guesses; `axlIsCornerEnabled` does NOT exist).
- The `Modelfile::` row prefix uses the absolute model path. SKILL's `axlGetModels(corner)` gives basenames only; `axlGetModelFile(modelHandle)` is the API that gives the absolute path. Production code historically chose basenames for cross-machine portability; the CSV path needs the abs path, so pull now captures BOTH (`file` = basename for round-trip, `_file_abs` underscore-prefixed for build).

**Schema implications (Phase 2 §3.3 extension):**
- `UnionRow.enabled: bool = True` — proper schema field, participates in round-trip.
- `ModelEntry.file_abs: str | None = None` — underscore-prefixed in JSON (`_file_abs`) per spec §4.2 modulus, so it does NOT participate in round-trip. It's informational; only the build emitter consumes it. Sidecars without it still load (default `None`); `pvt corners build` warns + falls back to basename + the emitted CSV is NOT Maestro-importable in that fallback case.

**v1 emitter limitations** (documented in `python/simkit/corners_csv.py` module docstring):
- Single test per setup. Multi-test extension needs per-(corner, test) enable matrix.
- One `Modelfile::` row per distinct abs path; corners sharing the same model file share one row.
- No CSV quoting/escaping. Any cell value containing `,` / `"` / `\n` / `\r` raises `CsvBuildError` rather than producing an ambiguous CSV. Real-world data has not exercised this.

**Alternatives considered:**
- `.sdb` (binary Maestro setup database) via `axlExportSetup`. Rejected for v1: requires Maestro running to produce the backup file (live API), defeats the "backup against runtime failures" purpose. Also opaque to inspection.
- `.pcf` (process customization file) via `axlLoadCornersFromPcfToSetupDB` on the import side. Rejected: no documented `axl*ToPcf` / export-side companion API; the format is GUI-only.
- Pure-Python custom CSV (one row per sub-corner, simple flat layout). Rejected: not re-importable via Maestro GUI; user would have to either retype or run the pure-Python re-importer, which itself depends on skillbridge — same dependency the backup was trying to avoid.

**Verification status (2026-05-13):**
- Offline byte-identical: emitter output equals `fnxsession0_baseline.csv` exactly, pinned as 5-case `TestGateU4SidecarToCSV`.
- Live runtime-verify: CLI `pvt corners build` produces the same bytes as the offline emitter; smoke test `diff` against ground truth is clean.
- **NOT YET verified**: end-to-end recovery flow (kill Maestro → restart → `Tools → Corners → Import → built.csv` → confirm 3 corners reappear with right vars/sections). Single physical action owed by user; logged in PROJECT_STATE.md "Owed" section.

---

## #38 — Phase 3B promoted: Formula-template authoring layer
_Date: 2026-05-14_

**Decision:** Promote Phase 3B (Formula Templates) to active phase, ahead of all other Phase 3 candidates (sim orchestrator, design-ref bulk update, report generator, auto-hook, standard TB generator). Goal: complete the **Define** layer of the system architecture by giving the user a way to declare "what to measure" with the same authoring economics they got from Phase 2's "what conditions to measure under" (PVT unions).

**Why now:**
- Phase 1 closed the Persist + Consume root; Phase 2 closed the corners side of the Define layer. P3B is the symmetric completion: measurements side of Define.
- Phase 3A (sim orchestrator) explicitly waits on a stable Define layer per `PHASE_PLAN.md`. Building the orchestrator while measurements are still hand-edited in Maestro Calculator means the batch flows configure the wrong things on every run.
- User's framing 2026-05-14: "剩下的几个都是痛点，我们得好好聊聊，实现起来估计有不少坑" — B is the smallest scope among the painful Phase 3 candidates; A/D/F are bigger commits that benefit from going second.

**Scope locked:** v1 = a working skeleton that lets the user author + persist their own formula templates and apply them to a live Maestro session. NO pre-baked template library in v1 — rise_time, dutyCycle, etc. are user-authored against this scaffold. Specific built-in templates land in a later iteration once the framework shape is right.

**Alternatives considered:**
- Sim orchestrator first. Rejected per the rationale above — wrong layer order.
- Standard TB generator first. Rejected: bigger scope (symbol generation + skeleton assembly + variant switching), less obvious leverage on the Phase 1 data layer.
- Skip B, jump to A. Rejected: leaves measurements as the hand-edited weak link; orchestrator's "auto-run + auto-ingest" still ingests the wrong measurements.

---

## #39 — Phase 3B core decisions (1–5b + F1–F3)
_Date: 2026-05-14_

**Decision:** Eight decisions locked during the 2026-05-14 Phase 3B spec kickoff. Recorded together because they form a coherent design and reference each other:

| # | Decision | Notes |
|---|---|---|
| P3B.1 | Template expression = arbitrary composite calculator expression + `$PARAM` placeholders. | Not bound to a single built-in (rise_time / average / etc.). Supports multi-function composites — the live `fnxSession0` Rtime_clkout `average(riseTime(vtime('tran "/Vout") 0 nil VAR("VDD") nil 10 90 t "time"))` is a representative case. |
| P3B.2 | Templates live project-level in `.pvtproject`'s `templatesDir/`. | Mirrors Phase 2 `unionsDir`. User-level (cross-project) library deferred to a later iteration. |
| P3B.3 | Apply unit = template-set × signal-group, cartesian product. | Two named collections; user organises sets and groups so the product is meaningful per #P3B.F1. |
| P3B.4 | Lands on Maestro's ADE-XL Outputs table via `axlAddOutputExpr`. | Confirmed by live probe 2026-05-14; see #40 for the API map. |
| P3B.5a | Template authoring lives entirely on the Python side (JSON files). Cadence is not consulted during template construction. | Avoids a live-session dependency in the most common authoring flow. |
| P3B.5b | Paste-import is a first-class flow: user pastes a working concrete Cadence expression; software extracts a template with placeholders. | User language: "也允许用户粘贴cadence的成品公式，软件自动替换为通用模板". |
| P3B.F1 | Set × Group cartesian: not-applicable combinations are hard-applied; Maestro reports errors at sim time. | No template-side metadata for "accepts current vs voltage". Lowest authoring friction. |
| P3B.F2 | Paste-to-template heuristic: signal paths auto-parameterized to `$SIG_n`; numeric literals are an interactive CLI choice ("parameterise 0.45? [y/N]"). | Signals are obviously knobs; numbers may be domain constants (e.g. 0.5 ≈ half-VDD) and over-parameterising them hurts UX. |
| P3B.F3 | Syntax check at template save lives **in Python only** (revised from initial "Cadence-side check"). Cadence errors surface implicitly at apply time. | Initial assumption was that Maestro exposes a parse-without-eval entry. Live probe (see #40) showed that `axlAddOutputExpr` accepts malformed expressions (unknown function names quietly stored as `(fn args)`, mismatched parens silently no-op) so there is no clean Cadence syntax-check API to call. Python checks: balanced parens/quotes/braces; `$PARAM` references all declared in `params` list; no unknown `$PARAM` left after substitution. |

**Why bundled into one entry:** These eight decisions reference each other (e.g. F1 only makes sense given P3B.3's cartesian semantics; F3 was a revision driven by #40's probe finding). Future readers should see them as one design move, not eight independent choices.

---

## #40 — Maestro Outputs-table API map (Phase 3B foundation)
_Date: 2026-05-14_

**Decision:** Phase 3B's Maestro-side surface is built on the following five entries, all confirmed-existent on `fnxSession0` via skillbridge probe 2026-05-14:

| API | Direction | Use in P3B |
|---|---|---|
| `axlAddOutputExpr(sess, test, name, ?expr E, ?evalType "point\|corners\|sweeps\|maa", ?exprDPLs DPLs, ?plot g, ?save g) => t / t_error` | write | **Push path.** Single-row or DPL-batch. evalType only settable via this entry (not the CSV path). |
| `axlAddOutputSignal(sess, test, signalName, ?type "net\|terminal", ?outputName, ?plot, ?save) => t / t_error` | write | Out of scope for v1 template flow (signal-tap outputs are a different concept). Phase 3B's pull path passes them through unchanged. |
| `axlDeleteOutput(sess, test, name, ?type "expr\|signal") => t / t_error` | write | Cleanup. Also used in dry-run-and-rollback patterns. |
| `axlOutputsExportToFile(sess, csvPath, ?omitTestCol) => t / nil` | read | **Pull path.** CSV columns: `Test, Name, Type, Output, Plot, Save, Spec`. Type ∈ {`net`, `expr`}. |
| `axlOutputsImportFromFile(sess, csvPath, ?operation "merge\|overwrite\|retain", ?test) => t / nil` | write | File-based push alternative. Less granular than `axlAddOutputExpr` (no per-row error, no evalType, no dry-run), but useful for "restore from snapshot" flows. Not the primary v1 push path. |

**Functions that do NOT exist on this Cadence version** (probed 2026-05-14, all returned `nil` via `getd`):
- `axlGetOutputs`, `axlGetOutput`, `axlGetOutputName`, `axlGetOutputExpr` — no programmatic enumerator or accessor exists. The CSV export is the only read path.
- `axlPutOutputExpr`, `axlSetOutputExpr` — no in-place expression mutation. Update = add with same name (silently no-ops if the new expression is malformed; see #39 P3B.F3).
- `calVal` / `calParse` / `calCheck` / `axlEvalExpr` / `axlExprParse` — no parse-without-evaluate entry. Note: `calcVal` (different casing) DOES exist but is a post-sim value lookup, not a syntax check.

**Probe-derived behavioural quirks** (documented here so they don't have to be re-discovered):
- `axlAddOutputExpr` returns `t` even when the input expression is malformed (unbalanced parens) or references an unknown function. Malformed-parens add silently no-ops; unknown-function add stores the expression in LISP-rewritten form (e.g. `totally_not_a_function(VT("/X"))` becomes `(totally_not_a_function VT("/X"))` in the CSV).
- For an output `name` that already exists in `(test, type)`, `axlAddOutputExpr` updates in place (no need to delete first), but the silent-no-op-on-malformed behaviour applies: a bad new expression leaves the old one in place.
- `axlOutputsExportToFile`'s CSV omits `evalType`. Round-trip through CSV loses evalType information. Phase 3B's authoritative source is therefore the template sidecar (which carries `evalType`), and the CSV is the snapshot-and-recovery format.
- Signal-tap rows (Type=net) have empty `Name`; expression rows always have `Name`. Pull-path filter for "templated outputs" is `Type == "expr" AND Name != ""`.

**Reference fixture:** `tests/fixtures/scratch_p3b_fnxsession0_outputs.csv` — captured live 2026-05-14 from `fnxSession0` (Test bench, 11 outputs: 4 net + 7 expr including the multi-function-composite Rtime_clkout). Promoted to a proper fixture name during §3 (SKILL bridge) implementation.

**Alternatives considered:**
- Use `axlOutputsImportFromFile` as the primary push path. Rejected for v1: no per-row error reporting (we'd report "CSV import failed" with no row-level detail), no dry-run, no evalType. Retained as the snapshot-restore path.
- Use Maestro's `calcVal` as a parse check. Rejected: `calcVal` only operates on already-added outputs and only after a sim run; not a parse-only entry.

---

## #41 — Phase 3B sidecar tri-architecture: templates × signal-groups × measurement bundles
_Date: 2026-05-14_

**Decision:** Phase 3B has **three** project-level sidecar types, not one. The split is forced by P3B.3's "set × group" cartesian and by P3B.5b's paste-to-template authoring:

| Sidecar | Location | One file = | Schema responsibility |
|---|---|---|---|
| **Template** | `<templatesDir>/<name>.template.json` | One reusable formula with `$PARAM` placeholders | Expression + param list + default evalType/plot/save metadata. No signal paths, no test names. |
| **Signal group** | `<signalGroupsDir>/<name>.siggroup.json` | One named collection of signal paths | Just a list of paths, no metadata about voltage-vs-current (per P3B.F1). |
| **Measurement bundle** | `<measurementsDir>/<name>.measure.json` | One named application: pick a template-set + a signal-group + a target test → renders to N concrete output rows | The "assignment" object; the analog of Phase 2 unions. |

**Sidecar paths default to** `./templates/`, `./signal_groups/`, `./measurements/` under the `.pvtproject`'s directory (mirrors Phase 2's `unionsDir`). All three are configurable via `.pvtproject` keys (`templatesDir`, `signalGroupsDir`, `measurementsDir`) — additive schema update, no version bump.

**Apply mechanics:**
- `pvt measure apply <bundle>` resolves the bundle's referenced templates + signal-group; renders each (template × signal) pair into a concrete output expression; pushes via `axlAddOutputExpr` per-row (DPL batch when N ≥ ~5).
- Output name format **v1**: `<template.short_alias>_<signal_basename>`, where `signal_basename` is the last path segment with `/` stripped. Example: template `Rtime` (short_alias) × signal `/Vout` → output name `Rtime_Vout`. Matches the fnxSession0 convention (`Rtime_clkout` style).
- Apply is **additive by default** — uses `axlAddOutputExpr` per row, not `axlOutputsImportFromFile overwrite`. User can pass `--replace` to delete existing same-named outputs first.

**Pull mechanics (v1):**
- `pvt measure pull <out>.snapshot.json` calls `axlOutputsExportToFile`, parses the CSV, writes a "raw snapshot" (no template match-back). The snapshot preserves enough fidelity to round-trip via `axlOutputsImportFromFile overwrite` and recover after a Maestro crash.
- **Template match-back is deferred to v2.** The hard part of reversing a concrete expression to (template + params) is not on the critical path for the skeleton, and v1 ships without it.

**Round-trip identity contracts:**
- (Apply) Bundle → render → Maestro → snapshot pull → snapshot push (overwrite) → snapshot pull = bit-identical snapshot. Tests: §6 Gates M2 + M3.
- (Authoring) Paste-import an existing fnxSession0 expression → template JSON → render-with-original-params → expression equals the pasted source modulo whitespace. Tests: §6 Gate M1.
- (Validation) Template with unbalanced parens / unknown `$PARAM` / quote-imbalance is rejected by the Python loader at save time. Tests: §6 Gate M4.

**Why three sidecars and not one merged "measurement-config.json":**
- Templates are reused across multiple bundles. Inlining defeats reuse.
- Signal groups are reused across multiple template applications.
- Measurement bundles are the only place "which test gets which outputs" is recorded. Co-mingling with templates/signal-groups would force re-validation of the entire file when any of the three things change.

**Why measurement bundles are NOT auto-derived from "apply" CLI invocations:**
- Ad-hoc apply (`pvt measure apply --template T --signal-group SG --test Test`) is a transient operation, no sidecar required. Good for one-offs.
- Persistent named bundles (`pvt measure apply <bundle.measure.json>`) is the recommended flow for repeat work and recovery. Authoring a bundle = create the JSON file (or `pvt measure new-bundle` helper, similar to Phase 1's `pvt label`).

**Alternatives considered:**
- One merged "measurements.json" per project. Rejected: poor reuse, large blast radius on any edit.
- Two sidecars (drop signal-groups, inline signal lists in bundles). Rejected: signal groups ARE reused across bundles (e.g. "high_speed_clocks" used in both `core_review.measure.json` and `crosstalk_check.measure.json`); inlining duplicates the list and de-syncs.
- Apply-and-discard, no bundle persistence. Rejected: defeats P3B.3 (you'd type the set × group every invocation) and the crash-recovery value prop.

---

## #42 — `pvt measure restore` defaults to `merge`, not `overwrite` (safety fix)
_Date: 2026-05-14_

**Decision:** `pvt measure restore` (and the `pvt_measure_restore` Python wrapper) default `operation` to **`merge`**, not the originally-shipped `overwrite`. Users who genuinely want to wipe-and-replace must opt in explicitly via `--operation overwrite`, and the CLI prints a one-line safety note when they do.

**Why:** Live verification 2026-05-14 of P3B Gates M2 + M3 against `fnxSession0` exposed a sharp edge: `pvt measure pull` defaults to `--include-signals=False` (snapshot captures only `expr`-type rows, by design — Phase 3B operates on expression outputs, not signal taps). `pvt measure restore` then defaulted to `overwrite`. The combination silently wiped `fnxSession0`'s 4 `net`-type signal-tap rows (Vin, Vout, AVDD, AGND) because `axlOutputsImportFromFile ?operation "overwrite"` does **not** mean "replace same-named rows" (which the SKILL doc text plausibly implies) — empirically, it means "replace the entire Outputs table with the imported CSV's contents." Rows not in the imported CSV are removed.

**Recovery:** Re-imported the live-captured baseline CSV (4 net + 7 expr) via direct `axlOutputsImportFromFile overwrite` to put fnxSession0 back to 11 rows. Then changed the CLI/wrapper defaults to `merge` so the same workflow can't bite anyone again. Both the live verify script (`/tmp/verify_m2_m3_live.py`) and the offline test suite (607/607) pass with the new default.

**Why not the alternative — change pull's default to include signals:**
Considered, rejected for v1. `pvt measure pull` is the audit + "what templates are applied" surface; including signal-tap rows pollutes the snapshot for that use case. Keeping pull lean and making restore conservative is the safer split. For a truly faithful crash-recovery snapshot the user can still run `pvt measure pull --include-signals`, and pair it with `pvt measure restore --operation overwrite` if needed; both opt-ins.

**Test coverage:** `test_cli_measure.py::RestoreCliTests::test_restore_csv_ok` and `test_skill_bridge_measure.py::PvtMeasureRestoreTests::test_calls_axlOutputsImportFromFile_with_overwrite` (now updated to assert `merge`) pin the new default. The existing `test_restore_explicit_operation` already covered the `--operation merge` path explicitly.

**Verified live 2026-05-14:** With the new defaults, full M2 + M3 round trip on `fnxSession0` is clean (M2: bundle apply → row landed byte-equal; M3: pull → restore → pull bit-identical), and the cleanup step (`axlDeleteOutput`) returns the session to its 11-row baseline. Net signal-tap rows survive intact.

---

## #43 — Phase 3B v1.1 builtins library: 17 templates, `ANALYSIS`-as-param, `signal+string` edge-delay
_Date: 2026-05-14_

**Decision:** Ship a 17-template builtins library under `config/builtins/`, installed via `pvt measure install-builtins [--force] [--names …] [--list]`. The set was derived by reverse-engineering one of the user's real production Outputs CSVs (a 130-row "sim_DCOBUF" testbench for a DCO buffer characterisation). Three shape choices that materially affect future v2 work:

1. **`ANALYSIS` is a template-level string param, defaulting to `tran`** — rather than shipping separate `_pss` variants. Every template that touches `vtime('tran …)` / `itime('tran …)` now reads `vtime('$ANALYSIS …)`. Override `ANALYSIS=pss` (or `dc`, `ac`) per bundle entry. One template, many analysis types. **Why:** the user's real workflow uses PSS for power steady-state (`average(itime('pss …))`) and TRAN for everything else; duplicating every windowed template `_pss`-style would double the library without earning anything except clutter.

2. **Edge-delay is `1 signal + 1 string ref`, not `2 signals`** — `edge_delay_avg` / `edge_delay_wave` declare `SIG_A` as the signal-kind param (iterates the bound signal_group) and `SIG_B` as a `string`-kind param (per-bundle-entry override, fixed reference path). The dominant real use is "many comparison signals vs. one master clock reference," which this shape captures cleanly. **Why:** Phase 3B v1 spec §3.5 enforces ≤1 `signal`-kind param per template; relaxing to N would require teaching `MeasureApply` to carry N signal_groups (pairwise / cross-product iteration), which is a real v2 design problem (do we mean cartesian, zip, or named-pairs?). Defer that until a real "N×M comparison matrix" case appears.

3. **`cycle_wrap_positive` and `phase_diff_wrap` are explicit follow-on templates, not folded into `edge_delay_avg`** — the user's source CSV writes the unwrapped and wrapped values as two separate Maestro outputs (`T_criteria_temp_5G` then `T_criteria_5G`) so that the unwrapped value remains visible during debug. The framework preserves that idiom: a bundle's `apply` list is the natural place to chain "raw measurement → post-process → spec-checked output."

**Library inventory (17):** `i_avg_window`, `i_avg_full`, `freq_window`, `duty_cycle_window`, `rise_time_auto`, `fall_time_auto`, `rise_time_fixed`, `fall_time_fixed`, `dft_window`, `dft_mag_at_freq`, `dft_phase_at_freq`, `db20_ratio`, `edge_delay_avg`, `edge_delay_wave`, `cycle_wrap_positive`, `phase_diff_wrap`, `value_at`.

**Walkthrough proof:** A 4-entry measure bundle (`tests/fixtures/builtins_walkthrough/dco2g_review.measure.json`) collapses 20 hand-written DCOBUF CSV rows (5 clock nets × {Freq, DutyC, Rtime, Ftime}) and renders byte-for-byte equal to the source rows. Pinned as 4 cases in `tests/test_builtins_walkthrough.py`.

**Known v1 limitation surfaced by the walkthrough — signal-basename collision:** When a signal_group's nets share basenames (e.g. four supply paths all ending in `/VDD`), the output-name composition `<short_alias><alias_suffix>_<basename>` collides and render fails loudly with `RenderError`. The user's source CSV disambiguates this by hand-numbering rows (`1`, `2`, …). Native absorption requires a v2 per-signal alias map — flagged as a discrete next step rather than a workaround in v1.1. Pinned as a deliberate failure case in `test_supply_group_collides_under_v1_naming`.

**Test coverage:** `test_builtins.py` (5 cases — load every builtin, render every builtin, byte-for-byte vs. 17 reverse-engineered DCOBUF rows). `test_cli_measure.py::InstallBuiltinsCliTests` (8 cases — full install, dry-run, subset by `--names`, unknown-name rejection, collision-refuse, `--force` overwrite, missing-project, post-install listability). `test_builtins_walkthrough.py` (4 cases). Python suite 598 → 602 / 0.

**Alternatives considered:**
- Cross-project sharing via `~/.simkit/templates/`. Rejected: P3B spec §3.5 already defers it to v2; install-builtins keeps templates project-local (which is the right scope for editing/branching them per project).
- Conditional rendering (e.g. optionally drop a `* $MUL` multiplier when `MUL=1`). Rejected: dumb-textual substitution is intentional per DECISIONS #41 — conditional logic in template bodies leaks the render contract into every consumer.
- Shipping a "rise time with explicit-rail" `i_avg_window_scaled` variant. Rejected after user feedback ("感觉没什么道理，碰巧相同"): the multiplier appeared in 10 source rows but the multiplier value (2) was coincidence across unrelated devices, not a generic idiom.

---

## #44 — Phase 3B v1.2: output_name override + raw_expression + param_sweep + _full rise/fall variants
_Date: 2026-05-15_

**Decision:** Close six v1.1 → v1.2 friction points surfaced by the 2026-05-15 dogfood against live `fnxSession0`. Two are polish, four extend the bundle expressiveness. Bumps `measure_schema_version` to 2 (v1 bundles still load; v2-only fields rejected in v1 with a "bump to 2" error).

**The six items, in dependency order:**

1. **(c) implicit `signal_group: null`** — When the apply-entry template declares no signal-kind param, omitting `signal_group` is now equivalent to explicit null. v1.1 required the field even when meaningless. Schema-version-neutral (relaxation).
2. **(d) `list-bundles` STATUS column** — Parse-failure status now reads `ERR: <reason>` with the leading bundle path stripped (the path is already in the `path` column). Polish.
3. **(a) `output_name` override + `${SIG}` placeholder** — apply entry gains optional `output_name` field that *fully* replaces the `<short_alias><alias_suffix>[_<basename>]` concat scheme. `${SIG}` is the lone placeholder, substituted to the signal basename when the template has a signal-kind param. v2-only.
4. **(b) Four `_full` rise/fall builtins** — `rise_time_auto_full` / `rise_time_fixed_full` / `fall_time_auto_full` / `fall_time_fixed_full` mirror the `i_avg_window` / `i_avg_full` precedent. Drop the `clip(... t_1 t_2)` wrap and the `T_START` / `T_END` params; expressions are otherwise identical. The live `fnxSession0` Rtime_clkout is byte-identical to `rise_time_fixed_full` with default rails. Library grows 17 → 21. Schema-version-neutral.
5. **(f) `raw_expression` entry kind** — New apply-entry shape `{raw_expression, output_name, plot?, save?, eval_type?}` peer to the template entry. Schema enforces exactly-one-of `{template, raw_expression}`. Render path: literal pass-through, no substitution. Required for round-tripping composite waves (e.g. `rfEdgePhaseNoise(...)` for `PN_wave`) that no builtin can express. v2-only.
6. **(e) `param_sweep` + `output_names`** — Single-axis sweep: apply entry gains `param_sweep: {KEY: [v1, v2, …]}` + parallel `output_names: [n1, n2, …]` arrays. Equal-length enforced; sweep key must be a declared non-signal-kind template param and not collide with `param_overrides`. Multi-axis is deferred to v1.3 (DECISIONS will get a follow-on entry). With signal_group, `${SIG}` in each name slot expands to basename — yielding `N×M` rows in `(sweep, signal)` order. v2-only.

**Critical design choice — why no CLIP parameter on rise/fall:** First-pass proposal added a `CLIP` boolean param to the four existing builtins, requiring a new conditional-render engine (`expression_when` alternatives). User pushed back: the existing convention already distinguishes window vs. non-window by *name* (`i_avg_window` / `i_avg_full`). Adding CLIP-as-param would have introduced a *second* mechanism for the same semantic distinction. Variant route is consistent, cheaper, and keeps each template expression simple. **Rule:** the library uses naming, not parameters, to switch between windowed / unwindowed shapes.

**Dogfood proof:** A 3-entry bundle (`measurements/dogfood_v2.measure.json`) describes the 7 expr rows of live `fnxSession0` exactly: 1 `raw_expression` entry for `PN_wave`, 1 `param_sweep` entry over `value_at` for the 5 `PN_*` spot frequencies, 1 template entry with `output_name` override for `Rtime_clkout`. `pvt measure apply --replace` on live → pull → diff vs. pre-apply baseline.snapshot.json: 11/11 rows byte-identical (4 net + 7 expr).

**Output-name precedence rule:** If `output_name` is set on an apply entry, it fully shadows the concat scheme. `${SIG}` placeholder is only substituted; no other text rewriting. Collisions (two entries producing the same name) raise `RenderError` at render time. For sweep entries, each `output_names[i]` follows the same rule per swept iteration.

**Test coverage:** 35 new cases across `test_measure_bundle.py` (load-time validation), `test_template_render.py` (render expansion), `test_builtins.py` (4 new builtins), `test_cli_measure.py` (count bump 17→21, STATUS column). Python suite 602 → 662 / 0. Live verification against `fnxSession0` clean.

**Alternatives considered:**
- `CLIP` boolean param + `expression_when` conditional render engine for rise/fall. Rejected (user feedback): inconsistent with `i_avg_window`/`i_avg_full` convention already in the library.
- Sum-type split `TemplateApply | RawApply` for `raw_expression`. Rejected: single `MeasureApply` dataclass with `template: Optional[Template]` + raw fields is simpler; loader enforces exclusivity.
- Pattern-based naming for `param_sweep` (`output_name_pattern: "PN_{X_LABEL}"`). Rejected (user pick): parallel `output_names` array is explicit, no name-derivation guesswork.
- Multi-axis sweep in v1.2. Rejected: scope creep; the single-axis case covers the dominant idiom (one scalar varies, output name encodes the variant). Multi-axis follows once a real "freq × temperature" 2-D matrix case appears.

---

## #45 — Phase 3B v1.3: spec passthrough at bundle apply entry layer
_Date: 2026-05-15_

**Decision:** Add an optional ``spec`` field to bundle apply entries (template, raw, and sweep kinds alike). The string is Cadence-native — exactly what Maestro's Outputs CSV shows in the Spec column (e.g. ``<100p``, ``> -140``, ``range -150 -100``, ``[2.4G:2.6G]``, ``tol 0.05``). SKILL push parses it and dispatches to ``axlAddSpecToOutput`` with the appropriate exclusive keyword (``?lt`` / ``?gt`` / ``?min`` / ``?max`` / ``?range`` / ``?tol``). Closes the silent v1.0–v1.2 gap where the framework pulled the Spec column on pull but discarded it on push, so any pass/fail criteria the user had hand-edited in Maestro got wiped on the next ``apply``.

**Why this layer and not the template:** Pinned via the AskUserQuestion design fork. Spec is design-specific, not formula-specific — the same ``rise_time_fixed_full`` template lands ``<100p`` for clkA and ``<200p`` for clkB depending on the testbench. Putting spec on the template would either force per-template-per-design library proliferation or push the variation into ``param_overrides``, neither of which earns its weight. The apply-entry layer is the natural carrier.

**Why "Cadence-native string" and not structured JSON:** User pick on the syntax fork. Bundles store exactly the string the user already knows from the Maestro GUI ("<100p"). The SKILL side parses it; Python is just a transport with a light prefix sanity check. Trade-off accepted: bundle files don't help validate semantic errors (the framework can't tell that "range -100 -200" is degenerate); Cadence raises at apply time. Acceptable because the GUI-typed-string idiom is what users already think in.

**Round-trip is semantic, not byte-identical.** Cadence normalises on the way in: ``<100p`` becomes ``< 1e-10`` after push+pull, and ``<-100`` becomes ``< -100``. The operator is space-separated and SI suffixes resolve to scientific notation. A pull-after-apply produces a snapshot whose spec strings differ textually from the bundle's spec strings, but the semantics are preserved. ``pvt measure restore`` (which uses ``axlOutputsImportFromFile`` and carries the spec column natively through Maestro) round-trips byte-identical, so the snapshot→snapshot loop is bit-clean — only the bundle→snapshot direction normalises.

**SKILL gotcha — uncatchable evalstring errors.** Verified live 2026-05-15 that ``(errset (evalstring "abc") t)`` does NOT catch the "unbound variable" error path: it leaks past errset and aborts the enclosing call. The parser hand-checks the character set before each evalstring (``_pvtMeasureSafeEvalNumber``) using a manual scan instead of relying on errset. Cadence's ``rexCompile`` syntax is also too restrictive for a single-pattern validator (no ``?`` quantifier, no ``()`` groups), which is why the check is a per-char loop over a static charset. Documented inline; no externally observable symptom but worth knowing for future "tiny SKILL parser" work.

**Push-failure semantics:** ``axlAddOutputExpr`` succeeding plus ``axlAddSpecToOutput`` failing does NOT downgrade the row's primary status to "failed". The per-row result table now carries an optional ``spec_status`` field — ``"ok"`` / ``"failed: <reason>"`` / ``"would_apply: <str>"`` / absent — so the caller can decide what's fatal. Rationale: the user mostly cares "did the measurement go in"; spec is metadata. A bad spec string should surface as a per-row warning, not abort the whole apply batch.

**Test coverage:** SKILL Tier-1 +15 cases on ``_pvtMeasureParseSpec`` + ``_pvtMeasureSafeEvalNumber`` (lt/gt/inclusive-min/max/range-keyword/range-bracket/tol, plus garbage/non-numeric/empty/embedded-letter rejection). Python +16 cases across ``test_measure_bundle.py`` (load-time validation on three entry kinds + v1-schema gate) and ``test_template_render.py`` (spec propagates through template / raw / multi-signal / param-sweep render paths) and ``test_cli_measure.py`` (rendered.csv gains a trailing ``spec`` column). Python suite 662 → 678 / 0. SKILL Tier-1 347 → 376 / 1 (baseline FAIL unchanged).

**Live dogfood:** ``measurements/dogfood_v3.measure.json`` adds spec to the v1.2 7-entry bundle: 5 PN_* rows get ``<-100`` (pulled back as ``< -100``), Rtime_clkout gets ``<100p`` (pulled back as ``< 1e-10``), PN_wave keeps no spec. Apply --replace → pull → spec strings observed verbatim from Cadence's normalisation. fnxSession0 restored to spec-clean baseline after.

**Deferred to v1.4:**
- Per-iteration spec on sweep entries (currently a single spec applies to all swept rows uniformly).
- ``axlGetSpecData`` / ``axlGetSpecWeight`` capture on pull — pull already records the Spec column string; the structured ?weight / ?info side metadata is lost.
- Dotted ``X..Y`` range form (parseString uses single-char delimiters, ambiguous with the dot in numeric literals).
- Spec status surfaced in the apply CLI summary (currently captured in the SKILL report but not printed; need to wire through PvtMeasurePushReport decoding).

**Alternatives considered:**
- Structured ``{op, value}`` JSON form. Rejected on user pick: passthrough is simpler and bundle files store what the user types.
- Switch the whole apply path to ``axlOutputsImportFromFile`` (gets spec "for free" because Maestro parses the CSV). Rejected: would lose the per-row added/replaced report that the user has been using to verify what landed. Per-row dispatch + new axlAddSpecToOutput call keeps that report and adds spec_status as a separate signal.
- Lock spec to v3 schema (separate version bump). Rejected: spec is additive on top of v1.2 fields; keeping ``measure_schema_version: 2`` lets users upgrade by adding the one field without re-deciding what their existing bundles mean.

---

## #46 — Phase 3B v1.4: spec parser accepts `X..Y` dotted range; weight/info read-side scope-down
_Date: 2026-05-16_

**Decision A — dotted range form lands in SKILL parser.** v1.3 doc claimed support for `X..Y` but `_pvtMeasureParseSpec` had the branch commented out as "intentionally NOT supported". v1.4 implements it for real. The single-char-delimiter argument that blocked it before is bypassed by using classic SKILL `index(s "..")` (returns substring at first match) instead of `parseString`. Two pre-checks are mandatory: reject 3+ consecutive dots ("1...2"), otherwise it silently parses as `range(1, .2)`; reject multiple `..` occurrences ("1.5..2.5..3.5"), otherwise the rhs feeds evalstring with a malformed numeric token and the SKILL reader's error path is uncatchable per #45.

**Decision B — `axlGetSpecWeight` IS readable; `info` is write-only.** Live skillbridge probe 2026-05-16 mapped the spec-data API surface end-to-end:

  | API | Status | Use |
  |---|---|---|
  | `axlGetSpecs(sdb)` | works | returns `(sdb_handle, [<test>.<output>...])` — count + address-string list |
  | `axlGetSpec(sdb, "<test>.<output>")` | works | returns spec INTEGER handle (NOT the un-dotted output name; that returns 0) |
  | `axlGetSpecName / ResultName / Type / Min / Max / Tol / Weight (int_handle)` | works | per-spec accessors; values are strings |
  | `axlGetSpecCondition(spec int_handle, field_str)` | exists | always returns 0 — different concept, not a value-getter |
  | `axlGetSpecData(sdb, test, output, [opt])` | works | returns nil when no spec set |
  | `axlGetSpecInfo` | DOES NOT EXIST | no read-side accessor for the `?info` field |
  | `axlDelSpecFromOutput` / similar | DOES NOT EXIST | no per-spec API. Bulk spec clear goes via `axlOutputsImportFromFile ?operation "overwrite"` with blank Spec columns (see Decision G); the same path `pvt measure restore` already uses |
  | `axlAddSpecToOutput ?lt + ?info` | REJECTED | "More than one spec type passed" — `?info` is treated as a mutually-exclusive spec kind, NOT a side metadata field |
  | `axlAddSpecToOutput ?lt + ?weight` | works | weight IS write-side passable alongside an operator |
  | `axlSpecMet / axlEvalSpec / axlGetResultPass*` | NONE EXIST | Maestro evaluates pass/fail internally for GUI dots, but does NOT expose a public eval API |

**Decision C — v1.4 #3 (pull captures weight + info) deferred to v1.5.** User confirmed they don't manually touch spec weights in daily work (all specs default to weight=1.0). Implementing pull-side capture without a corresponding push-side `spec_weight` field on `MeasureApply` would yield a misleading half-feature: snapshot would show weight but bundles couldn't apply it. Either implement both sides (medium scope, low user value) or skip both. Picked skip. v1.5 candidate if a real "this spec is more important" workflow surfaces.

**Decision D — pass/fail capture in collector (v1.4 #1) goes via Python, NOT a SKILL eval API.** No `axlSpecMet`/`axlEvalSpec` exists. Two paths considered: (i) capture spec strings in collector + recompute pass/fail in Python ingester from `value_num` + parsed spec; (ii) skip the collector and read pass/fail off the Maestro Results Browser (no public API). Picked (i). The Python ingester already has parser-only validation in `measure_bundle._validate_spec` — v1.4 #1 will add a small spec evaluator in Python (see follow-on decision for the eval design).

**Decision E — `axlGetSpecs(sdb)` shape is `(scalar_handle, [address_list])`, scalar IS NOT a spec id.** The first element of `axlGetSpecs` looks like a count or sdb echo, NOT a spec object. Iterating it as if each integer were a spec handle leads to "Cannot find a setup database entry for handle N" errors. Always iterate the second element (list of `<test>.<output>` strings), then map each to a handle via `axlGetSpec(sdb, full_address)`.

**Decision F — `axlGetSpec(sdb, name)` requires the FULL `<test>.<output>` address.** Passing just the output name (`"_probe_v14"`) returns 0 even when a spec exists; the dotted full form (`"Test._probe_v14"`) returns the actual integer handle. This is undocumented in adexlSKILLref but verified live.

**Decision G — spec cleanup goes via `axlOutputsImportFromFile ?operation overwrite` (the same path `pvt measure restore` already uses), NOT a dedicated `axlDel*FromOutput` API.** Initial v1.4 work mis-recorded this as "no programmatic cleanup; needs Cadence restart" — that was wrong. The escape hatch is to export the current outputs CSV, blank the Spec column for the row(s) you want to clear, and re-import with overwrite. Outputs are preserved; specs are wiped to whatever the CSV says (empty). Verified live 2026-05-16 by clearing the Rtime_clkout `< 1e-10` and PN_1M `> -150` specs left over from the v1.4 #1 dogfood. The earlier "orphan spec persists in axlGetSpecs(sdb) after axlDeleteOutput" finding still holds — but that's an orphan ID with no attached output, not a live spec, so it doesn't show up in any CSV / snapshot / capture and is effectively dead.

**Test coverage:** SKILL Tier-1 +9 cases on `_pvtMeasureParseSpec` dotted-range branch (5 happy + 4 reject), bringing Tier-1 376 → 385. Python +2 cases on `test_measure_bundle.SpecPassthroughTests` for dotted-range bundle load + negative-range bundle load.

**Live verification:** Parser exercised against 16 cases on fnxSession0 — all 5 happy decimals/ints/negative/SI/whitespace yield `(range V1 V2)`; all 4 reject paths return `pvt_err pvt_validation` with informative messages; all 5 v1.3 regression cases (bracket, range-kw, lt, ge, tol) unchanged. Cleanup path verified by removing the 2 dogfood specs via the overwrite-import recipe above; post-clean `_pvtCollCaptureSpecs` returns an empty table and the outputs CSV reverts to the 11-row baseline with all Spec columns empty.

---

## #47 — Phase 3B v1.5 #2: inclusive `>=` / `<=` specs dispatch via `?range`, not `?min` / `?max`
_Date: 2026-05-16_

**Decision:** `axlAddSpecToOutput ?min X` is stored by Maestro as `"minimize X"` (ADE-XL target-style: pass = value ≤ X); `?max X` as `"maximize X"` (pass = value ≥ X). These are optimization goals, not inclusive bounds — the **semantic opposite** of what `>=X` / `<=X` mean in a user-written bundle spec. v1.3 mapped `>=X` → `?min` and `<=X` → `?max`, which would flip pass/fail on every inclusive-bound spec. v1.4 dogfood didn't catch this because the only specs in use were `<` / `>`.

Fix: in `_pvtMeasureApplyParsedSpec`, dispatch `("ge" v)` → `axlAddSpecToOutput ?range (v _PVT_MEASURE_SPEC_HUGE)` and `("le" v)` → `?range ((minus _PVT_MEASURE_SPEC_HUGE) v)` with `_PVT_MEASURE_SPEC_HUGE = 1.0e30`. axlSKILL has no `?ge` / `?le` keyword; `?range` is the only inclusive-bound API surface. Cadence stores the result as `"range V 1e+30"` / `"range -1e+30 V"`, which Python `spec_eval.parse_spec` already evaluates as inclusive intervals.

Also renamed SKILL parser tags `"min"` → `"ge"`, `"max"` → `"le"` to match Python `spec_eval` convention and disambiguate from the ADE-XL `minimize` / `maximize` semantics.

**Why 1.0e30:** Beyond any physical quantity an analog-IC designer would put on a spec (frequencies, capacitances, resistances all fit in 1e20). Simple constant, visible in CSV exports — `range V 1e+30` reads obviously as "effectively unbounded above". No need to probe for an ADE-XL-internal infinity sentinel.

**Side effect on snapshot byte-identity:** v1.3 round-trip on `>=X` was already broken (push `>=X` → store `minimize X` → pull back `"minimize X"`). The fix changes the stored form to `"range X 1e+30"` — semantically correct, but a different string than the bundle source. `spec_eval` evaluates the read-back form identically to the bundle intent, so downstream pass/fail is unaffected. Snapshot-vs-bundle byte equality on inclusive-bound specs was never achievable given Cadence's storage normalisation.

**`minimize X` / `maximize X` bundle vocabulary NOT added.** Python parser accepts them on read (ADE-XL convention preserved); bundle write side still rejects. Promote to v1.6 if a user ever wants to express target-style optimization semantics in a bundle (versus inclusive bounds).

**Test coverage:** SKILL Tier-1 2 cases retagged (`measure/parseSpec/inclusive-{upper,lower}` from `inclusive-{max,min}`); count 394/1 unchanged.

**Live verification:** On fnxSession0, `>= 1e-10` on `Rtime_clkout` → CSV shows `range 1e-10 1e+30`; `<= -100` on `PN_1M` → `range -1e+30 -100`. Python `spec_eval.evaluate_spec` classifies 2.13e-11 vs `range 1e-10 1e+30` as `fail` and -167 vs `range -1e+30 -100` as `pass` — both correct under the inclusive-bound intent. Cleanup via overwrite-import (#46 G); fnxSession0 restored to 11-row baseline.

---

## #48 — Phase 3B v1.5 #3: per-iteration specs on sweep entries
_Date: 2026-05-16_

**Decision:** Add an optional `specs: list[str | null]` field on `MeasureApply` for sweep entries, parallel to `output_names`. Each `specs[i]` applies to the i-th swept row; `null` entries mean "no spec on this row". Mutually exclusive with the existing single uniform `spec` field; only valid when `param_sweep` is set; length must match `output_names`. Each non-null entry passes the same `_SPEC_PREFIX_RE` sanity that the uniform `spec` does.

Motivating case: phase-noise spot-frequency sweeps — `PN @ 1MHz < -100` vs `PN @ 100MHz < -140`. Pre-v1.5 a user had to pick between (a) one sweep entry with a single uniform spec (loses per-frequency tightness) or (b) N hand-written entries (loses the sweep economics). The parallel array recovers both.

**Renderer:** `_render_swept_entry` checks `entry.specs is not None` per iteration; if set, uses `entry.specs[i] or ""` as the per-row spec (None → empty string == no spec). Falls back to `entry.spec or ""` otherwise. No change to non-sweep render paths.

**SKILL push:** Unchanged. The per-row spec was already plumbed end-to-end via `RenderedRow.spec`; this decision just changes how that field gets populated at the Python boundary.

**Schema gating:** Added to `_V2_ONLY_APPLY_FIELDS`. v1 bundles touching `specs` raise the same "require 'measure_schema_version': 2" error as other v2-only fields.

**Test coverage:** 9 new Python cases in `test_measure_bundle.PerIterationSpecsTests` (happy + nulls + mutex + length + bad syntax + non-string element + empty element + non-sweep + non-array). 2 new render-side cases in `test_template_render.ParamSweepRenderTests` (per-row distinct specs, mix with None). Python 773 → 784.

**Not done:** No live skillbridge verification — this is a pure Python loader + renderer change; the SKILL surface is unchanged and was already live-verified in v1.3/v1.4. Future bundle dogfood with mixed-spec sweep will exercise it organically.

---

## #49 — Phase 3B v1.5 #4: signal-group alias map (siggroup schema v2)
_Date: 2026-05-16_

**Decision:** Bump `signal_group_schema_version` accepted set to `{1, 2}`. v2 lets each item in `signals[]` be either a bare net-path string (v1 form) OR a `{"net": "<path>", "alias": "<short>"}` object. When `alias` is present, the renderer uses it as the output-name basename in place of `signal_basename(net)`. Aliases must match `^[A-Za-z][A-Za-z0-9_]*$` and be unique within the group. Nets are unique within the group regardless of form. v1 sidecars are unaffected.

Motivating case: the v1.1 walkthrough's `dco2g_supplies.siggroup.json` — four nets whose basenames collide (`/I_BUF2G/DCO2G_buf_to_adpll/VDD`, `/I_BUF2G/I_rxbuf_lp/VDD`, `/I82/L13/PLUS`, `/I82/L0/PLUS` → `VDD/VDD/PLUS/PLUS` → render-time output-name collision → `RenderError`). Pre-v2 workaround was N hand-written `MeasureApply` entries with `output_name` overrides, which loses the "one entry + one group" economics. v2 alias absorbs the idiom natively.

**Dataclass change:** New `Signal(net: str, alias: Optional[str])` with a derived `output_basename` property. `SignalGroup.signals` is now `tuple[Signal, ...]` (was `tuple[str, ...]`). v1 bare-string items normalise to `Signal(net=str, alias=None)` at load time.

**Breaking-change blast radius:** 2 production callsites (`template_render._render_entry` line 132, `_render_swept_entry` line 251) + 3 test files. All updated. `cli/measure.py:739` uses `len(sg.signals)` which still works.

**Walkthrough fixture left alone:** `dco2g_supplies.siggroup.json` stays in v1 form as the regression pin for `test_supply_group_collides_under_v1_naming`. Its `_doc` field now points at this decision so future readers know the v2 alias form is the resolution. `test_template_render.SignalAliasRenderTests` carries the v2-form happy path with the same 4-supply pattern.

**Test coverage:** 11 new cases in `test_signal_group.AliasFormTests` (happy / mixed-with-strings / v1-rejects-alias / null-alias / optional-alias / bad-identifier / slash-in-alias / duplicate-alias / missing-net / unknown-key / cross-form-net-collision). 2 new cases in `test_template_render.SignalAliasRenderTests` (aliased group renders cleanly + bare-string regression). Python 784 → 797.

**Not done:** No live skillbridge verification — pure-Python loader + renderer change; net paths reach SKILL identical to today (only output_name strings differ, which the SKILL push side has always treated opaquely).

---

## #50 — Phase 3A §1: review-suite sidecar (`*.review.json`) shape
_Date: 2026-05-16_

**Decision:** A "review" is a named, ordered list of **items**, where each item bundles its own (tests, union, bundle) triple. Sidecar at `<reviewsDir>/<name>.review.json`. Top-level fields: `review_schema_version: 1`, `name`, `project`, `items: [...]`, optional `on_failure: {...}`. Item fields: required `name` + `tests: list[str]` + `union: path`; optional `bundle: path | null`, `enabled: bool = true`, `on_failure` (deep-merges over suite-level). Suite-level `on_failure` carries `default: "skip" | "halt"`, optional `corner_policy` / `item_policy` overrides, and a `strategies: [...]` chain.

**Why:** User explicitly clarified (2026-05-16) that real signoff workflow is **not** "one set of tests × one union" cartesian, but a list of 5-15 named items each pairing **its own** tests with **its own** corners — because trans tests need different corners than PSS (PSS often only wants typical/slow/fast for PN), different functional modes (BT2GRX / LE / interference) use different testbenches, etc. The list shape mirrors how the engineer thinks about a signoff battery: 10 named rows, each "a thing to do". Existing Phase 2 `.union.json` per-row `test` field already expresses per-test corner enables, so no schema work needed on that axis — items just compose existing sidecars.

**Alternatives considered:** (a) Cartesian top-level (`tests × union × bundle`) — rejected after user pushback; doesn't match real workflow. (b) Item carries `tests: list[str] + bundles: dict[test, path]` for per-test bundle — deferred to v1.1 when a real per-test-bundle case appears (default v1: single shared bundle covers the common case). (c) Item dependency graph — deferred to v1.1; v1 is flat sequential by list order.

**Resolves Phase 3A pre-§1 open questions:** sidecar shape (JSON), trigger surface (CLI primary + CIW callable), driver style (skillbridge), concurrency (delegated to Maestro/LSF intra-item; sequential inter-item), failure default (skip).

---

## #51 — Phase 3A §1: per-corner skip granularity + sequential item execution
_Date: 2026-05-16_

**Decision:** Failure handling is **per-corner**: if 1 of 21 corners in an item fails (sim_err / eval_err / non-convergence), mark only that corner FAIL in DB and **let the remaining 20 corners continue**. The item itself is not aborted unless explicitly configured via `on_failure: {item_policy: "halt"}` or all corners fail. Items run **sequentially in list order** — concurrency within one item is delegated to Maestro/LSF (orchestrator submits one `axlRunAllTestsWithCallback` per item and waits for the completion callback).

**Why:** User explicitly chose per-corner skip over per-item halt (2026-05-16) — "21 个角点里 1 个 fail，剩下 20 个继续". Matches the user's stated concurrency answer ("Maestro/LSF 已有 job 分发"): if Maestro is already dispatching the 21 corners in parallel, the orchestrator's job ends at "I submitted, here's the per-corner result map" — fail granularity has to be per-corner to be useful. Sequential item execution keeps the progress log human-readable (one item-finished log line at a time) and avoids the orchestrator reimplementing the LSF semantics Maestro already handles.

**Implementation:** Per-corner status comes from re-using Phase 1's `pvtCollIterateResults` — it already classifies each (corner, test) row as `ok` / `sim_err` / `eval_err` / `unknown`. Orchestrator wraps it in the new `pvtRunnerCollectHistory` SKILL verb after each item completes, hands the JSON envelope to `pvt ingest`, then walks the ingested rows for the failure-strategy decision.

---

## #52 — Phase 3A §1: failure-strategy plugin architecture; v1 ships framework + 1 placeholder
_Date: 2026-05-16_

**Decision:** Strategies are Python classes (`Strategy` base with `name`, `max_attempts`, `params`, `apply(ctx)`, `revert(ctx)`) discovered from both `simkit/strategies/` (built-in) and `<project>/strategies/*.py` (user-defined). The orchestrator wires the strategy chain from each item's `on_failure.strategies` array; chain entries shaped `{"name": "...", "max_attempts": int, ...params}` instantiate matching classes. v1 ships **only one built-in strategy** — `naive_retry` (no intervention, just re-run up to N times; covers transient license / disk / scheduler hiccups). The two production-relevant built-ins the user named (`gmin_bump`, `trans_pss_ic`) are deferred to v1.1.

**Why:** Per-2026-05-16 probe, Spectre `gmin` and PSS initial-condition fields live in the `asi*` namespace (298 functions surfaced), not `axl*`. Locking those two strategies in v1 would require a fresh probe phase against asiAddSimOption / asiChangeAnalysis / asiAddAnalysisOption + their PSS-specific arity, which we haven't done. User confirmed (2026-05-16): "v1 只出框架，策略推 v1.1". This keeps Phase 3A v1 unblocked and lets the user dogfood the orchestrator skeleton immediately. v1.1 gets its own `asi*` probe → 2 built-in strategies + a worked-example doc on writing a custom one. The plugin architecture is in v1 from day one so user strategies can land before the asi* probe completes (e.g., a user-authored "retry with longer time step" strategy needs no asi* APIs).

**Alternatives considered:** (a) Ship all 3 strategies in v1 — rejected, requires 1-3 more days of asi* probing user wasn't willing to wait for. (b) Ship 0 strategies, only framework — rejected, leaves the chain empty and the architecture untested; `naive_retry` is cheap and exercises the framework on real failures. (c) Built-ins only, no user-plugin discovery — rejected, user explicitly asked for user-extensible strategies.

---

## #53 — Phase 3A §1 probe: SKILL run-control + simulator-interface API map
_Date: 2026-05-16_

**Decision (catalog, not a behaviour choice):** Records the API map discovered by the 2026-05-16 skillbridge probe against `fnxSession0` (sim_yusheng/Test/maestro/config). Captured here so the §3 SKILL bridge implementation has the verified contract instead of guessing from PDFs.

**Run-control (`axl*` namespace):**
- `sdb = axlGetMainSetupDB(session_name)` — string session name → setupDB int handle. Required first step for almost every other call.
- `axlGetEnabledTests(sdb)` → `[[handle, name], ...]` per enabled test.
- `axlGetTest(sdb, name)` → test int handle; `axlGetTestName(h)` → name; `axlGetEnabled(h)` / `axlSetEnabled(h, t/nil)` → per-test on/off (set returns `1` on success).
- `axlRunAllTests(sdb, historyName)` sync; `axlRunAllTestsWithCallback(sdb, historyName, callback_fn, ...)` async (orchestrator's main path). At least 3 args required; remaining arity to nail in §3 first implementation (open decision 10.2).
- `axlGetRunStatus(session_string)` → `[code, sub]`. Idle = `[0, 0]`; running/done/failed transitions to be observed during §3 dogfood (open decision 10.1).
- `axlGetResultsLocation(sdb)` → fs path Maestro writes results to (same path Phase 1 collector already knows).
- `axlGetHistory(sdb)` + `axlGetHistoryResults(h)` + `axlGetHistoryName(h)` — post-run inspection.
- `axlStop / axlStopAll / axlStopJob` — abort paths.
- `axlGetTestToolArgs(h)` → `[[key, value], ...]` (sim engine, lib/cell/view, path). `axlSetTestToolArgs(h, args)` writes.

**Simulator interface (`asi*` namespace, 298 functions — strategy-side):**
- `asiAddSimOption` — Spectre simulator options (e.g. `gmin`). Likely target for v1.1 `gmin_bump` strategy. Exact signature to probe during v1.1 kickoff.
- `asiChangeAnalysis` / `asiAddAnalysisOption` — analysis-level fields (PSS `ic` lives here). v1.1 `trans_pss_ic` strategy will use this path.
- `asiAddEnvOption`, `asiAddDesignVarList`, `asiAddModelLibSelection` — adjacent knobs surfaced but not needed for v1.

**Session-detection rule (gotcha from this probe):** skillbridge runs in CIW context. `axlGetWindowSession()` from CIW returns `nil` even when Maestro window is open. Workaround: the user passes the session name explicitly to every helper (same pattern Phase 2 / 3B helpers already use). Orchestrator CLI requires `--session NAME` or `PVT_SESSION` env var.

**Bridge-restart gotcha (re-documented in `reference_skillbridge_recovery.md`):** bare `(pyStartServer)` defaults to `python` binary which doesn't exist on this host. Must call `(pyStartServer ?python "/usr/bin/python3")` or load `sbStart.il`. Surfaced during this probe when first `pyKillServer / pyStartServer` cycle exited with code 127.

---

## #54 — Phase 3A v1 dogfood: axlRunAllTests is async, modal-dialog wedges bridge
_Date: 2026-05-16_

**Decision (captured findings, not a behavior change):** Records the observed runtime characteristics of `axlRunAllTests` and the operational pitfall that fell out of the Phase 3A §6 live dogfood. v1.1's first task is to fix `pvtRunnerRun` to actually wait.

**Findings:**

1. **`axlRunAllTests(sess, x)` is fire-and-forget async.** Probed signature is `(string, string)` (type template `tt`, open decision 10.2 closed). The SKILL call returns immediately after dispatching the run to Maestro's job runner (LSF / local pool). Spectre processes continue to run in the background for the actual sim duration.
2. **Second arg is NOT honoured as a history name.** Maestro always auto-names new entries `Interactive.<N+1>`. v1 workaround: post-run `axlSetHistoryName(handle, desiredName)` via `axlGetCurrentHistory(sess)`.
3. **`axlGetRunStatus(sess)` returns a `[code, sub]` pair.** Observed values during dogfood: `[0,0]` = idle (initial state, also post-completion); `[1,1]` = post-cached-run (no real sim); `[5,9]` = post-real-run-completion. Full state-machine still under-observed; v1 uses "transition from any non-[0,0] back to [0,0]" as the "done" heuristic. Open decision 10.1 partially closed; full code map deferred.
4. **PvtSave (read-only) is safe to call during the async tail.** This is why S1 dogfood saw "PvtSave succeeded immediately, schema_version 2 dump landed cleanly" even though the sim was still running in the background.
5. **Any MUTATING op on the same setupdb during the async tail (axlRemoveElement, axlSetEnabled, axlPutCorner, etc.) can pop a modal `ADE Assembler Message 2423` dialog** that says "setupdb handle … has been temporarily locked … this history item is actively running. Wait for the temporary lock to end, before trying again." That modal blocks ALL skillbridge calls until the user clicks Close. `pyKillServer`/`pyStartServer` cycles DO NOT help while the dialog is up. Cost is repeated user-attention round-trips that look like the bridge is corrupted.
6. **`axlRemoveElement` on a history that's still "actively running" triggers (5).** During cleanup of S1+S2 orphan histories, removing the orch_s1_* entry (which had PvtSave dump but the underlying run was still finalising) wedged the bridge. The 4 orphan Interactive.NN entries deleted cleanly because they were truly idle.
7. **Mitigation rule (memory-pinned in `feedback_axl_run_async_wait_for_idle.md`):** before any destructive op post-`axlRunAllTests`, poll `axlGetRunStatus` until idle. Mirror the rule in `pvtRunnerRunBlocking` (v1.1 task — wraps `axlRunAllTests` + poll-to-idle + rename). When the bridge appears wedged, FIRST ask user to check Maestro for popups, BEFORE assuming bridge corruption.
8. **`pvt corners push` semantics for non-existent corner names: it ADDS, doesn't REPLACE.** S2 pushed a 3-row union and the session ended up with 6 corners (3 original + 3 new). Restore via re-push of the snapshot didn't drop the new ones either. v1.1 likely needs an explicit "replace mode" push verb. Phase 2 spec §4.2 will need clarification on this if it's intentional.

**S1 + S2 dogfood acceptance:**
- ✅ snapshot/enable_only/run/rename/PvtSave/ingest/pvt-list round-trip end-to-end (S1; result row `c955d584-…` visible in DB alongside Phase 3B v1.3/v1.4 dogfood runs).
- ⚠️ Real-Spectre wall-clock NOT measured (S2 reported "pending" status because polling-to-idle is unimplemented).
- ⚠️ Cleanup left 2 orphan histories (`orch_s1_s1_baseline_TT_1778932203_1`, `orch_s2_s2_temp_sweep_1778932284_1`) — recommend user clears via Maestro GUI History panel (right-click delete).

**v1.1 Phase 3A backlog (immediate):**
- `pvtRunnerRunBlocking` with poll-to-idle (resolves the misleading "Run" semantics). **DONE 2026-05-16 via #55 — but discriminator turned out to be blind on fnxSession0; see #55 for the residual gap and v1.2 plan.**
- `pvt corners push --replace` mode (or clarify the v1 ADD-semantics).
- Strategy chain wired into `execute()` per-corner failure detection (per #52 plumbing already in place).
- `gmin_bump` + `trans_pss_ic` built-in strategies after asi* probe phase.


## #55 — Phase 3A v1.1 #1: split Submit/Rename + Python poll-to-idle; `axlGetRunStatus` discriminator is blind
_Date: 2026-05-16_

**Decision:** v1's monolithic `pvtRunnerRun` (`axlRunAllTests` + immediate `axlSetHistoryName`) is split into `pvtRunnerSubmit` (dispatch-only) and `pvtRunnerRename` (rename-current-history-only). The Python orchestrator's `pvt_runner_run` now wraps the split with a poll-to-idle state machine using `axlGetRunStatus` as the discriminator. Tier-1 grows +13 cases for the validation paths; live-verified cached path returns cleanly in 3.4s with no ASSEMBLER-2423.

**Why the split:** Per DECISIONS #54, `axlSetHistoryName` against a still-finalising setupdb is the documented route to ASSEMBLER-2423. The Submit/Rename split gives the Python orchestrator a seam to insert "wait until safe" between dispatch and rename. Putting the poll loop in Python (vs SKILL `sleep`) keeps the bridge IPC responsive (per `reference_skillbridge_recovery.md`: never kill mid-call), allows sub-second poll intervals, and gives Python ownership of timeout/cancellation.

**State-machine shape (`pvt_runner_run`):**
- Submit, optional `initial_wait_sec` sleep, then loop:
- Each iteration: `time.sleep(poll_interval)` → call `pvt_runner_get_status`.
- `sawNonIdle` flag flips on first non-`[0,0]` read.
- Exit on `sawNonIdle and idle_streak >= idle_confirm_reads` (real run done) OR `not sawNonIdle and idle_streak >= dispatch_grace_reads` (cached / no-op completion).
- Optional `post_idle_quiesce_sec` sleep before `pvtRunnerRename` to let Maestro release the setupdb lock.
- Timeout raises `SkillBridgeError(pvt_runner_timeout)` without renaming.

**Handle-0 RuntimeError translation:** `axlGetRunStatus(sess)` throws an *uncatchable* C-level `*Error* error: Cannot find a setup database entry for handle 0.` when there is no active in-flight run record. SKILL `errset` / `errsetstring` / `(errset … t)` / a hypothetical `errSetSeverityFatal` flag ALL fail to trap it (probed 2026-05-16). The Python wrapper catches the `RuntimeError` and translates the "handle 0" message to a synthetic `(0, 0)` because semantically that means "no run in flight".

**Residual gap (live-verified): `axlGetRunStatus` is blind on fnxSession0.** During the 2026-05-16 dogfood:
- Cached path: state machine takes `dispatch_grace` branch, returns in ~3s, rename + delete clean. ✅
- Real-Spectre path (TT_v11verify, temp=56 forced fresh sim): submit dispatched the run; `axlGetRunStatus` returned `[0,0]` consistently for **90+ seconds** while Spectre was visibly still running (verified via `axlGetCurrentHistory` showing a fresh `Interactive.0` handle that never advanced to a "completed" state during the window). State machine exited via `dispatch_grace` at ~6s. The rename DID succeed mid-sim — Maestro accepts rename on the new history even while in-flight — BUT subsequent `pvtRunnerDeleteHistory` hit ASSEMBLER-2423 because the setupdb lock was still active. So the architectural improvement landed, but the polling discriminator is **insufficient on this session's installation**.

**Why `axlGetRunStatus` is blind here:** unknown. DECISIONS #54 observed it returning `[1,1]` / `[5,9]` on the SAME session days earlier. Possibly session-state dependent (was the SKILL `sleep` test corrupting something? Was there a per-session initialisation that drifted?). Probed `axlGetRunMode`/`axlIsRunning`/`axlGetRunningTests` — none exist. Other candidate APIs (`axlGetCurrentHistory` handle introspection, slot access on the history handle) don't expose an in-flight signal either.

**Mitigation in v1.1:** new kwargs `initial_wait_sec` (sleep BEFORE first poll) and `post_idle_quiesce_sec` (sleep AFTER loop exits, BEFORE rename). Defaults 0 to keep cached-path fast. Callers who know their sims take ≥ N seconds can pass `initial_wait_sec=N` as a manual override; the loop then doesn't get a chance to exit prematurely. NOT ideal but pragmatic — the architectural split is the real value-add of v1.1; the discriminator can swap independently.

**Robust discriminator (v1.2 task, priority HIGH):** reuse the Phase 1 collector's `_pvtCollWalkRdb` to count rows with status `'running` in the current history's rdb. When count drops to 0 AND stays 0 for `idle_confirm_reads` polls, the run is truly done. Implementation sketch: new SKILL helper `pvtRunnerCountRunning(sess)` that opens the current-history rdb via `maeReadResDB`, walks the test list, returns count of `tst->status == 'running`. Plumbing exists (collector loads it already); just need to expose count and wire into the Python state machine as a parallel signal.

**Code surface changes:**
- SKILL: `pvtRunnerSubmit` (+13 lines), `pvtRunnerRename` (+25 lines), `pvtRunnerRun` slimmed to a thin wrapper over Submit+Rename for back-compat CIW use.
- Python: `pvt_runner_run` rewritten with state machine + 8 kwargs; `pvt_runner_submit` / `pvt_runner_rename` exposed; `pvt_runner_get_status` gains handle-0 translation.
- Tests: +13 Tier-1 SKILL cases (validation paths only); +9 Python state-machine cases including timeout, idle-confirm-requires-consecutive, dispatch_grace, handle-0 translation, empty-history rejection. Python suite 835 → 868 (+33). SKILL Tier-1 394/1 → 407/1.
- Orchestrator: `execute()` gains `run_kwargs` passthrough for poll tuning.

**Pre-existing breakage surfaced:** the `(load runTests.il)` atomic driver path is broken in this dev tree (fails at line 168 = first inner load) regardless of v1.1 changes; the alternative bypass-loop (load each file individually + `(pvtTestRun)`) gives a clean 407/1. Driver fix deferred — not v1.1 #1 scope.

**Live cleanup owed to user:** the real-Spectre verify and the follow-up probe BOTH left a stuck-in-flight `Interactive.0` plus a renamed `orch_v11_real_1778936686`. Both are still locked by Maestro (verified via the ASSEMBLER-2423 trip during attempted delete). User clears via Maestro GUI History panel (right-click delete) once the sim is truly done. Cornerwise the session is back to the 3-row baseline (`TT` / `TT_pvt` / `TT_2p5G`).

**CORRECTION 2026-05-16 (post-#56 diagnosis):** The "`axlGetRunStatus` is blind on this session" framing above is **almost certainly wrong**. Cross-session debugging with a parallel agent found the AXL worker was never dispatching Spectre at all — Maestro's `runICRP21` launcher was `cd`-ing to `/home/yusheng/cadence_work/simkit_p3b_dogfood` (no `cds.lib`), where session config fails with `asiGet: no applicable method`, the worker exits, the history sits in "pending" forever, AND Maestro keeps the `actively running` flag set so destructive ops trip ASSEMBLER-2423. The 90-second `[0,0]` polling result is actually **semantically correct**: there really is no run in flight — but for a config-error reason, not a "discriminator blind" reason. The wrong `cd` path came from `skill_bridge.py`'s `_prep` calling `changeWorkingDir(simkit_p3b_dogfood)` for the prior `pvt_corners_pull`, which Maestro then snapshotted at next `axlRunAllTests`. Fix is `_prep` cwd snapshot/restore (DECISIONS #56). Re-verify against a clean fnxSession0 should show `axlGetRunStatus` returning the [1,1]/[5,9] codes originally documented in DECISIONS #54.


## #56 — Phase 3A v1.1 #1 root-cause correction: `_prep` cwd-leak crashes the AXL worker
_Date: 2026-05-16_

**Decision:** `skill_bridge.py` verbs that call `changeWorkingDir` (`_prep`, `_prep_measure`, `pvt_save`) now snapshot the parent Virtuoso's working directory on entry and restore it on exit. Implementation: convert `_prep` / `_prep_measure` from plain procedures to context managers; wrap `pvt_save`'s body in a new `_restore_cwd(ws)` context manager that saves cwd via `getWorkingDir()` and restores via `changeWorkingDir(orig)` in the `finally` clause. Verbs are migrated from `_prep(ws, path)` + body → `with _prep(ws, path): body`.

**Why:** `axlRunAllTests` snapshots the parent Virtuoso's current working directory into the generated `runICRP<N>` launcher script (Maestro creates one per dispatch). If that cwd doesn't contain `cds.lib`, the AXL worker fails to find any library — `ddUpdateLibList` warning, `asiGet: no applicable method` error during session configuration — Spectre never dispatches, the history sits in `pending` forever, AND Maestro keeps the "actively running" flag set so any subsequent cleanup op (delete history, rename) trips the ASSEMBLER-2423 modal. **`changeWorkingDir` for one verb leaks into the cwd inherited by the NEXT verb's dispatch.**

**Diagnostic incident (2026-05-16, parallel-agent investigation):** the v1.1 #1 live verify (DECISIONS #55) ran:
1. `pvt_corners_pull(baseline)` — `_prep('/home/yusheng/cadence_work/simkit_p3b_dogfood/.pvtproject')` → `changeWorkingDir(simkit_p3b_dogfood)` (this is a `.pvtproject` data dir, NOT a Virtuoso project dir, so no `cds.lib`)
2. `pvt_runner_run(...)` → `axlRunAllTests` → Maestro snapshots `simkit_p3b_dogfood` into `runICRP21` → worker boots there → AXL config fails (`asiGet: no applicable method for the classes ... list(symbol)`) → no spectre dispatched
3. `axlGetRunStatus(sess)` returns `[0,0]` (correctly: no in-flight run) → state machine exits via dispatch_grace
4. `pvtRunnerRename` succeeds (Maestro accepts rename on the empty `Interactive.<N>` shell)
5. `pvtRunnerDeleteHistory` trips ASSEMBLER-2423 (Maestro thinks the run is still active because the worker never explicitly reported termination — it just crashed)

The parallel agent's smoking gun was `cat runICRP21 | grep '^cd '` showing literal `cd /home/yusheng/cadence_work/simkit_p3b_dogfood`. Cross-verified via skillbridge: `ddGetObj("sim_yusheng")~>readPath` returned `/home/yusheng/cadence_work/Test/workarea/sim_yusheng`, confirming the correct workdir is `Test/workarea`, which is also where the parent Virtuoso's `$PWD` was at the time of inspection (just not at the time of axlRunAllTests). Manual GUI runs work because the user clicks Run from a state where parent `$PWD` is still `Test/workarea`.

**Why the prior Phase 3A v1 dogfood "worked" anyway:** S1 succeeded because the corner was already cached — no real Spectre dispatch needed, so the worker config failure didn't bite. S2's "Real-Spectre wall-clock NOT measured" symptom (DECISIONS #54) is the same cwd-leak in disguise: PvtSave returned data because it walks the in-memory rdb (which still had Phase 3B v1.x runs in it), and the "real" sim never started.

**Surface changes:**
- `python/simkit/skill_bridge.py`: add `_restore_cwd(ws)` ctx manager + `from contextlib import contextmanager`; convert `_prep` / `_prep_measure` to `@contextmanager` form; wrap `pvt_corners_pull` / `pvt_corners_push` / `pvt_measure_push` / `pvt_measure_pull` / `pvt_measure_restore` / `pvt_save` bodies in `with` blocks.
- Tests: `_make_mock_ws` in both `test_skill_bridge.py` and `test_skill_bridge_measure.py` gain a `getWorkingDir` mock returning sentinel `/orig/cwd/from/parent/virtuoso`. 4 existing `assert_called_once_with` cwd-pinning tests updated to assert `[enter, restore]` 2-call contract. +3 new tests: `pvt_save` happy-path restore, `pvt_save` restore-on-error, `pvt_corners_pull` restore-on-error. Python suite 868 → 871 / 0.
- SKILL: untouched (the leak is purely on the Python orchestration side).

**Re-verify owed:** with the cwd fix in place + bridge recovered, run S1 + S2 again on fnxSession0. Expectation: `runICRP<N>` should now `cd Test/workarea` (or wherever parent Virtuoso started), worker boots cleanly, Spectre dispatches, `axlGetRunStatus` returns the [1,1]/[5,9] codes DECISIONS #54 originally observed, cleanup-via-delete-history succeeds without modal. If verify holds, DECISIONS #55's "discriminator blindness" framing retires and v1.2 #1 (rdb-walker discriminator) drops from HIGH to "robustness improvement, do when convenient."

**Re-verify result 2026-05-16 (LATER, post-bridge-recovery):** CWD FIX CONFIRMED WORKING. Job27.log was written to `/home/yusheng/cadence_work/Test/workarea/logs_yusheng/logs0/` (the correct dir with `cds.lib`), NOT `simkit_p3b_dogfood/logs_*/`. AXL worker boots cleanly. **Spectre actually dispatches** (vs. prior "no spectre processes" symptom) — proven by the per-corner netlist dir `.tmpADEDir_yusheng/Test/sim_yusheng_Test_config_spectre/netlist/input.scs` materialising and Spectre running far enough to emit its own error (SFE-73 on a synthetic test corner — unrelated to simkit, my TT_v11verify model substitution issue).

Three discoveries from the re-verify:
1. **Discriminator vindicated.** `axlGetRunStatus` returning `[0,0]` was correct all along — no in-flight run = `[0,0]`. v1.2 #1 (rdb-walker discriminator) is **demoted from HIGH to "robustness improvement, do when convenient"**, contingent on observing a successful real run's transitions.
2. **ASSEMBLER-2423 on cleanup is a SEPARATE problem.** Even when Spectre exits (success OR error), Maestro keeps the history's `actively running` flag set until the worker formally finishes — a delete-history op meanwhile hits the lock. This is Maestro flag-stickiness, *independent* of the discriminator. A perfect "is-running" check wouldn't help. The right fix is one of: (a) wait for the worker process to actually exit (`ps -p <pid>` poll), (b) check `axlGetCurrentHistory`'s post-run state for a "done" marker, (c) catch the error and treat as "completed with error" instead of treating delete failure as crash. v1.2 takes this as a separate ticket.
3. **The S2 "real-Spectre wall-clock NOT measured" symptom (DECISIONS #54)** retroactively explains itself: cwd-leak prevented worker boot, no Spectre, hence no wall-clock to measure. With #56 fixed, a clean real-Spectre dogfood is finally possible.

**Robustness note:** the fix is "polite Python" (every entry has a matching exit) but doesn't fix the underlying Maestro quirk (cwd snapshot at dispatch time is non-obvious global state). If a user runs CIW commands that `changeWorkingDir` between simkit verbs, the cwd they end up in IS what Maestro will use. Document expectation: *the parent Virtuoso's cwd at any moment between simkit verbs should be a dir that contains `cds.lib`*. The fix means simkit never violates this; the user's own scripts still can.


## #57 — Phase 3A v1.2: trans→PSS IC piping is a workflow concern (`ic_from`), not a Strategy
_Date: 2026-05-16_

**Decision:** Cross-item IC piping (trans precursor → PSS/HB consumer) is expressed as an item-level field `ic_from: {item, file, mode}` on the consumer item, **not** as a failure-recovery `Strategy`. v1.2 ships this as `review_schema_version: 2`; v1 sidecars load unchanged. Original `trans_pss_ic` strategy entry in DECISIONS #52 / spec §9 is superseded.

**Why:** When the user described the workflow ("跑PSS之前，先跑一组trans的PVT，把每个corner的spectre.fc当作PSS的readns读入"), it became clear this is an **always-on prerequisite**, not a "PSS failed → retry with IC" recovery. Putting it in the Strategy chain would mean every PSS would either need a useless first-attempt-without-IC just to "fail" and trigger the strategy, OR the Strategy framework would need a new "always-apply" mode that's essentially "be an item dependency". Both are awkward. A dedicated `ic_from` field on the consumer item is conceptually clean: it parallels `bundle` (defines what the item RUNS WITH) the way `bundle` defines what the item MEASURES WITH.

**Design picks (user-confirmed 2026-05-16):**

| Question | Pick | Rationale |
|---|---|---|
| Which IC files? | `.fc`, `.ic`, `.dc` all supported | User said "spectre.ic 或者 spectre.fc... 再加一个 spectre.dc"; covers PSS readns hint (typical `.fc` / `.dc`) + trans-handoff hard IC (typical `.ic`). |
| Which read modes? | `readns` (soft) + `readic` (hard) | Spectre's two IC-read modes; let user pair freely per item. |
| Corner mapping | Strict same-union | v1 simplicity: zero-ambiguity per-corner pairing by index. Different-union mapping deferred. |
| Source corner failed | Naked retry (no IC) + warning | Matches DECISIONS #51 per-corner skip philosophy; PSS without IC may still converge. |
| Sidecar shape | Field on consumer item, reference source by `item` name | Source item must precede consumer in `items[]` order; both items stay independently ingestible (you get a separate slice for the trans + a separate slice for the PSS). |

**Per-corner path resolution (probed live against `simkit_verify`, 2026-05-16):**

```
<axlGetResultsLocation(sdb)>/<history_name>/<corner_idx_1based>/<test_name>/<sim_subdir>/spectre.{fc,ic,dc}
```

- `axlGetResultsLocation` takes an integer (sdb handle), NOT a session name string (one-off API quirk; sibling `axl*Name` family takes the session name). Probed by passing sdb integer; returns `/<simdir>/results/maestro`.
- `axlGetCorners(sdb)` returns `(handle . names-list)` — a 2-element list where `car` is a corners-collection handle and `cadr` is the bare name list. Use `cadr` for ordering.
- `corner_idx_1based` matches `axlGetCorners(sdb)` order at the time the source item ran. Orchestrator captures this when pushing the source item's union.
- `sim_subdir` is `netlist` for Spectre, `psf` for Alps (国产 simulator, work env, TBC at first work-env dogfood). Resolver tries known subdirs in declared order, picks first that has the file. Sidecar `ic_from.subdir` override stays as an escape hatch when a new simulator surfaces.

**SKILL surface:** new helpers `pvtRunnerSetIcSource(sess, testName, icPath, mode)` + `pvtRunnerClearIcSource(sess, testName, mode, prevValue)` write a Spectre CLI arg into the test's `additionalArgs` simulator option via `asiSetSimOptionVal(asi, "additionalArgs", "+nodeset <path>")` (for readns) or `"+ic <path>"` (for readic). The path through `additionalArgs` was chosen after an initial mis-design that tried setting `asiSetSimOptionVal(asi, "readns", path)` — readns/readic are NOT in the 133-option Spectre Options form (which holds reltol/gmin/temp/iabstol/etc.); they're netlist-syntax keywords, not option-form fields. `additionalArgs` always exists as a default sim option and accepts arbitrary Spectre CLI args, so the orchestrator can write `+nodeset <abs path>` / `+ic <abs path>` with **zero one-time UI setup** required from the user. Live-verified 2026-05-16 on `fnxSession0`: round-trip set readns → readic → clear restores additionalArgs to its original value.

**v1 caveat:** `additionalArgs` is shared with whatever else the user might put there. We snapshot the prev value on entry and pass it back to ClearIcSource for restore — but if the user relies on `additionalArgs` for OTHER per-test needs (logging level, debug flags) and runs ic_from concurrently, our write clobbers theirs for the duration of the consumer item's run. v1.2.1 follow-up if dogfood shows this matters: append-with-marker instead of replace-wholesale.

---

**Stage-2 follow-up 2026-05-16: true per-corner iteration** — the initial stage-1 had a "all corners share corner-1's IC" limitation because the orchestrator's existing `axlRunAllTests` path submitted the whole batch at once, so we could only set ONE additionalArgs value per item. Stage-2 fixes that:

1. New SKILL helpers (pvtRunner.il):
   - `pvtRunnerSnapshotCornersEnable(sess)` → list of `(name, enabled)` pairs in `axlGetCorners(sdb)` order (which matches the /1, /2, ... result-dir numbering).
   - `pvtRunnerEnableCornerByIndex(sess, idx)` → disables every corner except 1-based idx via `axlSetEnabled(cornerHandle, t/nil)`.
   - `pvtRunnerRestoreCornersEnable(sess, snap)` → apply snapshot.

2. Orchestrator (`_execute_per_corner_item`): for items with `ic_from`, wraps a per-corner loop in snapshot/restore. Each iteration: resolve per-corner IC → set additionalArgs → enable one corner → axlRunAllTests → PvtSave → ingest → clear additionalArgs. Per-corner failures don't abort the loop (recorded in notes). Live-verified on `fnxSession0` (TT/TT_pvt/TT_2p5G): snapshot returns `[['TT', None], ['TT_pvt', True], ['TT_2p5G', None]]`, enable-by-index round-trips cleanly, bad-idx rejection (0, -1, 999) all surface as `pvt_validation`.

3. Trade-off: per-corner = N submits instead of 1. For N=5-10 corner real PSS sweeps this is slow vs. cached batch (each submit pays Maestro's per-corner overhead). Acceptable for v1.2 because IC injection requires it; if dogfood reveals this is the bottleneck, v1.3 candidate is a per-corner-IC-aware single-batch netlist injection.

4. Fallback semantics: if upstream item has no recorded history (e.g. trans crashed pre-PvtSave), consumer falls back to batch-without-IC so the run still produces *something* observable. If results_root can't be resolved (.pvtproject in unexpected layout), same fallback. Per-corner missing-IC = naked retry for that corner (not whole item).

5. Stage-2 tests:
   - SKILL Tier-1: +7 cases (3 existence + 4 idx-validation for the new helpers) → 419 → 426 / 1. (A "positive-int-accepts-validation" smoke test was dropped because calling with `"fakeSession"` reaches `axlGetMainSetupDB` which raises a C-level error errset can't trap — same class as DECISIONS #55; the positive-int path is covered live by the bridge round-trip on fnxSession0 above.)
   - Python: rewrote 4 `ExecuteIcFromTests` cases to assert per-corner semantics (N set / N clear / N enable_corner_by_index / N+1 runs; snapshot+restore once; missing-IC partial fallback; snapshot restored even on run errors). Suite stays 912 / 0.

Stage-1's "v1 limitation: all corners share corner-1" is now gone. The ic_from feature delivers what the original user request asked for: "不同的corner读取不同的ic condition."



**Why bump schema_version (not additive):** the orchestrator's per-corner control loop differs structurally when `ic_from` is present — it iterates corners itself and calls SetIcSource/ClearIcSource around each, vs. v1's "submit one axlRunAllTests for the whole item, walk away." Loaders running v1 code against a v2 sidecar would silently ignore `ic_from` and submit the consumer item with no IC — which would converge-fail every PSS corner. Version bump makes the failure loud (loader rejects unknown schema).

**Promoted from v1.3 to v1.2 by user request (real workflow):** v1.2 backlog items (rdb-walker discriminator demoted in #56; `pvt corners push --replace`; per-corner verdict + strategy chain) reslot to v1.3.

---

**Stage-3 (v1.3) supersedes stage-2's per-corner-submit pattern.** User flagged stage-2's UX cost on 2026-05-16: "这样 cadence 里面看见的仿真结果是不是只能看见一个一个的了？" — N corners = N Maestro history entries instead of 1, which makes ViVA waveform comparison + results table browsing painful at real-bench scale (PSS sweeps with 20+ corners). Investigation found a clean alternative: **Maestro pre-run scripts** (`axlImportPreRunScript`) fire per-(test, sub-corner) in a worker virtuoso VM right before each point's netlist is generated. From inside that script we can call `asiSetSimOptionVal` on the per-test asi session to write `additionalArgs="+nodeset <abs_path>"` (or `"+ic <abs_path>"`) before Spectre sees the netlist.

**v1.3 architecture:**

1. Orchestrator side, ONCE per consumer item: enumerate sub-corner names via `explode(union)`, resolve each one's IC path from the upstream history's `<results>/<hist>/<sub_corner_idx>/<test>/netlist/spectre.<kind>`, build a `{sub_corner_name → "+nodeset <path>"}` map.
2. Render a self-contained SKILL pre-run script with the map embedded as a literal `(cons "name" "+nodeset path")` list. No JSON parsing at runtime; no dependency on simkit code being loaded in worker VM.
3. Snapshot each test's prior pre-run script (`axlGetPreRunScript`).
4. Install our script on each test in the consumer item via `pvtRunnerInstallPreRunScript` (wraps `axlImportPreRunScript` + `axlSetPreRunScriptEnabled t`).
5. ONE `axlRunAllTests` submit → single history entry → all N corners in one results table.
6. PvtSave + ingest the single batch history.
7. Cleanup: disable our pre-run on each test, reattach user's original if any, clear `additionalArgs` baseline.

**Live-probed on `fnxSession0` (2026-05-16):**
- Pre-run fires once per sub-corner with the FULL sub-corner name (TT_pvt explodes into TT_pvt_0..5 — each gets its own firing). 9 distinct firings for a 1+6+1 union, including one pre-flight call with `cornerName=""` that the script must guard against (asi is nil at that point).
- Worker VM has `asiGetCurrentSession`, `asiSetSimOptionVal`, `asiGetSimOptionVal`; set+get round-trip confirmed.
- Pre-run fires BEFORE netlist generation — script's `additionalArgs` write is picked up by Maestro at netlist-gen time.
- Pre-run errors are FATAL to that corner's run (Maestro aborts the point) — script always wraps SKILL calls in `errset` and returns `t` unconditionally.

**Trade-off vs. stage-2 per-corner-submit:**

| Dimension | stage-2 per-corner submit | stage-3 pre-run script (v1.3) |
|---|---|---|
| Maestro histories | N entries | 1 entry ✅ |
| ViVA / Results table | N to browse | one consolidated ✅ |
| Per-run overhead | N × per-corner Maestro dispatch | native single batch ✅ |
| Implementation complexity | medium (corner enable mask + sequential loop) | medium (cross-VM SKILL gen + state snapshot/restore) |
| Per-corner error isolation | partial fails skip that corner | partial fails skip via assoc miss (corner runs naked) |
| Cleanup hygiene | restore corner enable mask | disable our pre-run + reattach user's prior + clear additionalArgs |

**Surface changes vs. stage-2:**
- `python/simkit/pre_run_script.py` NEW: `PreRunSpec`, `render_pre_run_script`, `write_pre_run_script`, `build_corner_arg_map`. Pure-Python + filesystem; 18 unit tests.
- `python/simkit/orchestrator.py`: `_execute_per_corner_item` → `_execute_ic_chained_item`. Single batch, embeds union explode order to compute sub_corner_name → corner_idx mapping.
- `python/simkit/skill_bridge.py`: `pvt_runner_install_pre_run_script` / `pvt_runner_disable_pre_run_script` / `pvt_runner_get_pre_run_script` wrappers (alongside the stage-1 SetIcSource/ClearIcSource which now only run on cleanup).
- `skill/pvtRunner.il`: `pvtRunnerInstallPreRunScript` / `pvtRunnerDisablePreRunScript` / `pvtRunnerGetPreRunScript`. Stage-2's `pvtRunnerSnapshotCornersEnable` / `pvtRunnerEnableCornerByIndex` / `pvtRunnerRestoreCornersEnable` stay (general-purpose; just not on the v1.3 ic_from critical path).
- `tests/test_pre_run_script.py` NEW: 18 cases. `tests/test_orchestrator.py::ExecuteIcFromTests` rewritten for single-batch + pre-run shape (5 cases vs prior 4): happy path, upstream-failed fallback, partial-IC corner omitted from script, cleanup runs on batch error, user's prior pre-run reattached.

Final tally: Python 912 / 0 → 931 / 0 (+19); SKILL Tier-1 426/1 → +8 (3 existence + 5 validation for the new pre-run helpers).

**Known v1.3 gap (v1.3.1 candidate):** if the user had a non-empty `additionalArgs` in their Spectre Options form BEFORE the orchestrator runs, our pre-run script overwrites it per-corner; cleanup clears to `""` regardless of the prior state. Captures `prior_scripts` (pre-run files) but not `prior_additionalArgs` (the simoption value). Fix is straightforward — bridge.pvt_runner_get_pre_run_script-style getter for additionalArgs + restore on cleanup. Defer until first dogfood reveals it bites.




---

## #58 — Phase 3A v1.3 closeout: sdb-handle pass-through to defang post-run session-focus loss
_Date: 2026-05-17_

**Decision:** Make every `pvtRunner*` SKILL helper accept its `sess` argument as either a string (session name, e.g. `"fnxSession0"`) **OR** an integer (sdb handle previously cached by the caller). Two internal helpers `_pvtRunnerGetSdb` and `_pvtRunnerResolveSession` polymorphic-dispatch on `(integerp sess)`. The 5 helpers whose underlying axl* call genuinely needs the session NAME (`pvtRunnerSubmit` → `axlRunAllTests`, `pvtRunnerRename` → `axlGetCurrentHistory`, `pvtRunnerGetStatus` → `axlGetRunStatus`, `pvtRunnerRun` → composite, `pvtRunnerInstall/Disable/GetPreRunScript` → `axlImport/Set/GetPreRunScript`) guard at entry via a new `_pvtRunnerRequireSessName` helper that returns a clear pvtErr if an int slipped through. Python side: new `skill_bridge.get_sdb(name)` helper that returns the sdb int once + module docstring instructing callers to pass it as `session=` to all read-side wrappers. Wedge detection added: `get_sdb` translates the three known bridge-failure RuntimeError/ValueError patterns ("not enough values to unpack" → `bridge_wedge`; "The server unexpectedly died" → `bridge_dead`; "Cannot find an active session" → `session_focus_lost`) into `SkillBridgeError` with explicit recovery instructions instead of cryptic tracebacks.

**Why:** During the v1.3 dogfood retry on 2026-05-17 the bridge wedged repeatedly. The root cause is window-focus-keyed session registration on Cadence's side: after `axlRunAllTests` fires, Maestro pops a Run Summary sub-window that momentarily shadows the Assembler in the active-window list; the Assembler's `"fnxSession0"` registration becomes unfindable; `axlGetMainSetupDB("fnxSession0")` returns nil → every subsequent bridge call that re-resolves the session-name fails with `"Cannot find an active session named fnxSession0"`. Pre-existing recovery path was "user clicks back into Maestro Assembler to re-focus" — required after EVERY post-run probe, painfully manual. The fix: bypass the name-resolution step entirely on the call paths that don't need the name. The sdb integer handle is stable across this focus-loss state. Caller resolves name → sdb once (when focus is OK) and passes the int forever after.

A separate but co-occurring failure mode is the skillbridge transport leaving a half-formed reply in the socket buffer (likely after a previous python_server process was killed mid-response). The next call's `decode_response` splits on space and trips `not enough values to unpack`. Recovery is `(pyKillServer)(pyStartServer)` in CIW. Detection now lives in `get_sdb` so the first call after a wedge surfaces a clear, actionable error.

**Alternatives considered:**
- *Auto-recover via re-focus*: not possible from the bridge (we can't reach into Cadence to re-activate a window without an existing channel).
- *Polymorphic SKILL with name+sdb pair*: requires every wrapper signature to grow. Simpler to make the existing `sess` arg accept either form.
- *Force `axlGetMainSetupDB` retry with backoff*: doesn't help — focus has to be restored before the call can ever succeed.
- *Wrap every Python ws[...] call with wedge detection*: too invasive; `get_sdb` is the single bootstrap entry point that every script hits first, so wrapping just that one covers ~90% of the daily pain.

**Live-verified 2026-05-17 on `fnxSession0`:** end-to-end v1.3 retry with cached sdb. Pre-run script installed, fired across 6 sub-points of TT_pvt sweep-row (`cornerName=TT_pvt_0..5` confirmed via diagnostic log), 6 distinct `readic="/tmp/simkit_dogfood_TT_pvt_X.ic"` values landed in 6 separate `simulatorOptions options` blocks of `input.scs`. Spectre completed 0 errors. Cached-sdb pattern survived the post-`axlRunAllTests` focus loss in subsequent cleanup calls — no `"Cannot find an active session"` surfaced on the read-side restore path.

**Companion finding (no decision, just clearing a misattribution):** the `~1s per sub-point` serial dispatch the user observed is **not** caused by pre-run script attachment. A/B test 2026-05-17 — same TT_pvt 6-sub-point batch with and without pre-run installed — both showed identical `~1s` start-time gap between consecutive sub-points. Maestro's local sim dispatcher serializes regardless. If true parallel dispatch is wanted, configure session-level "Number of local jobs" (separate concern from this codebase).

## #59 — Phase 3A v1.4: `_prep` preserves a scalar baseline corner so Maestro doesn't auto-insert `nom`
_Date: 2026-05-18_

**Decision:** `_execute_ic_chained_item._prep` will guarantee at least one **scalar (non-sweep)** corner remains `axlSetEnabled t` in the corner table when launching a v1.3-style chained run. Selection policy:
1. **Auto (default):** scan the corner table in declared order, pick the first scalar corner that is currently enabled by the user. A scalar corner is one whose `vars` axis and `models` axis both resolve to single values (no space-separated sweep strings).
2. **Override (escape hatch):** sidecar `review.json` items may carry an optional `baseline_corner: "<name>"` field on entries that use `ic_from`. When present, it short-circuits auto-pick and selects that exact corner name; an unknown name surfaces as a hard error before any axl call fires.
3. **No-scalar-available case:** if auto-pick finds no scalar corner AND no override is given, raise `OrchestratorError("ic_from item '{name}' has no scalar baseline corner; declare baseline_corner: \"<name>\" in the sidecar")`. Do not silently let Maestro auto-insert `nom`.

The picked corner is enabled in addition to the sweep-row(s) the ic_from item needs. Its `models` axis section selections are passed through unmodified — section count is per-project and must not be normalised (see [[project_baseline_section_count]]).

**Why:** the v1.3 closeout dogfood produced a `nom` subdir as `/1/` in every history because `_prep` had disabled all scalar corners. `nom` has no active corner, so Maestro's netlister can't pick a model-file section and falls back to including **every** section of every model file (24 rf018.scs includes on this testmachine; would be 24-N at company depending on PDK shape). Manual user runs (`simkit_verify`) never produce `nom` because the user always leaves a scalar corner enabled as baseline — that's the well-known IC-design idiom this fix matches. The misclaim that this was a non-bug (PROJECT_STATE.md, 2026-05-17 PM handoff) was retracted 2026-05-18 after user pushback with on-disk evidence (subdir 1 `runObjFile` shows `#maeCorner=TT` in manual run vs `#maeCorner=nom` in v1.3 run).

**Alternatives considered:**
- *Synthesise a "nom-with-defaults" corner*: would require simkit to know per-PDK default sections (rf018: `tt`, `tt_rfmos`, `tt_rfmim` for company; just `tt` here). Brittle and project-specific.
- *Suppress the nom subdir via SKILL after the fact*: no Maestro API to inhibit auto-baseline; `axlRemoveElement` on a synthetic corner won't reach the implicit `nom`.
- *Always require explicit `baseline_corner:`*: shifts cost to user for the 99% case where they already have a TT in the table. A4 (auto + override) hits both ends.
- *Use the ic_from upstream item's corner as baseline*: requires orchestrator metadata threading across items; works for same-simkit-session chains but fails when upstream was a separate manual run. Auto-pick from current table is simpler and project-agnostic.

**Scope explicitly NOT in v1.4:**
- No CSV emitter / restore changes (baseline-corner is a runtime concept, not a corner-table-shape concept).
- No SKILL helper additions — pure Python orchestrator change.
- No re-verification of the `readic` injection mechanism itself (v1.3 proved it; v1.4 only changes which corners are enabled around it).

**Live-verify gate (must hold before declaring v1.4 done):**
1. On `fnxSession0`, run a v1.3-style chain. New history's subdir `/1/` `runObjFile` must show `#maeCorner=TT` (or whichever scalar was auto-picked), NOT `#maeCorner=nom`.
2. Subdir `/1/` `input.scs` must include rf018.scs exactly as many times as the picked corner's `models` axis declares (1 here; 3 at company). Do NOT hard-code `==1` in any test.
3. Subdirs `/2/`-`/7/` must still carry the distinct per-sub-corner `readic="..."` in their `simulatorOptions options` block — v1.3 functionality must not regress.

**Live-verified 2026-05-18 on `fnxSession0`** (history `v14dog_pss_v14_1779072455_1`, dogfood script `/tmp/v14_dogfood.py` against fake upstream `fake_trans_v14`). Pre-run input state: `[(TT, False), (TT_pvt, True), (TT_2p5G, False)]` — exactly the pathological "only sweep enabled" state that produced `nom` in earlier dogfoods. Orchestrator logged `baseline corner: 'TT' (auto)`. Resulting history:
- subdir 1 = TT (1 rf018 include — matches `simkit_verify` baseline)
- subdir 2 = TT_pvt_0 (1 rf018 include, distinct `readic`)
- subdir 3 = TT_pvt_1 (1 rf018 include, distinct `readic`)
- subdir 4 = TT_pvt_2 (1 rf018 include; `readic` is empty-in-map → inherits prior sub-point's value — pre-run-script leak that exists in v1.3 too, surfaces only when the union doesn't fully cover the live corner table; orthogonal to v1.4)
- NO `nom` subdir
- Corner-enable state restored byte-identical to entry snapshot in finally

**Known sub-issue surfaced during verify (NOT a v1.4 regression — pre-existing v1.3 behavior, queued for v1.4.1):** orchestrator's `Snapshot/RestoreCornersEnable` wrappers currently take session NAME only (not sdb-polymorphic). Post-`axlRunAllTests` focus loss (the Run Summary sub-window steals focus from Assembler) means the in-finally `restore_corners_enable` call will fail with "Cannot find an active session" unless the user re-clicks Maestro Assembler between the orchestrator's run-and-restore steps. Pattern is documented in [[reference_bridge_session_focus]] and DECISIONS #58 — those decisions made the error message clear, not the recovery automatic. Workaround for v1.4: user clicks Maestro back after each chained run (same as v1.3 daily flow). Real fix in v1.4.1: extend the SKILL helpers `pvtRunnerSnapshot/RestoreCornersEnable` with `(integerp sess)` polymorphism per DECISIONS #58 pattern, so the post-run restore can use the cached sdb handle and bypass name resolution entirely. Live-verify above succeeded only after a user-click on Maestro to restore focus between snapshot and run — confirms the picker logic, doesn't yet confirm hands-off recovery.
