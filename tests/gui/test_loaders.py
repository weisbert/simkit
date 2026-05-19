"""Tests for :mod:`simkit.gui.loaders` (Phase 4 Stage 3).

Pure-Python; no Qt. Builds a fake `.pvtproject` tree on a temp dir, walks
it with :func:`load_module`, and asserts the snapshot shape; also exercises
the union<->editor-rows adapters + the bundle-walker.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from simkit.db import bootstrap, connect
from simkit.gui.loaders import (
    LoadedHistoryRun,
    LoadedModule,
    LoadedReview,
    editor_rows_to_union_rows,
    load_bundle_for_editor,
    load_module,
    union_to_editor_rows,
)
from simkit.union import (
    ModelEntry,
    Union,
    UnionRow,
    UnionValidationError,
    load_union,
)


# --- Helpers -------------------------------------------------------------


def _write_pvtproject(
    tmp_path: Path,
    *,
    project: str = "demo",
    db_root: str | None = None,
) -> Path:
    """Lay down a .pvtproject file with default sidecar dirs."""
    db_root = db_root if db_root is not None else str(tmp_path)
    body = {
        "schema_version": 1,
        "project": project,
        "dbRoot": db_root,
    }
    p = tmp_path / ".pvtproject"
    p.write_text(json.dumps(body), encoding="utf-8")
    return p


def _write_review(
    project_root: Path, name: str, items: list[dict]
) -> Path:
    reviews_dir = project_root / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    p = reviews_dir / f"{name}.review.json"
    p.write_text(
        json.dumps(
            {
                "review_schema_version": 1,
                "name": name,
                "project": "demo",
                "items": items,
            }
        ),
        encoding="utf-8",
    )
    return p


def _seed_runs_db(db_path: Path, runs: list[dict]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = connect(db_path)
    try:
        bootstrap(con)
        for run in runs:
            con.execute(
                """
                INSERT INTO runs (
                  run_id, project_id, testbench_id, testbench_alias,
                  timestamp, author, label, note,
                  netlist_path, history_name, schema_version, ingested_at,
                  starred, milestone, partial_run
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    run["run_id"],
                    run.get("project_id", "demo"),
                    run.get("testbench_id", "LIB/cell/schematic"),
                    run.get("testbench_alias"),
                    run["timestamp"],
                    run.get("author", "tester"),
                    run.get("label"),
                    run.get("note"),
                    run.get("netlist_path"),
                    run.get("history_name", "pn_review_v1__1"),
                    1,
                    run.get("ingested_at", run["timestamp"]),
                    run.get("starred", False),
                    run.get("milestone"),
                    run.get("partial_run", False),
                ],
            )
    finally:
        con.close()


def _make_union(tmp_path: Path) -> Union:
    body = {
        "union_schema_version": 1,
        "name": "demo_pvt",
        "project": "demo",
        "testbench_id": "LIB/cell/schematic",
        "rows": [
            {
                "row_name": "TT_pvt",
                "vars": {
                    "process": "tt",
                    "temp": "27",
                    "vdd": "1.8",
                },
                "models": [
                    {
                        "file": "rf018.scs",
                        "block": "Global",
                        "test": "All",
                        "section": "tt",
                    }
                ],
            }
        ],
    }
    p = tmp_path / "demo_pvt.union.json"
    p.write_text(json.dumps(body), encoding="utf-8")
    return load_union(p)


# --- load_module --------------------------------------------------------


def test_load_module_empty_project(tmp_path):
    pvtproject = _write_pvtproject(tmp_path)
    module = load_module(pvtproject)
    assert isinstance(module, LoadedModule)
    assert module.project_name == "demo"
    assert module.project_path == pvtproject.resolve()
    assert module.project_root == tmp_path.resolve()
    assert module.reviews == ()
    assert module.history == ()
    assert module.milestones == ()
    assert module.union_default is None
    assert module.bundle_default is None


def test_load_module_returns_reviews_sorted(tmp_path):
    pvtproject = _write_pvtproject(tmp_path)
    _write_review(tmp_path, "z_review", [{"name": "a"}])
    _write_review(tmp_path, "a_review", [{"name": "x"}, {"name": "y"}])
    module = load_module(pvtproject)
    assert [r.review_name for r in module.reviews] == ["a_review", "z_review"]
    assert all(isinstance(r, LoadedReview) for r in module.reviews)
    assert module.reviews[0].item_count == 2
    assert module.reviews[1].item_count == 1


def test_load_module_reads_history_from_duckdb(tmp_path):
    pvtproject = _write_pvtproject(tmp_path)
    db = tmp_path / "simkit.duckdb"
    _seed_runs_db(
        db,
        [
            {
                "run_id": "aaaaaaaaaaaaaaaa-1111",
                "timestamp": "2026-05-10 09:00:00+00",
                "label": "before-fix",
                "starred": False,
                "milestone": None,
            },
            {
                "run_id": "bbbbbbbbbbbbbbbb-2222",
                "timestamp": "2026-05-12 09:00:00+00",
                "label": None,
                "starred": True,
                "milestone": "CDR",
            },
        ],
    )
    module = load_module(pvtproject)
    assert len(module.history) == 2
    # Ordered most-recent-first per loader contract.
    assert module.history[0].run_id.startswith("bbb")
    assert module.history[0].starred is True
    assert module.history[0].milestone == "CDR"
    assert module.history[0].short_id == "bbbbbbbb"
    assert module.history[1].label == "before-fix"
    assert module.history[1].milestone is None
    assert module.milestones == ("CDR",)


def test_load_module_handles_missing_db(tmp_path):
    pvtproject = _write_pvtproject(tmp_path)
    # No simkit.duckdb on disk → history must be empty (not raise).
    module = load_module(pvtproject)
    assert module.history == ()
    assert module.milestones == ()


def test_load_module_single_default_union_and_bundle(tmp_path):
    pvtproject = _write_pvtproject(tmp_path)
    unions = tmp_path / "unions"
    unions.mkdir()
    u = unions / "lone.union.json"
    u.write_text("{}", encoding="utf-8")
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    b = bundles / "lone.measure.json"
    b.write_text("{}", encoding="utf-8")

    module = load_module(pvtproject)
    assert module.union_default == u.resolve()
    assert module.bundle_default == b.resolve()


def test_load_module_ambiguous_default_is_none(tmp_path):
    pvtproject = _write_pvtproject(tmp_path)
    unions = tmp_path / "unions"
    unions.mkdir()
    (unions / "a.union.json").write_text("{}", encoding="utf-8")
    (unions / "b.union.json").write_text("{}", encoding="utf-8")
    module = load_module(pvtproject)
    assert module.union_default is None


# --- union_to_editor_rows -----------------------------------------------


def test_union_to_editor_rows_basic_named_columns(tmp_path):
    union = _make_union(tmp_path)
    rows = union_to_editor_rows(union)
    assert len(rows) == 1
    row = rows[0]
    assert row["row_name"] == "TT_pvt"
    assert row["process"] == "tt"
    assert row["temperature"] == "27"
    assert row["vdd"] == "1.8"
    assert row["model_file"] == "rf018.scs"
    assert row["_enabled"] is True


def test_union_to_editor_rows_packs_unknown_into_extra_vars(tmp_path):
    body = {
        "union_schema_version": 1,
        "name": "demo_x",
        "project": "demo",
        "testbench_id": "LIB/cell/schematic",
        "rows": [
            {
                "row_name": "TT",
                "vars": {
                    "process": "tt",
                    "rload": "10k",
                    "cload": ["1p", "2p", "5p"],
                },
                "models": [],
            }
        ],
    }
    p = tmp_path / "demo_x.union.json"
    p.write_text(json.dumps(body), encoding="utf-8")
    rows = union_to_editor_rows(load_union(p))
    extras = rows[0]["extra_vars"]
    # Either ordering is fine; assert that rload and cload sweeps both appear.
    assert "rload=10k" in extras
    assert "cload=1p,2p,5p" in extras


def test_union_to_editor_rows_respects_enabled_flag(tmp_path):
    body = {
        "union_schema_version": 1,
        "name": "demo_off",
        "project": "demo",
        "testbench_id": "LIB/cell/schematic",
        "rows": [
            {
                "row_name": "TT",
                "vars": {"process": "tt"},
                "models": [],
                "enabled": False,
            },
        ],
    }
    p = tmp_path / "demo_off.union.json"
    p.write_text(json.dumps(body), encoding="utf-8")
    rows = union_to_editor_rows(load_union(p))
    assert rows[0]["_enabled"] is False


# --- editor_rows_to_union_rows ------------------------------------------


def test_editor_rows_to_union_rows_single_axis_round_trip(tmp_path):
    union = _make_union(tmp_path)
    rows = union_to_editor_rows(union)
    rebuilt = editor_rows_to_union_rows(
        rows,
        name="demo_pvt",
        project="demo",
        testbench_id="LIB/cell/schematic",
    )
    assert isinstance(rebuilt, Union)
    assert len(rebuilt.rows) == 1
    rebuilt_row = rebuilt.rows[0]
    assert rebuilt_row.row_name == "TT_pvt"
    assert rebuilt_row.vars["process"] == ("tt",)
    assert rebuilt_row.vars["temp"] == ("27",)
    assert rebuilt_row.vars["vdd"] == ("1.8",)
    assert rebuilt_row.enabled is True
    assert len(rebuilt_row.models) == 1
    assert rebuilt_row.models[0].file == "rf018.scs"


def test_editor_rows_to_union_rows_rejects_empty():
    with pytest.raises(UnionValidationError):
        editor_rows_to_union_rows(
            [], name="x", project="x", testbench_id="x"
        )


def test_editor_rows_to_union_rows_rejects_missing_name():
    with pytest.raises(UnionValidationError):
        editor_rows_to_union_rows(
            [{"row_name": "", "process": "tt"}],
            name="x",
            project="x",
            testbench_id="x",
        )


def test_editor_rows_to_union_rows_rejects_duplicate_names():
    with pytest.raises(UnionValidationError):
        editor_rows_to_union_rows(
            [
                {"row_name": "A", "process": "tt"},
                {"row_name": "A", "process": "ss"},
            ],
            name="x",
            project="x",
            testbench_id="x",
        )


def test_editor_rows_to_union_rows_extra_vars_parsing():
    rows = [
        {
            "row_name": "TT",
            "process": "tt",
            "temperature": "",
            "vdd": "",
            "model_file": "",
            "extra_vars": "rload=10k; cload=1p,2p",
        }
    ]
    union = editor_rows_to_union_rows(
        rows,
        name="x",
        project="x",
        testbench_id="x",
    )
    row = union.rows[0]
    assert row.vars["rload"] == ("10k",)
    assert row.vars["cload"] == ("1p", "2p")


# --- load_bundle_for_editor ---------------------------------------------


def test_load_bundle_for_editor_returns_triple(tmp_path):
    pvtproject = _write_pvtproject(tmp_path)
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    tmpl_body = {
        "template_schema_version": 1,
        "name": "pn_at_freq",
        "short_alias": "PN",
        "expression": "value(VAR($OUT_NAME) $FREQ)",
        "params": [
            {"key": "OUT_NAME", "kind": "string"},
            {"key": "FREQ", "kind": "number", "default": "1000000"},
        ],
        "eval_type": "point",
    }
    (templates_dir / "pn_at_freq.template.json").write_text(
        json.dumps(tmpl_body), encoding="utf-8"
    )

    signal_groups_dir = tmp_path / "signal_groups"
    signal_groups_dir.mkdir()
    sg_body = {
        "signal_group_schema_version": 1,
        "name": "voltage_outs",
        "signals": ["/Vout"],
    }
    (signal_groups_dir / "voltage_outs.siggroup.json").write_text(
        json.dumps(sg_body), encoding="utf-8"
    )

    bundle_body = {
        "measure_schema_version": 2,
        "name": "demo_bundle",
        "project": "demo",
        "testbench_id": "LIB/cell/schematic",
        "test_name": "Test",
        "apply": [
            {
                "template": "pn_at_freq",
                "param_overrides": {"OUT_NAME": "pn"},
            }
        ],
    }
    bundle_path = tmp_path / "demo_bundle.measure.json"
    bundle_path.write_text(json.dumps(bundle_body), encoding="utf-8")

    raw, templates, signal_groups = load_bundle_for_editor(
        bundle_path, project_root=tmp_path
    )
    assert raw["name"] == "demo_bundle"
    assert "pn_at_freq" in templates
    assert "voltage_outs" in signal_groups
    assert templates["pn_at_freq"].name == "pn_at_freq"


def test_load_bundle_for_editor_missing_dirs_returns_empty_dicts(tmp_path):
    bundle_path = tmp_path / "x.measure.json"
    bundle_path.write_text(json.dumps({"apply": []}), encoding="utf-8")
    raw, templates, signal_groups = load_bundle_for_editor(
        bundle_path, project_root=tmp_path
    )
    assert templates == {}
    assert signal_groups == {}
