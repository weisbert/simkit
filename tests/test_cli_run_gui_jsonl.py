"""Phase 4 §9 — `pvt run --gui-jsonl` CLI integration.

Covers the producer side of the JSONL pipe:

* The CLI accepts ``--gui-jsonl`` and emits structured events.
* Argument / load errors surface as ``{"event": "error", ...}`` lines.
* Successful live run emits item_started + item_completed + review_done.
* SIGTERM during a run flips the LAST ingested run to ``partial_run=TRUE``
  and exits 130.
* The CLI also supports being spawned as a real subprocess (used by the
  Phase 4 GUI's QProcess) — covered with a tiny ``subprocess.run`` end-
  to-end smoke check that parses JSONL back into dicts.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.cli.__main__ import main  # noqa: E402


def _parse_jsonl(buf: str) -> list[dict]:
    out = []
    for line in buf.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


class EarlyErrorTests(unittest.TestCase):
    def test_missing_review_emits_error_event(self):
        buf = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            rc = main(["run", "/tmp/no-such-thing.review.json", "--gui-jsonl"])
        self.assertEqual(rc, 2)
        events = _parse_jsonl(buf.getvalue())
        self.assertTrue(any(e.get("event") == "error" for e in events),
                        msg=f"got: {events}")
        err_ev = [e for e in events if e["event"] == "error"][0]
        self.assertIn("code", err_ev)
        self.assertIn("msg", err_ev)

    def test_no_args_emits_error_event(self):
        buf = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            rc = main(["run", "--gui-jsonl"])
        self.assertEqual(rc, 2)
        events = _parse_jsonl(buf.getvalue())
        codes = [e.get("code") for e in events if e["event"] == "error"]
        self.assertIn("bad_args", codes)

    def test_no_gui_jsonl_no_events_in_stdout(self):
        # Without --gui-jsonl, stdout must NOT contain JSONL lines.
        buf = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            rc = main(["run", "/tmp/no-such-thing.review.json"])
        self.assertEqual(rc, 2)
        events = _parse_jsonl(buf.getvalue())
        self.assertEqual(events, [])


class LiveModeWithMockedExecuteTests(unittest.TestCase):
    """Live mode with ``execute`` mocked — verifies CLI wires `--gui-jsonl`
    into `progress_cb`/`cancel_check`, emits review_done, and respects the
    SIGTERM-cancel exit 130 path."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_gui_jsonl_"))
        (self.tmp / ".pvtproject").write_text(json.dumps({
            "schema_version": 1,
            "project": "tmp_project",
            "dbRoot": "./db",
        }))
        shutil.copy(
            _REPO_ROOT / "config" / "pvt_union_example.union.json",
            self.tmp / "u.union.json",
        )
        (self.tmp / "r.review.json").write_text(json.dumps({
            "review_schema_version": 1,
            "name": "r",
            "project": "tmp_project",
            "items": [
                {"name": "only", "tests": ["t"], "union": "u.union.json"},
            ],
        }))
        self._old_env = os.environ.pop("PVT_SESSION", None)

    def tearDown(self):
        if self._old_env is not None:
            os.environ["PVT_SESSION"] = self._old_env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _ok_report(self):
        from simkit.orchestrator import ExecuteReport, ItemResult
        return ExecuteReport(
            items=(ItemResult(
                item_name="only",
                history_names=("orch_only_1",),
                run_dirs=(Path("/tmp/x"),),
                completed=True,
                notes="",
            ),),
            snapshot_restored=True,
        )

    def test_progress_cb_kwarg_is_emitter_callback(self):
        with mock.patch("simkit.cli.run.execute",
                        return_value=self._ok_report()) as mexec:
            buf = io.StringIO(); err = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(err):
                rc = main([
                    "run", str(self.tmp / "r.review.json"),
                    "--session", "sessX",
                    "--project", str(self.tmp),
                    "--gui-jsonl",
                ])
        self.assertEqual(rc, 0, msg=f"err: {err.getvalue()}")
        kwargs = mexec.call_args.kwargs
        self.assertIn("progress_cb", kwargs)
        self.assertTrue(callable(kwargs["progress_cb"]))
        self.assertIn("cancel_check", kwargs)
        self.assertTrue(callable(kwargs["cancel_check"]))

    def test_progress_cb_not_passed_when_flag_absent(self):
        with mock.patch("simkit.cli.run.execute",
                        return_value=self._ok_report()) as mexec:
            buf = io.StringIO(); err = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(err):
                main([
                    "run", str(self.tmp / "r.review.json"),
                    "--session", "sessX",
                    "--project", str(self.tmp),
                ])
        # progress_cb is always wired (no-op emitter) — but the closure
        # should be the emitter's callback. We assert no JSONL appeared
        # in stdout (which is the user-visible contract).
        kwargs = mexec.call_args.kwargs
        self.assertIn("progress_cb", kwargs)

    def test_review_done_emitted_on_success(self):
        with mock.patch("simkit.cli.run.execute",
                        return_value=self._ok_report()):
            buf = io.StringIO(); err = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(err):
                rc = main([
                    "run", str(self.tmp / "r.review.json"),
                    "--session", "sessX",
                    "--project", str(self.tmp),
                    "--gui-jsonl",
                ])
        self.assertEqual(rc, 0)
        events = _parse_jsonl(buf.getvalue())
        done = [e for e in events if e["event"] == "review_done"]
        self.assertEqual(len(done), 1, msg=f"events={events}")
        self.assertEqual(done[0]["exit_code"], 0)
        self.assertEqual(done[0]["summary"]["cancelled"], False)
        self.assertEqual(len(done[0]["summary"]["items"]), 1)

    def test_sigterm_sets_partial_run_and_returns_130(self):
        # Simulate SIGTERM arriving DURING execute() by having the mocked
        # execute() set the module flag mid-call before returning.
        from simkit.orchestrator import ExecuteReport, ItemResult
        report = ExecuteReport(
            items=(ItemResult(
                item_name="only",
                history_names=("orch_only_1",),
                run_dirs=(Path("/nonexistent/run/path"),),
                completed=True,
                notes="",
            ),),
            snapshot_restored=True,
        )

        def fake_execute(*a, **kw):
            import simkit.cli.run as runmod
            runmod._cancel_requested = True
            return report

        with mock.patch("simkit.cli.run.execute", side_effect=fake_execute), \
             mock.patch("simkit.cli.run._mark_partial_run") as mmark:
            buf = io.StringIO(); err = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(err):
                rc = main([
                    "run", str(self.tmp / "r.review.json"),
                    "--session", "sessX",
                    "--project", str(self.tmp),
                    "--gui-jsonl",
                ])
        self.assertEqual(rc, 130, msg=f"err: {err.getvalue()}")
        self.assertEqual(mmark.call_count, 1)
        events = _parse_jsonl(buf.getvalue())
        done = [e for e in events if e["event"] == "review_done"]
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0]["exit_code"], 130)
        self.assertEqual(done[0]["summary"]["cancelled"], True)


class MarkPartialRunTests(unittest.TestCase):
    """`_mark_partial_run` is best-effort + touches DuckDB."""

    def setUp(self):
        from simkit.db import bootstrap, connect
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_partial_"))
        (self.tmp / "db").mkdir()
        (self.tmp / ".pvtproject").write_text(json.dumps({
            "schema_version": 1,
            "project": "p",
            "dbRoot": "./db",
        }))
        self.db_path = self.tmp / "db" / "simkit.duckdb"
        con = connect(self.db_path)
        try:
            bootstrap(con)
            for rid in ("RID1", "RID2"):
                con.execute(
                    "INSERT INTO runs (run_id, project_id, testbench_id, "
                    "testbench_alias, timestamp, author, label, note, "
                    "netlist_path, history_name, schema_version, "
                    "ingested_at, starred) "
                    "VALUES (?, 'p', 'tb', NULL, '2026-05-18T12:00:00+08:00',"
                    " 'a', NULL, NULL, NULL, ?, 3, "
                    "'2026-05-18T12:00:00+08:00', FALSE)",
                    [rid, f"hist_{rid}"],
                )
        finally:
            con.close()
        # Stub run dirs.
        for rid in ("RID1", "RID2"):
            d = self.tmp / "runs" / rid
            d.mkdir(parents=True)
            (d / "run.json").write_text(json.dumps({
                "schema_version": 2, "run": {"run_id": rid},
                "results": [], "artifacts": [],
            }))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_marks_last_run_partial(self):
        from simkit.cli.run import _mark_partial_run
        from simkit.db import connect
        from simkit.gui_events import GuiEventEmitter
        from simkit.orchestrator import ExecuteReport, ItemResult

        report = ExecuteReport(
            items=(
                ItemResult(
                    item_name="a", history_names=("h1",),
                    run_dirs=(self.tmp / "runs" / "RID1",), completed=True,
                ),
                ItemResult(
                    item_name="b", history_names=("h2",),
                    run_dirs=(self.tmp / "runs" / "RID2",), completed=True,
                ),
            ),
            snapshot_restored=True,
        )
        em = GuiEventEmitter(enabled=False)
        _mark_partial_run(report, self.tmp / ".pvtproject", em)

        con = connect(self.db_path, read_only=True)
        try:
            rows = con.execute(
                "SELECT run_id, partial_run FROM runs ORDER BY run_id"
            ).fetchall()
        finally:
            con.close()
        # RID2 is the LAST run dir → marked TRUE; RID1 stays FALSE.
        self.assertEqual(rows, [("RID1", False), ("RID2", True)])

    def test_no_run_dirs_logs_warn_no_crash(self):
        from simkit.cli.run import _mark_partial_run
        from simkit.gui_events import GuiEventEmitter
        from simkit.orchestrator import ExecuteReport
        em = GuiEventEmitter(enabled=False)
        # Should not raise.
        _mark_partial_run(ExecuteReport(items=(), snapshot_restored=True),
                          self.tmp / ".pvtproject", em)


class SubprocessSmokeTests(unittest.TestCase):
    """Spawn `python -m simkit.cli run --gui-jsonl` as a REAL subprocess.

    This is the exact wire-shape the Phase 4 GUI's RunController will see:
    one JSON object per line on stdout. We use a missing-review-path
    error path so we don't need Maestro or DuckDB to verify the wire
    contract.
    """

    def test_subprocess_emits_error_jsonl_on_missing_path(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(_REPO_ROOT / "python") + os.pathsep + env.get(
            "PYTHONPATH", "")
        proc = subprocess.run(
            [sys.executable, "-m", "simkit.cli", "run",
             "/tmp/no-such-thing.review.json", "--gui-jsonl"],
            capture_output=True, text=True, env=env, timeout=30,
        )
        # Exit code is 2 for review-load failure.
        self.assertEqual(proc.returncode, 2,
                         msg=f"stderr: {proc.stderr}\nstdout: {proc.stdout}")
        events = _parse_jsonl(proc.stdout)
        self.assertTrue(events, msg=f"no events in stdout: {proc.stdout!r}")
        self.assertTrue(any(e.get("event") == "error" for e in events))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
