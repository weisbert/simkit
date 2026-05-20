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
    pvt_runner_count_running,
    pvt_runner_get_history_lock_map,
    pvt_runner_get_sim_option_val,
    pvt_runner_get_status,
    pvt_runner_run,
    pvt_runner_set_history_lock,
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

    # --- v1.9 #1: --replace flag plumbing (DECISIONS #67) -----------------

    def test_replace_true_passes_replace_kwarg(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            pvtproj = _write_pvtproject(tmp)
            ws = _make_mock_ws(push_return=_ok("n"))
            pvt_corners_push(
                "/a/b.union.json",
                pvtproject_path=pvtproj,
                replace=True,
                workspace=ws,
            )
        ws._table["pvtCornersPush"].assert_called_once_with(
            unionJsonPath="/a/b.union.json", replace=True
        )

    def test_replace_false_omits_replace_kwarg(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            pvtproj = _write_pvtproject(tmp)
            ws = _make_mock_ws(push_return=_ok("n"))
            pvt_corners_push(
                "/a/b.union.json",
                pvtproject_path=pvtproj,
                replace=False,
                workspace=ws,
            )
        ws._table["pvtCornersPush"].assert_called_once_with(
            unionJsonPath="/a/b.union.json"
        )

    def test_replace_combines_with_dry_run_and_session(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            pvtproj = _write_pvtproject(tmp)
            ws = _make_mock_ws(push_return=_ok("n"))
            pvt_corners_push(
                "/a/b.union.json",
                pvtproject_path=pvtproj,
                session="fnxSession0",
                dry_run=True,
                replace=True,
                workspace=ws,
            )
        ws._table["pvtCornersPush"].assert_called_once_with(
            unionJsonPath="/a/b.union.json",
            sess="fnxSession0", dryRun=True, replace=True,
        )


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
               status_raises=(),
               count_running_sequence=None):
    """Build a stub workspace for the pvt_runner_run state machine.

    ``status_sequence`` is a list of (code, sub) tuples returned by
    successive pvtRunnerGetStatus calls. If a slot in
    ``status_raises`` is set to an exception, that call raises instead
    of returning.

    v1.5 F2: ``count_running_sequence`` is an optional list of int
    returns for successive ``pvtRunnerCountRunning`` calls. Default is
    all-zeros (matches the pre-F2 behaviour where the count was not
    consulted), so legacy tests that don't pass this kwarg keep working
    unchanged.
    """
    seq = list(status_sequence)
    raises = dict(status_raises)
    call_idx = {"n": 0}
    count_seq = list(count_running_sequence or [])
    count_idx = {"n": 0}

    def status_side(_sess):
        i = call_idx["n"]
        call_idx["n"] += 1
        if i in raises:
            raise raises[i]
        if i >= len(seq):
            return _ok([0, 0])  # default idle after the sequence ends
        c, s = seq[i]
        return _ok([c, s])

    def count_side(_sess):
        i = count_idx["n"]
        count_idx["n"] += 1
        if i >= len(count_seq):
            return _ok(0)  # default to "no in-flight rows" once sequence ends
        return _ok(int(count_seq[i]))

    submit_fn = MagicMock(return_value=submit_return)
    rename_fn = MagicMock(return_value=rename_return)
    status_fn = MagicMock(side_effect=status_side)
    count_fn = MagicMock(side_effect=count_side)

    table = {
        "load": MagicMock(),
        "pvtRunnerSubmit": submit_fn,
        "pvtRunnerRename": rename_fn,
        "pvtRunnerGetStatus": status_fn,
        "pvtRunnerCountRunning": count_fn,
    }
    ws = MagicMock()
    ws.__getitem__.side_effect = table.__getitem__
    ws._table = table
    ws._calls = call_idx
    ws._count_calls = count_idx
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
        """count_running non-zero for 3 polls then 0; one idle confirm
        is enough. axlGetRunStatus is deliberately junk (24,24) the whole
        time — the v1.6 fix means the loop ignores it and trusts the
        content-based count_running signal."""
        ws = _runner_ws(
            [(24, 24)] * 8,
            count_running_sequence=[6, 6, 6, 0],
        )
        code, sub, name = pvt_runner_run(
            "myhist", session="sess", workspace=ws,
            poll_interval=0, dispatch_grace_reads=99,  # disable grace path
            idle_confirm_reads=1, initial_wait_sec=0,
            _sleep=lambda _t: None,
        )
        self.assertEqual(name, "hist_renamed")
        self.assertEqual(ws._calls["n"], 4)

    def test_completes_when_axlGetRunStatus_never_reaches_zero(self):
        """Regression for the 2026-05-20 stuck-pending bug: on fnxSession0
        axlGetRunStatus returns (24,24)/(18,18)/(0,14) even when the run
        is genuinely complete. The loop must still finish — driven purely
        by count_running dropping to 0 — not poll to timeout_sec."""
        ws = _runner_ws(
            [(12, 12), (24, 24), (18, 18), (0, 14), (24, 24)],
            count_running_sequence=[7, 7, 3, 0, 0],
        )
        code, sub, name = pvt_runner_run(
            "h", session="s", workspace=ws,
            poll_interval=1, timeout_sec=1000, dispatch_grace_reads=99,
            idle_confirm_reads=2, initial_wait_sec=0,
            _sleep=lambda _t: None,
        )
        self.assertEqual(name, "hist_renamed")
        # 4th poll: count hits 0 (streak 1). 5th poll: 0 again (streak 2,
        # >= idle_confirm_reads) -> break. Never reached the timeout.
        self.assertEqual(ws._count_calls["n"], 5)

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
        """A single transient count_running==0 in the middle of a run
        should NOT be enough to declare 'done' when idle_confirm_reads>1.
        Models the rdb momentarily reading 0 (mid-write) between two
        non-zero reads."""
        # in-flight, transient 0, in-flight, then sustained 0,0.
        ws = _runner_ws(
            [(24, 24)] * 7,
            count_running_sequence=[6, 0, 6, 0, 0],
        )
        code, sub, name = pvt_runner_run(
            "h", session="s", workspace=ws,
            poll_interval=0, dispatch_grace_reads=99,
            idle_confirm_reads=2, initial_wait_sec=0,
            _sleep=lambda _t: None,
        )
        self.assertEqual(name, "hist_renamed")
        # 5 count reads consumed: the transient 0 at read 2 must not break.
        self.assertEqual(ws._count_calls["n"], 5)

    # --- v1.5 F2: count_running second signal ----------------------------

    def test_count_running_keeps_loop_waiting_until_zero(self):
        """Even when axlGetRunStatus is [0,0] from the start, a non-zero
        count_running must keep the state machine waiting. Sequence
        6 -> 4 -> 0 across 3 polls + idle_confirm streak must complete
        only after the count drops to zero AND stays."""
        # 5 polls so saw_non_idle path + idle_confirm_reads=2 are exercised.
        # status all-idle, count 6/4/0/0/0.
        ws = _runner_ws(
            [(0, 0)] * 6,
            count_running_sequence=[6, 4, 0, 0, 0],
        )
        code, sub, name = pvt_runner_run(
            "h", session="s", workspace=ws,
            poll_interval=0,
            dispatch_grace_reads=99,
            idle_confirm_reads=2,
            _sleep=lambda _t: None,
        )
        self.assertEqual((code, sub, name), (0, 0, "hist_renamed"))
        # Must have polled 5 times: 2 with count>0 (sets saw_non_idle via
        # the count-side reset), 1 with count=0 (streak=1), 1 with count=0
        # (streak=2 → idle_confirm satisfied → break). So 4 status calls.
        # Account for any small variation in how the streak rolls.
        self.assertGreaterEqual(ws._calls["n"], 4)
        # Count was queried at least once per status poll
        self.assertEqual(ws._count_calls["n"], ws._calls["n"])

    def test_dispatch_grace_unchanged_when_count_zero_from_start(self):
        """No regression: when status AND count both stay 0 from the
        first poll, dispatch_grace_reads still exits as before."""
        ws = _runner_ws(
            [(0, 0), (0, 0)],
            count_running_sequence=[0, 0],
        )
        code, sub, name = pvt_runner_run(
            "h", session="s", workspace=ws,
            poll_interval=0, dispatch_grace_reads=2,
            _sleep=lambda _t: None,
        )
        self.assertEqual((code, sub, name), (0, 0, "hist_renamed"))
        # Exactly 2 polls — grace exit on 2nd consecutive both-idle read.
        self.assertEqual(ws._calls["n"], 2)
        ws._table["pvtRunnerRename"].assert_called_once_with("s", "h")

    def test_count_takes_precedence_over_dispatch_grace(self):
        """NEW v1.5 F2 rule: if count_running starts non-zero while
        axlGetRunStatus is [0,0], dispatch_grace must NOT fire — the
        state machine must wait for the count to drop to zero."""
        # Status is [0,0] throughout. Count starts at 6, then 0/0/0.
        # With dispatch_grace_reads=2 the pre-F2 code would have exited
        # after the first 2 idle reads (i.e. polls 2 and 3). Under F2,
        # the count==6 on poll 1 sets saw_non_idle, so the only way out
        # is the idle_confirm_reads path.
        ws = _runner_ws(
            [(0, 0)] * 5,
            count_running_sequence=[6, 0, 0, 0, 0],
        )
        code, sub, name = pvt_runner_run(
            "h", session="s", workspace=ws,
            poll_interval=0,
            dispatch_grace_reads=2,
            idle_confirm_reads=2,
            _sleep=lambda _t: None,
        )
        self.assertEqual((code, sub, name), (0, 0, "hist_renamed"))
        # Poll 1: count=6, not idle. Poll 2: count=0, idle_streak=1.
        # Poll 3: count=0, idle_streak=2 → break (saw_non_idle path).
        self.assertEqual(ws._calls["n"], 3)

    def test_min_running_observed_forces_dispatch_wait(self):
        """When min_running_observed=1, the state machine MUST observe
        at least one count_running>=1 poll before allowing any kind of
        idle exit. Used by orchestrator to defeat the slow-queue race."""
        # status [0,0] throughout; count 0 for 3 polls, then 2, then 0/0/0.
        # Without the gate, dispatch_grace_reads=2 would exit on poll 2.
        # With min_running_observed=1, the loop must keep polling until
        # it sees the 2 in poll 4, then wait for it to drop.
        ws = _runner_ws(
            [(0, 0)] * 8,
            count_running_sequence=[0, 0, 0, 2, 0, 0, 0, 0],
        )
        code, sub, name = pvt_runner_run(
            "h", session="s", workspace=ws,
            poll_interval=0,
            dispatch_grace_reads=2,
            idle_confirm_reads=2,
            min_running_observed=1,
            _sleep=lambda _t: None,
        )
        self.assertEqual((code, sub, name), (0, 0, "hist_renamed"))
        # Must NOT have exited on poll 2 via grace. Must reach >= poll 4
        # (the one with count=2) plus enough idle reads after.
        self.assertGreaterEqual(ws._calls["n"], 6)


class TestPvtRunnerCountRunning(unittest.TestCase):
    """Wrapper-level tests for pvt_runner_count_running (v1.5 F2)."""

    def test_happy_path_returns_int(self):
        count_fn = MagicMock(return_value=_ok(5))
        table = {
            "load": MagicMock(),
            "pvtRunnerCountRunning": count_fn,
        }
        ws = MagicMock()
        ws.__getitem__.side_effect = table.__getitem__
        got = pvt_runner_count_running(session="sess0", workspace=ws)
        self.assertEqual(got, 5)
        self.assertIsInstance(got, int)
        count_fn.assert_called_once_with("sess0")

    def test_zero_returned_as_int(self):
        count_fn = MagicMock(return_value=_ok(0))
        table = {
            "load": MagicMock(),
            "pvtRunnerCountRunning": count_fn,
        }
        ws = MagicMock()
        ws.__getitem__.side_effect = table.__getitem__
        self.assertEqual(
            pvt_runner_count_running(session="s", workspace=ws), 0,
        )

    def test_skill_error_surfaces_as_skillbridgeerror(self):
        err_fn = MagicMock(return_value=_err(
            "pvt_validation", "session must be a non-empty string",
        ))
        table = {
            "load": MagicMock(),
            "pvtRunnerCountRunning": err_fn,
        }
        ws = MagicMock()
        ws.__getitem__.side_effect = table.__getitem__
        with self.assertRaises(SkillBridgeError) as cm:
            pvt_runner_count_running(session="", workspace=ws)
        self.assertEqual(cm.exception.category, "pvt_validation")


class TestPvtRunnerGetSimOptionVal(unittest.TestCase):
    """Wrapper-level tests for pvt_runner_get_sim_option_val (v1.9 #2)."""

    def _make_ws(self, return_value):
        fn = MagicMock(return_value=return_value)
        table = {
            "load": MagicMock(),
            "pvtRunnerGetSimOptionVal": fn,
        }
        ws = MagicMock()
        ws.__getitem__.side_effect = table.__getitem__
        return ws, fn

    def test_string_value_returned_as_str(self):
        ws, fn = self._make_ws(_ok("1e-12"))
        got = pvt_runner_get_sim_option_val(
            "Test", "gmin", session="s", workspace=ws,
        )
        self.assertEqual(got, "1e-12")
        fn.assert_called_once_with("s", "Test", "gmin")

    def test_no_option_returns_none(self):
        # SKILL pvt_runner_no_option → wrapper translates to None.
        ws, _ = self._make_ws(
            _err("pvt_runner_no_option", "option no_such not set on Test"),
        )
        got = pvt_runner_get_sim_option_val(
            "Test", "no_such", session="s", workspace=ws,
        )
        self.assertIsNone(got)

    def test_no_session_raises_skillbridgeerror(self):
        ws, _ = self._make_ws(
            _err("pvt_runner_no_session", "axlGetToolSession nil for missing"),
        )
        with self.assertRaises(SkillBridgeError) as cm:
            pvt_runner_get_sim_option_val(
                "missing", "gmin", session="s", workspace=ws,
            )
        self.assertEqual(cm.exception.category, "pvt_runner_no_session")

    def test_empty_test_name_raises_pvt_validation(self):
        with self.assertRaises(SkillBridgeError) as cm:
            pvt_runner_get_sim_option_val(
                "", "gmin", session="s", workspace=MagicMock(),
            )
        self.assertEqual(cm.exception.category, "pvt_validation")

    def test_empty_option_key_raises_pvt_validation(self):
        with self.assertRaises(SkillBridgeError) as cm:
            pvt_runner_get_sim_option_val(
                "Test", "", session="s", workspace=MagicMock(),
            )
        self.assertEqual(cm.exception.category, "pvt_validation")


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


# --- v1.8 #4: history lock wrappers --------------------------------------


def _make_lock_ws(eval_returns):
    """Mock ws['evalstring'] with a sequence of return values."""
    eval_fn = MagicMock(side_effect=list(eval_returns))
    table = {"evalstring": eval_fn}
    ws = MagicMock()
    ws.__getitem__.side_effect = table.__getitem__
    ws._table = table
    return ws


class TestPvtRunnerSetHistoryLock(unittest.TestCase):
    def test_lock_emits_t(self):
        ws = _make_lock_ws(["T"])
        pvt_runner_set_history_lock(
            "v17_demo", True, session="fnxSession0", workspace=ws,
        )
        call = ws._table["evalstring"].call_args
        self.assertIn('maeSetHistoryLock "v17_demo" t', call.args[0])
        self.assertIn('?session "fnxSession0"', call.args[0])

    def test_unlock_emits_nil(self):
        ws = _make_lock_ws(["T"])
        pvt_runner_set_history_lock(
            "v17_demo", False, session="fnxSession0", workspace=ws,
        )
        call = ws._table["evalstring"].call_args
        self.assertIn('maeSetHistoryLock "v17_demo" nil', call.args[0])

    def test_nil_response_raises(self):
        ws = _make_lock_ws(["nil"])
        with self.assertRaises(SkillBridgeError) as cm:
            pvt_runner_set_history_lock(
                "ghost", True, session="fnxSession0", workspace=ws,
            )
        self.assertEqual(cm.exception.category, "lock_failed")

    def test_double_quote_in_name_is_escaped(self):
        # SKILL string literal needs \" — our helper must produce it.
        ws = _make_lock_ws(["T"])
        pvt_runner_set_history_lock(
            'bad"name', True, session="S", workspace=ws,
        )
        call = ws._table["evalstring"].call_args
        self.assertIn(r'"bad\"name"', call.args[0])

    def test_newline_in_name_rejected(self):
        ws = _make_lock_ws(["T"])
        with self.assertRaises(SkillBridgeError) as cm:
            pvt_runner_set_history_lock(
                "bad\nname", True, session="S", workspace=ws,
            )
        self.assertEqual(cm.exception.category, "bad_history_name")


class TestPvtRunnerGetHistoryLockMap(unittest.TestCase):
    def test_parses_tab_separated_lines(self):
        # First evalstring resolves hsdb, second walks histories.
        ws = _make_lock_ws([
            "1001",
            "simkit_verify\tT\nv17_gmin_demo__gmin2\tnil\n",
        ])
        out = pvt_runner_get_history_lock_map(
            session="fnxSession0", workspace=ws,
        )
        self.assertEqual(
            out, {"simkit_verify": True, "v17_gmin_demo__gmin2": False},
        )

    def test_empty_session_returns_empty_dict(self):
        ws = _make_lock_ws(["1001", ""])
        out = pvt_runner_get_history_lock_map(
            session="fnxSession0", workspace=ws,
        )
        self.assertEqual(out, {})

    def test_trailing_blank_lines_skipped(self):
        ws = _make_lock_ws(["1001", "only_one\tT\n\n\n"])
        out = pvt_runner_get_history_lock_map(
            session="s", workspace=ws,
        )
        self.assertEqual(out, {"only_one": True})


if __name__ == "__main__":
    unittest.main()
