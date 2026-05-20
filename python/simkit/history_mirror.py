"""Mirror pre-existing Maestro history into simkit's DuckDB.

simkit only ingests runs it orchestrates itself. Histories produced
directly in Maestro (or by other tools) never reach the ``runs`` table,
so the GUI's History panel looks empty next to Maestro's own list.

:func:`mirror_maestro_history` closes that gap: it enumerates every
history entry in the live session, skips the ones already ingested
(matched on ``runs.history_name``), and for each remaining one runs the
existing Phase-1 collector (``pvt_save``) + ingester (``ingest_run_json``).

It composes existing building blocks only — no new SKILL. The collector
``PvtSave`` already accepts an arbitrary ``histName`` and synthesises a
run.json for histories simkit did not create.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class MirrorReport:
    """Outcome of one :func:`mirror_maestro_history` call."""

    mirrored: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mirrored": list(self.mirrored),
            "skipped": list(self.skipped),
            "failed": list(self.failed),
        }

    def summary_line(self) -> str:
        return (
            f"{len(self.mirrored)} mirrored / "
            f"{len(self.skipped)} already present / "
            f"{len(self.failed)} failed"
        )


def _db_path_for(pvtproject_path: Path) -> Path:
    from simkit.project import _parse_pvtproject

    proj = _parse_pvtproject(Path(pvtproject_path).expanduser().resolve())
    return proj.db_root / "simkit.duckdb"


def _existing_history_names(con: Any) -> set[str]:
    rows = con.execute(
        "SELECT DISTINCT history_name FROM runs "
        "WHERE history_name IS NOT NULL"
    ).fetchall()
    return {r[0] for r in rows if r[0]}


def mirror_maestro_history(
    *,
    pvtproject_path: str | Path,
    session: str,
    bridge: Any = None,
    workspace: Any = None,
) -> dict[str, Any]:
    """Ingest every Maestro history entry not already in the project DB.

    ``pvtproject_path`` / ``session`` are the only kwargs the GUI bridge
    dispatcher supplies; ``bridge`` / ``workspace`` exist for test
    injection and connection reuse.

    Returns :meth:`MirrorReport.as_dict` — ``{mirrored, skipped, failed}``.
    A single history that fails to collect or ingest is recorded in
    ``failed`` and does not abort the rest of the sweep.
    """
    if bridge is None:
        from simkit import skill_bridge as bridge

    from simkit.db import bootstrap, connect
    from simkit.ingest import ingest_run_json

    pvtproject_path = Path(pvtproject_path).expanduser().resolve()
    ws = workspace if workspace is not None else bridge._open_workspace()

    lock_map = bridge.pvt_runner_get_history_lock_map(
        session=session, workspace=ws
    )
    history_names = sorted(lock_map.keys())

    report = MirrorReport()
    db_path = _db_path_for(pvtproject_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = connect(db_path)
    try:
        bootstrap(con)
        already = _existing_history_names(con)
        for name in history_names:
            if name in already:
                report.skipped.append(name)
                continue
            try:
                run_dir = bridge.pvt_save(
                    name,
                    pvtproject_path=pvtproject_path,
                    session=session,
                    label=name,
                    workspace=ws,
                )
                run_json = Path(run_dir)
                if run_json.is_dir():
                    run_json = run_json / "run.json"
                ingest_run_json(con, run_json)
            except Exception as exc:  # noqa: BLE001 - one bad history is not fatal
                report.failed.append({"history": name, "error": str(exc)})
            else:
                report.mirrored.append(name)
    finally:
        con.close()

    return report.as_dict()
