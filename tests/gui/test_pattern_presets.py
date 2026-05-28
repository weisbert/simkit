"""Unit tests for the user-level PVT pattern preset library."""

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

from simkit.corner_model import PvtCornerEntry, PvtPattern  # noqa: E402
from simkit.gui import pattern_presets as pp  # noqa: E402


def _pat(name="P", corners=None):
    if corners is None:
        corners = (
            PvtCornerEntry(
                enabled=True, name="c1",
                process_levels=("TT", "SS"),
                voltage_levels=("NV",),
                temperature_levels=("NT", "HT"),
            ),
        )
    return PvtPattern(enabled=True, name=name, corners=tuple(corners))


class PatternPresetsTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self._path = Path(self._dir) / "pattern_presets.json"

    def test_missing_file_loads_empty(self):
        self.assertEqual(pp.load_user_presets(self._path), {})

    def test_save_then_load_round_trip(self):
        pp.save_user_preset("Worst", _pat("ignored"), self._path)
        loaded = pp.load_user_presets(self._path)
        self.assertIn("Worst", loaded)
        # The pattern's name is forced to the preset key.
        self.assertEqual(loaded["Worst"].name, "Worst")
        self.assertEqual(loaded["Worst"].corners[0].process_levels,
                         ("TT", "SS"))

    def test_save_overwrites_same_name(self):
        pp.save_user_preset("X", _pat(corners=(
            PvtCornerEntry(enabled=True, name="a", process_levels=("TT",),
                           voltage_levels=(), temperature_levels=()),
        )), self._path)
        pp.save_user_preset("X", _pat(corners=(
            PvtCornerEntry(enabled=True, name="b", process_levels=("SS",),
                           voltage_levels=(), temperature_levels=()),
        )), self._path)
        loaded = pp.load_user_presets(self._path)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded["X"].corners[0].process_levels, ("SS",))

    def test_delete_removes_preset(self):
        pp.save_user_preset("A", _pat(), self._path)
        pp.save_user_preset("B", _pat(), self._path)
        pp.delete_user_preset("A", self._path)
        loaded = pp.load_user_presets(self._path)
        self.assertEqual(set(loaded), {"B"})

    def test_delete_missing_is_noop(self):
        pp.save_user_preset("A", _pat(), self._path)
        pp.delete_user_preset("nope", self._path)  # must not raise
        self.assertEqual(set(pp.load_user_presets(self._path)), {"A"})

    def test_corrupt_file_loads_empty(self):
        self._path.write_text("{ not valid json", encoding="utf-8")
        self.assertEqual(pp.load_user_presets(self._path), {})

    def test_multi_corner_preset_round_trips(self):
        pat = _pat("Multi", corners=(
            PvtCornerEntry(enabled=True, name="c1",
                           process_levels=("TT",), voltage_levels=("NV",),
                           temperature_levels=("NT",)),
            PvtCornerEntry(enabled=False, name="c2",
                           process_levels=("FF",), voltage_levels=("HV",),
                           temperature_levels=("LT", "HT")),
        ))
        pp.save_user_preset("Multi", pat, self._path)
        loaded = pp.load_user_presets(self._path)["Multi"]
        self.assertEqual(len(loaded.corners), 2)
        self.assertFalse(loaded.corners[1].enabled)
        self.assertEqual(loaded.corners[1].temperature_levels, ("LT", "HT"))

    def test_legacy_flat_preset_promoted(self):
        # A hand-written / older preset with flat level tuples loads as a
        # one-corner pattern.
        doc = {
            "schema_version": 1,
            "presets": {
                "Flat": {
                    "enabled": True, "name": "Flat",
                    "process_levels": ["TT", "SS"],
                    "voltage_levels": ["NV"],
                    "temperature_levels": ["NT"],
                },
            },
        }
        self._path.write_text(json.dumps(doc), encoding="utf-8")
        loaded = pp.load_user_presets(self._path)["Flat"]
        self.assertEqual(len(loaded.corners), 1)
        self.assertEqual(loaded.corners[0].process_levels, ("TT", "SS"))

    def test_simkit_home_override_picks_path(self):
        home = tempfile.mkdtemp()
        with mock.patch.dict(os.environ, {"SIMKIT_HOME": home}):
            self.assertEqual(
                pp.presets_path(),
                Path(home) / "pattern_presets.json",
            )


if __name__ == "__main__":
    unittest.main()
