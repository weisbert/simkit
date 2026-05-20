# 1AXX RF Block — Design-Review Scenario Catalog

*Author: RF/analog IC designer (~8 yrs), GUI-only Virtuoso/Maestro user. No SKILL, no OCEAN, no Python — everything below is done by clicking, or not done at all.*

This document catalogs the concrete simulation-automation scenarios I live through taking the **1AXX** RF block (treat it as an LNA-class front-end block — gain stage with matching, biasing, and an output buffer) through PDR, CDR, and FDR. It is written so another team can check, scenario by scenario, whether their tooling supports what I actually need.

Today's reality for 1AXX: one testbench cell with two tests, **"Test"** (a DC/quick functional poke) and **"Test_trans"** (a transient sanity check), plus a baseline corner set of about **3 corners** (roughly tt/25C/nominal-supply, and two rough extremes). That is a debug TB. It is not review evidence. Part 0 says what it has to become.

---

## Part 0 — Making the 1AXX debug TB review-ready

The current "Test" / "Test_trans" pair tells me the circuit isn't dead. It tells a reviewer almost nothing. Below is what the 1AXX testbench actually has to contain before I can stand in front of a PDR/CDR/FDR panel. I build all of this by hand in ADE Assembler — adding tests, adding analyses, adding outputs, typing corner definitions into the corners setup form.

### 0.1 Tests / analyses the review TB must contain

I expect the review-ready 1AXX TB to be a **set of named tests** under one Assembler view, each test being one schematic + one or more analyses:

| Test name | Analysis | What it proves |
|---|---|---|
| `dcop` | DC operating point | Every device is in the right region; bias currents and node voltages are sane; quiescent supply current `Idd`. |
| `sp` | SP (S-parameter), swept frequency | S11, S22, S21, S12, stability factor (Kf, B1f), small-signal gain, input/output match across band. |
| `noise_sp` | SP with noise enabled (or dedicated `noise` analysis on the SP test) | NF vs frequency, NFmin, minimum-noise source impedance. |
| `pss_p1db` | PSS (shooting or HB) + swept input power | Gain compression curve, P1dB (input- and output-referred), DC current vs drive. |
| `pss_pac_gain` | PSS + PAC | Large-signal conversion/transducer gain at the operating tone, gain vs frequency under real drive. |
| `pss_pnoise` | PSS + Pnoise | Noise under large-signal drive; for any block with switching/LO content, phase-noise-relevant numbers. |
| `pss_pac_ip3` | PSS (two-tone) + PAC, or HB two-tone | IIP3 and IIP2 from a two-tone test at defined tone spacing. |
| `tran_startup` | Transient | Bias/enable startup, settling time, no latch-up, no oscillation on power-up. |
| `tran_pulse` | Transient (enable pulsed) | Enable/disable timing, turn-on transient, droop. |

That is the realistic RF spread: DCOP, SP, SP-noise, PSS+PAC, PSS+Pnoise, PSS two-tone for linearity, HB as an alternative engine where shooting struggles, and transient for startup. The debug TB has 2 of these (badly). The review TB needs ~9.

### 0.2 Measurements / outputs each test must export

Outputs are defined in the Assembler **Outputs** pane as expressions/measurements, and must be the *same names every milestone* so regression is possible:

- **Gain**: `S21_dB` (small-signal), `Gt_dB` (transducer gain under PSS), gain flatness across band (`max-min` over the band).
- **Match**: `S11_dB`, `S22_dB`, worst-case in-band return loss.
- **Stability**: `Kf` (must be >1), `B1f` (must be >0), across full frequency range, not just in-band.
- **Noise**: `NF_dB` at band center and band edges, `NFmin_dB`.
- **Linearity**: `P1dB_in`, `P1dB_out`, `IIP3`, `IIP2`, `OIP3`.
- **Power**: `Idd` quiescent, `Idd_maxdrive`, total `Pdc`.
- **Bias health**: key device `Vds`/`Vgs`/`region` checks, headroom margins.
- **Transient**: `t_settle`, `t_enable`, overshoot %, oscillation flag.

Each must be a saved, named output so it lands in the Assembler results table as a column. If a measurement is only ever read off a plot by eye, it is not review evidence.

### 0.3 PVT corners the review TB must sweep

The 3-corner debug set is replaced with a real corner matrix. A corner = process + voltage + temperature, defined in the Assembler corners setup:

- **Process**: `tt`, `ss`, `ff`, `sf`, `fs` for the active devices; plus passive/RC skew (`res_min/typ/max`, `cap_min/typ/max`) where the model files allow it.
- **Voltage**: `Vdd` nominal, `-5%`, `+5%` (and `-10%/+10%` for an automotive-grade variant if 1AXX targets that).
- **Temperature**: `-40C`, `27C`, `85C`, `125C`.

That is a full grid of up to 5 x 3 x 4 = 60 corners. For PDR I run a reduced "box" set (~6-9 corners: the obvious extremes). For CDR I want the full PVT cross. For FDR I add **Monte Carlo** (process + mismatch, a few hundred points) on the gain/NF/linearity outputs, and a **post-layout / extracted** corner pass.

### 0.4 Sweep variables

Defined as design/global variables in the TB and swept via the Assembler **Variables**/sweep setup:

- `Pin` — input power, swept for compression/P1dB and for the IIP3 sweep.
- `freq` — RF frequency across the operating band (and a wider span for stability).
- `Vdd` — supply, also a corner axis.
- `Ibias` — bias-current setting (design knob; sensitivity sweep).
- `temp` — temperature, as a corner axis.

### 0.5 Spec limits

Every output above gets a **spec line** entered in the Assembler **Specs** column so the results table auto-flags pass/fail. Representative 1AXX targets (LNA-class):

| Spec | Limit | Notes |
|---|---|---|
| `S21_dB` | ≥ 16 dB | in-band, all corners |
| Gain flatness | ≤ 1.0 dB | across operating band |
| `NF_dB` | ≤ 2.2 dB | band center, all corners |
| `S11_dB` | ≤ -10 dB | in-band |
| `S22_dB` | ≤ -10 dB | in-band |
| `Kf` | > 1 | DC to 2x top frequency |
| `IIP3` | ≥ -5 dBm | two-tone |
| `P1dB_in` | ≥ -18 dBm | — |
| `Idd` | ≤ 12 mA | nominal, with corner ceiling ≤ 15 mA |
| `t_settle` | ≤ 2 µs | enable-to-settled |

**Part 0 pain points (non-coder, by hand):** Entering 9 tests x dozens of outputs x 60 corners through GUI forms is a multi-day clickfest. Keeping output *names* identical across milestones is purely my discipline — one rename and regression silently breaks. Corner definitions get copy-paste-edited and a temperature gets left at 27C in a row that should say 125C; nothing warns me. There is no good way for me to *diff* "the TB I reviewed at PDR" vs "the TB now" — I rely on memory.

**Part 0 success criteria:** The TB has all 9 tests with stable output names; the full corner matrix is defined once and reusable; specs are entered so the results table flags pass/fail; and I can produce the same evidence package shape at PDR, CDR, and FDR without rebuilding it each time.

---

## Part A — PDR scenarios

PDR is early. Architecture and feasibility. I want to know the design *can* meet spec with margin, on a reduced corner box, before committing to detailed design.

### PDR-1 — First S-parameter + NF feasibility sweep

- **ID:** PDR-1
- **Review stage / context:** Early 1AXX, schematic-level, first time the matching network has real component values.
- **Goal:** Show the panel that gain, input/output match, and NF are in the right ballpark with margin, at nominal and a couple of extremes.
- **Steps:** In Assembler I add the `sp` test, point it at the 1AXX schematic, add an SP analysis swept over the band plus a wide span for stability, enable noise on the SP analysis. In Outputs I add `S21_dB`, `S11_dB`, `S22_dB`, `NF_dB`, `Kf`, `B1f`. I select a reduced corner box (tt/27C/nom, ss/125C/-5%, ff/-40C/+5%). I hit Run, wait, then read the results table.
- **Artifacts needed:** A gain/match/NF-vs-frequency plot at nominal; a small 3-corner table of in-band S21, S11, S22, NF, Kf; a one-line margin statement vs the Part 0 spec limits.
- **Pain points / failure modes:** Building the SP test and the 6 outputs by hand is slow. If I forget to enable noise on the SP analysis, NF columns come back empty and I don't notice until I'm building slides. Picking "a couple of extremes" by eye means I might miss the actually-worst corner. Reading worst-case across 3 corners x 5 outputs off a table by eye is error-prone.
- **Success criteria:** A clean 3-corner table with pass/fail flags against spec, plus the nominal plot, produced without me hand-transcribing numbers.

### PDR-2 — Compression / P1dB rough check

- **ID:** PDR-2
- **Review stage / context:** PDR, right after PDR-1, to prove linearity headroom exists.
- **Goal:** Get a rough P1dB and confirm the block doesn't compress inside the expected operating drive range.
- **Steps:** Add the `pss_p1db` test. Set up a PSS analysis at the operating tone, declare `Pin` as a swept variable from -40 dBm up to ~0 dBm. Add outputs `Gt_dB` vs `Pin`, and a P1dB measurement (gain drops 1 dB from small-signal). Run at tt/27C/nom plus one hot/slow corner.
- **Artifacts needed:** Gain-compression curve (`Gt_dB` vs `Pin`), extracted `P1dB_in`/`P1dB_out` numbers, comparison to the -18 dBm target.
- **Pain points / failure modes:** PSS may not converge on the first try; tuning shooting/HB settings is trial-and-error and I have no scripted retry. The P1dB "compression point" measurement function needs the small-signal reference gain set correctly — if the `Pin` sweep doesn't start low enough, the reference is already compressed and P1dB reads optimistically. Easy to ship a wrong number.
- **Success criteria:** A compression curve plus a P1dB value I trust, with the small-signal reference clearly low enough, at 2 corners.

### PDR-3 — Bias / DC operating-point health check

- **ID:** PDR-3
- **Review stage / context:** PDR, architecture sign-off on the bias scheme.
- **Goal:** Confirm every device sits in the intended region with headroom across the corner box, so the architecture is viable.
- **Steps:** Add the `dcop` test, DC operating-point analysis. Add outputs for key device `Vds`, `Vgs`, saturation/region flags, and `Idd`. Run across the reduced corner box including the -40C and 125C extremes.
- **Artifacts needed:** A device-by-device bias table per corner; headroom margin numbers; `Idd` vs corner.
- **Pain points / failure modes:** Pulling `Vds`/`region` for a dozen devices means hand-adding a dozen outputs and knowing each device's instance path. At a hot/slow corner a device can drop out of saturation and the only way I catch it is scanning the table row by row. No automatic "this device left saturation at ss/125C" flag unless I built that expression myself, which as a non-coder I mostly don't.
- **Success criteria:** A per-corner bias table where any device leaving its intended region is visibly flagged, not buried.

### PDR-4 — Startup / stability transient sanity

- **ID:** PDR-4
- **Review stage / context:** PDR, feasibility of the enable/bias-up behavior.
- **Goal:** Show the block powers up cleanly, settles, and doesn't oscillate.
- **Steps:** Upgrade the existing `Test_trans` into `tran_startup`: transient analysis with `Vdd` and the enable ramping up. Add outputs for `t_settle`, an oscillation/overshoot check on the output node. Run at nominal and a fast corner (ff/-40C, worst for ringing).
- **Artifacts needed:** Startup waveform plots, settling-time number, an explicit "no oscillation" statement.
- **Pain points / failure modes:** "No oscillation" is something I judge by zooming into the waveform by eye — there's no robust automatic detector I can set up without scripting. Transient at a fast cold corner is the one that rings, and if I only run nominal I'll miss it. Long transient runs and I'm just waiting.
- **Success criteria:** Startup plots at 2 corners plus a settling number, with a defensible (ideally automatic) oscillation check.

### PDR-5 — Reduced-corner margin summary for the PDR panel

- **ID:** PDR-5
- **Review stage / context:** PDR, assembling the actual review package.
- **Goal:** One consolidated margin table across all PDR tests and the reduced corner box, to present.
- **Steps:** After PDR-1..4 have run, I go to the Assembler results table, collect worst-case values per output across the corner box, and build a summary: spec / worst-case / margin / pass-fail.
- **Artifacts needed:** A single PDR margin table (spec, worst corner, worst value, margin, verdict); the supporting plots; a short list of risks.
- **Pain points / failure modes:** Today this is me copy-pasting numbers from the results table into a spreadsheet and computing margin by hand. Worst-case across corners has to be picked manually per row. If I re-run anything afterward, the spreadsheet is stale and there's no link back. High risk of a transcription error in the exact table the panel scrutinizes.
- **Success criteria:** The margin table is generated directly from the run results — no manual transcription — and clearly identifies which corner drove each worst case.

---

## Part B — CDR scenarios

CDR is detailed design. Full PVT coverage, every key spec simulated, margin demonstrated, and explicit regression against the PDR milestone.

### CDR-1 — Full PVT corner sweep on S-parameters and NF

- **ID:** CDR-1
- **Review stage / context:** CDR, design is detailed and frozen-ish; need full corner coverage.
- **Goal:** Demonstrate gain, match, NF, and stability hold across the full PVT matrix.
- **Steps:** Take the `sp`/`noise_sp` tests from PDR. In corners setup, expand from the reduced box to the full process x voltage x temperature grid (up to ~60 corners). Run all. Read the results table, sort by each output to find worst corners.
- **Artifacts needed:** Full-corner table for S21, S11, S22, NF, Kf; worst-corner callout per spec; gain/NF spread plots across corners.
- **Pain points / failure modes:** 60 corners is a long run; if a few corners fail to converge I get a partial table and have to hunt for the blanks. Sorting a 60-row table per output to find worst case is tedious and I can mis-sort. If the corner set was edited by hand since PDR (Part 0 pain), some corners may be subtly wrong and I won't know.
- **Success criteria:** Complete 60-corner table with no silent blanks, worst corner per spec identified automatically, clear pass/fail.

### CDR-2 — Linearity: IIP3 / IIP2 two-tone across corners

- **ID:** CDR-2
- **Review stage / context:** CDR, linearity must be proven, not estimated.
- **Goal:** Full IIP3 and IIP2 numbers across the PVT corners.
- **Steps:** Build/finish the `pss_pac_ip3` test: PSS two-tone (or HB two-tone) at defined tone spacing, PAC to pull the IM3/IM2 products. Add `IIP3`, `IIP2`, `OIP3` outputs. Run across the full corner matrix, or at least the linearity-critical subset.
- **Artifacts needed:** IIP3/IIP2 per corner, worst-corner values, an IM-product-vs-`Pin` plot at the worst corner.
- **Pain points / failure modes:** Two-tone PSS/HB is the heaviest, slowest, least convergent analysis I run; per-corner convergence babysitting is real. Tone spacing and the PAC sideband indices have to be set right or IIP3 is silently wrong. Getting consistent IIP3 across 60 corners by hand is genuinely hard.
- **Success criteria:** A trustworthy IIP3/IIP2 corner table where convergence failures are surfaced, not hidden as blanks.

### CDR-3 — Regression of CDR results vs the PDR milestone

- **ID:** CDR-3
- **Review stage / context:** CDR, the panel explicitly asks "what changed since PDR."
- **Goal:** Show, output by output, how 1AXX performance moved between PDR and CDR, and explain it.
- **Steps:** Compare the CDR results table against the PDR results table. For every shared output and shared corner, compute the delta. Flag anything that regressed.
- **Artifacts needed:** A PDR-vs-CDR delta table (output, corner, PDR value, CDR value, delta, better/worse); narrative for each notable change.
- **Pain points / failure modes:** This is the worst one by hand. The PDR run is weeks old; finding the exact saved results is archaeology. Output names or corner names may have drifted (Part 0 pain) so rows don't line up. I end up eyeballing two spreadsheets side by side. Easy to miss a quiet regression on a non-headline spec.
- **Success criteria:** An automatic, name-matched delta between two milestone result sets, with mismatched/missing outputs explicitly called out rather than silently dropped.

### CDR-4 — Sweep on a key design variable (bias-current sensitivity)

- **ID:** CDR-4
- **Review stage / context:** CDR, justifying the chosen bias point.
- **Goal:** Show how NF, gain, linearity, and `Idd` trade off vs `Ibias`, and that the chosen point is robust.
- **Steps:** In the relevant tests, declare `Ibias` as a swept variable over a realistic range. Run the sweep, ideally crossed with a couple of corners. Plot NF, S21, IIP3, Idd vs `Ibias`.
- **Artifacts needed:** Trade-off curves vs `Ibias`; a marked chosen operating point; sensitivity numbers (e.g. dNF/dIbias).
- **Pain points / failure modes:** Crossing a variable sweep with corners multiplies run count fast and the results table becomes a huge multi-dimensional thing that's awkward to slice in the GUI. Picking the "knee" of a trade-off curve is by-eye. If I want the same sweep on three tests I set it up three times by hand.
- **Success criteria:** Clean trade-off curves, the variable-x-corner result set easy to slice, chosen bias point justified with numbers.

### CDR-5 — Phase noise / large-signal noise check

- **ID:** CDR-5
- **Review stage / context:** CDR, noise under real drive conditions.
- **Goal:** Confirm noise behavior under large-signal operation meets spec across corners.
- **Steps:** Run the `pss_pnoise` test: PSS to establish the operating state, Pnoise for the noise spectrum. Add the relevant noise outputs. Run across corners.
- **Artifacts needed:** Noise-vs-offset/frequency plots, key noise numbers per corner, margin to spec.
- **Pain points / failure modes:** PSS+Pnoise convergence again; the PSS state must be the right operating point or the Pnoise result is meaningless. Reading noise at specific offsets across many corners is tedious. If the PSS tone or power got edited inconsistently between tests, the noise number is quietly wrong.
- **Success criteria:** Consistent per-corner noise numbers tied to a verified PSS operating state, with margin shown.

### CDR-6 — Full CDR margin package with pass/fail across all specs

- **ID:** CDR-6
- **Review stage / context:** CDR, building the review deliverable.
- **Goal:** One consolidated CDR margin table covering every Part 0 spec, full PVT, with verdicts.
- **Steps:** Aggregate worst-case results from CDR-1..5 across the full corner matrix into one table: spec, limit, worst value, worst corner, margin, pass/fail.
- **Artifacts needed:** Complete CDR margin table; the per-spec worst-corner plots; the CDR-vs-PDR regression (from CDR-3); risk/waiver list for anything tight or failing.
- **Pain points / failure modes:** Same transcription risk as PDR-5 but bigger — more specs, more corners, more tests. If any test was re-run after I built the table, it's stale. Keeping the package internally consistent (the margin table, the plots, the regression all from the *same* run) is pure manual discipline.
- **Success criteria:** A single coherent CDR package generated from one consistent set of runs, no hand transcription, every spec with a verdict and a named worst corner.

---

## Part C — FDR scenarios

FDR is signoff. Full corner plus Monte Carlo, extracted/post-layout where available, final margin table, regression vs CDR, and an evidence package someone signs.

### FDR-1 — Post-layout / extracted-netlist corner re-run

- **ID:** FDR-1
- **Review stage / context:** FDR, layout is done and parasitic-extracted.
- **Goal:** Re-prove all key specs on the extracted netlist across full PVT, and quantify the schematic-to-extracted hit.
- **Steps:** Switch the tests' netlist/view to the extracted (parasitic) view, keep the same tests/outputs/specs/corners, re-run the full matrix.
- **Artifacts needed:** Full-corner extracted results table; schematic-vs-extracted delta per spec (gain droop, NF bump from parasitics); updated margins.
- **Pain points / failure modes:** Extracted netlists are heavier and slower and converge worse; the 60-corner run gets long. I must be sure *every* test actually switched to the extracted view — miss one and that test's "extracted" numbers are still schematic, and nothing flags it. Parasitics typically erode match and gain, so worst corners can move.
- **Success criteria:** Confirmed all-tests-extracted run, full-corner table, clear schematic-vs-extracted delta, updated margins.

### FDR-2 — Monte Carlo on gain / NF / linearity

- **ID:** FDR-2
- **Review stage / context:** FDR, statistical signoff.
- **Goal:** Show yield / statistical margin on the headline specs (process + mismatch).
- **Steps:** Set up Monte Carlo (process + mismatch) on the `sp`/`noise_sp`/`pss_pac_ip3` tests, a few hundred samples, at the nominal and a couple of stressful corners. Run. Read histograms and sigma.
- **Artifacts needed:** Histograms for S21, NF, IIP3; mean, sigma, min/max; Cpk-style margin or yield estimate vs spec.
- **Pain points / failure modes:** Hundreds of MC points x several specs x a few corners is the longest run of the whole project. A handful of MC points won't converge and skew the stats if I don't notice the count is short. Pairing MC with corners multiplies everything. By-hand, I just read the Assembler histogram stats — computing a real yield number is awkward without scripting.
- **Success criteria:** Clean histograms with full sample counts, sigma/yield numbers per headline spec, non-converged samples surfaced and excluded knowingly.

### FDR-3 — Final regression: FDR vs CDR

- **ID:** FDR-3
- **Review stage / context:** FDR, panel asks "what moved since CDR, and is anything worse."
- **Goal:** Output-by-output delta CDR→FDR, with the post-layout effects explained.
- **Steps:** Compare the FDR extracted/MC results against the CDR schematic results, per output per corner. Flag every regression, attribute it (parasitic loss, layout mismatch, etc.).
- **Artifacts needed:** CDR-vs-FDR delta table; explicit list of every spec that moved worse; narrative attribution.
- **Pain points / failure modes:** Same as CDR-3 but now the two sets aren't even the same netlist (schematic vs extracted), so a naive compare conflates "design changed" with "parasitics added." Lining up CDR and FDR result sets by output/corner name by hand is slow and error-prone. A quiet regression on a secondary spec is exactly what gets missed and caught in silicon.
- **Success criteria:** Name-matched CDR→FDR delta, regressions flagged, each attributed; nothing dropped because a name didn't match.

### FDR-4 — Worst-case corner deep-dive and waiver evidence

- **ID:** FDR-4
- **Review stage / context:** FDR, a spec is tight or marginally failing at one corner.
- **Goal:** Fully characterize the worst corner and produce defensible waiver/risk evidence.
- **Steps:** Identify the worst corner from FDR-1/2. Re-run that single corner with extra detail — finer `Pin`/`freq` sweep, more outputs, longer transient. Inspect waveforms/curves.
- **Artifacts needed:** Detailed single-corner report; the exact margin/shortfall; supporting plots; a risk statement or waiver justification.
- **Pain points / failure modes:** Reproducing exactly one corner out of the 60-corner setup means carefully isolating that corner's process/voltage/temp without disturbing the matrix. Easy to deep-dive a slightly different corner than the one that actually failed. Then I'm presenting evidence for the wrong condition.
- **Success criteria:** The deep-dive provably uses the identical corner that failed in the full run; detailed evidence supports a clear risk/waiver call.

### FDR-5 — Final signoff evidence package

- **ID:** FDR-5
- **Review stage / context:** FDR, assembling the package that gets formally signed.
- **Goal:** A complete, self-consistent, traceable signoff package for 1AXX.
- **Steps:** Aggregate everything: final full-corner margin table (extracted), MC stats, CDR→FDR regression, worst-corner deep-dive, list of waivers. Tie each number to the run it came from.
- **Artifacts needed:** Final margin table with verdicts; MC summary; regression table; waiver list; traceability (which netlist, which corners, which model files, which date for each result).
- **Pain points / failure modes:** Traceability is the killer — by hand I cannot reliably state which exact model-file revision and netlist a six-week-old result used. If one test got re-run after the table was built, the package is silently inconsistent. This is the document we sign, so a stale or mismatched number here is the most expensive mistake in the whole flow.
- **Success criteria:** Every number in the package traces to a known run (netlist + models + corners + date); the package is provably from one consistent set of results; no manual transcription.

### FDR-6 — Cross-milestone trend (PDR→CDR→FDR)

- **ID:** FDR-6
- **Review stage / context:** FDR, showing the design converged over the project.
- **Goal:** A per-spec trend across all three milestones to demonstrate controlled convergence.
- **Steps:** Pull worst-case values per spec from the PDR, CDR, and FDR result sets and put them side by side.
- **Artifacts needed:** A 3-column trend table (PDR / CDR / FDR worst-case per spec) and small trend plots.
- **Pain points / failure modes:** Requires the PDR and CDR result sets to still exist and still be matchable by name — months later, with hand-edited TBs in between, this is fragile. If output names drifted across milestones the trend simply can't be built without manual fixup.
- **Success criteria:** A clean 3-milestone trend per spec, built from preserved, name-consistent result sets.

---

## Part D — Multiple Maestro sessions inside ONE Virtuoso process

I routinely end up with several Maestro/Assembler sessions open at once inside a single Virtuoso. It's not exotic — it's how comparison and parallel work actually happen.

### D-1 — Two design variants side by side

- **ID:** D-1
- **Review stage / context:** CDR-era, deciding between two 1AXX matching-network variants (A and B).
- **Goal:** Run the same test suite on variant A and variant B and compare directly.
- **Steps:** Open Maestro session 1 on the variant-A TB, session 2 on the variant-B TB, in the same Virtuoso. Run both. Flip between the two results tables to compare S21/NF/IIP3.
- **Artifacts needed:** An A-vs-B comparison table across the common specs and corners; a recommendation.
- **Pain points / failure modes:** Two results tables in two windows — comparing means alt-tabbing and reading by eye; there's no combined A-vs-B view. Easy to confuse which window is which variant. If both sessions launch sims at once they contend for licenses/CPU and both slow down with no clear feedback. Worst: I tweak a variable thinking I'm in session A and I'm actually in session B.
- **Success criteria:** Unambiguous which session is which variant; a single combined A-vs-B comparison; no silent cross-contamination of settings between sessions.

### D-2 — Run TB-A while editing TB-B

- **ID:** D-2
- **Review stage / context:** Any gate prep; long run going, I don't want to sit idle.
- **Goal:** Let a long corner run proceed in one Maestro session while I build/edit another TB in a second session.
- **Steps:** Start the full-corner run in session 1. Switch to session 2, add tests/outputs/corners to a different TB.
- **Pain points / failure modes:** Heavy GUI edits in session 2 can make the whole Virtuoso sluggish, which slows the run in session 1. If I accidentally edit shared library cells, I may be changing the design under session 1 *while it runs* — and the half-finished run is now inconsistent with itself. No clear isolation between what session 1 is using and what session 2 is touching.
- **Success criteria:** Session 2 editing never disturbs the in-flight session-1 run; if shared cells would be affected, I'm warned.

### D-3 — One Maestro per analysis type

- **ID:** D-3
- **Review stage / context:** CDR, when SP, PSS-linearity, and transient are all heavy.
- **Goal:** Separate the slow PSS two-tone work into its own session so quick SP iteration isn't blocked.
- **Steps:** Session 1 holds the `sp`/`noise_sp` tests; session 2 holds the heavy `pss_pac_ip3`/`pss_pnoise` tests; session 3 holds transient. Run them somewhat independently.
- **Artifacts needed:** Per-session results that I still have to merge into one per-corner margin table for the review.
- **Pain points / failure modes:** The block's results are now scattered across three sessions and I must hand-merge them into one margin table — and keep straight that all three used the same corner definitions and the same model files. If I update a global variable in one session, the others are out of step and I might not notice.
- **Success criteria:** Results from all three sessions merge cleanly into one consistent margin table; shared variables/corners verifiably identical across sessions.

### D-4 — Comparing a fix against the pre-fix baseline

- **ID:** D-4
- **Review stage / context:** CDR debug; a corner failed and I made a circuit fix.
- **Goal:** Confirm the fix recovers the failing corner without regressing others, against the pre-fix run.
- **Steps:** Keep the pre-fix results open in session 1. In session 2, open the fixed design, run the same corners. Compare.
- **Pain points / failure modes:** The pre-fix results only survive as long as I keep that session open — close Virtuoso and the baseline may be gone. Comparing fixed vs pre-fix is again two tables by eye. I can easily confirm the failing corner is fixed but miss a small regression on a corner that used to pass.
- **Success criteria:** The pre-fix baseline is preserved independent of the session; a full fixed-vs-baseline delta across all corners, not just the one I was chasing.

### D-5 — Recovering when one session wedges

- **ID:** D-5
- **Review stage / context:** Any time; a modal dialog or a convergence-failure popup appears in one session.
- **Goal:** Deal with the stuck session without killing the others' runs.
- **Steps:** A simulation-status or error modal pops in session 2. I find it, dismiss it, decide whether session 2's run is salvageable.
- **Pain points / failure modes:** A modal dialog in one session can block input to the whole Virtuoso process — all my sessions feel frozen until I find and clear the popup, sometimes hidden behind other windows. If I clear it wrong I may abort a run I wanted. No clear per-session status when one is wedged.
- **Success criteria:** A wedged/modal session doesn't freeze the others; clear per-session status; safe recovery without losing good runs.

---

## Part E — Multiple Virtuoso processes, each running multiple Maestro sessions

On a multi-block chip, or when I offload long runs, I have several Virtuoso processes alive at once — different blocks, different hosts, batch vs interactive — each with its own Maestro sessions.

### E-1 — One Virtuoso per block on a multi-block chip

- **ID:** E-1
- **Review stage / context:** Chip-level review; 1AXX is one of several blocks I own.
- **Goal:** Drive 1AXX's reviews while sibling blocks run their own corner sweeps in separate Virtuoso processes.
- **Steps:** Virtuoso #1 = 1AXX with its Maestro sessions; Virtuoso #2/#3 = sibling blocks. I move between processes to check progress and gather results.
- **Artifacts needed:** Per-block margin packages that roll up into a chip-level review.
- **Pain points / failure modes:** No single place to see "what's running where" — I tab through multiple Virtuoso windows hunting for status. Each Virtuoso may use a different model-file or PDK revision and I cannot easily confirm they all match. Rolling per-block evidence into a chip-level story is entirely manual.
- **Success criteria:** A consolidated cross-process run status; verified-consistent PDK/model revisions across all blocks; per-block packages that combine without rework.

### E-2 — Long batch run in one Virtuoso, interactive work in another

- **ID:** E-2
- **Review stage / context:** FDR prep; the 60-corner extracted + MC run takes many hours.
- **Goal:** Offload the giant FDR run into a dedicated Virtuoso while I keep iterating in another.
- **Steps:** Virtuoso #1 launched just to run the FDR matrix. Virtuoso #2 for my interactive debug. I periodically check on #1.
- **Pain points / failure modes:** I have to remember to go look at #1 — no notification when it finishes or stalls. If a corner failed convergence three hours in, I find out hours late. If #2 edits library cells shared with #1, I've corrupted the running FDR job. No clean isolation of the batch job's inputs.
- **Success criteria:** I'm notified when the batch run finishes or stalls; the batch job's inputs are isolated from my interactive edits; partial progress is visible without disturbing it.

### E-3 — Farm / remote-host runs separate from my desktop

- **ID:** E-3
- **Review stage / context:** CDR/FDR; heavy corner and MC runs pushed to compute-farm hosts.
- **Goal:** Run the heavy matrices on farm hosts, keep my desktop Virtuoso responsive.
- **Steps:** Configure tests to dispatch to farm hosts; monitor from Maestro; pull results back.
- **Pain points / failure modes:** Farm host vs desktop can differ in model-file paths, PDK version, or environment, so a farm result may not be apples-to-apples with a desktop result and I won't see it. Job failures on the farm surface as silent blanks in the results table. Pulling results back coherently and confirming the run environment matched is hard for a non-coder.
- **Success criteria:** Farm and desktop runs provably use the same models/PDK/env; farm job failures are surfaced explicitly, not as blanks; results return into one coherent table.

### E-4 — Crash recovery across processes

- **ID:** E-4
- **Review stage / context:** Any gate; a Virtuoso process crashes or its host goes down mid-run.
- **Goal:** Recover with minimum lost work and know exactly what was lost.
- **Steps:** A Virtuoso dies. I restart, reopen the TB, figure out which corners completed and which didn't, re-run only the missing ones.
- **Pain points / failure modes:** After a crash I don't reliably know which corners had finished — I may re-run everything (wasting hours) or assume completion that didn't happen. Results held only in a since-dead session can be gone entirely. Knowing precisely "these 12 corners still need running" is guesswork.
- **Success criteria:** After a crash I can see exactly which corners/tests completed and re-run only the rest; completed results survive the crash.

### E-5 — Coherent evidence gathering across all processes for the review

- **ID:** E-5
- **Review stage / context:** The actual gate review, with results spread across multiple Virtuoso processes and many Maestro sessions.
- **Goal:** One coherent, self-consistent evidence package for 1AXX (and the chip) despite the scatter.
- **Steps:** Collect results from every Virtuoso/session involved, merge into the margin table, regression, and trend, and verify they're mutually consistent.
- **Artifacts needed:** The final review package — margin table, regression vs prior milestone, trend, MC, waivers — assembled from all sources.
- **Pain points / failure modes:** This is where everything from D and E compounds. Results came from different processes, possibly different hosts, possibly different PDK revisions, at different times. By hand I cannot guarantee they're consistent. The single most dangerous review failure: presenting a margin table whose rows quietly came from mismatched conditions, and not knowing it.
- **Success criteria:** One package, provably from consistent conditions (same netlist class, models, PDK, corner defs), every number traceable to its run, no manual transcription, mismatches flagged before the review rather than discovered in silicon.

---

*End of catalog.*
