"""Slice-to-slice diff: results-table + netlist.

Implements the data layer behind ``pvt diff``. Three pieces:

* :func:`resolve_slice` — turn a label or run_id-prefix string into a
  unique ``run_id``.
* :func:`compute_results_diff` — align the two slices' result rows on
  ``(test, corner, point, output)`` and emit :class:`DiffRow` records.
* :func:`compute_netlist_diff` — unified diff of the two
  ``input.scs`` files; soft-misses when either side is null.

The CLI layer applies threshold filtering and renders to text or JSON.
"""

from __future__ import annotations

import difflib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import duckdb

from simkit.errors import AmbiguousSliceError, SliceNotFoundError


_Key = Tuple[str, str, int, str]  # (test, corner, point, output)


@dataclass(frozen=True)
class DiffRow:
    test: str
    corner: str
    point: int
    output: str
    value_a: Any
    value_b: Any
    status_a: Optional[str]
    status_b: Optional[str]
    abs_delta: Optional[float]
    rel_delta: Optional[float]
    kind: str  # "match" | "only_a" | "only_b" | "status_mismatch"
    is_sentinel: bool
    # v1.4 — spec verdict + string per slice. Both default to None for v1
    # data (no spec captured) so callers / serialisers see "missing", not
    # a false "no_spec" claim.
    spec_a: Optional[str] = None
    spec_b: Optional[str] = None
    spec_status_a: Optional[str] = None
    spec_status_b: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def spec_changed(self) -> bool:
        """True iff both slices captured spec_status and the verdicts differ.

        Filtering on this in the CLI surfaces only the rows whose pass/fail
        outcome flipped between the two slices — the main use case for
        regression triage after a spec change or a netlist change.
        """
        return (
            self.spec_status_a is not None
            and self.spec_status_b is not None
            and self.spec_status_a != self.spec_status_b
        )


@dataclass(frozen=True)
class NetlistDiff:
    a_path: Optional[str]
    b_path: Optional[str]
    diff_text: Optional[str]
    note: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DiffResult:
    slice_a_run_id: str
    slice_b_run_id: str
    slice_a_identifier: str
    slice_b_identifier: str
    rows: List[DiffRow]
    netlist: NetlistDiff

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slice_a": {
                "identifier": self.slice_a_identifier,
                "run_id": self.slice_a_run_id,
            },
            "slice_b": {
                "identifier": self.slice_b_identifier,
                "run_id": self.slice_b_run_id,
            },
            "results": [r.to_dict() for r in self.rows],
            "netlist": self.netlist.to_dict(),
        }


# ---------------------------------------------------------------------------
# Slice resolution
# ---------------------------------------------------------------------------

def resolve_slice(
    con: duckdb.DuckDBPyConnection, identifier: str,
) -> str:
    """Resolve ``identifier`` to a unique ``run_id``.

    Resolution order:

    1. Exact match on ``runs.label`` (unique among labeled runs).
    2. ``run_id`` prefix match (unique among all runs).

    Raises :class:`SliceNotFoundError` on no match,
    :class:`AmbiguousSliceError` on more than one match.
    """
    if not identifier:
        raise SliceNotFoundError("slice identifier must be a non-empty string")

    rows = con.execute(
        "SELECT run_id FROM runs WHERE label = ?", [identifier]
    ).fetchall()
    if len(rows) == 1:
        return rows[0][0]
    if len(rows) > 1:
        raise AmbiguousSliceError(
            f"label {identifier!r} matches {len(rows)} runs: "
            f"{sorted(r[0] for r in rows)!r}"
        )

    rows = con.execute(
        "SELECT run_id FROM runs WHERE run_id LIKE ?",
        [identifier + "%"],
    ).fetchall()
    if len(rows) == 1:
        return rows[0][0]
    if len(rows) > 1:
        raise AmbiguousSliceError(
            f"run_id prefix {identifier!r} matches {len(rows)} runs: "
            f"{sorted(r[0] for r in rows)!r}"
        )

    raise SliceNotFoundError(
        f"no run matches identifier {identifier!r} "
        "(tried label exact match, then run_id prefix)"
    )


# ---------------------------------------------------------------------------
# Results diff
# ---------------------------------------------------------------------------

def compute_results_diff(
    con: duckdb.DuckDBPyConnection,
    *,
    slice_a_run_id: str,
    slice_b_run_id: str,
) -> List[DiffRow]:
    """Align the two slices' result rows and emit :class:`DiffRow`s.

    Ordering: by (test, corner, point, output). Sentinel rows
    (``output == '__sim_status__'``) are returned with ``is_sentinel=True``;
    the CLI layer hides them unless ``--include-status`` is passed.
    """
    a = _load_results_keyed(con, slice_a_run_id)
    b = _load_results_keyed(con, slice_b_run_id)

    keys = sorted(set(a.keys()) | set(b.keys()))
    out: List[DiffRow] = []
    _empty = (None, None, None, None)
    for k in keys:
        test, corner, point, output = k
        va, sa, spec_a, spec_status_a = a.get(k, _empty)
        vb, sb, spec_b, spec_status_b = b.get(k, _empty)
        in_a = k in a
        in_b = k in b

        if in_a and not in_b:
            kind = "only_a"
        elif in_b and not in_a:
            kind = "only_b"
        elif sa != sb:
            kind = "status_mismatch"
        else:
            kind = "match"

        abs_delta, rel_delta = _compute_deltas(va, vb, in_a and in_b)
        out.append(DiffRow(
            test=test, corner=corner, point=point, output=output,
            value_a=va, value_b=vb,
            status_a=sa, status_b=sb,
            abs_delta=abs_delta, rel_delta=rel_delta,
            kind=kind,
            is_sentinel=(output == "__sim_status__"),
            spec_a=spec_a, spec_b=spec_b,
            spec_status_a=spec_status_a, spec_status_b=spec_status_b,
        ))
    return out


def _load_results_keyed(
    con: duckdb.DuckDBPyConnection, run_id: str,
) -> Dict[_Key, Tuple[Any, str, Optional[str], Optional[str]]]:
    """Return ``{key: (value, status, spec, spec_status)}`` for one slice.

    ``spec`` / ``spec_status`` are v1.4 additions and may be NULL on rows
    ingested from a v1 envelope or from a v2 envelope whose collector
    captured no spec for that output. Callers must treat NULL as "missing"
    distinct from the ``'no_spec'`` enum value.
    """
    rows = con.execute(
        """
        SELECT test, corner, point, output,
               value_num, value_str, status,
               spec, spec_status
        FROM results
        WHERE run_id = ?
        """,
        [run_id],
    ).fetchall()
    keyed: Dict[_Key, Tuple[Any, str, Optional[str], Optional[str]]] = {}
    for r in rows:
        key = (r[0], r[1], int(r[2]), r[3])
        value: Any
        if r[4] is not None:
            value = float(r[4])
        elif r[5] is not None:
            value = r[5]
        else:
            value = None
        keyed[key] = (value, r[6], r[7], r[8])
    return keyed


def _compute_deltas(
    va: Any, vb: Any, both_present: bool,
) -> Tuple[Optional[float], Optional[float]]:
    if not both_present:
        return (None, None)
    if not (isinstance(va, (int, float)) and not isinstance(va, bool)):
        return (None, None)
    if not (isinstance(vb, (int, float)) and not isinstance(vb, bool)):
        return (None, None)
    abs_delta = float(vb) - float(va)
    if va == 0:
        # Undefined relative delta; report None so the renderer can mark it.
        return (abs_delta, None)
    rel_delta = abs_delta / float(va)
    return (abs_delta, rel_delta)


# ---------------------------------------------------------------------------
# Netlist diff
# ---------------------------------------------------------------------------

def compute_netlist_diff(
    con: duckdb.DuckDBPyConnection,
    *,
    slice_a_run_id: str,
    slice_b_run_id: str,
    runs_root: Path,
) -> NetlistDiff:
    """Unified diff of the two runs' netlist files.

    Soft-miss conditions (each is captured in ``note``, not raised):

    * either ``runs.netlist_path`` is NULL (collector soft-miss);
    * either file is missing on disk.
    """
    a_rel = _query_netlist_path(con, slice_a_run_id)
    b_rel = _query_netlist_path(con, slice_b_run_id)

    if a_rel is None and b_rel is None:
        return NetlistDiff(
            a_path=None, b_path=None, diff_text=None,
            note="both slices have null netlist_path (collector soft-miss)",
        )
    if a_rel is None:
        return NetlistDiff(
            a_path=None, b_path=b_rel, diff_text=None,
            note=f"slice_a has null netlist_path; slice_b has {b_rel!r}",
        )
    if b_rel is None:
        return NetlistDiff(
            a_path=a_rel, b_path=None, diff_text=None,
            note=f"slice_b has null netlist_path; slice_a has {a_rel!r}",
        )

    a_abs = (Path(runs_root) / slice_a_run_id / a_rel).resolve()
    b_abs = (Path(runs_root) / slice_b_run_id / b_rel).resolve()

    if not a_abs.is_file() or not b_abs.is_file():
        missing = []
        if not a_abs.is_file():
            missing.append(f"slice_a: {a_abs}")
        if not b_abs.is_file():
            missing.append(f"slice_b: {b_abs}")
        return NetlistDiff(
            a_path=a_rel, b_path=b_rel, diff_text=None,
            note="netlist file missing on disk for " + "; ".join(missing),
        )

    a_lines = a_abs.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    b_lines = b_abs.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    diff = "".join(difflib.unified_diff(
        a_lines, b_lines,
        fromfile=f"slice_a/{a_rel}",
        tofile=f"slice_b/{b_rel}",
    ))
    return NetlistDiff(
        a_path=a_rel, b_path=b_rel,
        diff_text=diff,  # empty string = identical
        note=None,
    )


def _query_netlist_path(
    con: duckdb.DuckDBPyConnection, run_id: str,
) -> Optional[str]:
    row = con.execute(
        "SELECT netlist_path FROM runs WHERE run_id = ?",
        [run_id],
    ).fetchone()
    if row is None:
        # Caller already resolved the slice; this shouldn't fire.
        return None
    return row[0]


# ---------------------------------------------------------------------------
# Public top-level
# ---------------------------------------------------------------------------

def compute_diff(
    con: duckdb.DuckDBPyConnection,
    *,
    slice_a: str,
    slice_b: str,
    runs_root: Path,
) -> DiffResult:
    """End-to-end diff: resolve both slices, compute results + netlist."""
    a_id = resolve_slice(con, slice_a)
    b_id = resolve_slice(con, slice_b)
    rows = compute_results_diff(
        con, slice_a_run_id=a_id, slice_b_run_id=b_id,
    )
    netlist = compute_netlist_diff(
        con, slice_a_run_id=a_id, slice_b_run_id=b_id,
        runs_root=runs_root,
    )
    return DiffResult(
        slice_a_run_id=a_id,
        slice_b_run_id=b_id,
        slice_a_identifier=slice_a,
        slice_b_identifier=slice_b,
        rows=rows,
        netlist=netlist,
    )
