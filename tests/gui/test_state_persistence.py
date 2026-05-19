"""Unit tests for :mod:`simkit.gui.state` — global ``gui_app.json``.

Pure Python; no PyQt5 import. Covers:
  * ``GuiAppState`` round-trip ``to_dict`` -> ``from_dict``
  * Disk round-trip via ``save_app_state`` / ``load_app_state``
  * ``SIMKIT_HOME`` env override
  * Missing / corrupt file falls back to empty state
  * ``push_recent`` ring-buffer behaviour (de-dupe, cap, ordering)
  * Forward-compat: unknown keys round-trip untouched
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.gui.state import (  # noqa: E402
    RECENT_MAX,
    SCHEMA_VERSION,
    GuiAppState,
    app_state_path,
    load_app_state,
    save_app_state,
)


class AppStatePathTests(unittest.TestCase):

    def test_default_path_under_home(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SIMKIT_HOME", None)
            self.assertEqual(
                app_state_path(), Path.home() / ".simkit" / "gui_app.json",
            )

    def test_simkit_home_env_override(self):
        with mock.patch.dict(os.environ, {"SIMKIT_HOME": "/tmp/x"}):
            self.assertEqual(app_state_path(), Path("/tmp/x") / "gui_app.json")


class GuiAppStateRoundTripTests(unittest.TestCase):

    def test_empty_to_dict_has_schema_version(self):
        st = GuiAppState()
        data = st.to_dict()
        self.assertEqual(data["schema_version"], SCHEMA_VERSION)
        self.assertEqual(data["recent_modules"], [])
        self.assertEqual(data["registered_modules"], [])

    def test_populated_round_trip(self):
        st = GuiAppState(
            last_visited="/p/ndiv.pvtproject",
            recent_modules=["/p/ndiv.pvtproject", "/p/cp.pvtproject"],
            registered_modules=["/p/ndiv.pvtproject", "/p/cp.pvtproject"],
            window_geometry="AAAA",
            window_state="BBBB",
        )
        data = st.to_dict()
        rebuilt = GuiAppState.from_dict(data)
        self.assertEqual(rebuilt.last_visited, st.last_visited)
        self.assertEqual(rebuilt.recent_modules, st.recent_modules)
        self.assertEqual(rebuilt.registered_modules, st.registered_modules)
        self.assertEqual(rebuilt.window_geometry, st.window_geometry)
        self.assertEqual(rebuilt.window_state, st.window_state)

    def test_from_dict_garbage_input_returns_empty(self):
        for raw in [None, "x", 5, []]:
            st = GuiAppState.from_dict(raw)
            self.assertIsNone(st.last_visited)
            self.assertEqual(st.recent_modules, [])

    def test_unknown_keys_round_trip_via_extra(self):
        raw = {
            "schema_version": SCHEMA_VERSION,
            "last_visited": "/x",
            "future_setting": {"foo": 1},
        }
        st = GuiAppState.from_dict(raw)
        self.assertEqual(st.extra.get("future_setting"), {"foo": 1})
        # And it survives a write.
        self.assertEqual(
            st.to_dict().get("future_setting"), {"foo": 1},
        )


class PushRecentTests(unittest.TestCase):

    def test_push_recent_prepends(self):
        st = GuiAppState()
        st.push_recent("/p/a.pvtproject")
        st.push_recent("/p/b.pvtproject")
        self.assertEqual(
            st.recent_modules,
            ["/p/b.pvtproject", "/p/a.pvtproject"],
        )

    def test_push_recent_dedupes_existing(self):
        st = GuiAppState(recent_modules=["/p/a", "/p/b", "/p/c"])
        st.push_recent("/p/b")
        # /p/b moves to front; /p/a and /p/c keep their relative order.
        self.assertEqual(st.recent_modules, ["/p/b", "/p/a", "/p/c"])

    def test_push_recent_caps_at_recent_max(self):
        st = GuiAppState()
        for i in range(RECENT_MAX + 3):
            st.push_recent(f"/p/m{i}")
        self.assertEqual(len(st.recent_modules), RECENT_MAX)
        # Newest first.
        self.assertEqual(st.recent_modules[0], f"/p/m{RECENT_MAX + 2}")


class DiskRoundTripTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_gui_state_"))
        self.path = self.tmp / "gui_app.json"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_save_then_load_round_trip(self):
        st = GuiAppState(
            last_visited="/p/ndiv.pvtproject",
            recent_modules=["/p/ndiv.pvtproject"],
        )
        save_app_state(st, path=self.path)
        loaded = load_app_state(self.path)
        self.assertEqual(loaded.last_visited, "/p/ndiv.pvtproject")
        self.assertEqual(loaded.recent_modules, ["/p/ndiv.pvtproject"])

    def test_load_missing_file_returns_empty(self):
        loaded = load_app_state(self.path)
        self.assertIsNone(loaded.last_visited)
        self.assertEqual(loaded.recent_modules, [])

    def test_load_corrupt_file_returns_empty(self):
        self.path.write_text("{not json")
        loaded = load_app_state(self.path)
        self.assertIsNone(loaded.last_visited)

    def test_save_writes_schema_version(self):
        save_app_state(GuiAppState(), path=self.path)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(raw["schema_version"], SCHEMA_VERSION)

    def test_save_creates_parent_dir(self):
        nested = self.tmp / "nested" / "deeper" / "gui_app.json"
        save_app_state(GuiAppState(last_visited="/x"), path=nested)
        self.assertTrue(nested.is_file())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
