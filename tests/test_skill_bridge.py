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
    pvt_runner_get_status,
    pvt_runner_run,
    pvt_save,
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


_SENTINEL_ORIG_CWD = "/orig/cwd/from/parent/virtuoso"


def _make_mock_ws(pull_return=None, push_return=None):
    """Build a MagicMock that emulates skillbridge.Workspace's __getitem__.

    The mock exposes ``getWorkingDir`` returning the sentinel
    :data:`_SENTINEL_ORIG_CWD` so tests can assert the cwd-restore
    contract (DECISIONS #56): every verb that calls ``changeWorkingDir``
    must call it again with the sentinel before returning.
    """
    pull_fn = MagicMock(return_value=pull_return)
    push_fn = MagicMock(return_value=push_return)
    load_fn = MagicMock()
    cwd_fn = MagicMock()

    table = {
        "load": load_fn,
        "changeWorkingDir": cwd_fn,
        "setShellEnvVar": MagicMock(),
        "getWorkingDir": MagicMock(return_value=_SENTINEL_ORIG_CWD),
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

    def test_changes_working_dir_to_pvtproject_parent_then_restores(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            pvtproj = _write_pvtproject(tmp)
            ws = _make_mock_ws(pull_return=_ok("/x"))
            pvt_corners_pull("/x.union.json", pvtproject_path=pvtproj, workspace=ws)
        calls = ws._table["changeWorkingDir"].call_args_list
        # First call enters the project dir, second restores the sentinel.
        # DECISIONS #56: pvt_runner_run that fires later must NOT inherit
        # the per-verb cwd, otherwise Maestro's AXL worker boots in the
        # wrong dir and can't find cds.lib.
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].args[0], str(pvtproj.parent))
        self.assertEqual(calls[1].args[0], _SENTINEL_ORIG_CWD)


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


# --- runner: poll-to-idle state machine -----------------------------------


def _runner_ws(status_sequence, *, submit_return=_ok(SimpleNamespace(name="t")),
               rename_return=_ok("hist_renamed"),
               status_raises=()):
    """Build a stub workspace for the pvt_runner_run state machine.

    ``status_sequence`` is a list of (code, sub) tuples returned by
    successive pvtRunnerGetStatus calls. If a slot in
    ``status_raises`` is set to an exception, that call raises instead
    of returning.
    """
    seq = list(status_sequence)
    raises = dict(status_raises)
    call_idx = {"n": 0}

    def status_side(_sess):
        i = call_idx["n"]
        call_idx["n"] += 1
        if i in raises:
            raise raises[i]
        if i >= len(seq):
            return _ok([0, 0])  # default idle after the sequence ends
        c, s = seq[i]
        return _ok([c, s])

    submit_fn = MagicMock(return_value=submit_return)
    rename_fn = MagicMock(return_value=rename_return)
    status_fn = MagicMock(side_effect=status_side)

    table = {
        "load": MagicMock(),
        "pvtRunnerSubmit": submit_fn,
        "pvtRunnerRename": rename_fn,
        "pvtRunnerGetStatus": status_fn,
    }
    ws = MagicMock()
    ws.__getitem__.side_effect = table.__getitem__
    ws._table = table
    ws._calls = call_idx
    return ws


class TestPvtRunnerRunStateMachine(unittest.TestCase):
    """Covers the v1.1 poll-to-idle state machine in pvt_runner_run."""

    def test_cached_path_returns_via_dispatch_grace(self):
        """When status stays [0,0] for dispatch_grace_reads polls,
        accept as 'cached / no-op completion' and rename."""
        ws = _runner_ws([(0, 0), (0, 0)])
        code, sub, name = pvt_runner_run(
            "h", session="s", workspace=ws,
            poll_interval=0, dispatch_grace_reads=2,
            _sleep=lambda _t: None,
        )
        self.assertEqual((code, sub, name), (0, 0, "hist_renamed"))
        ws._table["pvtRunnerSubmit"].assert_called_once_with("s")
        ws._table["pvtRunnerRename"].assert_called_once_with("s", "h")

    def test_real_run_path_waits_for_non_idle_then_idle(self):
        """Saw non-idle then [0,0]; one idle confirm is enough by default."""
        ws = _runner_ws([(5, 9), (5, 9), (5, 9), (0, 0)])
        code, sub, name = pvt_runner_run(
            "myhist", session="sess", workspace=ws,
            poll_interval=0, dispatch_grace_reads=99,  # disable grace path
            idle_confirm_reads=1,
            _sleep=lambda _t: None,
        )
        self.assertEqual((code, sub, name), (0, 0, "hist_renamed"))
        self.assertEqual(ws._calls["n"], 4)

    def test_handle_zero_runtime_error_is_treated_as_idle(self):
        """The uncatchable 'handle 0' fatal from axlGetRunStatus must
        be translated to (0, 0), not propagated."""
        # First poll throws the handle-0 error; second poll returns idle.
        ws = _runner_ws(
            [(0, 0), (0, 0)],
            status_raises={
                0: RuntimeError(
                    '("error" 0 t nil ("*Error* error: Cannot find a '
                    'setup database entry for handle 0." nil))'
                ),
            },
        )
        code, sub, name = pvt_runner_run(
            "h", session="s", workspace=ws,
            poll_interval=0, dispatch_grace_reads=2,
            _sleep=lambda _t: None,
        )
        self.assertEqual((code, sub), (0, 0))
        self.assertEqual(name, "hist_renamed")

    def test_other_runtime_error_propagates(self):
        ws = _runner_ws(
            [(0, 0)],
            status_raises={0: RuntimeError("some unrelated transport blowup")},
        )
        with self.assertRaises(RuntimeError):
            pvt_runner_run(
                "h", session="s", workspace=ws,
                poll_interval=0, dispatch_grace_reads=3,
                _sleep=lambda _t: None,
            )

    def test_timeout_raises_skillbridge_error(self):
        # Always non-idle for many reads; with timeout=0 the loop never iterates
        # so we'd never check status. Use a small timeout and small interval.
        ws = _runner_ws([(5, 9)] * 100)
        with self.assertRaises(SkillBridgeError) as cm:
            pvt_runner_run(
                "h", session="s", workspace=ws,
                poll_interval=1, timeout_sec=3,
                _sleep=lambda _t: None,
            )
        self.assertEqual(cm.exception.category, "pvt_runner_timeout")
        # Rename NEVER called on timeout.
        ws._table["pvtRunnerRename"].assert_not_called()

    def test_empty_history_name_rejected_before_submit(self):
        ws = _runner_ws([(0, 0)])
        with self.assertRaises(SkillBridgeError) as cm:
            pvt_runner_run("", session="s", workspace=ws,
                           poll_interval=0, _sleep=lambda _t: None)
        self.assertEqual(cm.exception.category, "pvt_validation")
        ws._table["pvtRunnerSubmit"].assert_not_called()

    def test_idle_confirm_requires_consecutive_reads(self):
        """A single transient [0,0] in the middle of a run should NOT
        be enough to declare 'done' when idle_confirm_reads > 1."""
        # Non-idle, transient idle, non-idle, then sustained idle.
        ws = _runner_ws([(5, 9), (0, 0), (5, 9), (0, 0), (0, 0)])
        code, sub, name = pvt_runner_run(
            "h", session="s", workspace=ws,
            poll_interval=0, dispatch_grace_reads=99,
            idle_confirm_reads=2,
            _sleep=lambda _t: None,
        )
        self.assertEqual((code, sub), (0, 0))
        # 5 status reads consumed
        self.assertEqual(ws._calls["n"], 5)


class TestPvtRunnerGetStatusTranslation(unittest.TestCase):
    """The handle-0 translation is exercised separately for clarity."""

    def test_normal_ok(self):
        ws = MagicMock()
        ws.__getitem__.side_effect = {
            "load": MagicMock(),
            "pvtRunnerGetStatus": MagicMock(return_value=_ok([5, 9])),
        }.__getitem__
        self.assertEqual(pvt_runner_get_status(session="s", workspace=ws), (5, 9))

    def test_handle_zero_runtime_error_translated(self):
        def boom(_):
            raise RuntimeError(
                '("error" 0 t nil ("*Error* error: Cannot find a '
                'setup database entry for handle 0." nil))'
            )
        ws = MagicMock()
        ws.__getitem__.side_effect = {
            "load": MagicMock(),
            "pvtRunnerGetStatus": MagicMock(side_effect=boom),
        }.__getitem__
        self.assertEqual(pvt_runner_get_status(session="s", workspace=ws), (0, 0))

    def test_other_runtime_error_propagates(self):
        def boom(_):
            raise RuntimeError("transport went away")
        ws = MagicMock()
        ws.__getitem__.side_effect = {
            "load": MagicMock(),
            "pvtRunnerGetStatus": MagicMock(side_effect=boom),
        }.__getitem__
        with self.assertRaises(RuntimeError):
            pvt_runner_get_status(session="s", workspace=ws)


# --- cwd-restore contract for pvt_save (orchestrator's critical leak path) ---


class TestPvtSaveCwdRestore(unittest.TestCase):
    """pvt_save is invoked by the orchestrator immediately after
    pvt_runner_run. If it leaks the project dir as the parent's cwd,
    the NEXT pvt_runner_run inherits a wrong cwd and Maestro generates
    a broken runICRP launcher (DECISIONS #56). Pin the snapshot/restore
    behaviour here so any future refactor that drops the wrapper
    breaks loudly at test-time, not in a live dogfood."""

    def test_changes_cwd_to_pvtproject_parent_then_restores_to_sentinel(self):
        with tempfile.TemporaryDirectory() as td:
            pvtproj = _write_pvtproject(Path(td))
            save_fn = MagicMock(return_value=_ok("/abs/runs/r1"))
            table = {
                "load": MagicMock(),
                "changeWorkingDir": MagicMock(),
                "setShellEnvVar": MagicMock(),
                "getWorkingDir": MagicMock(return_value=_SENTINEL_ORIG_CWD),
                "PvtSave": save_fn,
            }
            ws = MagicMock()
            ws.__getitem__.side_effect = table.__getitem__
            ws._table = table

            got = pvt_save("hist_x", pvtproject_path=pvtproj, workspace=ws)

        self.assertEqual(got, "/abs/runs/r1")
        calls = ws._table["changeWorkingDir"].call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].args[0], str(pvtproj.parent.resolve()))
        self.assertEqual(calls[1].args[0], _SENTINEL_ORIG_CWD)
        ws._table["setShellEnvVar"].assert_called_once_with(
            f"PVT_PROJECT={pvtproj.resolve()}"
        )

    def test_restores_cwd_even_when_PvtSave_raises(self):
        """SKILL pvt_err must NOT prevent cwd restoration — the
        contextmanager's finally clause is what guarantees this."""
        with tempfile.TemporaryDirectory() as td:
            pvtproj = _write_pvtproject(Path(td))
            save_fn = MagicMock(return_value=_err("pvt_io", "boom"))
            table = {
                "load": MagicMock(),
                "changeWorkingDir": MagicMock(),
                "setShellEnvVar": MagicMock(),
                "getWorkingDir": MagicMock(return_value=_SENTINEL_ORIG_CWD),
                "PvtSave": save_fn,
            }
            ws = MagicMock()
            ws.__getitem__.side_effect = table.__getitem__
            ws._table = table

            with self.assertRaises(SkillBridgeError):
                pvt_save("hist_x", pvtproject_path=pvtproj, workspace=ws)

        calls = ws._table["changeWorkingDir"].call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[-1].args[0], _SENTINEL_ORIG_CWD)


class TestRestoreCwdOnException(unittest.TestCase):
    """Cross-verb: pvt_corners_pull's body raising must still restore."""

    def test_pull_restores_cwd_when_skill_returns_error(self):
        with tempfile.TemporaryDirectory() as td:
            pvtproj = _write_pvtproject(Path(td))
            ws = _make_mock_ws(pull_return=_err("pvt_io", "disk full"))
            with self.assertRaises(SkillBridgeError):
                pvt_corners_pull("/x", pvtproject_path=pvtproj, workspace=ws)
        calls = ws._table["changeWorkingDir"].call_args_list
        # Even on error: enter + restore = 2 calls, last one is sentinel.
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[-1].args[0], _SENTINEL_ORIG_CWD)


if __name__ == "__main__":
    unittest.main()
