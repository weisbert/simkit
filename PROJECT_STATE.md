# Project State

_Last updated: 2026-05-12 (evening — Phase 2 kicked off; §1 spec landed; §2 Python loader queued; PM-mode w/ verification gate)_

## Current phase

**Phase 2: PVT-Union Builder — §1 spec drafted (this commit), §2 next.** Spec at `docs/phase2_pvt_union_spec.md` (~290 lines), worked example at `config/pvt_union.example.json` matching the live `simkit_verify` corner table. DECISIONS #29-31 capture the data-model recovery via skillbridge (dual-axis vars + models), the explode-order rule (alphabetic key + lex-sorted values), and the v1 scope freeze (no templating, no `axlSetParameter`, no MTS). Six open decisions (8.1-8.6) flagged; resolved during §2 implementation as defaults from §8 unless domain feedback intervenes.

**Phase 1: Data Pillar MVP — COMPLETE.** Six sections shipped: spec, `.pvtproject` loaders (Python + SKILL + first-save dialog), collector SKILL with messy-data refactor + Bug A/B/C/D fixes + netlist Spectre fix, Python ingester + inline validator, full `pvt` CLI surface (`ingest`/`validate`/`attach`/`label`/`list`/`diff`), 4 acceptance gates pinned as regression. Open items (§2.2 dialog Tier-2 manual, screenshot v1.1, walker mock-rdb) all deferred per their DECISIONS entries.

## Goal of Phase 2 (one sentence)

End-to-end loop: "describe one semantic PVT in a sidecar" → "tool emits the exploded Maestro corner table" → "round-trip is bit-identical against the live session."

## Goal of Phase 1 (one sentence — historical reference)

End-to-end loop: "Maestro sim finishes" → "one command saves it" → "Python can query it and diff two slices."

## Recent timeline

- **2026-04-21 → 2026-04-22**: initial design conversation closed all architectural questions. See `DECISIONS.md` entries #1–#12.
- **2026-04-22**: project scaffold + git repo; Phase 1 scope locked in `TODO.md`.
- **2026-04-22**: §1 Specification complete (`docs/schema.md`, `config/pvtproject.example.json`).
- **2026-04-22**: Decision #13 — JSON over YAML/TOML for `.pvtproject`.
- **2026-04-22**: §2 item 1 done — Python loader (`python/simkit/project.py`, 30 unittest tests).
- **2026-05-08**: §2 item 2 done — SKILL loader (`skill/pvtError.il`, `pvtJson.il`, `pvtProject.il`, ~1800 lines + 19 fixtures + 76 tests, all passing). Drove tests via skillbridge. Surfaced 12 classic-SKILL idiom traps; see Decision #14.
- **2026-05-08**: §3 prep — JSON emitter + collector API probe committed (`c94c0d2`). pvtCollect.il scaffolded (974 lines) + Tier-1 unit tests (377 lines, 41 new tests; cumulative 168/0/0).
- **2026-05-10**: §3 first end-to-end on real Maestro (`sim_yusheng/Test/maestro:simkit_verify`, 7 tests / 49 outputs). **Tier-1 was green; live run silently produced 0 rows** until we localized 9 funobj-call bugs in `pvtCollect.il` via skillbridge probing. Post-fix: 42 ok rows, correct corner_vars, correct testbench_alias resolution, valid JSON envelope. See Decision #16 for the funobj/funcall rule and slot inventory.
- **2026-05-11**: §4 Python ingester (`python/simkit/{db,ingest,schema_sql,errors}.py` + `cli/`) and §3(d) Python validator (`python/simkit/validate.py`, 24 invariants + 2 warnings) landed. 138 tests passing (30 baseline + 108 new). Validator wired inline in ingester by default (Decision #17). Surfaced and resolved 5 decisions: #17 inline validator, #18 nullable `netlist_path`, #19 internal `simkit_meta`, #20 per-file ingest transactions, #21 dropped FK in DDL (DuckDB constraint-relaxation limitation). 42-row real-run fixture from 2026-05-10 checked into `tests/fixtures/runs/` as the integration anchor.
- **2026-05-11**: §3 messy-data approach (a) refactor landed end-to-end. `_pvtCollIterateResults` is now a 5-line composer over `_pvtCollWalkRdb` (live walk → walkData) + `_pvtCollRowsFromTuples` (pure shaper, owns all three passes). Tier-1 grew 167→215 (+16 tests / +48 assertions) covering all six TODO §3 scenarios + pre-fix Bug A/C/D documentation tests. Tier-2 verified via skillbridge against `simkit_verify` — byte-identical 42-row output (`firstTestName="Test"`, first row `Rtime_clkout=2.134521e-11` matches pre-refactor). Bug A/B/C/D fixes (`"running"`→`"unknown"`, gappy-pid walker B2, pass-2 writtenSet skip, unified `_no_corner_vars` marker) queued as Step 4.
- **2026-05-11 (overnight)**: §3 messy-data Step 4 landed — all four bugs fixed in separate commits, Tier-1 215/1/0 maintained, Tier-2 byte-identical regression on `simkit_verify` (42/42 data rows match 2026-05-10 reference). Pre-fix `CURRENTLY-…` tests flipped to assert post-fix behaviour. Two scope notes captured in DECISIONS #22 (corner_vars marker unified across all three passes, not just pass-3) and #23 (walker-level Tier-1 testing deferred — Bug B verified by Tier-2 happy-path regression + correct-by-reasoning, awaits real gappy-pid sim or future synthetic-rdb harness).
- **2026-05-12 (overnight)**: §5 `pvt` CLI fill-out landed in five commits — `pvt attach`, `pvt label`, `pvt list`, `pvt validate --from-db`, `pvt diff`. Pure-Python; full Tier-1 138 → 242 / 0 (104 new tests across 8 new test files). Surfaced three design decisions: #24 (diff slice resolution = exact-label-then-prefix), #25 (label re-policy = error w/o `--force`, `--clear` unconditional), #26 (DuckDB TIMESTAMPTZ ↔ Python via `CAST AS VARCHAR` + ISO normalisation, to avoid the stdlib-pytz dependency the offline-deploy constraint forbids). All §5 commands open the DB read-only when they only read.
- **2026-05-12 (morning)**: §3 netlist Spectre detection fixed (DECISIONS #27). Pre-fix probe used `asiGetAnalogSimulator` — wrong API for Maestro / ADE-XL contexts — and returned nil on every spectre run. Replaced with a path-presence test: `<netlistDir>/input.scs` exists iff the simulator is Spectre. Tier-2 verified on the live `simkit_verify` session: `netlist_path="input.scs"`, 2938-byte `input.scs` copied into the run dir, `pvt validate` clean (no W2), `pvt ingest` populates `runs.netlist_path` non-null. 42-row count + first-row value match the 2026-05-10 reference, so no other behaviour shifted. SKILL Tier-1: 224/1 unchanged.
- **2026-05-12 (morning)**: §6 end-to-end acceptance gates pinned. `tests/fixtures/acceptance/` carries the live-captured `run_a` (full simkit_verify dump from earlier today) plus a synthesised `run_b` (manual C0 capacitor edit + per-row +1% value delta) and a 69-byte dummy PNG. `tests/test_acceptance.py` exercises all four §6 gates — save→ingest→query, TT worst-case across the 7 corners, netlist diff between two slices, post-hoc attach + retrieve — as 12 unit tests that don't need live Maestro at test time. Phase 1 Python suite: 242 → 254 / 0.
- **2026-05-12 (afternoon)**: §2 item 3 — SKILL first-save dialog landed (`skill/pvtProjectDialog.il`, 482 lines). v1 scope per DECISIONS #28 is four fields: Project name (required), DB root / Author / Save path (optional with defaults). Validation-fail UX uses `?unmapAfterCB t` + `hiSetCallbackStatus` to keep the form open. Tier-1 +23 tests via skillbridge → cumulative 256 / 1 / 0 (the 1 fail is the unchanged Maestro-open no-session baseline). Three new classic-SKILL traps surfaced and added to DECISIONS #14's idiom list (now reaches #15): (13) `(procedure (name ()) ...)` is wrong for zero-arg; (14) `?okButtonText` is not a real `hiCreateAppForm` keyword; (15) `boundp` is false for procedures — caught during Tier-2 smoke when the step-3 gate in `pvtProject.il` never flipped despite the dialog file being loaded. Fixed in `pvtProject.il:337` (`boundp` → `getd`); pre-existing bug that no test had exercised. Tier-2 happy path visually confirmed on live Virtuoso: form rendered, OK click wrote a clean 190-byte `.pvtproject` (project from Maestro session lib name `sim_yusheng`, blank dbRoot defaulted, blank author got `$USER`, schema_version emitted as bare int). Smoke test (`/tmp/dialog_smoke.py`) also covers programmatic happy / validation-fail / `?allowDialog nil` paths — 11/11 checks pass.
- **2026-05-12 (evening)**: Phase 2 kicked off. §1 spec frozen at `docs/phase2_pvt_union_spec.md` + `config/pvt_union.example.json`. Data model recovered live via skillbridge against `fnxSession0` — discovered Maestro stores corner sweeps on two parallel axes (vars + models), each using the same space-separated-string sweep encoding. Initial single-axis draft was wrong; revised to vars + models.section (model file/block/test stay at defaults for v1). Explode order rule reverse-engineered from the live TT_pvt 6-sub-corner expansion: alphabetic key + lex-sorted values (DECISIONS #30). Six open decisions (8.1-8.6) flagged for resolution during §2; only 8.4 (axlSetParameter device-level overrides) is potentially scope-shifting. PM mode + verification gate granted by user: SKILL code must be skillbridge-verified non-blocking; Python must be runtime-verified.

## What's DONE

- All architectural decisions for the data pillar (see `DECISIONS.md`)
- Phase 1 scope defined (see `TODO.md`)
- Project scaffold and git repo
- §1 Specification: schema spec + example `.pvtproject`
- §2 item 1 — Python `.pvtproject` loader
- §2 item 2 — SKILL `.pvtproject` loader (commit `a3c8651`)
- §3 collector core — `PvtSave` entry, auto-capture, results iteration, JSON write, netlist copy (Spectre detection via file presence; DECISIONS #27). Verified end-to-end on a real Maestro history; 2026-05-12 Tier-2 run on `simkit_verify` confirms netlist_path now populates correctly. Tier-1 224/1/0 (1 baseline FAIL: no-session test with Maestro open).
- §3 messy-data refactor (a) Steps 1–4 — walker/shaper split + four targeted bugfixes (Bug A non-symbol status → `"unknown"`; Bug B walker pidList from `tst->pointID`; Bug C pass-2 per-test writtenSet skip; Bug D unified `_no_corner_vars` marker across all passes). Tier-2 byte-identical 42-row regression on `simkit_verify` confirms zero happy-path regression.
- §4 Python ingester — scan dump dir → DuckDB; idempotent on `run_id`; `schema_version` dispatch; inline-by-default validator. CLI: `pvt ingest` and `pvt validate`. 108 new tests, 138 total green.
- §3(d) Python validator — 24 invariants + 2 warnings (`W1` corner_vars magic markers, `W2` null netlist_path). Independently invocable (`pvt validate <path>`) and inlined in `ingest_run_json` by default. `pvt validate --from-db <run_id>` audits a DB-resident run by reconstructing the JSON-dump shape (DECISIONS #26).
- §5 `pvt` CLI surface — `attach`, `label` (set / `--force` / `--clear`), `list` (table or `--json`, `--slice-only`, `--project`, `--limit`), `diff` (results table + unified netlist diff, `--threshold`, `--include-status`, `--json`). Tier-1 grew 138 → 242 / 0; 8 new test modules. Slice resolution rule per DECISIONS #24.
- §6 end-to-end acceptance — 4 gates pinned as 12 tests in `tests/test_acceptance.py` against `tests/fixtures/acceptance/` (live `simkit_verify` dump + synthesised variant with documented manual netlist edit + dummy PNG). Demonstrates the full save→ingest→query→diff→attach loop without needing live Maestro at test time.
- §2 item 3 — SKILL first-save dialog (`skill/pvtProjectDialog.il` + `skill/tests/testPvtProjectDialog.il`). v1 four-field scope per DECISIONS #28. Tier-1 256 / 1 / 0 (the 1 baseline FAIL unchanged). Production-side classic-SKILL idioms validated against `virtuoso-skill` PDF index for every `hi*`/`axl*` call. **Tier-2 manual UI verification still owed** — 5 scenarios in `skill/tests/tier2/scenarios.md`, sandbox at `/home/yusheng/cadence_work/dialog_sandbox/`.

## What's IN PROGRESS

- **Phase 2 §2 — Python loader + validator.** Spec frozen; implementation queued. Module path: `python/simkit/union.py`. Verification gate per PM-mode rule: pytest 100% + `python -m simkit.union explode config/pvt_union.example.json` must reproduce the 7-row table in spec §9 verbatim.

## What's NEXT (Phase 2 sequencing — locked dependencies)

1. **§2 Python loader** (current). Subagent candidate (mechanical, well-spec'd, easy to verify).
2. **§3 SKILL bridge** — pull side first (produces inspectable JSON), then push side. Mine (skillbridge verification needs careful non-blocking probing).
3. **§5 CLI surface** — `pvt corners build/explode/list/diff/push/pull`. Subagent-friendly for the offline subcommands; push/pull wraps §3 SKILL functions.
4. **§6 Acceptance gates** — Gates U1, U3, U4 pin-able once §5 lands; U2 (VCO LO) waits until the VCO LO setup is loaded in Maestro.

**Backlog (deferred from Phase 1, do alongside if convenient):**
- §2.2 dialog Tier-2 manual UI verification (5 scenarios in `skill/tests/tier2/scenarios.md`).
- §3 walker mock-rdb harness (DECISIONS #23).
- §3 screenshot v1.1 (S3_DESIGN §3.5).

## Open questions / blockers

- **JSON byte representation** — `pvtJson.il` uses a precomputed 255-byte octal LUT to synthesize bytes (because classic SKILL `sprintf "%c"` rejects integers and there is no `intChar`). Side effect: **JSON strings cannot contain ` `** by this implementation. RFC 8259 allows it, Python stdlib accepts it. Not a blocker for `.pvtproject` (no NUL ever), and probably not for collector dumps either, but flag if §3 ends up needing to round-trip arbitrary user-supplied strings.

## Context cheatsheet for fresh sessions

- **User**: analog circuit designer; Cadence Virtuoso ICADVM18.1-64b; Python 3.11.4.
- **Environments**: home = dev (Claude Code OK, mirrored Cadence); work = red zone (offline only, no Claude Code). Deploy constraint: fully offline-installable.
- **POC file**: `../MyRunner/PvtDumpToJson.il` — proved dump path works. **Do NOT extend it**; Phase 1 writes a clean collector from scratch (Decision #12).
- **SKILL reference docs**: `../SKILL_file/` — 44 Cadence PDFs organized by topic. Consult before writing SKILL. The standing rule (`virtuoso-skill` skill) is mandatory for any SKILL coding task.
- **SKILL test infrastructure**: skillbridge is running on this machine (socket `/tmp/skill-server-default.sock`, install at `../skill_tools/skillbridge/`). Use it to verify SKILL idioms BEFORE assuming they work. See Decision #14 for the general classic-SKILL trap list and Decision #16 for the `axlrdb*` slot-accessor funobj rule (specific to live ADE-XL session code).
- **Tier-1 vs Tier-2**: Tier-1 (`/tmp/run_skill_tests.py`, 168 tests) covers pure helpers and is mock-free. Tier-1 is necessary but **not sufficient** for any code that touches a live ADE-XL session — see Decision #16 for the canonical "Tier-1 green, live silent-fail" episode. Mandatory Tier-2 verification against an open Maestro session before declaring any §3-style feature done.
- **Test driver**: `/tmp/run_skill_tests.py` (transient — `/tmp` may be cleaned). Pattern is documented in `skill/tests/README.md`; rebuild from there if missing.
