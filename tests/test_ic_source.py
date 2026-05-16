"""Unit tests for simkit.ic_source (Phase 3A v1.2 `ic_from` path resolver).

DECISIONS #57 + docs/phase3a_orchestrator_spec.md §2.5.

Synthetic temp-dir cases pin every error + auto-detect branch; a final
"live fixture" smoke test runs only when the user's ``simkit_verify``
result tree exists on disk (it's the real per-corner layout the resolver
was reverse-engineered from).

Run with:

    PYTHONPATH=python python3.11 -m pytest tests/test_ic_source.py -v
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.ic_source import (  # noqa: E402
    IcSourceError,
    ResolvedIcPath,
    enumerate_corner_dirs,
    resolve_ic_path,
)


# Live fixture: the user's simkit_verify history. 7 corners (/1.../7), each
# with Test/netlist/spectre.{ic,fc,dc}. Reverse-engineered the resolver
# from this layout — keep one smoke test that runs against it when present.
_LIVE_RESULTS_ROOT = Path(
    "/home/yusheng/simulation/sim_yusheng/Test/maestro/results/maestro"
)
_LIVE_HISTORY = "simkit_verify"


class _SyntheticTreeMixin:
    """Builds a fake results tree under ``self.tmp`` matching the
    Spectre per-corner layout the resolver targets.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_ic_"))
        self.hist = "fake_hist"
        self.results_root = self.tmp

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_corner(self, idx: int, test: str = "Test", *,
                     subdir: str = "netlist", kinds=("fc", "ic", "dc")):
        d = self.tmp / self.hist / str(idx) / test / subdir
        d.mkdir(parents=True, exist_ok=True)
        for kind in kinds:
            (d / f"spectre.{kind}").write_text("# fake spectre data\n")
        return d


class HappyPathTests(_SyntheticTreeMixin, unittest.TestCase):
    def test_finds_fc_under_netlist(self):
        self._make_corner(1)
        r = resolve_ic_path(self.results_root, self.hist, 1, "Test", "fc")
        self.assertIsNotNone(r)
        self.assertIsInstance(r, ResolvedIcPath)
        self.assertTrue(r.abs_path.is_file())
        self.assertEqual(r.abs_path.name, "spectre.fc")
        self.assertEqual(r.subdir, "netlist")

    def test_all_three_file_kinds(self):
        self._make_corner(3)
        for kind in ("fc", "ic", "dc"):
            with self.subTest(kind=kind):
                r = resolve_ic_path(self.results_root, self.hist, 3, "Test", kind)
                self.assertIsNotNone(r)
                self.assertEqual(r.abs_path.name, f"spectre.{kind}")

    def test_returns_absolute_path(self):
        self._make_corner(1)
        r = resolve_ic_path(self.results_root, self.hist, 1, "Test", "fc")
        self.assertTrue(r.abs_path.is_absolute())


class SubdirAutoDetectTests(_SyntheticTreeMixin, unittest.TestCase):
    def test_falls_back_to_psf_when_netlist_absent(self):
        self._make_corner(1, subdir="psf")  # no netlist/ in this corner
        r = resolve_ic_path(self.results_root, self.hist, 1, "Test", "fc")
        self.assertIsNotNone(r)
        self.assertEqual(r.subdir, "psf")

    def test_netlist_wins_when_both_present(self):
        # If both netlist and psf coexist (unlikely in practice but worth
        # pinning), candidate ORDER decides. netlist comes first in the
        # default registry.
        self._make_corner(1, subdir="netlist")
        self._make_corner(1, subdir="psf")
        r = resolve_ic_path(self.results_root, self.hist, 1, "Test", "fc")
        self.assertEqual(r.subdir, "netlist")

    def test_explicit_subdir_pin(self):
        # When explicit_subdir is set, ONLY that subdir is tried — even
        # if another known candidate has the file.
        self._make_corner(1, subdir="netlist")
        self._make_corner(1, subdir="psf")
        r = resolve_ic_path(
            self.results_root, self.hist, 1, "Test", "fc",
            explicit_subdir="psf",
        )
        self.assertEqual(r.subdir, "psf")

    def test_explicit_subdir_with_missing_file_returns_none(self):
        # User pins a subdir that doesn't have the file. We do NOT fall
        # back; we return None so the orchestrator can WARN with the
        # exact pin the user set.
        self._make_corner(1, subdir="netlist")
        r = resolve_ic_path(
            self.results_root, self.hist, 1, "Test", "fc",
            explicit_subdir="bogus_subdir",
        )
        self.assertIsNone(r)

    def test_custom_candidate_list_accepted(self):
        # Lets us support new simulators without code change.
        self._make_corner(1, subdir="weirdsim_out")
        r = resolve_ic_path(
            self.results_root, self.hist, 1, "Test", "fc",
            subdir_candidates=("netlist", "psf", "weirdsim_out"),
        )
        self.assertEqual(r.subdir, "weirdsim_out")


class NotFoundTests(_SyntheticTreeMixin, unittest.TestCase):
    """All return-None cases (which the orchestrator treats as naked-retry)."""

    def test_missing_corner_dir_returns_none(self):
        # The history exists but corner 5 doesn't.
        self._make_corner(1)
        r = resolve_ic_path(self.results_root, self.hist, 5, "Test", "fc")
        self.assertIsNone(r)

    def test_missing_test_dir_returns_none(self):
        self._make_corner(1)
        r = resolve_ic_path(self.results_root, self.hist, 1, "OtherTest", "fc")
        self.assertIsNone(r)

    def test_file_kind_absent_returns_none(self):
        # Spectre didn't write the .dc file for this corner.
        self._make_corner(1, kinds=("fc", "ic"))
        r = resolve_ic_path(self.results_root, self.hist, 1, "Test", "dc")
        self.assertIsNone(r)


class MisuseRaisesTests(_SyntheticTreeMixin, unittest.TestCase):
    """Distinct from "file missing" — these are caller bugs, not runtime events."""

    def test_invalid_file_kind_raises(self):
        with self.assertRaises(IcSourceError):
            resolve_ic_path(self.results_root, self.hist, 1, "Test", "xyz")

    def test_corner_idx_zero_raises(self):
        with self.assertRaises(IcSourceError):
            resolve_ic_path(self.results_root, self.hist, 0, "Test", "fc")

    def test_missing_history_dir_raises(self):
        # The whole history dir is absent → caller passed a wrong name.
        with self.assertRaises(IcSourceError):
            resolve_ic_path(self.results_root, "nope_hist", 1, "Test", "fc")

    def test_empty_test_name_raises(self):
        with self.assertRaises(IcSourceError):
            resolve_ic_path(self.results_root, self.hist, 1, "", "fc")


class EnumerateCornerDirsTests(_SyntheticTreeMixin, unittest.TestCase):
    def test_returns_sorted_int_list(self):
        for i in (3, 1, 7, 2):
            self._make_corner(i)
        self.assertEqual(
            enumerate_corner_dirs(self.results_root, self.hist),
            [1, 2, 3, 7],
        )

    def test_ignores_non_numeric_dirs(self):
        # Maestro sometimes puts sharedData / psf / wavedb siblings under
        # the history dir; the corner enumerator must skip them.
        self._make_corner(1)
        (self.tmp / self.hist / "sharedData").mkdir()
        (self.tmp / self.hist / "psf").mkdir()
        self.assertEqual(
            enumerate_corner_dirs(self.results_root, self.hist), [1],
        )

    def test_missing_history_raises(self):
        with self.assertRaises(IcSourceError):
            enumerate_corner_dirs(self.results_root, "nope")


class LiveFixtureSmokeTest(unittest.TestCase):
    """Runs only when the user's simkit_verify result tree is present."""

    def setUp(self):
        if not (_LIVE_RESULTS_ROOT / _LIVE_HISTORY).is_dir():
            self.skipTest(
                f"live fixture {_LIVE_RESULTS_ROOT / _LIVE_HISTORY} "
                f"not present on this host"
            )

    def test_simkit_verify_seven_corners(self):
        idxs = enumerate_corner_dirs(_LIVE_RESULTS_ROOT, _LIVE_HISTORY)
        # Reverse-engineered baseline: 7 corners.
        self.assertEqual(idxs, list(range(1, 8)))

    def test_simkit_verify_corner_1_has_all_three_kinds(self):
        for kind in ("fc", "ic", "dc"):
            with self.subTest(kind=kind):
                r = resolve_ic_path(
                    _LIVE_RESULTS_ROOT, _LIVE_HISTORY, 1, "Test", kind,
                )
                self.assertIsNotNone(r, f"spectre.{kind} not found at /1")
                self.assertEqual(r.subdir, "netlist")
                self.assertTrue(r.abs_path.is_file())


if __name__ == "__main__":
    unittest.main()
