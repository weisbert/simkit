"""Run-level rollups for the GUI Summary tab (G-3 margin + G-4 convergence).

Two pure-DuckDB reads over one ``run_id``:

* :func:`run_health` — counts result rows by status, sim-status failures,
  and the ``runs.partial_run`` flag. The "did this run finish, and how
  cleanly" line that turns a raw ``eval_err`` cell into a run verdict.
* :func:`margin_rollup` — one entry per output: spec, worst corner, worst
  value, margin, overall verdict. The review-evidence table designers
  otherwise rebuild by hand in Excel.

No Qt — the Summary tab widget wraps these. Mirrors the ``loaders.py`` /
``load_rows_for_run`` split: run-scoped data access stays testable
without a display server.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import duckdb

from simkit.provenance import load_provenance
from simkit.spec_eval import spec_margin


# Sentinel output name for per-corner Spectre status rows (crash, license,
# no-convergence). Excluded from the margin rollup; counted by run_health.
_SIM_SENTINEL = "__sim_status__"

# Sim-status values that mean "Spectre never produced numbers".
_SIM_FAIL_STATUSES = ("failed", "no_convergence")

# Verdict precedence — the worst present spec_status wins for an output.
_VERDICT_PRECEDENCE = (
    "fail", "parse_err", "no_value", "unsupported", "no_spec", "pass",
)


@dataclass(frozen=True)
class RunHealth:
    """Convergence / completeness snapshot for one run.

    ``status_counts`` is keyed by the raw ``results.status`` value over
    real output rows (the ``__sim_status__`` sentinel is excluded and
    surfaced separately via ``sim_fail_corners``).
    """

    total_rows: int
    status_counts: dict[str, int]
    sim_fail_corners: int
    partial_run: bool

    @property
    def clean(self) -> bool:
        """True when nothing needs the user's attention."""
        bad = sum(
            c for s, c in self.status_counts.items() if s != "ok"
        )
        return bad == 0 and self.sim_fail_corners == 0 and not self.partial_run


@dataclass(frozen=True)
class OutputRollup:
    """Per-output worst-case rollup across all corners of a run."""

    output: str
    spec: Optional[str]
    worst_corner: Optional[str]
    worst_value: Optional[float]
    margin: Optional[float]
    verdict: str
    n_corners: int


def _merge_value(value_num: Any, value_str: Any) -> Any:
    """Numeric wins over string; both null → None. Mirrors from_db."""
    if value_num is not None:
        return float(value_num)
    if value_str is not None:
        return value_str
    return None


def run_health(con: duckdb.DuckDBPyConnection, run_id: str) -> RunHealth:
    """Aggregate one run's row statuses + sim failures + partial flag.

    The caller owns the connection lifetime.
    """
    real = con.execute(
        """
        SELECT status, count(*)
        FROM results
        WHERE run_id = ? AND output <> ?
        GROUP BY status
        """,
        [run_id, _SIM_SENTINEL],
    ).fetchall()
    status_counts = {str(s): int(c) for s, c in real}
    total = sum(status_counts.values())

    sim_fail = con.execute(
        """
        SELECT count(DISTINCT corner)
        FROM results
        WHERE run_id = ? AND output = ? AND status IN ('failed', 'no_convergence')
        """,
        [run_id, _SIM_SENTINEL],
    ).fetchone()

    partial = con.execute(
        "SELECT partial_run FROM runs WHERE run_id = ?", [run_id],
    ).fetchone()

    return RunHealth(
        total_rows=total,
        status_counts=status_counts,
        sim_fail_corners=int(sim_fail[0]) if sim_fail else 0,
        partial_run=bool(partial[0]) if partial else False,
    )


def read_run_provenance(
    con: duckdb.DuckDBPyConnection, run_id: str
) -> Optional[Dict[str, Any]]:
    """Return the parsed ``runs.provenance`` object for ``run_id`` (G-5).

    ``None`` when the run has no provenance — it predates the feature,
    or came from a manual PvtSave that bypassed the orchestrator.
    """
    row = con.execute(
        "SELECT provenance FROM runs WHERE run_id = ?", [run_id],
    ).fetchone()
    if row is None:
        return None
    return load_provenance(row[0])


def _verdict(spec_statuses: list[str]) -> str:
    """Reduce an output's per-corner spec_status values to one verdict."""
    present = set(spec_statuses)
    for v in _VERDICT_PRECEDENCE:
        if v in present:
            return v
    return "no_spec"


def margin_rollup(
    con: duckdb.DuckDBPyConnection, run_id: str
) -> tuple[OutputRollup, ...]:
    """One :class:`OutputRollup` per output in ``run_id``.

    The "worst corner" is the one with the smallest signed margin (most
    violating, or — for a fully-passing output — the tightest). Outputs
    with no spec are still listed (verdict ``no_spec``) so the user can
    see what is unguarded. The caller owns the connection lifetime.
    """
    rows = con.execute(
        """
        SELECT corner, output, value_num, value_str, spec, spec_status
        FROM results
        WHERE run_id = ? AND output <> ?
        ORDER BY output, corner
        """,
        [run_id, _SIM_SENTINEL],
    ).fetchall()

    by_output: dict[str, list] = {}
    for corner, output, value_num, value_str, spec, spec_status in rows:
        by_output.setdefault(output, []).append(
            (corner, _merge_value(value_num, value_str), spec, spec_status)
        )

    out: list[OutputRollup] = []
    for output in sorted(by_output):
        entries = by_output[output]
        spec = next((s for _, _, s, _ in entries if s), None)
        verdict = _verdict([str(ss) for _, _, _, ss in entries if ss])

        scored = []
        for corner, value, row_spec, _ in entries:
            margin = spec_margin(row_spec, value)
            if margin is not None:
                scored.append((margin, corner, value))

        if scored:
            margin, worst_corner, worst_value = min(scored, key=lambda t: t[0])
        else:
            # No computable margin anywhere — point at the first corner so
            # the user still has a concrete row to inspect.
            margin = None
            worst_corner = sorted(c for c, _, _, _ in entries)[0] if entries else None
            worst_value = None

        out.append(
            OutputRollup(
                output=output,
                spec=spec,
                worst_corner=worst_corner,
                worst_value=worst_value,
                margin=margin,
                verdict=verdict,
                n_corners=len(entries),
            )
        )
    return tuple(out)
