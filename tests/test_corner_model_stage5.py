"""Tests for Phase 5 Stage 5 — var order, soft validation."""

from __future__ import annotations

import json
from pathlib import Path

from simkit.corner_model import (
    check_cornermodel,
    load_cornermodel,
    ordered_var_rows,
    set_var_order,
)


def _base() -> dict:
    return {
        "cornermodel_schema_version": 1,
        "name": "lo_corners",
        "project": "1AXX",
        "testbench_id": "sim_yusheng/Test/maestro",
        "modes": {"M": {"vars": {"d_en": "1"}}},
        "correlated_axes": {
            "temp_s5p": {
                "members": ["temperature", "s5p"],
                "tuples": [
                    {"label": "t55", "values": {
                        "temperature": "55", "s5p": "L_55.s5p"}},
                ],
            }
        },
        "run_sets": {"S": {"columns": ["M_seed", "Ghost"]}},
        "var_order": ["temperature", "d_en"],
        "columns": [
            {"mode": "M", "pvt_label": "seed", "enabled": True,
             "pvt_vars": {"temperature": "55"}},
        ],
    }


def _write_load(tmp_path: Path, data: dict):
    p = tmp_path / f"{data['name']}.cornermodel.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return load_cornermodel(p)


# --- var order -----------------------------------------------------------


def test_load_var_order(tmp_path):
    cm = _write_load(tmp_path, _base())
    assert cm.var_order == ("temperature", "d_en")


def test_ordered_var_rows_honours_var_order(tmp_path):
    cm = _write_load(tmp_path, _base())
    rows = ordered_var_rows(cm, {"d_en", "temperature", "extra"})
    # var_order entries first, then default (register d_en... wait it's listed)
    assert rows[0] == "temperature"
    assert rows[1] == "d_en"
    assert rows[2] == "extra"


def test_set_var_order(tmp_path):
    cm = _write_load(tmp_path, _base())
    cm2 = set_var_order(cm, ("d_en", "temperature"))
    assert cm2.var_order == ("d_en", "temperature")


# --- soft validation -----------------------------------------------------


def test_check_flags_missing_s5p(tmp_path):
    cm = _write_load(tmp_path, _base())
    issues = check_cornermodel(cm, base_dir=tmp_path)
    codes = {i.code for i in issues}
    assert "missing_file" in codes        # L_55.s5p does not exist


def test_check_flags_dangling_run_set_column(tmp_path):
    cm = _write_load(tmp_path, _base())
    issues = check_cornermodel(cm, base_dir=tmp_path)
    dangling = [i for i in issues if i.code == "dangling_column"]
    assert any("Ghost" in i.message for i in dangling)


def test_check_clean_when_file_present(tmp_path):
    (tmp_path / "L_55.s5p").write_text("", encoding="utf-8")
    d = _base()
    d["run_sets"]["S"]["columns"] = ["M_seed"]   # drop the ghost
    cm = _write_load(tmp_path, d)
    assert check_cornermodel(cm, base_dir=tmp_path) == []
