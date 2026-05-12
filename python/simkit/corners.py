"""PVT-corners offline helpers: enumerate `.union.json` files and diff two unions.

Pure logic behind the ``pvt corners list`` and ``pvt corners diff`` CLI verbs.
No argparse here; the ``simkit.cli.corners`` module handles formatting.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from simkit.union import (
    UNION_FILE_SUFFIX,
    Union,
    UnionError,
    explode,
    load_union,
)


@dataclass(frozen=True)
class UnionListing:
    name: str
    path: Path
    project: Optional[str]
    testbench_id: Optional[str]
    row_count: Optional[int]
    sub_corner_count: Optional[int]
    error: Optional[str]


@dataclass(frozen=True)
class UnionDiffChange:
    row_name: str
    field: str
    a: Any
    b: Any


@dataclass(frozen=True)
class UnionDiff:
    added: tuple[str, ...]
    removed: tuple[str, ...]
    changed: tuple[UnionDiffChange, ...]
    identical_count: int

    def has_differences(self) -> bool:
        return bool(self.added or self.removed or self.changed)


def list_unions(unions_dir: Path) -> list[UnionListing]:
    unions_dir = Path(unions_dir).expanduser().resolve()
    if not unions_dir.is_dir():
        return []
    out: list[UnionListing] = []
    for path in sorted(unions_dir.glob(f"*{UNION_FILE_SUFFIX}")):
        out.append(_summarise_union(path))
    return out


def _summarise_union(path: Path) -> UnionListing:
    name = path.name[: -len(UNION_FILE_SUFFIX)]
    try:
        union = load_union(path)
    except UnionError as exc:
        return UnionListing(
            name=name,
            path=path,
            project=None,
            testbench_id=None,
            row_count=None,
            sub_corner_count=None,
            error=str(exc),
        )
    sub_count = len(explode(union))
    return UnionListing(
        name=union.name,
        path=path,
        project=union.project,
        testbench_id=union.testbench_id,
        row_count=len(union.rows),
        sub_corner_count=sub_count,
        error=None,
    )


def resolve_unions_dir(pvtproject_path: Path) -> Path:
    """Resolve `<unionsDir>` from a `.pvtproject`. Default `./unions` relative to it."""
    pvtproject_path = Path(pvtproject_path).expanduser().resolve()
    raw_dir: Optional[str] = None
    try:
        with pvtproject_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        data = None
    if isinstance(data, dict):
        candidate = data.get("unionsDir")
        if isinstance(candidate, str) and candidate != "":
            raw_dir = candidate
    if raw_dir is None:
        raw_dir = "unions"
    resolved = Path(raw_dir).expanduser()
    if not resolved.is_absolute():
        resolved = (pvtproject_path.parent / resolved).resolve()
    else:
        resolved = resolved.resolve()
    return resolved


def diff_unions(a: Union, b: Union) -> UnionDiff:
    a_rows = {r.row_name: r for r in a.rows}
    b_rows = {r.row_name: r for r in b.rows}

    added = tuple(sorted(n for n in b_rows if n not in a_rows))
    removed = tuple(sorted(n for n in a_rows if n not in b_rows))

    changes: list[UnionDiffChange] = []
    identical = 0
    for name in sorted(n for n in a_rows if n in b_rows):
        row_changes = _row_changes(name, a_rows[name], b_rows[name])
        if row_changes:
            changes.extend(row_changes)
        else:
            identical += 1

    return UnionDiff(
        added=added,
        removed=removed,
        changed=tuple(changes),
        identical_count=identical,
    )


def _row_changes(row_name, a, b) -> list[UnionDiffChange]:
    out: list[UnionDiffChange] = []

    a_var_keys = set(a.vars)
    b_var_keys = set(b.vars)
    for k in sorted(a_var_keys | b_var_keys):
        if k not in a_var_keys:
            out.append(UnionDiffChange(
                row_name=row_name, field=f"vars.{k}",
                a=None, b=list(b.vars[k]),
            ))
        elif k not in b_var_keys:
            out.append(UnionDiffChange(
                row_name=row_name, field=f"vars.{k}",
                a=list(a.vars[k]), b=None,
            ))
        elif a.vars[k] != b.vars[k]:
            out.append(UnionDiffChange(
                row_name=row_name, field=f"vars.{k}",
                a=list(a.vars[k]), b=list(b.vars[k]),
            ))

    a_models = a.models
    b_models = b.models
    max_n = max(len(a_models), len(b_models))
    for i in range(max_n):
        if i >= len(a_models):
            out.append(UnionDiffChange(
                row_name=row_name, field=f"models[{i}]",
                a=None, b=_model_to_dict(b_models[i]),
            ))
        elif i >= len(b_models):
            out.append(UnionDiffChange(
                row_name=row_name, field=f"models[{i}]",
                a=_model_to_dict(a_models[i]), b=None,
            ))
        else:
            am, bm = a_models[i], b_models[i]
            for fld in ("file", "block", "test"):
                av, bv = getattr(am, fld), getattr(bm, fld)
                if av != bv:
                    out.append(UnionDiffChange(
                        row_name=row_name, field=f"models[{i}].{fld}",
                        a=av, b=bv,
                    ))
            if am.section != bm.section:
                out.append(UnionDiffChange(
                    row_name=row_name, field=f"models[{i}].section",
                    a=list(am.section), b=list(bm.section),
                ))
    return out


def _model_to_dict(m) -> dict:
    return {
        "file": m.file,
        "block": m.block,
        "test": m.test,
        "section": list(m.section),
    }
