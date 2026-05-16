"""Per-corner Spectre IC file path resolver for Phase 3A v1.2 `ic_from`.

Implements docs/phase3a_orchestrator_spec.md §2.5 + DECISIONS #57. Pure
filesystem walker — no SKILL or skillbridge dependency. The orchestrator
calls ``resolve_ic_path`` once per (consumer corner, source history) pair
to find the absolute path of the corresponding ``spectre.<kind>`` file
that gets fed into the consumer's PSS / HB analysis.

Layout (empirically derived from ``simkit_verify``, 2026-05-16):

    <results_root>/<history_name>/<corner_idx_1based>/<test_name>/<sim_subdir>/spectre.<kind>

``results_root`` is the value returned by ``axlGetResultsLocation(sdb)``
(typically ``.../results/maestro``). ``sim_subdir`` is ``netlist`` for
Spectre, ``psf`` for Alps; resolver auto-detects by file presence unless
the user pinned an explicit override via the ``ic_from.subdir`` sidecar
field.

Returns ``None`` when the file genuinely doesn't exist (upstream corner
failed, file not written by Spectre, …). The orchestrator turns that
into a "naked retry with warning" per the user's design pick.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from simkit.errors import SimkitError


class IcSourceError(SimkitError):
    """Misuse — invalid file_kind, missing history root, etc.

    Distinct from "file not found in any subdir", which is a runtime event
    the orchestrator handles by naked-retry-with-warning (resolver returns
    ``None`` for that case).
    """


# Sim-subdir registry. Order matters: resolver tries left-to-right and
# picks the first directory that holds the requested file. Spectre keeps
# all per-corner sim artefacts under ``netlist/`` (probe 2026-05-16 on
# simkit_verify confirmed ``spectre.{ic,fc,dc}`` all live there). Alps
# (国产 simulator, work env) uses ``psf/`` per user report; pending live
# confirmation at first work-env dogfood.
_DEFAULT_SIM_SUBDIR_CANDIDATES: tuple[str, ...] = ("netlist", "psf")

_VALID_FILE_KINDS = frozenset({"fc", "ic", "dc"})


@dataclass(frozen=True)
class ResolvedIcPath:
    """The path resolver's positive answer + which subdir it picked.

    The ``subdir`` field surfaces back to the caller so the orchestrator
    can log "found at netlist/" vs "found at psf/" without the caller
    having to re-derive it. Useful when the first work-env dogfood
    surprises us and we need to add a new simulator to the registry.
    """

    abs_path: Path
    subdir: str


def resolve_ic_path(
    results_root: Path | str,
    history_name: str,
    corner_idx: int,
    test_name: str,
    file_kind: str,
    *,
    subdir_candidates: tuple[str, ...] = _DEFAULT_SIM_SUBDIR_CANDIDATES,
    explicit_subdir: str | None = None,
) -> ResolvedIcPath | None:
    """Return the resolved Spectre IC path for one corner, or ``None``.

    Args:
        results_root: Result of ``axlGetResultsLocation(sdb)`` (e.g.
            ``/home/yusheng/.../results/maestro``). The history-name dir
            sits directly under this.
        history_name: The source item's history (e.g. ``review_signoff_trans_1234``).
        corner_idx: 1-based corner index. Maps to a sibling dir named
            ``"1"``, ``"2"``, … in the history dir, in ``axlGetCorners(sdb)``
            order at source-item submit time.
        test_name: The test whose IC we want (e.g. ``"Test"``).
        file_kind: One of ``"fc"`` / ``"ic"`` / ``"dc"``.
        subdir_candidates: Override the registered subdir search list.
            Tests use this to inject made-up simulator names.
        explicit_subdir: If set, ONLY this subdir is tried (the user
            pinned it via ``ic_from.subdir``). Raises ``IcSourceError``
            on file_kind mismatch but returns ``None`` on file-absent.

    Returns:
        ``ResolvedIcPath`` on success, ``None`` when no subdir has the
        requested file (upstream corner failed / Spectre didn't dump that
        kind / Alps stores it somewhere we don't know yet).

    Raises:
        ``IcSourceError`` for caller misuse (bad ``file_kind``, missing
        history dir, ``corner_idx < 1``).
    """
    if file_kind not in _VALID_FILE_KINDS:
        raise IcSourceError(
            f"file_kind {file_kind!r} not in {sorted(_VALID_FILE_KINDS)}"
        )
    if corner_idx < 1:
        raise IcSourceError(
            f"corner_idx must be >= 1, got {corner_idx}"
        )
    if not test_name:
        raise IcSourceError("test_name must be a non-empty string")

    root = Path(results_root).expanduser()
    history_dir = root / history_name
    if not history_dir.is_dir():
        raise IcSourceError(
            f"history dir not found: {history_dir} "
            f"(check axlGetResultsLocation output + history_name spelling)"
        )

    corner_dir = history_dir / str(corner_idx) / test_name
    if not corner_dir.is_dir():
        # Distinct from "file missing": the whole corner dir is absent.
        # Caller treats both cases the same way (naked retry), so we don't
        # raise; we just return None.
        return None

    candidates = (explicit_subdir,) if explicit_subdir else subdir_candidates
    filename = f"spectre.{file_kind}"
    for subdir in candidates:
        f = corner_dir / subdir / filename
        if f.is_file():
            return ResolvedIcPath(abs_path=f.resolve(), subdir=subdir)
    return None


def enumerate_corner_dirs(
    results_root: Path | str, history_name: str,
) -> list[int]:
    """Return the sorted list of 1-based corner indexes present under a history.

    Used by the orchestrator to sanity-check that the source item produced
    the same number of corner subdirs as it had corners — if not, the
    union explode count vs. on-disk count mismatch surfaces as a warning
    instead of a silent IC miss for the missing index.
    """
    root = Path(results_root).expanduser() / history_name
    if not root.is_dir():
        raise IcSourceError(f"history dir not found: {root}")
    idxs: list[int] = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        if entry.name.isdigit():
            idxs.append(int(entry.name))
    return sorted(idxs)
