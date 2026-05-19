# Bugs #1 + #2 dogfood — 2026-05-19

## Bug #1: stuck "pending"

**Verification layers:**

1. **Unit (argv):** `tests/gui/test_run_controller.py::StartRunGuardTests::test_start_run_uses_unbuffered_python` asserts `-u` precedes `-m` in QProcess argv. PASSED.

2. **Behavioral (streaming):** `test_unbuffered_subprocess_streams_before_exit` spawns a real python `-u -c '...'` subprocess that prints, sleeps 0.6s, prints again. Asserts the controller receives the first event within 400ms — while the child is still mid-sleep — so `finished` has NOT yet fired. Proves stdout streams line-by-line, not at exit. PASSED.

3. **Regression:** Full `tests/` suite (1512 tests + 93 subtests) PASSED.

## Bug #2: "Interactive.0" history name

**Verification layers:**

1. **Live skillbridge probe** against the user's running Maestro session `fnxSession0`:

```
Session: fnxSession0
Original history name: 'Interactive.0'
Rename to probe -> [Symbol('pvt_ok'), 'simkit_probe_overnight_2026_05_19']
History name now: 'simkit_probe_overnight_2026_05_19'   <-- Maestro accepted the rename
Restore -> [Symbol('pvt_ok'), 'Interactive.0']
History name final: 'Interactive.0'                      <-- restored cleanly
```

2. **Validation path:** `pvtRunnerRename('fakeSession', '')` returns `[pvt_err, pvt_validation, "historyName must be a non-empty string"]`. Confirmed via bridge.

3. **Surface-failure path:** the errset wrapper around `axlSetHistoryName` was removed. Now on rename failure, `pvtRunnerRename` returns `pvt_runner_rename` error instead of silently leaving the auto-named history.

**Note on the user's perception:** the live rename works and the rename code was already wired correctly. Their seeing "Interactive.0" was almost certainly a *consequence* of Bug #1: with `-u` missing, the GUI never reflected run completion, so the user looked at Maestro while `pvt_runner_run` was still pre-rename. Bug #1 fix is the load-bearing one for the perceived symptom.

## Bug #3: real-env (Command/alps) compat

**Verdict: A — equivalent to manual click.** Audit of every SKILL call in the run-dispatch path (orchestrator.py:1238-1279, skill_bridge.py:669-683, pvtRunner.il:221-246) found:

- No `axlSetMainSimulator` / `asiSetSimulator`
- No `axlPutRunMode`, `axlSetCommand`, host/queue setter
- No envvar assumptions
- Submit is literally `(axlRunAllTests sess "")`

**One narrow exposure:** `ic_from:`-enabled review items write Spectre-flavored `+nodeset/+ic` to `additionalArgs` (pvtRunner.il:458). Alps CLI compat with these flags is the open question. The common batch path (no `ic_from`) is engine-agnostic. Not a blocker — `ic_from` is opt-in.

No code change needed for Bug #3.
