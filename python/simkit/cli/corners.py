"""``pvt corners`` subcommand group.

Nested argparse group:

* ``pvt corners explode <path>`` — explode a `.union.json` to its sub-corners.
* ``pvt corners list [--project P]`` — enumerate unions under ``<unionsDir>/``.
* ``pvt corners diff <a> <b>`` — diff two `.union.json` files row-by-row.
* ``pvt corners pull <out>`` — pull live ADE-XL setup to a `.union.json`
  sidecar (via skillbridge → ``pvtCornersPull``).
* ``pvt corners push <union.json>`` — push a `.union.json` sidecar into
  the live ADE-XL setup (via skillbridge → ``pvtCornersPush``).

The ``build`` verb (sidecar → Maestro corners-CSV) is still blocked on
Open Decision 8.3 (CSV format).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List

from simkit.corners import (
    UnionDiff,
    UnionDiffChange,
    UnionListing,
    diff_unions,
    list_unions,
    resolve_unions_dir,
)
from simkit.project import PvtProjectError, load_pvtproject
from simkit.union import (
    SubCorner,
    UnionError,
    explode,
    load_union,
)


def add_subparser(sub) -> None:
    p = sub.add_parser(
        "corners",
        help="PVT-union authoring helpers (explode / list / diff / pull / push).",
        description=(
            "Phase 2 PVT-union builder. Offline verbs: explode, list, diff. "
            "Live-Maestro verbs (via skillbridge): pull, push. The `build` "
            "verb (sidecar → Maestro corners-CSV) is still pending Open "
            "Decision 8.3."
        ),
    )
    cs = p.add_subparsers(dest="corners_cmd", required=True)

    p_explode = cs.add_parser(
        "explode",
        help="Print exploded sub-corners for a .union.json.",
    )
    p_explode.add_argument("path", help="Path to a .union.json sidecar.")
    p_explode.add_argument(
        "--json", dest="as_json", action="store_true",
        help="Emit a JSON array of sub-corner dicts instead of the default table.",
    )
    p_explode.set_defaults(func=_run_explode)

    p_list = cs.add_parser(
        "list",
        help="List unions configured under <unionsDir>/.",
    )
    p_list.add_argument(
        "--project", default=None,
        help=(
            "Path to a .pvtproject file. Default: discover via PVT_PROJECT or "
            "cwd-walker."
        ),
    )
    p_list.add_argument(
        "--json", dest="as_json", action="store_true",
        help="Emit a JSON array of union listings instead of the default table.",
    )
    p_list.set_defaults(func=_run_list)

    p_diff = cs.add_parser(
        "diff",
        help="Diff two .union.json files row-by-row.",
    )
    p_diff.add_argument("a", help="First .union.json path.")
    p_diff.add_argument("b", help="Second .union.json path.")
    p_diff.add_argument(
        "--json", dest="as_json", action="store_true",
        help="Emit a JSON object instead of the default grouped table.",
    )
    p_diff.set_defaults(func=_run_diff)

    p_pull = cs.add_parser(
        "pull",
        help="Pull live ADE-XL corner table into a .union.json sidecar.",
        description=(
            "Invoke skillbridge to call pvtCornersPull against the running "
            "Virtuoso session. The output filename basename (minus "
            "'.union.json') becomes the union 'name' unless --union-name "
            "overrides it."
        ),
    )
    p_pull.add_argument("out_path", help="Output path (must end .union.json).")
    p_pull.add_argument(
        "--project", default=None,
        help="Path to a .pvtproject file. Default: PVT_PROJECT env or cwd walker.",
    )
    p_pull.add_argument(
        "--session", default=None,
        help="Maestro session id. Default: SKILL infers from the active window.",
    )
    p_pull.add_argument(
        "--union-name", default=None,
        help="Override the union 'name' field (default: basename of out_path).",
    )
    p_pull.set_defaults(func=_run_pull)

    p_push = cs.add_parser(
        "push",
        help="Push a .union.json sidecar into the live ADE-XL setup.",
        description=(
            "Invoke skillbridge to call pvtCornersPush against the running "
            "Virtuoso session. Use --dry-run to parse + validate the sidecar "
            "without touching the live setup."
        ),
    )
    p_push.add_argument("union_json_path", help="Path to a .union.json sidecar.")
    p_push.add_argument(
        "--project", default=None,
        help="Path to a .pvtproject file. Default: PVT_PROJECT env or cwd walker.",
    )
    p_push.add_argument(
        "--session", default=None,
        help="Maestro session id. Default: SKILL infers from the active window.",
    )
    p_push.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="Validate the sidecar but do not touch the live setup.",
    )
    p_push.set_defaults(func=_run_push)


# --- explode ---------------------------------------------------------------


def _run_explode(args) -> int:
    try:
        union = load_union(args.path)
    except UnionError as exc:
        print(f"pvt corners explode: {exc}", file=sys.stderr)
        return 2

    sub_corners = explode(union)
    if args.as_json:
        print(json.dumps([_sub_corner_to_dict(sc) for sc in sub_corners], indent=2))
    else:
        for sc in sub_corners:
            print(_format_sub_corner(sc))
    return 0


def _sub_corner_to_dict(sc: SubCorner) -> dict:
    return {
        "row_name": sc.row_name,
        "sub_corner_name": sc.sub_corner_name,
        "vars": dict(sc.vars),
        "models": [
            {
                "file": m.file,
                "block": m.block,
                "test": m.test,
                "section": m.section,
            }
            for m in sc.models
        ],
    }


def _format_sub_corner(sc: SubCorner) -> str:
    parts: list[str] = []
    for vname in sc.vars:
        parts.append(f"{vname}={sc.vars[vname]}")
    for k, m in enumerate(sc.models):
        label = "model.section" if len(sc.models) == 1 else f"model[{k}].section"
        parts.append(f"{label}={m.section}")
    return f"{sc.sub_corner_name:<16}{', '.join(parts)}"


# --- list ------------------------------------------------------------------


_LIST_COL_WIDTHS = {
    "name": 24,
    "testbench": 32,
    "rows": 5,
    "sub_corners": 11,
    "status": 30,
}


def _run_list(args) -> int:
    try:
        if args.project is not None:
            pvtproject_path = Path(args.project).expanduser().resolve()
            if not pvtproject_path.is_file():
                print(
                    f"pvt corners list: .pvtproject not found: {pvtproject_path}",
                    file=sys.stderr,
                )
                return 3
        else:
            proj = load_pvtproject()
            pvtproject_path = proj.source_path
    except PvtProjectError as exc:
        print(f"pvt corners list: {exc}", file=sys.stderr)
        return 3

    unions_dir = resolve_unions_dir(pvtproject_path)
    listings = list_unions(unions_dir)

    if args.as_json:
        out = [_listing_to_dict(l) for l in listings]
        print(json.dumps(out, indent=2))
    else:
        _print_list_table(listings, unions_dir)
    return 0


def _listing_to_dict(l: UnionListing) -> dict:
    return {
        "name": l.name,
        "path": str(l.path),
        "project": l.project,
        "testbench_id": l.testbench_id,
        "row_count": l.row_count,
        "sub_corner_count": l.sub_corner_count,
        "status": "OK" if l.error is None else l.error,
    }


def _print_list_table(listings: List[UnionListing], unions_dir: Path) -> None:
    headers = ("NAME", "TESTBENCH", "ROWS", "SUB_CORNERS", "STATUS")
    keys = ("name", "testbench", "rows", "sub_corners", "status")
    widths = [_LIST_COL_WIDTHS[k] for k in keys]
    sep = "  "

    print(f"# unionsDir = {unions_dir}")
    print(sep.join(h.ljust(w) for h, w in zip(headers, widths)))
    print(sep.join("-" * w for w in widths))

    if not listings:
        print("(no unions found)")
        return

    for l in listings:
        if l.error is None:
            status = "OK"
            tb = l.testbench_id or ""
            rows_str = str(l.row_count)
            sub_str = str(l.sub_corner_count)
        else:
            status = l.error
            tb = ""
            rows_str = "-"
            sub_str = "-"
        cells = (
            _trunc(l.name, widths[0]),
            _trunc(tb, widths[1]),
            rows_str.ljust(widths[2]),
            sub_str.ljust(widths[3]),
            _trunc(status, widths[4]),
        )
        print(sep.join(c.ljust(w) for c, w in zip(cells, widths)))


def _trunc(s: str, width: int) -> str:
    if len(s) <= width:
        return s
    if width <= 1:
        return s[:width]
    return s[: width - 1] + "…"


# --- diff ------------------------------------------------------------------


def _run_diff(args) -> int:
    try:
        a = load_union(args.a)
        b = load_union(args.b)
    except UnionError as exc:
        print(f"pvt corners diff: {exc}", file=sys.stderr)
        return 2

    diff = diff_unions(a, b)

    if args.as_json:
        print(json.dumps(_diff_to_dict(diff), indent=2))
    else:
        _print_diff(a, b, diff)

    return 1 if diff.has_differences() else 0


def _diff_to_dict(diff: UnionDiff) -> dict:
    return {
        "added": list(diff.added),
        "removed": list(diff.removed),
        "changed": [
            {
                "row_name": c.row_name,
                "field": c.field,
                "a": c.a,
                "b": c.b,
            }
            for c in diff.changed
        ],
        "identical_count": diff.identical_count,
    }


def _print_diff(a, b, diff: UnionDiff) -> None:
    print(f"# diff a={a.name!r} (rows={len(a.rows)})")
    print(f"#      b={b.name!r} (rows={len(b.rows)})")
    print()

    print("Rows added in B:")
    if diff.added:
        for n in diff.added:
            print(f"  + {n}")
    else:
        print("  (none)")
    print()

    print("Rows removed (only in A):")
    if diff.removed:
        for n in diff.removed:
            print(f"  - {n}")
    else:
        print("  (none)")
    print()

    print("Rows changed:")
    if diff.changed:
        for c in diff.changed:
            print(
                f"  {c.row_name}  {c.field}  "
                f"{json.dumps(c.a)} -> {json.dumps(c.b)}"
            )
    else:
        print("  (none)")
    print()

    print(f"Rows identical: {diff.identical_count}")


# --- pull / push (live Maestro via skillbridge) --------------------------


def _resolve_project_for_live(args) -> Path | None:
    """Resolve `.pvtproject` path for live verbs; print + return None on error."""
    from simkit.skill_bridge import resolve_pvtproject_path
    try:
        return resolve_pvtproject_path(args.project)
    except (FileNotFoundError, PvtProjectError) as exc:
        print(f"pvt corners {args.corners_cmd}: {exc}", file=sys.stderr)
        return None


def _run_pull(args) -> int:
    out_path = Path(args.out_path).expanduser()
    if not out_path.name.endswith(".union.json"):
        print(
            f"pvt corners pull: out_path basename must end '.union.json' "
            f"(got {out_path.name!r})",
            file=sys.stderr,
        )
        return 2

    pvtproject_path = _resolve_project_for_live(args)
    if pvtproject_path is None:
        return 3

    from simkit.skill_bridge import SkillBridgeError, pvt_corners_pull
    try:
        result = pvt_corners_pull(
            str(out_path.resolve()),
            pvtproject_path=pvtproject_path,
            session=args.session,
            union_name=args.union_name,
        )
    except SkillBridgeError as exc:
        print(f"pvt corners pull: {exc}", file=sys.stderr)
        return 4

    print(f"pulled -> {result}")
    return 0


def _run_push(args) -> int:
    union_path = Path(args.union_json_path).expanduser()
    if not union_path.is_file():
        print(
            f"pvt corners push: .union.json not found: {union_path}",
            file=sys.stderr,
        )
        return 2

    pvtproject_path = _resolve_project_for_live(args)
    if pvtproject_path is None:
        return 3

    from simkit.skill_bridge import SkillBridgeError, pvt_corners_push
    try:
        union_name = pvt_corners_push(
            str(union_path.resolve()),
            pvtproject_path=pvtproject_path,
            session=args.session,
            dry_run=args.dry_run,
        )
    except SkillBridgeError as exc:
        print(f"pvt corners push: {exc}", file=sys.stderr)
        return 4

    marker = " (dry-run)" if args.dry_run else ""
    print(f"pushed{marker} -> {union_name}")
    return 0
