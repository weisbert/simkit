"""Tests for Phase 5 Stage 2 — aggregation, correlated axes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from simkit import union
from simkit.corner_model import (
    CorrelatedAxis,
    CorrelatedTuple,
    CornerModelValidationError,
    add_correlated_axis,
    classify_pull,
    column_display_vars,
    column_point_count,
    empty_cornermodel,
    load_cornermodel,
    materialize,
    materialize_column_rows,
    remove_correlated_axis,
    to_dict,
    update_correlated_axis,
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
        "columns": [
            {"mode": "VCO", "pvt_label": "scalar_tt", "enabled": True,
             "pvt_vars": {"temperature": "55"}},
            {"mode": "VCO", "pvt_label": "TT", "enabled": True,
             "pvt_vars": {"temperature": "55"}},
            {"mode": "VCO", "pvt_label": "PVT_45", "enabled": True,
             "pvt_vars": {"VDD": ["0.9", "0.85", "0.95"]},
             "correlated_axes": ["proc_ct", "temp_s5p"]},
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
    pvt45 = next(c for c in cm.columns if c.pvt_label == "PVT_45")
    rows = materialize_column_rows(cm, pvt45)
    # 5 proc_ct tuples × 3 temp_s5p tuples = 15 union rows
    assert len(rows) == 15
    assert "VCO_PVT_45__tt_t55" in {r.row_name for r in rows}


def test_vco_45_points_not_405(tmp_path):
    # 痛点 h: [proc+CT] × [VDD] × [temp+s5p] = 45, not 5×3×3×3×3=405.
    cm = _write_load(tmp_path, _base())
    pvt45 = next(c for c in cm.columns if c.pvt_label == "PVT_45")
    assert column_point_count(cm, pvt45) == 45


def test_column_display_vars_merges_expansion(tmp_path):
    cm = _write_load(tmp_path, _base())
    pvt45 = next(c for c in cm.columns if c.pvt_label == "PVT_45")
    disp = column_display_vars(cm, pvt45)
    assert set(disp["process"]) == {"tt", "ff", "ss", "fs", "sf"}
    assert set(disp["VDD"]) == {"0.9", "0.85", "0.95"}


def test_materialize_whole_model_explodes(tmp_path):
    cm = _write_load(tmp_path, _base())
    u = materialize(cm)
    subs = union.explode(u)
    # scalar_tt(1) + TT(1) + PVT_45(45)
    assert len(subs) == 47


# --- builders ------------------------------------------------------------


def test_add_correlated_axis(tmp_path):
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


def test_update_and_remove_correlated_axis():
    ax = CorrelatedAxis(
        name="v", members=("VDD",),
        tuples=(CorrelatedTuple(label="nom", values={"VDD": "1.0"}),),
    )
    cm = add_correlated_axis(empty_cornermodel(), ax)
    grown = CorrelatedAxis(
        name="v", members=("VDD",),
        tuples=(
            CorrelatedTuple(label="nom", values={"VDD": "1.0"}),
            CorrelatedTuple(label="hi", values={"VDD": "1.1"}),
        ),
    )
    cm = update_correlated_axis(cm, grown)
    assert len(cm.correlated_axes["v"].tuples) == 2
    cm = remove_correlated_axis(cm, "v")
    assert "v" not in cm.correlated_axes


def test_remove_correlated_axis_in_use_rejected(tmp_path):
    # _base()'s PVT_45 column crosses proc_ct — the axis cannot be dropped
    # while a column still references it.
    cm = _write_load(tmp_path, _base())
    with pytest.raises(CornerModelValidationError):
        remove_correlated_axis(cm, "proc_ct")


# --- round-trip + reconciliation -----------------------------------------


def test_to_dict_round_trip_stage2(tmp_path):
    cm = _write_load(tmp_path, _base())
    out = tmp_path / "vco_corners.cornermodel.json"
    out.write_text(json.dumps(to_dict(cm)), encoding="utf-8")
    reloaded = load_cornermodel(out)
    assert to_dict(reloaded) == to_dict(cm)


def test_classify_pull_maps_expanded_correlated_names(tmp_path):
    cm = _write_load(tmp_path, _base())
    pulled = materialize(cm)                  # round-trip our own expansion
    result = classify_pull(cm, pulled)
    assert result.foreign == ()
    assert "VCO_PVT_45__tt_t55" in result.matched


# --- section-bearing dimensions / level subsets / inline -----------------


def test_section_dimension_materialize_applies_section(tmp_path):
    d = _base()
    d["correlated_axes"]["proc"] = {
        "members": ["CT"], "model_file": "/pdk/m.scs",
        "tuples": [
            {"label": "TT", "values": {"CT": "100"}, "section": "tt"},
            {"label": "SS", "values": {"CT": "120"}, "section": "ss"},
        ],
    }
    d["columns"].append({
        "mode": "VCO", "pvt_label": "PROC", "enabled": True,
        "correlated_axes": ["proc"],
    })
    cm = _write_load(tmp_path, d)
    col = next(c for c in cm.columns if c.pvt_label == "PROC")
    rows = materialize_column_rows(cm, col)
    assert len(rows) == 2
    assert {r.models[0].section for r in rows} == {("tt",), ("ss",)}


def test_selected_levels_subset(tmp_path):
    from simkit.corner_model import Column, add_column
    cm = _write_load(tmp_path, _base())
    col = Column(
        mode="VCO", enabled=True, pvt_vars={}, models=(), pvt_label="SUB",
        correlated_axes=("proc_ct",),
        selected_levels={"proc_ct": ("tt", "ff")},
    )
    cm = add_column(cm, col)
    sub = next(c for c in cm.columns if c.pvt_label == "SUB")
    assert column_point_count(cm, sub) == 2          # 2 of 5 proc_ct levels


def test_inline_axis_crosses_and_round_trips(tmp_path):
    from simkit.corner_model import Column, add_column
    cm = _write_load(tmp_path, _base())
    inline = CorrelatedAxis("vlt", ("VDD",), (
        CorrelatedTuple("a", {"VDD": "1"}),
        CorrelatedTuple("b", {"VDD": "2"}),
    ))
    cm = add_column(cm, Column(
        mode="VCO", enabled=True, pvt_vars={}, models=(),
        pvt_label="INL", inline_axes=(inline,),
    ))
    inl = next(c for c in cm.columns if c.pvt_label == "INL")
    assert column_point_count(cm, inl) == 2
    out = tmp_path / "vco_corners.cornermodel.json"
    out.write_text(json.dumps(to_dict(cm)), encoding="utf-8")
    assert to_dict(load_cornermodel(out)) == to_dict(cm)


def test_assign_mode_to_column(tmp_path):
    from simkit.corner_model import assign_mode_to_column, effective_name
    d = _base()
    d["columns"].append({
        "name": "RawCorner", "mode": None, "enabled": True,
        "pvt_vars": {"temperature": "55"},
    })
    cm = _write_load(tmp_path, d)
    idx = next(i for i, c in enumerate(cm.columns) if c.name == "RawCorner")
    cm2 = assign_mode_to_column(cm, idx, "VCO")
    assert cm2.columns[idx].mode == "VCO"
    assert effective_name(cm2.columns[idx]) == "VCO_RawCorner"
