"""Unit tests for :mod:`simkit.provenance` (G-5 traceability).

Covers the pure builder / injector / comparator plus an end-to-end
ingest round-trip proving the orchestrator-injected ``provenance`` block
lands in ``runs.provenance``.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.db import bootstrap, connect  # noqa: E402
from simkit.ingest import ingest_run_json  # noqa: E402
from simkit.provenance import (  # noqa: E402
    build_provenance,
    compare_provenance,
    inject_run_provenance,
    load_provenance,
)


_RUN_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def _union_dict(model_abs: str, name: str = "u") -> dict:
    return {
        "union_schema_version": 1,
        "name": name, "project": "p", "testbench_id": "lib/cell/view",
        "rows": [{
            "row_name": "TT",
            "models": [{
                "file": "rf018.scs", "section": "tt", "_file_abs": model_abs,
            }],
        }],
    }


def _run_dump() -> dict:
    return {
        "schema_version": 1,
        "run": {
            "run_id": _RUN_ID, "project_id": "p",
            "testbench_id": "lib/cell/view", "testbench_alias": None,
            "timestamp": "2026-05-20T12:00:00+08:00", "author": "t",
            "label": None, "note": None,
            "netlist_path": "input.scs", "history_name": "h",
        },
        "results": [], "artifacts": [],
    }


class ProvenanceTestBase(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_prov_"))
        self.model = self.tmp / "rf018.scs"
        self.model.write_text("* model v1\n", encoding="utf-8")
        self.union = self.tmp / "u.union.json"
        self.union.write_text(json.dumps(_union_dict(str(self.model))))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_run_json(self) -> Path:
        run_dir = self.tmp / "runs" / _RUN_ID
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "input.scs").write_text("* net\n", encoding="utf-8")
        (run_dir / "run.json").write_text(json.dumps(_run_dump()))
        return run_dir


class BuildProvenanceTests(ProvenanceTestBase):

    def test_build_has_core_keys(self):
        prov = build_provenance(union_path=self.union)
        self.assertIn("host", prov)
        self.assertIn("captured_at", prov)
        self.assertIn("pdk_version", prov)
        self.assertIn("model_files", prov)

    def test_model_files_fingerprinted(self):
        prov = build_provenance(union_path=self.union)
        files = prov["model_files"]
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0]["exists"])
        self.assertEqual(files[0]["size"], len("* model v1\n"))
        self.assertEqual(files[0]["path"], str(self.model))

    def test_missing_model_file_marked_absent(self):
        union = self.tmp / "u2.union.json"
        union.write_text(
            json.dumps(_union_dict("/nonexistent/x.scs", name="u2"))
        )
        prov = build_provenance(union_path=union)
        self.assertFalse(prov["model_files"][0]["exists"])

    def test_no_union_means_empty_model_files(self):
        prov = build_provenance(union_path=None)
        self.assertEqual(prov["model_files"], [])

    def test_pdk_version_from_env(self):
        import os
        os.environ["PVT_PDK_VERSION"] = "rf018_v2.0"
        try:
            prov = build_provenance()
            self.assertEqual(prov["pdk_version"], "rf018_v2.0")
        finally:
            del os.environ["PVT_PDK_VERSION"]


class InjectProvenanceTests(ProvenanceTestBase):

    def test_inject_writes_provenance_block(self):
        run_dir = self._write_run_json()
        ok = inject_run_provenance(run_dir, union_path=self.union)
        self.assertTrue(ok)
        dump = json.loads((run_dir / "run.json").read_text())
        self.assertIn("provenance", dump)
        self.assertIn("host", dump["provenance"])

    def test_inject_accepts_run_json_path_directly(self):
        run_dir = self._write_run_json()
        ok = inject_run_provenance(
            run_dir / "run.json", union_path=self.union,
        )
        self.assertTrue(ok)

    def test_inject_missing_run_json_returns_false(self):
        ok = inject_run_provenance(self.tmp / "no_such_dir")
        self.assertFalse(ok)

    def test_inject_preserves_existing_envelope(self):
        run_dir = self._write_run_json()
        inject_run_provenance(run_dir, union_path=self.union)
        dump = json.loads((run_dir / "run.json").read_text())
        # The original envelope keys survive.
        self.assertEqual(dump["run"]["run_id"], _RUN_ID)
        self.assertEqual(dump["results"], [])


class LoadProvenanceTests(unittest.TestCase):

    def test_load_valid_json(self):
        obj = load_provenance('{"host": "h1"}')
        self.assertEqual(obj, {"host": "h1"})

    def test_load_none_or_empty(self):
        self.assertIsNone(load_provenance(None))
        self.assertIsNone(load_provenance(""))

    def test_load_garbage_returns_none(self):
        self.assertIsNone(load_provenance("not json"))

    def test_load_non_object_returns_none(self):
        self.assertIsNone(load_provenance("[1, 2, 3]"))


class CompareProvenanceTests(unittest.TestCase):

    def _prov(self, **kw):
        base = {
            "host": "hostA", "pdk_version": "v1",
            "model_files": [
                {"path": "/m.scs", "size": 100, "mtime": "2026-01-01T00:00:00"},
            ],
        }
        base.update(kw)
        return base

    def test_identical_provenance_no_mismatch(self):
        self.assertEqual(compare_provenance(self._prov(), self._prov()), [])

    def test_host_mismatch_flagged(self):
        diffs = compare_provenance(self._prov(), self._prov(host="hostB"))
        self.assertTrue(any("host" in d for d in diffs))

    def test_pdk_mismatch_flagged(self):
        diffs = compare_provenance(self._prov(), self._prov(pdk_version="v2"))
        self.assertTrue(any("PDK" in d for d in diffs))

    def test_model_file_changed_flagged(self):
        changed = self._prov(model_files=[
            {"path": "/m.scs", "size": 999, "mtime": "2026-01-01T00:00:00"},
        ])
        diffs = compare_provenance(self._prov(), changed)
        self.assertTrue(any("已改动" in d for d in diffs))

    def test_none_side_flagged_as_unprovable(self):
        diffs = compare_provenance(self._prov(), None)
        self.assertEqual(len(diffs), 1)
        self.assertIn("没有 provenance", diffs[0])


class IngestProvenanceRoundTripTests(ProvenanceTestBase):

    def test_injected_provenance_lands_in_runs_column(self):
        run_dir = self._write_run_json()
        inject_run_provenance(run_dir, union_path=self.union)
        db = self.tmp / "simkit.duckdb"
        con = connect(db)
        try:
            bootstrap(con)
            ingest_run_json(con, run_dir / "run.json")
            raw = con.execute(
                "SELECT provenance FROM runs WHERE run_id = ?", [_RUN_ID],
            ).fetchone()[0]
        finally:
            con.close()
        prov = load_provenance(raw)
        self.assertIsNotNone(prov)
        self.assertIn("host", prov)

    def test_run_without_provenance_ingests_with_null_column(self):
        run_dir = self._write_run_json()  # no inject
        db = self.tmp / "simkit.duckdb"
        con = connect(db)
        try:
            bootstrap(con)
            ingest_run_json(con, run_dir / "run.json")
            raw = con.execute(
                "SELECT provenance FROM runs WHERE run_id = ?", [_RUN_ID],
            ).fetchone()[0]
        finally:
            con.close()
        self.assertIsNone(raw)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
