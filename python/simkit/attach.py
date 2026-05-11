"""Post-hoc artifact attach.

Implements the data-layer side of ``pvt attach`` (TODO §5). Copies a
source file into ``<runs_root>/<run_id>/artifacts/`` and inserts an
``artifacts`` row with ``source='manual'``.

Per ``docs/schema.md`` §2.3 / §4: the artifact file lives under the run's
dump directory; ``artifacts.relative_path`` is stored relative to that
directory (e.g. ``artifacts/measurement.png``).

Transactional contract: the file copy AND the DB insert succeed together
or neither happens. On DB failure we delete the freshly-copied file; on
copy failure we never touch the DB. We do NOT roll back a pre-existing
destination file on caller mistake — that case raises before any work.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import duckdb

from simkit.db import transaction
from simkit.errors import (
    DuplicateArtifactError,
    InvalidArtifactTypeError,
    RunNotFoundError,
    SimkitError,
)
from simkit.validate import VALID_ARTIFACT_TYPES


@dataclass(frozen=True)
class AttachResult:
    run_id: str
    artifact_type: str
    relative_path: str
    absolute_path: Path
    description: Optional[str]
    created_at: str


def attach_artifact(
    con: duckdb.DuckDBPyConnection,
    *,
    run_id: str,
    src_path: Path,
    artifact_type: str,
    runs_root: Path,
    description: Optional[str] = None,
    dest_name: Optional[str] = None,
    now: Optional[Callable[[], datetime]] = None,
) -> AttachResult:
    """Copy ``src_path`` into the run's ``artifacts/`` dir and record it.

    Parameters
    ----------
    con
        Open DuckDB connection. Bootstrapped tables expected.
    run_id
        Must already exist in the ``runs`` table.
    src_path
        Source file. Must be an existing regular file.
    artifact_type
        One of ``VALID_ARTIFACT_TYPES`` (see ``docs/schema.md`` §2.3).
    runs_root
        Absolute path to ``<dbRoot>/runs/``. The destination is
        ``<runs_root>/<run_id>/artifacts/<dest_name or src basename>``.
    description
        Optional human-readable note. Stored verbatim.
    dest_name
        Optional override for the destination filename. Used when the
        source basename would collide with an existing artifact.
    now
        Injectable clock for tests.

    Raises
    ------
    RunNotFoundError
        ``run_id`` is not in the ``runs`` table.
    InvalidArtifactTypeError
        ``artifact_type`` is not in :data:`VALID_ARTIFACT_TYPES`.
    DuplicateArtifactError
        The chosen ``relative_path`` is already in ``artifacts`` for this
        run, or the destination file already exists on disk.
    MissingDumpError
        ``src_path`` does not exist or is not a regular file.
    """
    src = Path(src_path).expanduser().resolve()
    if not src.is_file():
        # Reuse the same error class the ingester uses for IO failures.
        from simkit.errors import MissingDumpError
        raise MissingDumpError(f"not a file: {src}")

    if artifact_type not in VALID_ARTIFACT_TYPES:
        raise InvalidArtifactTypeError(
            f"unknown artifact type {artifact_type!r}; "
            f"valid: {sorted(VALID_ARTIFACT_TYPES)}"
        )

    row = con.execute(
        "SELECT 1 FROM runs WHERE run_id = ?", [run_id]
    ).fetchone()
    if row is None:
        raise RunNotFoundError(
            f"run_id {run_id!r} not found in DB"
        )

    filename = dest_name if dest_name is not None else src.name
    if "/" in filename or filename in ("", ".", ".."):
        raise SimkitError(
            f"invalid dest_name {filename!r}: must be a bare filename"
        )

    relative_path = f"artifacts/{filename}"

    existing = con.execute(
        "SELECT 1 FROM artifacts WHERE run_id = ? AND relative_path = ?",
        [run_id, relative_path],
    ).fetchone()
    if existing is not None:
        raise DuplicateArtifactError(
            f"artifact {relative_path!r} already exists for run {run_id!r}; "
            "pass --as <new_name> to rename"
        )

    dest_dir = Path(runs_root).expanduser().resolve() / run_id / "artifacts"
    dest_file = dest_dir / filename
    if dest_file.exists():
        raise DuplicateArtifactError(
            f"destination file already exists: {dest_file}; "
            "pass --as <new_name> to rename"
        )

    now_fn = now if now is not None else (lambda: datetime.now(timezone.utc))
    created = now_fn()
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    created_at = created.isoformat()

    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest_file)
    try:
        with transaction(con):
            con.execute(
                """
                INSERT INTO artifacts (
                  run_id, type, relative_path,
                  description, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    run_id, artifact_type, relative_path,
                    description, "manual", created_at,
                ],
            )
    except BaseException:
        # Best-effort: undo the copy so the FS and DB stay aligned.
        try:
            dest_file.unlink()
        except OSError:
            pass
        raise

    return AttachResult(
        run_id=run_id,
        artifact_type=artifact_type,
        relative_path=relative_path,
        absolute_path=dest_file,
        description=description,
        created_at=created_at,
    )
