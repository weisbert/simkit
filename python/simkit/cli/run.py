"""``pvt run`` subcommand — Phase 3A §5 orchestrator entry point.

Two modes:

  1. Sidecar mode:    ``pvt run <review.json> [--dry-run] [--items name1,name2]``
  2. Ad-hoc mode:     ``pvt run --tests T1,T2 --union U.union.json [--bundle B.measure.json] [--dry-run]``

v1 ships ``--dry-run`` end-to-end. Live execution is gated on Phase 3A §3
(SKILL bridge) + §4 (strategy framework); ``pvt run`` without ``--dry-run``
exits with a clear "not yet implemented" message pointing at the tasks.

Exit codes:
    0  plan resolved + (dry-run) printed cleanly
    2  schema / load error
    3  missing union / bundle file (only when --strict-paths is set)
    4  --items: unknown item name
    5  live execution requested but not yet implemented
"""

from __future__ import annotations

import argparse
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
from simkit.project import PvtProjectError, load_pvtproject
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

    # Live mode — gated on §3 + §4.
    print("ERROR: live execute() not yet implemented (Phase 3A §3 SKILL bridge "
          "+ §4 strategy framework pending). Use --dry-run for now.",
          file=sys.stderr)
    return 5


def _resolve_project_name(project_path: Optional[str]) -> str:
    """For ad-hoc mode: load enclosing .pvtproject and return its project name."""
    start = Path(project_path) if project_path else None
    pvtproj = load_pvtproject(start=start)
    return pvtproj.project


def _try_load_pvtproject(project_path: Optional[str]):
    start = Path(project_path) if project_path else None
    return load_pvtproject(start=start)
