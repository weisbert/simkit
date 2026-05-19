"""Global app state — ``~/.simkit/gui_app.json`` (spec §7.1 + §16.3).

Tracks:
  * ``last_visited`` — absolute path to the last-opened module's
    ``.pvtproject``. Read on boot to restore the user's working module.
  * ``recent_modules`` — ring buffer (max 5) of recently-visited module
    paths. Powers the top-bar "Recent: NDIV / CP / LDO / ..." quick-list.
  * ``registered_modules`` — explicit user-curated module list, drives
    the top-bar module dropdown + status-strip cross-module queries.
    Distinct from ``recent_modules`` because the user may register
    modules they haven't yet opened in this app run.
  * ``window_geometry`` — Qt window geometry/state, base64 strings.
    Stored opaquely; the MainWindow decides what to do on read.

Shape is **additive-tolerant**: unknown keys round-trip untouched, so a
newer simkit can write fields an older simkit will ignore (and vice
versa) without data loss.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


SCHEMA_VERSION = 1
RECENT_MAX = 5

_DEFAULT_PATH = Path.home() / ".simkit" / "gui_app.json"


@dataclass
class GuiAppState:
    """In-memory representation of ``~/.simkit/gui_app.json``."""

    last_visited: Optional[str] = None
    recent_modules: list[str] = field(default_factory=list)
    registered_modules: list[str] = field(default_factory=list)
    window_geometry: Optional[str] = None
    window_state: Optional[str] = None
    # Unknown / forward-compat keys land here so they survive a round-trip.
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "last_visited": self.last_visited,
            "recent_modules": list(self.recent_modules),
            "registered_modules": list(self.registered_modules),
            "window_geometry": self.window_geometry,
            "window_state": self.window_state,
        }
        for k, v in self.extra.items():
            # Defensive: never clobber a known field with a forward-compat key.
            if k not in out:
                out[k] = v
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "GuiAppState":
        if not isinstance(raw, dict):
            raw = {}
        known = {
            "schema_version",
            "last_visited",
            "recent_modules",
            "registered_modules",
            "window_geometry",
            "window_state",
        }
        extra = {k: v for k, v in raw.items() if k not in known}
        return cls(
            last_visited=raw.get("last_visited"),
            recent_modules=list(raw.get("recent_modules") or []),
            registered_modules=list(raw.get("registered_modules") or []),
            window_geometry=raw.get("window_geometry"),
            window_state=raw.get("window_state"),
            extra=extra,
        )

    def push_recent(self, module_path: str) -> None:
        """Bump ``module_path`` to the front of the recents ring buffer."""
        module_path = str(module_path)
        # Remove any existing occurrence, then prepend.
        self.recent_modules = (
            [module_path]
            + [m for m in self.recent_modules if m != module_path]
        )[:RECENT_MAX]


def app_state_path() -> Path:
    """Return the canonical ``~/.simkit/gui_app.json`` path.

    Respects ``SIMKIT_HOME`` env var as an override (useful for tests).
    """
    override = os.environ.get("SIMKIT_HOME")
    if override:
        return Path(override).expanduser() / "gui_app.json"
    return _DEFAULT_PATH


def load_app_state(path: Optional[Path] = None) -> GuiAppState:
    """Read the global app state file.

    Missing / unreadable file returns an empty :class:`GuiAppState`. Never
    raises — spec §7.1 mandates crash-free fallback on stale state.
    """
    p = Path(path) if path is not None else app_state_path()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    return GuiAppState.from_dict(raw if isinstance(raw, dict) else {})


def save_app_state(state: GuiAppState, path: Optional[Path] = None) -> Path:
    """Atomically write the global app state file. Returns the path."""
    p = Path(path) if path is not None else app_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(
        json.dumps(state.to_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp.replace(p)
    return p
