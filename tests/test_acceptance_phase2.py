"""Phase 2 §6 acceptance gates.

Gates per `docs/phase2_pvt_union_spec.md` §6:

* **U1 — Round-trip fidelity on fnxSession0**. Offline-pinned via
  captured pre/post fixture triple in tests/fixtures/unions/u1_*. Live
  capture script: /tmp/u1_capture_edit.py (2026-05-13).
* **U2 — VCO LO acceptance** (21-row synthesised + pushed to fnxSession0
  2026-05-13; offline-pinned).
* **U3 — Explode arithmetic** (THIS FILE). 2 × 3 × 5 = 30 sub-corners,
  documented sub-corner names + ordering.
* **U4 — Sidecar -> CSV -> Sidecar bit-identical**. Blocked on
  `pvt corners build` CLI subcommand (Open Decision 8.3, CSV format).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.union import (  # noqa: E402
    ModelEntry,
    Union,
    UnionRow,
    explode,
)


def _make_2x3x5_union() -> Union:
    """Synthetic union with 3 sweep axes of length 2, 3, 5. Two vars + one
    model.section sweep. Per spec §3.4 the cross product is 30, ordered
    alphabetically by field key with values lex-sorted within each field.
    Field keys here: 'A' (var), 'B' (var), 'model[0].section'. Alphabetic:
    A < B < model[0].section. So A is innermost (fastest), section outermost."""
    return Union(
        union_schema_version=1,
        name="gate_u3",
        project="acceptance",
        testbench_id="x/y/schematic",
        rows=(
            UnionRow(
                row_name="R",
                vars={
                    "A": ("a1", "a2"),
                    "B": ("b1", "b2", "b3"),
                },
                models=(
                    ModelEntry(
                        file="m.scs",
                        block="Global",
                        test="All",
                        section=("s1", "s2", "s3", "s4", "s5"),
                    ),
                ),
                sweep_var_keys=frozenset({"A", "B"}),
                sweep_model_indices=frozenset({0}),
            ),
        ),
    )


class TestGateU3ExplodeArithmetic:

    def test_thirty_sub_corners(self):
        sub = explode(_make_2x3x5_union())
        assert len(sub) == 30

    def test_names_are_indexed(self):
        sub = explode(_make_2x3x5_union())
        names = [s.sub_corner_name for s in sub]
        assert names == [f"R_{i}" for i in range(30)]

    def test_innermost_is_A_alphabetic(self):
        """A is the alphabetically-first sweep key, so it is the
        innermost (fastest-changing) loop. R_0 and R_1 differ only in A."""
        sub = explode(_make_2x3x5_union())
        assert sub[0].vars["A"] == "a1"
        assert sub[1].vars["A"] == "a2"
        # B unchanged within the inner pair
        assert sub[0].vars["B"] == sub[1].vars["B"]
        # section unchanged within the inner pair
        assert sub[0].models[0].section == sub[1].models[0].section

    def test_middle_is_B(self):
        """B cycles every 2 steps (n_A=2). R_0..R_5 cycle B through its 3 vals."""
        sub = explode(_make_2x3x5_union())
        # B values lex-sorted: b1, b2, b3. Stride for B is n_A = 2.
        for i, expected_b in enumerate(("b1", "b2", "b3")):
            assert sub[i * 2].vars["B"] == expected_b
            assert sub[i * 2 + 1].vars["B"] == expected_b

    def test_outermost_is_section_lex_sorted(self):
        """section cycles every 6 steps (n_A * n_B = 6). 5 sections lex-sorted
        give s1, s2, s3, s4, s5."""
        sub = explode(_make_2x3x5_union())
        for s_idx, expected_sec in enumerate(("s1", "s2", "s3", "s4", "s5")):
            assert sub[s_idx * 6].models[0].section == expected_sec
            assert sub[s_idx * 6 + 5].models[0].section == expected_sec

    def test_last_sub_corner(self):
        sub = explode(_make_2x3x5_union())
        last = sub[29]
        assert last.sub_corner_name == "R_29"
        # Maximum index in each axis: A=a2 (idx 1), B=b3 (idx 2), section=s5 (idx 4).
        assert last.vars["A"] == "a2"
        assert last.vars["B"] == "b3"
        assert last.models[0].section == "s5"


# ----------------------------------------------------------------------------
# Gate U2 — VCO LO 21×3 acceptance (offline component).
#
# Live verified 2026-05-13 against fnxSession0: push of the 21-row sidecar
# leaves Maestro with 21 new corner rows alongside the existing ones; pull
# back is byte-identical for all 21 vars + models. The offline component
# below pins the fixture so future regressions catch any shape break in the
# loader or explode order without needing a live session. See DECISIONS #34
# (push verification) and TODO Phase 2 §6.
# ----------------------------------------------------------------------------

from simkit.union import load_union  # noqa: E402

_GATE_U2_FIXTURE = (
    _REPO_ROOT / "tests" / "fixtures" / "unions" / "vco_lo_21x3.union.json"
)


class TestGateU2VCOLoAcceptance:

    def test_fixture_loads(self):
        u = load_union(_GATE_U2_FIXTURE)
        assert u.name == "vco_lo_21x3"
        assert len(u.rows) == 21

    def test_seven_processes_three_ind_temps(self):
        u = load_union(_GATE_U2_FIXTURE)
        row_names = {r.row_name for r in u.rows}
        processes = {"TT", "FF", "SS", "FNSP", "SNFP", "FF_ext", "SS_ext"}
        ind_temps = {"Cold", "RT", "Hot"}
        expected = {f"{p}_{i}" for p in processes for i in ind_temps}
        assert row_names == expected

    def test_each_row_has_temperature_sweep_of_three(self):
        u = load_union(_GATE_U2_FIXTURE)
        for row in u.rows:
            assert row.vars["temperature"] == ("-40", "25", "105")

    def test_explode_yields_63_sub_corners(self):
        u = load_union(_GATE_U2_FIXTURE)
        sub = explode(u)
        assert len(sub) == 63

    def test_section_per_process(self):
        u = load_union(_GATE_U2_FIXTURE)
        for row in u.rows:
            proc = row.row_name.split("_")[0]
            # "FF_ext" / "SS_ext" split to ("FF", "ext"); section is the
            # lowercase full process tag e.g. "ff_ext".
            if row.row_name.startswith(("FF_ext", "SS_ext")):
                expected_section = row.row_name.rsplit("_", 1)[0].lower()
            else:
                expected_section = proc.lower()
            assert row.models[0].section == (expected_section,)


# ----------------------------------------------------------------------------
# Gate U1 — Round-trip fidelity (offline-pinned).
#
# Live capture on 2026-05-13 against fnxSession0 (skillbridge invocation in
# /tmp/u1_capture_edit.py):
#
#   1. pull baseline       -> u1_baseline.union.json (3 rows, TT.temp="55")
#   2. edit (Python)       -> u1_edited.union.json   (TT.temp flipped to "85")
#   3. push edited; pull   -> u1_post_edit_pull.union.json
#   4. push baseline back  -> fnxSession0 restored
#
# The edit-persists invariant: u1_edited and u1_post_edit_pull must be
# byte-identical modulo the top-level "name" field (pvtCornersPull derives
# `name` from the outPath basename, so two pulls written to different paths
# carry different names by construction). All other fields — rows, vars,
# models, sweeps — must match exactly.
# ----------------------------------------------------------------------------

_U1_BASELINE = _REPO_ROOT / "tests" / "fixtures" / "unions" / "u1_baseline.union.json"
_U1_EDITED = _REPO_ROOT / "tests" / "fixtures" / "unions" / "u1_edited.union.json"
_U1_POST = _REPO_ROOT / "tests" / "fixtures" / "unions" / "u1_post_edit_pull.union.json"


def _drop_top_name(d: dict) -> dict:
    return {k: v for k, v in d.items() if k != "name"}


class TestGateU1RoundTrip:

    def test_baseline_loads_three_rows(self):
        u = load_union(_U1_BASELINE)
        assert len(u.rows) == 3
        assert {r.row_name for r in u.rows} == {"TT", "TT_pvt", "TT_2p5G"}

    def test_baseline_TT_temperature_is_original(self):
        u = load_union(_U1_BASELINE)
        tt = next(r for r in u.rows if r.row_name == "TT")
        # Loader normalises scalar var values to 1-tuples.
        assert tt.vars["temperature"] == ("55",)

    def test_edited_diverges_from_baseline(self):
        baseline = load_union(_U1_BASELINE)
        edited = load_union(_U1_EDITED)
        b_tt = next(r for r in baseline.rows if r.row_name == "TT")
        e_tt = next(r for r in edited.rows if r.row_name == "TT")
        assert b_tt.vars["temperature"] == ("55",)
        assert e_tt.vars["temperature"] == ("85",)

    def test_edit_persists_through_push_pull(self):
        """The core U1 invariant: edited.union pushed and re-pulled comes
        back identical modulo the top-level `name` field."""
        edited = json.loads(_U1_EDITED.read_text())
        post = json.loads(_U1_POST.read_text())
        assert _drop_top_name(edited) == _drop_top_name(post)

    def test_post_pull_TT_temperature_is_edited_value(self):
        """Sanity probe — independent of the JSON-equality check above."""
        u = load_union(_U1_POST)
        tt = next(r for r in u.rows if r.row_name == "TT")
        assert tt.vars["temperature"] == ("85",)

    def test_non_TT_rows_unaffected_by_edit(self):
        """Edit only touched TT — TT_pvt and TT_2p5G must be untouched
        across baseline / edited / post_pull."""
        baseline = json.loads(_U1_BASELINE.read_text())
        post = json.loads(_U1_POST.read_text())
        b_other = [r for r in baseline["rows"] if r["row_name"] != "TT"]
        p_other = [r for r in post["rows"] if r["row_name"] != "TT"]
        assert b_other == p_other
