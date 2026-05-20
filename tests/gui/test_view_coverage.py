"""M2 hard gate — every GUI view must carry a view-layer render test.

Process mandate M2 (docs/dispatch_mandates.md): a ``model.rowCount() == N``
assertion does not prove the user can *see* N rows. Every ``gui/views/*.py``
added from Phase 5 onward must have a matching ``tests/gui/test_<view>.py``
containing at least one ``def test_*render*`` function that asserts a rendered
geometry property (rowHeight / sectionSize / visibleRegion).

This meta-test fails the whole suite if a non-grandfathered view lacks one.
The 11 Phase 4 views are grandfathered — the user decided 2026-05-20 not to
backfill them. A new view simply must not be added to ``_GRANDFATHERED``.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_VIEWS_DIR = _REPO_ROOT / "python" / "simkit" / "gui" / "views"
_GUI_TESTS_DIR = _REPO_ROOT / "tests" / "gui"

# Phase 4 views — grandfathered, not backfilled (user decision 2026-05-20).
# Do NOT add Phase 5 views here; they must ship with a render test instead.
_GRANDFATHERED = frozenset({
    "corners_editor",
    "diff_tab",
    "glossary_dialog",
    "measures_editor",
    "results_tab",
    "review_editor",
    "review_wizard",
    "run_picker",
    "run_progress",
    "summary_tab",
    "trend_tab",
})

_RENDER_TEST_RE = re.compile(r"^\s*def\s+test_\w*render\w*\s*\(", re.MULTILINE)


def _view_modules() -> list[str]:
    return sorted(
        p.stem
        for p in _VIEWS_DIR.glob("*.py")
        if p.stem != "__init__"
    )


def _has_render_test(view: str) -> bool:
    test_file = _GUI_TESTS_DIR / f"test_{view}.py"
    if not test_file.is_file():
        return False
    return bool(_RENDER_TEST_RE.search(test_file.read_text(encoding="utf-8")))


class ViewRenderCoverageTest(unittest.TestCase):
    def test_every_phase5_view_has_a_render_test(self):
        missing = [
            view
            for view in _view_modules()
            if view not in _GRANDFATHERED and not _has_render_test(view)
        ]
        self.assertEqual(
            missing,
            [],
            "M2 violation — these views lack a `def test_*render*` function in "
            f"tests/gui/test_<view>.py: {missing}. "
            "Add a pytest-qt test asserting a rendered geometry property "
            "(rowHeight / sectionSize / visibleRegion), not just rowCount(). "
            "See docs/dispatch_mandates.md M2.",
        )

    def test_grandfather_list_has_no_stale_entries(self):
        existing = set(_view_modules())
        stale = sorted(_GRANDFATHERED - existing)
        self.assertEqual(
            stale,
            [],
            f"_GRANDFATHERED names views that no longer exist: {stale}. "
            "Remove them so the gate stays honest.",
        )


if __name__ == "__main__":
    unittest.main()
