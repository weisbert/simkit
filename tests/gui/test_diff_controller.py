"""Unit tests for :class:`simkit.gui.controllers.diff.DiffController`.

End-to-end: build a temp DuckDB with two ingested runs, point the
controller at it via the ``db_path_resolver``, and assert that
``open_diff`` emits a fully-constructed :class:`DiffTab`. Mirrors the
fixture-building pattern in ``tests/test_diff.py`` — same dump shape,
same ``ingest_run_json``.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "python"))

from PyQt5.QtCore import pyqtBoundSignal  # noqa: E402
from PyQt5.QtWidgets import QApplication, QWidget  # noqa: E402

from simkit.db import bootstrap, connect  # noqa: E402
from simkit.gui.controllers.diff import DiffController  # noqa: E402
from simkit.gui.views.diff_tab import DiffTab  # noqa: E402
from simkit.ingest import ingest_run_json  # noqa: E402


_QAPP = QApplication.instance() or QApplication(sys.argv)


_RUN_A_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_RUN_B_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


_BASE_DUMP: Dict[str, Any] = {
    "schema_version": 1,
    "run": {
        "run_id": _RUN_A_ID,
        "project_id": "diff_gui_test",
        "testbench_id": "lib/cell/view",
        "testbench_alias": None,
        "timestamp": "2026-05-10T12:00:00+08:00",
        "author": "tester",
        "label": None,
        "note": None,
        "netlist_path": "input.scs",
        "history_name": "h",
    },
    "results": [],
    "artifacts": [],
}


def _result_row(test, corner, point, output, value, status="ok"):
    return {
        "point": point, "corner": corner, "test": test, "output": output,
        "value": value, "status": status,
        "sweep": {}, "corner_vars": {}, "test_note": None,
    }


class DiffControllerTestBase(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_gui_diff_"))
        self.runs_root = self.tmp / "runs"
        self.db = self.tmp / "simkit.duckdb"
        self.con = connect(self.db)
        bootstrap(self.con)
        self._con_closed = False

    def tearDown(self):
        if not self._con_closed:
            self.con.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _close_write_con(self):
        """Release the write connection so the controller can open
        read-only on the same file (DuckDB refuses mixed modes)."""
        if not self._con_closed:
            self.con.close()
            self._con_closed = True

    def _ingest(
        self, run_id: str, rows: List[Dict[str, Any]],
        *, timestamp: str = "2026-05-10T12:00:00+08:00",
        label: Optional[str] = None,
        netlist_text: Optional[str] = "* netlist v1\n",
    ):
        dump = copy.deepcopy(_BASE_DUMP)
        dump["run"]["run_id"] = run_id
        dump["run"]["timestamp"] = timestamp
        dump["results"] = rows
        if netlist_text is None:
            dump["run"]["netlist_path"] = None
        run_dir = self.runs_root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        if netlist_text is not None:
            (run_dir / "input.scs").write_text(netlist_text, encoding="utf-8")
        run_json = run_dir / "run.json"
        run_json.write_text(json.dumps(dump), encoding="utf-8")
        ingest_run_json(self.con, run_json)
        if label is not None:
            from simkit.label import set_run_label
            set_run_label(self.con, run_id=run_id, label=label)

    def _make_controller(self):
        # Resolver just returns the test DB path verbatim — the GUI
        # production resolver maps project_path → db_path; here we let
        # the test pin it directly.
        return DiffController(
            db_path_resolver=lambda _proj: self.db,
        )


class DiffControllerSignalsTests(DiffControllerTestBase):

    def test_signals_exist(self):
        ctrl = self._make_controller()
        self.assertIsInstance(ctrl.diff_ready, pyqtBoundSignal)
        self.assertIsInstance(ctrl.error, pyqtBoundSignal)


class DiffControllerOpenDiffTests(DiffControllerTestBase):

    def test_open_diff_emits_difftab_with_rows(self):
        self._ingest(_RUN_A_ID,
                     [_result_row("T", "TT", 0, "v", 1.0)])
        self._ingest(_RUN_B_ID,
                     [_result_row("T", "TT", 0, "v", 2.0)])
        self._close_write_con()
        ctrl = self._make_controller()
        seen: list[DiffTab] = []
        ctrl.diff_ready.connect(seen.append)

        ctrl.open_diff(self.tmp, _RUN_A_ID, _RUN_B_ID)

        self.assertEqual(len(seen), 1)
        tab = seen[0]
        self.assertIsInstance(tab, DiffTab)
        self.assertEqual(tab.diff_result.slice_a_run_id, _RUN_A_ID)
        self.assertEqual(tab.diff_result.slice_b_run_id, _RUN_B_ID)
        self.assertEqual(len(tab.diff_result.rows), 1)
        self.assertEqual(tab.diff_result.rows[0].abs_delta, 1.0)

    def test_open_diff_with_unknown_run_emits_error(self):
        self._ingest(_RUN_A_ID, [])
        self._close_write_con()
        ctrl = self._make_controller()
        errors: list[str] = []
        diffs: list[object] = []
        ctrl.error.connect(errors.append)
        ctrl.diff_ready.connect(diffs.append)

        ctrl.open_diff(self.tmp, _RUN_A_ID, "no-such-id")

        self.assertEqual(diffs, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("Diff failed", errors[0])

    def test_open_diff_with_missing_db_emits_error(self):
        ctrl = DiffController(
            db_path_resolver=lambda _proj: self.tmp / "nonexistent.duckdb",
        )
        errors: list[str] = []
        ctrl.error.connect(errors.append)
        ctrl.open_diff(self.tmp, _RUN_A_ID, _RUN_B_ID)
        self.assertEqual(len(errors), 1)

    def test_open_diff_carries_netlist_diff(self):
        self._ingest(_RUN_A_ID, [], netlist_text="* netlist v1\n")
        self._ingest(_RUN_B_ID, [], netlist_text="* netlist v2\n")
        self._close_write_con()
        ctrl = self._make_controller()
        seen: list[DiffTab] = []
        ctrl.diff_ready.connect(seen.append)

        ctrl.open_diff(self.tmp, _RUN_A_ID, _RUN_B_ID)

        self.assertEqual(len(seen), 1)
        diff_text = seen[0].diff_result.netlist.diff_text
        self.assertIsNotNone(diff_text)
        self.assertIn("-* netlist v1", diff_text)
        self.assertIn("+* netlist v2", diff_text)


class DiffControllerLoadRunsTests(DiffControllerTestBase):
    """The internal helper that powers the picker."""

    def test_load_runs_returns_dicts_in_descending_timestamp(self):
        self._ingest(_RUN_A_ID, [], timestamp="2026-05-10T12:00:00+08:00",
                     label="golden")
        self._ingest(_RUN_B_ID, [], timestamp="2026-05-11T12:00:00+08:00")
        self._close_write_con()
        ctrl = self._make_controller()
        rows = ctrl._load_runs(self.tmp)  # pylint: disable=protected-access
        self.assertEqual(len(rows), 2)
        # Newer first.
        self.assertEqual(rows[0]["run_id"], _RUN_B_ID)
        self.assertEqual(rows[1]["run_id"], _RUN_A_ID)
        # short_id is first 8 chars.
        self.assertEqual(rows[0]["short_id"], _RUN_B_ID[:8])
        # Label round-trips.
        self.assertEqual(rows[1]["label"], "golden")
        self.assertIsNone(rows[0]["label"])

    def test_load_runs_empty_db_returns_empty(self):
        self._close_write_con()
        ctrl = self._make_controller()
        self.assertEqual(ctrl._load_runs(self.tmp), [])  # noqa: SLF001


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
