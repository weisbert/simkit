"""Qt helper: subscribe to a :class:`BridgeWorker`, translate ``op_failed``.

Kept separate from :class:`~simkit.gui.main_window.MainWindow` so the
translation is testable in isolation with pytest-qt's ``QSignalSpy``.
Phase 3 just wires ``worker.op_failed -> translator.on_op_failed`` and
``translator.translated -> main_window.show_translated_error``.
"""

from __future__ import annotations

from typing import Optional

try:
    from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot  # type: ignore[import-not-found]

    _QT_AVAILABLE = True
except ImportError:  # pragma: no cover — env-specific
    _QT_AVAILABLE = False

    class QObject:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "PyQt5 not installed; ErrorTranslator cannot be instantiated."
            )

    class _StubSignal:  # pylint: disable=too-few-public-methods
        def __init__(self, *args, **kwargs):
            pass

        def emit(self, *args, **kwargs):
            raise RuntimeError("PyQt5 not installed; signals unavailable.")

        def connect(self, *args, **kwargs):
            pass

    def pyqtSignal(*args, **kwargs):  # type: ignore[no-redef]
        return _StubSignal(*args, **kwargs)

    def pyqtSlot(*args, **kwargs):  # type: ignore[no-redef]
        def deco(fn):
            return fn

        return deco


from simkit.gui.bridge_worker import BridgeError
from simkit.gui.error_translation import TranslatedError, translate


class ErrorTranslator(QObject):
    """Translate :class:`BridgeError` payloads into :class:`TranslatedError`."""

    translated = pyqtSignal(int, object)  # (request_id, TranslatedError)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)

    @pyqtSlot(int, object)
    def on_op_failed(self, request_id: int, bridge_error: object) -> None:
        """Slot for :attr:`BridgeWorker.op_failed`. Translate, then re-emit."""
        if isinstance(bridge_error, BridgeError):
            err = bridge_error
        else:
            err = BridgeError.from_exception(
                bridge_error if isinstance(bridge_error, BaseException)
                else RuntimeError(str(bridge_error))
            )
        result: TranslatedError = translate(err)
        self.translated.emit(request_id, result)
