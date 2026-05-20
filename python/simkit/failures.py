"""FAIL-corner detection over an ingested run (Phase 3A v1.6 #1).

Single entry point :func:`find_failed_corners` reads the ``results`` table
for one ``run_id`` and aggregates per-corner failures so the orchestrator
can decide which corners to feed into a strategy chain.

FAIL sources (DECISIONS #62):

* ``spec_status='fail'`` — v1.4 captured Cadence specs; a measured value
  violates one (e.g. ``Rtime > 1e-10`` measured 5e-12). Real EDA failure.
* sentinel row (``output='__sim_status__'``) with status in
  ``{failed, no_convergence}`` — Spectre never produced numbers for the
  triple (crash, license, no convergence).

Deliberately EXCLUDED from auto-retry (still surfaced via reason code in
``find_failed_corners`` when callers ask):

* ``status='eval_err'`` — calc-expression bug; retrying won't fix it.
* sentinel ``status='running'`` / ``'pending'`` — sim still in flight.
  v1.5 F2's poll-to-idle should prevent this, but if a row slips through,
  the right action is "wait", not "retry".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import duckdb


@dataclass(frozen=True)
class FailedCorner:
    """One corner that has at least one FAIL row.

    ``reasons`` is a frozenset of reason codes — a single corner can fail
    for multiple reasons (spec + sim_status). ``sample_test`` /
    ``sample_output`` hold the first such row's (test, output) so the
    caller has a concrete failing measurement to point at.
    """

    corner: str
    reasons: frozenset[str]
    sample_test: str
    sample_output: str


REASON_SPEC = "spec_fail"
REASON_SIM = "sim_status_fail"
REASON_EVAL = "eval_err"  # surfaced but excluded by default

_AUTO_RETRY_REASONS = frozenset({REASON_SPEC, REASON_SIM})


def find_failed_corners(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    *,
    include_eval_err: bool = False,
) -> tuple[FailedCorner, ...]:
    """Aggregate FAIL rows for ``run_id`` into one entry per corner.

    Order: corners in the order they first appear in the table (stable for
    a given DB; typically matches Maestro's explode order).
    """
    rows = con.execute(
        """
        SELECT corner, test, output, status, spec_status
        FROM results
        WHERE run_id = ?
          AND (
            spec_status = 'fail'
            OR (output = '__sim_status__'
                AND status IN ('failed', 'no_convergence'))
            OR (status = 'eval_err' AND output <> '__sim_status__')
          )
        ORDER BY corner, test, output
        """,
        [run_id],
    ).fetchall()

    acc: dict[str, list] = {}
    for corner, test, output, status, spec_status in rows:
        reason = _classify(status, spec_status, output)
        if reason is None:
            continue
        if not include_eval_err and reason == REASON_EVAL:
            continue
        if corner not in acc:
            acc[corner] = [{reason}, test, output]
        else:
            acc[corner][0].add(reason)

    return tuple(
        FailedCorner(
            corner=name,
            reasons=frozenset(reasons),
            sample_test=sample_t,
            sample_output=sample_o,
        )
        for name, (reasons, sample_t, sample_o) in acc.items()
    )


def auto_retry_corners(failed: Iterable[FailedCorner]) -> tuple[str, ...]:
    """Names of corners that have at least one auto-retryable reason.

    Eval-only failures are skipped — the orchestrator surfaces them in the
    final report but doesn't pull them into a retry round.
    """
    return tuple(
        f.corner for f in failed
        if f.reasons & _AUTO_RETRY_REASONS
    )


def _classify(status, spec_status, output) -> str | None:
    # eval_err must be checked BEFORE spec_status so that a row whose calc
    # expression errored (and whose spec_status therefore cannot be trusted)
    # is never mis-classified as a genuine spec failure.
    if status == "eval_err" and output != "__sim_status__":
        return REASON_EVAL
    if spec_status == "fail":
        return REASON_SPEC
    if output == "__sim_status__" and status in ("failed", "no_convergence"):
        return REASON_SIM
    return None
