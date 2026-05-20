"""Tests for simkit.history_mirror.mirror_maestro_history."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from simkit.db import bootstrap, connect
from simkit.history_mirror import mirror_maestro_history

_FIX = (
    Path(__file__).parent
    / "fixtures" / "runs" / "synthetic_minimal" / "run.json"
)


def _make_pvtproject(tmp_path: Path) -> tuple[Path, Path]:
    db_root = tmp_path / "db"
    db_root.mkdir()
    proj = tmp_path / ".pvtproject"
    proj.write_text(
        json.dumps(
            {"schema_version": 1, "project": "demo", "dbRoot": str(db_root)}
        ),
        encoding="utf-8",
    )
    return proj, db_root / "simkit.duckdb"


def _seed_history(db_path: Path, history_name: str) -> None:
    """Ingest one run.json so `history_name` is already present in `runs`."""
    con = connect(db_path)
    try:
        bootstrap(con)
        from simkit.ingest import ingest_run_json

        dump = json.loads(_FIX.read_text())
        dump["run"]["run_id"] = str(uuid.uuid4())
        dump["run"]["history_name"] = history_name
        p = db_path.parent / f"_seed_{history_name}.json"
        p.write_text(json.dumps(dump), encoding="utf-8")
        ingest_run_json(con, p)
    finally:
        con.close()


class _FakeBridge:
    """Stand-in for simkit.skill_bridge — no live Maestro."""

    def __init__(self, histories: list[str], *, fail: tuple[str, ...] = ()):
        self._histories = histories
        self._fail = set(fail)
        self.saved: list[str] = []

    def _open_workspace(self):
        return object()

    def pvt_runner_get_history_lock_map(self, *, session, workspace):
        return {h: False for h in self._histories}

    def pvt_save(self, name, *, pvtproject_path, session, label, workspace):
        assert label == name
        if name in self._fail:
            raise RuntimeError(f"collect failed for {name}")
        run_dir = Path(pvtproject_path).parent / f"_save_{name}"
        run_dir.mkdir(parents=True, exist_ok=True)
        dump = json.loads(_FIX.read_text())
        dump["run"]["run_id"] = str(uuid.uuid4())
        dump["run"]["history_name"] = name
        (run_dir / "run.json").write_text(json.dumps(dump), encoding="utf-8")
        self.saved.append(name)
        return str(run_dir)


def test_mirrors_all_new_histories(tmp_path):
    proj, db_path = _make_pvtproject(tmp_path)
    bridge = _FakeBridge(["Interactive.0", "Interactive.1"])
    out = mirror_maestro_history(
        pvtproject_path=proj, session="s0", bridge=bridge
    )
    assert sorted(out["mirrored"]) == ["Interactive.0", "Interactive.1"]
    assert out["skipped"] == []
    assert out["failed"] == []
    con = connect(db_path)
    try:
        names = {
            r[0] for r in con.execute(
                "SELECT history_name FROM runs"
            ).fetchall()
        }
    finally:
        con.close()
    assert names == {"Interactive.0", "Interactive.1"}


def test_skips_history_already_in_db(tmp_path):
    proj, db_path = _make_pvtproject(tmp_path)
    _seed_history(db_path, "Interactive.0")
    bridge = _FakeBridge(["Interactive.0", "Interactive.1"])
    out = mirror_maestro_history(
        pvtproject_path=proj, session="s0", bridge=bridge
    )
    assert out["mirrored"] == ["Interactive.1"]
    assert out["skipped"] == ["Interactive.0"]
    # The already-present history must NOT be re-collected.
    assert bridge.saved == ["Interactive.1"]


def test_one_failing_history_does_not_abort_the_sweep(tmp_path):
    proj, _ = _make_pvtproject(tmp_path)
    bridge = _FakeBridge(
        ["good_1", "bad", "good_2"], fail=("bad",)
    )
    out = mirror_maestro_history(
        pvtproject_path=proj, session="s0", bridge=bridge
    )
    assert sorted(out["mirrored"]) == ["good_1", "good_2"]
    assert len(out["failed"]) == 1
    assert out["failed"][0]["history"] == "bad"
    assert "collect failed" in out["failed"][0]["error"]


def test_mirror_is_idempotent_on_second_run(tmp_path):
    proj, _ = _make_pvtproject(tmp_path)
    bridge = _FakeBridge(["Interactive.0"])
    first = mirror_maestro_history(
        pvtproject_path=proj, session="s0", bridge=bridge
    )
    assert first["mirrored"] == ["Interactive.0"]
    second = mirror_maestro_history(
        pvtproject_path=proj, session="s0", bridge=bridge
    )
    assert second["mirrored"] == []
    assert second["skipped"] == ["Interactive.0"]
