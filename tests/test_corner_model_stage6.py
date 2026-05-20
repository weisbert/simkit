"""Tests for Phase 5 Stage 6 — PVT Profile semantic mapping layer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from simkit import union
from simkit.corner_model import (
    CornerModelSchemaVersionError,
    CornerModelValidationError,
    apply_template,
    check_cornermodel,
    column_point_count,
    load_cornermodel,
    load_pvtprofile,
    materialize,
    materialize_column,
    profile_to_dict,
    to_dict,
)


def _profile_dict() -> dict:
    return {
        "pvtprofile_schema_version": 1,
        "name": "rf018",
        "project": "1AXX",
        "axes": {
            "process": {"levels": {
                "TT": {"models": [{"file": "rf018.scs", "section": "tt"}]},
                "ssMOS_ffRC": {"models": [
                    {"file": "mos.scs", "section": "ss"},
                    {"file": "rc.scs", "section": "ff"}]},
                "allsec": {"models": [{"section": "newsec"}]},
            }},
            "voltage": {"levels": {
                "nominal": {"vars": {"LDO_VSET": "20"}},
                "low": {"vars": {"LDO_VSET": "15"}},
            }},
            "temperature": {"levels": {
                "nominal": {"vars": {"temperature": "55"}},
                "drift": {"vars": {"temperature": ["-40", "27", "85"]}},
            }},
        },
    }


def _load_profile(tmp_path: Path, data: dict | None = None):
    data = data or _profile_dict()
    p = tmp_path / f"{data['name']}.pvtprofile.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return load_pvtprofile(p)


def _cm_dict() -> dict:
    return {
        "cornermodel_schema_version": 1,
        "name": "lo_corners",
        "project": "1AXX",
        "testbench_id": "sim_yusheng/Test/maestro",
        "pvt_profile": "rf018",
        "modes": {"M": {"vars": {"d_en": "1"}}},
        "columns": [
            {"mode": "M", "pvt_label": "TT", "enabled": True,
             "axis_levels": {"process": "TT", "voltage": "nominal",
                             "temperature": "nominal"}},
        ],
    }


def _load_cm(tmp_path: Path, data: dict | None = None):
    data = data or _cm_dict()
    p = tmp_path / f"{data['name']}.cornermodel.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return load_cornermodel(p)


# --- profile loading -----------------------------------------------------


def test_load_profile(tmp_path):
    prof = _load_profile(tmp_path)
    assert set(prof.axes) == {"process", "voltage", "temperature"}
    assert "ssMOS_ffRC" in prof.axes["process"].levels


def test_profile_bad_version(tmp_path):
    d = _profile_dict()
    d["pvtprofile_schema_version"] = 7
    with pytest.raises(CornerModelSchemaVersionError):
        _load_profile(tmp_path, d)


def test_profile_level_needs_vars_or_models(tmp_path):
    d = _profile_dict()
    d["axes"]["voltage"]["levels"]["empty"] = {}
    with pytest.raises(CornerModelValidationError):
        _load_profile(tmp_path, d)


def test_profile_basename_must_match_name(tmp_path):
    d = _profile_dict()
    p = tmp_path / "wrong.pvtprofile.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(CornerModelValidationError):
        load_pvtprofile(p)


# --- materialise resolves axis_levels ------------------------------------


def test_materialize_resolves_var_levels(tmp_path):
    prof = _load_profile(tmp_path)
    cm = _load_cm(tmp_path)
    row = materialize_column(cm, cm.columns[0], prof)
    assert row.vars["LDO_VSET"] == ("20",)        # voltage:nominal
    assert row.vars["temperature"] == ("55",)     # temperature:nominal
    assert row.vars["d_en"] == ("1",)             # mode reg


def test_materialize_resolves_process_models(tmp_path):
    prof = _load_profile(tmp_path)
    cm = _load_cm(tmp_path)
    row = materialize_column(cm, cm.columns[0], prof)
    # process:TT -> one model rf018.scs section tt
    assert len(row.models) == 1
    assert row.models[0].file == "rf018.scs"
    assert row.models[0].section == ("tt",)


def test_split_corner_resolves_to_two_models(tmp_path):
    prof = _load_profile(tmp_path)
    d = _cm_dict()
    d["columns"][0]["axis_levels"]["process"] = "ssMOS_ffRC"
    cm = _load_cm(tmp_path, d)
    row = materialize_column(cm, cm.columns[0], prof)
    by_file = {m.file: m.section[0] for m in row.models}
    assert by_file == {"mos.scs": "ss", "rc.scs": "ff"}


def test_nofile_process_level_sets_section_on_all_models(tmp_path):
    prof = _load_profile(tmp_path)
    d = _cm_dict()
    d["columns"][0]["models"] = [
        {"file": "a.scs", "section": "old"},
        {"file": "b.scs", "section": "old"},
    ]
    d["columns"][0]["axis_levels"]["process"] = "allsec"
    cm = _load_cm(tmp_path, d)
    row = materialize_column(cm, cm.columns[0], prof)
    assert all(m.section == ("newsec",) for m in row.models)
    assert len(row.models) == 2


def test_temperature_drift_level_is_a_sweep(tmp_path):
    prof = _load_profile(tmp_path)
    d = _cm_dict()
    d["columns"][0]["axis_levels"]["temperature"] = "drift"
    cm = _load_cm(tmp_path, d)
    assert column_point_count(cm, cm.columns[0], prof) == 3   # 3 temp points


def test_materialize_without_profile_ignores_axis_levels(tmp_path):
    cm = _load_cm(tmp_path)
    row = materialize_column(cm, cm.columns[0])     # no profile
    assert "LDO_VSET" not in row.vars               # axis_levels not resolved
    assert row.vars["d_en"] == ("1",)               # literal layer still works


def test_materialize_whole_model_with_profile(tmp_path):
    prof = _load_profile(tmp_path)
    cm = _load_cm(tmp_path)
    u = materialize(cm, prof)
    assert union.explode(u)[0].vars["LDO_VSET"] == "20"


# --- templates carry axis_levels -----------------------------------------


def test_apply_template_carries_axis_levels(tmp_path):
    d = _cm_dict()
    d["pvt_templates"] = {"t": {"columns": [
        {"pvt_label": "PVT", "axis_levels": {"voltage": "low"}}
    ]}}
    cm = _load_cm(tmp_path, d)
    cm2 = apply_template(cm, "M", "t")
    gen = next(c for c in cm2.columns if c.pvt_label == "PVT")
    assert gen.axis_levels == {"voltage": "low"}


# --- check_cornermodel ---------------------------------------------------


def test_check_flags_unknown_axis_level(tmp_path):
    prof = _load_profile(tmp_path)
    d = _cm_dict()
    d["columns"][0]["axis_levels"]["voltage"] = "ghost_level"
    cm = _load_cm(tmp_path, d)
    issues = check_cornermodel(cm, profile=prof)
    assert any(i.code == "unknown_axis_level" for i in issues)


def test_check_flags_missing_profile(tmp_path):
    cm = _load_cm(tmp_path)                 # pvt_profile set, none passed
    issues = check_cornermodel(cm)
    assert any(i.code == "missing_profile" for i in issues)


# --- round-trips ---------------------------------------------------------


def test_cornermodel_round_trip_stage6(tmp_path):
    cm = _load_cm(tmp_path)
    out = tmp_path / "lo_corners.cornermodel.json"
    out.write_text(json.dumps(to_dict(cm)), encoding="utf-8")
    assert to_dict(load_cornermodel(out)) == to_dict(cm)


def test_profile_round_trip(tmp_path):
    prof = _load_profile(tmp_path)
    out = tmp_path / "rf018.pvtprofile.json"
    out.write_text(json.dumps(profile_to_dict(prof)), encoding="utf-8")
    assert profile_to_dict(load_pvtprofile(out)) == profile_to_dict(prof)
