"""Thin Python wrapper around skillbridge for Phase 2 corners verbs.

Exposes two callables that mirror the SKILL entry points in
``skill/pvtCorners.il``:

* :func:`pvt_corners_pull` — write the live ADE-XL corner table to a
  ``.union.json`` sidecar.
* :func:`pvt_corners_push` — load a ``.union.json`` sidecar and apply it
  to the live ADE-XL setup.

Both calls re-``load`` the production SKILL files at the start of each
invocation. Re-loading is idempotent (SKILL ``load`` replaces
procedure definitions) and cheap (~5 files, <1s wall-clock) — and it
guarantees the in-memory definitions match the on-disk source even if
the user has edited the files since the Virtuoso session started.

SKILL pvtErr results are decoded and re-raised as
:class:`SkillBridgeError`; the underlying skillbridge transport
exceptions (connect failures, malformed responses) propagate unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from simkit.project import PvtProject

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SKILL_DIR = _REPO_ROOT / "skill"
_PRODUCTION_SKILL_FILES = (
    "pvtError.il",
    "pvtJson.il",
    "pvtProject.il",
    "pvtCollect.il",
    "pvtCorners.il",
)


class SkillBridgeError(RuntimeError):
    """A ``pvt_err`` result surfaced from SKILL as a Python exception."""

    def __init__(self, category: str, message: str, source: Optional[str] = None):
        self.category = category
        self.message = message
        self.source = source
        suffix = f" (in {source})" if source else ""
        super().__init__(f"{category}: {message}{suffix}")


def _open_workspace():
    try:
        from skillbridge import Workspace
    except ImportError as exc:  # pragma: no cover — env-specific
        raise RuntimeError(
            "skillbridge Python package is not importable; "
            "install from ../skill_tools/skillbridge/"
        ) from exc
    return Workspace.open()


def _load_production_files(ws) -> None:
    for fname in _PRODUCTION_SKILL_FILES:
        ws["load"](str(_SKILL_DIR / fname))


def _symbol_name(s: Any) -> str:
    """skillbridge returns SKILL symbols as objects with a ``name`` attr;
    fall back to ``str`` so this stays robust across skillbridge versions."""
    return s.name if hasattr(s, "name") else str(s)


def _unwrap(result: Any) -> Any:
    """Decode a SKILL discriminated-result list. ``pvt_ok`` returns the
    wrapped value; ``pvt_err`` raises :class:`SkillBridgeError`."""
    if not isinstance(result, (list, tuple)) or len(result) < 2:
        raise SkillBridgeError(
            "transport",
            f"unexpected SKILL response shape: {result!r}",
        )
    head_name = _symbol_name(result[0])
    if head_name == "pvt_ok":
        return result[1]
    if head_name == "pvt_err":
        category = _symbol_name(result[1])
        message = result[2]
        source = result[3] if len(result) > 3 else None
        raise SkillBridgeError(category, message, source)
    raise SkillBridgeError(
        "transport",
        f"unknown SKILL result head: {head_name!r}",
    )


def _prep(ws, pvtproject_path: Path) -> None:
    """Load production files, ``cd`` to the project root, and pin the
    Virtuoso-side ``PVT_PROJECT`` env var to the path we resolved on
    the Python side. Pinning is important because the Virtuoso process
    can have a stale ``PVT_PROJECT`` left over from prior probes /
    sessions; if it points at a non-existent file, SKILL's
    ``pvtLoadPvtProject`` fails fast (per DECISIONS #6) before falling
    back to the cwd walker."""
    _load_production_files(ws)
    ws["changeWorkingDir"](str(pvtproject_path.parent))
    ws["setShellEnvVar"](f"PVT_PROJECT={pvtproject_path}")


def pvt_corners_pull(
    out_path: str | Path,
    *,
    pvtproject_path: Path,
    session: Optional[str] = None,
    union_name: Optional[str] = None,
    workspace: Any = None,
) -> str:
    """Pull the live ADE-XL corner table into ``out_path``.

    Returns the absolute path of the written sidecar (echoed by SKILL).
    """
    ws = workspace if workspace is not None else _open_workspace()
    _prep(ws, pvtproject_path)
    kwargs = {"outPath": str(out_path)}
    if session is not None:
        kwargs["sess"] = session
    if union_name is not None:
        kwargs["unionName"] = union_name
    return _unwrap(ws["pvtCornersPull"](**kwargs))


def resolve_live_testbench_id(
    *,
    session: Optional[str] = None,
    workspace: Any = None,
) -> str:
    """Return ``lib/cell/view`` for the current ADE-XL session.

    Mirrors what the SKILL collector synthesises into a sidecar's
    ``testbench_id`` field. Used by ``pvt corners restore`` to fill in
    the CSV's missing testbench info before pushing.
    """
    ws = workspace if workspace is not None else _open_workspace()
    sess = session if session is not None else ws["axlGetWindowSession"]()
    if not sess:
        raise SkillBridgeError(
            "pvt_validation",
            "no active Maestro session — cannot infer testbench_id",
        )
    lib = ws["axlGetSessionLibName"](sess)
    cell = ws["axlGetSessionCellName"](sess)
    view = ws["axlGetSessionViewName"](sess)
    if not (lib and cell and view):
        raise SkillBridgeError(
            "pvt_validation",
            f"session {sess!r} returned incomplete lib/cell/view "
            f"({lib!r}/{cell!r}/{view!r})",
        )
    return f"{lib}/{cell}/{view}"


def pvt_corners_push(
    union_json_path: str | Path,
    *,
    pvtproject_path: Path,
    session: Optional[str] = None,
    dry_run: bool = False,
    workspace: Any = None,
) -> str:
    """Push ``union_json_path`` into the live ADE-XL setup.

    Returns the union ``name`` echoed by SKILL on success.
    """
    ws = workspace if workspace is not None else _open_workspace()
    _prep(ws, pvtproject_path)
    kwargs: dict = {"unionJsonPath": str(union_json_path)}
    if session is not None:
        kwargs["sess"] = session
    if dry_run:
        kwargs["dryRun"] = True
    return _unwrap(ws["pvtCornersPush"](**kwargs))


def resolve_pvtproject_path(explicit: Optional[str]) -> Path:
    """Resolve a ``.pvtproject`` path from an explicit override or the
    DECISIONS-#6 fallback chain (env var → cwd walker)."""
    if explicit is not None:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f".pvtproject not found: {path}")
        return path
    proj: PvtProject = _load_pvtproject_default()
    return proj.source_path


def _load_pvtproject_default() -> PvtProject:
    # Indirection so unit tests can monkeypatch without importing project.
    from simkit.project import load_pvtproject

    return load_pvtproject()
