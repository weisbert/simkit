"""Unit tests for :mod:`simkit.gui.results_model`.

Two layers:

* :class:`ResultsModel` — covered with PyQt5 in-process. The local
  ``.venv`` ships PyQt5 5.15.11 + ``QT_QPA_PLATFORM=offscreen`` works,
  so we set the env var and build a ``QApplication`` at module import.
  Pure model-layer assertions — no widgets, no event loop.

* :func:`load_rows_for_run` — covered against an in-memory DuckDB. No
  skillbridge, no on-disk state. Bootstraps the schema via
  :func:`simkit.db.bootstrap` and inserts a small fixture.

Why not mock PyQt5 by ``__new__`` like ``test_bridge_worker.py``: the
model is a thin wrapper around ``QAbstractTableModel`` whose
``rowCount`` / ``columnCount`` / ``data`` overrides interact with Qt's
C-level model machinery via ``index()``. The wrapper-bypass trick we
use for ``BridgeWorker`` (manual field init) does not cover that
machinery cleanly, so we just run a real QApplication.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

# Headless Qt — must be set BEFORE PyQt5 import.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "python"))

from PyQt5.QtCore import QModelIndex, Qt  # noqa: E402
from PyQt5.QtGui import QBrush, QColor  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from simkit.db import bootstrap, connect  # noqa: E402
from simkit.gui.results_model import (  # noqa: E402
    ResultsModel,
    apply_spec_to_output,
    load_rows_for_run,
)


# One QApplication per process — required for any QObject allocation
# (QBrush in our case). Idempotent: re-use the existing instance if a
# sibling test already constructed one.
_QAPP = QApplication.instance() or QApplication(sys.argv)


# A small fixture: 3 rows covering pass, status-fail, and spec-fail
# (where status="pass" but spec_status="fail" — the v1.4 spec-eval
# layer fails the verdict even though Spectre reports pass).
_FIXTURE_ROWS = [
    {
        "corner": "TT_pvt",
        "test": "pn",
        "output": "PN_1M",
        "value": -125.0,
        "status": "pass",
        "spec": "< -100",
        "spec_status": "pass",
    },
    {
        "corner": "SS_pvt",
        "test": "pn",
        "output": "PN_1M",
        "value": -95.0,
        "status": "fail",
        "spec": "< -100",
        "spec_status": "fail",
    },
    {
        "corner": "FF_pvt",
        "test": "max_freq",
        "output": "f_max",
        "value": None,
        "status": "pass",
        "spec": None,
        "spec_status": "no_spec",
    },
]


class ResultsModelBasicTests(unittest.TestCase):
    """rowCount / columnCount / headerData / DisplayRole."""

    def test_row_count_matches_fixture(self):
        model = ResultsModel(_FIXTURE_ROWS)
        self.assertEqual(model.rowCount(), 3)
        # rowCount with a non-root parent is always 0 in a flat table.
        self.assertEqual(model.rowCount(model.index(0, 0)), 0)

    def test_column_count_is_seven(self):
        # Columns spec'd in Stage-2: corner, test, output, value, status,
        # spec, spec_status — exactly seven.
        model = ResultsModel(_FIXTURE_ROWS)
        self.assertEqual(model.columnCount(), 7)

    def test_header_data_horizontal_returns_column_names(self):
        model = ResultsModel(_FIXTURE_ROWS)
        headers = [
            model.headerData(i, Qt.Horizontal, Qt.DisplayRole)
            for i in range(model.columnCount())
        ]
        self.assertEqual(
            headers,
            ["corner", "test", "output", "value", "status", "spec", "spec_status"],
        )

    def test_header_data_other_role_returns_none(self):
        model = ResultsModel(_FIXTURE_ROWS)
        # EditRole is not part of our contract; should be None.
        self.assertIsNone(model.headerData(0, Qt.Horizontal, Qt.EditRole))

    def test_display_role_returns_string_value(self):
        model = ResultsModel(_FIXTURE_ROWS)
        # Row 0 / col 0 = corner column = "TT_pvt".
        self.assertEqual(
            model.data(model.index(0, 0), Qt.DisplayRole), "TT_pvt",
        )
        # Row 0 / col 4 = status = "pass".
        self.assertEqual(
            model.data(model.index(0, 4), Qt.DisplayRole), "pass",
        )

    def test_display_role_none_renders_as_em_dash(self):
        model = ResultsModel(_FIXTURE_ROWS)
        # Row 2: value=None, spec=None.
        # Column 3 = "value", column 5 = "spec".
        self.assertEqual(
            model.data(model.index(2, 3), Qt.DisplayRole), "—",
        )
        self.assertEqual(
            model.data(model.index(2, 5), Qt.DisplayRole), "—",
        )

    def test_invalid_index_returns_none(self):
        model = ResultsModel(_FIXTURE_ROWS)
        self.assertIsNone(model.data(QModelIndex(), Qt.DisplayRole))


class ResultsModelBackgroundRoleTests(unittest.TestCase):
    """BackgroundRole highlight rules — light-red on fail, default else."""

    def _bg(self, model: ResultsModel, row: int):
        return model.data(model.index(row, 0), Qt.BackgroundRole)

    def test_status_fail_row_gets_light_red_brush(self):
        # Row 1 has status="fail".
        model = ResultsModel(_FIXTURE_ROWS)
        brush = self._bg(model, 1)
        self.assertIsInstance(brush, QBrush)
        self.assertEqual(brush.color(), QColor(255, 220, 220))

    def test_spec_status_fail_row_gets_light_red_brush(self):
        # status="pass" but spec_status="fail" — still highlight.
        rows = [
            {
                "corner": "TT", "test": "pn", "output": "x",
                "value": -90, "status": "pass",
                "spec": "< -100", "spec_status": "fail",
            },
        ]
        model = ResultsModel(rows)
        brush = self._bg(model, 0)
        self.assertIsInstance(brush, QBrush)
        self.assertEqual(brush.color(), QColor(255, 220, 220))

    def test_spec_status_eval_err_row_gets_light_red_brush(self):
        # spec_status="eval_err" — the spec string couldn't be evaluated
        # (e.g. unbound variable). Treat as failed (per spec §4 stage-2).
        rows = [
            {
                "corner": "TT", "test": "pn", "output": "x",
                "value": -90, "status": "pass",
                "spec": "< $missing", "spec_status": "eval_err",
            },
        ]
        model = ResultsModel(rows)
        brush = self._bg(model, 0)
        self.assertIsInstance(brush, QBrush)
        self.assertEqual(brush.color(), QColor(255, 220, 220))

    def test_passing_row_has_default_background(self):
        # Row 0: status=pass, spec_status=pass — no highlight.
        model = ResultsModel(_FIXTURE_ROWS)
        self.assertIsNone(self._bg(model, 0))
        # Row 2: status=pass, spec_status=no_spec — no highlight.
        self.assertIsNone(self._bg(model, 2))


class LoadRowsForRunTests(unittest.TestCase):
    """DuckDB integration — :func:`load_rows_for_run` shape + values."""

    def _con_with_results(self):
        """Build an in-memory DuckDB pre-loaded with 2 result rows."""
        con = connect(":memory:")
        bootstrap(con)
        # One run row (FK-equivalent — the DDL doesn't enforce it but
        # the read shouldn't care).
        con.execute(
            """
            INSERT INTO runs(
              run_id, project_id, testbench_id, timestamp,
              author, history_name, schema_version, ingested_at
            )
            VALUES
              ('R1', 'projA', 'tbA', TIMESTAMPTZ '2026-05-19 12:00:00+00',
               'tester', 'h1', 3, TIMESTAMPTZ '2026-05-19 12:00:00+00')
            """
        )
        # Two results: one numeric/pass, one string/fail-by-spec.
        con.execute(
            """
            INSERT INTO results(
              run_id, point, corner, test, output,
              value_num, value_str, status, sweep, corner_vars,
              test_note, spec, spec_status
            ) VALUES
              ('R1', 0, 'TT_pvt', 'pn', 'PN_1M',
               -125.0, NULL, 'pass', '{}', '{}',
               NULL, '< -100', 'pass'),
              ('R1', 0, 'SS_pvt', 'pn', 'PN_1M',
               NULL, 'inf', 'fail', '{}', '{}',
               NULL, '< -100', 'fail')
            """
        )
        return con

    def test_load_rows_returns_dicts_in_corner_order(self):
        con = self._con_with_results()
        try:
            rows = load_rows_for_run(con, "R1")
        finally:
            con.close()
        self.assertEqual(len(rows), 2)
        # Sorted by corner ASC: SS_pvt < TT_pvt alphabetically.
        self.assertEqual(rows[0]["corner"], "SS_pvt")
        self.assertEqual(rows[1]["corner"], "TT_pvt")

    def test_load_rows_keys_match_model_columns(self):
        con = self._con_with_results()
        try:
            rows = load_rows_for_run(con, "R1")
        finally:
            con.close()
        for r in rows:
            self.assertEqual(
                set(r.keys()),
                set(ResultsModel.COLUMNS),
            )

    def test_load_rows_merges_numeric_and_string_value(self):
        con = self._con_with_results()
        try:
            rows = load_rows_for_run(con, "R1")
        finally:
            con.close()
        # SS_pvt (first by sort) has value_str='inf'.
        self.assertEqual(rows[0]["value"], "inf")
        # TT_pvt has value_num=-125.0.
        self.assertEqual(rows[1]["value"], -125.0)

    def test_load_rows_empty_for_unknown_run(self):
        con = self._con_with_results()
        try:
            rows = load_rows_for_run(con, "does_not_exist")
        finally:
            con.close()
        self.assertEqual(rows, [])

    def test_load_rows_then_feed_model_round_trips_through_qt(self):
        """End-to-end: DB → load_rows_for_run → ResultsModel → Qt data()."""
        con = self._con_with_results()
        try:
            rows = load_rows_for_run(con, "R1")
        finally:
            con.close()
        model = ResultsModel(rows)
        self.assertEqual(model.rowCount(), 2)
        # First row (SS_pvt, status=fail) should highlight.
        brush = model.data(model.index(0, 0), Qt.BackgroundRole)
        self.assertIsInstance(brush, QBrush)
        # Second row (TT_pvt, all pass) should not.
        self.assertIsNone(model.data(model.index(1, 0), Qt.BackgroundRole))


class ApplySpecToOutputTests(unittest.TestCase):
    """:func:`apply_spec_to_output` — in-place spec re-evaluation (G-1b)."""

    def _con_no_specs(self):
        """In-memory DuckDB, run 'R0', output 'PN_1M' across 2 corners,
        no specs yet (spec NULL, spec_status 'no_spec')."""
        con = connect(":memory:")
        bootstrap(con)
        con.execute(
            """
            INSERT INTO runs(
              run_id, project_id, testbench_id, timestamp,
              author, history_name, schema_version, ingested_at
            ) VALUES
              ('R0', 'projA', 'tbA', TIMESTAMPTZ '2026-05-19 12:00:00+00',
               'tester', 'h0', 3, TIMESTAMPTZ '2026-05-19 12:00:00+00')
            """
        )
        con.execute(
            """
            INSERT INTO results(
              run_id, point, corner, test, output,
              value_num, value_str, status, sweep, corner_vars,
              test_note, spec, spec_status
            ) VALUES
              ('R0', 0, 'TT', 'pn', 'PN_1M',
               -125.0, NULL, 'pass', '{}', '{}', NULL, NULL, 'no_spec'),
              ('R0', 0, 'SS', 'pn', 'PN_1M',
               -95.0, NULL, 'pass', '{}', '{}', NULL, NULL, 'no_spec'),
              ('R0', 0, 'TT', 'pn', 'gain',
               20.0, NULL, 'pass', '{}', '{}', NULL, NULL, 'no_spec')
            """
        )
        return con

    def test_apply_spec_sets_spec_and_reevaluates_per_row(self):
        con = self._con_no_specs()
        try:
            n = apply_spec_to_output(con, "R0", "PN_1M", "< -100")
            rows = load_rows_for_run(con, "R0")
        finally:
            con.close()
        self.assertEqual(n, 2)  # only the two PN_1M rows
        verdicts = {r["corner"]: r["spec_status"] for r in rows
                    if r["output"] == "PN_1M"}
        # TT value -125 < -100 → pass; SS value -95 → fail.
        self.assertEqual(verdicts, {"TT": "pass", "SS": "fail"})
        # The untouched 'gain' output keeps its no_spec verdict.
        gain = [r for r in rows if r["output"] == "gain"][0]
        self.assertEqual(gain["spec_status"], "no_spec")

    def test_apply_spec_does_not_touch_recorded_value(self):
        con = self._con_no_specs()
        try:
            apply_spec_to_output(con, "R0", "PN_1M", "< -100")
            rows = load_rows_for_run(con, "R0")
        finally:
            con.close()
        vals = {r["corner"]: r["value"] for r in rows
                if r["output"] == "PN_1M"}
        self.assertEqual(vals, {"TT": -125.0, "SS": -95.0})

    def test_apply_empty_spec_clears_back_to_no_spec(self):
        con = self._con_no_specs()
        try:
            apply_spec_to_output(con, "R0", "PN_1M", "< -100")
            apply_spec_to_output(con, "R0", "PN_1M", "")
            rows = load_rows_for_run(con, "R0")
        finally:
            con.close()
        for r in rows:
            if r["output"] == "PN_1M":
                self.assertIsNone(r["spec"])
                self.assertEqual(r["spec_status"], "no_spec")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
