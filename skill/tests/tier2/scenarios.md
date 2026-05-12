# Tier-2 manual scenarios — `pvtProjectFirstSaveDialog`

These exercises hit the **UI wrapper** of `pvtProjectDialog.il`. Tier-1 unit
tests cover all pure layers (defaults, validators, JSON build/write); the
UI wrapper itself (`hi*` widgets, modal display, callback orchestration)
needs a live Virtuoso session.

Run these once per UI-affecting change. Past green = past green; don't
re-run unless the dialog code or `hi*` widget usage changed.

---

## Sandbox preparation (do this once, then keep it around)

The walker would otherwise find the workarea's own `.pvtproject` and skip
the dialog path entirely. We need a directory with NO `.pvtproject` from it
up to `/`. The clean spot is alongside `Test/`:

```sh
# In a shell on the dev host (NOT in CIW):
mkdir -p /home/yusheng/cadence_work/dialog_sandbox/sub_a
mkdir -p /home/yusheng/cadence_work/dialog_sandbox/sub_b
# Verify nothing above shadows us:
find /home/yusheng/cadence_work -maxdepth 4 -name .pvtproject
#   -> (no output expected)
find /home/yusheng -maxdepth 2 -name .pvtproject
#   -> (no output expected)
```

The simkit SKILL files load by **absolute path** — no copying needed. We
only need the sandbox dir tree to host the freshly-written `.pvtproject`
files.

If a scenario writes a `.pvtproject` and the next scenario expects "no
file", the cleanup line at the top of each scenario will remove it.

## CIW prelude — paste this once per Virtuoso session before running scenarios

```skill
;; Load the production files (order matters — pvtProjectDialog after pvtProject):
(load "/home/yusheng/cadence_work/Test/workarea/simkit/skill/pvtError.il")
(load "/home/yusheng/cadence_work/Test/workarea/simkit/skill/pvtJson.il")
(load "/home/yusheng/cadence_work/Test/workarea/simkit/skill/pvtProject.il")
(load "/home/yusheng/cadence_work/Test/workarea/simkit/skill/pvtProjectDialog.il")
(load "/home/yusheng/cadence_work/Test/workarea/simkit/skill/pvtCollect.il")
```

Verify the gate flipped:

```skill
(boundp 'pvtProjectFirstSaveDialog)   ; => t
(getShellEnvVar "DISPLAY")            ; => non-nil (you have a GUI session)
```

---

## Scenario 1 — Happy path

**Setup**
```skill
(changeWorkingDir "/home/yusheng/cadence_work/dialog_sandbox/sub_a")
(when (isFile (strcat (getWorkingDir) "/.pvtproject"))
  (deleteFile (strcat (getWorkingDir) "/.pvtproject")))
```

**Trigger**
```skill
(setq r (pvtLoadPvtProject))
```

**Expected**
- A modal form titled "Create new .pvtproject" appears.
- "Project name" prefilled with `sub_a` (cwd basename normalised) or with
  the active cellview's lib name if a Maestro window is in focus.
- "DB root" prefilled with `./simkit_data`.
- "Author" prefilled with your `$USER`.
- "Save .pvtproject to" prefilled with
  `/home/yusheng/cadence_work/dialog_sandbox/sub_a/.pvtproject`.
- Click OK. Form closes. `r` is a `pvtOk(...)` discriminated result.
- `(pvtUnwrap r)->project` matches what you typed.
- File exists on disk:
  ```skill
  (isFile "/home/yusheng/cadence_work/dialog_sandbox/sub_a/.pvtproject")  ; => t
  ```
- Re-invoke `(pvtLoadPvtProject)` and confirm: **no dialog** this time, walker
  hits the freshly-written file directly.

**FAIL signals**
- Form doesn't appear, or appears non-modal, or buttons missing.
- Default values blank or wrong.
- File not written, or file written but `pvtParsePvtProject` rejects it
  (the dialog should have round-trip-checked before returning).

---

## Scenario 2 — Cancel path

**Setup**
```skill
(changeWorkingDir "/home/yusheng/cadence_work/dialog_sandbox/sub_b")
(when (isFile (strcat (getWorkingDir) "/.pvtproject"))
  (deleteFile (strcat (getWorkingDir) "/.pvtproject")))
```

**Trigger**
```skill
(setq r (pvtLoadPvtProject))
```

**Expected**
- Form appears (same as Scenario 1).
- Click **Cancel**.
- `r` is a `:err pvt_notFound` result with message
  `"no .pvtproject found walking up from <sub_b> and PVT_PROJECT is not set"`.
- File NOT on disk:
  ```skill
  (isFile "/home/yusheng/cadence_work/dialog_sandbox/sub_b/.pvtproject")  ; => nil
  ```

**FAIL signals**
- File written despite Cancel.
- Cancel returns `:ok` or some other category.

---

## Scenario 3 — Validation keeps the form open

**Setup**
```skill
(changeWorkingDir "/home/yusheng/cadence_work/dialog_sandbox/sub_a")
(when (isFile (strcat (getWorkingDir) "/.pvtproject"))
  (deleteFile (strcat (getWorkingDir) "/.pvtproject")))
```

**Trigger**
```skill
(pvtLoadPvtProject)
```

In the form, **clear** Project name and type `My Bad Name` (with capital
letter and a space — both rejected by the regex). Click **OK**.

**Expected**
- Form **does NOT close**. A `WARNING: pvt: Project name "My Bad Name"
  must match ^[a-z0-9_-]+$` (or similar) prints in the CIW.
- Edit Project name to `good_name`. Click OK.
- Form closes. File written. `(isFile ...)` confirms.

**FAIL signals**
- Form closes despite invalid input.
- No warn message in CIW.

---

## Scenario 4 — Re-entrancy is impossible

The framework + classic SKILL's sync callback semantics mean you literally
cannot fire the dialog twice in the same SKILL evaluation. This scenario
exists only to confirm that double-clicking OK / Cancel doesn't break
anything.

**Setup** — same as Scenario 1, file removed.

**Trigger** — `(pvtLoadPvtProject)`, then double-click OK very fast.

**Expected**
- Exactly one form close, one file written.
- `r` is a single `:ok` result.
- No SKILL stack trace in CIW.

**FAIL signals**
- Form re-appears.
- Two files written or one corrupted file.
- SKILL error after the close.

---

## Scenario 5 — Headless suppression

This proves `?allowDialog nil` and the `DISPLAY` env-var gate both work.

**Setup (in a fresh shell)**
```sh
unset DISPLAY
cd /home/yusheng/cadence_work/Test/workarea/simkit
virtuoso -nograph -replay skill/tests/runTests.il
```

**Expected**
- Full Tier-1 suite runs.
- Zero new failures vs. the GUI run (the no-session test that fails under
  Maestro should now PASS because nograph has no session).
- No dialog pops (there's no display anyway).

**FAIL signals**
- Test driver hangs (dialog tried to pop without a display).
- New unexpected failures unrelated to the session test.

---

## After all five pass

Mark Tier-2 done in your tracker, then proceed to the final doc sweep
(`TODO.md` §2 item 2.2, `PROJECT_STATE.md`, `DECISIONS.md` entry for the
v1 scope + `?okButtonText` non-existence + `?unmapAfterCB t` /
`hiSetCallbackStatus` pattern). The sandbox dir
`/home/yusheng/cadence_work/dialog_sandbox/` can stay around as a
regression bench for future UI changes — no need to clean it up.
