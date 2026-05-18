"""Integration tests for `pvt run` CLI (Phase 3A §5).

Drives ``simkit.cli.__main__.main`` end-to-end with argv lists.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.cli.__main__ import main  # noqa: E402


_EXAMPLE_REVIEW = _REPO_ROOT / "config" / "review_example.review.json"


class CliRunDryRunTests(unittest.TestCase):
    def test_dry_run_example_exits_zero_with_summary(self):
        buf_out = io.StringIO()
        buf_err = io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            rc = main(["run", str(_EXAMPLE_REVIEW), "--dry-run"])
        self.assertEqual(rc, 0, msg=f"stderr was: {buf_err.getvalue()}")
        out = buf_out.getvalue()
        self.assertIn("REVIEW review_example", out)
        self.assertIn("BT2GRX trans PVT", out)
        self.assertIn("SUMMARY  5 items planned", out)

    def test_dry_run_strict_paths_exits_3_when_missing(self):
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(io.StringIO()):
            rc = main(["run", str(_EXAMPLE_REVIEW), "--dry-run", "--strict-paths"])
        self.assertEqual(rc, 3)

    def test_dry_run_items_filter(self):
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(io.StringIO()):
            rc = main([
                "run", str(_EXAMPLE_REVIEW), "--dry-run",
                "--items", "BT2GRX trans PVT,干扰仿真",
            ])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("planned=2", out)
        self.assertIn("BT2GRX trans PVT", out)
        self.assertIn("干扰仿真", out)
        self.assertNotIn("LE mode trans PVT", out)

    def test_items_filter_unknown_returns_4(self):
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(io.StringIO()):
            rc = main([
                "run", str(_EXAMPLE_REVIEW), "--dry-run",
                "--items", "no_such_item",
            ])
        self.assertEqual(rc, 4)

    def test_live_mode_without_session_returns_2(self):
        buf_err = io.StringIO()
        old_env = os.environ.pop("PVT_SESSION", None)
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(buf_err):
                rc = main(["run", str(_EXAMPLE_REVIEW)])
        finally:
            if old_env is not None:
                os.environ["PVT_SESSION"] = old_env
        self.assertEqual(rc, 2)
        self.assertIn("--session", buf_err.getvalue())

    def test_no_args_or_path_returns_2(self):
        buf_err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(buf_err):
            rc = main(["run"])
        self.assertEqual(rc, 2)
        self.assertIn("either a review.json", buf_err.getvalue())

    def test_mixing_review_and_tests_returns_2(self):
        buf_err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(buf_err):
            rc = main([
                "run", str(_EXAMPLE_REVIEW),
                "--tests", "sim_a",
                "--union", "/tmp/x.union.json",
            ])
        self.assertEqual(rc, 2)
        self.assertIn("cannot mix", buf_err.getvalue())

    def test_tests_without_union_returns_2(self):
        buf_err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(buf_err):
            rc = main(["run", "--tests", "sim_a"])
        self.assertEqual(rc, 2)
        self.assertIn("--union", buf_err.getvalue())


class CliRunAdHocModeTests(unittest.TestCase):
    """Ad-hoc mode requires a discoverable .pvtproject for the project name.
    Spin one up in tmp + cd there for the test."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_cli_adhoc_"))
        # Minimal .pvtproject
        (self.tmp / ".pvtproject").write_text(json.dumps({
            "schema_version": 1,
            "project": "tmp_project",
            "dbRoot": "./db",
        }))
        # Copy the real example union so the planner can count corners
        shutil.copy(
            _REPO_ROOT / "config" / "pvt_union_example.union.json",
            self.tmp / "pvt_union_example.union.json",
        )
        self._old_cwd = Path.cwd()
        import os
        os.chdir(self.tmp)

    def tearDown(self):
        import os
        os.chdir(self._old_cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_adhoc_dry_run_counts_corners(self):
        buf = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            rc = main([
                "run",
                "--tests", "sim_a",
                "--union", "pvt_union_example.union.json",
                "--dry-run",
            ])
        self.assertEqual(rc, 0, msg=f"err: {err.getvalue()}")
        out = buf.getvalue()
        # The example union explodes to 7 sub-corners
        self.assertIn("(7 corners)", out)
        self.assertIn("ad-hoc", out)


class CliRunLiveModeTests(unittest.TestCase):
    """Live-mode tests with execute() mocked out — verify wiring + exit codes."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_cli_live_"))
        (self.tmp / ".pvtproject").write_text(json.dumps({
            "schema_version": 1,
            "project": "tmp_project",
            "dbRoot": "./db",
        }))
        # Minimal review.json + union that the planner can load.
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

    def _fake_report(self, *, all_ok: bool):
        from simkit.orchestrator import ExecuteReport, ItemResult
        completed = all_ok
        return ExecuteReport(
            items=(ItemResult(
                item_name="only",
                history_names=("orch_only_1",),
                run_dirs=(Path("/tmp/x"),),
                completed=completed,
                notes="" if all_ok else "axlRunAllTests error: synthetic",
            ),),
            snapshot_restored=True,
        )

    def test_live_all_ok_returns_0(self):
        with mock.patch("simkit.cli.run.execute",
                        return_value=self._fake_report(all_ok=True)) as mexec:
            buf = io.StringIO(); err = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(err):
                rc = main([
                    "run", str(self.tmp / "r.review.json"),
                    "--session", "sessX",
                    "--project", str(self.tmp),
                ])
        self.assertEqual(rc, 0, msg=f"err: {err.getvalue()}")
        self.assertEqual(mexec.call_count, 1)
        kwargs = mexec.call_args.kwargs
        self.assertEqual(kwargs["session"], "sessX")
        self.assertEqual(kwargs["push_union"], True)
        self.assertEqual(kwargs["history_prefix"], "orch")
        self.assertIn("DONE", buf.getvalue())
        self.assertIn("[ok] only", buf.getvalue())

    def test_live_partial_returns_6(self):
        with mock.patch("simkit.cli.run.execute",
                        return_value=self._fake_report(all_ok=False)):
            buf = io.StringIO(); err = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(err):
                rc = main([
                    "run", str(self.tmp / "r.review.json"),
                    "--session", "sessX",
                    "--project", str(self.tmp),
                ])
        self.assertEqual(rc, 6, msg=f"err: {err.getvalue()}")
        self.assertIn("[INCOMPLETE]", buf.getvalue())

    def test_live_no_push_union_flag(self):
        with mock.patch("simkit.cli.run.execute",
                        return_value=self._fake_report(all_ok=True)) as mexec:
            buf = io.StringIO(); err = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(err):
                rc = main([
                    "run", str(self.tmp / "r.review.json"),
                    "--session", "sessX",
                    "--project", str(self.tmp),
                    "--no-push-union",
                    "--history-prefix", "v1dog",
                ])
        self.assertEqual(rc, 0)
        kwargs = mexec.call_args.kwargs
        self.assertEqual(kwargs["push_union"], False)
        self.assertEqual(kwargs["history_prefix"], "v1dog")

    def test_live_session_from_env(self):
        with mock.patch("simkit.cli.run.execute",
                        return_value=self._fake_report(all_ok=True)) as mexec:
            os.environ["PVT_SESSION"] = "envSess"
            try:
                buf = io.StringIO(); err = io.StringIO()
                with redirect_stdout(buf), redirect_stderr(err):
                    rc = main([
                        "run", str(self.tmp / "r.review.json"),
                        "--project", str(self.tmp),
                    ])
            finally:
                del os.environ["PVT_SESSION"]
        self.assertEqual(rc, 0)
        self.assertEqual(mexec.call_args.kwargs["session"], "envSess")

    def test_live_pvtproject_walked_from_review_path(self):
        # No --project, no cwd change — must find via review.json's parent.
        with mock.patch("simkit.cli.run.execute",
                        return_value=self._fake_report(all_ok=True)) as mexec:
            buf = io.StringIO(); err = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(err):
                rc = main([
                    "run", str(self.tmp / "r.review.json"),
                    "--session", "sessX",
                ])
        self.assertEqual(rc, 0, msg=f"err: {err.getvalue()}")
        self.assertEqual(
            mexec.call_args.kwargs["pvtproject_path"],
            self.tmp / ".pvtproject",
        )

    def test_live_execute_raises_orchestrator_error_returns_6(self):
        from simkit.orchestrator import OrchestratorError
        with mock.patch("simkit.cli.run.execute",
                        side_effect=OrchestratorError("bridge wedge")):
            buf = io.StringIO(); err = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(err):
                rc = main([
                    "run", str(self.tmp / "r.review.json"),
                    "--session", "sessX",
                    "--project", str(self.tmp),
                ])
        self.assertEqual(rc, 6)
        self.assertIn("bridge wedge", err.getvalue())


if __name__ == "__main__":
    unittest.main()
