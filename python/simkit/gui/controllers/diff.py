"""DiffController — wires :mod:`simkit.diff` to :class:`DiffTab`.

Stateless across calls; the per-module baseline pin lives in
:class:`ModuleSession` (caller's responsibility, not ours).

Why synchronous (no QThread): the diff queries hit one DuckDB read-only
connection, fetch O(rows-per-run) tuples, and Python-loop over the
intersection of two key sets. For Tier-1 sweep sizes (240 → 1000 rows)
this is well under one frame; the UX cost of spinning up a worker thread
for the diff would be larger than the work itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Optional

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import QDialog, QWidget

from simkit.db import connect
from simkit.diff import compute_diff
from simkit.gui.views.diff_tab import DiffTab
from simkit.gui.views.run_picker import RunPickerDialog


class DiffController(QObject):
    """Compute diffs + materialise :class:`DiffTab` widgets."""

    diff_ready = pyqtSignal(object)  # the DiffTab widget
    error = pyqtSignal(str)

    def __init__(
        self,
        *,
        db_path_resolver: Callable[[Path], Path],
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self._resolve_db = db_path_resolver

    # --- public ---------------------------------------------------------

    def open_diff(
        self,
        project_path: Path,
        run_id_a: str,
        run_id_b: str,
    ) -> None:
        """Compute the diff and emit :pyattr:`diff_ready` with the DiffTab.

        Errors surface via the :pyattr:`error` signal; never raises.
        """
        try:
            db_path = self._resolve_db(project_path)
            runs_root = db_path.parent / "runs"
            con = connect(db_path, read_only=True)
            try:
                # Pass the full run_ids straight through — they're already
                # resolved by the caller. resolve_slice() inside
                # compute_diff will still re-validate.
                result = compute_diff(
                    con,
                    slice_a=run_id_a,
                    slice_b=run_id_b,
                    runs_root=runs_root,
                )
            finally:
                con.close()
        except Exception as exc:  # noqa: BLE001 — error surfaces via signal
            self.error.emit(f"Diff failed: {exc}")
            return

        tab = DiffTab(result)
        self.diff_ready.emit(tab)

    def pick_run_for_compare(
        self,
        project_path: Path,
        current_run_id: str,
        parent_widget: QWidget,
    ) -> Optional[str]:
        """Modally pick a run from the project DB. Returns run_id or None."""
        try:
            runs = self._load_runs(project_path)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"Run list unavailable: {exc}")
            return None

        dlg = RunPickerDialog(
            runs,
            current_run_id=current_run_id,
            parent=parent_widget,
        )
        if dlg.exec_() == QDialog.Accepted:
            return dlg.selected_run_id
        return None

    # --- internals ------------------------------------------------------

    def _load_runs(self, project_path: Path) -> List[dict]:
        """Fetch the run list for the picker — minimal columns."""
        db_path = self._resolve_db(project_path)
        con = connect(db_path, read_only=True)
        try:
            # CAST timestamp to VARCHAR in-SQL so the read works even
            # when DuckDB's pytz hook for TIMESTAMPTZ is unavailable
            # (the red-zone Python ships without pytz). Same workaround
            # the CLI list-runs uses.
            rows = con.execute(
                """
                SELECT run_id, label, CAST(timestamp AS VARCHAR)
                FROM runs
                ORDER BY timestamp DESC
                """,
            ).fetchall()
        finally:
            con.close()
        out: List[dict] = []
        for run_id, label, ts in rows:
            out.append({
                "run_id": run_id,
                "short_id": (run_id or "")[:8],
                "timestamp": "" if ts is None else str(ts),
                "label": label,
            })
        return out
