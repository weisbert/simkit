"""Tests for Phase 5 Stage 4 — run-sets + column filtering."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from simkit.corner_model import (
    CornerModelValidationError,
    add_run_set,
    apply_run_set,
    effective_name,
    load_cornermodel,
    run_set_membership,
    to_dict,
)


def _base() -> dict:
    return {
        "cornermodel_schema_version": 1,
        "name": "lo_corners",
        "project": "1AXX",
        "testbench_id": "sim_yusheng/Test/maestro",
        "modes": {
            "BT_2G_RX": {"vars": {"d_en": "1"}},
            "BT_2G_TX": {"vars": {"d_en": "1"}},
        },
        "run_sets": {
            "All_Mode_TT": {"columns": ["BT_2G_RX_TT", "BT_2G_TX_TT"]},
            "RX_only": {"columns": ["BT_2G_RX_TT"]},
        },
        "columns": [
            {"mode": "BT_2G_RX", "pvt_label": "TT", "enabled": True,
             "pvt_vars": {"temperature": "55"}},
            {"mode": "BT_2G_TX", "pvt_label": "TT", "enabled": True,
             "pvt_vars": {"temperature": "55"}},
        ],
    }


def _write_load(tmp_path: Path, data: dict):
    p = tmp_path / f"{data['name']}.cornermodel.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return load_cornermodel(p)


def test_load_with_run_sets(tmp_path):
    cm = _write_load(tmp_path, _base())
    assert set(cm.run_sets) == {"All_Mode_TT", "RX_only"}
    assert cm.run_sets["RX_only"].columns == ("BT_2G_RX_TT",)


def test_run_set_with_unknown_column_tolerated(tmp_path):
    d = _base()
    d["run_sets"]["RX_only"]["columns"].append("Ghost_col")
    cm = _write_load(tmp_path, d)              # forward-compat: loads fine
    assert "Ghost_col" in cm.run_sets["RX_only"].columns


def test_apply_run_set_sets_enabled_flags(tmp_path):
    cm = _write_load(tmp_path, _base())
    cm2 = apply_run_set(cm, "RX_only")
    by_name = {effective_name(c): c for c in cm2.columns}
    assert by_name["BT_2G_RX_TT"].enabled is True
    assert by_name["BT_2G_TX_TT"].enabled is False


def test_apply_run_set_all(tmp_path):
    cm = _write_load(tmp_path, _base())
    cm2 = apply_run_set(cm, "All_Mode_TT")
    assert all(c.enabled for c in cm2.columns)


def test_add_run_set(tmp_path):
    cm = _write_load(tmp_path, _base())
    cm2 = add_run_set(cm, "TX_only", ("BT_2G_TX_TT",))
    assert "TX_only" in cm2.run_sets


def test_add_duplicate_run_set_rejected(tmp_path):
    cm = _write_load(tmp_path, _base())
    with pytest.raises(CornerModelValidationError):
        add_run_set(cm, "RX_only", ())


def test_run_set_membership(tmp_path):
    cm = _write_load(tmp_path, _base())
    assert run_set_membership(cm, "RX_only") == {"BT_2G_RX_TT"}


def test_to_dict_round_trip_stage4(tmp_path):
    cm = _write_load(tmp_path, _base())
    out = tmp_path / "lo_corners.cornermodel.json"
    out.write_text(json.dumps(to_dict(cm)), encoding="utf-8")
    assert to_dict(load_cornermodel(out)) == to_dict(cm)
