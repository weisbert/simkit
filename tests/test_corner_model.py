"""Tests for simkit.corner_model — Phase 5 Stage 1 corner-manager model.

The reconciliation test (M1 mandate) feeds a real Maestro-pulled union shape
from tests/fixtures/unions/fnxsession0_baseline.union.json — a genuine live
capture — rather than a hand-authored dict.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from simkit import union
from simkit.corner_model import (
    CornerModel,
    CornerModelSchemaVersionError,
    CornerModelValidationError,
    Mode,
    adopt_column,
    apply_pull,
    classify_pull,
    column_models,
    effective_name,
    is_cell_red,
    load_cornermodel,
    make_unmanaged_column,
    materialize,
    materialize_column,
    save_cornermodel,
    set_column_model_section,
    set_mode_var,
    set_var_order,
    to_dict,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_LIVE_UNION = (
    _REPO_ROOT / "tests" / "fixtures" / "unions"
    / "fnxsession0_baseline.union.json"
)


def _base() -> dict:
    return {
        "cornermodel_schema_version": 1,
        "name": "lo_corners",
        "project": "1AXX",
        "testbench_id": "sim_yusheng/Test/maestro",
        "modes": {
            "BT_2G_RX": {"vars": {"d_en_dummy": "1", "div_sel": "2"}},
        },
        "columns": [
            {
                "mode": "BT_2G_RX", "pvt_label": "TT", "enabled": True,
                "pvt_vars": {"temperature": "55", "VDD": "0.9"},
                "models": [{"file": "rf018.scs", "section": "tt"}],
            },
            {
                "mode": "BT_2G_RX", "pvt_label": "SS_1", "enabled": True,
                "pvt_vars": {"temperature": "125", "VDD": "0.85"},
                "overrides": {"d_en_dummy": "0"},
                "models": [{"file": "rf018.scs", "section": "ss"}],
            },
        ],
    }


def _write_load(tmp_path: Path, data: dict) -> CornerModel:
    p = tmp_path / f"{data['name']}.cornermodel.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return load_cornermodel(p)


# --- loading -------------------------------------------------------------


def test_load_valid(tmp_path):
    cm = _write_load(tmp_path, _base())
    assert cm.name == "lo_corners"
    assert set(cm.modes) == {"BT_2G_RX"}
    assert len(cm.columns) == 2


def test_bad_schema_version(tmp_path):
    d = _base()
    d["cornermodel_schema_version"] = 99
    with pytest.raises(CornerModelSchemaVersionError):
        _write_load(tmp_path, d)


def test_name_must_match_basename(tmp_path):
    d = _base()
    p = tmp_path / "other.cornermodel.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(CornerModelValidationError):
        load_cornermodel(p)


def test_project_mismatch(tmp_path):
    p = tmp_path / "lo_corners.cornermodel.json"
    p.write_text(json.dumps(_base()), encoding="utf-8")
    with pytest.raises(CornerModelValidationError):
        load_cornermodel(p, expected_project="WRONG")


def test_override_must_be_mode_var(tmp_path):
    d = _base()
    d["columns"][1]["overrides"] = {"not_a_mode_var": "5"}
    with pytest.raises(CornerModelValidationError):
        _write_load(tmp_path, d)


def test_pvt_var_collides_with_mode_var(tmp_path):
    d = _base()
    d["columns"][0]["pvt_vars"]["div_sel"] = "7"
    with pytest.raises(CornerModelValidationError):
        _write_load(tmp_path, d)


def test_managed_column_rejects_explicit_name(tmp_path):
    d = _base()
    d["columns"][0]["name"] = "Foo"
    with pytest.raises(CornerModelValidationError):
        _write_load(tmp_path, d)


def test_unmanaged_column_rejects_overrides(tmp_path):
    d = _base()
    d["columns"].append(
        {"mode": None, "name": "Foreign", "enabled": True,
         "overrides": {"x": "1"}, "pvt_vars": {"temperature": "55"}}
    )
    with pytest.raises(CornerModelValidationError):
        _write_load(tmp_path, d)


def test_empty_unmanaged_column_rejected(tmp_path):
    d = _base()
    d["columns"].append({"mode": None, "name": "Empty", "enabled": True})
    with pytest.raises(CornerModelValidationError):
        _write_load(tmp_path, d)


def test_duplicate_effective_name_rejected(tmp_path):
    d = _base()
    d["columns"].append(dict(d["columns"][0]))  # same mode + pvt_label
    with pytest.raises(CornerModelValidationError):
        _write_load(tmp_path, d)


def test_mode_var_must_be_scalar(tmp_path):
    d = _base()
    d["modes"]["BT_2G_RX"]["vars"]["d_en_dummy"] = ["1", "0"]
    with pytest.raises(CornerModelValidationError):
        _write_load(tmp_path, d)


# --- effective name ------------------------------------------------------


def test_effective_name_derived_and_alias(tmp_path):
    d = _base()
    d["columns"][0]["alias"] = "Golden_TT"
    cm = _write_load(tmp_path, d)
    assert effective_name(cm.columns[0]) == "Golden_TT"
    assert effective_name(cm.columns[1]) == "BT_2G_RX_SS_1"


# --- materialize ---------------------------------------------------------


def test_materialize_merges_mode_and_overrides(tmp_path):
    cm = _write_load(tmp_path, _base())
    tt = materialize_column(cm, cm.columns[0])
    ss = materialize_column(cm, cm.columns[1])
    assert tt.row_name == "BT_2G_RX_TT"
    assert tt.vars["d_en_dummy"] == ("1",)       # mode base
    assert tt.vars["div_sel"] == ("2",)
    assert tt.vars["temperature"] == ("55",)
    assert ss.vars["d_en_dummy"] == ("0",)       # override wins
    assert ss.vars["div_sel"] == ("2",)          # mode base inherited


def test_materialize_explodes_via_phase2(tmp_path):
    d = _base()
    d["columns"][0]["pvt_vars"]["VDD"] = ["2.8", "3.0"]  # a sweep
    cm = _write_load(tmp_path, d)
    u = materialize(cm)
    subs = union.explode(u)
    tt_subs = [s.sub_corner_name for s in subs if s.row_name == "BT_2G_RX_TT"]
    assert tt_subs == ["BT_2G_RX_TT_0", "BT_2G_RX_TT_1"]


def test_managed_column_may_have_only_mode_vars(tmp_path):
    d = _base()
    d["columns"].append(
        {"mode": "BT_2G_RX", "pvt_label": "reg_only", "enabled": True}
    )
    cm = _write_load(tmp_path, d)
    row = materialize_column(cm, cm.columns[2])
    assert set(row.vars) == {"d_en_dummy", "div_sel"}


# --- D1 red flag ---------------------------------------------------------


def test_is_cell_red(tmp_path):
    cm = _write_load(tmp_path, _base())
    assert is_cell_red(cm, cm.columns[1], "d_en_dummy") is True   # 0 != 1
    assert is_cell_red(cm, cm.columns[0], "d_en_dummy") is False  # no override


# --- global edit ---------------------------------------------------------


def test_set_mode_var_syncs_unoverridden_columns(tmp_path):
    cm = _write_load(tmp_path, _base())
    cm2 = set_mode_var(cm, "BT_2G_RX", "d_en_dummy", "0")
    tt = materialize_column(cm2, cm2.columns[0])
    assert tt.vars["d_en_dummy"] == ("0",)                 # synced
    # SS_1 keeps its override "0"; now equal to base -> no longer red.
    assert is_cell_red(cm2, cm2.columns[1], "d_en_dummy") is False


def test_set_mode_var_keeps_diverging_override_red(tmp_path):
    cm = _write_load(tmp_path, _base())
    cm2 = set_mode_var(cm, "BT_2G_RX", "d_en_dummy", "9")
    assert is_cell_red(cm2, cm2.columns[1], "d_en_dummy") is True  # 0 != 9


# --- reconciliation (M1: live-shape pull) --------------------------------


def test_classify_pull_against_live_union(tmp_path):
    d = _base()
    # An unmanaged column named to match the live fixture's "TT" row.
    d["columns"].append(
        {"mode": None, "name": "TT", "enabled": True,
         "pvt_vars": {"temperature": "55"},
         "models": [{"file": "rf018.scs", "section": "tt"}]}
    )
    cm = _write_load(tmp_path, d)
    pulled = union.load_union(_LIVE_UNION)

    result = classify_pull(cm, pulled)
    assert "TT" in result.matched
    assert result.matched["TT"] == []                      # vars identical
    foreign_names = {r.row_name for r in result.foreign}
    assert foreign_names == {"TT_pvt", "TT_2p5G"}           # not in cornermodel
    assert "BT_2G_RX_TT" in result.missing                  # ours, not pulled


def test_apply_pull_mirrors_maestro_order(tmp_path):
    """Mirror semantics: the result's columns appear in Maestro's pulled
    order (the matched corner is re-positioned, not left in place)."""
    d = _base()
    d["modes"] = {}
    d["columns"] = [
        {"mode": None, "name": "TT_pvt", "enabled": True,
         "pvt_vars": {"temperature": "55"},
         "models": [{"file": "rf018.scs", "section": "tt"}]},
    ]
    cm = _write_load(tmp_path, d)
    pulled = union.load_union(_LIVE_UNION)

    result = classify_pull(cm, pulled)
    merged = apply_pull(cm, pulled, result)

    # Column order tracks pulled-row order — not local order.
    assert [c.name for c in merged.columns] == ["TT", "TT_pvt", "TT_2p5G"]
    by_name = {c.name: c for c in merged.columns}
    # The matched corner re-synced — Maestro's VDD sweep is now on it.
    assert by_name["TT_pvt"].pvt_vars["VDD"] == ("3", "2.8")
    assert "VDD" in by_name["TT_pvt"].pvt_sweep_keys
    # Variable-row order also follows Maestro.
    assert merged.var_order == ("temperature", "VDD", "flo")


def test_apply_pull_drops_local_only_corners(tmp_path):
    """Mirror semantics: simkit corners that do NOT appear in the pull are
    DROPPED. The caller (GUI) snapshots + confirms before invoking this
    (2026 UX: "回到 Cadence 当前状态")."""
    cm = _write_load(tmp_path, _base())   # 2 managed columns on BT_2G_RX
    pulled = union.load_union(_LIVE_UNION)
    result = classify_pull(cm, pulled)
    # The managed columns' effective names don't appear in the pulled union,
    # so they are missing and will be dropped.
    assert set(result.missing) == {"BT_2G_RX_TT", "BT_2G_RX_SS_1"}
    merged = apply_pull(cm, pulled, result)
    assert {c.name for c in merged.columns} == {"TT", "TT_pvt", "TT_2p5G"}
    assert all(not c.is_managed for c in merged.columns)
    # The mode itself survives (only columns are dropped).
    assert "BT_2G_RX" in merged.modes


def test_apply_pull_resyncs_managed_register_via_override(tmp_path):
    """When a managed column's effective name matches a pulled row, the
    pulled register values land as column overrides (not new modes)."""
    cm = _write_load(tmp_path, {
        "cornermodel_schema_version": 1, "name": "lo_corners",
        "project": "1AXX", "testbench_id": "sim_yusheng/Test/maestro",
        "modes": {"BT_2G_RX": {"vars": {"d_en_dummy": "1"}}},
        "columns": [
            {"mode": "BT_2G_RX", "pvt_label": "TT", "enabled": True,
             "pvt_vars": {"temperature": "27"},
             "models": [{"file": "rf018.scs", "section": "tt"}]},
        ],
    })
    pulled = union.load_union(_LIVE_UNION)   # has a TT row with temperature=55
    result = classify_pull(cm, pulled)
    merged = apply_pull(cm, pulled, result)
    # BT_2G_RX_TT did not match — only the unmanaged TT/TT_pvt/TT_2p5G rows
    # come through. The mode stays, the managed column is dropped.
    assert [c.name for c in merged.columns] == ["TT", "TT_pvt", "TT_2p5G"]
    assert "BT_2G_RX" in merged.modes


def test_materialize_orders_row_vars_by_var_order(tmp_path):
    cm = _write_load(tmp_path, _base())
    order = ("VDD", "div_sel", "temperature", "d_en_dummy")
    cm = set_var_order(cm, order)
    u = materialize(cm)
    for row in u.rows:
        expected = [v for v in order if v in row.vars]
        assert list(row.vars) == expected     # Push carries the row order


def test_make_unmanaged_column_from_foreign(tmp_path):
    pulled = union.load_union(_LIVE_UNION)
    foreign = next(r for r in pulled.rows if r.row_name == "TT_pvt")
    col = make_unmanaged_column(foreign)
    assert col.is_managed is False
    assert col.name == "TT_pvt"
    assert col.pvt_vars["VDD"] == ("3", "2.8")
    assert "VDD" in col.pvt_sweep_keys


def test_make_unmanaged_column_carries_tests():
    col = make_unmanaged_column(union.UnionRow(
        row_name="Foreign", vars={"temperature": ("55",)}, models=(),
        tests=("Test", "Test_trans"),
    ))
    assert col.tests == ("Test", "Test_trans")


def test_materialize_carries_column_tests(tmp_path):
    cm = _write_load(tmp_path, _base())
    scoped = replace(cm.columns[0], tests=("Test",))
    cm2 = replace(cm, columns=(scoped,) + cm.columns[1:])
    u = materialize(cm2)
    assert u.rows[0].tests == ("Test",)
    assert u.rows[1].tests == ()


def test_union_var_order_merges_row_orderings():
    from simkit.corner_model import union_var_order
    u = union.Union(
        union_schema_version=1, name="n", project="p", testbench_id="t",
        rows=(
            union.UnionRow(row_name="A",
                           vars={"temperature": ("55",), "vdd": ("0.9",)},
                           models=()),
            union.UnionRow(row_name="B",
                           vars={"temperature": ("-40",), "gain": ("10",),
                                 "vdd": ("1.1",)},
                           models=()),
        ),
    )
    # row B places `gain` before `vdd`; the merge respects that.
    assert union_var_order(u) == ("temperature", "gain", "vdd")


def test_reclassify_mode_moves_vars_between_register_and_pvt():
    from simkit.corner_model import (
        Column, empty_cornermodel, add_mode, add_column, reclassify_mode,
    )
    m = empty_cornermodel("corners", "p", "tb")
    m = add_mode(m, "RX", {"d_en": "1", "div_sel": "2"})
    m = add_column(m, Column(
        mode="RX", enabled=True,
        pvt_vars={"temperature": ("55",), "gain": ("10",)},
        models=(), pvt_label="TT",
    ))
    # gain (PVT) -> register; div_sel (register) -> per-column PVT.
    m2 = reclassify_mode(m, "RX", {"d_en": "1", "gain": "10"})
    assert set(m2.modes["RX"].vars) == {"d_en", "gain"}
    col = m2.columns[0]
    assert col.pvt_vars["div_sel"] == ("2",)   # seeded with the old value
    assert "gain" not in col.pvt_vars


def test_set_column_test_enabled_toggles_scope():
    from simkit.corner_model import (
        Column, empty_cornermodel, add_mode, add_column,
        set_column_test_enabled,
    )
    m = empty_cornermodel("corners", "p", "tb")
    m = add_mode(m, "RX", {"d_en": "1"})
    m = add_column(m, Column(
        mode="RX", enabled=True, pvt_vars={"temperature": ("55",)},
        models=(), pvt_label="TT",
    ))
    m = replace(m, tests=("acdc", "tran"))
    # disabling acdc scopes the column to the remaining test.
    m2 = set_column_test_enabled(m, 0, "acdc", False)
    assert m2.columns[0].tests == ("tran",)
    # re-enabling collapses the scope back to empty (= all tests).
    m3 = set_column_test_enabled(m2, 0, "acdc", True)
    assert m3.columns[0].tests == ()


def test_reclassify_mode_allows_empty_register_set():
    # A mode with zero registers is a legitimate "PVT-only" configuration —
    # corners on it inherit every register value from the design file's
    # defaults at sim time (2026 UX item: reference corner may leave some
    # registers intentionally unset).
    from simkit.corner_model import (
        empty_cornermodel, add_mode, reclassify_mode,
    )
    m = add_mode(empty_cornermodel("c", "p", "tb"), "RX", {"d_en": "1"})
    m2 = reclassify_mode(m, "RX", {})
    assert m2.modes["RX"].vars == {}


def test_adopt_column_three_way_split():
    mode = Mode(name="BT_2G_RX", vars={"d_en_dummy": "1", "div_sel": "2"})
    col = make_unmanaged_column(
        union.UnionRow(
            row_name="Foreign",
            vars={"d_en_dummy": ("1",), "div_sel": ("9",),
                  "temperature": ("55",)},
            models=(),
        )
    )
    adopted, split = adopt_column(col, mode, "adopted")
    assert split.inherited == ("d_en_dummy",)        # 1 == 1
    assert split.overrides == {"div_sel": "9"}       # 9 != 2
    assert split.pvt_vars_kept == ("temperature",)
    assert adopted.mode == "BT_2G_RX"
    assert effective_name(adopted) == "BT_2G_RX_adopted"


# --- process-model section edit ------------------------------------------


def test_set_column_model_section_retargets_one_column(tmp_path):
    cm = _write_load(tmp_path, _base())
    edited = set_column_model_section(cm, 0, "rf018.scs", "ff")
    assert column_models(edited.columns[0])[0].section == ("ff",)
    # the sibling column keeps its own section
    assert column_models(edited.columns[1])[0].section == ("ss",)


def test_set_column_model_section_unknown_file_raises(tmp_path):
    cm = _write_load(tmp_path, _base())
    with pytest.raises(CornerModelValidationError):
        set_column_model_section(cm, 0, "nonexistent.scs", "ff")


# --- New Mode from a column ----------------------------------------------


def _unmanaged_cm(tmp_path: Path) -> CornerModel:
    return _write_load(tmp_path, {
        "cornermodel_schema_version": 1, "name": "lo_corners",
        "project": "1AXX", "testbench_id": "sim_yusheng/Test/maestro",
        "modes": {},
        "columns": [
            {"mode": None, "name": "RX_TT", "enabled": True,
             "pvt_vars": {"d_en": "1", "div_sel": "2", "temperature": "55"},
             "models": [{"file": "rf018.scs", "section": "tt"}]},
        ],
    })


def test_add_mode_with_empty_register_value(tmp_path):
    # A register with an empty-string value means "intentionally unset" —
    # the mode declares the var as one of its registers, but the design
    # file's default applies at sim time (no axlPutVar). The user can fill
    # in the value later from the modes panel (2026 UX).
    from simkit.corner_model import add_mode, empty_cornermodel
    m = empty_cornermodel("c", "p", "tb")
    m = add_mode(m, "RX", {"d_en": "1", "d_div": ""})
    assert m.modes["RX"].vars == {"d_en": "1", "d_div": ""}


def test_managed_column_skips_empty_register_at_materialization(tmp_path):
    # An empty register value is NOT emitted into the materialised row, so
    # downstream axlPutVar is not called for it (= design default applies).
    from simkit.corner_model import (
        add_column, add_mode, empty_cornermodel, materialize, Column,
    )
    m = empty_cornermodel("c", "p", "tb")
    m = add_mode(m, "RX", {"d_en": "1", "d_div": ""})
    m = add_column(m, Column(
        mode="RX", enabled=True, pvt_vars={"temperature": ("27",)},
        models=(), pvt_label="TT", pvt_sweep_keys=frozenset(),
    ))
    u = materialize(m)
    row = u.rows[0]
    assert dict(row.vars) == {"d_en": ("1",), "temperature": ("27",)}
    assert "d_div" not in row.vars  # empty register is skipped


def test_column_override_can_fill_an_empty_register(tmp_path):
    # A column override on an empty register fills it at materialise time —
    # the override wins over the mode's empty base.
    from simkit.corner_model import (
        add_column, add_mode, empty_cornermodel, materialize,
        set_column_override, Column,
    )
    m = empty_cornermodel("c", "p", "tb")
    m = add_mode(m, "RX", {"d_div": ""})
    m = add_column(m, Column(
        mode="RX", enabled=True, pvt_vars={"temperature": ("27",)},
        models=(), pvt_label="TT", pvt_sweep_keys=frozenset(),
    ))
    m = set_column_override(m, 0, "d_div", "8")
    u = materialize(m)
    assert dict(u.rows[0].vars) == {"d_div": ("8",), "temperature": ("27",)}


# --- serialisation round-trip --------------------------------------------


def test_to_dict_save_round_trip(tmp_path):
    cm = _write_load(tmp_path, _base())
    out = tmp_path / "lo_corners.cornermodel.json"
    save_cornermodel(cm, out)
    reloaded = load_cornermodel(out)
    assert to_dict(reloaded) == to_dict(cm)
