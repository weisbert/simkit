"""Tests for Phase 5 Stage 2 — PVT templates, aggregation, correlated axes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from simkit import union
from simkit.corner_model import (
    CorrelatedAxis,
    CorrelatedTuple,
    CornerModelValidationError,
    PvtTemplate,
    TemplateColumn,
    add_correlated_axis,
    add_pvt_template,
    apply_template,
    classify_pull,
    column_display_vars,
    column_point_count,
    effective_name,
    load_cornermodel,
    materialize,
    materialize_column_rows,
    to_dict,
    unbind_template,
)


def _proc_ct() -> dict:
    return {
        "members": ["process", "CT"],
        "tuples": [
            {"label": "tt", "values": {"process": "tt", "CT": "100"}},
            {"label": "ff", "values": {"process": "ff", "CT": "88"}},
            {"label": "ss", "values": {"process": "ss", "CT": "120"}},
            {"label": "fs", "values": {"process": "fs", "CT": "100"}},
            {"label": "sf", "values": {"process": "sf", "CT": "100"}},
        ],
    }


def _temp_s5p() -> dict:
    return {
        "members": ["temperature", "s5p"],
        "tuples": [
            {"label": "t55", "values": {"temperature": "55", "s5p": "L_55"}},
            {"label": "t125", "values": {"temperature": "125", "s5p": "L_125"}},
            {"label": "tn40", "values": {"temperature": "-40", "s5p": "L_n40"}},
        ],
    }


def _base() -> dict:
    return {
        "cornermodel_schema_version": 1,
        "name": "vco_corners",
        "project": "1AXX",
        "testbench_id": "sim_yusheng/Test/maestro",
        "modes": {"VCO": {"vars": {"d_en_dummy": "1"}}},
        "correlated_axes": {"proc_ct": _proc_ct(), "temp_s5p": _temp_s5p()},
        "pvt_templates": {
            "vco_full": {
                "columns": [
                    {"pvt_label": "TT",
                     "pvt_vars": {"temperature": "55"}},
                    {"pvt_label": "PVT_45",
                     "pvt_vars": {"VDD": ["0.9", "0.85", "0.95"]},
                     "correlated_axes": ["proc_ct", "temp_s5p"]},
                ]
            }
        },
        "columns": [
            {"mode": "VCO", "pvt_label": "scalar_tt", "enabled": True,
             "pvt_vars": {"temperature": "55"}},
        ],
    }


def _write_load(tmp_path: Path, data: dict):
    p = tmp_path / f"{data['name']}.cornermodel.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return load_cornermodel(p)


# --- loading -------------------------------------------------------------


def test_load_stage2_sidecar(tmp_path):
    cm = _write_load(tmp_path, _base())
    assert set(cm.correlated_axes) == {"proc_ct", "temp_s5p"}
    assert len(cm.correlated_axes["proc_ct"].tuples) == 5
    assert "vco_full" in cm.pvt_templates


def test_correlated_tuple_must_cover_members(tmp_path):
    d = _base()
    d["correlated_axes"]["proc_ct"]["tuples"][0]["values"] = {"process": "tt"}
    with pytest.raises(CornerModelValidationError):
        _write_load(tmp_path, d)


def test_duplicate_tuple_label_rejected(tmp_path):
    d = _base()
    d["correlated_axes"]["proc_ct"]["tuples"][1]["label"] = "tt"
    with pytest.raises(CornerModelValidationError):
        _write_load(tmp_path, d)


def test_column_axis_member_collision_rejected(tmp_path):
    d = _base()
    d["columns"].append(
        {"mode": "VCO", "pvt_label": "bad", "enabled": True,
         "pvt_vars": {"CT": "50"}, "correlated_axes": ["proc_ct"]}
    )
    with pytest.raises(CornerModelValidationError):
        _write_load(tmp_path, d)


# --- materialisation with correlated axes --------------------------------


def test_correlated_column_expands_to_multiple_rows(tmp_path):
    cm = _write_load(tmp_path, _base())
    cm = apply_template(cm, "VCO", "vco_full")
    pvt45 = next(c for c in cm.columns if c.pvt_label == "PVT_45")
    rows = materialize_column_rows(cm, pvt45)
    # 5 proc_ct tuples × 3 temp_s5p tuples = 15 union rows
    assert len(rows) == 15
    assert "VCO_PVT_45__tt_t55" in {r.row_name for r in rows}


def test_vco_45_points_not_405(tmp_path):
    # 痛点 h: [proc+CT] × [VDD] × [temp+s5p] = 45, not 5×3×3×3×3=405.
    cm = _write_load(tmp_path, _base())
    cm = apply_template(cm, "VCO", "vco_full")
    pvt45 = next(c for c in cm.columns if c.pvt_label == "PVT_45")
    assert column_point_count(cm, pvt45) == 45


def test_column_display_vars_merges_expansion(tmp_path):
    cm = _write_load(tmp_path, _base())
    cm = apply_template(cm, "VCO", "vco_full")
    pvt45 = next(c for c in cm.columns if c.pvt_label == "PVT_45")
    disp = column_display_vars(cm, pvt45)
    assert set(disp["process"]) == {"tt", "ff", "ss", "fs", "sf"}
    assert set(disp["VDD"]) == {"0.9", "0.85", "0.95"}


def test_materialize_whole_model_explodes(tmp_path):
    cm = _write_load(tmp_path, _base())
    cm = apply_template(cm, "VCO", "vco_full")
    u = materialize(cm)
    subs = union.explode(u)
    # scalar_tt(1) + TT(1) + PVT_45(45)
    assert len(subs) == 47


# --- templates: apply / unbind ------------------------------------------


def test_apply_template_generates_columns(tmp_path):
    cm = _write_load(tmp_path, _base())
    cm2 = apply_template(cm, "VCO", "vco_full")
    names = {effective_name(c) for c in cm2.columns}
    assert {"VCO_TT", "VCO_PVT_45"} <= names
    assert any(
        b.mode == "VCO" and b.template == "vco_full"
        for b in cm2.template_bindings
    )


def test_apply_template_idempotent(tmp_path):
    cm = _write_load(tmp_path, _base())
    cm2 = apply_template(cm, "VCO", "vco_full")
    cm3 = apply_template(cm2, "VCO", "vco_full")
    assert len(cm3.columns) == len(cm2.columns)         # no duplicates


def test_unbind_freezes_generated_columns(tmp_path):
    cm = _write_load(tmp_path, _base())
    cm = apply_template(cm, "VCO", "vco_full")
    cm = unbind_template(cm, "VCO", "vco_full")
    assert cm.template_bindings == ()
    gen = [c for c in cm.columns if c.pvt_label == "PVT_45"]
    assert len(gen) == 1                                 # column kept (D3)
    assert gen[0].template is None                       # provenance frozen


# --- builders ------------------------------------------------------------


def test_add_correlated_axis_and_template(tmp_path):
    cm = _write_load(tmp_path, _base())
    axis = CorrelatedAxis(
        name="vdd_axis", members=("VDD",),
        tuples=(
            CorrelatedTuple(label="lo", values={"VDD": "0.8"}),
            CorrelatedTuple(label="hi", values={"VDD": "1.0"}),
        ),
    )
    cm2 = add_correlated_axis(cm, axis)
    assert "vdd_axis" in cm2.correlated_axes
    tmpl = PvtTemplate(
        name="mini",
        columns=(TemplateColumn(pvt_label="A", correlated_axes=("vdd_axis",)),),
    )
    cm3 = add_pvt_template(cm2, tmpl)
    assert "mini" in cm3.pvt_templates


# --- round-trip + reconciliation -----------------------------------------


def test_to_dict_round_trip_stage2(tmp_path):
    cm = _write_load(tmp_path, _base())
    cm = apply_template(cm, "VCO", "vco_full")
    out = tmp_path / "vco_corners.cornermodel.json"
    out.write_text(json.dumps(to_dict(cm)), encoding="utf-8")
    reloaded = load_cornermodel(out)
    assert to_dict(reloaded) == to_dict(cm)


def test_classify_pull_maps_expanded_correlated_names(tmp_path):
    cm = _write_load(tmp_path, _base())
    cm = apply_template(cm, "VCO", "vco_full")
    pulled = materialize(cm)                  # round-trip our own expansion
    result = classify_pull(cm, pulled)
    assert result.foreign == ()
    assert "VCO_PVT_45__tt_t55" in result.matched
