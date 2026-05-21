"""MainWindow integration tests — Stage 2 wiring.

Pins the contract between :class:`simkit.gui.main_window.MainWindow` and
the right-panel tab widgets (``ResultsTab`` / ``SummaryTab`` /
``CornerManagerView`` / ``MeasuresEditor``) so future refactors of either
side surface as test failures rather than silent UX regressions.

Scope (Stage 2 — log-only handlers): we only verify that:
  * the static tabs are present + in the documented order;
  * every outbound signal from a tab routes into the bottom log panel
    (the Stage-3-replaceable handler).

We do NOT exercise BridgeWorker here — wiring to BridgeWorker is a
Stage 3 concern.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "python"))


pytest.importorskip("PyQt5")


from simkit.gui.main_window import MainWindow  # noqa: E402
from simkit.gui.views.corner_manager import CornerManagerView  # noqa: E402
from simkit.gui.views.measures_editor import MeasuresEditor  # noqa: E402
from simkit.gui.views.results_tab import ResultsTab  # noqa: E402
from simkit.gui.views.summary_tab import SummaryTab  # noqa: E402


def test_right_panel_has_four_tabs_in_documented_order(qtbot):
    w = MainWindow()
    qtbot.addWidget(w)
    tabs = w.right_panel
    assert tabs.count() == 4
    assert tabs.tabText(0) == "Results"
    assert tabs.tabText(1) == "Summary"
    assert tabs.tabText(2) == "Corners"
    assert tabs.tabText(3) == "Measures"


def test_tab_widgets_are_the_right_classes(qtbot):
    w = MainWindow()
    qtbot.addWidget(w)
    assert isinstance(w.results_tab, ResultsTab)
    assert isinstance(w.summary_tab, SummaryTab)
    assert isinstance(w.corner_manager, CornerManagerView)
    assert isinstance(w.measures_editor, MeasuresEditor)


def test_corners_tab_is_a_usable_corner_manager_at_startup(qtbot):
    """The Corners tab hosts the Corner Manager and is present + usable
    on startup with no load step (user requirement 2026-05-20)."""
    w = MainWindow()
    qtbot.addWidget(w)
    assert w.right_panel.widget(2) is w.corner_manager
    # An empty-but-valid cornermodel — the manager works immediately.
    cm = w.corner_manager.cornermodel()
    assert cm.columns == ()
    assert w.corner_manager.btn_new_mode.isEnabled()


def test_run_requested_logs_to_bottom_panel(qtbot):
    w = MainWindow()
    qtbot.addWidget(w)
    w.results_tab.run_requested.emit("/tmp/foo.review.json")
    text = w.bottom_log.toPlainText()
    assert "run" in text.lower()
    assert "/tmp/foo.review.json" in text


def test_corner_manager_pull_requested_logs(qtbot):
    w = MainWindow()
    qtbot.addWidget(w)
    w.corner_manager.pull_requested.emit()
    # No bridge/module wired → routes through _can_dispatch_bridge.
    assert "pull" in w.bottom_log.toPlainText().lower()


def test_corner_manager_push_requested_logs(qtbot):
    w = MainWindow()
    qtbot.addWidget(w)
    w.corner_manager.push_requested.emit(w.corner_manager.cornermodel())
    assert "push" in w.bottom_log.toPlainText().lower()


def test_measures_apply_requested_logs_row_count(qtbot):
    w = MainWindow()
    qtbot.addWidget(w)
    w.measures_editor.apply_requested.emit([{"output_name": "pn_VDD_1M"}, {}, {}])
    text = w.bottom_log.toPlainText()
    assert "apply" in text.lower()
    assert "3 rendered rows" in text


# --- Cap #6: milestone tagging right-click ----------------------------------

def _build_module_with_run(tmp_path):
    """Construct a project directory + DuckDB so MainWindow.load_module
    finds a single history run we can tag."""
    from simkit.db import connect
    from datetime import datetime as _dt, timezone as _tz

    project_root = tmp_path / "proj"
    project_root.mkdir()
    db_root = tmp_path / "db"
    db_root.mkdir()
    pvtproject = project_root / ".pvtproject"
    pvtproject.write_text(
        '{"_doc": "test", "schema_version": 1, '
        '"project": "milestonetest", '
        f'"dbRoot": "{db_root.as_posix()}", '
        '"author": "tester"}\n'
    )
    db = db_root / "simkit.duckdb"
    con = connect(db)
    try:
        con.execute(
            "CREATE TABLE runs ("
            "  run_id VARCHAR PRIMARY KEY, project_id VARCHAR, "
            "  testbench_id VARCHAR, testbench_alias VARCHAR, "
            "  timestamp TIMESTAMPTZ, author VARCHAR, "
            "  label VARCHAR, note VARCHAR, "
            "  netlist_path VARCHAR, history_name VARCHAR, "
            "  schema_version INT, "
            "  ingested_at TIMESTAMPTZ, "
            "  milestone VARCHAR DEFAULT NULL, starred BOOLEAN DEFAULT FALSE"
            ")"
        )
        con.execute(
            "INSERT INTO runs VALUES "
            "('run-abc-1234', 'milestonetest', 'tb', NULL, ?, 'me', "
            " NULL, NULL, NULL, 'history-1', 2, ?, NULL, FALSE)",
            [_dt(2026, 5, 19, 8, 0, tzinfo=_tz.utc),
             _dt(2026, 5, 19, 8, 1, tzinfo=_tz.utc)],
        )
    finally:
        con.close()
    return pvtproject


def test_apply_milestone_writes_db_and_refreshes_tree(qtbot, tmp_path):
    from simkit.db import connect
    from simkit.gui.loaders import load_module

    pvtproject = _build_module_with_run(tmp_path)
    w = MainWindow()
    qtbot.addWidget(w)
    w.load_module(load_module(pvtproject))

    w._apply_milestone("run-abc-1234", "PDR")

    db_path = w._loaded_module.db_path
    con = connect(db_path, read_only=True)
    try:
        row = con.execute(
            "SELECT milestone FROM runs WHERE run_id = 'run-abc-1234'"
        ).fetchone()
    finally:
        con.close()
    assert row[0] == "PDR"
    # Tree should now contain a "PDR" milestone group.
    assert "PDR" in w._loaded_module.milestones


def test_apply_milestone_clear_reverts_to_null(qtbot, tmp_path):
    from simkit.db import connect
    from simkit.gui.loaders import load_module

    pvtproject = _build_module_with_run(tmp_path)
    w = MainWindow()
    qtbot.addWidget(w)
    w.load_module(load_module(pvtproject))

    w._apply_milestone("run-abc-1234", "CDR")
    w._apply_milestone("run-abc-1234", None)

    con = connect(w._loaded_module.db_path, read_only=True)
    try:
        row = con.execute(
            "SELECT milestone FROM runs WHERE run_id = 'run-abc-1234'"
        ).fetchone()
    finally:
        con.close()
    assert row[0] is None


def test_apply_milestone_without_module_is_safe(qtbot):
    w = MainWindow()
    qtbot.addWidget(w)
    assert w._loaded_module is None
    w._apply_milestone("anything", "PDR")  # must not raise


# --- Spec A5: Restart bridge button ------------------------------------------

def test_restart_bridge_button_hidden_when_green(qtbot):
    from simkit.gui.bridge_worker import BridgeStatus

    w = MainWindow()
    qtbot.addWidget(w)
    w.set_bridge_status(BridgeStatus.GREEN)
    assert w.restart_bridge_button.isVisible() is False, (
        "When bridge is healthy, the restart affordance must not clutter the top bar."
    )


def test_restart_bridge_button_visible_when_amber(qtbot):
    from simkit.gui.bridge_worker import BridgeStatus

    w = MainWindow()
    qtbot.addWidget(w)
    w.show()  # widgets need a visible parent to report isVisible() truthfully
    w.set_bridge_status(BridgeStatus.AMBER)
    assert w.restart_bridge_button.isVisible() is True


def test_restart_bridge_button_visible_and_emphasized_when_red(qtbot):
    from simkit.gui.bridge_worker import BridgeStatus

    w = MainWindow()
    qtbot.addWidget(w)
    w.show()
    w.set_bridge_status(BridgeStatus.RED)
    assert w.restart_bridge_button.isVisible() is True
    style = w.restart_bridge_button.styleSheet()
    # Red theming + bold to draw the eye in the broken state.
    assert "bold" in style.lower()
    assert "c0392b" in style.lower() or "red" in style.lower()


def test_restart_bridge_button_click_invokes_worker_restart(qtbot):
    from unittest import mock as _mock

    w = MainWindow()
    qtbot.addWidget(w)
    # Inject a fake worker to capture restart() — set_bridge_worker also
    # wires controllers + does auto-detect probes, so go direct.
    fake_worker = _mock.MagicMock()
    w._worker = fake_worker
    w._on_restart_bridge_clicked()
    fake_worker.restart.assert_called_once_with()


def test_restart_bridge_button_click_without_worker_is_safe(qtbot):
    # Early in app boot the worker hasn't been wired yet. Clicking must
    # not crash; it should log + return.
    w = MainWindow()
    qtbot.addWidget(w)
    assert w._worker is None, "precondition: worker not wired yet"
    w._on_restart_bridge_clicked()  # must not raise
    assert "restart requested before worker" in w.bottom_log.toPlainText().lower()


# --- G-15: explanatory bridge status tooltips --------------------------------

def test_bridge_status_dot_tooltip_explains_each_state(qtbot):
    from simkit.gui.bridge_worker import BridgeStatus

    w = MainWindow()
    qtbot.addWidget(w)
    w.set_bridge_status(BridgeStatus.RED)
    red_tip = w.status_dot.toolTip()
    assert "down" in red_tip and "Restart bridge" in red_tip
    w.set_bridge_status(BridgeStatus.GREEN)
    assert "connected" in w.status_dot.toolTip()
    w.set_bridge_status(BridgeStatus.AMBER)
    assert "unconfirmed" in w.status_dot.toolTip()


# --- G-7: vocabulary tooltips + glossary -------------------------------------

def test_session_input_has_explanatory_tooltip(qtbot):
    w = MainWindow()
    qtbot.addWidget(w)
    tip = w.session_input.toolTip()
    assert "session" in tip.lower()
    assert tip != ""


def test_help_menu_opens_glossary(qtbot):
    from unittest import mock as _mock

    w = MainWindow()
    qtbot.addWidget(w)
    # The action exists and is wired; exec_ is patched so the modal does
    # not block the test.
    with _mock.patch(
        "simkit.gui.main_window.GlossaryDialog"
    ) as fake_dialog:
        w._glossary_action.trigger()
    fake_dialog.assert_called_once()


def test_sanitize_history_prefix_drops_punctuation():
    from simkit.gui.main_window import _sanitize_history_prefix

    assert _sanitize_history_prefix("PN check CDR") == "PN_check_CDR"
    assert _sanitize_history_prefix("v17/gmin-2") == "v17_gmin_2"
    assert _sanitize_history_prefix("hello!?world") == "helloworld"
    assert _sanitize_history_prefix("___") == "run"   # empty after strip
    assert _sanitize_history_prefix("") == "run"
    assert _sanitize_history_prefix("ok_name") == "ok_name"


# --- _serialize_union carries _file_abs (SFE-73) ----------------------------

def _one_model_union(file_abs):
    from simkit.union import Union, UnionRow, ModelEntry

    return Union(
        union_schema_version=1, name="u", project="demo",
        testbench_id="LIB/cell/sch",
        rows=(
            UnionRow(
                row_name="TT", vars={},
                models=(ModelEntry(
                    file="rf018.scs", block="Global", test="All",
                    section=("tt",), file_abs=file_abs,
                ),),
            ),
        ),
    )


def test_serialize_union_emits_file_abs_when_present():
    import json as _json
    from simkit.gui.main_window import _serialize_union

    out = _json.loads(_serialize_union(_one_model_union("/pdk/models/rf018.scs")))
    model = out["rows"][0]["models"][0]
    assert model["_file_abs"] == "/pdk/models/rf018.scs"
    assert model["file"] == "rf018.scs"


def test_serialize_union_omits_file_abs_when_absent():
    import json as _json
    from simkit.gui.main_window import _serialize_union

    out = _json.loads(_serialize_union(_one_model_union(None)))
    assert "_file_abs" not in out["rows"][0]["models"][0]


# --- File > Open Module… menu item ------------------------------------------

def test_menu_bar_exists(qtbot):
    """MainWindow must expose a QMenuBar (not None and not empty)."""
    w = MainWindow()
    qtbot.addWidget(w)
    mb = w.menuBar()
    assert mb is not None
    assert mb.actions(), "menu bar has no menus"


def test_file_menu_has_open_module_action(qtbot):
    """File menu must contain an 'Open Module…' action."""
    w = MainWindow()
    qtbot.addWidget(w)
    mb = w.menuBar()
    # Find the File menu by title (strip & mnemonic marker).
    file_menu = None
    for action in mb.actions():
        if "file" in action.text().lower().replace("&", ""):
            file_menu = action.menu()
            break
    assert file_menu is not None, "No 'File' menu found in menu bar"
    action_texts = [a.text() for a in file_menu.actions()]
    assert any("open module" in t.lower() for t in action_texts), (
        f"'Open Module…' action not found in File menu; found: {action_texts}"
    )


def test_open_module_action_wired_to_handler(qtbot):
    """_open_module_action must exist as a QAction attribute and be connected."""
    w = MainWindow()
    qtbot.addWidget(w)
    action = getattr(w, "_open_module_action", None)
    assert action is not None, "_open_module_action attribute missing from MainWindow"
    from PyQt5.QtWidgets import QAction
    assert isinstance(action, QAction)


def test_open_module_cancels_gracefully(qtbot, monkeypatch):
    """Cancelling the file dialog (empty string returned) must not crash or log."""
    from PyQt5.QtWidgets import QFileDialog

    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        staticmethod(lambda *a, **kw: ""),
    )
    w = MainWindow()
    qtbot.addWidget(w)
    w._on_open_module()  # must not raise
    assert w.bottom_log.toPlainText() == "", (
        "Cancelling open-module dialog should not write to the log"
    )


# --- File > Sync Maestro History --------------------------------------------

def test_file_menu_has_sync_history_action(qtbot):
    """File menu must contain a 'Sync Maestro History' action."""
    w = MainWindow()
    qtbot.addWidget(w)
    mb = w.menuBar()
    file_menu = None
    for action in mb.actions():
        if "file" in action.text().lower().replace("&", ""):
            file_menu = action.menu()
            break
    assert file_menu is not None
    action_texts = [a.text() for a in file_menu.actions()]
    assert any("sync maestro history" in t.lower() for t in action_texts), (
        f"'Sync Maestro History' action missing; found: {action_texts}"
    )


def test_sync_history_done_logs_summary_and_handles_failures(qtbot):
    """_on_sync_maestro_history_done must log the mirror/skip/fail counts."""
    w = MainWindow()
    qtbot.addWidget(w)
    w._on_sync_maestro_history_done(
        {
            "mirrored": ["Interactive.0", "Interactive.1"],
            "skipped": ["orch_x"],
            "failed": [{"history": "bad", "error": "rdb unreadable"}],
        }
    )
    log = w.bottom_log.toPlainText()
    assert "2 mirrored" in log
    assert "1 already present" in log
    assert "1 failed" in log
    assert "bad" in log and "rdb unreadable" in log


# --- regression tests: scenario-testing bug fixes ---------------------------


def _add_review(pvtproject_path, name, *, valid=True):
    """Drop a .review.json into the project's reviews/ dir."""
    reviews = pvtproject_path.parent / "reviews"
    reviews.mkdir(exist_ok=True)
    path = reviews / f"{name}.review.json"
    if valid:
        path.write_text(
            '{"review_schema_version": 1, "name": "%s", '
            '"project": "milestonetest", "items": []}' % name
        )
    else:
        path.write_text("{ this is not valid json")
    return path


def test_open_module_resolves_directory_to_pvtproject(qtbot, tmp_path, monkeypatch):
    """getExistingDirectory hands back a directory; _on_open_module must
    resolve it to the .pvtproject file before load_module sees it."""
    from PyQt5.QtWidgets import QFileDialog

    pvtproject = _build_module_with_run(tmp_path)
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory",
        staticmethod(lambda *a, **kw: str(pvtproject.parent)),
    )
    w = MainWindow()
    qtbot.addWidget(w)
    w._on_open_module()
    assert w._loaded_module is not None
    assert w._loaded_module.project_name == "milestonetest"


def test_open_module_warns_when_dir_has_no_pvtproject(qtbot, tmp_path, monkeypatch):
    from PyQt5.QtWidgets import QFileDialog

    empty = tmp_path / "notamodule"
    empty.mkdir()
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory",
        staticmethod(lambda *a, **kw: str(empty)),
    )
    warned = []
    monkeypatch.setattr(
        MainWindow, "_warn", lambda self, title, text: warned.append(title)
    )
    w = MainWindow()
    qtbot.addWidget(w)
    w._on_open_module()
    assert w._loaded_module is None
    assert warned


def test_broken_review_keeps_run_button_disabled(qtbot, tmp_path):
    from simkit.gui.loaders import load_module

    pvtproject = _build_module_with_run(tmp_path)
    _add_review(pvtproject, "broken", valid=False)
    w = MainWindow()
    qtbot.addWidget(w)
    w.load_module(load_module(pvtproject))
    broken = next(
        r for r in w._loaded_module.reviews if r.review_name == "broken"
    )
    assert broken.parse_error
    w._select_review(broken)
    assert w.results_tab.run_button.isEnabled() is False


def test_good_review_enables_run_and_is_remembered(qtbot, tmp_path):
    from simkit.gui.loaders import load_module

    pvtproject = _build_module_with_run(tmp_path)
    path = _add_review(pvtproject, "good", valid=True)
    w = MainWindow()
    qtbot.addWidget(w)
    w.load_module(load_module(pvtproject))
    good = next(
        r for r in w._loaded_module.reviews if r.review_name == "good"
    )
    w._select_review(good)
    assert w.results_tab.run_button.isEnabled() is True
    assert w.current_review_path() == str(path.resolve())


def test_restore_session_rebinds_last_selected_review(qtbot, tmp_path):
    from simkit.gui.loaders import load_module

    pvtproject = _build_module_with_run(tmp_path)
    path = _add_review(pvtproject, "good", valid=True)
    w = MainWindow()
    qtbot.addWidget(w)
    w.load_module(load_module(pvtproject))
    w.restore_session(None, None, str(path.resolve()))
    assert w.current_review_path() == str(path.resolve())
    assert w.results_tab.run_button.isEnabled() is True


def test_warn_if_op_stalled_logs_when_op_still_pending(qtbot):
    w = MainWindow()
    qtbot.addWidget(w)
    w._pending_ops[99] = {
        "on_ok": None, "on_err": None, "func": "pvt_corners_push",
    }
    w._warn_if_op_stalled(99)
    assert "may be stuck" in w.bottom_log.toPlainText()


def test_warn_if_op_stalled_silent_when_op_completed(qtbot):
    w = MainWindow()
    qtbot.addWidget(w)
    before = w.bottom_log.toPlainText()
    w._warn_if_op_stalled(4242)  # never registered → already done
    assert w.bottom_log.toPlainText() == before


def test_open_corner_model_loads_into_the_single_corners_tab(qtbot, tmp_path):
    import json

    w = MainWindow()
    qtbot.addWidget(w)
    cm_path = tmp_path / "demo.cornermodel.json"
    cm_path.write_text(json.dumps({
        "cornermodel_schema_version": 1,
        "name": "demo",
        "project": "1AXX",
        "testbench_id": "sim_yusheng/Test/maestro",
        "modes": {"M": {"vars": {"d_en": "1"}}},
        "columns": [{"mode": "M", "pvt_label": "TT", "enabled": True,
                     "pvt_vars": {"temperature": "55"}}],
    }), encoding="utf-8")

    view = w.open_corner_model(cm_path)
    # Loads into the existing Corners tab — no new tab is added.
    assert view is w.corner_manager
    assert w.right_panel.count() == 4
    assert w.right_panel.tabText(2) == "Corners"
    assert w.right_panel.currentWidget() is w.corner_manager
    assert w.corner_manager.cornermodel().name == "demo"


def test_discover_cornermodel_honours_cornermodelsdir(qtbot, tmp_path):
    """A custom cornerModelsDir in the .pvtproject must be searched, not
    just the project root (RFIC-designer walkthrough finding 2026-05-21)."""
    import json

    from simkit.gui.loaders import load_module

    pvtproject = _build_module_with_run(tmp_path)
    # point cornerModelsDir at a non-default subdir
    data = json.loads(pvtproject.read_text())
    data["cornerModelsDir"] = "my_corners"
    pvtproject.write_text(json.dumps(data))
    cm_dir = pvtproject.parent / "my_corners"
    cm_dir.mkdir()
    (cm_dir / "lo.cornermodel.json").write_text(json.dumps({
        "cornermodel_schema_version": 1, "name": "lo",
        "project": "milestonetest", "testbench_id": "tb",
        "modes": {"M": {"vars": {"d_en": "1"}}},
        "columns": [{"mode": "M", "pvt_label": "TT", "enabled": True,
                     "pvt_vars": {"temperature": "55"}}],
    }))

    w = MainWindow()
    qtbot.addWidget(w)
    w.load_module(load_module(pvtproject))
    assert w.corner_manager.cornermodel().name == "lo"
    assert "discovered lo.cornermodel.json" in w.bottom_log.toPlainText()


def test_open_corner_model_bad_file_logs_and_returns_none(qtbot, tmp_path):
    w = MainWindow()
    qtbot.addWidget(w)
    w._warn = lambda *a, **k: None  # a real QMessageBox blocks offscreen Qt
    bad = tmp_path / "broken.cornermodel.json"
    bad.write_text("{not json", encoding="utf-8")
    assert w.open_corner_model(bad) is None
    assert "[corner-model] load failed" in w.bottom_log.toPlainText()


# --- Push safety gate -------------------------------------------------------

class _RecordingWorker:
    """Minimal BridgeWorker stand-in — records queued ops, returns req ids."""

    def __init__(self):
        self.ops = []

    def queue_op(self, func_name, **kwargs):
        self.ops.append((func_name, kwargs))
        return len(self.ops)


def _cm_with_one_column():
    from simkit.corner_model import add_column, add_mode, empty_cornermodel, Column
    cm = add_mode(empty_cornermodel(name="t", project="P", testbench_id="l/c/v"),
                  "M", {"d_en": "1"})
    return add_column(cm, Column(mode="M", enabled=True,
                                 pvt_vars={"temperature": ("55",)},
                                 models=(), pvt_label="TT"))


def _write_union(path, row_names):
    import json
    path.write_text(json.dumps({
        "union_schema_version": 1, "name": "snap", "project": "P",
        "testbench_id": "l/c/v",
        "rows": [{"row_name": n, "vars": {"temperature": ["55"]}}
                 for n in row_names],
    }), encoding="utf-8")


def test_corner_push_snapshots_before_pushing(qtbot, tmp_path):
    """Push must first queue a pvt_corners_pull snapshot — never push direct."""
    from simkit.gui.loaders import load_module

    pvtproject = _build_module_with_run(tmp_path)
    w = MainWindow()
    qtbot.addWidget(w)
    w.load_module(load_module(pvtproject))
    w._worker = _RecordingWorker()
    w.session_input.setText("fnxSession0")

    w._on_corner_model_push(_cm_with_one_column())

    assert w._worker.ops, "no op was queued"
    assert w._worker.ops[0][0] == "pvt_corners_pull"
    assert "snapshots/" in w._worker.ops[0][1]["out_path"]
    # no push has been queued yet — it waits for snapshot + confirmation
    assert all(op[0] != "pvt_corners_push" for op in w._worker.ops)


def test_corner_push_confirm_yes_queues_push(qtbot, tmp_path):
    from unittest import mock as _mock
    from simkit.corner_model import materialize
    import simkit.gui.main_window as mw

    pvtproject = _build_module_with_run(tmp_path)
    w = MainWindow()
    qtbot.addWidget(w)
    from simkit.gui.loaders import load_module
    module = load_module(pvtproject)
    w.load_module(module)
    w._worker = _RecordingWorker()

    snapshot = tmp_path / "snap.union.json"
    _write_union(snapshot, ["TT", "OLD_CORNER"])  # OLD_CORNER not in the model
    u = materialize(_cm_with_one_column())  # materialises corner "M_TT"

    with _mock.patch.object(mw.QMessageBox, "exec_",
                            return_value=mw.QMessageBox.Yes):
        w._corner_model_push_confirm(u, snapshot, "fnxSession0", module)
    assert any(op[0] == "pvt_corners_push" for op in w._worker.ops)


def test_corner_push_confirm_no_skips_push(qtbot, tmp_path):
    from unittest import mock as _mock
    from simkit.corner_model import materialize
    import simkit.gui.main_window as mw

    pvtproject = _build_module_with_run(tmp_path)
    w = MainWindow()
    qtbot.addWidget(w)
    from simkit.gui.loaders import load_module
    module = load_module(pvtproject)
    w.load_module(module)
    w._worker = _RecordingWorker()

    snapshot = tmp_path / "snap.union.json"
    _write_union(snapshot, ["TT", "OLD_CORNER"])
    u = materialize(_cm_with_one_column())

    with _mock.patch.object(mw.QMessageBox, "exec_",
                            return_value=mw.QMessageBox.No):
        w._corner_model_push_confirm(u, snapshot, "fnxSession0", module)
    assert all(op[0] != "pvt_corners_push" for op in w._worker.ops)
    assert "cancelled by user" in w.bottom_log.toPlainText()
