"""Run provenance — record the conditions a run was produced under (G-5).

A signoff margin table is only as trustworthy as a reviewer's ability
to prove each number against its origin: which host ran it, which
model-file revision, which PDK, when. simkit's ``run.json`` already
keeps the netlist + timestamp + corner vars; this module adds the
missing axis (FDR-5, E-3, E-5).

Capture strategy is **orchestrator injection** (the chosen design): the
``pvt run`` orchestrator calls :func:`inject_run_provenance` on the
freshly-saved ``run.json`` just before ingest. That covers the GUI
Tier-1 path (the GUI runs ``pvt run`` as a subprocess) and the CLI
path; a manual ``PvtSave`` that bypasses the orchestrator simply has no
``provenance`` block and the DB column stays NULL.

Everything here is best-effort and pure-stdlib: a provenance failure
must never break a run's ingest, so :func:`inject_run_provenance` never
raises.

Provenance block shape (top-level key ``provenance`` in run.json)::

    {
      "host":         "rhel7-farm-03",
      "captured_at":  "2026-05-20T22:14:05+08:00",
      "pdk_version":  "rf018_v1.9" | null,
      "model_files": [
        {"path": "/pdk/rf018.scs", "exists": true,
         "size": 184320, "mtime": "2026-04-02T09:11:00+08:00"},
        ...
      ]
    }
"""

from __future__ import annotations

import json
import logging
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_LOG = logging.getLogger(__name__)

PROVENANCE_KEY = "provenance"

# Environment variable a site can set to stamp the PDK revision onto
# every run. PDK version has no clean programmatic accessor, so this
# (or a `.pvtproject` "pdk_version" field) is the honest best we can do.
_PDK_ENV_VAR = "PVT_PDK_VERSION"


def build_provenance(
    *,
    union_path: Optional[Path] = None,
    pvtproject_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Assemble the provenance block for a run.

    ``union_path`` is the ``.union.json`` the run used — every distinct
    model file it references gets fingerprinted. ``pvtproject_path`` is
    consulted (after the env var) for a ``pdk_version`` field.
    """
    return {
        "host": _hostname(),
        "captured_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "pdk_version": _detect_pdk_version(pvtproject_path),
        "model_files": _model_file_fingerprints(union_path),
    }


def inject_run_provenance(
    run_dir: Path,
    *,
    union_path: Optional[Path] = None,
    pvtproject_path: Optional[Path] = None,
) -> bool:
    """Write a ``provenance`` block into ``run_dir``'s ``run.json``.

    ``run_dir`` may be the run directory or the ``run.json`` path
    itself. Best-effort: any failure is logged and swallowed (returns
    ``False``) so a provenance hiccup never aborts a run's ingest.
    Returns ``True`` when the block was written.
    """
    try:
        path = Path(run_dir)
        if path.is_dir():
            path = path / "run.json"
        if not path.is_file():
            _LOG.warning("provenance: run.json not found at %s", path)
            return False
        dump = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(dump, dict):
            _LOG.warning("provenance: %s is not a JSON object", path)
            return False
        dump[PROVENANCE_KEY] = build_provenance(
            union_path=union_path, pvtproject_path=pvtproject_path,
        )
        path.write_text(
            json.dumps(dump, indent=2, sort_keys=False), encoding="utf-8",
        )
        return True
    except Exception as exc:  # noqa: BLE001 — provenance must never abort ingest
        _LOG.warning("provenance: injection failed for %s: %s", run_dir, exc)
        return False


def load_provenance(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    """Parse a ``runs.provenance`` column value back into a dict.

    Returns ``None`` for a NULL / empty / unparseable value — callers
    treat that as "this run predates provenance capture".
    """
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def compare_provenance(
    a: Optional[Dict[str, Any]],
    b: Optional[Dict[str, Any]],
) -> List[str]:
    """Describe how two runs' conditions differ.

    Returns a list of human-readable mismatch lines — empty when the two
    runs ran under the same host / PDK / model files. When either side
    is ``None`` (a run with no provenance) a single "unknown" line is
    returned rather than a false "match", because an unprovable run is
    exactly the risk G-5 exists to surface.
    """
    if a is None or b is None:
        return ["其中一个 run 没有 provenance 记录,无法证明条件一致"]

    out: List[str] = []
    if a.get("host") != b.get("host"):
        out.append(
            f"host 不同: {a.get('host')!r} vs {b.get('host')!r}"
        )
    if a.get("pdk_version") != b.get("pdk_version"):
        out.append(
            f"PDK 版本不同: {a.get('pdk_version')!r} vs "
            f"{b.get('pdk_version')!r}"
        )
    out.extend(_model_file_mismatches(
        a.get("model_files") or [], b.get("model_files") or [],
    ))
    return out


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------

def _hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:  # noqa: BLE001 — pragma: no cover
        return "unknown"


def _detect_pdk_version(pvtproject_path: Optional[Path]) -> Optional[str]:
    env = os.environ.get(_PDK_ENV_VAR)
    if env:
        return env.strip()
    if pvtproject_path is not None:
        try:
            data = json.loads(
                Path(pvtproject_path).read_text(encoding="utf-8")
            )
            if isinstance(data, dict):
                v = data.get("pdk_version")
                if isinstance(v, str) and v.strip():
                    return v.strip()
        except Exception:  # noqa: BLE001 — best-effort
            pass
    return None


def _model_file_fingerprints(
    union_path: Optional[Path],
) -> List[Dict[str, Any]]:
    """Fingerprint every distinct model file the union references."""
    if union_path is None:
        return []
    paths = _collect_model_paths(union_path)
    return [_fingerprint(p) for p in paths]


def _collect_model_paths(union_path: Path) -> List[str]:
    """Distinct model-file paths across all rows of a union (order-stable)."""
    try:
        from simkit.union import load_union

        union = load_union(union_path)
    except Exception as exc:  # noqa: BLE001 — best-effort
        _LOG.warning("provenance: cannot load union %s: %s", union_path, exc)
        return []
    seen: set[str] = set()
    out: List[str] = []
    for row in union.rows:
        for model in row.models:
            # file_abs is the resolved path push uses; fall back to the
            # bare `file` name when a sidecar predates _file_abs.
            ref = model.file_abs or model.file
            if ref and ref not in seen:
                seen.add(ref)
                out.append(ref)
    return out


def _fingerprint(path_str: str) -> Dict[str, Any]:
    """Stat one model file into a {path, exists, size, mtime} record."""
    record: Dict[str, Any] = {
        "path": path_str,
        "exists": False,
        "size": None,
        "mtime": None,
    }
    try:
        p = Path(path_str)
        if p.is_file():
            st = p.stat()
            record["exists"] = True
            record["size"] = st.st_size
            record["mtime"] = datetime.fromtimestamp(
                st.st_mtime, tz=timezone.utc,
            ).astimezone().isoformat()
    except Exception:  # noqa: BLE001 — best-effort
        pass
    return record


def _model_file_mismatches(
    a_files: List[Dict[str, Any]],
    b_files: List[Dict[str, Any]],
) -> List[str]:
    a_by_path = {f.get("path"): f for f in a_files}
    b_by_path = {f.get("path"): f for f in b_files}

    out: List[str] = []
    only_a = sorted(set(a_by_path) - set(b_by_path))
    only_b = sorted(set(b_by_path) - set(a_by_path))
    for p in only_a:
        out.append(f"model 文件只在 A 中: {p}")
    for p in only_b:
        out.append(f"model 文件只在 B 中: {p}")
    for p in sorted(set(a_by_path) & set(b_by_path)):
        fa, fb = a_by_path[p], b_by_path[p]
        # A different size or mtime means the model file changed between
        # the two runs even though the path is the same.
        if (fa.get("size"), fa.get("mtime")) != (fb.get("size"), fb.get("mtime")):
            out.append(f"model 文件已改动(size/mtime 不同): {p}")
    return out
