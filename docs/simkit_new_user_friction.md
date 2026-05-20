# simkit — New-User Friction Catalog

*Dogfood by an RF/analog IC designer, GUI-only Virtuoso/Maestro user. No SKILL,
no OCEAN, no Python. Everything below is what I hit trying to do four real jobs
on the **1AXX** block: read past sim data, view/edit corners, view/edit
measurement expressions, and set up a batch simulation. All editing was done
against a scratch copy (`/tmp/simkit_1AXX_friction`), never the live project.*

This is a usability report, not a code review. Every observation is grounded in
the actual GUI screens and CLI commands I saw.

---

## First impressions — the first ~10 minutes

I launched it the way I was told: `pvt gui`. A window came up titled **simkit**.
That part was fine. But the cold-start feel is rough:

- **The window opens tiny and there's no obvious "this is your home base."**
  On my desktop the simkit window came up small, floating over the Virtuoso
  CIW and ADE Assembler. Nothing tells me what to do first. I see a left tree
  (Reviews / Bundles / Milestones / History), three tabs (Results / Corners /
  Measures), and a log pane at the bottom.

- **The top-left says `[Module: 1AXX]` — engineer-speak I had to decode.**
  In ADE Maestro I think in *cell / testbench / library*. simkit calls my block
  a "Module" and shows it as `[Module: 1AXX]`. Before a module is loaded the
  label is half-Chinese, half-English: `未打开模块 — File ▸ Open Module… (Ctrl+O)`.
  The mixed-language UI is jarring — some dialogs are English, some Chinese, some
  both in one sentence (e.g. a review parse failure logs `解析失败，无法运行`).

- **The Results tab opens blank and slightly accusatory: `(no run selected)`.**
  My first instinct was "show me my last sim." Nothing is shown. I have to know
  to go click something in the left tree first. There's no "recent runs" landing
  view, no thumbnail, no "you have 3 runs, click one."

- **A yellow dot and a "Restart bridge" button sit top-right.** I have no idea
  what the "bridge" is. As a designer I don't think about a SKILL socket. The
  dot was amber the whole time and I never learned whether that's bad. There's a
  `Session:` box next to it with placeholder `e.g. fnxSession0` — another piece
  of jargon. I run ADE Maestro; I don't know my "session name." (The tool *can*
  auto-detect it from Maestro, but only if the bridge is connected, and there's
  no message telling me it tried.)

- **The bottom log talks in `[corners]` / `[measures]` tags.** Useful to a
  developer; to me it reads like console spew. `[corners] loaded 3 rows from
  baseline.union.json` — I didn't ask it to load anything.

**Verdict on first 10 minutes:** I can find the three tabs, but nothing
*onboards* me. There is no "Welcome / here is your block / here is your last
run / here is what to click." A first-time designer will poke around the tree
by trial and error.

---

## Task 1 — Read historical simulation data

**What I expected:** Open the tool, see my block's recent simulations in a list,
click the latest, see a results table with corners down the side and
measurements across — the same shape as the ADE Maestro results table — with
pass/fail flags.

**What I actually found:**

The runs *are* there. The left tree has a **History (3)** group:

```
History (3)
   v6_cancel_probe  ·  2h ago
   c1_sanity_run    ·  2h ago
   orch_Test_basic_1779240708_1  ·  4h ago
```

Clicking a history row loads the **Results** tab. That tab has a header line
(history name · project · timestamp) and a sortable table with columns
`corner | test | output | value | status | spec | spec_status`. That's a
reasonable table and it did populate (36 rows for the run I picked).

**Friction, step by step:**

1. **The run *names* are unreadable.** Two of my three runs are called
   `v6_cancel_probe` and `c1_sanity_run` — those look like a developer's test
   labels, not something I'd recognize as "my LNA gain sweep." The third is
   `orch_Test_basic_1779240708_1` — a raw machine string with a Unix epoch
   buried in it. As a designer I name runs after *what I was checking*
   ("gain_corners_PDR"). The tool's default naming gives me garbage to scan.
   *(Minor papercut, but it compounds — see Task 4.)*

2. **The results table shows `spec` = `—` and `spec_status` = `no_spec` on
   every single row.** My whole reason to look at a results table is pass/fail
   against spec. simkit shows me numbers with no spec line, so I'm back to
   reading raw numbers by eye — exactly the pain Part 0 of the requirements
   catalog says the tool is supposed to remove. Nothing in the GUI tells me
   *how* to attach specs, or that I even can.

3. **`status = eval_err` on a row with value `—` and no explanation.** One
   measurement (`Rtime_clkout`) failed to evaluate. The table just says
   `eval_err`. As a designer I'd want "why" — bad expression? missing signal?
   I get a three-letter code and a dash. I'd have to dig.

4. **The corner names in the table are `TT_pvt_0`, `TT_pvt_1`, … `TT_pvt_5`.**
   These are *exploded sub-corner* names — the tool fanned out one corner row
   (`TT_pvt`, which sweeps tt/ss/ff × VDD 3/2.8) into 6 numbered rows. But the
   table never tells me `TT_pvt_3` means "ss process, VDD=3". I'd have to go to
   the Corners tab and mentally cross-reference, or run a CLI explode. The
   results table should spell out the corner definition.

5. **No plot.** Every RF review artifact I produce is a curve — gain vs freq,
   NF vs freq, compression curve. The Results tab is a *table only*. There is no
   plotting in simkit. For "read historical data" that's a real gap: I can see a
   number, never a shape.

6. **History list is flat and short.** It shows 3 runs. There's no filter by
   date, no grouping by testbench, no "failed only" in the GUI tree (the CLI has
   `--failed-only` but the tree doesn't). With 50 runs this list becomes a wall.

**Severity:** *Major annoyance.* I *can* read past data, but the missing specs
(everything `no_spec`) and the missing plots mean the Results tab is not yet a
substitute for the Maestro results table I already trust.

---

## Task 2 — View and edit corners

**What I expected:** See my PVT corner setup as a grid — one row per corner,
columns for process / voltage / temperature — and be able to add a corner the
way I add a row in the Maestro corners form.

**What I actually found:**

The **Corners** tab is the best-feeling part of the tool. It's a real grid:
`Enable | row_name | process | temperature | vdd | model_file | extra_vars`,
with **Add row / Duplicate row / Delete row** buttons, right-click duplicate/
delete, and dropdowns on the process and temperature cells. That maps closely
to the Maestro corners form and felt natural.

But it has serious friction:

1. **The "Send to Maestro" button is greyed out and won't tell me why.**
   This is the worst thing I hit in Task 2. The moment my baseline corner set
   loaded, "Send to Maestro" was disabled. There is *no message* on screen
   explaining it. I'd be stuck — I'd assume the tool is broken or I'm not
   "connected."

   The real reason (which I only learned by looking deeper): two rows have
   `model_file = rf018.scs`, a *bare filename*. simkit's validation resolves
   that relative to the project folder, doesn't find `rf018.scs` there, and
   silently kills the push button. The first row of the same table has the
   *fully-qualified* path to the same file and validates fine. So my corner
   table is internally inconsistent — and the tool punishes me by disabling the
   action with zero on-screen feedback. **A disabled button with no tooltip and
   no error strip is a dead end for a non-coder.**

2. **The grid columns don't match how a corner is really built.** A corner =
   process + voltage + temperature. The grid has a `vdd` column — but in my
   actual data the supply lives in `extra_vars` as `VDD=3,2.8`, and the `vdd`
   column is blank. So my supply variation is hidden in a free-text
   `extra_vars` cell as `VDD=3,2.8`, while the dedicated `vdd` column sits
   empty. Two places for the same concept; I'd never guess which one Maestro
   actually reads.

3. **`process` cell shows `tt,ss,ff` — a comma-jammed list in one cell.** My
   `TT_pvt` row sweeps three process corners, and the grid crams that into one
   cell as the literal text `tt,ss,ff`. The dropdown offers single picks
   (tt/ss/ff/sf/fs) but to do a sweep I have to *know* to type a comma list. No
   UI hint. And one row that's secretly 6 corners reads visually like one
   corner.

4. **`temperature = 55` everywhere — and the dropdown suggests -40/0/27/85/125
   but not 55.** Fine that it's editable, but it shows the discrepancy: my real
   data uses 55 C, the tool's idea of "common" temps doesn't include it.

5. **No "what does this corner expand to" preview.** The CLI has
   `pvt corners explode` (it printed all 8 sub-corners clearly). The GUI grid
   has nothing equivalent — I can't see that `TT_pvt` becomes 6 runs without
   leaving the GUI.

6. **`Last sync: —` and a "Pull from Maestro" button — but Pull needs the
   bridge + session.** If the bridge is amber and I have no session name, Pull
   just won't do anything useful, and the failure mode (per the code) is a
   modal complaining I need a session. So the natural first click ("Pull from
   Maestro to see my real corners") can dead-end on jargon I don't have.

**Severity:** *Blocker* for the disabled-Send-button-with-no-reason (a new user
genuinely cannot get past it without help). *Major annoyance* for the
vdd/extra_vars split and the comma-list-in-a-cell.

---

## Task 3 — View and edit measurement expressions

**What I expected:** See my outputs/measurements the way the ADE Assembler
Outputs pane shows them — a list of named outputs with their expression — and
edit one.

**What I actually found:**

The **Measures** tab has a left "entry list", three add buttons
(**+ Template / + Raw / + Sweep**), Delete / Move up / Move down, and a right
"Live preview" pane (`output_name | test | expression`) with an **OK** status
and an **Apply to Maestro** button. The live preview is genuinely nice — I can
see the rendered expressions update.

Friction:

1. **"Template" vs "Raw" vs "Sweep" vs "bundle" — four words for things I've
   never heard of.** In Maestro I have *outputs*. simkit has *measure bundles*,
   *templates*, *signal groups*, *raw entries*, *sweep entries*. The tab is
   called "Measures", the file is a "bundle", the left tree calls it a "Bundle",
   and inside it entries are "Template/Raw/Sweep". That's a vocabulary I have to
   learn before I can touch my own outputs. Nothing in the GUI defines these
   terms.

2. **The entry list is cryptic.** Rows read like
   `[raw]      PN_1M  ← value(PN_wave 1000000)` and
   `[template] cycle_wrap_positive  ⨯  signal_group=(none)`. The `←` and `⨯`
   glyphs, the `[raw]`/`[template]` prefixes — it's a compact developer
   notation, not a designer's "Outputs" list.

3. **To edit an expression I have to know to *double-click* the row.** There is
   no Edit button. Double-click opens a small "Edit entry" dialog. Discoverable
   only by accident.

4. **The edit dialog exposes raw fields with cryptic labels.** For a raw entry I
   get: `output_name`, `raw_expression`, `param_overrides (k=v;k=v)`,
   `alias_suffix`, `spec`. `param_overrides (k=v;k=v)` is asking me to type a
   mini key-value syntax. `alias_suffix` — no idea. For a template entry I get a
   `template:` dropdown and a `signal_group:` dropdown with `(none)`. None of it
   is explained. As a non-coder I can change `output_name` and maybe `spec`;
   everything else is a coin toss.

5. **The expressions themselves are bare SKILL/OCEAN.** The bundle I opened has
   entries like `average(riseTime(vtime('tran "/Vout") 0 nil VAR("VDD") nil 10
   90 t "time"))` and `rfEdgePhaseNoise(?result "pnoise_sample_pm0" ?eventList
   'nil)`. The tool presents these as flat editable text. I don't write
   OCEAN — if I have to hand-edit that string, the "I'm not a programmer"
   promise is broken. The *Template* mechanism is presumably the fix for this,
   but there's no guided "build a measurement" path — I'd have to already know
   which of the 21 templates (`db20_ratio`, `dft_mag_at_freq`, …) does what.

6. **CLI/GUI inconsistency that would confuse me badly:** the GUI's left tree
   correctly shows `Bundles (1)` with my bundle in it. But if I drop to the CLI
   and run `pvt measure list-bundles`, it prints **"(no .measure.json files
   found)"** — because the CLI looks in a `measurements/` folder while the bundle
   actually lives in `bundles/`. Same project, two tools, opposite answers. A
   new user who tries both will think the tool lost their file.

7. **No spec column in the preview.** Same gap as Task 1 — the preview shows
   `output_name | test | expression`, no spec, so the round-trip to "results
   table with pass/fail" is broken at both ends.

**Severity:** *Major annoyance.* The live preview is good, but the vocabulary
wall (bundle/template/signal-group/raw/sweep) plus hand-editable OCEAN strings
plus the list-bundles-finds-nothing CLI bug make this feel like a developer
tool, not a designer tool.

---

## Task 4 — Design a batch simulation for a module

**What I expected:** "Run my block across its corners" — pick the testbench,
pick the corner set, pick the outputs, hit Run, get a table.

**What I actually found:**

This is split across concepts: a **review** (`.review.json`) is the runnable
batch definition; it contains **items**; each item names **tests** + a
**union** (corner set) + optionally a **bundle** (outputs). You author one via
the **New Review Wizard** (right-click the Reviews group → "+ New Review
(wizard)…").

The wizard is 4 steps: Step 1 Project & name, Step 2 Items, Step 3 Failure
handling, Step 4 Review & save.

Friction:

1. **"Review" is the wrong word for me.** To an IC designer a *review* is a
   meeting — PDR / CDR / FDR with a panel. simkit uses "review" to mean a
   batch-run definition file. So "create a review" reads like "schedule a
   meeting", not "set up a simulation suite." Every time the wizard says
   "review" I have to translate.

2. **Step 2 "Items" is the crux and it's unguided.** The wizard says *"Add one
   item per (tests, union, bundle) grouping."* I have to already understand that
   trio. The items table has columns `Item name | Tests (comma-separated) |
   Union | Bundle`. "Tests (comma-separated)" expects me to *type* test names
   exactly as they exist in Maestro — there's no picker that reads my testbench
   and lists "Test, Test_trans". If I typo a test name, nothing catches it in
   the wizard.

3. **The wizard let me reach Step 4 with an empty item.** I clicked through and
   the Step-4 preview showed:
   ```json
   "items": [ { "name": "", "tests": [], "union": "" } ]
   ```
   An item with no name, no tests, no corner set. The wizard's "Next" didn't
   stop me. (Final validation on *Finish* may catch it — but letting me build a
   hollow review and only complain at the very end is backwards. I want to be
   blocked early, at the field.)

4. **Step 4 shows me raw JSON.** The final confirmation screen is a pretty-
   printed `.review.json` blob, complete with `"review_schema_version": 1` and a
   `—` escape in the `_doc` string. I'm being asked to "confirm the
   assembled review" by reading JSON. I don't read JSON. A plain-English summary
   ("Suite *review_suite*: runs test X across corner set Y, 8 corners") is what
   I'd need.

5. **Step 3 "Failure handling" is all developer concepts.** `Default policy:
   skip`, `Retry strategy: (none)`, `Max attempts`. For a designer the natural
   question is "if a corner doesn't converge, what happens?" — but I'd have to
   map that onto "policy" and "retry strategy" myself, with no explanation of
   what "skip" vs the alternatives do.

6. **The corner set must already exist as a "union".** The wizard's Union column
   is a dropdown of existing `.union.json` files. If I haven't authored a union
   yet, I can't make a runnable review — and the wizard doesn't offer to create
   one or send me to the Corners tab. The four tasks are coupled but the GUI
   doesn't walk me across them.

7. **Running it needs the Session + bridge again.** Even with a valid review,
   the "Run this review" button (over in the Results tab header) will pop a
   modal `缺少 Maestro session` if I haven't filled the Session box. So the last
   click of the whole workflow dead-ends on the same jargon as Task 2.

8. **Two ways to run, no guidance on which.** I can right-click a review in the
   tree → "Run this review…", or select it and use the "Run this review" button
   in the Results-tab header. Fine, but a new user won't know the button is even
   tied to tree selection (it's greyed until you click a review).

**Severity:** *Major annoyance.* The wizard exists and is 4 clean steps, but
"review" terminology, the unguided Items step, the hollow-review-reaches-Step-4
gap, and the raw-JSON confirmation mean a first-timer will not author a correct
batch sim without help.

---

## Prioritized friction list

Ranked by how much each would hurt a real first-time designer.

| # | Friction | Where | Severity | Why it hurts |
|---|----------|-------|----------|--------------|
| 1 | **"Send to Maestro" silently disabled, no reason shown** — bare `model_file` filename fails path validation, kills the button with no tooltip/error strip | Corners tab | **Blocker** | A non-coder cannot diagnose or get past it; looks like the tool is broken |
| 2 | **Everything reads `no_spec` / `spec = —`** — no specs anywhere, no GUI way to add them | Results + Measures | **Major** | Kills the core promise (auto pass/fail); back to reading numbers by eye |
| 3 | **Vocabulary wall** — module / bridge / session / review / union / bundle / template / signal-group / raw / sweep, none defined in-app | Whole GUI | **Major** | Designer must learn a new language before doing anything |
| 4 | **No plots** — Results tab is table-only | Results tab | **Major** | Every RF review artifact is a curve; can't see gain/NF/compression shape |
| 5 | **Hand-editable OCEAN strings** in the measure editor; no guided "build a measurement" | Measures tab | **Major** | Breaks the "not a programmer" promise for anything beyond a node tap |
| 6 | **Wizard reaches Step 4 with an empty item**; final confirm is raw JSON | New Review Wizard | **Major** | Lets me build a hollow batch sim; asks me to verify by reading JSON |
| 7 | **CLI `pvt measure list-bundles` finds nothing** while the GUI shows the bundle (looks in `measurements/` vs `bundles/`) | CLI vs GUI | **Major** | Two tools, opposite answers — user thinks the file is lost |
| 8 | **Corner concept split across `vdd` column and `extra_vars` text** | Corners tab | **Major** | Supply variation hidden in free text; ambiguous which Maestro reads |
| 9 | **Exploded corner names (`TT_pvt_3`) with no definition shown** | Results + Corners | **Minor** | Must cross-reference or shell out to `pvt corners explode` |
| 10 | **Mixed Chinese/English UI**, sometimes in one sentence | Dialogs, log, labels | **Minor** | Inconsistent, looks unfinished |
| 11 | **Run names default to dev junk** (`orch_Test_basic_1779240708_1`, `c1_sanity_run`) | History tree | **Minor** | Hard to scan "which run was my gain sweep" |
| 12 | **No cold-start onboarding** — blank Results tab, `(no run selected)`, no "here's your block / last run" landing | First launch | **Minor** | User pokes around by trial and error |
| 13 | **Edit-measurement is double-click-only**, no Edit button | Measures tab | **Minor** | Undiscoverable affordance |
| 14 | **Amber bridge dot + "Restart bridge" never explained** | Top bar | **Minor** | Designer doesn't know if amber is a problem |

---

## Overall verdict

simkit's *bones* are right for a designer — a corner grid that looks like the
Maestro corners form, a sortable results table, a measure editor with a live
preview, a 4-step batch-run wizard. The Corners grid and the Measures live
preview are the closest to "natural."

But as a first-time, GUI-only RF designer I would **get stuck**. The single
hardest wall is the silently-disabled "Send to Maestro" button (#1) — a dead end
with no explanation. Right behind it: the tool currently shows me numbers with
**no specs** (#2), so it doesn't yet replace the Maestro results table I trust;
it makes me learn a **whole new vocabulary** (#3) — "review" especially means
the wrong thing to me; and it has **no plots** (#4), which no RF reviewer can
live without. The CLI and GUI even disagree about where my files are (#7).

It feels like a capable automation layer built by someone who already knows its
internal model — not yet a tool that *gets out of the way* of a designer who
just wants to sweep a block across its corners and see pass/fail curves.
