# Mandate sweep — 2026-05-19 overnight

For each of the 11 spec mandates we have a programmatic dogfood + the
test file(s) that pin its contract. Manual verification (eye-on-pixels)
is for the user in the morning.

| ID | Mandate | Tonight status | Evidence |
|----|---------|---------------|----------|
| A1 | Single long-lived BridgeWorker on QThread; queue ops; busy state | UNCHANGED — working | `tests/gui/test_bridge_worker.py` (19 tests, +3 for restart) |
| A2 | `pvt run` as QProcess; JSONL stdout streaming; Cancel cascade | **FIXED Bug #1** — added `-u` to argv so JSONL streams line-by-line; `test_unbuffered_subprocess_streams_before_exit` proves first event arrives BEFORE subprocess exits | `tests/gui/test_run_controller.py` (20 tests, +2 for buffering) |
| A3 | Tables = QAbstractTableModel + QSortFilterProxyModel; no QTableWidget | UNCHANGED — working | `tests/gui/test_results_model.py`, `test_diff_model.py` |
| A4 | ModuleSession holds per-module state; survives restart | UNCHANGED — working | `tests/gui/test_module_session.py` |
| A5 | Heartbeat 10s; evalstring("t"); G/A/R dot; **visible "Restart bridge"** | **FIXED** — added `BridgeWorker.restart()` + `MainWindow.restart_bridge_button` (hidden GREEN, plain AMBER, red+bold RED). See `main_window_red.png` | `tests/gui/test_bridge_worker.py::RestartTests` (3), `tests/gui/test_main_window.py` (5 button tests) |
| B1 | Status strip: "Last 24h: X done / Y running / Z FAIL [chips]"; cross-module; clickable | **FIXED** — `StatusStripWidget` + DuckDB aggregate `last_24h_summary()` across recent_modules; refresh on 30s timer, run_finished, GREEN recovery. See `main_window_green.png` for chips | `tests/gui/test_status_strip.py` (14 tests) |
| B2 | "Run this review" button in review header | UNCHANGED — working | `tests/gui/test_results_tab.py` |
| B3 | Compare button + baseline pin + separate spec-delta + netlist-delta tabs | PARTIAL — Compare exists in Results header; right-click-on-History-row Compare path needs review (audit flagged as MED risk). Not blocking. | `tests/gui/test_diff_tab.py` |
| B4 | Corner editor: add/dup/enable/per-cell dropdowns; Sync/Send + timestamp | UNCHANGED — working | `tests/gui/test_corners_editor.py` |
| B5 | Error translation: raw → zh-CN + actions; known-error table; "Report this" | UNCHANGED — working | `tests/gui/test_error_translation.py` |
| Cap#6 | Milestone tagging via right-click on History row | **FIXED** — wrote `simkit.milestone.set_run_milestone`; right-click menu now offers editable "Set milestone… (current)" + "Clear milestone" with PDR/CDR/FDR presets; refreshes tree on apply | `tests/test_milestone.py` (11), `tests/gui/test_main_window.py` (3 milestone tests) |
| Cap#7 / #8 | Copy-edit + Wizard | **OUT OF SCOPE** — entire features absent; flagged as nice-to-have, not part of overnight "大差不差" cut. Audit confirms these are Stage 5+ scope. | n/a |

## Final regression

`pytest tests/ -q` → **1549 passed, 93 subtests passed** (was 1512 at session start, +37 new tests).

## Screenshots (offscreen Qt — proves the layout renders correctly)

- `main_window_green.png` — bridge healthy, restart button hidden, 3 FAIL chips
- `main_window_amber.png` — bridge stale, plain restart button
- `main_window_red.png` — bridge broken, red+bold restart button

These were rendered at 1280×800 with the production `MainWindow`
constructor — they reflect actual production output, not a mock.
