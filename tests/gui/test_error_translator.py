"""pytest-qt tests for :mod:`simkit.gui.controllers.error_translator`.

Round-trips fake ``op_failed`` emissions through :class:`ErrorTranslator`
into the ``translated`` signal and asserts the payload is a
:class:`TranslatedError` with the expected category-mapped headline.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


# Headless Qt rendering for the red-zone test matrix (spec §18.3).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "python"))

import pytest  # noqa: E402

pytest.importorskip("pytestqt")
pytest.importorskip("PyQt5")

from PyQt5.QtCore import QObject, pyqtSignal  # noqa: E402

from simkit.gui.bridge_worker import BridgeError  # noqa: E402
from simkit.gui.controllers.error_translator import ErrorTranslator  # noqa: E402
from simkit.gui.error_translation import TranslatedError  # noqa: E402


class _FakeBridgeWorker(QObject):
    """Minimal stand-in that emits ``op_failed(int, object)``."""

    op_failed = pyqtSignal(int, object)


@pytest.fixture
def translator(qtbot):
    obj = ErrorTranslator()
    return obj


@pytest.fixture
def wired(qtbot, translator):
    worker = _FakeBridgeWorker()
    worker.op_failed.connect(translator.on_op_failed)
    return worker, translator


def test_known_error_round_trips_to_translated_signal(qtbot, wired):
    worker, translator = wired
    err = BridgeError(category="lock_failed", message="history missing")
    with qtbot.waitSignal(translator.translated, timeout=1000) as blocker:
        worker.op_failed.emit(42, err)
    req_id, payload = blocker.args
    assert req_id == 42
    assert isinstance(payload, TranslatedError)
    assert payload.is_known is True
    assert "history lock" in payload.headline


def test_unknown_error_round_trips_with_fallthrough_payload(qtbot, wired):
    worker, translator = wired
    err = BridgeError(category="totally_made_up", message="surprise")
    with qtbot.waitSignal(translator.translated, timeout=1000) as blocker:
        worker.op_failed.emit(7, err)
    req_id, payload = blocker.args
    assert req_id == 7
    assert payload.is_known is False
    assert "totally_made_up" in payload.headline
    assert "Report this" in payload.action_hint


def test_request_id_is_preserved_per_call(qtbot, wired):
    worker, translator = wired
    seen: list[tuple[int, TranslatedError]] = []
    translator.translated.connect(lambda rid, payload: seen.append((rid, payload)))

    # Emit two ops in quick succession; each must produce one translation
    # carrying its own request_id.
    err1 = BridgeError(category="pvt_runner_no_session", message="")
    err2 = BridgeError(category="lock_failed", message="")
    worker.op_failed.emit(1, err1)
    worker.op_failed.emit(2, err2)
    qtbot.waitUntil(lambda: len(seen) == 2, timeout=1000)

    rids = [rid for rid, _ in seen]
    assert rids == [1, 2]
    assert seen[0][1].is_known is True
    assert seen[1][1].is_known is True
    assert seen[0][1].headline != seen[1][1].headline


def test_non_bridge_error_payload_is_wrapped(qtbot, translator):
    # If something other than a BridgeError comes through (e.g. a raw
    # exception or string from a malformed caller), the translator must
    # still produce a TranslatedError rather than crash.
    with qtbot.waitSignal(translator.translated, timeout=1000) as blocker:
        translator.on_op_failed(99, RuntimeError("raw exc"))
    req_id, payload = blocker.args
    assert req_id == 99
    assert isinstance(payload, TranslatedError)
    assert payload.is_known is False


def test_non_exception_payload_is_wrapped(qtbot, translator):
    with qtbot.waitSignal(translator.translated, timeout=1000) as blocker:
        translator.on_op_failed(100, "just a string")
    req_id, payload = blocker.args
    assert req_id == 100
    assert isinstance(payload, TranslatedError)
    assert payload.is_known is False


def test_substring_match_routes_correctly(qtbot, wired):
    worker, translator = wired
    err = BridgeError(
        category="pvt_runner_timeout",
        message="ASSEMBLER-2423: setupdb temporarily locked",
    )
    with qtbot.waitSignal(translator.translated, timeout=1000) as blocker:
        worker.op_failed.emit(5, err)
    _, payload = blocker.args
    assert "对话框" in payload.headline
