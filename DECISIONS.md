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

