"""``pvt corner-model`` subcommand group — Phase 5 Stage 1.

Offline verbs over a `.cornermodel.json` sidecar:

* ``pvt corner-model explode <path> [--json]`` — materialise the cornermodel
  to a Phase 2 union and print its exploded sub-corners.
* ``pvt corner-model build <path> [--out <p>]`` — materialise + write the
  union out as a `.union.json` the Phase 2 tooling can consume.

The live ``push`` / ``pull`` verbs are deferred (spec §8): for Stage 1 the GUI
owns the live Maestro path, and shipping unverified bridge code violates the
dispatch-mandate M4 gate.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from simkit.corner_model import (
    CornerModelError,
    load_cornermodel,
    load_pvtprofile,
    materialize,
)
from simkit.union import Union, explode


def add_subparser(sub) -> None:
    p = sub.add_parser(
        "corner-model",
        help="Corner-manager model helpers (explode / build).",
        description=(
            "Phase 5 Stage 1 corner manager. Offline verbs over a "
            ".cornermodel.json sidecar: explode, build. The live push/pull "
            "path is GUI-only for Stage 1 (spec §8)."
        ),
    )
    cs = p.add_subparsers(dest="corner_model_cmd", required=True)

    p_explode = cs.add_parser(
        "explode",
        help="Materialise a .cornermodel.json and print its sub-corners.",
    )
    p_explode.add_argument("path", help="Path to a .cornermodel.json sidecar.")
    p_explode.add_argument(
        "--json", dest="as_json", action="store_true",
        help="Emit a JSON array of sub-corner dicts instead of the table.",
    )
    p_explode.add_argument(
        "--profile", default=None,
        help="Path to a .pvtprofile.json — resolves Stage 6 axis_levels.",
    )
    p_explode.set_defaults(func=_run_explode)

    p_build = cs.add_parser(
        "build",
        help="Materialise a .cornermodel.json out to a .union.json.",
        description=(
            "Lower the cornermodel to a Phase 2 union and write it as a "
            ".union.json — the union is the demoted serialisation format "
            "(spec §3.4). Output is consumable by `pvt corners`."
        ),
    )
    p_build.add_argument("path", help="Path to a .cornermodel.json sidecar.")
    p_build.add_argument(
        "--out", default=None,
        help="Output .union.json path. Default: <name>.union.json alongside.",
    )
    p_build.add_argument(
        "--profile", default=None,
        help="Path to a .pvtprofile.json — resolves Stage 6 axis_levels.",
    )
    p_build.set_defaults(func=_run_build)


def _load_profile_arg(args, verb: str):
    """Load the optional --profile; print + return False on error, None if
    not supplied, else the PvtProfile."""
    if getattr(args, "profile", None) is None:
        return None
    try:
        return load_pvtprofile(args.profile)
    except CornerModelError as exc:
        print(f"pvt corner-model {verb}: {exc}", file=sys.stderr)
        return False


def _run_explode(args) -> int:
    try:
        model = load_cornermodel(args.path)
    except CornerModelError as exc:
        print(f"pvt corner-model explode: {exc}", file=sys.stderr)
        return 2
    profile = _load_profile_arg(args, "explode")
    if profile is False:
        return 2

    sub_corners = explode(materialize(model, profile))
    if args.as_json:
        print(json.dumps(
            [
                {
                    "row_name": sc.row_name,
                    "sub_corner_name": sc.sub_corner_name,
                    "vars": dict(sc.vars),
                    "models": [
                        {"file": m.file, "block": m.block,
                         "test": m.test, "section": m.section}
                        for m in sc.models
                    ],
                }
                for sc in sub_corners
            ],
            indent=2,
        ))
    else:
        for sc in sub_corners:
            parts = [f"{k}={v}" for k, v in sc.vars.items()]
            for k, m in enumerate(sc.models):
                label = "model.section" if len(sc.models) == 1 \
                    else f"model[{k}].section"
                parts.append(f"{label}={m.section}")
            print(f"{sc.sub_corner_name:<24}{', '.join(parts)}")
    return 0


def _run_build(args) -> int:
    src = Path(args.path).expanduser()
    try:
        model = load_cornermodel(src)
    except CornerModelError as exc:
        print(f"pvt corner-model build: {exc}", file=sys.stderr)
        return 2
    profile = _load_profile_arg(args, "build")
    if profile is False:
        return 2

    if args.out is not None:
        out_path = Path(args.out).expanduser()
    else:
        out_path = src.parent / f"{model.name}.union.json"

    out_path.write_text(
        _union_to_json(materialize(model, profile)), encoding="utf-8"
    )
    print(f"built -> {out_path}")
    return 0


def _union_to_json(u: Union) -> str:
    rows = []
    for r in u.rows:
        models = []
        for j, m in enumerate(r.models):
            entry = {
                "file": m.file, "block": m.block, "test": m.test,
                "section": (list(m.section) if j in r.sweep_model_indices
                            else m.section[0]),
            }
            if m.file_abs is not None:
                entry["_file_abs"] = m.file_abs
            models.append(entry)
        rows.append({
            "row_name": r.row_name,
            "enabled": r.enabled,
            "vars": {
                v: (list(tup) if v in r.sweep_var_keys else tup[0])
                for v, tup in r.vars.items()
            },
            "models": models,
        })
    return json.dumps({
        "union_schema_version": u.union_schema_version,
        "name": u.name,
        "project": u.project,
        "testbench_id": u.testbench_id,
        "rows": rows,
    }, indent=2) + "\n"
