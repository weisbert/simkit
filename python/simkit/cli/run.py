"""``pvt run`` subcommand — Phase 3A §5 orchestrator entry point.

Two modes:

  1. Sidecar mode:    ``pvt run <review.json> [--dry-run] [--items name1,name2]``
  2. Ad-hoc mode:     ``pvt run --tests T1,T2 --union U.union.json [--bundle B.measure.json] [--dry-run]``

Live execution drives Maestro via ``simkit.skill_bridge`` and calls
``orchestrator.execute()``. Use ``--dry-run`` to print the plan without
driving Maestro.

Exit codes:
    0  dry-run printed OR all items completed AND zero FAIL corners remain
    2  schema / load error / missing --session in live mode
    3  missing union / bundle file (only when --strict-paths is set)
    4  --items: unknown item name
    6  one or more items did not complete cleanly OR completed with FAIL
       corners remaining after the strategy chain exhausted
    7  bridge import / session setup failure
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import List, Optional

from simkit.orchestrator import (
    NotImplementedYetError,
    OrchestratorError,
    dry_run,
    execute,
    plan_review,
    synthesize_adhoc_review,
)
from simkit.project import (
    PvtProjectError,
    PvtProjectNotFoundError,
    find_pvtproject,
    load_pvtproject,
)
from simkit.review import (
    ReviewError,
    check_project_match,
    load_review,
)


def add_subparser(sub) -> None:
    p = sub.add_parser(
        "run",
        help="Drive a review.json (or ad-hoc tests+union) through Maestro.",
        description=(
            "Phase 3A orchestrator. Sidecar mode: pvt run <review.json>. "
            "Ad-hoc mode: pvt run --tests T1,T2 --union U.union.json. "
            "Use --dry-run to see the plan without driving Maestro."
        ),
    )

    # Mutually exclusive: review path OR ad-hoc tests.
    p.add_argument(
        "review_path",
        nargs="?",
        default=None,
        help="Path to a .review.json. Mutually exclusive with --tests.",
    )
    p.add_argument(
        "--tests",
        default=None,
        help=(
            "Ad-hoc mode: comma-separated test names. Requires --union. "
            "Synthesizes a one-item review in memory."
        ),
    )
    p.add_argument(
        "--union",
        default=None,
        help="Ad-hoc mode: path to a .union.json (required with --tests).",
    )
    p.add_argument(
        "--bundle",
        default=None,
        help="Ad-hoc mode: optional path to a .measure.json.",
    )
    p.add_argument(
        "--items",
        default=None,
        help=(
            "Sidecar mode: comma-separated item names to run (subset of "
            "review.items). Default: all enabled items."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan; do NOT drive Maestro.",
    )
    p.add_argument(
        "--strict-paths",
        action="store_true",
        help=(
            "When set, exit non-zero if any union/bundle sidecar referenced "
            "by an item is missing."
        ),
    )
    p.add_argument(
        "--project",
        default=None,
        help=(
            "Path to a .pvtproject. Default: PVT_PROJECT env var or cwd walker. "
            "Only consulted in ad-hoc mode (for the project name); sidecar mode "
            "reads project from the review.json."
        ),
    )
    p.add_argument(
        "--session",
        default=None,
        help=(
            "Maestro session name to drive (e.g. fnxSession0). Required when "
            "NOT in --dry-run mode. Can also be set via PVT_SESSION env var."
        ),
    )
    p.add_argument(
        "--no-push-union",
        action="store_true",
        help=(
            "Do NOT push each item's union sidecar before running. Use the "
            "session's current corner table as-is. Useful when the user has "
            "already set up corners by hand."
        ),
    )
    p.add_argument(
        "--history-prefix",
        default="orch",
        help=(
            "Prefix for auto-generated Maestro history names "
            "(<prefix>_<item>_<timestamp>). Default: 'orch'."
        ),
    )
    p.set_defaults(func=_cli_run)


def _cli_run(args: argparse.Namespace) -> int:
    # --- mode resolution -------------------------------------------------
    if args.review_path and args.tests:
        print("ERROR: cannot mix sidecar mode (review_path) with ad-hoc mode "
              "(--tests). Pick one.", file=sys.stderr)
        return 2
    if not args.review_path and not args.tests:
        print("ERROR: provide either a review.json path OR --tests + --union.",
              file=sys.stderr)
        return 2
    if args.tests and not args.union:
        print("ERROR: --tests requires --union.", file=sys.stderr)
        return 2

    items_filter: Optional[List[str]] = None
    if args.items:
        items_filter = [s.strip() for s in args.items.split(",") if s.strip()]

    # --- build the Review object ----------------------------------------
    try:
        if args.review_path:
            review = load_review(Path(args.review_path))
        else:
            project = _resolve_project_name(args.project)
            tests = [s.strip() for s in args.tests.split(",") if s.strip()]
            review = synthesize_adhoc_review(
                project=project,
                tests=tests,
                union=Path(args.union),
                bundle=Path(args.bundle) if args.bundle else None,
            )
    except ReviewError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except PvtProjectError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    # --- project-match check (sidecar mode only) ------------------------
    if args.review_path:
        try:
            pvtproj = _try_load_pvtproject(args.project)
        except PvtProjectError as exc:
            print(f"WARNING: could not locate .pvtproject for project-match "
                  f"check: {exc}", file=sys.stderr)
            pvtproj = None
        if pvtproj is not None:
            try:
                check_project_match(review, pvtproj.project)
            except ReviewError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 2

    # --- build the plan -------------------------------------------------
    try:
        plan = plan_review(review, items_filter=items_filter)
    except OrchestratorError as exc:
        msg = str(exc)
        print(f"ERROR: {msg}", file=sys.stderr)
        return 4 if "--items" in msg else 2

    # --- dispatch -------------------------------------------------------
    if args.dry_run:
        dry_run(plan)
        if args.strict_paths and plan.has_blocking_issues:
            return 3
        return 0

    # --- live mode ------------------------------------------------------
    session = args.session or os.environ.get("PVT_SESSION")
    if not session:
        print("ERROR: live mode requires --session NAME (or PVT_SESSION env "
              "var). Use --dry-run to skip Maestro.", file=sys.stderr)
        return 2

    pvtproject_path = _resolve_pvtproject_path(
        args.project, review_path=args.review_path,
    )
    if pvtproject_path is None:
        print("ERROR: live mode requires a .pvtproject — pass --project PATH "
              "or run from inside a project directory.", file=sys.stderr)
        return 2

    try:
        from simkit import skill_bridge as bridge
    except Exception as exc:
        print(f"ERROR: cannot import simkit.skill_bridge: {exc}",
              file=sys.stderr)
        return 7

    try:
        report = execute(
            plan,
            bridge,
            session=session,
            pvtproject_path=pvtproject_path,
            history_prefix=args.history_prefix,
            push_union=not args.no_push_union,
        )
    except OrchestratorError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 6
    except Exception as exc:
        print(f"ERROR: execute() raised {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 7

    print("---")
    print(f"DONE  {len(report.items)} item(s), "
          f"snapshot_restored={report.snapshot_restored}")
    incomplete = [ir for ir in report.items if not ir.completed]
    failed_any = [ir for ir in report.items if ir.final_failed_corners]
    for ir in report.items:
        if not ir.completed:
            status = "INCOMPLETE"
        elif ir.final_failed_corners:
            status = f"FAIL ({len(ir.final_failed_corners)})"
        else:
            status = "ok"
        hist = ", ".join(ir.history_names) or "(no history)"
        print(f"  [{status}] {ir.item_name}  histories={hist}")
        if ir.final_failed_corners:
            print(f"           FAIL corners: "
                  f"{', '.join(ir.final_failed_corners)}")
        for att in ir.strategy_attempts:
            print(f"           {att.strategy_name} #{att.attempt_number} "
                  f"→ {att.outcome}  targeted="
                  f"{','.join(att.corners_targeted) or '∅'}  "
                  f"remaining="
                  f"{','.join(att.corners_remaining) or '∅'}")
        if ir.notes:
            for line in ir.notes.splitlines():
                print(f"           ! {line}")

    return 0 if not (incomplete or failed_any) else 6


def _resolve_project_name(project_path: Optional[str]) -> str:
    """For ad-hoc mode: load enclosing .pvtproject and return its project name."""
    start = Path(project_path) if project_path else None
    pvtproj = load_pvtproject(start=start)
    return pvtproj.project


def _try_load_pvtproject(project_path: Optional[str]):
    start = Path(project_path) if project_path else None
    return load_pvtproject(start=start)


def _resolve_pvtproject_path(
    project_arg: Optional[str],
    *,
    review_path: Optional[str],
) -> Optional[Path]:
    """Resolve the .pvtproject path execute() needs.

    Order: --project arg → walk up from review.json's dir → walk up from cwd.
    Returns None if nothing found (caller decides whether that's fatal).
    """
    if project_arg:
        p = Path(project_arg).expanduser()
        if p.is_dir():
            found = find_pvtproject(p)
            return found
        return p if p.is_file() else None

    env_val = os.environ.get("PVT_PROJECT")
    if env_val:
        p = Path(env_val).expanduser()
        return p if p.is_file() else None

    if review_path:
        found = find_pvtproject(Path(review_path).resolve().parent)
        if found is not None:
            return found

    return find_pvtproject()
