"""Tests for Phase 5 Stage 3 — variants + three-layer override fallback."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from simkit.corner_model import (
    Column,
    CornerModelValidationError,
    Variant,
    add_variant,
    effective_name,
    is_cell_red,
    load_cornermodel,
    materialize_column,
    set_column_override,
    set_mode_var,
    set_variant_var,
    to_dict,
)


def _base() -> dict:
    return {
        "cornermodel_schema_version": 1,
        "name": "lo_corners",
        "project": "1AXX",
        "testbench_id": "sim_yusheng/Test/maestro",
        "modes": {
            "BT_2G_RX": {"vars": {
                "d_en_dummy": "1", "d_div12_en": "1", "d_other": "5",
            }},
        },
        "variants": {
            "BT_2G_RX_PN": {
                "base_mode": "BT_2G_RX",
                "vars": {"d_div12_en": "0"},
            }
        },
        "columns": [
            {"mode": "BT_2G_RX", "variant": "BT_2G_RX_PN",
             "pvt_label": "seed", "enabled": True,
             "pvt_vars": {"temperature": "55"}},
        ],
    }


def _write_load(tmp_path: Path, data: dict):
    p = tmp_path / f"{data['name']}.cornermodel.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return load_cornermodel(p)


# --- loading -------------------------------------------------------------


def test_load_with_variant(tmp_path):
    cm = _write_load(tmp_path, _base())
    assert "BT_2G_RX_PN" in cm.variants
    assert cm.variants["BT_2G_RX_PN"].base_mode == "BT_2G_RX"
    assert cm.columns[0].variant == "BT_2G_RX_PN"


def test_variant_var_must_be_base_register(tmp_path):
    d = _base()
    d["variants"]["BT_2G_RX_PN"]["vars"]["nonexist"] = "0"
    with pytest.raises(CornerModelValidationError):
        _write_load(tmp_path, d)


def test_variant_unknown_base_mode_rejected(tmp_path):
    d = _base()
    d["variants"]["BT_2G_RX_PN"]["base_mode"] = "NOPE"
    with pytest.raises(CornerModelValidationError):
        _write_load(tmp_path, d)


def test_column_variant_base_mode_must_match(tmp_path):
    d = _base()
    d["modes"]["OTHER"] = {"vars": {"x": "1"}}
    d["columns"][0]["mode"] = "OTHER"   # mismatch with variant base_mode
    with pytest.raises(CornerModelValidationError):
        _write_load(tmp_path, d)


def test_effective_name_uses_variant_root(tmp_path):
    cm = _write_load(tmp_path, _base())
    assert effective_name(cm.columns[0]) == "BT_2G_RX_PN_seed"


# --- three-layer fallback ------------------------------------------------


def test_materialize_three_layer_fallback(tmp_path):
    cm = _write_load(tmp_path, _base())
    row = materialize_column(cm, cm.columns[0])
    assert row.vars["d_div12_en"] == ("0",)   # variant overrides base "1"
    assert row.vars["d_en_dummy"] == ("1",)   # uncovered -> base
    assert row.vars["d_other"] == ("5",)


def test_d2_uncovered_var_inherits_base_change(tmp_path):
    cm = _write_load(tmp_path, _base())
    cm2 = set_mode_var(cm, "BT_2G_RX", "d_en_dummy", "9")
    row = materialize_column(cm2, cm2.columns[0])
    assert row.vars["d_en_dummy"] == ("9",)   # inherited the base change


def test_d2_covered_var_pinned_against_base_change(tmp_path):
    cm = _write_load(tmp_path, _base())
    cm2 = set_mode_var(cm, "BT_2G_RX", "d_div12_en", "7")
    row = materialize_column(cm2, cm2.columns[0])
    assert row.vars["d_div12_en"] == ("0",)   # variant absolute value pinned


def test_manual_override_beats_variant(tmp_path):
    cm = _write_load(tmp_path, _base())
    cm2 = set_column_override(cm, 0, "d_div12_en", "3")
    row = materialize_column(cm2, cm2.columns[0])
    assert row.vars["d_div12_en"] == ("3",)   # 手改 > 变体


# --- D1 red flag against the variant layer -------------------------------


def test_is_cell_red_against_variant_value(tmp_path):
    cm = _write_load(tmp_path, _base())
    # override d_div12_en to "1" — diverges from variant value "0" -> red
    cm = set_column_override(cm, 0, "d_div12_en", "1")
    assert is_cell_red(cm, cm.columns[0], "d_div12_en") is True
    # override d_en_dummy to "1" — equals base "1", variant doesn't cover it
    cm = set_column_override(cm, 0, "d_en_dummy", "1")
    assert is_cell_red(cm, cm.columns[0], "d_en_dummy") is False


# --- operations ----------------------------------------------------------


def test_add_variant(tmp_path):
    cm = _write_load(tmp_path, _base())
    cm2 = add_variant(cm, Variant(
        name="BT_2G_RX_LP", base_mode="BT_2G_RX", vars={"d_other": "0"}
    ))
    assert "BT_2G_RX_LP" in cm2.variants


def test_add_variant_rejects_nonbase_var(tmp_path):
    cm = _write_load(tmp_path, _base())
    with pytest.raises(CornerModelValidationError):
        add_variant(cm, Variant(
            name="BT_2G_RX_LP", base_mode="BT_2G_RX", vars={"ghost": "0"}
        ))


def test_set_variant_var_global_edit(tmp_path):
    cm = _write_load(tmp_path, _base())
    cm2 = set_variant_var(cm, "BT_2G_RX_PN", "d_div12_en", "2")
    row = materialize_column(cm2, cm2.columns[0])
    assert row.vars["d_div12_en"] == ("2",)


def test_rename_mode_cascades(tmp_path):
    from simkit.corner_model import rename_mode
    cm = _write_load(tmp_path, _base())
    cm2 = rename_mode(cm, "BT_2G_RX", "BT_2G_RX2")
    assert "BT_2G_RX2" in cm2.modes and "BT_2G_RX" not in cm2.modes
    assert cm2.variants["BT_2G_RX_PN"].base_mode == "BT_2G_RX2"
    assert cm2.columns[0].mode == "BT_2G_RX2"


def test_remove_mode_cascades(tmp_path):
    from simkit.corner_model import remove_mode
    cm = _write_load(tmp_path, _base())
    cm2 = remove_mode(cm, "BT_2G_RX")
    assert "BT_2G_RX" not in cm2.modes
    assert cm2.columns == ()                    # the mode's column went
    assert "BT_2G_RX_PN" not in cm2.variants    # variant based on it went


def test_to_dict_round_trip_stage3(tmp_path):
    cm = _write_load(tmp_path, _base())
    out = tmp_path / "lo_corners.cornermodel.json"
    out.write_text(json.dumps(to_dict(cm)), encoding="utf-8")
    assert to_dict(load_cornermodel(out)) == to_dict(cm)
