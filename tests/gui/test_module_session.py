"""Unit tests for :mod:`simkit.gui.module_session`.

Pure-Python — no PyQt5 import. Covers:
  * Round-trip ``to_dict`` -> ``from_dict``
  * Disk round-trip via ``save_session`` / ``load_session``
  * Stale / missing / corrupted state file falls back to an empty session
  * Schema version is written + accepted on read
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.gui.module_session import (  # noqa: E402
    SCHEMA_VERSION,
    ModuleSession,
    TreeState,
    load_session,
    save_session,
    state_file_for,
)


class ModuleSessionDictRoundTripTests(unittest.TestCase):

    def test_empty_round_trip(self):
        sess = ModuleSession(project_path=Path("/tmp/some.pvtproject"))
        data = sess.to_dict()
        self.assertEqual(data["schema_version"], SCHEMA_VERSION)
        rebuilt = ModuleSession.from_dict(
            data, project_path=Path("/tmp/some.pvtproject"),
        )
        self.assertEqual(rebuilt.last_selected_review, None)
        self.assertEqual(rebuilt.left_tree.expanded, [])
        self.assertEqual(rebuilt.dirty_editors, {})

    def test_populated_round_trip(self):
        sess = ModuleSession(
            project_path=Path("/tmp/p.pvtproject"),
            project_name="p",
            last_selected_review="reviews/pn_review_v3.review.json",
            left_tree=TreeState(
                expanded=["Reviews", "Milestones"],
                selected_path=["Reviews", "pn_review_v3.review.json"],
            ),
            active_baseline="CDR-2026q2",
            last_run_id_viewed="abc12345",
        )
        data = sess.to_dict()
        rebuilt = ModuleSession.from_dict(
            data, project_path=sess.project_path, project_name=sess.project_name,
        )
        self.assertEqual(rebuilt.last_selected_review, sess.last_selected_review)
        self.assertEqual(rebuilt.left_tree.expanded, sess.left_tree.expanded)
        self.assertEqual(
            rebuilt.left_tree.selected_path, sess.left_tree.selected_path,
        )
        self.assertEqual(rebuilt.active_baseline, sess.active_baseline)
        self.assertEqual(rebuilt.last_run_id_viewed, sess.last_run_id_viewed)

    def test_from_dict_tolerates_garbage(self):
        # spec §7.1: "Module gone / corrupted state: fall back to empty
        # session. Never crash on stale state."
        for raw in [None, "string-not-dict", 42, []]:
            rebuilt = ModuleSession.from_dict(
                raw, project_path=Path("/tmp/x.pvtproject"),
            )
            self.assertEqual(rebuilt.last_selected_review, None)
            self.assertEqual(rebuilt.left_tree.expanded, [])

    def test_from_dict_drops_unknown_keys(self):
        raw = {
            "schema_version": SCHEMA_VERSION,
            "last_selected_review": "r.json",
            "future_field": {"will": "be ignored"},
        }
        rebuilt = ModuleSession.from_dict(
            raw, project_path=Path("/tmp/x.pvtproject"),
        )
        self.assertEqual(rebuilt.last_selected_review, "r.json")


class ModuleSessionDiskRoundTripTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_gui_msess_"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_state_file_for_uses_dot_simkit_subdir(self):
        # project_path is the .pvtproject file inside a project dir.
        proj_file = self.tmp / "ndiv.pvtproject"
        proj_file.write_text("{}")
        path = state_file_for(proj_file)
        self.assertEqual(path, self.tmp / ".simkit" / "gui_state.json")

    def test_state_file_for_when_given_a_directory(self):
        # Some callers pass a dir directly (not the .pvtproject file).
        path = state_file_for(self.tmp)
        self.assertEqual(path, self.tmp / ".simkit" / "gui_state.json")

    def test_save_then_load_round_trip(self):
        proj_file = self.tmp / "ndiv.pvtproject"
        proj_file.write_text("{}")
        sess = ModuleSession(
            project_path=proj_file,
            project_name="ndiv",
            last_selected_review="reviews/x.review.json",
            active_baseline="CDR-2026q2",
        )
        written = save_session(sess)
        self.assertTrue(written.exists())
        # File is valid JSON with the pinned schema version.
        loaded_raw = json.loads(written.read_text(encoding="utf-8"))
        self.assertEqual(loaded_raw["schema_version"], SCHEMA_VERSION)
        # Round-trip back into a ModuleSession matches.
        loaded = load_session(proj_file, project_name="ndiv")
        self.assertEqual(loaded.last_selected_review, sess.last_selected_review)
        self.assertEqual(loaded.active_baseline, sess.active_baseline)
        self.assertEqual(loaded.project_path, proj_file)

    def test_load_session_missing_file_returns_empty_session(self):
        proj_file = self.tmp / "ndiv.pvtproject"
        proj_file.write_text("{}")
        # No save_session() call beforehand.
        loaded = load_session(proj_file, project_name="ndiv")
        self.assertIsNone(loaded.last_selected_review)
        self.assertEqual(loaded.left_tree.expanded, [])
        self.assertEqual(loaded.project_path, proj_file)

    def test_load_session_corrupted_file_returns_empty_session(self):
        proj_file = self.tmp / "ndiv.pvtproject"
        proj_file.write_text("{}")
        sf = state_file_for(proj_file)
        sf.parent.mkdir(parents=True, exist_ok=True)
        sf.write_text("THIS IS NOT JSON {{{")
        loaded = load_session(proj_file, project_name="ndiv")
        self.assertIsNone(loaded.last_selected_review)

    def test_save_is_atomic_no_tmp_left_over(self):
        proj_file = self.tmp / "ndiv.pvtproject"
        proj_file.write_text("{}")
        sess = ModuleSession(project_path=proj_file)
        save_session(sess)
        # No .tmp residue in the .simkit dir.
        state_dir = (self.tmp / ".simkit")
        for p in state_dir.iterdir():
            self.assertFalse(
                p.name.endswith(".tmp"),
                f"atomic-write left tmp file: {p}",
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
