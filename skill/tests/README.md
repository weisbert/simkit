# SKILL test runner

The test driver is `runTests.il`. It loads the production sources from
`../pvtError.il`, `../pvtJson.il`, `../pvtProject.il`, then loads each
`testPvt*.il` file (which registers test thunks via `pvtTestRegister`),
then calls `pvtTestRun()` to execute everything and print a summary.

## Running under Cadence

From the repository root:

```sh
virtuoso -nograph -replay skill/tests/runTests.il
```

This requires the `cwd` to be the simkit repository root because the
production `.il` files reference `<cwd>/skill/...`. To run from another
directory, override the two roots:

```sh
PVT_SKILL_ROOT=/abs/path/to/simkit/skill \
PVT_TEST_ROOT=/abs/path/to/simkit/skill/tests \
virtuoso -nograph -replay /abs/path/to/simkit/skill/tests/runTests.il
```

If `-replay` is unavailable on your Cadence version, evaluate from a CIW:

```skill
load("/abs/path/to/simkit/skill/tests/runTests.il")
```

## Test layout

| File                  | Coverage                                          |
|-----------------------|---------------------------------------------------|
| `testPvtJson.il`      | Strict-JSON accept/reject paths in `pvtJson.il`   |
| `testPvtProject.il`   | Validators + walker + env-var lookup + bundled example |

The fixtures used by `testPvtProject.il` live in `fixtures/`. See
`fixtures/README.md` for the per-file expected outcome.

## Exit status

`pvtTestRun()` returns `t` on zero failures, `nil` otherwise. The harness
does NOT call `exit()` — that decision is left to the wrapper script (e.g.
a future CI shim that translates the boolean into a process exit code).
