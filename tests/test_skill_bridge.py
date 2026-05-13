"""Tier-1 tests for the Python ``skill_bridge`` wrapper.

These tests do NOT touch a live Virtuoso session: they inject a
mock ``workspace`` so the wrapper can be exercised in isolation.
Live-Maestro verification is captured separately as a runtime probe.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "python"))

from simkit.skill_bridge import (  # noqa: E402
    SkillBridgeError,
    pvt_corners_pull,
    pvt_corners_push,
    resolve_pvtproject_path,
)


def _ok(value):
    return [SimpleNamespace(name="pvt_ok"), value]


def _err(category: str, msg: str, source=None):
    return [
        SimpleNamespace(name="pvt_err"),
        SimpleNamespace(name=category),
        msg,
        source,
    ]


def _make_mock_ws(pull_return=None, push_return=None):
    """Build a MagicMock that emulates skillbridge.Workspace's __getitem__."""
    pull_fn = MagicMock(return_value=pull_return)
    push_fn = MagicMock(return_value=push_return)
    load_fn = MagicMock()
    cwd_fn = MagicMock()

    table = {
        "load": load_fn,
        "changeWorkingDir": cwd_fn,
        "setShellEnvVar": MagicMock(),
        "pvtCornersPull": pull_fn,
        "pvtCornersPush": push_fn,
    }

    ws = MagicMock()
    ws.__getitem__.side_effect = table.__getitem__
    ws._table = table
    return ws


def _write_pvtproject(tmpdir: Path) -> Path:
    proj_dir = tmpdir / "myproj"
    proj_dir.mkdir()
    p = proj_dir / ".pvtproject"
    p.write_text(
        json.dumps(
            {"project": "skbridge_test", "dbRoot": "./data", "schema_version": 1}
        ),
        encoding="utf-8",
    )
    return p


# --- pull -----------------------------------------------------------------


class TestPvtCornersPull(unittest.TestCase):

    def test_returns_skill_outpath_on_ok(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            pvtproj = _write_pvtproject(tmp)
            ws = _make_mock_ws(pull_return=_ok("/abs/out.union.json"))
            got = pvt_corners_pull(
                "/abs/out.union.json", pvtproject_path=pvtproj, workspace=ws
            )
        self.assertEqual(got, "/abs/out.union.json")

    def test_passes_outpath_kwarg(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            pvtproj = _write_pvtproject(tmp)
            ws = _make_mock_ws(pull_return=_ok("/x.union.json"))
            pvt_corners_pull(
                "/x.union.json", pvtproject_path=pvtproj, workspace=ws
            )
        ws._table["pvtCornersPull"].assert_called_once_with(outPath="/x.union.json")

    def test_session_override_passed_as_sess(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            pvtproj = _write_pvtproject(tmp)
            ws = _make_mock_ws(pull_return=_ok("/x.union.json"))
            pvt_corners_pull(
                "/x.union.json",
                pvtproject_path=pvtproj,
                session="fnxSession0",
                workspace=ws,
            )
        ws._table["pvtCornersPull"].assert_called_once_with(
            outPath="/x.union.json", sess="fnxSession0"
        )

    def test_union_name_override_passed(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            pvtproj = _write_pvtproject(tmp)
            ws = _make_mock_ws(pull_return=_ok("/x.union.json"))
            pvt_corners_pull(
                "/x.union.json",
                pvtproject_path=pvtproj,
                union_name="my_name",
                workspace=ws,
            )
        ws._table["pvtCornersPull"].assert_called_once_with(
            outPath="/x.union.json", unionName="my_name"
        )

    def test_pvt_err_raises_skillbridge_error(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            pvtproj = _write_pvtproject(tmp)
            ws = _make_mock_ws(
                pull_return=_err("pvt_validation", "?outPath is required", None)
            )
            with self.assertRaises(SkillBridgeError) as ctx:
                pvt_corners_pull("/x.union.json", pvtproject_path=pvtproj, workspace=ws)
        self.assertEqual(ctx.exception.category, "pvt_validation")
        self.assertEqual(ctx.exception.message, "?outPath is required")
        self.assertIsNone(ctx.exception.source)

    def test_pvt_err_includes_source(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            pvtproj = _write_pvtproject(tmp)
            ws = _make_mock_ws(
                pull_return=_err("pvt_io", "cannot write", "/etc/readonly")
            )
            with self.assertRaises(SkillBridgeError) as ctx:
                pvt_corners_pull("/etc/readonly", pvtproject_path=pvtproj, workspace=ws)
        self.assertEqual(ctx.exception.source, "/etc/readonly")
        self.assertIn("/etc/readonly", str(ctx.exception))

    def test_loads_all_production_skill_files(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            pvtproj = _write_pvtproject(tmp)
            ws = _make_mock_ws(pull_return=_ok("/x"))
            pvt_corners_pull("/x.union.json", pvtproject_path=pvtproj, workspace=ws)
        loaded = [c.args[0] for c in ws._table["load"].call_args_list]
        # All five production files, in dependency order.
        names = [Path(p).name for p in loaded]
        self.assertEqual(
            names,
            [
                "pvtError.il",
                "pvtJson.il",
                "pvtProject.il",
                "pvtCollect.il",
                "pvtCorners.il",
            ],
        )

    def test_changes_working_dir_to_pvtproject_parent(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            pvtproj = _write_pvtproject(tmp)
            ws = _make_mock_ws(pull_return=_ok("/x"))
            pvt_corners_pull("/x.union.json", pvtproject_path=pvtproj, workspace=ws)
        ws._table["changeWorkingDir"].assert_called_once_with(str(pvtproj.parent))


# --- push -----------------------------------------------------------------


class TestPvtCornersPush(unittest.TestCase):

    def test_returns_name_on_ok(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            pvtproj = _write_pvtproject(tmp)
            ws = _make_mock_ws(push_return=_ok("myunion"))
            got = pvt_corners_push(
                "/path/to/x.union.json", pvtproject_path=pvtproj, workspace=ws
            )
        self.assertEqual(got, "myunion")

    def test_passes_unionjsonpath_kwarg(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            pvtproj = _write_pvtproject(tmp)
            ws = _make_mock_ws(push_return=_ok("n"))
            pvt_corners_push("/a/b.union.json", pvtproject_path=pvtproj, workspace=ws)
        ws._table["pvtCornersPush"].assert_called_once_with(
            unionJsonPath="/a/b.union.json"
        )

    def test_dry_run_passes_dryrun_kwarg(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            pvtproj = _write_pvtproject(tmp)
            ws = _make_mock_ws(push_return=_ok("n"))
            pvt_corners_push(
                "/a/b.union.json",
                pvtproject_path=pvtproj,
                dry_run=True,
                workspace=ws,
            )
        ws._table["pvtCornersPush"].assert_called_once_with(
            unionJsonPath="/a/b.union.json", dryRun=True
        )

    def test_dry_run_false_omits_dryrun_kwarg(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            pvtproj = _write_pvtproject(tmp)
            ws = _make_mock_ws(push_return=_ok("n"))
            pvt_corners_push(
                "/a/b.union.json",
                pvtproject_path=pvtproj,
                dry_run=False,
                workspace=ws,
            )
        ws._table["pvtCornersPush"].assert_called_once_with(
            unionJsonPath="/a/b.union.json"
        )

    def test_session_passed_as_sess(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            pvtproj = _write_pvtproject(tmp)
            ws = _make_mock_ws(push_return=_ok("n"))
            pvt_corners_push(
                "/a/b.union.json",
                pvtproject_path=pvtproj,
                session="fnxSession0",
                workspace=ws,
            )
        ws._table["pvtCornersPush"].assert_called_once_with(
            unionJsonPath="/a/b.union.json", sess="fnxSession0"
        )

    def test_pvt_err_raises(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            pvtproj = _write_pvtproject(tmp)
            ws = _make_mock_ws(
                push_return=_err("pvt_validation", "missing 'rows' array", "/x.json")
            )
            with self.assertRaises(SkillBridgeError) as ctx:
                pvt_corners_push("/x.json", pvtproject_path=pvtproj, workspace=ws)
        self.assertEqual(ctx.exception.category, "pvt_validation")
        self.assertEqual(ctx.exception.source, "/x.json")


# --- result decoder -------------------------------------------------------


class TestUnwrapEdgeCases(unittest.TestCase):

    def test_unknown_head_raises(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            pvtproj = _write_pvtproject(tmp)
            ws = _make_mock_ws(pull_return=[SimpleNamespace(name="huh"), "x"])
            with self.assertRaises(SkillBridgeError) as ctx:
                pvt_corners_pull("/x.union.json", pvtproject_path=pvtproj, workspace=ws)
        self.assertEqual(ctx.exception.category, "transport")

    def test_malformed_response_raises(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            pvtproj = _write_pvtproject(tmp)
            ws = _make_mock_ws(pull_return="not-a-list")
            with self.assertRaises(SkillBridgeError) as ctx:
                pvt_corners_pull("/x.union.json", pvtproject_path=pvtproj, workspace=ws)
        self.assertEqual(ctx.exception.category, "transport")


# --- resolve_pvtproject_path ---------------------------------------------


class TestResolvePvtprojectPath(unittest.TestCase):

    def test_explicit_existing_file(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.pvtproject"
            p.write_text("{}")
            got = resolve_pvtproject_path(str(p))
        self.assertEqual(got, p.resolve())

    def test_explicit_missing_raises(self):
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "nope.pvtproject"
            with self.assertRaises(FileNotFoundError):
                resolve_pvtproject_path(str(missing))


if __name__ == "__main__":
    unittest.main()
