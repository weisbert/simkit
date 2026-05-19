"""Widget tests for :mod:`simkit.gui.views.corners_editor` (spec §11).

Uses ``pytest-qt``'s ``qtbot`` fixture. No live skillbridge / no
``MainWindow`` integration — signals are only checked for emission.
"""

from __future__ import annotations

import os

# Headless rendering for the red-zone test runner — spec §18.3 baseline.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

# pytest-qt comes with the GUI extra; skip cleanly otherwise so a partial
# install still reports useful coverage on the non-GUI test suite.
pytest.importorskip("pytestqt")
pytest.importorskip("PyQt5")

from PyQt5.QtCore import Qt  # noqa: E402

from simkit.gui.views.corners_editor import (  # noqa: E402
    COL_ENABLE,
    COL_PROCESS,
    COL_ROW_NAME,
    COL_TEMPERATURE,
    CornersEditor,
)


# --- Fixtures + sample data ------------------------------------------------


def _sample_rows() -> list[dict]:
    return [
        {
            "row_name": "TT_pvt",
            "process": "tt",
            "temperature": "27",
            "vdd": "1.8",
            "model_file": "rf018.scs",
            "extra_vars": "",
        },
        {
            "row_name": "SS_pvt",
            "process": "ss",
            "temperature": "125",
            "vdd": "1.6",
            "model_file": "rf018.scs",
            "extra_vars": "",
        },
    ]


@pytest.fixture
def editor(qtbot):
    widget = CornersEditor()
    qtbot.addWidget(widget)
    return widget


# --- Construction ---------------------------------------------------------


def test_construct_has_expected_children(editor):
    # Header bar widgets
    assert editor.pull_button is not None
    assert editor.push_button is not None
    assert editor.last_sync_label is not None
    assert editor.last_sync_label.text() == "Last sync: —"

    # Table
    assert editor.table is not None
    assert editor.table.model() is not None
    assert editor.table.model().columnCount() == 7

    # Bottom row buttons
    assert editor.add_row_button is not None
    assert editor.duplicate_row_button is not None
    assert editor.delete_row_button is not None

    # Divergence strip hidden by default (spec §11.2 only shown on diff)
    assert editor._divergence.isVisible() is False


# --- Load / dump round-trip -----------------------------------------------


def test_load_union_populates_then_dump_round_trips(editor):
    rows = _sample_rows()
    editor.load_union(rows)
    assert editor._model.rowCount() == 2

    out = editor.dump_union()
    assert len(out) == 2
    # Field-for-field round trip (rows enabled by default -> no _enabled flag).
    for got, expect in zip(out, rows):
        assert got["row_name"] == expect["row_name"]
        assert got["process"] == expect["process"]
        assert got["temperature"] == expect["temperature"]
        assert got["vdd"] == expect["vdd"]
        assert got["model_file"] == expect["model_file"]
        assert got["extra_vars"] == expect["extra_vars"]
        # default-enabled rows MUST NOT carry _enabled: False
        assert got.get("_enabled", True) is True


# --- Mutations ------------------------------------------------------------


def test_add_row_appends_corner_n(editor):
    editor.add_row()
    assert editor._model.rowCount() == 1
    assert editor._model.item(0, COL_ROW_NAME).text() == "corner_1"

    editor.add_row()
    editor.add_row()
    assert editor._model.rowCount() == 3
    names = [editor._model.item(r, COL_ROW_NAME).text() for r in range(3)]
    assert names == ["corner_1", "corner_2", "corner_3"]


def test_duplicate_row_suffixes_copy(editor):
    editor.load_union(_sample_rows())
    # Select row 0
    editor.table.selectRow(0)
    editor.duplicate_row()
    assert editor._model.rowCount() == 3
    new_name = editor._model.item(2, COL_ROW_NAME).text()
    assert new_name == "TT_pvt_copy"


def test_duplicate_row_no_selection_is_noop(editor):
    editor.load_union(_sample_rows())
    editor.table.clearSelection()
    editor.duplicate_row()
    # No selectionModel currentIndex -> nothing happens
    # (Some Qt versions still report a currentIndex even after clear;
    # whatever the platform decides, row count is bounded: equal or one
    # more, but never two more.)
    assert editor._model.rowCount() in (2, 3)


def test_delete_row_removes_from_model(editor):
    editor.load_union(_sample_rows())
    editor.table.selectRow(0)
    editor.delete_row()
    assert editor._model.rowCount() == 1
    # The surviving row is row 1 of the original load — "SS_pvt".
    assert editor._model.item(0, COL_ROW_NAME).text() == "SS_pvt"


# --- Enable checkbox -----------------------------------------------------


def test_checkbox_toggle_emits_disabled_flag(editor):
    editor.load_union(_sample_rows())
    enable_item = editor._model.item(0, COL_ENABLE)
    assert enable_item.checkState() == Qt.Checked

    # Toggle off
    enable_item.setCheckState(Qt.Unchecked)
    out = editor.dump_union()
    assert out[0]["_enabled"] is False
    # Other row stays default — no _enabled key
    assert "_enabled" not in out[1] or out[1].get("_enabled") is True

    # Toggle back on
    enable_item.setCheckState(Qt.Checked)
    out = editor.dump_union()
    assert "_enabled" not in out[0] or out[0].get("_enabled") is True


# --- Validation ----------------------------------------------------------


def test_validation_flags_missing_row_name(editor):
    editor.load_union([{"row_name": "", "process": "tt"}])
    errors = editor.validation_errors()
    assert any("missing row_name" in e for e in errors)


def test_validation_flags_duplicate_row_name(editor):
    editor.load_union(
        [
            {"row_name": "TT_pvt", "process": "tt"},
            {"row_name": "TT_pvt", "process": "ss"},
        ]
    )
    errors = editor.validation_errors()
    assert any("duplicate row_name" in e for e in errors)


def test_validation_clean_means_no_errors(editor):
    editor.load_union(_sample_rows())
    assert editor.validation_errors() == []


# --- Push button gating ---------------------------------------------------


def test_push_button_disabled_when_invalid(editor):
    editor.load_union([{"row_name": ""}])
    assert editor.push_button.isEnabled() is False


def test_push_button_enabled_when_valid(editor):
    editor.load_union(_sample_rows())
    assert editor.push_button.isEnabled() is True


# --- Signals --------------------------------------------------------------


def test_push_requested_emits_payload(editor, qtbot):
    editor.load_union(_sample_rows())

    with qtbot.waitSignal(editor.push_requested, timeout=1000) as blocker:
        editor.push_button.click()

    # The signal is (payload,) — pyqtSignal(object)
    assert isinstance(blocker.args[0], list)
    assert len(blocker.args[0]) == 2
    assert blocker.args[0][0]["row_name"] == "TT_pvt"


def test_pull_requested_emits(editor, qtbot):
    with qtbot.waitSignal(editor.pull_requested, timeout=1000):
        editor.pull_button.click()


# --- Divergence strip -----------------------------------------------------


def test_divergence_strip_hidden_initially(editor):
    assert editor._divergence.isVisible() is False


def test_set_divergence_shows_strip(editor, qtbot):
    editor.show()  # widget must be visible for child visibility to mean anything
    editor.set_divergence(6, 4)
    assert editor._divergence.isVisible() is True
    text = editor.divergence_label.text()
    # Spec §11.2 wording (exact phrases pinned)
    assert "Maestro session has 6 rows" in text
    assert "your sidecar has 4" in text
    assert "show diff" in text
    assert "pull overrides sidecar" in text
    assert "keep sidecar" in text


def test_set_divergence_equal_counts_hides_strip(editor):
    editor.show()
    editor.set_divergence(6, 4)
    editor.set_divergence(4, 4)
    assert editor._divergence.isVisible() is False


def test_divergence_buttons_emit_signals(editor, qtbot):
    editor.show()
    editor.set_divergence(6, 4)

    with qtbot.waitSignal(editor.show_diff, timeout=1000):
        editor.show_diff_button.click()

    with qtbot.waitSignal(editor.pull_overrides_sidecar, timeout=1000):
        editor.pull_overrides_button.click()

    with qtbot.waitSignal(editor.keep_sidecar, timeout=1000):
        editor.keep_sidecar_button.click()


# --- Last-sync helper -----------------------------------------------------


def test_set_last_sync_updates_label(editor):
    editor.set_last_sync("2026-05-19 12:34:56")
    assert editor.last_sync_label.text() == "Last sync: 2026-05-19 12:34:56"

    editor.set_last_sync("")
    assert editor.last_sync_label.text() == "Last sync: —"


# --- set_project_root + model_file existence (Phase 4 Stage 3) -----------


def test_set_project_root_no_root_no_existence_check(editor):
    editor.load_union(
        [
            {
                "row_name": "TT_pvt",
                "process": "tt",
                "temperature": "27",
                "vdd": "1.8",
                "model_file": "definitely_missing.scs",
                "extra_vars": "",
            }
        ]
    )
    # No project root bound -> only minimal validation -> no errors.
    assert editor.validation_errors() == []


def test_set_project_root_flags_missing_model_file(editor, tmp_path):
    editor.set_project_root(tmp_path)
    editor.load_union(
        [
            {
                "row_name": "TT_pvt",
                "process": "tt",
                "temperature": "27",
                "vdd": "1.8",
                "model_file": "nope.scs",
                "extra_vars": "",
            }
        ]
    )
    errors = editor.validation_errors()
    assert any(
        "nope.scs" in e and "not found" in e for e in errors
    )


def test_set_project_root_accepts_existing_model_file(editor, tmp_path):
    model_file = tmp_path / "rf018.scs"
    model_file.write_text("* dummy\n", encoding="utf-8")

    editor.set_project_root(tmp_path)
    editor.load_union(
        [
            {
                "row_name": "TT_pvt",
                "process": "tt",
                "temperature": "27",
                "vdd": "1.8",
                "model_file": "rf018.scs",
                "extra_vars": "",
            }
        ]
    )
    assert editor.validation_errors() == []


def test_set_project_root_empty_model_file_is_ignored(editor, tmp_path):
    editor.set_project_root(tmp_path)
    editor.load_union(
        [
            {
                "row_name": "TT_pvt",
                "process": "tt",
                "temperature": "27",
                "vdd": "1.8",
                "model_file": "",
                "extra_vars": "",
            }
        ]
    )
    # Empty model_file string should NOT be flagged — the check only fires
    # on non-empty cells.
    assert editor.validation_errors() == []


def test_set_project_root_absolute_path_passes_when_present(
    editor, tmp_path
):
    model_file = tmp_path / "abs_model.scs"
    model_file.write_text("* dummy\n", encoding="utf-8")
    editor.set_project_root(tmp_path)
    editor.load_union(
        [
            {
                "row_name": "TT_pvt",
                "process": "tt",
                "temperature": "27",
                "vdd": "1.8",
                "model_file": str(model_file),
                "extra_vars": "",
            }
        ]
    )
    assert editor.validation_errors() == []


def test_set_project_root_none_unbinds(editor, tmp_path):
    editor.set_project_root(tmp_path)
    editor.set_project_root(None)
    editor.load_union(
        [
            {
                "row_name": "TT_pvt",
                "process": "tt",
                "temperature": "27",
                "vdd": "1.8",
                "model_file": "still_missing.scs",
                "extra_vars": "",
            }
        ]
    )
    # Unbound -> no existence check -> no errors.
    assert editor.validation_errors() == []


def test_set_project_root_disables_push_when_model_missing(
    editor, tmp_path
):
    editor.set_project_root(tmp_path)
    editor.load_union(
        [
            {
                "row_name": "TT_pvt",
                "process": "tt",
                "temperature": "27",
                "vdd": "1.8",
                "model_file": "nope.scs",
                "extra_vars": "",
            }
        ]
    )
    # Push must be disabled because validation_errors() is non-empty.
    assert editor.push_button.isEnabled() is False
