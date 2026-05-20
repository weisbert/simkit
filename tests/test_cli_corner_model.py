"""Tests for the ``pvt corner-model`` CLI (explode / build) — Phase 5 Stage 1."""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.cli.__main__ import main as cli_main  # noqa: E402
from simkit.union import load_union  # noqa: E402


def _run(*args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = cli_main(list(args))
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 1
    return rc, out.getvalue(), err.getvalue()


def _write_cm(tmp_path: Path) -> Path:
    data = {
        "cornermodel_schema_version": 1,
        "name": "lo_corners",
        "project": "1AXX",
        "testbench_id": "sim_yusheng/Test/maestro",
        "modes": {"BT_2G_RX": {"vars": {"d_en_dummy": "1"}}},
        "columns": [
            {"mode": "BT_2G_RX", "pvt_label": "TT", "enabled": True,
             "pvt_vars": {"VDD": ["2.8", "3.0"]},
             "models": [{"file": "rf018.scs", "section": "tt"}]},
        ],
    }
    p = tmp_path / "lo_corners.cornermodel.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_explode_prints_sub_corners(tmp_path):
    cm = _write_cm(tmp_path)
    rc, out, err = _run("corner-model", "explode", str(cm))
    assert rc == 0, err
    assert "BT_2G_RX_TT_0" in out
    assert "BT_2G_RX_TT_1" in out


def test_explode_json(tmp_path):
    cm = _write_cm(tmp_path)
    rc, out, _ = _run("corner-model", "explode", str(cm), "--json")
    assert rc == 0
    subs = json.loads(out)
    assert {s["sub_corner_name"] for s in subs} == {
        "BT_2G_RX_TT_0", "BT_2G_RX_TT_1"
    }


def test_build_writes_loadable_union(tmp_path):
    cm = _write_cm(tmp_path)
    rc, out, err = _run("corner-model", "build", str(cm))
    assert rc == 0, err
    union_path = tmp_path / "lo_corners.union.json"
    assert union_path.is_file()
    u = load_union(union_path)               # round-trips Phase 2 loader
    assert u.rows[0].row_name == "BT_2G_RX_TT"
    assert u.rows[0].vars["d_en_dummy"] == ("1",)
    assert "VDD" in u.rows[0].sweep_var_keys


def test_explode_bad_file(tmp_path):
    bad = tmp_path / "broken.cornermodel.json"
    bad.write_text("{not json", encoding="utf-8")
    rc, _, err = _run("corner-model", "explode", str(bad))
    assert rc == 2
    assert "corner-model explode" in err


def test_explode_with_profile_resolves_axis_levels(tmp_path):
    prof = tmp_path / "rf018.pvtprofile.json"
    prof.write_text(json.dumps({
        "pvtprofile_schema_version": 1, "name": "rf018", "project": "1AXX",
        "axes": {"voltage": {"levels": {
            "nominal": {"vars": {"LDO_VSET": "20"}}}}},
    }), encoding="utf-8")
    cm = tmp_path / "lo_corners.cornermodel.json"
    cm.write_text(json.dumps({
        "cornermodel_schema_version": 1, "name": "lo_corners",
        "project": "1AXX", "testbench_id": "a/b/c", "pvt_profile": "rf018",
        "modes": {"M": {"vars": {"d_en": "1"}}},
        "columns": [{"mode": "M", "pvt_label": "TT", "enabled": True,
                     "axis_levels": {"voltage": "nominal"}}],
    }), encoding="utf-8")
    rc, out, err = _run(
        "corner-model", "explode", str(cm), "--profile", str(prof)
    )
    assert rc == 0, err
    assert "LDO_VSET=20" in out          # axis_levels resolved via profile
