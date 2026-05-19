"""MainWindow integration tests — Stage 2 wiring.

Pins the contract between :class:`simkit.gui.main_window.MainWindow` and
the three right-panel tab widgets (``ResultsTab`` / ``CornersEditor`` /
``MeasuresEditor``) so future refactors of either side surface as test
failures rather than silent UX regressions.

Scope (Stage 2 — log-only handlers): we only verify that:
  * the three tabs are present + in the documented order;
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
from simkit.gui.views.corners_editor import CornersEditor  # noqa: E402
from simkit.gui.views.measures_editor import MeasuresEditor  # noqa: E402
from simkit.gui.views.results_tab import ResultsTab  # noqa: E402


def test_right_panel_has_three_tabs_in_documented_order(qtbot):
    w = MainWindow()
    qtbot.addWidget(w)
    tabs = w.right_panel
    assert tabs.count() == 3
    assert tabs.tabText(0) == "Results"
    assert tabs.tabText(1) == "Corners"
    assert tabs.tabText(2) == "Measures"


def test_tab_widgets_are_the_right_classes(qtbot):
    w = MainWindow()
    qtbot.addWidget(w)
    assert isinstance(w.results_tab, ResultsTab)
    assert isinstance(w.corners_editor, CornersEditor)
    assert isinstance(w.measures_editor, MeasuresEditor)


def test_run_requested_logs_to_bottom_panel(qtbot):
    w = MainWindow()
    qtbot.addWidget(w)
    w.results_tab.run_requested.emit("/tmp/foo.review.json")
    text = w.bottom_log.toPlainText()
    assert "run" in text.lower()
    assert "/tmp/foo.review.json" in text


def test_corners_pull_requested_logs(qtbot):
    w = MainWindow()
    qtbot.addWidget(w)
    w.corners_editor.pull_requested.emit()
    assert "pull" in w.bottom_log.toPlainText().lower()


def test_corners_push_requested_logs_row_count(qtbot):
    w = MainWindow()
    qtbot.addWidget(w)
    w.corners_editor.push_requested.emit([{"row_name": "a"}, {"row_name": "b"}])
    text = w.bottom_log.toPlainText()
    assert "push" in text.lower()
    assert "2 rows" in text


def test_corners_divergence_signals_all_log(qtbot):
    w = MainWindow()
    qtbot.addWidget(w)
    w.corners_editor.show_diff.emit()
    w.corners_editor.pull_overrides_sidecar.emit()
    w.corners_editor.keep_sidecar.emit()
    text = w.bottom_log.toPlainText().lower()
    assert "show-diff" in text
    assert "pull-overrides-sidecar" in text
    assert "keep-sidecar" in text


def test_measures_apply_requested_logs_row_count(qtbot):
    w = MainWindow()
    qtbot.addWidget(w)
    w.measures_editor.apply_requested.emit([{"output_name": "pn_VDD_1M"}, {}, {}])
    text = w.bottom_log.toPlainText()
    assert "apply" in text.lower()
    assert "3 rendered rows" in text


def test_sanitize_history_prefix_drops_punctuation():
    from simkit.gui.main_window import _sanitize_history_prefix

    assert _sanitize_history_prefix("PN check CDR") == "PN_check_CDR"
    assert _sanitize_history_prefix("v17/gmin-2") == "v17_gmin_2"
    assert _sanitize_history_prefix("hello!?world") == "helloworld"
    assert _sanitize_history_prefix("___") == "run"   # empty after strip
    assert _sanitize_history_prefix("") == "run"
    assert _sanitize_history_prefix("ok_name") == "ok_name"
