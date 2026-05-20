# Live-shape fixtures (Mandate M1)

Fixtures here are **captured from a real Maestro pull**, not hand-authored.
Tests covering union / corner / measure pull/push paths must consume these —
hand-written dicts reflect the author's *guess* at the data shape and miss what
real Maestro produces (`model.section` arrays, empty `_file_abs`,
comma-separated multi-section values).

See `docs/dispatch_mandates.md` M1.

## Adding a fixture

While Virtuoso is running, pull once via skillbridge and drop the result here.
Every fixture file must begin with a provenance header recording where it came
from:

- JSON: a top-level `"_provenance"` key — `testbench`, `session`, `pulled` (date).
- CSV / other: a leading comment line — `# provenance: testbench=... session=... pulled=YYYY-MM-DD`.

## Existing live captures elsewhere

`tests/fixtures/unions/fnxsession0_baseline.union.json` predates this directory;
it is a genuine live pull from `sim_yusheng/Test/maestro` (session `fnxSession0`).
New live captures from Phase 5 onward go here.
