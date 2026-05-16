"""Thin Python wrapper around skillbridge for Phase 2 / 3B SKILL verbs.

Exposes callables that mirror SKILL entry points:

Phase 2 (``skill/pvtCorners.il``):

* :func:`pvt_corners_pull` — write the live ADE-XL corner table to a
  ``.union.json`` sidecar.
* :func:`pvt_corners_push` — load a ``.union.json`` sidecar and apply it
  to the live ADE-XL setup.

Phase 3B (``skill/pvtMeasure.il``):

* :func:`pvt_measure_push` — push a pre-rendered measurement JSON
  (template_render output) into the live Outputs table.
* :func:`pvt_measure_pull` — snapshot the live Outputs table to a
  ``.snapshot.json`` sidecar.
* :func:`pvt_measure_restore` — re-import a snapshot CSV via
  ``axlOutputsImportFromFile`` (overwrite/merge/retain).

Each call re-``load``s the production SKILL files at the start of each
invocation. Re-loading is idempotent (SKILL ``load`` replaces
procedure definitions) and cheap (~7 files, <1s wall-clock) — and it
guarantees the in-memory definitions match the on-disk source even if
the user has edited the files since the Virtuoso session started.

SKILL pvtErr results are decoded and re-raised as
:class:`SkillBridgeError`; the underlying skillbridge transport
exceptions (connect failures, malformed responses) propagate unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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

# Phase 3B SKILL files. pvtError + pvtJson overlap with the Phase 2 list and
# are intentionally re-loaded here so callers that only invoke measure entry
# points still get a consistent in-memory image. Idempotent load means the
# duplication is free (DECISIONS #41 / Phase 3B §3).
_PRODUCTION_MEASURE_SKILL_FILES = (
    "pvtError.il",
    "pvtJson.il",
    "pvtMeasure.il",
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


def _load_measure_skill_files(ws) -> None:
    """Idempotent re-load of Phase 3B's measurement-side SKILL modules.

    Sibling of :func:`_load_production_files`; kept separate so the corners
    verbs do not need to load pvtMeasure.il (and vice versa). DECISIONS #41
    / Phase 3B §3 covers the design.
    """
    for fname in _PRODUCTION_MEASURE_SKILL_FILES:
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


# --------------------------------------------------------------------------
# Phase 3B — measurement wrappers
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PvtMeasurePushRow:
    """One per-row outcome returned by ``pvtMeasurePush``.

    ``status`` is one of ``added`` / ``replaced`` / ``failed`` /
    ``would_add`` / ``would_replace``. ``reason`` is set when status is
    ``failed`` (or whenever the SKILL layer attaches one).

    ``spec_status`` (v1.3+) reports the outcome of the optional
    ``axlAddSpecToOutput`` call attached to this row. ``None`` when the row
    carries no spec; ``"ok"`` on success; ``"failed: <reason>"`` on parse
    or push failure. Spec failure does NOT down-grade the primary status.
    """

    name: str
    status: str
    reason: Optional[str] = None
    spec_status: Optional[str] = None


@dataclass(frozen=True)
class PvtMeasurePushReport:
    """Result payload of :func:`pvt_measure_push`."""

    n_pushed: int
    rows: tuple[PvtMeasurePushRow, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PvtMeasurePullReport:
    """Result payload of :func:`pvt_measure_pull`."""

    n_rows: int
    path: str


def _decode_push_row(raw: Any) -> PvtMeasurePushRow:
    """Decode one row entry from the SKILL push report.

    SKILL emits each row as a small table that the skillbridge bridge turns
    into a dict. Older skillbridges may surface it as a tuple/list of
    ``(key, value)`` pairs; handle both shapes.
    """
    if isinstance(raw, dict):
        name = raw.get("name", "")
        status = raw.get("status", "")
        reason = raw.get("reason")
        spec_status = raw.get("spec_status")
    else:
        pairs = dict(raw)
        name = pairs.get("name", "")
        status = pairs.get("status", "")
        reason = pairs.get("reason")
        spec_status = pairs.get("spec_status")
    return PvtMeasurePushRow(
        name=str(name) if name is not None else "",
        status=str(status) if status is not None else "",
        reason=(str(reason) if reason is not None else None),
        spec_status=(str(spec_status) if spec_status is not None else None),
    )


def _decode_push_report(raw: Any) -> PvtMeasurePushReport:
    if isinstance(raw, dict):
        n_pushed = raw.get("n_pushed", 0)
        rows = raw.get("rows", []) or []
    else:
        pairs = dict(raw)
        n_pushed = pairs.get("n_pushed", 0)
        rows = pairs.get("rows", []) or []
    if not isinstance(rows, (list, tuple)):
        rows = []
    return PvtMeasurePushReport(
        n_pushed=int(n_pushed),
        rows=tuple(_decode_push_row(r) for r in rows),
    )


def _decode_pull_report(raw: Any) -> PvtMeasurePullReport:
    if isinstance(raw, dict):
        n_rows = raw.get("n_rows", 0)
        path = raw.get("path", "")
    else:
        pairs = dict(raw)
        n_rows = pairs.get("n_rows", 0)
        path = pairs.get("path", "")
    return PvtMeasurePullReport(n_rows=int(n_rows), path=str(path or ""))


def _prep_measure(
    ws,
    pvtproject_path: Optional[Path],
    cwd_fallback: Optional[Path],
) -> None:
    """Common pre-call setup for measure verbs.

    Loads the Phase 3B SKILL modules and ``cd``s the Virtuoso shell to
    either the supplied ``.pvtproject`` directory or to ``cwd_fallback``
    (typically the bundle/JSON file's parent directory). If a project
    path is supplied, ``PVT_PROJECT`` is pinned just like the corners
    helpers do — keeps SKILL session-state predictable when both verb
    families share a process.
    """
    _load_measure_skill_files(ws)
    if pvtproject_path is not None:
        ws["changeWorkingDir"](str(pvtproject_path.parent))
        ws["setShellEnvVar"](f"PVT_PROJECT={pvtproject_path}")
    elif cwd_fallback is not None:
        ws["changeWorkingDir"](str(cwd_fallback))


def pvt_measure_push(
    rendered_json_path: str | Path,
    *,
    test_name: str = "Test",
    dry_run: bool = False,
    replace: bool = False,
    session: Optional[str] = None,
    pvtproject_path: Optional[Path] = None,
    workspace: Any = None,
) -> PvtMeasurePushReport:
    """Push pre-rendered measurement rows into the live ADE-XL setup.

    ``rendered_json_path`` must conform to the schema produced by
    ``simkit.template_render`` (``rendered_schema_version`` 1, a ``rows``
    array, optional ``test`` and ``session`` hints). ``test_name``
    overrides the JSON-level hint. ``dry_run`` instructs the SKILL side
    to validate + report what would be pushed without touching the
    setup. ``replace`` causes each existing same-named output to be
    deleted before the add.
    """
    rendered_path = Path(rendered_json_path).expanduser()
    ws = workspace if workspace is not None else _open_workspace()
    fallback = (
        rendered_path.parent.resolve()
        if rendered_path.parent != Path("")
        else None
    )
    _prep_measure(ws, pvtproject_path, fallback)

    kwargs: dict = {"renderedJsonPath": str(rendered_path)}
    if test_name is not None:
        kwargs["testName"] = test_name
    if dry_run:
        kwargs["dryRun"] = True
    if replace:
        kwargs["replace"] = True
    if session is not None:
        kwargs["sess"] = session

    raw = _unwrap(ws["pvtMeasurePush"](**kwargs))
    return _decode_push_report(raw)


def pvt_measure_pull(
    out_path: str | Path,
    *,
    test_name: str = "Test",
    include_signals: bool = False,
    session: Optional[str] = None,
    pvtproject_path: Optional[Path] = None,
    workspace: Any = None,
) -> PvtMeasurePullReport:
    """Snapshot the live Outputs table to ``out_path``.

    Filters to rows whose ``Test`` column equals ``test_name`` (default
    ``"Test"``); excludes ``Type=net`` (signal-tap) rows unless
    ``include_signals=True``.
    """
    snapshot_path = Path(out_path).expanduser()
    ws = workspace if workspace is not None else _open_workspace()
    fallback = (
        snapshot_path.parent.resolve()
        if snapshot_path.parent != Path("")
        else None
    )
    _prep_measure(ws, pvtproject_path, fallback)

    kwargs: dict = {"outPath": str(snapshot_path)}
    if test_name is not None:
        kwargs["testName"] = test_name
    if include_signals:
        kwargs["includeSignals"] = True
    if session is not None:
        kwargs["sess"] = session

    raw = _unwrap(ws["pvtMeasurePull"](**kwargs))
    return _decode_pull_report(raw)


def pvt_measure_restore(
    csv_path: str | Path,
    *,
    operation: str = "merge",
    test_name: Optional[str] = None,
    session: Optional[str] = None,
    pvtproject_path: Optional[Path] = None,
    workspace: Any = None,
) -> None:
    """Re-import a Maestro Outputs CSV via ``axlOutputsImportFromFile``.

    This is the snapshot-restore path: lossy on ``evalType`` (CSV does
    not carry it) but cheap and crash-recovery friendly. ``operation``
    must be one of ``overwrite`` / ``merge`` / ``retain`` (Maestro's
    documented modes). Defaults to ``merge`` (conservative — preserves
    rows not in the snapshot). Use ``overwrite`` only when the snapshot
    is a full faithful copy (e.g. pulled with ``--include-signals``);
    otherwise overwrite will wipe live rows the snapshot did not capture
    — see live verification 2026-05-14 where default pull (expr-only)
    + overwrite restore wiped the 4 net signal-tap rows of fnxSession0.
    """
    csv = Path(csv_path).expanduser()
    if operation not in ("overwrite", "merge", "retain"):
        raise SkillBridgeError(
            "pvt_validation",
            f"operation must be one of overwrite/merge/retain (got {operation!r})",
        )
    if not csv.is_file():
        raise SkillBridgeError(
            "pvt_io",
            f"snapshot CSV not found: {csv}",
            str(csv),
        )

    ws = workspace if workspace is not None else _open_workspace()
    _prep_measure(ws, pvtproject_path, csv.parent.resolve())

    sess = session if session is not None else ws["axlGetWindowSession"]()
    if not sess:
        raise SkillBridgeError(
            "pvt_validation",
            "no active Maestro session — pass --session or open Maestro",
        )

    kwargs: dict = {"operation": operation}
    if test_name is not None:
        kwargs["test"] = test_name

    rv = ws["axlOutputsImportFromFile"](sess, str(csv), **kwargs)
    if rv is None or rv is False:
        raise SkillBridgeError(
            "pvt_io",
            f"axlOutputsImportFromFile returned {rv!r} for {csv}",
            str(csv),
        )


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
