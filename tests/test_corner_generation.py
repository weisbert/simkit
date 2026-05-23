"""Tests for the PVT corner generator — generate_pattern_columns (痛点 a / h).

A generator pattern row expands into corner columns: composite axes (a level
binds 2+ variables) split into one column each; simple axes (one variable)
stay multi-valued. The column name carries only the composite level labels.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.corner_model import (  # noqa: E402
    CornerModelError,
    add_column,
    axis_is_composite,
    column_point_count,
    effective_name,
    generate_pattern_columns,
    load_cornermodel,
    materialize,
)


def _proc(members_extra: bool) -> dict:
    """A section-bearing process axis. With members_extra it also carries
    CT (-> composite); without, section only (-> simple)."""
    labels = ["TT", "SS", "FF", "FS", "SF"]
    cts = {"TT": "100", "SS": "120", "FF": "88", "FS": "100", "SF": "100"}
    tuples = []
    for lab in labels:
        t = {"label": lab, "values": {}, "section": lab.lower()}
        if members_extra:
            t["values"] = {"CT": cts[lab]}
        tuples.append(t)
    axis = {"model_file": "/pdk/models.scs", "tuples": tuples}
    axis["members"] = ["CT"] if members_extra else []
    return axis


def _temp(members_extra: bool) -> dict:
    """Temperature axis. With members_extra it also carries the .s5p
    inductor file (-> composite); without, temperature only (-> simple)."""
    rows = [("NT", "55", "L_55"), ("LT", "-40", "L_n40"), ("HT", "125", "L_125")]
    tuples = []
    for lab, temp, s5p in rows:
        vals = {"temperature": temp}
        if members_extra:
            vals["indfile"] = f"{s5p}.s5p"
        tuples.append({"label": lab, "values": vals})
    members = ["temperature", "indfile"] if members_extra else ["temperature"]
    return {"members": members, "tuples": tuples}


def _volt() -> dict:
    """Voltage axis — one variable, always simple."""
    return {
        "members": ["vdd"],
        "tuples": [
            {"label": "NV", "values": {"vdd": "0.80"}},
            {"label": "HV", "values": {"vdd": "0.85"}},
            {"label": "LV", "values": {"vdd": "0.75"}},
        ],
    }


def _model(tmp_path: Path):
    data = {
        "cornermodel_schema_version": 1,
        "name": "gen",
        "project": "1AXX",
        "testbench_id": "sim/Test/maestro",
        "modes": {"VCO": {"vars": {"d_en_dummy": "1"}}},
        "correlated_axes": {
            "proc_c": _proc(True),
            "proc_s": _proc(False),
            "volt": _volt(),
            "temp_c": _temp(True),
            "temp_s": _temp(False),
        },
        "columns": [
            {"mode": "VCO", "pvt_label": "seed", "enabled": True,
             "correlated_axes": ["volt"]},
        ],
    }
    p = tmp_path / "gen.cornermodel.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return load_cornermodel(p)


# --- composite classification ------------------------------------------------

def test_axis_is_composite_classification(tmp_path):
    cm = _model(tmp_path)
    assert axis_is_composite(cm.correlated_axes["proc_c"])    # section + CT
    assert axis_is_composite(cm.correlated_axes["temp_c"])    # temp + indfile
    assert not axis_is_composite(cm.correlated_axes["proc_s"])  # section only
    assert not axis_is_composite(cm.correlated_axes["volt"])    # vdd only
    assert not axis_is_composite(cm.correlated_axes["temp_s"])  # temp only


# --- all-simple: one column --------------------------------------------------

def test_all_simple_axes_generate_one_column(tmp_path):
    cm = _model(tmp_path)
    cols = generate_pattern_columns(
        cm, "VCO", "Beacon_PVT_45",
        [("proc_s", ("TT", "SS", "FF", "FS", "SF")),
         ("volt", ("NV", "HV", "LV")),
         ("temp_s", ("NT", "LT", "HT"))],
    )
    assert len(cols) == 1
    assert effective_name(cols[0]) == "Beacon_PVT_45"
    assert column_point_count(cm, cols[0]) == 45


# --- composite axes expand ---------------------------------------------------

def test_composite_axes_expand_to_one_column_each(tmp_path):
    cm = _model(tmp_path)
    cols = generate_pattern_columns(
        cm, "VCO", "VCO_PVT_45",
        [("proc_c", ("TT", "SS", "FF", "FS", "SF")),
         ("volt", ("NV", "HV", "LV")),
         ("temp_c", ("NT", "LT", "HT"))],
    )
    # process (5, composite) x temperature (3, composite) = 15 columns
    assert len(cols) == 15
    names = {effective_name(c) for c in cols}
    assert "VCO_PVT_45_TT_NT" in names
    assert "VCO_PVT_45_SF_HT" in names
    # voltage is simple -> stays multi-valued, never in a column name
    assert all("NV" not in n and "HV" not in n for n in names)
    # each column still sweeps the 3 voltages
    for c in cols:
        assert column_point_count(cm, c) == 3


def test_one_composite_one_simple(tmp_path):
    cm = _model(tmp_path)
    cols = generate_pattern_columns(
        cm, "VCO", "P5",
        [("proc_c", ("TT", "SS", "FF", "FS", "SF")),
         ("volt", ("NV", "HV", "LV"))],
    )
    assert len(cols) == 5
    assert {effective_name(c) for c in cols} == {
        "P5_TT", "P5_SS", "P5_FF", "P5_FS", "P5_SF"
    }


def test_name_suffix_follows_selection_order(tmp_path):
    cm = _model(tmp_path)
    cols = generate_pattern_columns(
        cm, "VCO", "X",
        [("proc_c", ("TT",)), ("temp_c", ("NT",))],
    )
    assert len(cols) == 1
    assert effective_name(cols[0]) == "X_TT_NT"


# --- generated columns are valid in the model --------------------------------

def test_generated_columns_round_trip_through_add_column(tmp_path):
    cm = _model(tmp_path)
    cols = generate_pattern_columns(
        cm, "VCO", "VCO_PVT",
        [("proc_c", ("TT", "SS")), ("volt", ("NV", "HV")),
         ("temp_c", ("NT", "HT"))],
    )
    assert len(cols) == 4
    for c in cols:
        cm = add_column(cm, c)
    # re-load to prove the generated columns serialize + validate
    # (filename basename must equal the model name "gen").
    from simkit.corner_model import save_cornermodel
    out_dir = tmp_path / "rt"
    out_dir.mkdir()
    out = out_dir / "gen.cornermodel.json"
    save_cornermodel(cm, out)
    reloaded = load_cornermodel(out)
    assert "VCO_PVT_TT_NT" in {effective_name(c) for c in reloaded.columns}


# --- materialise: one column == one Maestro corner --------------------------

def test_all_simple_pattern_materialises_to_one_row(tmp_path):
    # A pattern of only simple axes is ONE column — and must stay ONE
    # corner (a single union row with the levels swept inside it), not
    # expand into N rows on the way to Maestro.
    cm = _model(tmp_path)
    cols = generate_pattern_columns(
        cm, "VCO", "Beacon_PVT_45",
        [("proc_s", ("TT", "SS", "FF", "FS", "SF")),
         ("volt", ("NV", "HV", "LV")),
         ("temp_s", ("NT", "LT", "HT"))],
    )
    for c in cols:
        cm = add_column(cm, c)
    union = materialize(cm)
    gen = [r for r in union.rows if r.row_name.startswith("Beacon_PVT_45")]
    assert len(gen) == 1
    # voltage stayed a multi-valued sweep inside the single corner
    assert gen[0].vars["vdd"] == ("0.80", "0.85", "0.75")
    assert "vdd" in gen[0].sweep_var_keys


def test_composite_pattern_materialises_to_one_row_per_column(tmp_path):
    # VCO: Process + Temperature composite -> 15 columns -> 15 corners;
    # Voltage (simple) stays a 3-way sweep inside each.
    cm = _model(tmp_path)
    cols = generate_pattern_columns(
        cm, "VCO", "VCO_PVT",
        [("proc_c", ("TT", "SS", "FF", "FS", "SF")),
         ("volt", ("NV", "HV", "LV")),
         ("temp_c", ("NT", "LT", "HT"))],
    )
    for c in cols:
        cm = add_column(cm, c)
    union = materialize(cm)
    gen = [r for r in union.rows if r.row_name.startswith("VCO_PVT")]
    assert len(gen) == 15
    for row in gen:
        assert row.vars["vdd"] == ("0.80", "0.85", "0.75")
        assert "vdd" in row.sweep_var_keys


# --- validation --------------------------------------------------------------

def test_unknown_mode_raises(tmp_path):
    cm = _model(tmp_path)
    with pytest.raises(CornerModelError):
        generate_pattern_columns(cm, "NOPE", "P", [("volt", ("NV",))])


def test_unknown_axis_raises(tmp_path):
    cm = _model(tmp_path)
    with pytest.raises(CornerModelError):
        generate_pattern_columns(cm, "VCO", "P", [("ghost", ("NV",))])


def test_unknown_level_raises(tmp_path):
    cm = _model(tmp_path)
    with pytest.raises(CornerModelError):
        generate_pattern_columns(cm, "VCO", "P", [("volt", ("ZZ",))])


def test_bad_pattern_name_raises(tmp_path):
    cm = _model(tmp_path)
    with pytest.raises(CornerModelError):
        generate_pattern_columns(cm, "VCO", "9bad", [("volt", ("NV",))])
