"""Summary tab — run-level review evidence (G-3 margin + G-4 convergence).

Layout::

    ┌─ SummaryTab ───────────────────────────────────────────────────┐
    │ 36 rows · 30 ok · 6 eval_err  [⚠ partial run]      ← health line │
    ├─────────────────────────────────────────────────────────────────┤
    │ output | spec | worst corner | worst value | margin | verdict |  │
    │ ...                                                              │ ← rollup
    └─────────────────────────────────────────────────────────────────┘

The health line answers "did this run finish, and how cleanly"; the
table answers "for each spec'd output, what is the worst corner and how
much margin is left". Both are derived from :mod:`simkit.gui.run_summary`
— this widget is a pure view, no DuckDB logic of its own.
"""

from __future__ import annotations

from typing import Any, List, Optional

import duckdb

from PyQt5.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PyQt5.QtGui import QBrush, QColor
from PyQt5.QtWidgets import (
    QHeaderView,
    QLabel,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from simkit.gui.run_summary import (
    RunHealth,
    margin_rollup,
    read_run_provenance,
    run_health,
)


_MISSING = "—"

# Verdict → row tint. fail is the only hard red; the "can't judge" verdicts
# get amber; a clean pass gets a faint green so a healthy table reads at a
# glance. no_spec stays plain — it is a gap, not a failure.
_VERDICT_BRUSH = {
    "fail": QBrush(QColor(255, 220, 220)),
    "no_value": QBrush(QColor(255, 235, 200)),
    "parse_err": QBrush(QColor(255, 235, 200)),
    "unsupported": QBrush(QColor(255, 235, 200)),
    "pass": QBrush(QColor(223, 245, 223)),
}


def _fmt_num(value: Any) -> str:
    """Compact numeric formatting; non-numbers pass through as text."""
    if value is None:
        return _MISSING
    if isinstance(value, (int, float)):
        return f"{value:.4g}"
    return str(value)


class MarginRollupModel(QAbstractTableModel):
    """Read-only table model over a tuple of :class:`OutputRollup`."""

    COLUMNS: tuple[str, ...] = (
        "output", "spec", "worst corner", "worst value", "margin",
        "verdict", "corners",
    )

    def __init__(self, rollup: tuple = (), parent: Any = None):
        super().__init__(parent)
        self._rows: List[Any] = list(rollup)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal and 0 <= section < len(self.COLUMNS):
            return self.COLUMNS[section]
        if orientation == Qt.Vertical:
            return section + 1
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        if not (0 <= row < len(self._rows)):
            return None
        entry = self._rows[row]
        if role == Qt.DisplayRole:
            return self._cell(entry, col)
        if role == Qt.BackgroundRole:
            return _VERDICT_BRUSH.get(entry.verdict)
        return None

    def _cell(self, entry, col: int) -> str:
        if col == 0:
            return entry.output
        if col == 1:
            return entry.spec if entry.spec else _MISSING
        if col == 2:
            return entry.worst_corner or _MISSING
        if col == 3:
            return _fmt_num(entry.worst_value)
        if col == 4:
            return _fmt_num(entry.margin)
        if col == 5:
            return entry.verdict
        if col == 6:
            return str(entry.n_corners)
        return ""

    def rows(self) -> List[Any]:
        """Defensive copy of the backing rollup — for tests."""
        return list(self._rows)


def provenance_line(prov: Optional[dict]) -> str:
    """One-line summary of a run's recorded conditions (G-5).

    ``None`` → an explicit "not recorded" line, because an unprovable
    run is itself the thing a reviewer needs to notice.
    """
    if not prov:
        return ("Run conditions: not recorded (this run predates the "
                "traceability feature, or was a manual PvtSave)")
    host = prov.get("host") or "?"
    pdk = prov.get("pdk_version") or "unknown"
    models = prov.get("model_files") or []
    captured = (prov.get("captured_at") or "")[:19].replace("T", " ")
    parts = [f"host={host}", f"PDK={pdk}", f"{len(models)} model file(s)"]
    if captured:
        parts.append(f"captured {captured}")
    return "Run conditions: " + "  ·  ".join(parts)


def provenance_tooltip(prov: Optional[dict]) -> str:
    """Multi-line model-file detail for the provenance label's tooltip."""
    if not prov:
        return (
            "No provenance record — cannot prove which model version / "
            "which machine produced these numbers."
        )
    models = prov.get("model_files") or []
    if not models:
        return "No model files recorded."
    lines = ["Model files:"]
    for m in models:
        path = m.get("path", "?")
        if not m.get("exists", False):
            lines.append(f"  {path}  (not found)")
        else:
            mtime = (m.get("mtime") or "")[:19].replace("T", " ")
            size = m.get("size")
            lines.append(f"  {path}  ({size} bytes, mtime {mtime})")
    return "\n".join(lines)


def health_line(health: RunHealth) -> str:
    """Render a :class:`RunHealth` as the one-line summary string."""
    parts = [f"{health.total_rows} rows"]
    for status in sorted(health.status_counts):
        parts.append(f"{health.status_counts[status]} {status}")
    if health.sim_fail_corners:
        parts.append(f"{health.sim_fail_corners} corners sim-failed")
    line = "  ·  ".join(parts)
    if health.partial_run:
        line += "    [⚠ partial run — results incomplete]"
    return line


class SummaryTab(QWidget):
    """Right-panel tab: run health line + per-output margin rollup."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._model: Optional[MarginRollupModel] = None

        self.health_label = QLabel("(no run selected)", self)
        self.health_label.setObjectName("summaryHealthLabel")
        self.health_label.setWordWrap(True)

        # G-5 — run-condition provenance line (host / PDK / model files).
        self.provenance_label = QLabel("", self)
        self.provenance_label.setObjectName("summaryProvenanceLabel")
        self.provenance_label.setWordWrap(True)
        self.provenance_label.setStyleSheet("color: #555;")
        self.provenance_label.setVisible(False)

        self.table = QTableView(self)
        self.table.setObjectName("marginRollupTable")
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Interactive
        )

        self._proxy = QSortFilterProxyModel(self)
        self._proxy.setSortRole(Qt.DisplayRole)
        self.table.setModel(self._proxy)

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 6, 8, 6)
        v.addWidget(self.health_label)
        v.addWidget(self.provenance_label)
        v.addWidget(self.table, stretch=1)

    def set_run(self, run_id: str, con: duckdb.DuckDBPyConnection) -> None:
        """Populate the health line + rollup table for ``run_id``."""
        health = run_health(con, run_id)
        self.health_label.setText(health_line(health))
        self._style_health(health)
        prov = read_run_provenance(con, run_id)
        self.provenance_label.setText(provenance_line(prov))
        self.provenance_label.setToolTip(provenance_tooltip(prov))
        # Amber when conditions are unknown — an unprovable run is a risk.
        self.provenance_label.setStyleSheet(
            "color: #555;" if prov
            else "QLabel#summaryProvenanceLabel { background: #fff3a3; "
                 "border: 1px solid #d4b500; padding: 2px 8px; }"
        )
        self.provenance_label.setVisible(True)
        self._model = MarginRollupModel(margin_rollup(con, run_id), parent=self)
        self._proxy.setSourceModel(self._model)

    def clear(self) -> None:
        """Drop the table + reset the health line (no run selected)."""
        self.health_label.setText("(no run selected)")
        self.health_label.setStyleSheet("")
        self.provenance_label.setText("")
        self.provenance_label.setVisible(False)
        self._model = None
        self._proxy.setSourceModel(None)

    def _style_health(self, health: RunHealth) -> None:
        """Amber background when the run needs attention; plain when clean."""
        if health.clean:
            self.health_label.setStyleSheet("")
        else:
            self.health_label.setStyleSheet(
                "QLabel#summaryHealthLabel { background: #fff3a3; "
                "border: 1px solid #d4b500; padding: 4px 8px; }"
            )
