"""Per-module session state held by ``AppController`` (spec §7, A4).

A ``ModuleSession`` is the unit of "what the user is currently looking at
inside one ``.pvtproject``". One per opened-this-app-run module:
serialised to that project's ``.simkit/gui_state.json`` on module switch
and on graceful exit; reloaded on next app start.

The dataclass intentionally separates the **serialisable** facts (tree
selection, dirty editors, active baseline, last viewed run) from the
**live** Qt handles (active ``QProcess``, ``QAbstractTableModel`` caches).
Only the serialisable facts hit disk. The live handles live on the
``ModuleSession`` instance for fast tab-switch but are recreated fresh
on every app launch.

Schema v1 shape is pinned in spec §7.2. Any breaking change requires a
migration note in ``DECISIONS.md``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


SCHEMA_VERSION = 1
"""On-disk shape version for ``.simkit/gui_state.json``."""


@dataclass
class TreeState:
    """Left-tree expansion + selection. Pure data."""

    expanded: list[str] = field(default_factory=list)
    selected_path: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "expanded": list(self.expanded),
            "selected_path": list(self.selected_path),
        }

    @classmethod
    def from_dict(cls, raw: Optional[dict[str, Any]]) -> "TreeState":
        if not isinstance(raw, dict):
            return cls()
        return cls(
            expanded=list(raw.get("expanded") or []),
            selected_path=list(raw.get("selected_path") or []),
        )


@dataclass
class ModuleSession:
    """Per-module session state. See spec §7.

    Only ``project_path`` is required; everything else has empty defaults
    so a freshly-opened module is a valid ``ModuleSession``.

    Live Qt handles (``active_qprocess``, ``last_results_model``) are NOT
    serialised — they live on the instance for fast tab-switch but reset
    on app restart. They default to ``None`` and are excluded from
    :meth:`to_dict`.
    """

    project_path: Path
    project_name: str = ""
    last_selected_review: Optional[str] = None
    left_tree: TreeState = field(default_factory=TreeState)
    active_baseline: Optional[str] = None
    dirty_editors: dict[str, Any] = field(default_factory=dict)
    last_run_id_viewed: Optional[str] = None
    # Maestro session name (e.g. "fnxSession0"). Required for every
    # BridgeWorker call; persisted so the user types it once per module.
    session_name: Optional[str] = None

    # Live (non-serialised) state. Spec §7 lists these on the dataclass
    # but they hold Qt handles that don't survive a process restart.
    active_qprocess: Any = field(default=None, repr=False, compare=False)
    last_results_model: Any = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        """Produce the ``gui_state.json`` shape (schema v1).

        Excludes ``project_path`` / ``project_name`` because those are the
        addressing context (the file lives under the project) and the
        live Qt handles. Matches spec §7.2.
        """
        return {
            "schema_version": SCHEMA_VERSION,
            "last_selected_review": self.last_selected_review,
            "left_tree": self.left_tree.to_dict(),
            "active_baseline": self.active_baseline,
            "dirty_editors": dict(self.dirty_editors),
            "last_run_id_viewed": self.last_run_id_viewed,
            "session_name": self.session_name,
        }

    @classmethod
    def from_dict(
        cls, raw: dict[str, Any], *, project_path: Path, project_name: str = "",
    ) -> "ModuleSession":
        """Reconstruct from a ``gui_state.json`` dict.

        Unknown / missing fields fall back to defaults — never raises on
        stale state (spec §7.1: "Module gone / corrupted state: fall back
        to empty session"). The caller decides what to do about a
        non-matching ``schema_version``; here we accept anything to keep
        the read path crash-free.
        """
        if not isinstance(raw, dict):
            raw = {}
        return cls(
            project_path=project_path,
            project_name=project_name,
            last_selected_review=raw.get("last_selected_review"),
            left_tree=TreeState.from_dict(raw.get("left_tree")),
            active_baseline=raw.get("active_baseline"),
            dirty_editors=dict(raw.get("dirty_editors") or {}),
            last_run_id_viewed=raw.get("last_run_id_viewed"),
            session_name=raw.get("session_name"),
        )


def state_file_for(project_path: Path) -> Path:
    """Resolve the per-module state file path.

    ``<project_dir>/.simkit/gui_state.json`` where ``project_dir`` is the
    directory containing the ``.pvtproject`` (mirrors existing
    ``.simkit/`` conventions inside a project).
    """
    project_path = Path(project_path)
    base = project_path if project_path.is_dir() else project_path.parent
    return base / ".simkit" / "gui_state.json"


def load_session(project_path: Path, *, project_name: str = "") -> ModuleSession:
    """Read ``<project>/.simkit/gui_state.json`` into a ``ModuleSession``.

    Missing file / unreadable JSON returns an empty session pointing at
    ``project_path``. Never raises on stale or corrupted state — spec
    §7.1 mandates a crash-free fallback.
    """
    path = state_file_for(project_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    return ModuleSession.from_dict(
        raw if isinstance(raw, dict) else {},
        project_path=Path(project_path),
        project_name=project_name,
    )


def save_session(session: ModuleSession) -> Path:
    """Atomically write ``session`` to its per-module state file.

    Writes ``<tmp>`` then ``os.replace`` so a crash mid-write can't leave
    a half-written JSON on disk (next boot would then trip the silent
    fallback). Returns the written path.
    """
    path = state_file_for(session.project_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(session.to_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp.replace(path)
    return path
