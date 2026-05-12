# Project State

_Last updated: 2026-05-12 (morning — §6 acceptance gates landed)_

## Current phase

**Phase 1: Data Pillar MVP — COMPLETE.** All six sections of the Phase 1 plan are done: spec (§1), `.pvtproject` loaders (§2 items 1 & 2), collector SKILL with all four messy-data bugs fixed plus working netlist Spectre detection (§3), Python ingester + inline validator (§3(d), §4), full `pvt` CLI surface (`ingest`/`validate`/`attach`/`label`/`list`/`diff`, §5), and the four §6 end-to-end acceptance gates pinned as a regression suite. Open items remaining (§2.2 SKILL dialog, screenshot v1.1, full walker mock-rdb harness) are all explicitly deferred per their DECISIONS entries and don't block Phase 2 from starting.

## Goal of Phase 1 (one sentence)

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

## What's IN PROGRESS

_(nothing — Phase 1 just wrapped. All four §6 acceptance gates pinned as regression tests. Ready for Phase 2 kickoff or for the explicitly-deferred Phase 1 backlog below.)_

## What's NEXT (next 1–2 sessions)

1. **Phase 2 kickoff.** With the data pillar done, the next planned pillar is one isolated authoring helper (per DECISIONS #1 build order). `PHASE_PLAN.md` carries the corner-var-shape candidate from the overnight VCO PVT-union case; that's the natural starting point unless something newer has surfaced.
2. **§2.2 SKILL first-save dialog (deferred Phase 1 backlog).** Plan-D in `docs/plans/§2.2_dialog.md`. Needs `virtuoso-skill` PDF lookup for `hi*` form construction + live Virtuoso UI testing. A clean cloned-workarea testbed under `/home/yusheng/cadence_work/` will be prepared when this starts. Not strictly Phase 1 — batch/scripted callers don't need it.
3. **Walker mock-rdb harness (deferred — DECISIONS #23).** `_pvtCollBuildPidListFromTests` covers the Bug B surface at Tier-1; live-walker end-to-end via mock-rdb remains deferred (write-protected `maeReadResDB`, no `flet` in classic SKILL). Waiting on either a real gappy-pid sim or a budget for the medium-cost walker API refactor.
4. **Screenshot v1.1 (deferred — S3_DESIGN §3.5).** Current behaviour is one-shot warn + return nil. Pick up when a use case shows up.

### Two smaller items still owed from §2 (independent of §3, can interleave)

- **§2.2 dialog** — SKILL-only first-save fallback (`skill/pvtProjectDialog.il`). Plan in conversation history; deferred from §2.2 main land. Not a blocker for §3 because batch / scripted callers don't need it.
- **README/fixtures README rewrite of "pvt:foo" doc strings** — done in commit `a3c8651`. (Just noting so a future grep doesn't surprise anyone.)

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
