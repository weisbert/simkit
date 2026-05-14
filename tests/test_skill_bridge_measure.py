"""Tier-1 tests for the Phase 3B measure wrappers in ``skill_bridge``.

Mirror ``tests/test_skill_bridge.py``: inject a mock workspace so the
wrappers can be exercised without a live Virtuoso session.
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
    PvtMeasurePullReport,
    PvtMeasurePushReport,
    PvtMeasurePushRow,
    SkillBridgeError,
    pvt_measure_pull,
    pvt_measure_push,
    pvt_measure_restore,
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


def _make_mock_ws(push_return=None, pull_return=None, import_return=True,
                  session="fnxSession0"):
    push_fn = MagicMock(return_value=push_return)
    pull_fn = MagicMock(return_value=pull_return)
    load_fn = MagicMock()
    cwd_fn = MagicMock()
    env_fn = MagicMock()
    import_fn = MagicMock(return_value=import_return)
    get_session_fn = MagicMock(return_value=session)

    table = {
        "load": load_fn,
        "changeWorkingDir": cwd_fn,
        "setShellEnvVar": env_fn,
        "pvtMeasurePush": push_fn,
        "pvtMeasurePull": pull_fn,
        "axlOutputsImportFromFile": import_fn,
        "axlGetWindowSession": get_session_fn,
    }

    ws = MagicMock()
    ws.__getitem__.side_effect = table.__getitem__
    ws._table = table
    return ws


def _push_payload(n=1, rows=None):
    if rows is None:
        rows = [
            {"name": "Rtime_Vout", "status": "added"},
        ]
    return {"n_pushed": n, "rows": rows}


def _pull_payload(n=1, path="/tmp/x.snapshot.json"):
    return {"n_rows": n, "path": path}


def _make_rendered_json(td: Path, *, rows=None) -> Path:
    p = td / "rendered.json"
    body = {
        "rendered_schema_version": 1,
        "test": "Test",
        "rows": rows or [
            {
                "output_name": "Rtime_Vout",
                "expression": "average(VT(\"/Vout\"))",
                "eval_type": "point",
                "plot": True,
                "save": False,
            }
        ],
    }
    p.write_text(json.dumps(body), encoding="utf-8")
    return p


# --- pvt_measure_push -----------------------------------------------------


class TestPvtMeasurePush(unittest.TestCase):

    def test_returns_decoded_push_report(self):
        with tempfile.TemporaryDirectory() as td:
            jp = _make_rendered_json(Path(td))
            ws = _make_mock_ws(push_return=_ok(_push_payload()))
            got = pvt_measure_push(jp, workspace=ws)
        self.assertIsInstance(got, PvtMeasurePushReport)
        self.assertEqual(got.n_pushed, 1)
        self.assertEqual(len(got.rows), 1)
        self.assertEqual(got.rows[0].name, "Rtime_Vout")
        self.assertEqual(got.rows[0].status, "added")
        self.assertIsNone(got.rows[0].reason)

    def test_passes_renderedjsonpath_kwarg(self):
        with tempfile.TemporaryDirectory() as td:
            jp = _make_rendered_json(Path(td))
            ws = _make_mock_ws(push_return=_ok(_push_payload()))
            pvt_measure_push(jp, workspace=ws)
            kwargs = ws._table["pvtMeasurePush"].call_args.kwargs
        self.assertEqual(kwargs["renderedJsonPath"], str(jp))
        self.assertEqual(kwargs["testName"], "Test")

    def test_dry_run_passes_dryrun_kwarg(self):
        with tempfile.TemporaryDirectory() as td:
            jp = _make_rendered_json(Path(td))
            ws = _make_mock_ws(push_return=_ok(_push_payload()))
            pvt_measure_push(jp, dry_run=True, workspace=ws)
            kwargs = ws._table["pvtMeasurePush"].call_args.kwargs
        self.assertTrue(kwargs.get("dryRun"))

    def test_replace_passes_replace_kwarg(self):
        with tempfile.TemporaryDirectory() as td:
            jp = _make_rendered_json(Path(td))
            ws = _make_mock_ws(push_return=_ok(_push_payload()))
            pvt_measure_push(jp, replace=True, workspace=ws)
            kwargs = ws._table["pvtMeasurePush"].call_args.kwargs
        self.assertTrue(kwargs.get("replace"))

    def test_session_passes_sess_kwarg(self):
        with tempfile.TemporaryDirectory() as td:
            jp = _make_rendered_json(Path(td))
            ws = _make_mock_ws(push_return=_ok(_push_payload()))
            pvt_measure_push(jp, session="fnxSession0", workspace=ws)
            kwargs = ws._table["pvtMeasurePush"].call_args.kwargs
        self.assertEqual(kwargs["sess"], "fnxSession0")

    def test_test_name_override_passes_testname_kwarg(self):
        with tempfile.TemporaryDirectory() as td:
            jp = _make_rendered_json(Path(td))
            ws = _make_mock_ws(push_return=_ok(_push_payload()))
            pvt_measure_push(jp, test_name="OtherTest", workspace=ws)
            kwargs = ws._table["pvtMeasurePush"].call_args.kwargs
        self.assertEqual(kwargs["testName"], "OtherTest")

    def test_loads_measure_skill_files(self):
        with tempfile.TemporaryDirectory() as td:
            jp = _make_rendered_json(Path(td))
            ws = _make_mock_ws(push_return=_ok(_push_payload()))
            pvt_measure_push(jp, workspace=ws)
            loaded = [c.args[0] for c in ws._table["load"].call_args_list]
        names = [Path(p).name for p in loaded]
        self.assertEqual(
            names, ["pvtError.il", "pvtJson.il", "pvtMeasure.il"]
        )

    def test_pvt_err_raises_skillbridge_error(self):
        with tempfile.TemporaryDirectory() as td:
            jp = _make_rendered_json(Path(td))
            ws = _make_mock_ws(
                push_return=_err(
                    "pvt_validation",
                    "row 'Rtime_Vout' failed: bad expr",
                    str(jp),
                ),
            )
            with self.assertRaises(SkillBridgeError) as ctx:
                pvt_measure_push(jp, workspace=ws)
        self.assertEqual(ctx.exception.category, "pvt_validation")
        self.assertEqual(ctx.exception.source, str(jp))

    def test_decodes_failed_row_with_reason(self):
        with tempfile.TemporaryDirectory() as td:
            jp = _make_rendered_json(Path(td))
            payload = {
                "n_pushed": 0,
                "rows": [
                    {
                        "name": "BadRow",
                        "status": "failed",
                        "reason": "missing expression",
                    }
                ],
            }
            ws = _make_mock_ws(push_return=_ok(payload))
            got = pvt_measure_push(jp, workspace=ws)
        self.assertEqual(got.rows[0].status, "failed")
        self.assertEqual(got.rows[0].reason, "missing expression")

    def test_changes_working_dir_to_json_parent_when_no_project(self):
        with tempfile.TemporaryDirectory() as td:
            jp = _make_rendered_json(Path(td))
            ws = _make_mock_ws(push_return=_ok(_push_payload()))
            pvt_measure_push(jp, workspace=ws)
        # cwd should be the rendered JSON's parent dir.
        ws._table["changeWorkingDir"].assert_called_once_with(
            str(jp.parent.resolve())
        )

    def test_project_path_pins_pvt_project_env(self):
        with tempfile.TemporaryDirectory() as td:
            jp = _make_rendered_json(Path(td))
            pvtproject = Path(td) / ".pvtproject"
            pvtproject.write_text("{}", encoding="utf-8")
            ws = _make_mock_ws(push_return=_ok(_push_payload()))
            pvt_measure_push(jp, pvtproject_path=pvtproject, workspace=ws)
        ws._table["changeWorkingDir"].assert_called_once_with(
            str(pvtproject.parent)
        )
        ws._table["setShellEnvVar"].assert_called_once_with(
            f"PVT_PROJECT={pvtproject}"
        )


# --- pvt_measure_pull -----------------------------------------------------


class TestPvtMeasurePull(unittest.TestCase):

    def test_returns_decoded_pull_report(self):
        ws = _make_mock_ws(pull_return=_ok(_pull_payload(n=7, path="/x.json")))
        got = pvt_measure_pull("/x.snapshot.json", workspace=ws)
        self.assertIsInstance(got, PvtMeasurePullReport)
        self.assertEqual(got.n_rows, 7)
        self.assertEqual(got.path, "/x.json")

    def test_passes_outpath_kwarg(self):
        ws = _make_mock_ws(pull_return=_ok(_pull_payload()))
        pvt_measure_pull("/x.snapshot.json", workspace=ws)
        kwargs = ws._table["pvtMeasurePull"].call_args.kwargs
        self.assertEqual(kwargs["outPath"], "/x.snapshot.json")
        self.assertEqual(kwargs["testName"], "Test")

    def test_include_signals_passes_kwarg(self):
        ws = _make_mock_ws(pull_return=_ok(_pull_payload()))
        pvt_measure_pull(
            "/x.snapshot.json", include_signals=True, workspace=ws,
        )
        kwargs = ws._table["pvtMeasurePull"].call_args.kwargs
        self.assertTrue(kwargs.get("includeSignals"))

    def test_session_passes_sess_kwarg(self):
        ws = _make_mock_ws(pull_return=_ok(_pull_payload()))
        pvt_measure_pull(
            "/x.snapshot.json", session="fnxSession0", workspace=ws,
        )
        kwargs = ws._table["pvtMeasurePull"].call_args.kwargs
        self.assertEqual(kwargs["sess"], "fnxSession0")

    def test_test_name_default_is_Test(self):
        ws = _make_mock_ws(pull_return=_ok(_pull_payload()))
        pvt_measure_pull("/x.snapshot.json", workspace=ws)
        kwargs = ws._table["pvtMeasurePull"].call_args.kwargs
        self.assertEqual(kwargs["testName"], "Test")

    def test_pvt_err_raises(self):
        ws = _make_mock_ws(
            pull_return=_err("pvt_io", "cannot write", "/foo"),
        )
        with self.assertRaises(SkillBridgeError) as ctx:
            pvt_measure_pull("/foo", workspace=ws)
        self.assertEqual(ctx.exception.category, "pvt_io")

    def test_changes_working_dir_to_outpath_parent(self):
        ws = _make_mock_ws(pull_return=_ok(_pull_payload()))
        pvt_measure_pull("/tmp/x.snapshot.json", workspace=ws)
        ws._table["changeWorkingDir"].assert_called_once_with("/tmp")


# --- pvt_measure_restore --------------------------------------------------


class TestPvtMeasureRestore(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_skb_restore_"))
        self.csv = self.tmp / "x.csv"
        self.csv.write_text(
            "Test,Name,Type,Output,Plot,Save,Spec\n", encoding="utf-8",
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_invokes_axl_outputs_import_from_file(self):
        ws = _make_mock_ws(import_return=True)
        pvt_measure_restore(self.csv, workspace=ws)
        ws._table["axlOutputsImportFromFile"].assert_called_once()
        args, kwargs = ws._table["axlOutputsImportFromFile"].call_args
        self.assertEqual(args[0], "fnxSession0")
        self.assertEqual(args[1], str(self.csv))
        self.assertEqual(kwargs["operation"], "merge")

    def test_passes_session_override(self):
        ws = _make_mock_ws(import_return=True)
        pvt_measure_restore(self.csv, session="otherSession", workspace=ws)
        args = ws._table["axlOutputsImportFromFile"].call_args.args
        self.assertEqual(args[0], "otherSession")

    def test_passes_test_name(self):
        ws = _make_mock_ws(import_return=True)
        pvt_measure_restore(self.csv, test_name="OtherTest", workspace=ws)
        kwargs = ws._table["axlOutputsImportFromFile"].call_args.kwargs
        self.assertEqual(kwargs["test"], "OtherTest")

    def test_missing_csv_raises(self):
        with self.assertRaises(SkillBridgeError) as ctx:
            pvt_measure_restore(self.tmp / "no_such.csv")
        self.assertEqual(ctx.exception.category, "pvt_io")

    def test_invalid_operation_raises(self):
        with self.assertRaises(SkillBridgeError) as ctx:
            pvt_measure_restore(
                self.csv, operation="bogus", workspace=_make_mock_ws(),
            )
        self.assertEqual(ctx.exception.category, "pvt_validation")

    def test_import_returning_nil_raises(self):
        ws = _make_mock_ws(import_return=None)
        with self.assertRaises(SkillBridgeError) as ctx:
            pvt_measure_restore(self.csv, workspace=ws)
        self.assertEqual(ctx.exception.category, "pvt_io")

    def test_no_active_session_raises(self):
        ws = _make_mock_ws(import_return=True, session=None)
        with self.assertRaises(SkillBridgeError) as ctx:
            pvt_measure_restore(self.csv, workspace=ws)
        self.assertEqual(ctx.exception.category, "pvt_validation")


# --- dataclass plumbing ---------------------------------------------------


class TestDataclasses(unittest.TestCase):

    def test_push_row_default_reason(self):
        r = PvtMeasurePushRow(name="x", status="added")
        self.assertIsNone(r.reason)

    def test_push_report_default_rows_empty(self):
        rep = PvtMeasurePushReport(n_pushed=0)
        self.assertEqual(rep.rows, ())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
