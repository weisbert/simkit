"""User-level PVT pattern preset library — ``~/.simkit/pattern_presets.json``.

Lets a user save a pattern authored in one project and re-use it across
projects (2026 UX). Distinct from ``cm.patterns``, which is per-project:
this store is per-user. Pointing ``SIMKIT_HOME`` at a shared directory
turns it into a team-shared library for free.

The on-disk pattern shape matches the cornermodel's ``patterns[*]`` entry
(``{enabled, name, corners: [{enabled, name, *_levels}]}``), so a preset
and a project pattern are interchangeable.

Loading is crash-free: a missing / unreadable / malformed file yields an
empty preset set rather than raising — mirrors ``gui/state.py``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from simkit.corner_model import PvtCornerEntry, PvtPattern

SCHEMA_VERSION = 1
PRESETS_FILENAME = "pattern_presets.json"


def presets_path() -> Path:
    """Canonical ``~/.simkit/pattern_presets.json`` (``SIMKIT_HOME`` override
    respected, same as :func:`simkit.gui.state.app_state_path`)."""
    override = os.environ.get("SIMKIT_HOME")
    if override:
        return Path(override).expanduser() / PRESETS_FILENAME
    return Path.home() / ".simkit" / PRESETS_FILENAME


def _corner_to_dict(c: PvtCornerEntry) -> dict[str, Any]:
    return {
        "enabled": c.enabled,
        "name": c.name,
        "process_levels": list(c.process_levels),
        "voltage_levels": list(c.voltage_levels),
        "temperature_levels": list(c.temperature_levels),
    }


def _pattern_to_dict(p: PvtPattern) -> dict[str, Any]:
    return {
        "enabled": p.enabled,
        "name": p.name,
        "corners": [_corner_to_dict(c) for c in p.corners],
    }


def _str_tuple(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(v for v in raw if isinstance(v, str))


def _corner_from_dict(raw: Any) -> Optional[PvtCornerEntry]:
    if not isinstance(raw, dict):
        return None
    name = raw.get("name", "")
    if not isinstance(name, str):
        name = ""
    return PvtCornerEntry(
        enabled=bool(raw.get("enabled", True)),
        name=name,
        process_levels=_str_tuple(raw.get("process_levels")),
        voltage_levels=_str_tuple(raw.get("voltage_levels")),
        temperature_levels=_str_tuple(raw.get("temperature_levels")),
    )


def _pattern_from_dict(name: str, raw: Any) -> Optional[PvtPattern]:
    if not isinstance(raw, dict):
        return None
    raw_corners = raw.get("corners")
    if isinstance(raw_corners, list):
        corners = tuple(
            c for c in (_corner_from_dict(rc) for rc in raw_corners)
            if c is not None
        )
    else:
        # Tolerate a legacy flat pattern (level tuples on the pattern
        # itself) by promoting it to a single-corner pattern.
        legacy = _corner_from_dict(raw)
        corners = (legacy,) if legacy is not None else ()
    return PvtPattern(
        enabled=bool(raw.get("enabled", True)),
        name=name,
        corners=corners,
    )


def load_user_presets(path: Optional[Path] = None) -> dict[str, PvtPattern]:
    """Read the user preset library. Never raises — a missing / corrupt
    file returns ``{}``. The returned mapping is name → PvtPattern, with
    each pattern's ``name`` forced to its key."""
    p = Path(path) if path is not None else presets_path()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    raw_presets = raw.get("presets")
    if not isinstance(raw_presets, dict):
        return {}
    out: dict[str, PvtPattern] = {}
    for name, body in raw_presets.items():
        if not isinstance(name, str):
            continue
        pat = _pattern_from_dict(name, body)
        if pat is not None:
            out[name] = pat
    return out


def _write_presets(
    presets: dict[str, PvtPattern], path: Optional[Path] = None,
) -> Path:
    p = Path(path) if path is not None else presets_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "schema_version": SCHEMA_VERSION,
        "presets": {
            name: _pattern_to_dict(pat) for name, pat in presets.items()
        },
    }
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(
        json.dumps(doc, indent=2, sort_keys=True), encoding="utf-8"
    )
    tmp.replace(p)
    return p


def save_user_preset(
    name: str, pattern: PvtPattern, path: Optional[Path] = None,
) -> None:
    """Add / overwrite the named preset, persisting the whole library. The
    saved pattern's ``name`` is forced to ``name`` so the key and the
    pattern agree."""
    from dataclasses import replace

    presets = load_user_presets(path)
    presets[name] = replace(pattern, name=name)
    _write_presets(presets, path)


def delete_user_preset(name: str, path: Optional[Path] = None) -> None:
    """Remove the named preset if present (no-op otherwise)."""
    presets = load_user_presets(path)
    if name in presets:
        del presets[name]
        _write_presets(presets, path)
