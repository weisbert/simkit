"""JSONL progress-event emitter — `pvt run --gui-jsonl` producer side.

Phase 4 §9 (DECISIONS #77). One JSON object per line on stdout for each
progress event so the GUI's :class:`simkit.gui.controllers.run.RunController`
can drain ``readyReadStandardOutput`` line-by-line and turn each line
into a Qt signal. No Qt anywhere in this module — keeps the CLI surface
import-cheap and headless-friendly.

Event shape conforms to spec §9.2:
  ``{"ts": "<ISO-Z>", "event": "<name>", ...}``

When ``enabled=False`` every method is a no-op so the CLI can carry one
emitter object that's silent in non-GUI mode.
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
from typing import Any, IO, Optional


def _utc_now_iso() -> str:
    """ISO-8601 UTC timestamp with trailing ``Z``."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class GuiEventEmitter:
    """One-line-per-event JSONL emitter.

    The stream defaults to ``sys.stdout`` (resolved at ``emit`` time so
    redirects + ``capsys`` capture work). When ``enabled=False`` every
    method short-circuits; the CLI can always construct one and skip
    branching at call sites.
    """

    def __init__(self, *, enabled: bool, stream: Optional[IO[str]] = None):
        self.enabled = bool(enabled)
        self._stream = stream

    # --- core ------------------------------------------------------------

    def emit(self, event: dict) -> None:
        """Stamp ``ts`` if missing, ``json.dumps`` + newline + flush."""
        if not self.enabled:
            return
        if "ts" not in event:
            event = {"ts": _utc_now_iso(), **event}
        out = self._stream if self._stream is not None else sys.stdout
        out.write(json.dumps(event, ensure_ascii=False, default=str))
        out.write("\n")
        try:
            out.flush()
        except Exception:
            pass

    def emit_dict_event_callback(self, event: dict) -> None:
        """Closure-friendly alias for ``emit`` — used as ``progress_cb``."""
        self.emit(event)

    # --- convenience helpers --------------------------------------------

    def item_started(
        self, *, item_index: int, item_name: str, total_items: int,
    ) -> None:
        self.emit({
            "event": "item_started",
            "item_index": item_index,
            "item_name": item_name,
            "total_items": total_items,
        })

    def item_progress(
        self, *, item_index: int, running: int,
        completed: int, failed: int, total_corners: int,
    ) -> None:
        self.emit({
            "event": "item_progress",
            "item_index": item_index,
            "running": running,
            "completed": completed,
            "failed": failed,
            "total_corners": total_corners,
        })

    def item_completed(
        self, *, item_index: int, run_id: str,
        completed: int, failed: int, history_name: Optional[str],
    ) -> None:
        self.emit({
            "event": "item_completed",
            "item_index": item_index,
            "run_id": run_id,
            "completed": completed,
            "failed": failed,
            "history_name": history_name,
        })

    def log(self, level: str, msg: str) -> None:
        self.emit({"event": "log", "level": level, "msg": msg})

    def review_done(self, *, exit_code: int, summary: dict) -> None:
        self.emit({
            "event": "review_done",
            "exit_code": exit_code,
            "summary": summary,
        })

    def error(self, *, code: str, msg: str) -> None:
        self.emit({"event": "error", "code": code, "msg": msg})

    def strategy_attempt(
        self, *, item_index: int, strategy_name: str, attempt_number: int,
        outcome: str, targeted: list[str], remaining: list[str],
    ) -> None:
        self.emit({
            "event": "strategy_attempt",
            "item_index": item_index,
            "strategy_name": strategy_name,
            "attempt_number": attempt_number,
            "outcome": outcome,
            "targeted": list(targeted),
            "remaining": list(remaining),
        })


__all__ = ["GuiEventEmitter"]
