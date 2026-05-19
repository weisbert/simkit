"""`RunController` — spawn `pvt run --gui-jsonl` + drain JSONL into Qt signals.

Phase 4 §9 (DECISIONS #77). One controller drives one ``pvt run``
subprocess at a time; ``MainWindow`` instantiates this on the
"Run this review" click and connects its signals to the progress panel.

The controller is intentionally thin: it spawns the QProcess, drains
``readyReadStandardOutput`` line-by-line, ``json.loads`` each non-empty
line, and re-emits each as a typed Qt signal. The kanban / log / verdict
logic all lives downstream — kept here so the controller stays unit-
testable without a running ``pvt`` machinery (the integration tests
spawn a tiny ``python -c "..."`` that just prints JSONL).

Cancel cascade (spec §9.3):
  1. ``cancel()`` calls ``QProcess.terminate()`` (SIGTERM on POSIX)
  2. 5-second grace timer (``QTimer.singleShot``)
  3. If still alive: ``QProcess.kill()`` (SIGKILL) + emit ``cancelled``

PyQt5 is imported at module load (signals are class-body declarations).
A guard fallback keeps non-GUI imports of ``simkit.gui.controllers``
non-fatal in the same shape as ``bridge_worker``.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, Optional


log = logging.getLogger(__name__)


try:
    from PyQt5.QtCore import (  # type: ignore[import-not-found]
        QObject,
        QProcess,
        QTimer,
        pyqtSignal,
        pyqtSlot,
    )

    _QT_AVAILABLE = True
except ImportError:  # pragma: no cover — env-specific
    _QT_AVAILABLE = False

    class _StubSignal:
        def __init__(self, *a, **k): pass
        def emit(self, *a, **k):
            raise RuntimeError("PyQt5 not installed; RunController unavailable.")
        def connect(self, *a, **k): pass

    class QObject:  # type: ignore[no-redef]
        def __init__(self, *a, **k):
            raise RuntimeError("PyQt5 not installed; RunController unavailable.")

    class QProcess:  # type: ignore[no-redef]
        MergedChannels = 2
        NotRunning = 0
        def __init__(self, *a, **k):
            raise RuntimeError("PyQt5 not installed; QProcess unavailable.")

    class QTimer:  # type: ignore[no-redef]
        @staticmethod
        def singleShot(ms, cb): pass

    def pyqtSignal(*a, **k):  # type: ignore[no-redef]
        return _StubSignal(*a, **k)

    def pyqtSlot(*a, **k):  # type: ignore[no-redef]
        def deco(fn): return fn
        return deco


_KILL_GRACE_MS = 5000


class RunController(QObject):
    """Spawn `pvt run --gui-jsonl` + parse JSONL stdout into Qt signals.

    One controller can drive ONE run at a time; :meth:`start_run` returns
    False (and emits ``error``) if called while another is in flight.
    """

    progress_event = pyqtSignal(dict)          # one parsed JSONL event
    run_finished = pyqtSignal(int, dict)       # (exit_code, summary_event)
    cancelled = pyqtSignal()                   # SIGKILL fired
    error = pyqtSignal(str)                    # spawn failed, etc.

    def __init__(
        self,
        *,
        python_exe: Optional[str] = None,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self._python_exe = python_exe or sys.executable
        self._proc: Optional[QProcess] = None
        self._buffer: bytearray = bytearray()
        self._last_review_done: dict = {}
        self._cancel_pending: bool = False

    # --- public API ------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.state() != QProcess.NotRunning

    def start_run(
        self,
        review_path: str,
        *,
        session: str,
        extra_args: Optional[list[str]] = None,
    ) -> bool:
        """Launch ``pvt run`` as a QProcess. Returns False if a run is in flight."""
        if self.is_running:
            self.error.emit("RunController: another run is in flight; ignored.")
            return False

        argv = [
            "-m", "simkit.cli", "run", review_path,
            "--session", session, "--gui-jsonl",
        ]
        if extra_args:
            argv.extend(extra_args)

        self._buffer.clear()
        self._last_review_done = {}
        self._cancel_pending = False

        self._proc = QProcess(self)
        self._proc.setProcessChannelMode(QProcess.MergedChannels)
        self._proc.readyReadStandardOutput.connect(self._on_ready_read)
        # PyQt5 QProcess.finished is overloaded; both 1-arg + 2-arg slots
        # exist. The 2-arg form (exit_code, exit_status) is the one Qt
        # actually emits on POSIX, so connect to that.
        self._proc.finished.connect(self._on_finished)
        self._proc.errorOccurred.connect(self._on_error_occurred)

        self._proc.start(self._python_exe, argv)
        if not self._proc.waitForStarted(3000):
            err = self._proc.errorString() if self._proc else "unknown"
            self.error.emit(f"RunController: QProcess failed to start: {err}")
            self._proc = None
            return False
        return True

    def cancel(self) -> None:
        """SIGTERM the QProcess; 5s grace then SIGKILL + emit ``cancelled``."""
        if not self.is_running or self._proc is None:
            return
        self._cancel_pending = True
        try:
            self._proc.terminate()  # SIGTERM on POSIX
        except Exception as exc:  # pragma: no cover — defensive
            log.warning("RunController: terminate raised: %s", exc)
        QTimer.singleShot(_KILL_GRACE_MS, self._sigkill_if_alive)

    # --- Qt slots --------------------------------------------------------

    @pyqtSlot()
    def _on_ready_read(self) -> None:
        if self._proc is None:
            return
        data = bytes(self._proc.readAllStandardOutput())
        if not data:
            return
        self._buffer.extend(data)
        # Drain by newline; keep trailing partial line for next read.
        while b"\n" in self._buffer:
            line_b, _, rest = self._buffer.partition(b"\n")
            self._buffer = bytearray(rest)
            line = line_b.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            self._handle_line(line)

    def _handle_line(self, line: str) -> None:
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            log.debug("RunController: skipping malformed JSONL: %s (%s)", line, exc)
            return
        if not isinstance(event, dict):
            log.debug("RunController: skipping non-object JSONL: %r", event)
            return
        if event.get("event") == "review_done":
            self._last_review_done = event
        self.progress_event.emit(event)

    @pyqtSlot(int, "QProcess::ExitStatus")
    def _on_finished(self, exit_code: int, _exit_status: Any) -> None:
        # Drain any trailing buffered bytes (no terminating newline).
        if self._buffer:
            tail = bytes(self._buffer).decode("utf-8", errors="replace").strip()
            self._buffer.clear()
            if tail:
                self._handle_line(tail)

        summary = dict(self._last_review_done)
        self._proc = None
        cancelled_now = self._cancel_pending
        self._cancel_pending = False
        if cancelled_now:
            self.cancelled.emit()
        self.run_finished.emit(int(exit_code), summary)

    # NOTE: no @pyqtSlot decorator here. errorOccurred carries a
    # QProcess.ProcessError enum value; the decorator's signature must match
    # the signal exactly or PyQt aborts (TypeError → core dump on connect).
    # Plain Python method bound via connect() lets PyQt5 introspect the
    # actual signature at runtime, which is what we want.
    def _on_error_occurred(self, err: Any) -> None:
        # FailedToStart / Crashed / Timedout / etc. The textual variant is
        # what the user sees in the log; the enum is too Qt-specific.
        if self._proc is None:
            return
        msg = self._proc.errorString()
        self.error.emit(f"RunController: QProcess error: {msg}")

    @pyqtSlot()
    def _sigkill_if_alive(self) -> None:
        if self._proc is None:
            return
        if self._proc.state() != QProcess.NotRunning:
            try:
                self._proc.kill()  # SIGKILL on POSIX
            except Exception as exc:  # pragma: no cover — defensive
                log.warning("RunController: kill raised: %s", exc)


__all__ = ["RunController"]
