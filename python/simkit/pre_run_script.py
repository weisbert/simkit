"""Generate self-contained SKILL pre-run scripts for v1.3 ic_from.

DECISIONS #57 stage-3. Replaces stage-2's per-corner-submit pattern with
**single-batch + Maestro pre-run hook**: orchestrator generates one
SKILL script per consumer item, attaches it to each test in that item,
fires ONE axlRunAllTests, and the script — running in Maestro's worker
virtuoso VM right before each (test, corner) point is netlisted —
writes the corner-specific +nodeset / +ic flag into the test's
``additionalArgs`` sim option. Result: ONE Maestro history with N
sub-corners, per-corner IC delivered, GUI consolidation intact.

The script is fully self-contained — no JSON parsing, no file IO at run
time, no dependency on simkit code being loaded in the worker VM.
The corner→arg mapping is embedded directly as a SKILL ``assoc`` list,
so the only built-ins the worker needs are those Maestro already
guarantees: ``axlGetCornerNameForCurrentPointInRun``, ``assoc``, ``car``,
``cdr``, ``equal``, ``cond``, ``errset``, ``asiGetCurrentSession``,
``asiSetSimOptionVal``.

Live-probed 2026-05-16 on fnxSession0:
  * Pre-run fires once per sub-corner with the FULL sub-corner name
    (TT_pvt explodes into TT_pvt_0..5 — each gets its own firing).
  * One additional "pre-flight" call with ``corner=""`` happens first;
    asiGetCurrentSession returns nil at that point. Script MUST guard.
  * Worker VM has asiSetSimOptionVal; round-trip set/get works.
  * Script returning nil aborts that corner — always return t.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class PreRunSpec:
    """One consumer item's per-(sub-corner) IC argument table.

    ``corner_to_arg`` maps the exact sub-corner name (as returned by
    ``axlGetCornerNameForCurrentPointInRun`` at runtime) to the Spectre
    CLI fragment that should land in ``additionalArgs`` for that
    corner — e.g. ``"+nodeset /abs/path/spectre.fc"`` (readns) or
    ``"+ic /abs/path/spectre.ic"`` (readic). Corners not in the map
    (e.g. their upstream IC was missing) are left untouched by the
    pre-run script — that corner runs naked.
    """

    item_name: str
    mode: str  # "readns" | "readic" | "gmin_bump"
    corner_to_arg: Mapping[str, str]
    # SKILL sim-option name the hook writes into. Defaults to
    # ``additionalArgs`` for the ic_from / readns / readic modes that
    # piggy-back on the netlist's existing simulatorOptions block. For
    # gmin_bump and future per-corner option overrides this becomes the
    # actual option key (e.g. ``"gmin"``).
    option_key: str = "additionalArgs"
    # Value to restore on EVERY firing before applying the per-corner
    # override (if any). Needed for partial-map scenarios where the
    # worker-VM asi session is reused across sub-corners and would
    # otherwise carry the previous sub-corner's override into the next.
    # Set to ``None`` (default) for ic_from / readns / readic, whose map
    # covers every sub-corner so the leak path is unreachable. Set to a
    # string (e.g. ``"1e-12"``) for gmin_bump / partial overrides.
    # Live-discovered 2026-05-18 on fnxSession0 (Phase 1 A5 verify).
    baseline_value: str | None = None
    # Phase 3A v1.9 #3 gap #2: optional per-test override of corner_to_arg.
    # When set, ``write_per_test_pre_run_scripts`` looks each test up in
    # this dict and renders a per-test script with that test's specific
    # map; tests absent from the dict fall back to the top-level
    # ``corner_to_arg``. The top-level map remains required as the
    # default. ``None`` (default) preserves v1.3/v1.7 single-map shape.
    per_test_corner_to_arg: Mapping[str, Mapping[str, str]] | None = None


def _skill_quote(s: str) -> str:
    """Quote a Python string for embedding into SKILL source.

    SKILL string literals use double-quotes and require escaping of
    ``\\`` and ``"``. Pre-run script paths and IC paths are absolute
    POSIX paths so ``\\`` is unusual but cheap to escape defensively.
    """
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_pre_run_script(spec: PreRunSpec) -> str:
    """Produce the SKILL source for a pre-run script encoding ``spec``.

    The generated script:
      1. Reads current sub-corner name via axlGetCornerNameForCurrentPointInRun.
      2. If empty (pre-flight call) or not in the map, returns t without
         touching the sim session.
      3. Otherwise looks up corner→arg via SKILL ``assoc`` and writes the
         arg into ``additionalArgs`` via asiSetSimOptionVal.
      4. Always returns t — never nil — so Maestro doesn't abort the corner.
    """
    # SKILL note: use (list k v) for assoc entries, NOT (cons k v).
    # Cadence SKILL's cons rejects non-list 2nd arg ("argument #2 should
    # be a list"), so dotted-pair (cons "k" "v") doesn't compile. The
    # 2-element-list form (list "k" "v") gives assoc-compatible entries
    # retrieved via cadr (the cdr is the rest-of-list, not the value
    # directly). Probed on fnxSession0 2026-05-16.
    table_lines = []
    for corner, arg in spec.corner_to_arg.items():
        table_lines.append(
            f"        (list {_skill_quote(corner)} {_skill_quote(arg)})"
        )
    table_block = "\n".join(table_lines) if table_lines else "        ;; (empty map — item has no IC for any corner)"

    body = _render_body(spec)

    return f""";; AUTOGENERATED by simkit.pre_run_script — do NOT hand-edit.
;; Item: {spec.item_name}
;; Mode: {spec.mode}  ({len(spec.corner_to_arg)} corners mapped)  → asi {spec.option_key}
;;
;; Runs in Maestro's worker virtuoso VM BEFORE each (test, corner)
;; point is netlisted. Looks up the current sub-corner in the embedded
;; map and writes the matching value into the configured sim option.
;;
;; SKILL note: uses (let ...) + (setq ...) instead of (let* ...) —
;; the worker VM's SKILL parser is stricter than the main VM's and
;; rejects let* with errset-wrapped initial values ("unbound variable"
;; on the bound name itself). Probed on fnxSession0 2026-05-16.

(let ((cornerName nil)
      (cornerMap (list
{table_block}
      ))
      (entry nil)
      (asi nil))
  (setq cornerName (car (errset (axlGetCornerNameForCurrentPointInRun) nil)))
  ;; Pre-flight call has cornerName="" and asi=nil — guard.
  (when (and cornerName (stringp cornerName) (not (equal cornerName "")))
{body}
  ;; ALWAYS return t — nil would abort this corner.
  t)
"""


def _render_body(spec: PreRunSpec) -> str:
    """Render the per-firing body that decides what to write into asi.

    Two shapes:
      * ``baseline_value`` is None (ic_from / readns / readic): only writes
        when this corner is in the map. asi is resolved lazily inside the
        ``(when entry ...)`` branch — matches the v1.3 shape verbatim.
      * ``baseline_value`` is set (gmin_bump and similar): resolves asi
        unconditionally on every firing, restores baseline FIRST so the
        previous sub-corner's bump doesn't leak across the shared
        worker-VM asi session, then applies the per-corner override
        (if any). Live-discovered 2026-05-18 — same-row sub-corners share
        one asi instance so unmapped corners would otherwise inherit the
        last bump.
    """
    qk = _skill_quote(spec.option_key)
    if spec.baseline_value is None:
        return f"""    (setq entry (assoc cornerName cornerMap))
    (when entry
      (setq asi (car (errset (asiGetCurrentSession) nil)))
      (when asi
        ;; assoc returns (key val) — cadr extracts val.
        ;; (Cadence SKILL's assoc with list-form entries; see note above.)
        (errset
          (asiSetSimOptionVal asi {qk} (cadr entry))
          nil))))"""
    qb = _skill_quote(spec.baseline_value)
    return f"""    (setq entry (assoc cornerName cornerMap))
    (setq asi (car (errset (asiGetCurrentSession) nil)))
    (when asi
      ;; STEP 1: restore baseline so any previous sub-corner's override
      ;; doesn't persist on this same worker-VM asi session.
      (errset (asiSetSimOptionVal asi {qk} {qb}) nil)
      ;; STEP 2: if THIS corner has an override, apply it.
      (when entry
        (errset
          (asiSetSimOptionVal asi {qk} (cadr entry))
          nil))))"""


def write_pre_run_script(
    spec: PreRunSpec,
    workdir: Path | str,
    *,
    subdir: str = ".simkit/pre_run",
) -> Path:
    """Render ``spec`` and write it to ``<workdir>/<subdir>/pre_run_<itemHash>.il``.

    The filename includes a content hash so concurrent / cached runs
    with different IC maps don't stomp on each other. Returns the
    absolute resolved path of the written file (the value to pass to
    ``axlImportPreRunScript``).
    """
    source = render_pre_run_script(spec)
    h = hashlib.sha1(source.encode("utf-8")).hexdigest()[:10]
    safe_name = "".join(c if (c.isalnum() or c in "_-") else "_"
                        for c in spec.item_name)
    out_dir = Path(workdir).expanduser().resolve() / subdir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"pre_run_{safe_name}_{h}.il"
    out_path.write_text(source, encoding="utf-8")
    return out_path


def write_per_test_pre_run_scripts(
    spec: PreRunSpec,
    tests: list[str] | tuple[str, ...],
    workdir: Path | str,
    *,
    subdir: str = ".simkit/pre_run",
) -> dict[str, Path]:
    """Render one pre-run script per test and return ``{test: path}``.

    Phase 3A v1.9 #3 gap #2. Each test gets its EFFECTIVE corner_to_arg
    map — the per-test override from ``spec.per_test_corner_to_arg[test]``
    when present, otherwise the top-level ``spec.corner_to_arg``. The
    rest of the spec (mode, option_key, baseline_value, item_name) is
    shared across all tests.

    When ``spec.per_test_corner_to_arg`` is None (the v1.3/v1.7 single-
    map shape), every test gets the same script and Python's content-hash
    filename keeps the output count at one file on disk. Callers can opt
    into divergent per-test maps when they need it without rewriting the
    orchestrator's batch shape.
    """
    per_test_map = spec.per_test_corner_to_arg or {}
    out: dict[str, Path] = {}
    for test in tests:
        effective = per_test_map.get(test, spec.corner_to_arg)
        # Derive a child spec carrying the test-specific map (and a
        # test-tagged item_name so different tests get distinct content
        # hashes even when their effective maps differ).
        tagged_item = (
            spec.item_name if effective is spec.corner_to_arg
            else f"{spec.item_name}__{test}"
        )
        child = PreRunSpec(
            item_name=tagged_item,
            mode=spec.mode,
            corner_to_arg=effective,
            option_key=spec.option_key,
            baseline_value=spec.baseline_value,
            per_test_corner_to_arg=None,
        )
        out[test] = write_pre_run_script(child, workdir, subdir=subdir)
    return out


def build_corner_arg_map(
    sub_corner_names: list[str],
    corner_to_ic_path: Mapping[str, str | None],
    mode: str,
) -> dict[str, str]:
    """Build the corner→Spectre-option map embedded in the pre-run script.

    Args:
        sub_corner_names: ordered list of sub-corner names from the
            union's explode (e.g. ``["TT", "TT_pvt_0", ..., "TT_2p5G"]``).
            Defines the keyspace.
        corner_to_ic_path: mapping name → absolute IC file path (or None
            if the upstream corner failed / no IC produced). Corners
            mapped to None are dropped from the output map so the
            script's ``assoc`` lookup misses and the corner runs naked.
        mode: ``"readns"`` → emits ``readns="<path>"``;
              ``"readic"`` → emits ``readic="<path>"``.

    Live-discovered 2026-05-17 on fnxSession0 dogfood: Maestro
    **appends `additionalArgs` into the netlist's ``simulatorOptions
    options`` block**, NOT to Spectre's command line. Originally we
    used ``+nodeset <path>`` / ``+ic <path>`` (CLI syntax), which Spectre
    saw as malformed continuations of the prior option and emitted
    SFE-1994 warnings. The CORRECT form is the netlist-syntax
    ``readns="<path>"`` / ``readic="<path>"`` — same shape the engineer
    types into the Spectre Options > Init Conds & Nodesets form by hand.
    """
    if mode not in ("readns", "readic"):
        raise ValueError(f"mode must be readns/readic, got {mode!r}")
    out: dict[str, str] = {}
    for name in sub_corner_names:
        ic = corner_to_ic_path.get(name)
        if ic:
            # readns="<path>" / readic="<path>" — valid Spectre
            # simulatorOptions key=value syntax. Maestro appends this
            # verbatim into the existing simulatorOptions options block,
            # so quotes around the path are essential.
            out[name] = f'{mode}="{ic}"'
    return out
