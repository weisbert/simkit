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

from contextlib import contextmanager
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

# Phase 3A SKILL files. Same idempotent-load argument as the measure family.
_PRODUCTION_RUNNER_SKILL_FILES = (
    "pvtError.il",
    "pvtJson.il",
    "pvtProject.il",
    "pvtCollect.il",   # PvtSave is invoked from the runner caller
    "pvtRunner.il",
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


def get_sdb(session: str, *, workspace: Any = None) -> int:
    """Look up the sdb handle for a session by name, ONCE.

    Pass the returned int as the ``session=`` arg to subsequent read-side
    wrappers (snapshot/install/get/restore — anything that doesn't wrap
    ``axlRunAllTests`` / ``axlGetRunStatus`` / ``axlGetCurrentHistory``).
    The SKILL helpers accept either a session-name string or this int.

    Why bother: after ``axlRunAllTests`` fires, Maestro pops a Run Summary
    sub-window that momentarily shadows the Assembler in Cadence's
    window-focus-keyed session registry. Until the user clicks back into
    the Assembler, ``axlGetMainSetupDB(<session_name>)`` returns nil and
    every wrapper that re-resolves the name fails with
    ``"Cannot find an active session named X"``. The sdb HANDLE is stable
    across this state — pass it directly and the wrappers skip the broken
    name-lookup step.

    Run-side wrappers (``pvt_runner_run`` / ``pvt_runner_submit`` /
    ``pvt_runner_get_status`` / ``pvt_runner_rename``) still need the
    session name string — they wrap axl* calls that don't accept an sdb.

    Example::

        sdb = skill_bridge.get_sdb("fnxSession0")
        # read-side: pass sdb, immune to focus loss
        snap = pvt_runner_snapshot_corners_enable(session=sdb)
        pre  = pvt_runner_get_pre_run_script("Test_trans", session=sdb)
        # run-side: still pass name (active during the run anyway)
        pvt_runner_run("simkit_v13_retry", session="fnxSession0")
    """
    ws = workspace if workspace is not None else _open_workspace()
    try:
        raw = ws["axlGetMainSetupDB"](session)
    except ValueError as exc:
        # skillbridge channel.decode_response splits on space; a malformed /
        # half-sent reply trips "not enough values to unpack". Classic wedge
        # left by a prior axlRunAllTests call's tail. Restart fixes it.
        if "not enough values to unpack" in str(exc):
            raise SkillBridgeError(
                "bridge_wedge",
                "skillbridge transport is wedged (stale half-response). "
                "Fix: in Virtuoso CIW type `(pyKillServer)(pyStartServer)` "
                "then re-run.",
                session,
            ) from exc
        raise
    except RuntimeError as exc:
        msg = str(exc)
        if "The server unexpectedly died" in msg:
            raise SkillBridgeError(
                "bridge_dead",
                "skillbridge python_server process crashed or socket lost. "
                "Fix: kill all stale python_server processes from the shell, "
                "then in CIW type `(pyStartServer)` once.",
                session,
            ) from exc
        if "Cannot find an active session" in msg:
            raise SkillBridgeError(
                "session_focus_lost",
                f"axlGetMainSetupDB({session!r}) failed: Cadence's session "
                f"registry is window-focus-keyed and the Maestro Assembler "
                f"is not currently the active ADE-XL window. "
                f"Fix: click the Maestro Assembler window, then re-run.",
                session,
            ) from exc
        raise
    if raw is None or raw == 0:
        raise SkillBridgeError(
            "pvt_validation",
            f"axlGetMainSetupDB returned nil/0 for {session!r}",
            session,
        )
    return int(raw)


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


@contextmanager
def _restore_cwd(ws):
    """Snapshot Virtuoso's working directory on enter; restore on exit.

    Critical for any verb that calls ``changeWorkingDir``. Without this,
    a subsequent ``axlRunAllTests`` (in this verb or a later one in the
    same Python process) inherits the project-specific cwd that the
    earlier verb left behind. Maestro snapshots the parent's cwd into
    the AXL worker's ``runICRP`` launcher script; if that cwd doesn't
    contain ``cds.lib`` (which a ``.pvtproject`` dir typically does
    not), the worker's session configuration fails (`asiGet: no
    applicable method`), Spectre is never dispatched, the history sits
    in "pending" forever, and any cleanup op trips the ASSEMBLER-2423
    "setupdb temporarily locked" modal because Maestro thinks the run
    is still active. DECISIONS #56 captures the diagnosis.
    """
    orig_cwd = ws["getWorkingDir"]()
    try:
        yield
    finally:
        ws["changeWorkingDir"](str(orig_cwd))


@contextmanager
def _prep(ws, pvtproject_path: Path):
    """Load production files, ``cd`` to the project root, pin
    ``PVT_PROJECT``, and restore the original cwd on exit.

    Context-manager form (was a plain procedure in v1) so the verb's
    body runs inside the cwd-restoration scope. Pinning ``PVT_PROJECT``
    is important because the Virtuoso process can have a stale value
    left over from prior probes / sessions; if it points at a
    non-existent file, SKILL's ``pvtLoadPvtProject`` fails fast (per
    DECISIONS #6) before falling back to the cwd walker."""
    _load_production_files(ws)
    with _restore_cwd(ws):
        ws["changeWorkingDir"](str(pvtproject_path.parent))
        ws["setShellEnvVar"](f"PVT_PROJECT={pvtproject_path}")
        yield


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
    with _prep(ws, pvtproject_path):
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
    replace: bool = False,
    workspace: Any = None,
) -> str:
    """Push ``union_json_path`` into the live ADE-XL setup.

    Returns the union ``name`` echoed by SKILL on success.

    When ``replace=True``, every live corner whose name is NOT in the
    sidecar's ``rows[*].row_name`` set is dropped via
    ``axlGetCorner`` + ``axlRemoveElement`` before the sidecar's rows are
    pushed. Empty sidecar + replace = pvt_validation error (would wipe
    the corner table; almost certainly a typo). Default (``replace=False``)
    preserves the v1 ADD-semantics so existing-but-unmentioned rows survive.
    """
    ws = workspace if workspace is not None else _open_workspace()
    with _prep(ws, pvtproject_path):
        kwargs: dict = {"unionJsonPath": str(union_json_path)}
        if session is not None:
            kwargs["sess"] = session
        if dry_run:
            kwargs["dryRun"] = True
        if replace:
            kwargs["replace"] = True
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


@contextmanager
def _prep_measure(
    ws,
    pvtproject_path: Optional[Path],
    cwd_fallback: Optional[Path],
):
    """Common pre-call setup for measure verbs (context-manager form).

    Loads the Phase 3B SKILL modules and ``cd``s the Virtuoso shell to
    either the supplied ``.pvtproject`` directory or to ``cwd_fallback``
    (typically the bundle/JSON file's parent directory). If a project
    path is supplied, ``PVT_PROJECT`` is pinned just like the corners
    helpers do — keeps SKILL session-state predictable when both verb
    families share a process.

    Wraps body in :func:`_restore_cwd` so the cwd change does not leak
    to a subsequent ``axlRunAllTests`` (DECISIONS #56).
    """
    _load_measure_skill_files(ws)
    with _restore_cwd(ws):
        if pvtproject_path is not None:
            ws["changeWorkingDir"](str(pvtproject_path.parent))
            ws["setShellEnvVar"](f"PVT_PROJECT={pvtproject_path}")
        elif cwd_fallback is not None:
            ws["changeWorkingDir"](str(cwd_fallback))
        yield


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
    with _prep_measure(ws, pvtproject_path, fallback):
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
    with _prep_measure(ws, pvtproject_path, fallback):
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
    with _prep_measure(ws, pvtproject_path, csv.parent.resolve()):
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


def _load_runner_skill_files(ws) -> None:
    for fname in _PRODUCTION_RUNNER_SKILL_FILES:
        ws["load"](str(_SKILL_DIR / fname))


def pvt_runner_snapshot_test_state(
    *, session: str, workspace: Any = None,
) -> list[tuple[str, bool]]:
    """Read every test's enable state in the given session.

    Returns a list of (name, enabled) tuples — same shape the SKILL
    `pvtRunnerSnapshotTestState` returns. Pure read, no side effects.
    """
    ws = workspace if workspace is not None else _open_workspace()
    _load_runner_skill_files(ws)
    raw = _unwrap(ws["pvtRunnerSnapshotTestState"](session))
    return [(name, bool(enabled)) for name, enabled in raw]


def pvt_runner_restore_test_state(
    snap: list[tuple[str, bool]], *, session: str, workspace: Any = None,
) -> None:
    """Restore each test's enable state from a snapshot tuple-list."""
    ws = workspace if workspace is not None else _open_workspace()
    _load_runner_skill_files(ws)
    payload = [[name, en] for name, en in snap]
    _unwrap(ws["pvtRunnerRestoreTestState"](session, payload))


def pvt_runner_enable_only(
    test_names: list[str], *, session: str, workspace: Any = None,
) -> list[tuple[str, bool, bool]]:
    """Disable all tests except those in ``test_names``; enable those.

    Returns the diff: list of (name, before, after) triples for every test
    in the session. Raises :class:`SkillBridgeError` if any requested name
    is unknown.
    """
    ws = workspace if workspace is not None else _open_workspace()
    _load_runner_skill_files(ws)
    raw = _unwrap(ws["pvtRunnerEnableOnly"](session, list(test_names)))
    return [(name, bool(b), bool(a)) for name, b, a in raw]


def pvt_runner_submit(
    *, session: str, workspace: Any = None,
) -> None:
    """Dispatch ``axlRunAllTests`` on the session. Fire-and-forget.

    Returns once Maestro has accepted the dispatch; the sims continue
    running in the background. Caller must poll
    :func:`pvt_runner_get_status` until idle BEFORE calling
    :func:`pvt_runner_rename` or any other mutating op against the same
    setupdb, else risks the ASSEMBLER-2423 modal that wedges the
    bridge (DECISIONS #54).
    """
    ws = workspace if workspace is not None else _open_workspace()
    _load_runner_skill_files(ws)
    _unwrap(ws["pvtRunnerSubmit"](session))


def pvt_runner_rename(
    history_name: str, *, session: str, workspace: Any = None,
) -> str:
    """Rename the session's current history entry. DESTRUCTIVE.

    Must only run once the prior dispatch has reached idle. Returns
    the actual post-rename name (Maestro may sanitise).
    """
    ws = workspace if workspace is not None else _open_workspace()
    _load_runner_skill_files(ws)
    raw = _unwrap(ws["pvtRunnerRename"](session, history_name))
    return str(raw)


# axlGetRunStatus throws an uncatchable C-level error when there is no
# active in-flight run record (handle 0). errset can't trap it; neither
# can errsetstring. Per 2026-05-16 probe, the message is exact and
# stable. Treat its appearance as semantically "no active run = idle."
_RUN_STATUS_NO_ACTIVE_MARKERS = (
    "Cannot find a setup database entry for handle 0",
)


def pvt_runner_get_status(
    *, session: str, workspace: Any = None,
) -> tuple[int, int]:
    """Read ``axlGetRunStatus`` for the session.

    Returns ``(code, sub)``. Raises :class:`SkillBridgeError` for any
    SKILL-side ``pvt_err``. The "no active run" RuntimeError is
    translated to a synthetic ``(0, 0)`` because the C call escapes all
    SKILL trap mechanisms on that path (see pvtRunner.il caveat).
    """
    ws = workspace if workspace is not None else _open_workspace()
    _load_runner_skill_files(ws)
    try:
        raw = _unwrap(ws["pvtRunnerGetStatus"](session))
    except RuntimeError as exc:
        msg = str(exc)
        if any(m in msg for m in _RUN_STATUS_NO_ACTIVE_MARKERS):
            return (0, 0)
        raise
    return (int(raw[0]), int(raw[1]))


def pvt_runner_count_running(
    *, session: str, workspace: Any = None,
) -> int:
    """Count in-flight test/corner rows in the session's CURRENT history.

    Walks the current history's results-db (``maeReadResDB``) and counts
    every test row whose ``tst->status`` is non-terminal (i.e. not
    ``'done``/``'failed``/``'no_convergence``/``'aborted``/``'killed``).
    Returns 0 when there's no current history yet, when the rdb can't be
    opened, or when every row has reached a final status.

    Why this exists (Phase 3A v1.5 F2, 2026-05-18): ``axlGetRunStatus``
    sometimes returns ``[0, 0]`` from the very first poll even though
    Maestro has not yet queued the first sub-point — the state machine
    in :func:`pvt_runner_run` would then exit via ``dispatch_grace`` and
    PvtSave would see only ``'pending`` rows. This counter, AND-ed with
    ``axlGetRunStatus``, gives a content-based "this run actually
    finished" signal that survives the slow-queue race.

    Pure read; safe during the async tail of ``axlRunAllTests``.

    Raises :class:`SkillBridgeError` for SKILL-side ``pvt_err``
    (e.g. bad session arg shape).
    """
    ws = workspace if workspace is not None else _open_workspace()
    _load_runner_skill_files(ws)
    return int(_unwrap(ws["pvtRunnerCountRunning"](session)))


def pvt_runner_run(
    history_name: str, *,
    session: str,
    poll_interval: float = 2.0,
    timeout_sec: float = 1800.0,
    idle_confirm_reads: int = 2,
    dispatch_grace_reads: int = 2,
    initial_wait_sec: float = 0.0,
    post_idle_quiesce_sec: float = 0.0,
    min_running_observed: int = 0,
    workspace: Any = None,
    _sleep=None,
) -> tuple[int, int, str]:
    """Submit a run, poll-to-idle, rename. BLOCKING for real.

    v1.1 (2026-05-16) rewrite — was a thin wrapper over the legacy
    ``pvtRunnerRun`` which returned immediately after dispatch and
    renamed the history while the sim was still in-flight, risking
    ASSEMBLER-2423 (DECISIONS #54). v1.1 uses pvtRunnerSubmit +
    poll-loop on pvtRunnerGetStatus + pvtRunnerRename, ensuring the
    rename only fires when Maestro is truly idle.

    v1.5 F2 (2026-05-18) addition: the idle test now AND-s the
    ``axlGetRunStatus == (0, 0)`` signal with
    :func:`pvt_runner_count_running` ``== 0``. The status signal can lie
    in either direction during slow-queue dispatch (it returns ``[0, 0]``
    before Maestro has queued the first sub-point); the count signal is
    content-based — it walks the history's rdb and only returns 0 when
    every row has reached a terminal status. Either signal seeing
    in-flight work resets ``saw_non_idle`` so the state machine waits for
    the work to finish.

    Returns ``(final_code, final_sub, actual_history_name)``.

    Args:
        history_name: name the caller wants the new history entry to
            carry. Renamed post-completion via
            :func:`pvt_runner_rename`.
        session: Maestro session name (e.g. ``fnxSession0``).
        poll_interval: seconds between ``axlGetRunStatus`` polls. Each
            poll is one bridge round-trip; pick to balance latency vs
            chatter. Default 2.0.
        timeout_sec: hard ceiling on the whole submit→idle wait. Default
            1800s (30 min) — large enough for typical PVT sweeps but
            not so large that a wedged sim silently consumes a session.
        idle_confirm_reads: after observing a non-idle state, require
            this many consecutive idle reads before declaring done.
            Guards against a brief momentary [0,0] mid-transition.
        dispatch_grace_reads: when the loop never observes non-idle,
            allow up to this many consecutive idle reads as "cached /
            no-op completion" before declaring done. This handles the
            S1-style re-run-the-already-cached-corner case where the
            full submit→complete happens between two poll intervals.
            NOTE (v1.5 F2): the grace path now ALSO requires
            ``pvt_runner_count_running == 0`` — if the rdb shows any
            in-flight rows, the loop ignores grace and waits for them
            to clear.
        initial_wait_sec: extra sleep AFTER submit and BEFORE the first
            poll. Use when the session's ``axlGetRunStatus`` is unable
            to report in-flight state and the loop would otherwise
            exit via dispatch_grace before Spectre has even started.
            Set to e.g. 30-60s for sessions where post-completion
            destructive ops are seen to hit ASSEMBLER-2423 (DECISIONS
            #54 / #55). Default 0 keeps the cached-path fast.
        post_idle_quiesce_sec: extra sleep AFTER the loop reports
            idle, BEFORE pvtRunnerRename fires. Lets Maestro release
            the setupdb lock; mirrors the ASSEMBLER-2423 mitigation.
        min_running_observed: require having seen
            ``count_running >= min_running_observed`` AT LEAST ONCE
            before believing the run can be declared done. Default 0
            preserves prior behavior. Set to 1 to force the state
            machine to actually observe a sub-point in flight (useful
            for slow-queue cases where neither status nor count have
            ramped up by the first poll). Orchestrator can pass 1 to
            guarantee it waits past the dispatch race.
        workspace: optional pre-opened skillbridge Workspace.
        _sleep: hook for tests; defaults to ``time.sleep``.

    Raises :class:`SkillBridgeError` on submit failure, rename failure,
    or timeout.
    """
    import time
    sleep = _sleep if _sleep is not None else time.sleep

    ws = workspace if workspace is not None else _open_workspace()
    _load_runner_skill_files(ws)

    if not history_name or not isinstance(history_name, str):
        raise SkillBridgeError(
            "pvt_validation",
            "history_name must be a non-empty string",
        )

    pvt_runner_submit(session=session, workspace=ws)

    if initial_wait_sec > 0:
        sleep(initial_wait_sec)
        elapsed_initial = initial_wait_sec
    else:
        elapsed_initial = 0.0

    last_code, last_sub = 0, 0
    saw_non_idle = False
    saw_running = False                       # v1.5 F2: count-side observation
    max_running_seen = 0                      # for diagnostics on timeout
    idle_streak = 0
    elapsed = elapsed_initial
    while elapsed < timeout_sec:
        sleep(poll_interval)
        elapsed += poll_interval
        code, sub = pvt_runner_get_status(session=session, workspace=ws)
        last_code, last_sub = code, sub
        # v1.5 F2: second, content-based signal. Counts test/corner rows
        # in the CURRENT history whose status is still pending/running.
        # 0 == nothing observably in flight from the rdb's perspective.
        count_running = pvt_runner_count_running(session=session, workspace=ws)
        if count_running > max_running_seen:
            max_running_seen = count_running
        if count_running > 0:
            saw_running = True
        status_idle = (code == 0 and sub == 0)
        count_idle = (count_running == 0)
        is_idle = status_idle and count_idle
        # min_running_observed gate (v1.5 F2): when set, never declare
        # done until we've actually observed count_running >= threshold.
        gate_satisfied = saw_running or (min_running_observed <= 0)
        if is_idle and gate_satisfied:
            idle_streak += 1
            if saw_non_idle and idle_streak >= idle_confirm_reads:
                break  # transitioned non-idle → idle for N reads: done for real
            if (not saw_non_idle) and idle_streak >= dispatch_grace_reads:
                break  # never observed non-idle: cached / no-op completion
        else:
            # Either status or count says something is in flight; reset.
            if not is_idle:
                saw_non_idle = True
            idle_streak = 0
    else:
        raise SkillBridgeError(
            "pvt_runner_timeout",
            f"axlRunAllTests did not return to idle in {timeout_sec}s "
            f"(last status [{last_code}, {last_sub}], "
            f"max running seen {max_running_seen})",
            session,
        )

    if post_idle_quiesce_sec > 0:
        sleep(post_idle_quiesce_sec)

    actual_name = pvt_runner_rename(
        history_name, session=session, workspace=ws,
    )
    return (last_code, last_sub, actual_name)


_VALID_IC_MODES = frozenset({"readns", "readic"})


def pvt_runner_set_ic_source(
    test_name: str, ic_path: str, mode: str, *,
    session: str, workspace: Any = None,
) -> str:
    """Point the consumer test's Spectre at a per-corner IC file.

    Wraps SKILL ``pvtRunnerSetIcSource``, which writes a Spectre CLI arg
    into the test's ``additionalArgs`` sim option via
    ``asiSetSimOptionVal``:

      * ``mode="readns"`` → ``additionalArgs="+nodeset <ic_path>"`` —
        soft nodeset hint, typical for PSS convergence aid.
      * ``mode="readic"`` → ``additionalArgs="+ic <ic_path>"`` — hard
        initial condition.

    Per DECISIONS #57: the path through ``additionalArgs`` was chosen
    after a probe found that readns/readic aren't in the 133-option
    Spectre Options form (which holds reltol/gmin/temp/etc.) but
    ``additionalArgs`` always exists and accepts arbitrary Spectre CLI
    args — so zero one-time UI setup is required.

    Returns the option's PREVIOUS value (or ``""`` if unset), which the
    orchestrator threads back into :func:`pvt_runner_clear_ic_source`
    after the run to restore the session.

    Raises :class:`SkillBridgeError` if the bridge or asi session is
    unavailable.
    """
    if mode not in _VALID_IC_MODES:
        raise SkillBridgeError(
            "pvt_validation",
            f"mode must be one of {sorted(_VALID_IC_MODES)}, got {mode!r}",
        )
    ws = workspace if workspace is not None else _open_workspace()
    _load_runner_skill_files(ws)
    raw = _unwrap(
        ws["pvtRunnerSetIcSource"](session, test_name, ic_path, mode)
    )
    return str(raw) if raw is not None else ""


def pvt_runner_clear_ic_source(
    test_name: str, mode: str, prev_value: str = "", *,
    session: str, workspace: Any = None,
) -> None:
    """Restore the consumer test's Spectre IC option to ``prev_value``.

    Counterpart of :func:`pvt_runner_set_ic_source`. Pass ``prev_value=""``
    to clear (the SKILL helper normalises empty string + nil to "unset").
    Always re-issues an asiSetSimOptionVal call; treats a nil return
    (option not registered) as a successful no-op since clearing an
    already-unset option is the desired terminal state.
    """
    if mode not in _VALID_IC_MODES:
        raise SkillBridgeError(
            "pvt_validation",
            f"mode must be one of {sorted(_VALID_IC_MODES)}, got {mode!r}",
        )
    ws = workspace if workspace is not None else _open_workspace()
    _load_runner_skill_files(ws)
    _unwrap(
        ws["pvtRunnerClearIcSource"](session, test_name, mode, prev_value)
    )


def pvt_runner_install_pre_run_script(
    test_name: str, script_path: str, *,
    session: str, workspace: Any = None,
) -> str:
    """Attach + enable a pre-run script for ``test_name``.

    Wraps SKILL ``pvtRunnerInstallPreRunScript`` which calls
    ``axlImportPreRunScript`` followed by ``axlSetPreRunScriptEnabled``.
    The script fires in Maestro's worker virtuoso VM BEFORE each
    (test, corner) point is netlisted — see DECISIONS #57 stage-3.

    Returns the path Maestro recorded (usually identical to ``script_path``).
    """
    ws = workspace if workspace is not None else _open_workspace()
    _load_runner_skill_files(ws)
    return str(_unwrap(
        ws["pvtRunnerInstallPreRunScript"](session, test_name, script_path)
    ))


def pvt_runner_disable_pre_run_script(
    test_name: str, *, session: str, workspace: Any = None,
) -> None:
    """Disable the pre-run script for ``test_name``.

    Maestro has no "detach" API; disable is our equivalent. The script
    file on disk stays — orchestrator owns its cleanup.
    """
    ws = workspace if workspace is not None else _open_workspace()
    _load_runner_skill_files(ws)
    _unwrap(ws["pvtRunnerDisablePreRunScript"](session, test_name))


def pvt_runner_get_pre_run_script(
    test_name: str, *, session: str, workspace: Any = None,
) -> str:
    """Return the currently-attached pre-run script path for ``test_name``.

    ``""`` means no script attached (or attached-but-disabled — Maestro
    doesn't expose the enabled flag to a getter). Used by the orchestrator
    to snapshot user state before installing our script.
    """
    ws = workspace if workspace is not None else _open_workspace()
    _load_runner_skill_files(ws)
    raw = _unwrap(ws["pvtRunnerGetPreRunScript"](session, test_name))
    return str(raw) if raw else ""


def pvt_runner_snapshot_corners_enable(
    *, session: str, workspace: Any = None,
) -> list[tuple[str, bool]]:
    """Capture per-corner enable state for later restore.

    Returns a list of ``(corner_name, enabled_bool)`` pairs in the order
    ``axlGetCorners(sdb)`` returns names (which matches the explode order
    of the source union, which matches the /1, /2, ... result dir
    numbering). Pass the result back into
    :func:`pvt_runner_restore_corners_enable` after a per-corner loop.
    """
    ws = workspace if workspace is not None else _open_workspace()
    _load_runner_skill_files(ws)
    raw = _unwrap(ws["pvtRunnerSnapshotCornersEnable"](session))
    out: list[tuple[str, bool]] = []
    for pair in raw or []:
        if isinstance(pair, (list, tuple)) and len(pair) >= 2:
            out.append((str(pair[0]), bool(pair[1])))
    return out


def pvt_runner_enable_corner_by_index(
    idx: int, *, session: str, workspace: Any = None,
) -> str:
    """Disable every corner except the one at 1-based ``idx``.

    Returns the enabled corner's name (so the caller can log it).
    Raises :class:`SkillBridgeError` if ``idx`` is out of range.
    """
    if not isinstance(idx, int) or idx < 1:
        raise SkillBridgeError(
            "pvt_validation",
            f"idx must be a positive integer (1-based), got {idx!r}",
        )
    ws = workspace if workspace is not None else _open_workspace()
    _load_runner_skill_files(ws)
    return str(_unwrap(ws["pvtRunnerEnableCornerByIndex"](session, idx)))


def pvt_runner_restore_corners_enable(
    snap: list[tuple[str, bool]], *, session: str, workspace: Any = None,
) -> None:
    """Restore per-corner enable state from a snapshot.

    Pairs missing from ``snap`` (e.g. corners added between
    snapshot and restore) are silently no-op'd by the SKILL helper.
    """
    ws = workspace if workspace is not None else _open_workspace()
    _load_runner_skill_files(ws)
    # SKILL wants a list of [name, bool] pairs
    payload = [[name, bool(en)] for name, en in snap]
    _unwrap(ws["pvtRunnerRestoreCornersEnable"](session, payload))


def pvt_runner_get_sim_option_val(
    test_name: str, option_key: str,
    *, session: str, workspace: Any = None,
) -> Optional[str]:
    """Read one Spectre sim-option from a specific test's asi session.

    Wraps ``pvtRunnerGetSimOptionVal`` (Phase 3A v1.9 #2, DECISIONS #68).
    Strategies use this to auto-probe baseline values (e.g. ``gmin``)
    at apply-start instead of hardcoding them in the sidecar.

    Args:
        test_name:  test whose asi we should resolve (per-test via
                    ``axlGetToolSession`` + ``asiGetSession``).
        option_key: simulator option name (e.g. ``"gmin"``,
                    ``"additionalArgs"``, ``"reltol"``).
        session:    Maestro session name.

    Returns:
        The option value as a string when the option is set on that
        test's asi. Returns ``None`` when the option exists in the
        Spectre options catalog but isn't set on this test's asi.

    Raises:
        SkillBridgeError on category ``pvt_validation`` (bad arg) or
        ``pvt_runner_no_session`` (the per-test asi could not be
        reached — e.g. test name doesn't exist).
    """
    if not isinstance(test_name, str) or not test_name:
        raise SkillBridgeError(
            "pvt_validation", "test_name must be a non-empty string",
        )
    if not isinstance(option_key, str) or not option_key:
        raise SkillBridgeError(
            "pvt_validation", "option_key must be a non-empty string",
        )
    ws = workspace if workspace is not None else _open_workspace()
    _load_runner_skill_files(ws)
    try:
        raw = _unwrap(
            ws["pvtRunnerGetSimOptionVal"](session, test_name, option_key),
        )
    except SkillBridgeError as exc:
        # Option-not-set on this test's asi is a normal "no value" answer
        # for callers like gmin_bump's auto-probe; surface as None.
        if exc.category == "pvt_runner_no_option":
            return None
        raise
    return str(raw) if raw is not None else None


def pvt_runner_delete_history(
    history_name: str, *, session: str, workspace: Any = None,
) -> None:
    """Drop a named history entry from the session.

    Cleanup helper. Raises SkillBridgeError if the history isn't found
    or Maestro refuses the delete.
    """
    ws = workspace if workspace is not None else _open_workspace()
    _load_runner_skill_files(ws)
    _unwrap(ws["pvtRunnerDeleteHistory"](session, history_name))


# v1.8 #4 — history-lock surface (DECISIONS #65).
#
# Two bound APIs are available (verified live 2026-05-18):
# - `axlSetHistoryLock(historyHandle, t|nil)` — handle-based; needs
#   `axlGetHistoryEntry(hsdb, name)` to resolve a string to a handle.
# - `maeSetHistoryLock(name, t|nil, ?session sess)` — name-based, much
#   simpler from Python. NO `maeGetHistoryLock` exists; reads must go
#   through the axl* path.
#
# We use mae* for the setter (less ceremony) and axl* for the reader.


def pvt_runner_set_history_lock(
    history_name: str, lock: bool, *,
    session: str, workspace: Any = None,
) -> None:
    """Lock or unlock a Maestro history entry.

    A locked entry cannot be deleted by Maestro (GUI or API) and survives
    routine cleanup. simkit uses this to sync the user's ``runs.starred``
    flag to the GUI so a starred run can't be accidentally wiped from
    the session.

    Raises :class:`SkillBridgeError` if the history isn't found or
    Maestro refuses the change.
    """
    ws = workspace if workspace is not None else _open_workspace()
    expr = (
        f'(if (maeSetHistoryLock "{_escape_skill_string(history_name)}" '
        f'{"t" if lock else "nil"} '
        f'?session "{_escape_skill_string(session)}") "T" "nil")'
    )
    result = ws["evalstring"](expr)
    if result != "T":
        raise SkillBridgeError(
            "lock_failed",
            f"maeSetHistoryLock({history_name!r}, "
            f"{'t' if lock else 'nil'}) returned nil",
        )


def pvt_runner_get_history_lock_map(
    *, session: str, workspace: Any = None,
) -> dict[str, bool]:
    """Return ``{history_name: locked_bool}`` for every entry in the session.

    Walks ``axlGetHistory(hsdb)`` to enumerate names, then probes each via
    ``axlGetHistoryEntry`` + ``axlGetHistoryLock``. Read-only; safe to call
    at any time without side effects.
    """
    ws = workspace if workspace is not None else _open_workspace()
    sess_esc = _escape_skill_string(session)
    # hsdb is the int-keyed setup-db handle for the session; cache it once.
    hsdb = ws["evalstring"](f'(sprintf nil "%d" (axlGetMainSetupDB "{sess_esc}"))')
    hsdb_int = int(hsdb)
    # Pull the name list + lock state inline as a "name|T|name|nil|..."
    # string so skillbridge's translator only has to decode a flat string.
    expr = (
        f'(let ((info (axlGetHistory {hsdb_int})) (out ""))'
        f'  (foreach name (cadr info)'
        f'    (let ((ent (axlGetHistoryEntry {hsdb_int} name)))'
        f'      (setq out (strcat out (sprintf nil "%s\\t%s\\n"'
        f'                                     name'
        f'                                     (if (axlGetHistoryLock ent) "T" "nil"))))))'
        f'  out)'
    )
    raw = ws["evalstring"](expr)
    out: dict[str, bool] = {}
    for line in (raw or "").splitlines():
        if not line:
            continue
        name, _, flag = line.partition("\t")
        out[name] = (flag == "T")
    return out


def _escape_skill_string(s: str) -> str:
    """Escape a Python string for embedding inside a SKILL string literal.

    Backslash + double-quote only; SKILL string literals don't need full
    JSON escaping. Newlines / tabs are rejected outright because we don't
    want them in history names anyway.
    """
    if "\n" in s or "\r" in s or "\t" in s:
        raise SkillBridgeError(
            "bad_history_name",
            f"history name must not contain newline/tab: {s!r}",
        )
    return s.replace("\\", "\\\\").replace('"', '\\"')


def pvt_save(
    history_name: str, *,
    pvtproject_path: Path,
    session: Optional[str] = None,
    label: Optional[str] = None,
    note: Optional[str] = None,
    workspace: Any = None,
) -> str:
    """Invoke the Phase 1 collector's PvtSave on a named history.

    Returns the absolute path of the run-dir that PvtSave wrote (which the
    Python ingester then walks via `pvt ingest`).

    Pins PVT_PROJECT on the SKILL side (same pattern as `_prep`) so PvtSave's
    `pvtLoadPvtProject` doesn't fall back to a cwd walker that wouldn't find
    anything (the bridge process runs from Virtuoso's cwd, not Python's).
    """
    ws = workspace if workspace is not None else _open_workspace()
    _load_runner_skill_files(ws)
    pvtproject_path = Path(pvtproject_path).expanduser().resolve()
    with _restore_cwd(ws):
        ws["changeWorkingDir"](str(pvtproject_path.parent))
        ws["setShellEnvVar"](f"PVT_PROJECT={pvtproject_path}")
        kwargs: dict[str, Any] = {"histName": history_name}
        if label is not None:
            kwargs["label"] = label
        if note is not None:
            kwargs["note"] = note
        if session is not None:
            kwargs["explicitSess"] = session
        raw = ws["PvtSave"](**kwargs)
        # PvtSave returns (pvt_ok run-dir-path) on success.
        return _unwrap(raw)


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
