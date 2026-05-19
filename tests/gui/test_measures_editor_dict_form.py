"""Verify MeasuresEditor.set_available_templates accepts dict-form (Phase 4 Stage 3).

The recon found ``set_available_templates`` already accepts
``dict[str, Template]``. This test pins the dict-form contract by loading
two real Template objects, then driving the editor programmatically to
confirm the live preview surfaces the expected output_name for an apply
entry that references one of them.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("PyQt5")

from simkit.signal_group import Signal, SignalGroup  # noqa: E402
from simkit.template import Template, TemplateParam  # noqa: E402

from simkit.gui.views.measures_editor import MeasuresEditor  # noqa: E402


def _pn_template() -> Template:
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


def _rise_template() -> Template:
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
        source_path=Path("/tmp/fake_rise.template.json"),
    )


def _voltage_outs() -> SignalGroup:
    return SignalGroup(
        signal_group_schema_version=1,
        name="voltage_outs",
        signals=(Signal(net="/Vout"),),
        source_path=Path("/tmp/fake_voltage_outs.siggroup.json"),
    )


@pytest.fixture
def editor(qtbot):
    w = MeasuresEditor()
    qtbot.addWidget(w)
    return w


def test_set_available_templates_dict_form_populates_lookup(editor):
    templates = {
        "pn_at_freq": _pn_template(),
        "rise_time_threshold": _rise_template(),
    }
    editor.set_available_templates(templates)
    assert "pn_at_freq" in editor._templates
    assert "rise_time_threshold" in editor._templates
    assert sorted(editor._template_names) == [
        "pn_at_freq",
        "rise_time_threshold",
    ]


def test_dict_form_renders_apply_entry_to_expected_output_name(editor):
    editor.set_available_templates(
        {
            "rise_time_threshold": _rise_template(),
        }
    )
    editor.set_available_signal_groups({"voltage_outs": _voltage_outs()})
    editor.load_bundle(
        {
            "measure_schema_version": 2,
            "name": "demo",
            "project": "demo",
            "testbench_id": "LIB/cell/schematic",
            "test_name": "Test",
            "apply": [
                {
                    "template": "rise_time_threshold",
                    "signal_group": "voltage_outs",
                }
            ],
        }
    )
    assert editor._status_label.text() == "OK"
    assert editor._preview_model.rowCount() == 1
    # rise template short_alias=Rtime; signal_basename(/Vout)=Vout
    assert editor._preview_model.item(0, 0).text() == "Rtime_Vout"


def test_dict_form_unknown_template_in_apply_surfaces_error(editor):
    # Only register pn_at_freq; apply asks for missing_template -> error.
    editor.set_available_templates({"pn_at_freq": _pn_template()})
    editor.load_bundle(
        {
            "measure_schema_version": 2,
            "name": "demo",
            "project": "demo",
            "testbench_id": "LIB/cell/schematic",
            "test_name": "Test",
            "apply": [
                {
                    "template": "missing_template",
                    "param_overrides": {"OUT_NAME": "pn"},
                }
            ],
        }
    )
    assert editor._status_label.text() == "Error"
    assert editor._apply_btn.isEnabled() is False


def test_dict_form_replaces_previous_registration(editor):
    editor.set_available_templates({"pn_at_freq": _pn_template()})
    # New registration replaces the old set wholesale.
    editor.set_available_templates({"rise_time_threshold": _rise_template()})
    assert "pn_at_freq" not in editor._templates
    assert "rise_time_threshold" in editor._templates


def test_programmatic_apply_entry_render_matches_pn_at_freq(editor):
    editor.set_available_templates({"pn_at_freq": _pn_template()})
    # No signal_group needed — pn_at_freq has no signal-kind param.
    editor.load_bundle(
        {
            "measure_schema_version": 2,
            "name": "pn_check",
            "project": "demo",
            "testbench_id": "LIB/cell/schematic",
            "test_name": "Test",
            "apply": [
                {
                    "template": "pn_at_freq",
                    "param_overrides": {"OUT_NAME": "pn", "FREQ": "1e6"},
                    "output_name": "pn_at_1M",
                }
            ],
        }
    )
    assert editor._status_label.text() == "OK"
    assert editor._preview_model.rowCount() == 1
    assert editor._preview_model.item(0, 0).text() == "pn_at_1M"
