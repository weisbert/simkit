"""Translate :class:`BridgeError` payloads into actionable zh-CN messages.

Spec §8.3 (mandate B5): raw error text like ``ASSEMBLER-2423`` or
``pvt_runner_no_session`` is meaningless to a Chinese-speaking analog IC
engineer. This module maps known ``(category, message-substring)`` pairs
to a one-line headline + concrete next-step hint. Unknown errors fall
through with the raw text + a "Report this" hint.

Pure Python — no Qt import. The Qt-aware bridge into a ``BridgeWorker``
lives in :mod:`simkit.gui.controllers.error_translator`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Tuple, Union

if TYPE_CHECKING:  # pragma: no cover - typing only
    from simkit.gui.bridge_worker import BridgeError


@dataclass(frozen=True)
class TranslatedError:
    """User-visible translation of a :class:`BridgeError`."""

    headline: str
    detail: str
    action_hint: str
    is_known: bool


# Matcher forms:
#   str                       -> exact category match
#   (category, substr)        -> category match AND substr in message
#   (None,     substr)        -> substr in message (any category)
Matcher = Union[str, Tuple[Optional[str], str]]


# First match wins; order = priority. Substring matches before bare
# category matches so a more specific reason (e.g. ``ASSEMBLER-2423``
# inside a generic ``pvt_runner_*`` failure) wins.
KNOWN_ERRORS: List[Tuple[Matcher, str, str]] = [
    (
        (None, "ASSEMBLER-2423"),
        "Maestro has a dialog open (setupdb temporarily locked)",
        "Click the Maestro main window to dismiss the dialog, then retry.",
    ),
    (
        (None, "axlGetRunStatus returned nil"),
        "Maestro's current session is not recognised",
        "Click once in the Maestro window to activate the session, then retry.",
    ),
    (
        (None, "Connection refused"),
        "Virtuoso is not running / the skillbridge server is down",
        'Re-run (pyKillServer)(pyStartServer ?python "/usr/bin/python3") '
        'in the CIW, or restart Virtuoso.',
    ),
    (
        (None, "socket"),
        "Virtuoso is not running / the skillbridge server is down",
        'Re-run (pyKillServer)(pyStartServer ?python "/usr/bin/python3") '
        'in the CIW, or restart Virtuoso.',
    ),
    (
        (None, "Constraint violation"),
        "The local database was written concurrently",
        "Close other simkit instances and retry.",
    ),
    (
        ("pvt_validation", "not found"),
        "An input file path cannot be found",
        "Check the review.json / union.json / bundle.json paths.",
    ),
    (
        "pvt_runner_no_session",
        "The Maestro session does not exist or is misspelled",
        "Confirm the session name (e.g. fnxSession0) is spelled correctly "
        "and open in Maestro.",
    ),
    (
        "session_focus_lost",
        "Maestro's current session is not recognised (focus moved away)",
        "Click once in the Maestro Assembler window to re-activate the "
        "session, then retry.",
    ),
    (
        "bridge_socket_dead",
        "Virtuoso is not running / the skillbridge server is down",
        'Re-run (pyKillServer)(pyStartServer ?python "/usr/bin/python3") '
        'in the CIW, or restart Virtuoso.',
    ),
    (
        "bridge_dead",
        "The skillbridge python_server process has exited",
        "Kill any leftover python_server process in a shell, then run "
        "(pyStartServer) in the CIW.",
    ),
    (
        "bridge_wedge",
        "The skillbridge channel is stuck (stale half-response)",
        "Run (pyKillServer)(pyStartServer) in the CIW, then retry.",
    ),
    (
        "lock_failed",
        "The Maestro history lock operation failed",
        "Confirm the history name exists and Maestro is not in cleanup.",
    ),
    (
        "pvt_runner_no_option",
        "A Spectre option is not set (this is usually normal)",
        "If this was a probe, ignore it; if a write, check the test name.",
    ),
    (
        "pvt_runner_timeout",
        "The Maestro run timed out without returning to idle",
        "Check the Maestro main window for an error dialog; restart "
        "Spectre if necessary.",
    ),
    (
        "pvt_validation",
        "Input parameter validation failed",
        "See the specific field in Details.",
    ),
    (
        "pvt_io",
        "An input/output file operation failed",
        "Check the path in Details and confirm permissions.",
    ),
    (
        "bad_history_name",
        "The history name contains illegal characters (newline/Tab)",
        "Use a plain-text history name and retry.",
    ),
    (
        "transport",
        "The skillbridge response format is malformed",
        "This usually means the Virtuoso-side SKILL did not load fully; "
        "reload the simkit SKILL and retry.",
    ),
]


def _format_detail(err: "BridgeError") -> str:
    base = f"[{err.category}] {err.message}"
    if err.source:
        base += f"  (source: {err.source})"
    return base


def _matches(matcher: Matcher, err: "BridgeError") -> bool:
    if isinstance(matcher, str):
        return err.category == matcher
    cat, substr = matcher
    if cat is not None and err.category != cat:
        return False
    return substr in (err.message or "")


def translate(err: "BridgeError") -> TranslatedError:
    """Match ``err`` against :data:`KNOWN_ERRORS`; first match wins.

    Always returns a :class:`TranslatedError`; never raises.
    """
    detail = _format_detail(err)
    for matcher, headline, hint in KNOWN_ERRORS:
        if _matches(matcher, err):
            return TranslatedError(
                headline=headline,
                detail=detail,
                action_hint=hint,
                is_known=True,
            )
    return TranslatedError(
        headline=f"Unrecognised error: {err.category}",
        detail=detail,
        action_hint="If this recurs, use 'Report this' to send the exact "
                    "reproduction steps + the Details text.",
        is_known=False,
    )


def translate_exception(exc: BaseException) -> TranslatedError:
    """Wrap any exception via :meth:`BridgeError.from_exception` and translate."""
    from simkit.gui.bridge_worker import BridgeError

    return translate(BridgeError.from_exception(exc))
