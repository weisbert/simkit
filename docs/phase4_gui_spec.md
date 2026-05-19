# Phase 4 §1 — GUI / Adoption Layer Spec

**Schema version: 1** (Phase 4 v1). Frozen surface for Phase 4. Any breaking change to user-visible behaviour or `.gui_state.json` shape requires a migration note in `DECISIONS.md`.

Phase 4 builds the **adoption layer** on top of the three architectural pillars closed in Phase 1–3A (Data / Define / Execute). Up to this point `simkit` was driven through the `pvt` CLI surface, dogfood-tested by Claude on behalf of the user. The user — an analog IC designer, NOT a software engineer — has never used the CLI. Phase 4 is therefore not "a convenient wrapper for an experienced CLI user"; it is the **first real user entry point** for the tool. Tier-1 must let the user complete a real signoff cycle end-to-end inside the GUI, otherwise adoption never starts.

This spec is informed by (a) the 2026-05-19 workflow conversation in which the user described their morning ritual, multi-module Design-Review cycles (PDR/CDR/FDR), constant use of diff, and FAIL-handling spanning rerun / corner-edit / measure-edit / netlist-debug; (b) two parallel independent design reviews (architecture + UX) whose joint recommendations are absorbed verbatim below.

---

## 1. Problem statement (one paragraph)

An analog IC designer doing tape-out signoff walks through three Design-Review gates (PDR → CDR → FDR) covering multiple modules (NDIV / charge pump / LDO / …). Each module has its own `.pvtproject` directory and several review files (`pn_review.review.json`, `max_freq.review.json`, `timing_margin.review.json`, …). On any given morning, the user wants to (i) see what ran overnight across all modules, (ii) drill into a specific module's latest run, (iii) compare today's run against a milestone baseline (e.g. CDR-locked), (iv) if something failed: rerun / tweak corners / tweak measures / dig netlist — no single dominant path. Today this is all manual: open Maestro, click test boxes, enable corners, fill Outputs, run, eyeball log, save, open another module, repeat. Phase 4 turns this into one GUI in which the user spends their day, with the CLI demoted to internals.

---

## 2. Architectural decisions (locked)

The following are not negotiable in v1. Any change requires a DECISIONS entry.

| Decision | Pick | Why |
|---|---|---|
| **Stack** | PyQt5 5.15.9 + pytest-qt 4.5.0 + QtAwesome 1.4.2 | Already installed on red zone Python 3.11.4; user's other projects use PyQt5 successfully; zero environment learning curve; offline-installable via existing 3-zone pipeline. |
| **Form factor** | Desktop app, single window (Tier-1) | Web/FastAPI rejected (red-zone deployment of web stack is regulatory unknown). SKILL/Virtuoso form rejected (LLM writes SKILL UI poorly; slow iteration). |
| **Organisation unit** | One `.pvtproject` = one module | Matches user's mental unit ("today I'm working on NDIV"). |
| **CLI entry** | `pvt gui` subcommand | Matches existing CLI surface; no separate `pvt-gui` binary. |
| **Default opening view** | Restore last-visited module + last-selected review | User said: pick up where I left off. |
| **DR milestone mechanism** | Free-string `milestone` tag on runs row | Extends existing `★ starred` infra (DECISIONS #65). User-typed string (`PDR` / `CDR` / `FDR` / `ECO_1` / anything). No enum. |
| **Cross-module visibility** | Narrow status strip in Tier-1 (overrides earlier Tier-2 defer) | Direct response to UX review B#1: morning ritual needs cross-module pulse; narrow strip is much smaller than a full dashboard. |
| **Layout** | Single window: top-bar (status strip + module switcher) / left tree / right tabbed panel / bottom log. See §6. | Module-centric per locked direction. |
| **Persistence** | `.simkit/gui_state.json` per module + `~/.simkit/gui_app.json` global | Schema v1, shape pinned in §8. |
| **Code location** | `python/simkit/gui/` (new package) | Same Python package; same test suite. |

### 2.1 Architectural mandates from agent review (binding)

| Tag | Mandate | Source |
|---|---|---|
| A1 | **Single long-lived `BridgeWorker` QObject on a dedicated QThread.** All SKILL ops marshalled via a request queue; signals carry results back. UI buttons disable while worker is busy. **"Bridge busy" is global state, not per-tab.** | Arch review A#1. The skillbridge socket is single-stream; concurrent calls corrupt response framing → wedges identical to the manual recovery dance. |
| A2 | **`pvt run` runs as a `QProcess` with `MergedChannels`** and emits structured progress as JSONL on stdout (NOT regex parsing). Cancel = SIGTERM + 5s grace + SIGKILL. Cancelled runs land in DB as `partial_run` state, never silent. | Arch review A#2. |
| A3 | **All result tables use `QAbstractTableModel` + `QSortFilterProxyModel`.** Banned: `QTableWidget` anywhere in the codebase. Diff view = a second model that wraps two `run_id`s; `data()` returns delta cells + `BackgroundRole` for highlight. | Arch review A#3. Anticipates 240→1000-row sweeps at month 3. |
| A4 | **`ModuleSession` object** holds per-module state: tree selection, dirty editors, active QProcess handle, last-results-model. Module-switch = save current session to QSettings → swap central widget's session ref. App restart restores last `ModuleSession`. | Arch review A#4. |
| A5 | **Bridge heartbeat every 10s when idle**: `BridgeWorker` runs `evalstring("t")` periodically; status-bar widget renders green/amber/red. Red disables all SKILL-dependent buttons and exposes a one-click "Restart bridge" that runs the documented `pyKillServer` / `pyStartServer ?python "/usr/bin/python3"` recipe. | Arch review A#5. |
| B1 | **Top-bar status strip** (single line, ~80 chars): "Last 24h: 7 done / 1 running / 2 FAIL (NDIV-PDR, CP-CDR)". Each chip click jumps to the run's module view. Cross-module, but a single widget; NOT a full dashboard. | UX review B#1. Addresses morning ritual. |
| B2 | **"Run this review" button lives in the review-header bar**, not buried inside a Run tab. Tabs are for state views; primary action is always visible when a review is selected. | UX review B#2. |
| B3 | **Diff trigger = explicit "Compare" button on every run row.** No double-click. Plus a sticky "Baseline: <milestone>" pin at the top of the Results tab — every Results view is implicitly-diffed against the pinned baseline if one is set. **Diff view has separate tabs for spec-delta vs netlist-delta** (they are two mental modes; never conflate into one modal). | UX review B#3. |
| B4 | **Corner editor mirrors Maestro Tools→Corners affordances**: add-row / duplicate-row / per-row enable checkbox / per-cell dropdown of valid values. Pull/push surfaced as "Sync from Maestro" / "Send to Maestro" buttons with last-sync timestamp visible at all times. | UX review B#4. Otherwise the editor feels like a downgrade and user falls back to Maestro. |
| B5 | **Error translation layer**: `ASSEMBLER-2423` / `axlGetRunStatus returned nil` / DuckDB constraint errors etc. get mapped to plain-language messages with action hints. Raw text behind a "Details" disclosure. Known-error table is curated; unknown errors fall through with a "Report this" link. | UX review B#5. |

---

## 3. User profile and workflow (verbatim recap)

**User**: analog circuit designer; Cadence Virtuoso ICADVM18.1-64b; Python 3.11.4. IC engineer, not SW engineer. Has never used `pvt` CLI; Phase 4 GUI is the first real entry point.

**Verbatim workflow (2026-05-19)**:
1. **Morning ritual**: open computer → "看看昨天的仿真结果, 看看跑出来没, 有没有 error, 看看结果是否需要优化".
2. **Design Review = multi-module**: e.g. NDIV + charge pump in one DR cycle. Each module has many parameters to cover (PN, max freq, timing margin, …).
3. **DR gates**: PDR → CDR → FDR. Each gate revisits the same modules with possibly more corners or tighter specs. Data collected "类似或者增多".
4. **FAIL handling**: all of {rerun, change corner, change measure, dig netlist}. No dominant path.
5. **Comparison**: "电路仿真没有对比就没有设计". Diff is daily, very high frequency.
6. **CLI usage**: zero. "我就想用 GUI."

---

## 4. Tier-1 scope (must ship in v1)

Eight user-facing capabilities. All eight are required for v1 release — Tier-1 is not "all-CLI-coverage", it is "enough to do a real signoff cycle end-to-end".

| # | Capability | CLI verb wrapped | Notes |
|---|---|---|---|
| 1 | **View run results** | `pvt list` + `pvt ingest` already-done | Corner × test × measure table, pass/fail/spec/spec_status columns, failed-corner highlight. Per-run summary header (history name, project, testbench, time, milestone tag). |
| 2 | **Run a review** | `pvt run` | Pick review from left tree → "Run this review" button in review header → progress UI (§13) → results visible when done. |
| 3 | **Diff two runs** | `pvt diff` | Compare button on run rows + baseline pin (§10). |
| 4 | **Edit corner table** | `pvt corners pull/push --replace` | In-GUI table editor (§11), Maestro-affordance mirror. |
| 5 | **Edit measure bundle** | `pvt measure apply/pull` | Signal-group selector + formula-template picker + live render preview (§12). |
| 6 | **Tag run with milestone** | `pvt star` + new milestone field | Right-click run → "Set milestone…" → free-string input. ★ icon + label rendered everywhere the run appears. |
| 7 | **Copy-edit review** | (new) | Select review → "Copy as…" → form editor with all fields pre-filled → save-as. |
| 8 | **Wizard: new review from scratch** | (new) | Step-by-step: testbench → tests → union → bundle → strategy → save. |

### 4.1 Cross-cutting (not user capabilities, but Tier-1)

- Narrow top status strip (B1).
- BridgeWorker singleton + heartbeat indicator (A1+A5).
- Error translation layer (B5).
- `ModuleSession` + persistence (A4).
- `.simkit/gui_state.json` + `~/.simkit/gui_app.json` write on graceful exit.

---

## 5. Tier-2 deferred

Items explicitly NOT in v1; pick up after a real dogfood task.

- Full cross-module dashboard page (narrow strip in Tier-1 partially substitutes).
- Multi-window / detachable tabs.
- Charts / waveform overlay / spectrum view inside the GUI.
- Tier-1 run-progress UI is text+kanban (§13); richer (gantt / per-corner timing) is Tier-2.
- `pvt corners explode` / `pvt validate` / `install-builtins` — Tier-3, may stay CLI-only.
- Cross-project review sharing (`~/.simkit/templates/`).
- GUI for `pvt sync-stars push/pull` standalone (the milestone tag flow already triggers push behind the scenes).

---

## 6. Layout

Single window, single ~1200×800 default size, resizable. ASCII:

```
┌─────────────────────────────────────────────────────────────────────────┐
│ [Module: NDIV ▼] [Recent: NDIV/CP/LDO/...]                  [● Bridge]  │  ← top bar
│ Last 24h: 7 done / 1 running / 2 FAIL [NDIV-PDR ✗] [CP-CDR ✗]            │  ← B1 status strip
├──────────────────┬──────────────────────────────────────────────────────┤
│                  │ Review: pn_review_v3                  [▶ Run]  [⚙]  │  ← review header
│ ▼ Reviews        │                                                       │     (B2 Run lives here)
│   pn_review_v3   │ [Results] [Corners] [Measures] [Wizard/Edit]         │  ← right-panel tabs
│   max_freq_v2    ├──────────────────────────────────────────────────────┤
│   timing_margin  │ Baseline: ★ CDR-2026q2 (pin)         [Compare to…]  │  ← B3 baseline pin
│                  │ ┌─────┬──────┬──────┬──────┬─────────┬────────────┐ │
│ ▼ Milestones     │ │ ✓/✗ │corner│ test │meas. │ value   │ spec       │ │
│   ★ PDR (3)      │ ├─────┼──────┼──────┼──────┼─────────┼────────────┤ │
│   ★ CDR (5)      │ │  ✗  │TT_pvt│ pn   │PN_1M │ -95     │ < -100  ✗  │ │  ← QAbstractTableModel
│   ★ FDR (—)      │ │  ✓  │TT    │ pn   │PN_1M │ -125    │ < -100  ✓  │ │
│                  │ └─────┴──────┴──────┴──────┴─────────┴────────────┘ │
│ ▼ History (24)   │                                                       │
│   8m ago  ✗      │                                                       │
│   3h ago  ✓      │                                                       │
│   yesterday      │                                                       │
│   ...            │                                                       │
├──────────────────┴──────────────────────────────────────────────────────┤
│ Log ▾  [running: pvt run pn_review_v3 --session fnxSession0]            │  ← bottom log
│ 12:03:14  item 1/3 BT2GRX trans PVT — running 4/6 corners                │
│ 12:01:02  item 1/3 BT2GRX trans PVT — submitted to Maestro               │
└─────────────────────────────────────────────────────────────────────────┘
```

### 6.1 Zone descriptions

- **Top bar** (always visible): module selector dropdown + recent-5 quick-list + bridge status dot.
- **B1 status strip** (always visible, second line of top bar): cross-module 24h summary; FAIL chips are clickable shortcuts to the failing run.
- **Left tree** (resizable splitter, default 260px wide): three groups — Reviews / Milestones / History.
- **Right panel** (rest of width): tabbed view following left-tree selection. Each tab content is described in §9–§12.
- **Bottom log** (collapsible, default expanded, 160px high): tails the active `pvt` subprocess + bridge worker debug log. "Details" toggle pages through any error-translation source text.

---

## 7. Module session lifecycle (A4)

A `ModuleSession` is a Python object held by `AppController`. One per opened-this-app-run module. Carries:

```python
@dataclass
class ModuleSession:
    project_path: Path              # .pvtproject dir
    project_name: str
    last_selected_review: Path | None
    left_tree_state: TreeState      # which nodes expanded, which row selected
    dirty_editors: dict[str, EditorState]   # corner/measure pending edits keyed by review path
    active_qprocess: QProcess | None        # running pvt run, if any
    last_results_model: ResultsModel | None # cached for fast tab-switch
    last_run_id_viewed: str | None
```

### 7.1 Switch / persistence

- **Switch to another module** (user picks from dropdown or recent-5): current session → serialise to `.simkit/gui_state.json` in the module's project dir → swap `AppController.current_session`.
- **Close app gracefully**: serialise all open sessions; write `~/.simkit/gui_app.json` recording last-visited module path + recent-5 ring buffer.
- **Open app**: read `~/.simkit/gui_app.json`, load `last_visited` module + its `.simkit/gui_state.json`.
- **Module gone / corrupted state**: fall back to empty session pointing at the dropdown's selected module. Never crash on stale state.

### 7.2 `gui_state.json` shape (v1)

```json
{
  "schema_version": 1,
  "last_selected_review": "reviews/pn_review_v3.review.json",
  "left_tree": {
    "expanded": ["Reviews", "Milestones"],
    "selected_path": ["Reviews", "pn_review_v3.review.json"]
  },
  "active_baseline": "CDR-2026q2",
  "dirty_editors": {}
}
```

`dirty_editors` is intentionally `{}` on graceful save (we apply or discard pending edits at save-time). It only contains entries if the user closes the app mid-edit; on next open, prompt to restore or discard.

---

## 8. Bridge layer (A1 + A5 + B5)

### 8.1 `BridgeWorker` singleton

- One `QObject` subclass, lives on a dedicated `QThread` (NOT the UI thread).
- API: `queue_op(op: BridgeOp) -> int` returns a request ID; result delivered via `op_complete = pyqtSignal(int, object)` or `op_failed = pyqtSignal(int, BridgeError)`.
- Internally serialises ops via a `queue.Queue`. While an op is in flight, `is_busy = True`; `busy_changed` signal toggles UI button-enabled state globally.
- **No tab is allowed to call skillbridge directly.** All SKILL access must go through `BridgeWorker`. Enforced by linter rule in tests: `simkit.skill_bridge` import outside `simkit.gui.bridge_worker` is a test failure.

### 8.2 Heartbeat

- When `is_busy = False`, fire `evalstring("t")` every 10s.
- Three states tracked + rendered as status dot in top bar:
  - **green**: last heartbeat OK within 15s.
  - **amber**: last heartbeat ≥15s old, retrying.
  - **red**: 3 consecutive failures or socket gone.
- Red disables all SKILL-dependent UI controls; shows "Restart bridge" button in top bar. Click runs the recovery recipe (`reference_skillbridge_recovery` memory). If that fails, surface details + manual recipe.

### 8.3 Error translation table (B5)

`simkit/gui/error_translation.py` ships a curated dict. Examples:

| Raw error fragment | Translated message | Action hint |
|---|---|---|
| `ASSEMBLER-2423` | "Maestro 当前有对话框打开 (setupdb temporarily locked)" | "Click here to focus Virtuoso" (focuses via `wmctrl`-equivalent if available; otherwise instructs user) |
| `axlGetRunStatus returned nil` | "Maestro 当前 session 未识别" | "Click in the Maestro window once, then retry" |
| `pvt_runner_no_session` | "Maestro session name 不存在或拼写错误" | "Check the session dropdown matches the live Maestro window" |
| `socket.gaierror` / connection refused | "Virtuoso 没在运行 / skillbridge server 没起" | "Restart bridge button on top right" |
| DuckDB `Constraint violation` | "本地数据库被并发写入" | "Close other simkit instances and retry" |

Unknown errors render the raw text with "Report this" link (file an issue / paste into a 反馈 channel — TBD by user).

### 8.4 Operations exposed by `BridgeWorker`

Wrap exactly the existing `simkit.skill_bridge` functions (one BridgeOp per public function). No new SKILL surface for Phase 4. v1 list:

- `pvt_corners_pull(project, session) -> Union`
- `pvt_corners_push(project, union, replace, dry_run, session)`
- `pvt_measure_pull / apply / restore`
- `pvt_runner_set_history_lock` (used by milestone tag → star push)
- `pvt_save` (used post-run to dump the just-finished history)
- `get_sdb(session)`
- heartbeat (`evalstring("t")`)

---

## 9. Subprocess layer (A2)

### 9.1 `pvt run` execution

- Spawned as `QProcess`, args = `[sys.executable, "-m", "simkit.cli", "run", <review-path>, "--session", <name>, "--gui-jsonl"]`.
- New CLI flag `--gui-jsonl`: switch `pvt run` to emit one JSON object per line on stdout for each progress event. This is **additive** to the existing CLI; humans still see normal output when the flag is absent.
- `setProcessChannelMode(MergedChannels)` so stderr threads into the same stream (we control the producer).
- `readyReadStandardOutput` → drain line-by-line → `json.loads()` each line → emit `progress_event` signal.

### 9.2 JSONL event shape

```json
{"ts": "2026-05-19T12:03:14Z", "event": "item_started", "item_index": 1, "item_name": "BT2GRX trans PVT", "total_items": 3}
{"ts": "...", "event": "item_progress", "item_index": 1, "running": 4, "completed": 0, "failed": 0, "total_corners": 6}
{"ts": "...", "event": "item_completed", "item_index": 1, "run_id": "8e882e98", "completed": 5, "failed": 1, "history_name": "pn_review_v3__1"}
{"ts": "...", "event": "log", "level": "info", "msg": "..."}
{"ts": "...", "event": "review_done", "exit_code": 0, "summary": {...}}
{"ts": "...", "event": "error", "code": "...", "msg": "..."}
```

Producer side (CLI): a tiny `GuiEventEmitter` class with `item_started(...)` / `progress(...)` / etc. methods that print JSONL when `--gui-jsonl` is set, no-op otherwise. Hooked into the existing `_run_strategy_chain` + post-PvtSave path.

### 9.3 Cancel semantics

User clicks "Cancel" mid-run:
1. UI sends SIGTERM to QProcess.
2. CLI's `pvt run` catches SIGTERM → tries graceful shutdown (best-effort `axlStop` if a Maestro run is active) → exits with code 130.
3. UI waits up to 5s for exit; on timeout, sends SIGKILL.
4. Whatever partial result reached DuckDB is tagged with `partial_run=True` in the runs row (additive column, default False).
5. UI shows "Cancelled — partial results saved (N corners completed)" in log + bottom strip.

### 9.4 Output streaming to log panel

- Non-progress events with `event: "log"` go straight to bottom log panel.
- Progress events update the items kanban (right-side sub-panel inside the active review tab while running).

---

## 10. Diff workflow (B3 + A3)

### 10.1 Two triggers (both Tier-1)

| Trigger | UX |
|---|---|
| **Run-vs-run diff** | Click "Compare" button on any run row (in History panel or Results header). Pops chooser: "Compare against which run?" → run-picker dialog (filterable list) → opens diff view. |
| **Run-vs-baseline diff** | Set milestone baseline once via the "Baseline:" pin in the Results tab header (e.g. "★ CDR-2026q2"). All subsequent runs in this module auto-show delta-vs-baseline columns in the Results table. Toggle off via clicking the pin. |

### 10.2 Diff view shape

Opens as a new tab inside the right-panel (not modal, not separate window) titled `Diff: <run_a> vs <run_b>`. Three sub-tabs:

- **Spec delta**: corners × measures table. Cells coloured: pass→fail (red), fail→pass (green), value-change-only (yellow, with delta), unchanged (grey). Filter row: "show only changed" / "show only verdict-flipped".
- **Netlist delta**: side-by-side or unified text diff per testbench (uses existing `pvt diff` netlist output).
- **Spec-string delta**: if the spec strings themselves differ between runs (e.g. spec tightened CDR→FDR), they're shown as a side-by-side mini-table at top. Surfaces "did the bar move?" separately from "did the value move?".

### 10.3 Model layer (A3)

```python
class DiffResultsModel(QAbstractTableModel):
    def __init__(self, run_id_a: str, run_id_b: str): ...
    def data(self, index, role):
        if role == Qt.BackgroundRole: return self._cell_color(index)
        if role == Qt.DisplayRole:    return self._cell_text(index)
        ...
```

`QSortFilterProxyModel` wraps it for the "show only changed" filter without copying data.

---

## 11. Corner editor (B4)

The corner editor opens inside the "Corners" tab of the right panel when the selected left-tree node is a review or a corner sidecar. Edits the live union JSON in memory; "Send to Maestro" pushes via `pvt_corners_push --replace`.

### 11.1 Affordances (mandatory)

- **Add row**: button + keyboard shortcut Ctrl+Shift+N. New row gets a generated unique row_name (`corner_<n>`).
- **Duplicate row**: select row → button or right-click. Suffixes name with `_copy`.
- **Enable checkbox per row** (mirrors Maestro): default ON; OFF means push-as-disabled.
- **Per-cell dropdown** for known-value cells (process: `tt/ss/ff/sf/fs`; temperature: known-list; etc.). Unknown cells stay free-form text input.
- **Pull from Maestro / Send to Maestro** buttons in tab header with "Last sync: 12 min ago" label.

### 11.2 Live-vs-sidecar divergence indicator

When the user opens the editor: if `pull` (run automatically on tab open via BridgeWorker) shows a difference from the on-disk union, render a yellow strip: "Maestro session has 6 rows, your sidecar has 4 — [show diff] [pull overrides sidecar] [keep sidecar]".

### 11.3 Validation

Live validation while user types: any constraint violation (missing `row_name`, model file path doesn't exist, etc.) is highlighted in red on the offending cell + a tooltip explains. "Send to Maestro" button disabled until all red cells clear.

---

## 12. Measure bundle editor

The bundle editor opens in the "Measures" tab when a review is selected. Edits an in-memory `.measure.json`.

### 12.1 Layout (split pane)

```
┌─ Edit ──────────────────────────────┬─ Live preview ──────────────┐
│ Entries:                            │ Output name | Test | Expr   │
│   [+ Template]  [+ Raw]  [+ Sweep]  │ -----------------------------│
│ ┌─────────────────────────────┐    │ pn_VDD_1M    | sim | rfEdge..│
│ │ ▼ entry 1: template=pn_*    │    │ pn_VDD_100k  | sim | rfEdge..│
│ │   template: rfEdge_pn       │    │ Rtime_clk    | sim | risetim │
│ │   signal_group: dco2g_sup   │    │ ...                          │
│ │   params: { ANALYSIS: pss } │    │                              │
│ │   spec: < -100              │    │ [Apply to Maestro]            │
│ ├─────────────────────────────┤    │                              │
│ │ ▼ entry 2: sweep ...        │    │                              │
│ └─────────────────────────────┘    │                              │
└─────────────────────────────────────┴──────────────────────────────┘
```

### 12.2 Live render preview (right sidebar)

- Every keystroke / dropdown change re-renders the bundle via existing `simkit.template_render` and shows the rendered output rows live.
- Render errors (unbound `$SIG`, missing param, collision) shown inline at top of preview panel with the offending entry highlighted.
- "Apply to Maestro" button below preview; disabled while render shows errors.

### 12.3 Pickers

- **Template picker** (left side of an entry): dropdown of installed templates from `<project>/templates/` (+ builtins if `pvt measure install-builtins` was run). Each entry shows the template's `_doc` field on hover.
- **Signal group picker**: dropdown of signal groups in `<project>/signal_groups/`. Quick "+ new" inline opens a small dialog.
- **Param entry**: list of name→value pairs; renders as a small kv-grid. Param keys constrained by the picked template's declared params.

---

## 13. Run progress UI

When `pvt run` is active for the current review, the Run tab content swaps to a progress view:

```
┌─ Progress ──────────────────────────────────────────────────────────┐
│ Review: pn_review_v3 — running                          [Cancel]    │
│                                                                      │
│ Items:                                                               │
│   [✓] 1/3  BT2GRX trans PVT     completed  5/6 ok, 1 fail            │
│   [▶] 2/3  BT2GRX PSS PN        running    4/6 in flight             │
│   [ ] 3/3  LE trans PVT         queued                               │
│                                                                      │
│ (text log streams in bottom log panel)                               │
└──────────────────────────────────────────────────────────────────────┘
```

- No progress bar / no time estimate in Tier-1 (sim wall-clock varies wildly — false precision is worse than honest "?").
- Items list is rebuilt from JSONL events.
- "Cancel" wired to §9.3 cancel semantics.

Tier-2 (deferred): per-corner timing, gantt view, ETA based on history.

---

## 14. Review wizard + copy-edit

### 14.1 Copy-edit (the primary path)

1. Select existing review in left tree.
2. Right-click → "Copy as…" → name prompt.
3. Opens a form-based editor in the right panel with all fields pre-populated:
   - Review name (editable; must be unique within project)
   - Items list (each item editable inline: tests / union / bundle / strategy / on_failure)
   - Suite-level `on_failure` defaults
4. "Save" writes the new `.review.json`; tree refreshes.

### 14.2 Wizard (from-scratch)

For when there's no existing review to copy.

Step 1 — **Project + name**: pick `.pvtproject`, type review name.
Step 2 — **Items**: "Add item" → fill in `name` / pick `tests` (multi-select from live Maestro testbench list, fetched via bridge) / pick `union` (file picker over `<project>/unions/`) / pick `bundle` (file picker over `<project>/bundles/`).
Step 3 — **Failure handling**: pick strategy chain (none / naive_retry / gmin_bump / trans_pss_ic), set `max_attempts`, set per-strategy params.
Step 4 — **Review + save**: shows the assembled JSON in read-only preview; user confirms → write file.

### 14.3 Validation

Both paths run `simkit.review.validate` before write. Errors shown inline at the offending field.

---

## 15. Milestone tagging

### 15.1 UX

- Right-click a run (in History or Results header) → "Set milestone…" → text input with autocomplete from existing milestone strings used in this project (DuckDB query: `SELECT DISTINCT milestone FROM runs WHERE project = ? ORDER BY milestone`).
- Empty string clears the milestone.
- Setting a milestone also stars the run (via existing `pvt star` → Maestro lock round-trip).

### 15.2 Schema (DuckDB)

Schema v3 → v4: add `runs.milestone VARCHAR DEFAULT NULL`. Migration: additive ALTER, existing rows get NULL. CLI `pvt star --milestone <str>` extended for symmetry (optional Phase 4 deliverable; can wait until needed).

### 15.3 Left-tree Milestones group

- Groups runs by milestone string (DISTINCT).
- Counter shows how many runs carry each tag.
- Click a milestone group → filters History view to that milestone.

---

## 16. Top-bar status strip (B1)

### 16.1 Source query

Every 30s, run a DuckDB query:

```sql
SELECT
  COUNT(*) FILTER (WHERE finish_ts > now() - INTERVAL 24 HOUR AND failed_corners = 0)         AS done,
  COUNT(*) FILTER (WHERE finish_ts IS NULL AND start_ts > now() - INTERVAL 24 HOUR)            AS running,
  COUNT(*) FILTER (WHERE finish_ts > now() - INTERVAL 24 HOUR AND failed_corners > 0)         AS fail,
  ARRAY_AGG(...) FILTER (WHERE failed_corners > 0)                                             AS failed_chips
FROM runs
WHERE project IN (SELECT project_name FROM registered_modules);
```

(Schema columns `failed_corners`, `start_ts`, `finish_ts` need to be added — additive migration. v3 already has `timestamp`; add `started_ts` if `timestamp` doesn't fit running-vs-done semantics.)

### 16.2 Render rules

- "Last 24h" rolling window.
- FAIL chips: up to 5 most recent fail-runs as compact chips with module name; rest collapse to "+N more".
- Click chip = open the failing run's module + select the run.

### 16.3 Registered modules list

- Stored at `~/.simkit/gui_app.json::registered_modules`.
- Top-bar's module dropdown is the same list; "Add module…" lets user pick a `.pvtproject` dir to register.

---

## 17. CLI entry (`pvt gui`)

```
$ pvt gui                          # opens GUI, restores last state
$ pvt gui --module <path>          # opens to specific module (overrides last-visited)
$ pvt gui --safe-mode               # skip restore, fresh launch (for state-corruption recovery)
```

Implementation: `python/simkit/cli/gui.py` calls `simkit.gui.app.main()`. Errors before Qt boots (e.g. missing PyQt5) print to stderr in CLI-friendly form.

---

## 18. Tests

### 18.1 Unit tests

`tests/test_gui_*.py` modules, run with regular `pytest`. Coverage:

- `test_gui_module_session.py` — ModuleSession serialise/deserialise, dirty-editor restore prompt.
- `test_gui_bridge_worker.py` — queue ordering, busy state, heartbeat state transitions (mock bridge).
- `test_gui_jsonl_events.py` — `--gui-jsonl` emission shape (no Qt).
- `test_gui_diff_model.py` — DiffResultsModel cell logic on synthesized run pairs.
- `test_gui_error_translation.py` — known-fragment → translated message mapping.
- `test_gui_state_persistence.py` — gui_state.json + gui_app.json round-trip + schema migration stubs.

### 18.2 Widget tests (`pytest-qt`)

Light: enough to pin the wiring, not exhaustive UI rendering.

- `test_widget_corner_editor.py` — add-row / dup-row / per-cell dropdown / validation-blocks-send-button.
- `test_widget_measure_editor.py` — live preview re-renders on edit; apply-button-disabled-on-render-error.
- `test_widget_review_wizard.py` — step navigation, validation per step.
- `test_widget_status_strip.py` — chip render given a DB fixture.

### 18.3 Full pytest must stay green

Current: 1153/0. Phase 4 lands additive tests; no existing test must break. Phase 4 tests run on Linux with `QT_QPA_PLATFORM=offscreen`.

### 18.4 Manual / Tier-2

The "real dogfood gate" — the user runs the GUI on `fnxSession0` and does one full signoff task end-to-end. Acceptance gate: cannot move to Tier-2 work until this passes.

---

## 19. Deployment integration

Leverages the existing 3-zone pipeline (DECISIONS #72; landed 2026-05-19). Phase 4 work items in the pipeline:

1. Add `PyQt5==5.15.9`, `pytest-qt==4.5.0`, `QtAwesome==1.4.2` to `requirements.txt`.
2. Re-freeze `requirements.lock.txt` via `pip freeze --all`.
3. On yellow Windows: re-run `scripts/download_wheels.py` for the new wheels.
4. Bundle a fresh tarball via `scripts/make_payload.py`.
5. On red Linux: deploy via `scripts/unpack_payload.sh` + `deploy_venv.sh` — the existing `current/` symlink + cleanup script handle iteration.

User's existing PyQt5 environment on red zone (per locked decision) means no new system install is needed; Python wheels are sufficient.

---

## 20. Open questions / deferred (parking lot)

1. **Wizard's testbench-list source**: Tier-1 fetches via bridge from live Maestro session. If user wants to draft a review for a not-currently-open testbench, Tier-2 may need a `.pvtproject`-level cached testbench catalog.
2. **Multi-monitor support**: defer until requested.
3. **Theme**: light only in Tier-1. Dark theme is a one-line Qt stylesheet swap if needed later.
4. **Internationalisation**: error translation table is bilingual (中文 + English) for known errors. Future: separate locale files.
5. **`pvt gui` keyboard shortcuts**: spec'd at implementation time; gather a default set then surface in a help dialog.
6. **What happens when user opens GUI without Virtuoso running**: read-only mode — can browse past runs / diff / inspect, but all SKILL-dependent actions disabled (bridge red). Spec'd; details land in §8.

---

## 21. Implementation order (suggested for next session)

This spec is the design contract; implementation is a separate Phase 4 §2 push. Suggested order:

1. Add PyQt5 deps + re-deploy pipeline integration (so the user can install the v0 shell).
2. App skeleton: `pvt gui` entry + main window + top bar + bridge worker + heartbeat (no real functionality yet — proves the architecture).
3. View results path: left tree (Reviews + History) + Results tab + `QAbstractTableModel`.
4. Run path: `--gui-jsonl` CLI flag + QProcess wiring + progress UI.
5. Diff path: Compare button + DiffResultsModel + baseline pin.
6. Corner editor.
7. Measure editor.
8. Milestone tag + status strip (depends on schema migration).
9. Review copy-edit + wizard.
10. Error translation polish + manual dogfood.

Each step is its own commit-sized chunk; user reviews running tool after each, not the spec.

---

## 22. What this spec is NOT

- Not a UX style guide (icons / colours / fonts decided at impl time within Qt defaults + QtAwesome).
- Not a class-level design doc (impl picks classes; this fixes the contract).
- Not a substitute for the dogfood gate — the GUI is only "done" when the user has used it for a real signoff cycle.
