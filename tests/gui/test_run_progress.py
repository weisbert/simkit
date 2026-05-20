"""Tests for :mod:`simkit.gui.views.run_progress` (Phase 4 Stage 3)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("PyQt5")

from simkit.gui.views.run_progress import RunProgressWidget  # noqa: E402


@pytest.fixture
def widget(qtbot):
    w = RunProgressWidget()
    qtbot.addWidget(w)
    return w


def test_construct_has_header_and_cancel_button(widget):
    assert widget.header_label is not None
    assert widget.cancel_button is not None
    assert widget.items_list is not None
    # Cancel disabled until reset() (no active run).
    assert widget.cancel_button.isEnabled() is False


def test_reset_enables_cancel_and_sets_header(widget):
    widget.reset("pn_review_v3", 3)
    assert widget.cancel_button.isEnabled() is True
    assert "pn_review_v3" in widget.header_label.text()
    assert "0 / 3" in widget.header_label.text()
    assert widget.items_list.count() == 0


def test_set_running_toggles_cancel_directly(widget):
    widget.set_running(True)
    assert widget.cancel_button.isEnabled() is True
    widget.set_running(False)
    assert widget.cancel_button.isEnabled() is False


def test_item_started_appends_row(widget):
    widget.reset("review_x", 2)
    widget.handle_event(
        {
            "event": "item_started",
            "item_index": 1,
            "item_name": "BT2GRX trans PVT",
            "total_items": 2,
        }
    )
    assert widget.items_list.count() == 1
    text = widget.items_list.item(0).text()
    assert "BT2GRX" in text
    assert "1/2" in text


def test_item_progress_updates_row(widget):
    widget.reset("review_x", 1)
    widget.handle_event(
        {
            "event": "item_started",
            "item_index": 1,
            "item_name": "Item A",
            "total_items": 1,
        }
    )
    widget.handle_event(
        {
            "event": "item_progress",
            "item_index": 1,
            "running": 4,
            "completed": 0,
            "failed": 0,
            "total_corners": 6,
        }
    )
    text = widget.items_list.item(0).text()
    assert "Item A" in text
    assert "6" in text  # total_corners surfaced


def test_item_completed_shows_done_tally(widget):
    widget.reset("review_x", 1)
    widget.handle_event(
        {
            "event": "item_started",
            "item_index": 1,
            "item_name": "Item A",
            "total_items": 1,
        }
    )
    widget.handle_event(
        {
            "event": "item_completed",
            "item_index": 1,
            "run_id": "8e882e98abcd",
            "completed": 5,
            "failed": 1,
            "history_name": "review_x__1",
        }
    )
    text = widget.items_list.item(0).text()
    assert "completed" in text
    assert "5" in text
    assert "1 fail" in text
    assert "8e882e98" in text  # short_id


def test_review_done_disables_cancel(widget):
    widget.reset("review_x", 1)
    widget.handle_event(
        {
            "event": "item_started",
            "item_index": 1,
            "item_name": "Item A",
            "total_items": 1,
        }
    )
    widget.handle_event(
        {
            "event": "item_completed",
            "item_index": 1,
            "run_id": "abc12345",
            "completed": 3,
            "failed": 0,
        }
    )
    widget.handle_event({"event": "review_done", "exit_code": 0})
    assert widget.cancel_button.isEnabled() is False
    assert "OK" in widget.header_label.text()


def test_error_event_disables_cancel_and_shows_msg(widget):
    widget.reset("review_x", 1)
    widget.handle_event(
        {"event": "error", "code": "ASSEMBLER-2423", "msg": "wedge"}
    )
    assert widget.cancel_button.isEnabled() is False
    assert "ERROR" in widget.header_label.text()
    assert "wedge" in widget.header_label.text()


def test_log_event_is_silently_ignored(widget):
    widget.reset("review_x", 1)
    widget.handle_event({"event": "log", "level": "info", "msg": "..."})
    # No row created — log goes to bottom-log panel, not the kanban.
    assert widget.items_list.count() == 0


def test_cancel_button_emits_signal(widget, qtbot):
    widget.reset("review_x", 1)
    with qtbot.waitSignal(widget.cancel_requested, timeout=1000):
        widget.cancel_button.click()


def test_multiple_items_render_in_order(widget):
    widget.reset("review_y", 3)
    for i in range(1, 4):
        widget.handle_event(
            {
                "event": "item_started",
                "item_index": i,
                "item_name": f"Item_{i}",
                "total_items": 3,
            }
        )
    assert widget.items_list.count() == 3
    for i in range(1, 4):
        assert f"Item_{i}" in widget.items_list.item(i - 1).text()


def test_unknown_event_does_not_crash(widget):
    widget.reset("review_x", 1)
    # Should be silently ignored.
    widget.handle_event({"event": "made_up_thing", "foo": 1})
    widget.handle_event(None)  # type: ignore[arg-type]
    widget.handle_event({})
    assert widget.items_list.count() == 0


def test_failed_item_uses_fail_glyph(widget):
    widget.reset("review_x", 1)
    widget.handle_event(
        {
            "event": "item_started",
            "item_index": 1,
            "item_name": "A",
            "total_items": 1,
        }
    )
    widget.handle_event(
        {
            "event": "item_completed",
            "item_index": 1,
            "run_id": "abcd1234",
            "completed": 2,
            "failed": 3,
        }
    )
    text = widget.items_list.item(0).text()
    # Fail status should surface in the row tally.
    assert "3 fail" in text


def test_mark_cancelled_updates_header_and_disables_cancel(widget):
    """After cancel the header must no longer read 'Running:' — a stale
    'Running:' falsely implies the run is still in flight."""
    widget.reset("my_review", total_items=3)
    assert "Running:" in widget.header_label.text()
    widget.mark_cancelled()
    assert "CANCELLED" in widget.header_label.text()
    assert "Running:" not in widget.header_label.text()
    assert widget.cancel_button.isEnabled() is False
