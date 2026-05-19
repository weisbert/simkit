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

from PyQt5.QtCore import Qt, QModelIndex, QPoint, QFileSystemWatcher, QTimer
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from simkit.gui.bridge_worker import BridgeError, BridgeStatus, BridgeWorker
from simkit.gui.status_strip import (
    StatusStripWidget,
    Summary as StatusStripSummary,
    last_24h_summary,
)
from simkit.gui.controllers.diff import DiffController
from simkit.gui.controllers.error_translator import ErrorTranslator
from simkit.gui.controllers.run import RunController
from simkit.gui.error_translation import TranslatedError
from simkit.gui.loaders import (
    LoadedBundle,
    LoadedHistoryRun,
    LoadedModule,
    LoadedReview,
    editor_rows_to_union_rows,
    load_bundle_for_editor,
    snapshot_to_bundle_dict,
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
        # Filesystem watcher for the loaded project's reviews/unions/bundles
        # dirs. When the user edits a sidecar in their $EDITOR (or via the
        # right-click "Open .review.json"), the tree refreshes automatically.
        self._fs_watcher: Optional[QFileSystemWatcher] = None

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
        top_bar_layout.addWidget(self.status_dot)

        # Spec A5: visible "Restart bridge" affordance. Always shown so
        # the user can also force a re-probe on AMBER without waiting
        # for the next 10s heartbeat tick. Visually emphasized in RED
        # state via set_bridge_status() below.
        self.restart_bridge_button = QPushButton(
            "Restart bridge", objectName="restartBridgeButton",
        )
        self.restart_bridge_button.setToolTip(
            "Re-probe the Cadence SKILL bridge. If the bridge stays RED "
            "after clicking, the Cadence pyServer is down — run "
            "(pyKillServer)(pyStartServer ?python \"/usr/bin/python3\") "
            "in the CIW and click again."
        )
        self.restart_bridge_button.setVisible(False)
        self.restart_bridge_button.clicked.connect(self._on_restart_bridge_clicked)
        top_bar_layout.addWidget(self.restart_bridge_button)

        # Initialize visual state (status dot + button visibility).
        self.set_bridge_status(BridgeStatus.AMBER)

        # --- status strip (spec B1) -------------------------------------
        # Cross-module 24h activity summary with clickable FAIL chips.
        # Populated by ``refresh_status_strip()`` once a paths provider
        # is wired by app.py; until then renders the placeholder text.
        self.status_strip = StatusStripWidget()
        self.status_strip.fail_clicked.connect(self._on_status_strip_fail_clicked)
        # Caller (app.py) sets this so we can enumerate module DBs
        # without pulling app_state into the window layer.
        self._status_strip_paths_provider: Optional[Callable[[], list[Path]]] = None
        # Periodic refresh: 30s is rare enough to not hammer DuckDB but
        # frequent enough that the user sees a freshly-ingested run.
        self._status_strip_timer = QTimer(self)
        self._status_strip_timer.setInterval(30_000)
        self._status_strip_timer.timeout.connect(self.refresh_status_strip)
        self._status_strip_timer.start()

        # --- left tree --------------------------------------------------
        self.left_tree = QTreeView(objectName="leftTree")
        self.left_tree.setHeaderHidden(True)
        self.left_tree.setMinimumWidth(180)
        self._tree_model = ProjectTreeModel(self)
        self.left_tree.setModel(self._tree_model)
        self.left_tree.clicked.connect(self._on_tree_clicked)
        self.left_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.left_tree.customContextMenuRequested.connect(self._on_tree_context_menu)

        # --- right panel ------------------------------------------------
        self.right_panel = QTabWidget(objectName="rightPanel")
        self.right_panel.setDocumentMode(True)
        self.right_panel.setTabsClosable(False)

        self.results_tab = ResultsTab()
        self.corners_editor = CornersEditor()
        self.measures_editor = MeasuresEditor()

        # Right-click on corner-table rows: Duplicate / Delete (faster than
        # the bottom button row for power users).
        self.corners_editor.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.corners_editor.table.customContextMenuRequested.connect(
            self._on_corners_table_context_menu
        )

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
        self.measures_editor.pull_requested.connect(self._on_measures_pull_requested)

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
        # Spec A5: button is hidden when GREEN (no need to restart),
        # visible + plain on AMBER (something flickering, user can probe
        # early), and prominent on RED (clearly broken, one-click rescue).
        self._bridge_status = status
        if status == BridgeStatus.GREEN:
            self.restart_bridge_button.setVisible(False)
        else:
            self.restart_bridge_button.setVisible(True)
            if status == BridgeStatus.RED:
                self.restart_bridge_button.setStyleSheet(
                    "QPushButton { color: white; background-color: #c0392b; "
                    "border: 1px solid #962d22; border-radius: 4px; "
                    "padding: 2px 8px; font-weight: bold; }"
                )
            else:  # AMBER
                self.restart_bridge_button.setStyleSheet(
                    "QPushButton { padding: 2px 8px; }"
                )

    def _on_restart_bridge_clicked(self) -> None:
        """Spec A5: user-initiated bridge re-probe.

        Forwards to :meth:`BridgeWorker.restart`. If the worker hasn't
        been wired yet (early in the app boot), no-ops silently — the
        button only becomes interactive after ``set_bridge_worker``
        flips status off GREEN.
        """
        worker = getattr(self, "_worker", None)
        if worker is None:
            self.append_log("[bridge] restart requested before worker wired; ignored")
            return
        self.append_log("[bridge] manual restart requested by user")
        worker.restart()

    def append_log(self, line: str) -> None:
        self.bottom_log.append(line)

    def set_status_strip_paths_provider(
        self, provider: Callable[[], list[Path]]
    ) -> None:
        """Inject the closure that returns the list of module DB paths.

        Wired once by :mod:`simkit.gui.app` against ``app_state``'s
        ``recent_modules`` so the strip can aggregate across modules the
        user has visited recently (max ~5). MainWindow stays decoupled
        from on-disk app-state storage.
        """
        self._status_strip_paths_provider = provider
        # Repaint immediately so the user doesn't wait 30s for the
        # first non-placeholder summary.
        self.refresh_status_strip()

    def refresh_status_strip(self) -> None:
        """Re-run the 24h aggregation and update the widget.

        Called by the 30s timer, on ``run_finished``, and on bridge
        recovery (GREEN). Safe to call before a paths provider is wired
        (no-ops with placeholder text)."""
        provider = self._status_strip_paths_provider
        if provider is None:
            return
        try:
            db_paths = provider()
        except Exception as exc:  # noqa: BLE001
            log.debug("status-strip paths provider raised: %s", exc)
            return
        running_count = 0
        rc = getattr(self, "_run_controller", None)
        if rc is not None and rc.is_running:
            running_count = 1
        try:
            summary = last_24h_summary(
                [Path(p) for p in db_paths],
                running_count=running_count,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("status-strip aggregate failed: %s", exc)
            return
        self.status_strip.set_summary(summary)

    def _on_status_strip_fail_clicked(
        self, run_id: str, project_id: str,
    ) -> None:
        """User clicked a FAIL chip — surface a hint in the log.

        Full cross-module navigation (switch loaded module + open the
        failing review) is a Phase-5 follow-up. For now we log enough
        for the user to find the run themselves and, if the run is in
        the currently-loaded module, attempt to locate it in the tree.
        """
        self.append_log(
            f"[status-strip] FAIL chip clicked: run={run_id} "
            f"project={project_id or '?'}"
        )
        # Best-effort: if the run is in the loaded module, switch focus
        # to the Results tab so the user lands somewhere actionable.
        if self._loaded_module is not None and (
            not project_id or project_id == self._loaded_module.project_name
        ):
            self.right_panel.setCurrentWidget(self.results_tab)

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

        self._rewire_fs_watcher()

    def _rewire_fs_watcher(self) -> None:
        """(Re-)install QFileSystemWatcher on the project's sidecar dirs.

        Watches reviews/, unions/, bundles/. Any add/remove/edit triggers a
        debounced reload of the current module so the left tree + editors
        stay in sync with on-disk edits. Survives the user opening a file in
        $EDITOR and saving it back.
        """
        if self._loaded_module is None:
            return
        if self._fs_watcher is not None:
            self._fs_watcher.deleteLater()
        self._fs_watcher = QFileSystemWatcher(self)
        for sub in ("reviews", "unions", "bundles"):
            d = self._loaded_module.project_root / sub
            if d.is_dir():
                self._fs_watcher.addPath(str(d))
        self._fs_watcher.directoryChanged.connect(self._on_project_dir_changed)

    def _on_project_dir_changed(self, dir_path: str) -> None:
        if self._loaded_module is None:
            return
        from simkit.gui.loaders import load_module
        try:
            self._loaded_module = load_module(self._loaded_module.project_path)
        except Exception as exc:  # noqa: BLE001
            self.append_log(f"[watch] reload failed: {exc}")
            return
        self._tree_model.populate(self._loaded_module)
        self.left_tree.expandAll()
        self.append_log(f"[watch] {Path(dir_path).name}/ changed → tree refreshed")
        # Some editors (vim) replace-via-rename, which silently drops the
        # watch on the original inode. Re-add the directory to be safe.
        if self._fs_watcher and dir_path not in self._fs_watcher.directories():
            self._fs_watcher.addPath(dir_path)

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
        elif kind == ProjectTreeModel.NODE_KIND_BUNDLE and isinstance(payload, LoadedBundle):
            self._load_bundle_from_disk(payload.bundle_path)
            self.right_panel.setCurrentWidget(self.measures_editor)
            self.append_log(f"[tree] loaded bundle {payload.bundle_name}")

    def _on_corners_table_context_menu(self, pos: QPoint) -> None:
        """Right-click on a corner row → Duplicate / Delete shortcuts."""
        table = self.corners_editor.table
        index = table.indexAt(pos)
        if not index.isValid():
            return
        # Select the right-clicked row so the existing helpers act on it
        # (Qt's default context-menu policy doesn't auto-select for us).
        table.selectRow(index.row())
        menu = QMenu(table)
        a_dup = menu.addAction("Duplicate row")
        a_del = menu.addAction("Delete row")
        chosen = menu.exec_(table.viewport().mapToGlobal(pos))
        if chosen is a_dup:
            self.corners_editor.duplicate_row()
        elif chosen is a_del:
            self.corners_editor.delete_row()

    def _on_tree_context_menu(self, pos: QPoint) -> None:
        """Right-click on a tree node → context menu (per node kind)."""
        index = self.left_tree.indexAt(pos)
        if not index.isValid():
            return
        kind = self._tree_model.node_kind(index)
        payload = self._tree_model.node_payload(index)
        menu = QMenu(self.left_tree)
        if (
            kind == ProjectTreeModel.NODE_KIND_GROUP
            and payload == ProjectTreeModel.GROUP_REVIEWS
        ):
            a_new = menu.addAction("+ New Review…")
            menu.addSeparator()
            a_refresh = menu.addAction("Refresh tree (rescan reviews/)")
            chosen = menu.exec_(self.left_tree.viewport().mapToGlobal(pos))
            if chosen is a_new:
                self._new_review_dialog()
            elif chosen is a_refresh:
                self._on_project_dir_changed(
                    str(self._loaded_module.project_root / "reviews")
                    if self._loaded_module else ""
                )
            return
        if kind == ProjectTreeModel.NODE_KIND_REVIEW and isinstance(payload, LoadedReview):
            a_run = menu.addAction("Run this review…")
            a_open = menu.addAction("Open .review.json")
            menu.addSeparator()
            a_del = menu.addAction("Delete .review.json")
            chosen = menu.exec_(self.left_tree.viewport().mapToGlobal(pos))
            if chosen is a_run:
                self.results_tab.set_review_path(str(payload.review_path))
                self._on_run_requested(str(payload.review_path))
            elif chosen is a_open:
                self._open_in_editor(payload.review_path)
            elif chosen is a_del:
                self._confirm_delete_file(payload.review_path, refresh_tree=True)
        elif kind == ProjectTreeModel.NODE_KIND_HISTORY and isinstance(
            payload, LoadedHistoryRun
        ):
            a_view = menu.addAction("View results")
            a_baseline = menu.addAction("Set as Baseline for Compare")
            a_compare = menu.addAction("Compare to baseline / pick…")
            menu.addSeparator()
            a_copy = menu.addAction(f"Copy run_id ({payload.short_id})")
            menu.addSeparator()
            a_set_ms = menu.addAction("Set milestone… (Stage 4 — TODO)")
            a_set_ms.setEnabled(False)
            chosen = menu.exec_(self.left_tree.viewport().mapToGlobal(pos))
            if chosen is a_view:
                self._show_history_run(payload)
            elif chosen is a_baseline:
                self.results_tab.set_baseline(payload.run_id)
                self.append_log(f"[baseline] pinned to {payload.short_id}")
            elif chosen is a_compare:
                self._compare_from_history(payload.run_id)
            elif chosen is a_copy:
                QApplication.clipboard().setText(payload.run_id)
                self.append_log(f"[clipboard] {payload.run_id}")

    def _new_review_dialog(self) -> None:
        """MVP 'New Review' creation flow (proper wizard is Stage 4 §14).

        Asks for review name, defaults the item to (name='item1',
        tests=['Test'], union=first available). Writes the .review.json,
        refreshes the tree, selects the new node.
        """
        if self._loaded_module is None:
            self._warn("没有 module 加载", "先用 --module 启动一个工程")
            return
        name, ok = QInputDialog.getText(
            self,
            "simkit — 新建 Review",
            "Review 名 (字母/数字/下划线/连字符):",
            QLineEdit.Normal,
            "new_review",
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        # Validate: only [A-Za-z0-9_-] (mirror review.py's regex)
        if not _is_valid_review_name(name):
            self._warn(
                "名称非法",
                f"Review 名 {name!r} 含非法字符（只能 字母/数字/下划线/连字符）",
            )
            return
        out_path = (
            self._loaded_module.project_root / "reviews" / f"{name}.review.json"
        )
        if out_path.exists():
            self._warn("已存在", f"{out_path.name} 已经在 reviews/ 下，换个名字")
            return
        # Pick a sane default union
        union_default = self._loaded_module.union_default
        if union_default is not None:
            union_rel = "../" + union_default.relative_to(
                self._loaded_module.project_root
            ).as_posix()
        else:
            union_rel = "../unions/baseline.union.json"
        import json as _json

        review = {
            "_doc": f"Created from GUI on {Path(__file__).name} — edit items as needed.",
            "review_schema_version": 1,
            "name": name,
            "project": self._loaded_module.project_name,
            "items": [
                {
                    "name": "item1",
                    "tests": ["Test"],
                    "union": union_rel,
                }
            ],
        }
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(_json.dumps(review, indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            self._warn("写入失败", f"{out_path}: {exc}")
            return
        self.append_log(f"[review] created {out_path.name}")
        # Reload module so the new review shows in the tree
        from simkit.gui.loaders import load_module
        self._loaded_module = load_module(self._loaded_module.project_path)
        self._tree_model.populate(self._loaded_module)
        self.left_tree.expandAll()
        # Auto-bind the review so Run button activates immediately
        self.results_tab.set_review_path(str(out_path))
        # Open the file in $EDITOR so user can flesh it out
        self._open_in_editor(out_path)

    def _open_in_editor(self, path: Path) -> None:
        """Launch the user's $EDITOR on a file (falls back to xdg-open)."""
        import os
        import shutil
        import subprocess

        editor = os.environ.get("EDITOR") or shutil.which("xdg-open") or "xdg-open"
        try:
            subprocess.Popen([editor, str(path)])
            self.append_log(f"[open] {editor} {path.name}")
        except Exception as exc:  # noqa: BLE001
            self.append_log(f"[open] failed: {exc}")

    def _confirm_delete_file(self, path: Path, *, refresh_tree: bool) -> None:
        """Confirm modal then delete a sidecar file from disk."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("simkit — 删除文件确认")
        box.setText(f"确定要删除 {path.name}？")
        box.setInformativeText(f"路径: {path}")
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Cancel)
        if box.exec_() != QMessageBox.Yes:
            return
        try:
            path.unlink()
            self.append_log(f"[delete] {path.name}")
        except Exception as exc:  # noqa: BLE001
            self.append_log(f"[delete] failed: {exc}")
            return
        if refresh_tree and self._loaded_module is not None:
            from simkit.gui.loaders import load_module
            self._loaded_module = load_module(self._loaded_module.project_path)
            self._tree_model.populate(self._loaded_module)
            self.left_tree.expandAll()

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
        # Spec B1: the newly-ingested run should count toward "X done" and
        # any spec FAILs should join the chip strip.
        self.refresh_status_strip()

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

    def _compare_from_history(self, run_id_a: str) -> None:
        """History-row right-click 'Compare' — uses pinned baseline if set,
        else opens the picker."""
        if self._diff_controller is None or self._loaded_module is None:
            self.append_log("[diff] no bridge / module loaded")
            return
        baseline = self.results_tab.baseline_run_id()
        if baseline and baseline != run_id_a:
            self._diff_controller.open_diff(
                self._loaded_module.project_path, run_id_a, baseline,
            )
            return
        other = self._diff_controller.pick_run_for_compare(
            self._loaded_module.project_path, run_id_a, parent_widget=self,
        )
        if other is None:
            return
        self._diff_controller.open_diff(
            self._loaded_module.project_path, run_id_a, other,
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
        # Switch focus to the Corners tab so the user sees the result of
        # their action, and include row names in the log for diagnostic clarity
        # (pull-of-same-data looks invisible otherwise).
        self.right_panel.setCurrentWidget(self.corners_editor)
        row_names = ", ".join(r.row_name for r in u.rows)
        self.append_log(f"[corners] pulled {len(u.rows)} rows: {row_names}")

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

    def _on_measures_pull_requested(self) -> None:
        if not self._can_dispatch_bridge("measures pull"):
            return
        session = self.current_session_name()
        if not session:
            self._warn_session_required()
            return
        test_name, ok = QInputDialog.getText(
            self,
            "simkit — Pull measures",
            "Maestro test 名 (Outputs 表按 Test 列过滤):",
            QLineEdit.Normal,
            "Test",
        )
        if not ok or not test_name.strip():
            return
        test_name = test_name.strip()
        snap_path = self._scratch_path("measures_pull", ".json")
        self._queue_op(
            "pvt_measure_pull",
            on_ok=lambda result: self._on_measures_pulled(
                snap_path, test_name=test_name,
            ),
            kwargs={
                "out_path": str(snap_path),
                "test_name": test_name,
                "include_signals": True,
                "session": session,
                "pvtproject_path": self._loaded_module.project_path,
            },
        )
        self.append_log(f"[measures] pull queued (test={test_name!r}) → {snap_path.name}")

    def _on_measures_pulled(self, snap_path: Path, *, test_name: str) -> None:
        import json as _json
        try:
            snap = _json.loads(snap_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            self.append_log(f"[measures] snapshot parse failed: {exc}")
            return
        n_rows = len(snap.get("rows") or [])
        if n_rows == 0:
            self.append_log(f"[measures] pulled 0 rows (Test={test_name!r} has no outputs?)")
            return
        # Generate a bundle name + path; write to bundles/ so it shows in tree.
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        bundle_name = f"live_pulled_{ts}"
        out_path = (
            self._loaded_module.project_root
            / "bundles"
            / f"{bundle_name}.measure.json"
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        bundle_dict = snapshot_to_bundle_dict(
            snap,
            name=bundle_name,
            project=self._loaded_module.project_name,
            testbench_id=snap.get("testbench_id", ""),
        )
        out_path.write_text(_json.dumps(bundle_dict, indent=2), encoding="utf-8")
        self.append_log(
            f"[measures] pulled {n_rows} rows → {out_path.name} "
            f"(every row as raw_expression — template-aware reverse is P3B v2)"
        )
        # Load it into the editor + switch focus
        self._load_bundle_from_disk(out_path)
        self.right_panel.setCurrentWidget(self.measures_editor)

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


def _is_valid_review_name(name: str) -> bool:
    """Mirror review.py's _REVIEW_NAME_RE (lower-case only — review/union/
    bundle file basenames need filesystem-portable lowercase per Phase 2 design
    decision; project-id is the loosened one per DECISIONS #79)."""
    import re
    return bool(re.match(r"^[a-zA-Z0-9_-]+$", name))


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
