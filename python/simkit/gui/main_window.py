"""MainWindow — Phase 4 Stage 3 wiring.

Stage 2 shipped 3 right-panel tabs with log-only stub handlers. Stage 3
wires those stubs to real ``BridgeWorker.queue_op`` calls, adds module
loading via :mod:`simkit.gui.loaders`, introduces the left-tree
``ProjectTreeModel``, and instantiates the three Stage-3 controllers
(``RunController`` / ``DiffController`` / ``ErrorTranslator``).

External integration points (called from ``simkit.gui.app``):

* :meth:`set_bridge_worker` — inject the shared :class:`BridgeWorker`
  after the worker thread is started. All controllers are instantiated
  lazily inside this method so MainWindow stays runnable for tests that
  don't need a bridge (the 5 outbound signal handlers fall back to
  log-only when no worker is set).
* :meth:`load_module` — populate editors / left tree / Maestro-session
  input from a :class:`LoadedModule` (the output of
  :func:`simkit.gui.loaders.load_module`).
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional

from PyQt5.QtCore import Qt, QModelIndex
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from simkit.gui.bridge_worker import BridgeError, BridgeStatus, BridgeWorker
from simkit.gui.controllers.diff import DiffController
from simkit.gui.controllers.error_translator import ErrorTranslator
from simkit.gui.controllers.run import RunController
from simkit.gui.error_translation import TranslatedError
from simkit.gui.loaders import (
    LoadedHistoryRun,
    LoadedModule,
    LoadedReview,
    editor_rows_to_union_rows,
    load_bundle_for_editor,
    union_to_editor_rows,
)
from simkit.gui.tree_model import ProjectTreeModel
from simkit.gui.views.corners_editor import CornersEditor
from simkit.gui.views.diff_tab import DiffTab
from simkit.gui.views.measures_editor import MeasuresEditor
from simkit.gui.views.results_tab import ResultsTab
from simkit.gui.views.run_progress import RunProgressWidget
from simkit.union import load_union


log = logging.getLogger(__name__)


_DOT_COLORS = {
    BridgeStatus.GREEN: "#2ecc71",
    BridgeStatus.AMBER: "#f1c40f",
    BridgeStatus.RED: "#e74c3c",
}


class MainWindow(QMainWindow):
    """Top-level window. Stage 3 wires real controllers + module loading."""

    DEFAULT_SIZE = (1200, 800)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("simkit")
        self.resize(*self.DEFAULT_SIZE)

        # --- live state (populated lazily) ------------------------------
        self._loaded_module: Optional[LoadedModule] = None
        self._worker: Optional[BridgeWorker] = None
        self._run_controller: Optional[RunController] = None
        self._diff_controller: Optional[DiffController] = None
        self._error_translator: Optional[ErrorTranslator] = None
        self._run_progress: Optional[RunProgressWidget] = None
        self._pending_ops: dict[int, dict[str, Any]] = {}

        # --- top bar ----------------------------------------------------
        self.top_bar = QWidget(objectName="topBar")
        top_bar_layout = QHBoxLayout(self.top_bar)
        top_bar_layout.setContentsMargins(8, 4, 8, 4)

        self.module_selector = QWidget(objectName="moduleSelector")
        ms_layout = QHBoxLayout(self.module_selector)
        ms_layout.setContentsMargins(0, 0, 0, 0)
        self.module_label = QLabel("[Module: -]")
        ms_layout.addWidget(self.module_label)
        top_bar_layout.addWidget(self.module_selector)

        top_bar_layout.addStretch(1)

        # Maestro session input — required for every bridge call. Persisted
        # via ModuleSession so the user types it once per module.
        top_bar_layout.addWidget(QLabel("Session:"))
        self.session_input = QLineEdit(objectName="sessionInput")
        self.session_input.setPlaceholderText("e.g. fnxSession0")
        self.session_input.setFixedWidth(160)
        top_bar_layout.addWidget(self.session_input)

        self.status_dot = QLabel(objectName="statusDot")
        self.status_dot.setFixedSize(14, 14)
        self.status_dot.setToolTip("Bridge status")
        self.set_bridge_status(BridgeStatus.AMBER)
        top_bar_layout.addWidget(self.status_dot)

        # --- status strip (spec B1) -------------------------------------
        self.status_strip = QLabel("Last 24h: -", objectName="statusStrip")
        self.status_strip.setContentsMargins(8, 0, 8, 4)

        # --- left tree --------------------------------------------------
        self.left_tree = QTreeView(objectName="leftTree")
        self.left_tree.setHeaderHidden(True)
        self.left_tree.setMinimumWidth(180)
        self._tree_model = ProjectTreeModel(self)
        self.left_tree.setModel(self._tree_model)
        self.left_tree.clicked.connect(self._on_tree_clicked)

        # --- right panel ------------------------------------------------
        self.right_panel = QTabWidget(objectName="rightPanel")
        self.right_panel.setDocumentMode(True)
        self.right_panel.setTabsClosable(False)

        self.results_tab = ResultsTab()
        self.corners_editor = CornersEditor()
        self.measures_editor = MeasuresEditor()

        self.right_panel.addTab(self.results_tab, "Results")
        self.right_panel.addTab(self.corners_editor, "Corners")
        self.right_panel.addTab(self.measures_editor, "Measures")

        # --- editor signal wiring ---------------------------------------
        self.results_tab.run_requested.connect(self._on_run_requested)
        self.results_tab.compare_requested.connect(self._on_compare_requested)
        self.results_tab.baseline_pinned.connect(self._on_baseline_pinned)
        self.corners_editor.pull_requested.connect(self._on_corners_pull_requested)
        self.corners_editor.push_requested.connect(self._on_corners_push_requested)
        self.corners_editor.show_diff.connect(self._on_corners_show_diff)
        self.corners_editor.pull_overrides_sidecar.connect(
            self._on_corners_pull_overrides_sidecar
        )
        self.corners_editor.keep_sidecar.connect(self._on_corners_keep_sidecar)
        self.measures_editor.apply_requested.connect(self._on_measures_apply_requested)

        # --- bottom log -------------------------------------------------
        self.bottom_log = QTextEdit(objectName="bottomLog")
        self.bottom_log.setReadOnly(True)
        self.bottom_log.setFixedHeight(160)
        self.bottom_log.setPlaceholderText(
            "Log output streams here when pvt run is active."
        )

        # --- assemble ---------------------------------------------------
        h_splitter = QSplitter(Qt.Horizontal, objectName="mainHSplitter")
        h_splitter.addWidget(self.left_tree)
        h_splitter.addWidget(self.right_panel)
        h_splitter.setStretchFactor(0, 0)
        h_splitter.setStretchFactor(1, 1)
        h_splitter.setSizes([260, 940])

        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self.top_bar)
        v.addWidget(self.status_strip)
        v.addWidget(h_splitter, stretch=1)
        v.addWidget(self.bottom_log)
        self.setCentralWidget(central)

    # ----------------------------------------------------------------
    # Public surface (called from simkit.gui.app)
    # ----------------------------------------------------------------

    def set_bridge_status(self, status: BridgeStatus) -> None:
        color = _DOT_COLORS.get(status, "#888")
        self.status_dot.setStyleSheet(
            f"background-color: {color}; border-radius: 7px;"
        )
        self.status_dot.setToolTip(f"Bridge: {status.value}")

    def append_log(self, line: str) -> None:
        self.bottom_log.append(line)

    def set_status_strip(self, text: str) -> None:
        self.status_strip.setText(text)

    def set_bridge_worker(self, worker: BridgeWorker) -> None:
        """Inject the shared BridgeWorker + instantiate Stage-3 controllers.

        Called once from ``app.py`` after the worker thread is started.
        Wires ``op_complete`` / ``op_failed`` to MainWindow's dispatch +
        the ErrorTranslator. Builds the RunController + DiffController so
        the 5 outbound signal handlers can dispatch real work.

        Also kicks off a one-shot session auto-detect via ``queue_op`` —
        if Maestro's current window session resolves and the input field
        is still empty, prefill it. Avoids the "user must know what
        fnxSession0 is" friction (DECISIONS #79 D2 + user feedback).
        """
        self._worker = worker
        worker.op_complete.connect(self._on_op_complete)
        worker.op_failed.connect(self._on_op_failed)
        self._auto_detect_session()

        self._error_translator = ErrorTranslator(self)
        worker.op_failed.connect(self._error_translator.on_op_failed)
        self._error_translator.translated.connect(self._show_translated_error)

        self._run_controller = RunController(parent=self)
        self._run_controller.progress_event.connect(self._on_run_progress_event)
        self._run_controller.run_finished.connect(self._on_run_finished)
        self._run_controller.cancelled.connect(self._on_run_cancelled)
        self._run_controller.error.connect(self._on_run_controller_error)

        self._diff_controller = DiffController(
            db_path_resolver=self._resolve_db_path,
            parent=self,
        )
        self._diff_controller.diff_ready.connect(self._on_diff_ready)
        self._diff_controller.error.connect(
            lambda msg: self.append_log(f"[diff] {msg}")
        )

    def load_module(self, module: LoadedModule) -> None:
        """Populate editors + tree + session input from a LoadedModule.

        Idempotent; replaces any previously loaded module.
        """
        self._loaded_module = module
        self.module_label.setText(f"[Module: {module.project_name}]")
        self.setWindowTitle(f"simkit — {module.project_name}")
        self._tree_model.populate(module)
        self.left_tree.expandAll()
        self.corners_editor.set_project_root(module.project_root)

        # Auto-load the default union/bundle if exactly one candidate each.
        if module.union_default is not None:
            self._load_union_from_disk(module.union_default)
        if module.bundle_default is not None:
            self._load_bundle_from_disk(module.bundle_default)

    def restore_session(self, session_name: Optional[str], baseline: Optional[str]) -> None:
        """Apply persisted ModuleSession bits to the live UI."""
        if session_name:
            self.session_input.setText(session_name)
        if baseline:
            self.results_tab.set_baseline(baseline)

    def _auto_detect_session(self) -> None:
        """Probe Maestro for the currently-focused session; prefill if empty.

        Asynchronous via BridgeWorker — never blocks the UI thread. Only
        fills if the user hasn't typed anything yet (don't overwrite their
        explicit choice). Failures are silent (just no auto-fill).
        """
        if self._worker is None:
            return
        if self.current_session_name():
            return  # user already provided one
        self._queue_op(
            "pvt_runner_get_window_session",
            on_ok=self._on_session_autodetected,
            on_err=None,  # silent fail — user can still type manually
        )

    def _on_session_autodetected(self, sess: object) -> None:
        if not isinstance(sess, str) or not sess:
            return
        # Race: user may have typed during the probe. Don't clobber.
        if self.current_session_name():
            return
        self.session_input.setText(sess)
        self.append_log(f"[session] auto-detected '{sess}' from Maestro")

    def current_session_name(self) -> Optional[str]:
        text = self.session_input.text().strip()
        return text or None

    def current_baseline_run_id(self) -> Optional[str]:
        return self.results_tab.baseline_run_id()

    # ----------------------------------------------------------------
    # Left tree
    # ----------------------------------------------------------------

    def _on_tree_clicked(self, index: QModelIndex) -> None:
        kind = self._tree_model.node_kind(index)
        payload = self._tree_model.node_payload(index)
        if kind == ProjectTreeModel.NODE_KIND_REVIEW and isinstance(payload, LoadedReview):
            self.results_tab.set_review_path(str(payload.review_path))
            self.append_log(f"[tree] selected review {payload.review_name}")
        elif kind == ProjectTreeModel.NODE_KIND_HISTORY and isinstance(
            payload, LoadedHistoryRun
        ):
            self._show_history_run(payload)

    def _show_history_run(self, run: LoadedHistoryRun) -> None:
        if self._loaded_module is None:
            return
        from simkit.db import connect

        try:
            con = connect(self._loaded_module.db_path)
        except Exception as exc:  # noqa: BLE001
            self.append_log(f"[results] could not open DB: {exc}")
            return
        try:
            self.results_tab.set_run(run.run_id, con)
            self.results_tab.set_header(
                history_name=run.history_name or "",
                project_id=self._loaded_module.project_name,
                timestamp=run.timestamp,
                milestone=run.milestone or "",
            )
            self.right_panel.setCurrentWidget(self.results_tab)
        finally:
            con.close()

    # ----------------------------------------------------------------
    # Run path (§5)
    # ----------------------------------------------------------------

    def _on_run_requested(self, review_path: str) -> None:
        if self._run_controller is None:
            self.append_log(f"[run] no bridge — would run {review_path}")
            return
        session = self.current_session_name()
        if not session:
            self._warn(
                "缺少 Maestro session",
                "请在顶部 Session 输入框填写 Maestro session 名 (e.g. fnxSession0).",
            )
            return
        default_name = Path(review_path).stem.replace(".review", "")
        run_name, ok = QInputDialog.getText(
            self,
            "simkit — 命名本次运行",
            "Run name (出现在 History 树 + Maestro history 名里):",
            QLineEdit.Normal,
            default_name,
        )
        if not ok:
            self.append_log("[run] cancelled at name prompt")
            return
        run_name = run_name.strip() or default_name
        sanitized = _sanitize_history_prefix(run_name)

        self._run_progress = RunProgressWidget()
        self._run_progress.cancel_requested.connect(self._on_run_cancel_clicked)
        progress_idx = self.right_panel.addTab(self._run_progress, "Run progress")
        self.right_panel.setCurrentIndex(progress_idx)
        # total_items unknown until first item_started event; use 0 placeholder
        self._run_progress.reset(run_name, total_items=0)
        extra_args = ["--history-prefix", sanitized, "--label", run_name]
        started = self._run_controller.start_run(
            review_path, session=session, extra_args=extra_args,
        )
        if not started:
            self.append_log("[run] could not start — another run in flight or spawn failed")
        else:
            self.append_log(f"[run] launched as {run_name!r} (history-prefix={sanitized!r})")

    def _on_run_cancel_clicked(self) -> None:
        if self._run_controller is not None and self._run_controller.is_running:
            self._run_controller.cancel()
            self.append_log("[run] cancel requested (SIGTERM, 5s grace then SIGKILL)")

    def _on_run_progress_event(self, event: dict) -> None:
        if self._run_progress is not None:
            self._run_progress.handle_event(event)
        # Mirror log events into the bottom panel for visibility.
        if event.get("event") == "log":
            self.append_log(f"[run] {event.get('msg', '')}")
        elif event.get("event") == "error":
            self.append_log(
                f"[run] error: {event.get('code', '?')} {event.get('msg', '')}"
            )

    def _on_run_finished(self, exit_code: int, summary: dict) -> None:
        self.append_log(f"[run] finished exit={exit_code}")
        if self._run_progress is not None:
            self._run_progress.set_running(False)
        # Refresh history so the just-finished run appears.
        if self._loaded_module is not None:
            try:
                from simkit.gui.loaders import load_module

                self._loaded_module = load_module(self._loaded_module.project_path)
                self._tree_model.populate(self._loaded_module)
                self.left_tree.expandAll()
            except Exception as exc:  # noqa: BLE001
                self.append_log(f"[run] history refresh failed: {exc}")

    def _on_run_cancelled(self) -> None:
        self.append_log("[run] cancelled (SIGKILL fired)")
        if self._run_progress is not None:
            self._run_progress.set_running(False)

    def _on_run_controller_error(self, msg: str) -> None:
        self.append_log(f"[run] controller error: {msg}")

    # ----------------------------------------------------------------
    # Diff path (§6)
    # ----------------------------------------------------------------

    def _on_compare_requested(self) -> None:
        if self._diff_controller is None or self._loaded_module is None:
            self.append_log("[diff] no bridge / module loaded")
            return
        current = self.results_tab.current_run_id()
        if not current:
            self.append_log("[diff] no current run selected")
            return
        other = self._diff_controller.pick_run_for_compare(
            self._loaded_module.project_path, current, parent_widget=self,
        )
        if other is None:
            return  # user cancelled
        self._diff_controller.open_diff(
            self._loaded_module.project_path, current, other,
        )

    def _on_baseline_pinned(self, run_id: object) -> None:
        self.append_log(f"[baseline] pin = {run_id!r}")

    def _on_diff_ready(self, widget: object) -> None:
        if not isinstance(widget, DiffTab):
            self.append_log(f"[diff] unexpected widget type {type(widget).__name__}")
            return
        idx = self.right_panel.addTab(widget, widget.title)
        widget.closed.connect(lambda: self._close_tab(widget))
        self.right_panel.setCurrentIndex(idx)

    def _close_tab(self, widget: QWidget) -> None:
        idx = self.right_panel.indexOf(widget)
        if idx >= 0:
            self.right_panel.removeTab(idx)
            widget.deleteLater()

    # ----------------------------------------------------------------
    # Corners (§7)
    # ----------------------------------------------------------------

    def _on_corners_pull_requested(self) -> None:
        if not self._can_dispatch_bridge("corners pull"):
            return
        session = self.current_session_name()
        if not session:
            self._warn_session_required()
            return
        out_path = self._scratch_path("union_pull", ".union.json")
        self._queue_op(
            "pvt_corners_pull",
            on_ok=lambda result: self._on_corners_pulled(out_path),
            kwargs={
                "out_path": str(out_path),
                "pvtproject_path": self._loaded_module.project_path,
                "session": session,
            },
        )
        self.append_log(f"[corners] pull queued → {out_path.name}")

    def _on_corners_pulled(self, sidecar_path: Path) -> None:
        try:
            u = load_union(sidecar_path)
        except Exception as exc:  # noqa: BLE001
            self.append_log(f"[corners] pulled union failed to load: {exc}")
            return
        self.corners_editor.load_union(union_to_editor_rows(u))
        from datetime import datetime
        self.corners_editor.set_last_sync(
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
        self.append_log(f"[corners] pulled {len(u.rows)} rows")

    def _on_corners_push_requested(self, rows: object) -> None:
        row_count = len(rows) if isinstance(rows, list) else "?"
        self.append_log(f"[corners] push requested ({row_count} rows)")
        if not self._can_dispatch_bridge("corners push"):
            return
        if not isinstance(rows, list):
            self.append_log(f"[corners] push payload not a list: {type(rows).__name__}")
            return
        session = self.current_session_name()
        if not session:
            self._warn_session_required()
            return
        # Build a Union from editor rows + serialize to a temp sidecar
        # (pvt_corners_push expects a file path, not in-memory rows).
        module = self._loaded_module
        try:
            u = editor_rows_to_union_rows(
                rows,
                name="gui_push",
                project=module.project_name,
                testbench_id="",  # populated by the SKILL side or rejected; surface error
            )
        except Exception as exc:  # noqa: BLE001
            self.append_log(f"[corners] push rejected: {exc}")
            return
        out_path = self._scratch_path("union_push", ".union.json")
        out_path.write_text(_serialize_union(u), encoding="utf-8")
        self._queue_op(
            "pvt_corners_push",
            on_ok=lambda result: self.append_log(f"[corners] pushed: {result}"),
            kwargs={
                "union_json_path": str(out_path),
                "pvtproject_path": module.project_path,
                "session": session,
                "replace": True,
            },
        )
        self.append_log(f"[corners] push queued ({len(rows)} rows, --replace)")

    def _on_corners_show_diff(self) -> None:
        self.append_log("[corners] show-diff requested (sidecar vs live)")

    def _on_corners_pull_overrides_sidecar(self) -> None:
        self.append_log("[corners] pull-overrides-sidecar requested")
        # Same as plain pull — load_union into editor overwrites local state.
        self._on_corners_pull_requested()

    def _on_corners_keep_sidecar(self) -> None:
        self.corners_editor.set_divergence(0, 0)  # hides the strip
        self.append_log("[corners] keep-sidecar requested — divergence strip dismissed")

    # ----------------------------------------------------------------
    # Measures (§8)
    # ----------------------------------------------------------------

    def _on_measures_apply_requested(self, rendered_rows: object) -> None:
        row_count = len(rendered_rows) if isinstance(rendered_rows, list) else "?"
        self.append_log(f"[measures] apply requested ({row_count} rendered rows)")
        if not self._can_dispatch_bridge("measure apply"):
            return
        if not isinstance(rendered_rows, list):
            self.append_log(
                f"[measures] apply payload not a list: {type(rendered_rows).__name__}"
            )
            return
        session = self.current_session_name()
        if not session:
            self._warn_session_required()
            return
        # template_render.RenderedRow → JSONL-able rows dict (the shape
        # pvt_measure_push expects from disk).
        rows_serialized = [
            {
                "output_name": r.output_name,
                "expression": r.expression,
                "test": r.test,
                "type": r.type,
                "eval_type": r.eval_type,
                "plot": r.plot,
                "save": r.save,
                "alias": r.alias,
                "spec": r.spec,
            }
            for r in rendered_rows
        ]
        rendered_path = self._scratch_path("rendered", ".measure.json")
        rendered_path.write_text(
            json.dumps(
                {"rendered_schema_version": 1, "rows": rows_serialized},
                indent=2,
            ),
            encoding="utf-8",
        )
        self._queue_op(
            "pvt_measure_push",
            on_ok=lambda result: self.append_log(f"[measures] applied: {result}"),
            kwargs={
                "rendered_json_path": str(rendered_path),
                "session": session,
                "pvtproject_path": self._loaded_module.project_path,
                "replace": True,
            },
        )
        self.append_log(
            f"[measures] apply queued ({len(rows_serialized)} rendered rows)"
        )

    # ----------------------------------------------------------------
    # BridgeWorker dispatch + error rendering
    # ----------------------------------------------------------------

    def _queue_op(
        self,
        func_name: str,
        *,
        on_ok: Optional[Callable[[Any], None]] = None,
        on_err: Optional[Callable[[BridgeError], None]] = None,
        kwargs: Optional[dict] = None,
    ) -> int:
        if self._worker is None:
            raise RuntimeError("queue_op called before set_bridge_worker")
        req = self._worker.queue_op(func_name, **(kwargs or {}))
        self._pending_ops[req] = {"on_ok": on_ok, "on_err": on_err, "func": func_name}
        return req

    def _on_op_complete(self, request_id: int, result: object) -> None:
        info = self._pending_ops.pop(request_id, None)
        if info is None:
            return
        cb = info.get("on_ok")
        if cb is None:
            return
        try:
            cb(result)
        except Exception as exc:  # noqa: BLE001
            self.append_log(
                f"[ui] on_ok for {info.get('func')} raised: {exc}"
            )

    def _on_op_failed(self, request_id: int, error: object) -> None:
        info = self._pending_ops.pop(request_id, None)
        if info is None:
            return
        cb = info.get("on_err")
        if cb is None:
            return
        try:
            cb(error)
        except Exception as exc:  # noqa: BLE001
            self.append_log(
                f"[ui] on_err for {info.get('func')} raised: {exc}"
            )

    def _show_translated_error(
        self, request_id: int, translated: object,
    ) -> None:
        if not isinstance(translated, TranslatedError):
            return
        self.append_log(
            f"[error] {translated.headline}\n        {translated.detail}\n"
            f"        {translated.action_hint}"
        )
        # For known errors fire a modal so the user notices immediately;
        # unknown errors stay in the log to avoid dialog spam.
        if translated.is_known:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("simkit — bridge error")
            box.setText(translated.headline)
            box.setInformativeText(translated.action_hint)
            box.setDetailedText(translated.detail)
            box.exec_()

    # ----------------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------------

    def _resolve_db_path(self, project_path: Path) -> Path:
        from simkit.project import _parse_pvtproject

        proj = _parse_pvtproject(Path(project_path).expanduser().resolve())
        return proj.db_root / "simkit.duckdb"

    def _scratch_path(self, prefix: str, suffix: str) -> Path:
        return Path(tempfile.gettempdir()) / f"simkit_{prefix}_{id(self)}{suffix}"

    def _can_dispatch_bridge(self, label: str) -> bool:
        if self._worker is None:
            self.append_log(f"[{label}] no bridge worker — operation skipped")
            return False
        if self._loaded_module is None:
            self.append_log(f"[{label}] no module loaded")
            return False
        return True

    def _warn_session_required(self) -> None:
        self._warn(
            "缺少 Maestro session",
            "请在顶部 Session 输入框填写 Maestro session 名 (e.g. fnxSession0).",
        )

    def _warn(self, title: str, text: str) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(f"simkit — {title}")
        box.setText(text)
        box.exec_()

    def _load_union_from_disk(self, union_path: Path) -> None:
        try:
            u = load_union(union_path)
        except Exception as exc:  # noqa: BLE001
            self.append_log(f"[corners] load_union({union_path}) failed: {exc}")
            return
        self.corners_editor.load_union(union_to_editor_rows(u))
        self.append_log(f"[corners] loaded {len(u.rows)} rows from {union_path.name}")

    def _load_bundle_from_disk(self, bundle_path: Path) -> None:
        if self._loaded_module is None:
            return
        try:
            raw, templates, signals = load_bundle_for_editor(
                bundle_path, self._loaded_module.project_root,
            )
        except Exception as exc:  # noqa: BLE001
            self.append_log(f"[measures] load_bundle({bundle_path}) failed: {exc}")
            return
        self.measures_editor.set_available_templates(templates)
        self.measures_editor.set_available_signal_groups(signals)
        self.measures_editor.load_bundle(raw)
        self.append_log(f"[measures] loaded bundle {bundle_path.name}")


def _sanitize_history_prefix(name: str) -> str:
    """Strip a user-typed run name down to what Maestro accepts as a history-name
    component: alphanumeric + underscore. Spaces and dashes become underscores;
    everything else is dropped. Empty result falls back to 'run'."""
    out_chars: list[str] = []
    for ch in name:
        if ch.isalnum() or ch == "_":
            out_chars.append(ch)
        elif ch in (" ", "-", ".", "/"):
            out_chars.append("_")
    cleaned = "".join(out_chars).strip("_")
    return cleaned or "run"


def _serialize_union(u: Any) -> str:
    """JSON-serialize a Union back into the .union.json on-disk shape."""
    rows_out = []
    for r in u.rows:
        row_dict: dict[str, Any] = {"row_name": r.row_name}
        if r.vars:
            row_dict["vars"] = {k: list(v) for k, v in r.vars.items()}
        if r.models:
            row_dict["models"] = [
                {
                    "file": m.file,
                    "block": m.block,
                    "test": m.test,
                    "section": list(m.section),
                }
                for m in r.models
            ]
        if not r.enabled:
            row_dict["enabled"] = False
        rows_out.append(row_dict)
    return json.dumps(
        {
            "union_schema_version": u.union_schema_version,
            "name": u.name,
            "project": u.project,
            "testbench_id": u.testbench_id,
            "rows": rows_out,
        },
        indent=2,
    )
