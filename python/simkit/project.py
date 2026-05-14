"""`.pvtproject` loader.

Implements the layered-lookup + schema rules from DECISIONS #6 / #13
and docs/schema.md §1. Pure-Python, stdlib-only, no Cadence dependency.
"""

from __future__ import annotations

import json
import os
import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional


PVTPROJECT_FILENAME = ".pvtproject"
ENV_VAR = "PVT_PROJECT"

_PROJECT_NAME_RE = re.compile(r"^[a-z0-9_-]+$")
_SUPPORTED_SCHEMA_VERSIONS = frozenset({1})
_KNOWN_FIELDS = frozenset({
    "project",
    "dbRoot",
    "author",
    "testbench_aliases",
    "schema_version",
    # Phase 2 §1 — `.union.json` sidecar dir override.
    "unionsDir",
    # Phase 3B §1 (DECISIONS #41) — measurement-template authoring layer dirs.
    "templatesDir",
    "signalGroupsDir",
    "measurementsDir",
})


class PvtProjectError(Exception):
    """Base class for `.pvtproject` loader errors."""


class PvtProjectNotFoundError(PvtProjectError):
    """Neither `PVT_PROJECT` nor the cwd-walker produced a usable `.pvtproject` path."""


class PvtProjectValidationError(PvtProjectError):
    """A `.pvtproject` was located but failed schema validation."""


@dataclass(frozen=True)
class PvtProject:
    project: str
    db_root: Path
    author: Optional[str]
    testbench_aliases: Mapping[str, str]
    schema_version: int
    source_path: Path

    def alias_for(self, testbench_id: str) -> Optional[str]:
        return self.testbench_aliases.get(testbench_id)


def find_pvtproject(start: Optional[Path] = None) -> Optional[Path]:
    """Walk up from ``start`` (or cwd) looking for a `.pvtproject` file.

    Returns the absolute path of the first match, or ``None`` if the walk
    reaches the filesystem root without finding one.
    """
    cur = Path(start).resolve() if start is not None else Path.cwd().resolve()
    if cur.is_file():
        cur = cur.parent
    while True:
        candidate = cur / PVTPROJECT_FILENAME
        if candidate.is_file():
            return candidate
        if cur.parent == cur:
            return None
        cur = cur.parent


def load_pvtproject(
    start: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
) -> PvtProject:
    """Load a `.pvtproject` per the DECISIONS #6 fallback order.

    1. ``PVT_PROJECT`` env var — absolute path to the `.pvtproject` file.
       If set but invalid, fail fast (no silent fallback).
    2. Walk up from ``start`` (or cwd) looking for `.pvtproject`.
    3. If neither yields a path, raise ``PvtProjectNotFoundError``.

    The interactive "first-save dialog" fallback from #6 is SKILL-side only;
    Python (CLI / batch) treats "not found" as a hard error.
    """
    env_map = os.environ if env is None else env

    env_val = env_map.get(ENV_VAR)
    if env_val:
        path = Path(env_val).expanduser()
        if not path.is_file():
            raise PvtProjectNotFoundError(
                f"{ENV_VAR}={env_val!r} does not point to an existing file"
            )
        return _parse_pvtproject(path)

    found = find_pvtproject(start)
    if found is None:
        origin = Path(start).resolve() if start is not None else Path.cwd().resolve()
        raise PvtProjectNotFoundError(
            f"no {PVTPROJECT_FILENAME} found walking up from {origin} "
            f"and {ENV_VAR} is not set"
        )
    return _parse_pvtproject(found)


def _parse_pvtproject(path: Path) -> PvtProject:
    path = path.resolve()

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise PvtProjectValidationError(f"{path}: invalid JSON — {exc}") from exc
    except OSError as exc:
        raise PvtProjectValidationError(f"{path}: cannot read — {exc}") from exc

    if not isinstance(data, dict):
        raise PvtProjectValidationError(
            f"{path}: top-level must be a JSON object, got {type(data).__name__}"
        )

    schema_version = _validate_schema_version(path, data)
    project = _validate_project(path, data)
    db_root = _validate_db_root(path, data)
    author = _validate_author(path, data)
    aliases = _validate_aliases(path, data)

    _warn_unknown_keys(path, data)

    return PvtProject(
        project=project,
        db_root=db_root,
        author=author,
        testbench_aliases=aliases,
        schema_version=schema_version,
        source_path=path,
    )


def _validate_schema_version(path: Path, data: dict) -> int:
    raw = data.get("schema_version", 1)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise PvtProjectValidationError(
            f"{path}: 'schema_version' must be an integer"
        )
    if raw not in _SUPPORTED_SCHEMA_VERSIONS:
        raise PvtProjectValidationError(
            f"{path}: schema_version {raw} not supported "
            f"(supported: {sorted(_SUPPORTED_SCHEMA_VERSIONS)})"
        )
    return raw


def _validate_project(path: Path, data: dict) -> str:
    if "project" not in data:
        raise PvtProjectValidationError(f"{path}: missing required field 'project'")
    value = data["project"]
    if not isinstance(value, str):
        raise PvtProjectValidationError(f"{path}: 'project' must be a string")
    if not _PROJECT_NAME_RE.match(value):
        raise PvtProjectValidationError(
            f"{path}: 'project' {value!r} does not match ^[a-z0-9_-]+$"
        )
    return value


def _validate_db_root(path: Path, data: dict) -> Path:
    if "dbRoot" not in data:
        raise PvtProjectValidationError(f"{path}: missing required field 'dbRoot'")
    raw = data["dbRoot"]
    if not isinstance(raw, str) or raw == "":
        raise PvtProjectValidationError(
            f"{path}: 'dbRoot' must be a non-empty string"
        )
    resolved = Path(raw).expanduser()
    if not resolved.is_absolute():
        resolved = (path.parent / resolved).resolve()
    else:
        resolved = resolved.resolve()
    return resolved


def _validate_author(path: Path, data: dict) -> Optional[str]:
    if "author" not in data:
        return None
    value = data["author"]
    if value is None:
        return None
    if not isinstance(value, str):
        raise PvtProjectValidationError(f"{path}: 'author' must be a string if set")
    return value


def _validate_aliases(path: Path, data: dict) -> Mapping[str, str]:
    raw = data.get("testbench_aliases", {})
    if not isinstance(raw, dict):
        raise PvtProjectValidationError(
            f"{path}: 'testbench_aliases' must be a JSON object"
        )
    for k, v in raw.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise PvtProjectValidationError(
                f"{path}: 'testbench_aliases' keys and values must be strings"
            )
    seen: dict[str, str] = {}
    for k, v in raw.items():
        if v in seen:
            raise PvtProjectValidationError(
                f"{path}: duplicate alias {v!r} "
                f"(used by both {seen[v]!r} and {k!r})"
            )
        seen[v] = k
    return dict(raw)


def _warn_unknown_keys(path: Path, data: dict) -> None:
    for key in data:
        if key in _KNOWN_FIELDS or key.startswith("_"):
            continue
        warnings.warn(
            f"{path}: unknown key {key!r} (ignored)",
            stacklevel=4,
        )
