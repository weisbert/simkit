"""Widget tests for :mod:`simkit.gui.views.measures_editor` (spec §12).

Uses ``pytest-qt``'s ``qtbot`` fixture + the real ``simkit.template_render``
pipeline. No SKILL / no MainWindow / no live skillbridge.

Coverage (~14 cases):
  * Construction has both panes (left edit + right preview)
  * Empty bundle → "Empty bundle" grey status, no preview rows, apply disabled
  * Valid 1-entry template bundle → "OK" green, preview rows, apply enabled
  * Broken bundle (output_name collision) → "Error" red, apply disabled
  * "+ Template" / "+ Raw" / "+ Sweep" buttons add entries of the right kind
  * "Delete entry" removes selected entry
  * "Move up" / "Move down" reorder
  * ``dump_bundle()`` round-trips through ``load_bundle``
  * ``apply_requested`` fires with rendered rows when Apply clicked
  * ``set_available_templates`` / ``set_available_signal_groups`` accept
    both list[str] and dict[str, Template] forms
"""

from __future__ import annotations

import os

# Headless rendering for the red-zone test runner — spec §18.3 baseline.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

# pytest-qt comes with the GUI extra; skip cleanly otherwise so a partial
# install still reports useful coverage on the non-GUI test suite.
pytest.importorskip("pytestqt")
pytest.importorskip("PyQt5")

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import QListWidget, QSplitter, QTableView  # noqa: E402

from simkit.signal_group import Signal, SignalGroup  # noqa: E402
from simkit.template import Template, TemplateParam  # noqa: E402
from simkit.template_render import RenderedRow  # noqa: E402

from simkit.gui.views.measures_editor import (  # noqa: E402
    MeasuresEditor,
    _EntryDialog,
)


# --- Sample data ---------------------------------------------------------


def _rise_template() -> Template:
    """A signal-bound rise-time template with two number params (defaults)."""
    return Template(
        template_schema_version=1,
        name="rise_time_threshold",
        short_alias="Rtime",
        expression=(
            'average(riseTime(vtime(\'tran "$SIG") 0 nil VAR("VDD") nil '
            '$V_LOW $V_HIGH t "time"))'
        ),
        params=(
            TemplateParam(key="SIG", kind="signal"),
            TemplateParam(key="V_LOW", kind="number", default="10"),
            TemplateParam(key="V_HIGH", kind="number", default="90"),
        ),
        eval_type="point",
        plot=True,
        save=False,
        unit=None,
        pasted_from=None,
        source_path=Path("/tmp/fake_rise_time_threshold.template.json"),
    )


def _pn_template() -> Template:
    """A signal-less phase-noise-at-freq template."""
    return Template(
        template_schema_version=1,
        name="pn_at_freq",
        short_alias="PN",
        expression="value(VAR($OUT_NAME) $FREQ)",
        params=(
            TemplateParam(key="OUT_NAME", kind="string"),
            TemplateParam(key="FREQ", kind="number", default="1000000"),
        ),
        eval_type="point",
        plot=True,
        save=False,
        unit=None,
        pasted_from=None,
        source_path=Path("/tmp/fake_pn_at_freq.template.json"),
    )


def _voltage_outs_group() -> SignalGroup:
    return SignalGroup(
        signal_group_schema_version=1,
        name="voltage_outs",
        signals=(Signal(net="/Vout"),),
        source_path=Path("/tmp/fake_voltage_outs.siggroup.json"),
    )


def _two_signal_group() -> SignalGroup:
    return SignalGroup(
        signal_group_schema_version=1,
        name="two_signals",
        signals=(Signal(net="/Vout"), Signal(net="/Vout2")),
        source_path=Path("/tmp/fake_two_signals.siggroup.json"),
    )


def _valid_one_entry_bundle() -> dict:
    return {
        "measure_schema_version": 2,
        "name": "voltage_outs_rise",
        "project": "my_block",
        "testbench_id": "MY_LIB/my_block_tb/schematic",
        "test_name": "Test",
        "apply": [
            {
                "template": "rise_time_threshold",
                "signal_group": "voltage_outs",
            }
        ],
    }


def _collision_bundle() -> dict:
    """Two entries that render to the same output_name -> RenderError."""
    return {
        "measure_schema_version": 2,
        "name": "collision",
        "project": "my_block",
        "testbench_id": "MY_LIB/my_block_tb/schematic",
        "test_name": "Test",
        "apply": [
            {
                "template": "rise_time_threshold",
                "signal_group": "voltage_outs",
            },
            {
                "template": "rise_time_threshold",
                "signal_group": "voltage_outs",
            },
        ],
    }


# --- Fixtures ------------------------------------------------------------


@pytest.fixture
def editor(qtbot):
    widget = MeasuresEditor()
    qtbot.addWidget(widget)
    # Wire templates + signal groups the editor needs to render.
    widget.set_available_templates(
        {
            "rise_time_threshold": _rise_template(),
            "pn_at_freq": _pn_template(),
        }
    )
    widget.set_available_signal_groups(
        {
            "voltage_outs": _voltage_outs_group(),
            "two_signals": _two_signal_group(),
        }
    )
    return widget


# --- Construction --------------------------------------------------------


def test_construct_has_both_panes(editor):
    # The top-level splitter holds two children: left (edit) + right (preview).
    splitter = editor.findChild(QSplitter, "measuresEditorSplitter")
    assert splitter is not None
    assert splitter.count() == 2
    # Both panes are wired up.
    assert editor._left_pane is not None
    assert editor._right_pane is not None
    # Entry list + preview table exist.
    assert editor.findChild(QListWidget, "entryList") is not None
    assert editor.findChild(QTableView, "previewTable") is not None
    # Preview-status starts in the grey "Empty bundle" state.
    assert editor._status_label.text() == "Empty bundle"
    # Apply disabled when nothing is loaded.
    assert editor._apply_btn.isEnabled() is False


# --- load_bundle / empty / valid / broken -------------------------------


def test_empty_bundle_shows_empty_status(editor):
    editor.load_bundle({})
    assert editor._status_label.text() == "Empty bundle"
    assert editor._apply_btn.isEnabled() is False
    assert editor._preview_model.rowCount() == 0


def test_valid_bundle_renders_ok(editor):
    editor.load_bundle(_valid_one_entry_bundle())
    assert editor._status_label.text() == "OK"
    assert editor._apply_btn.isEnabled() is True
    # Single signal in the group -> one rendered row.
    assert editor._preview_model.rowCount() == 1
    # Column 0 is output_name; the rise template's short_alias is "Rtime"
    # and the signal basename for "/Vout" is "Vout" -> "Rtime_Vout".
    assert editor._preview_model.item(0, 0).text() == "Rtime_Vout"
    # Column 1 is the test_name from the bundle metadata.
    assert editor._preview_model.item(0, 1).text() == "Test"
    # Column 2 is the rendered expression — sanity-check it expanded $SIG.
    expr = editor._preview_model.item(0, 2).text()
    assert "/Vout" in expr
    # And expanded the V_LOW / V_HIGH defaults.
    assert " 10 90 " in expr


def test_broken_bundle_shows_error_status_and_disables_apply(editor):
    # render_bundle raises RenderError on duplicate output names.
    editor.load_bundle(_collision_bundle())
    assert editor._status_label.text() == "Error"
    assert editor._apply_btn.isEnabled() is False
    # Error detail panel shown + non-empty. ``isVisible()`` returns False
    # for any child widget while its top-level parent is unshown
    # (qtbot.addWidget doesn't .show() the widget), so check ``isHidden()``
    # which reflects the explicit visibility flag rather than the
    # effective on-screen state.
    assert editor._error_detail.isHidden() is False
    assert editor._error_detail.text() != ""
    # The preview table is cleared on error.
    assert editor._preview_model.rowCount() == 0


def test_unbound_signal_template_without_signal_group_is_error(editor):
    """rise_time_threshold has a signal-kind param -> needs a signal_group.
    Omitting it surfaces as a render error (red status, apply disabled)."""
    bundle = {
        "measure_schema_version": 2,
        "name": "no_sg",
        "project": "my_block",
        "testbench_id": "MY_LIB/my_block_tb/schematic",
        "test_name": "Test",
        "apply": [
            {"template": "rise_time_threshold", "signal_group": None}
        ],
    }
    editor.load_bundle(bundle)
    assert editor._status_label.text() == "Error"
    assert editor._apply_btn.isEnabled() is False
    assert editor._error_detail.isHidden() is False


# --- Add buttons --------------------------------------------------------


def test_add_template_button_appends_template_entry(editor, qtbot):
    qtbot.mouseClick(editor._add_template_btn, Qt.LeftButton)
    bundle = editor.dump_bundle()
    assert len(bundle["apply"]) == 1
    entry = bundle["apply"][0]
    assert "template" in entry
    assert "raw_expression" not in entry
    assert "param_sweep" not in entry


def test_add_raw_button_appends_raw_entry(editor, qtbot):
    qtbot.mouseClick(editor._add_raw_btn, Qt.LeftButton)
    bundle = editor.dump_bundle()
    assert len(bundle["apply"]) == 1
    entry = bundle["apply"][0]
    assert "raw_expression" in entry
    assert "output_name" in entry
    assert "template" not in entry


def test_add_sweep_button_appends_sweep_entry(editor, qtbot):
    qtbot.mouseClick(editor._add_sweep_btn, Qt.LeftButton)
    bundle = editor.dump_bundle()
    assert len(bundle["apply"]) == 1
    entry = bundle["apply"][0]
    assert "template" in entry
    # Sweep entries carry param_sweep + output_names keys (possibly empty).
    assert "param_sweep" in entry
    assert "output_names" in entry


# --- Delete / move up / move down ---------------------------------------


def test_delete_entry_removes_selected_row(editor):
    editor.load_bundle(_valid_one_entry_bundle())
    # Append a second entry so there are two to choose from.
    editor._entries.append({"raw_expression": "0", "output_name": "rawZero"})
    editor._refresh_entries()
    editor._render_preview()
    assert len(editor.dump_bundle()["apply"]) == 2

    # Select row 0 then delete -> only the second one survives.
    editor._entry_list.setCurrentRow(0)
    editor._on_delete()
    apply = editor.dump_bundle()["apply"]
    assert len(apply) == 1
    assert apply[0].get("raw_expression") == "0"


def test_move_up_swaps_with_previous(editor):
    editor.load_bundle(
        {
            "measure_schema_version": 2,
            "name": "two_raw",
            "project": "my_block",
            "testbench_id": "MY_LIB/my_block_tb/schematic",
            "test_name": "Test",
            "apply": [
                {"raw_expression": "0", "output_name": "a"},
                {"raw_expression": "1", "output_name": "b"},
            ],
        }
    )
    editor._entry_list.setCurrentRow(1)
    editor._on_move_up()
    apply = editor.dump_bundle()["apply"]
    assert apply[0]["output_name"] == "b"
    assert apply[1]["output_name"] == "a"


def test_move_down_swaps_with_next(editor):
    editor.load_bundle(
        {
            "measure_schema_version": 2,
            "name": "two_raw",
            "project": "my_block",
            "testbench_id": "MY_LIB/my_block_tb/schematic",
            "test_name": "Test",
            "apply": [
                {"raw_expression": "0", "output_name": "a"},
                {"raw_expression": "1", "output_name": "b"},
            ],
        }
    )
    editor._entry_list.setCurrentRow(0)
    editor._on_move_down()
    apply = editor.dump_bundle()["apply"]
    assert apply[0]["output_name"] == "b"
    assert apply[1]["output_name"] == "a"


def test_move_up_at_top_is_noop(editor):
    editor.load_bundle(_valid_one_entry_bundle())
    editor._entry_list.setCurrentRow(0)
    before = editor.dump_bundle()["apply"]
    editor._on_move_up()
    after = editor.dump_bundle()["apply"]
    assert before == after


# --- dump_bundle round-trip --------------------------------------------


def test_dump_bundle_round_trips(editor):
    original = _valid_one_entry_bundle()
    editor.load_bundle(original)
    dumped = editor.dump_bundle()
    # Metadata round-trips field-for-field.
    for key in (
        "measure_schema_version",
        "name",
        "project",
        "testbench_id",
        "test_name",
    ):
        assert dumped[key] == original[key]
    # apply array round-trips entry-for-entry (subset comparison — the
    # editor may add no extra keys for entries it didn't touch).
    assert len(dumped["apply"]) == len(original["apply"])
    for got, expect in zip(dumped["apply"], original["apply"]):
        for k, v in expect.items():
            assert got[k] == v


# --- apply_requested signal --------------------------------------------


def test_apply_button_emits_signal_with_rendered_rows(editor, qtbot):
    editor.load_bundle(_valid_one_entry_bundle())
    assert editor._apply_btn.isEnabled() is True

    received: list = []

    def _capture(rows):
        received.append(rows)

    editor.apply_requested.connect(_capture)

    with qtbot.waitSignal(editor.apply_requested, timeout=2000):
        qtbot.mouseClick(editor._apply_btn, Qt.LeftButton)

    assert len(received) == 1
    rows = received[0]
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert isinstance(rows[0], RenderedRow)
    assert rows[0].output_name == "Rtime_Vout"


def test_apply_button_disabled_does_not_emit_on_error(editor, qtbot):
    editor.load_bundle(_collision_bundle())
    assert editor._apply_btn.isEnabled() is False
    # The button being disabled means qtbot.mouseClick won't trigger
    # clicked() — assert no emission within a short window.
    received: list = []
    editor.apply_requested.connect(lambda rows: received.append(rows))
    qtbot.mouseClick(editor._apply_btn, Qt.LeftButton)
    assert received == []


# --- set_available_* dual-form contract ---------------------------------


def test_set_available_templates_accepts_list_of_names(qtbot):
    widget = MeasuresEditor()
    qtbot.addWidget(widget)
    # list-of-strings form: dropdown names registered but rendering will
    # fail for any entry that uses one of these templates.
    widget.set_available_templates(["foo", "bar"])
    assert widget._template_names == ["foo", "bar"]
    # Switch to dict form -> rendering works.
    widget.set_available_templates({"pn_at_freq": _pn_template()})
    assert widget._template_names == ["pn_at_freq"]
    assert "pn_at_freq" in widget._templates


def test_set_available_signal_groups_accepts_list_of_names(qtbot):
    widget = MeasuresEditor()
    qtbot.addWidget(widget)
    widget.set_available_signal_groups(["g1", "g2"])
    assert widget._signal_group_names == ["g1", "g2"]
    widget.set_available_signal_groups({"voltage_outs": _voltage_outs_group()})
    assert widget._signal_group_names == ["voltage_outs"]
    assert "voltage_outs" in widget._signal_groups


# --- Multi-row signal_group renders many rows --------------------------


def test_multi_signal_group_renders_one_row_per_signal(editor):
    bundle = {
        "measure_schema_version": 2,
        "name": "two_sig",
        "project": "my_block",
        "testbench_id": "MY_LIB/my_block_tb/schematic",
        "test_name": "Test",
        "apply": [
            {
                "template": "rise_time_threshold",
                "signal_group": "two_signals",
            }
        ],
    }
    editor.load_bundle(bundle)
    assert editor._status_label.text() == "OK"
    assert editor._preview_model.rowCount() == 2
    names = [
        editor._preview_model.item(r, 0).text()
        for r in range(editor._preview_model.rowCount())
    ]
    assert names == ["Rtime_Vout", "Rtime_Vout2"]


# --- G-1a: spec field discoverability + validation -----------------------


def _entry_dialog(spec: str = ""):
    entry = {"raw_expression": "/Vout", "output_name": "gain"}
    if spec:
        entry["spec"] = spec
    return _EntryDialog(entry, template_names=[], signal_group_names=[])


def test_entry_dialog_spec_field_has_placeholder_and_hint(qtbot):
    d = _entry_dialog()
    qtbot.addWidget(d)
    assert d._spec_edit.placeholderText() != ""
    # Hint label is present and non-empty so the syntax is discoverable.
    assert d._spec_hint.text() != ""


def test_entry_dialog_flags_unparseable_spec(qtbot):
    d = _entry_dialog()
    qtbot.addWidget(d)
    d._spec_edit.setText(">> not a spec")
    assert "failed to parse" in d._spec_hint.text()
    assert "border" in d._spec_edit.styleSheet()


def test_entry_dialog_accepts_valid_spec(qtbot):
    d = _entry_dialog()
    qtbot.addWidget(d)
    d._spec_edit.setText("range 1 5")
    assert "failed to parse" not in d._spec_hint.text()
    assert d._spec_edit.styleSheet() == ""
    # The valid spec round-trips through updated_entry().
    assert d.updated_entry()["spec"] == "range 1 5"


def test_entry_dialog_prefilled_bad_spec_flagged_on_open(qtbot):
    d = _entry_dialog(spec="garbage!!")
    qtbot.addWidget(d)
    assert "failed to parse" in d._spec_hint.text()


# --- G-14: measures-editor affordances ------------------------------------


def test_edit_button_present(editor):
    assert editor._edit_btn is not None
    assert editor._edit_btn.objectName() == "editEntryBtn"
    assert "Edit" in editor._edit_btn.text()


def test_edit_entry_at_invalid_row_is_noop(editor):
    editor.load_bundle({"apply": [
        {"raw_expression": "0", "output_name": "x"},
    ]})
    before = editor.dump_bundle()
    editor._edit_entry_at(-1)
    editor._edit_entry_at(99)
    assert editor.dump_bundle() == before


def test_edit_button_opens_dialog_for_selected_entry(editor, qtbot, monkeypatch):
    from PyQt5.QtWidgets import QDialog
    from simkit.gui.views import measures_editor as me

    captured = []

    class _FakeDialog:
        def __init__(self, entry, *, template_names, signal_group_names,
                     parent=None):
            captured.append(dict(entry))
            self._entry = dict(entry)

        def exec_(self):
            return QDialog.Accepted

        def updated_entry(self):
            e = dict(self._entry)
            e["alias_suffix"] = "EDITED"
            return e

    monkeypatch.setattr(me, "_EntryDialog", _FakeDialog)
    editor.load_bundle({"apply": [
        {"raw_expression": "0", "output_name": "first"},
        {"raw_expression": "1", "output_name": "second"},
    ]})
    editor._entry_list.setCurrentRow(1)
    qtbot.mouseClick(editor._edit_btn, Qt.LeftButton)

    # Dialog opened for the *selected* (second) entry.
    assert captured and captured[-1]["output_name"] == "second"
    # The accepted edit was written back.
    assert editor.dump_bundle()["apply"][1]["alias_suffix"] == "EDITED"


def test_entry_summary_has_no_cryptic_brackets(editor):
    editor.load_bundle({"apply": [
        {"raw_expression": "ymax(VT(\"/o\"))", "output_name": "pk"},
        {"template": "rise_time_threshold", "signal_group": None},
    ]})
    raw_label = editor._entry_list.item(0).text()
    tmpl_label = editor._entry_list.item(1).text()
    assert raw_label.startswith("Raw expression")
    assert tmpl_label.startswith("Template")
    assert "[raw]" not in raw_label and "[template]" not in tmpl_label


def test_raw_entry_dialog_has_help_hint_and_placeholder(qtbot):
    d = _entry_dialog()  # builds a raw-kind entry
    qtbot.addWidget(d)
    assert d._raw_expr_edit is not None
    assert d._raw_expr_edit.placeholderText() != ""
