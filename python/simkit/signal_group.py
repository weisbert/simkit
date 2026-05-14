"""`.siggroup.json` sidecar loader for signal groups.

Implements the Phase 3B §3.3 contract (docs/phase3b_measure_template_spec.md).
Pure-Python, stdlib-only. A signal group is a named ordered list of signal
paths with no metadata (per DECISIONS #39 P3B.F1).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from simkit.errors import SimkitError


SIGNAL_GROUP_FILE_SUFFIX = ".siggroup.json"

_SIGNAL_GROUP_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

_SUPPORTED_SIGNAL_GROUP_SCHEMA_VERSIONS = frozenset({1})


class SignalGroupError(SimkitError):
    """Base class for `.siggroup.json` loader errors."""


class SignalGroupSchemaVersionError(SignalGroupError):
    """A sidecar declared a ``signal_group_schema_version`` the loader does not support."""


class SignalGroupMalformedError(SignalGroupError):
    """A sidecar is unreadable / not parseable as JSON / not a JSON object."""


class SignalGroupLoadError(SignalGroupError):
    """A sidecar parsed cleanly but failed schema validation per spec §3.3."""


@dataclass(frozen=True)
class SignalGroup:
    signal_group_schema_version: int
    name: str
    signals: tuple[str, ...]
    source_path: Path


def load_signal_group(path: Path | str) -> SignalGroup:
    p = Path(path).expanduser().resolve()

    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise SignalGroupMalformedError(f"{p}: invalid JSON — {exc}") from exc
    except OSError as exc:
        raise SignalGroupMalformedError(f"{p}: cannot read — {exc}") from exc

    if not isinstance(data, dict):
        raise SignalGroupMalformedError(
            f"{p}: top-level must be a JSON object, got {type(data).__name__}"
        )

    schema_version = _validate_schema_version(p, data)
    name = _validate_name(p, data)
    signals = _validate_signals(p, data)

    return SignalGroup(
        signal_group_schema_version=schema_version,
        name=name,
        signals=signals,
        source_path=p,
    )


def _validate_schema_version(path: Path, data: dict) -> int:
    if "signal_group_schema_version" not in data:
        raise SignalGroupSchemaVersionError(
            f"{path}: missing required field 'signal_group_schema_version'"
        )
    raw = data["signal_group_schema_version"]
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise SignalGroupSchemaVersionError(
            f"{path}: 'signal_group_schema_version' must be an integer"
        )
    if raw not in _SUPPORTED_SIGNAL_GROUP_SCHEMA_VERSIONS:
        raise SignalGroupSchemaVersionError(
            f"{path}: signal_group_schema_version {raw} not supported "
            f"(supported: {sorted(_SUPPORTED_SIGNAL_GROUP_SCHEMA_VERSIONS)})"
        )
    return raw


def _validate_name(path: Path, data: dict) -> str:
    if "name" not in data:
        raise SignalGroupLoadError(f"{path}: missing required field 'name'")
    name = data["name"]
    if not isinstance(name, str) or name == "":
        raise SignalGroupLoadError(
            f"{path}: 'name' must be a non-empty string"
        )
    if not _SIGNAL_GROUP_NAME_RE.match(name):
        raise SignalGroupLoadError(
            f"{path}: 'name' {name!r} does not match ^[a-z][a-z0-9_]*$"
        )
    basename = path.name
    if not basename.endswith(SIGNAL_GROUP_FILE_SUFFIX):
        raise SignalGroupLoadError(
            f"{path}: filename must end with '{SIGNAL_GROUP_FILE_SUFFIX}' "
            f"(got {basename!r})"
        )
    expected = basename[: -len(SIGNAL_GROUP_FILE_SUFFIX)]
    if expected != name:
        raise SignalGroupLoadError(
            f"{path}: 'name' {name!r} must equal filename basename "
            f"{expected!r}"
        )
    return name


def _validate_signals(path: Path, data: dict) -> tuple[str, ...]:
    if "signals" not in data:
        raise SignalGroupLoadError(f"{path}: missing required field 'signals'")
    raw = data["signals"]
    if not isinstance(raw, list):
        raise SignalGroupLoadError(f"{path}: 'signals' must be a JSON array")
    if len(raw) == 0:
        raise SignalGroupLoadError(f"{path}: 'signals' must be non-empty")

    seen: set[str] = set()
    out: list[str] = []
    for i, raw_sig in enumerate(raw):
        if not isinstance(raw_sig, str):
            raise SignalGroupLoadError(
                f"{path}: signals[{i}] must be a string "
                f"(got {type(raw_sig).__name__})"
            )
        if raw_sig == "":
            raise SignalGroupLoadError(
                f"{path}: signals[{i}] must be a non-empty string"
            )
        if not raw_sig.startswith("/"):
            raise SignalGroupLoadError(
                f"{path}: signals[{i}] {raw_sig!r} must start with '/'"
            )
        if raw_sig in seen:
            raise SignalGroupLoadError(
                f"{path}: signals[{i}] duplicates earlier path {raw_sig!r}"
            )
        seen.add(raw_sig)
        out.append(raw_sig)
    return tuple(out)


def signal_basename(signal_path: str) -> str:
    """Last `/`-separated segment of a signal path, no leading `/`.

    `/Vout` → `Vout`; `/buf/y` → `y`. Used by template_render for output names.
    """
    if not signal_path.startswith("/"):
        raise ValueError(
            f"signal path must start with '/' (got {signal_path!r})"
        )
    return signal_path.rsplit("/", 1)[-1]
