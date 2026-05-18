"""``pvt star`` / ``pvt unstar`` / ``pvt sync-stars`` subcommands (v1.8 #4).

Workflow:

* ``pvt star <run_id>``         — UPDATE runs.starred=TRUE, then push the
  matching history_name to Maestro via ``maeSetHistoryLock t``. Use
  ``--no-push`` to skip the Maestro round-trip (DB-only).
* ``pvt unstar <run_id>``       — reverse.
* ``pvt sync-stars push``       — bulk: DB authoritative.
* ``pvt sync-stars pull``       — bulk: Maestro authoritative.

Both single-run and bulk paths share the same :func:`simkit.star` core
so the sync semantics never diverge.

Exit codes:
    0  success (including idempotent no-ops)
    1  domain error (run not found, label/session conflict)
    3  filesystem / DB IO error
    7  skillbridge import / session-resolve failure (only when pushing)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from simkit.db import bootstrap, connect
from simkit.errors import RunNotFoundError, SimkitError
from simkit.project import PvtProjectError, load_pvtproject
from simkit.star import (
    StarResult,
    apply_sync_plan,
    compute_sync_plan,
    load_db_rows,
    set_run_starred,
)


# --- shared helpers ------------------------------------------------------


def _add_db_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--db", type=Path, default=None,
        help=(
            "Override DB path. Default: <dbRoot>/simkit.duckdb from the "
            ".pvtproject discovered via PVT_PROJECT or cwd-walker."
        ),
    )


def _add_session_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--session", default=None,
        help=(
            "Maestro session name. Default: PVT_SESSION env var; required "
            "for any subcommand that pushes to Maestro."
        ),
    )


def _resolve_db_path(args) -> Path:
    if args.db is not None:
        return Path(args.db).expanduser().resolve()
    proj = load_pvtproject()
    return Path(proj.db_root) / "simkit.duckdb"


def _resolve_session(args, prog: str) -> str:
    import os
    sess = args.session or os.environ.get("PVT_SESSION")
    if not sess:
        print(
            f"{prog}: --session is required (or set PVT_SESSION env)",
            file=sys.stderr,
        )
        sys.exit(2)
    return sess


def _import_bridge():
    """Lazy import so DB-only paths don't drag in skillbridge."""
    try:
        from simkit import skill_bridge as sb
    except Exception as exc:  # pragma: no cover — env-specific
        print(
            f"pvt: skillbridge unavailable ({exc}). Use --no-push to "
            "operate on the DB only.",
            file=sys.stderr,
        )
        sys.exit(7)
    return sb


# --- pvt star / pvt unstar ----------------------------------------------


def _add_subparser_star(sub) -> None:
    p = sub.add_parser(
        "star",
        help="Mark a run as starred (and lock the Maestro history).",
        description=(
            "Sets runs.starred=TRUE for <run_id>. By default also calls "
            "maeSetHistoryLock t on the matching history in the named "
            "session so the history can't be deleted from the GUI."
        ),
    )
    p.add_argument("run_id", help="run_id of an already-ingested run.")
    p.add_argument(
        "--no-push", dest="push", action="store_false", default=True,
        help="Skip the Maestro lock push (DB-only).",
    )
    _add_session_arg(p)
    _add_db_arg(p)
    p.set_defaults(func=_run_star, _mode="set")


def _add_subparser_unstar(sub) -> None:
    p = sub.add_parser(
        "unstar",
        help="Clear the starred flag (and unlock the Maestro history).",
        description=(
            "Sets runs.starred=FALSE for <run_id>. By default also calls "
            "maeSetHistoryLock nil on the matching history."
        ),
    )
    p.add_argument("run_id", help="run_id of an already-ingested run.")
    p.add_argument(
        "--no-push", dest="push", action="store_false", default=True,
        help="Skip the Maestro unlock push (DB-only).",
    )
    _add_session_arg(p)
    _add_db_arg(p)
    p.set_defaults(func=_run_star, _mode="clear")


def _run_star(args) -> int:
    starred = (args._mode == "set")
    prog = "pvt star" if starred else "pvt unstar"

    try:
        db_path = _resolve_db_path(args)
    except PvtProjectError as exc:
        print(f"{prog}: {exc}", file=sys.stderr)
        return 3
    if not db_path.is_file():
        print(
            f"{prog}: DB not found: {db_path} (run `pvt ingest` first)",
            file=sys.stderr,
        )
        return 3

    try:
        con = connect(db_path)
    except Exception as exc:  # pragma: no cover - duckdb wraps OSError
        print(f"{prog}: cannot open DB {db_path}: {exc}", file=sys.stderr)
        return 3
    try:
        bootstrap(con)
        try:
            res = set_run_starred(con, run_id=args.run_id, starred=starred)
        except RunNotFoundError as exc:
            print(f"{prog}: {exc}", file=sys.stderr)
            return 1
        except SimkitError as exc:
            print(f"{prog}: {exc}", file=sys.stderr)
            return 1
    finally:
        con.close()

    print(_format_star_line(prog, res))

    if not args.push:
        return 0
    if res.action == "noop":
        # DB already matched intent; still push to Maestro in case it drifted.
        pass
    sess = _resolve_session(args, prog)
    sb = _import_bridge()
    try:
        sb.pvt_runner_set_history_lock(
            res.history_name, starred, session=sess,
        )
    except sb.SkillBridgeError as exc:
        print(
            f"{prog}: Maestro push failed ({exc}). DB state is set; "
            "rerun `pvt sync-stars push` once Maestro reachable.",
            file=sys.stderr,
        )
        return 1
    print(
        f"{prog}: maestro {res.history_name!r} -> "
        f"{'locked' if starred else 'unlocked'}"
    )
    return 0


def _format_star_line(prog: str, res: StarResult) -> str:
    if res.action == "set":
        return (
            f"{prog}: starred run_id={res.run_id} "
            f"history={res.history_name!r}"
        )
    if res.action == "cleared":
        return (
            f"{prog}: cleared run_id={res.run_id} "
            f"history={res.history_name!r}"
        )
    # noop
    return (
        f"{prog}: run_id={res.run_id} already "
        f"{'starred' if res.previous else 'unstarred'} (noop)"
    )


# --- pvt sync-stars push|pull -------------------------------------------


def _add_subparser_sync(sub) -> None:
    p = sub.add_parser(
        "sync-stars",
        help="Reconcile starred-runs vs Maestro history locks.",
        description=(
            "push: DB authoritative — push the starred set to Maestro "
            "locks. pull: Maestro authoritative — copy the Maestro lock "
            "state into runs.starred for matching history_names."
        ),
    )
    p.add_argument(
        "direction", choices=("push", "pull"),
        help="Which side wins.",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Print the plan without writing anything.",
    )
    _add_session_arg(p)
    _add_db_arg(p)
    p.set_defaults(func=_run_sync)


def _run_sync(args) -> int:
    prog = "pvt sync-stars"

    try:
        db_path = _resolve_db_path(args)
    except PvtProjectError as exc:
        print(f"{prog}: {exc}", file=sys.stderr)
        return 3
    if not db_path.is_file():
        print(
            f"{prog}: DB not found: {db_path} (run `pvt ingest` first)",
            file=sys.stderr,
        )
        return 3

    sess = _resolve_session(args, prog)
    sb = _import_bridge()

    try:
        mae_map = sb.pvt_runner_get_history_lock_map(session=sess)
    except sb.SkillBridgeError as exc:
        print(f"{prog}: cannot read session {sess!r}: {exc}", file=sys.stderr)
        return 1

    try:
        con = connect(db_path)
    except Exception as exc:  # pragma: no cover
        print(f"{prog}: cannot open DB {db_path}: {exc}", file=sys.stderr)
        return 3
    try:
        bootstrap(con)
        db_rows = load_db_rows(con)
        plan = compute_sync_plan(
            direction=args.direction,
            db_rows=db_rows,
            maestro_lock_map=mae_map,
        )

        for w in plan.warnings:
            print(f"{prog}: WARN {w}", file=sys.stderr)

        if not plan.actions:
            print(f"{prog}: nothing to do (DB and Maestro already in sync)")
            return 0

        for act in plan.actions:
            verb = {
                "maestro_lock": "lock   maestro",
                "maestro_unlock": "unlock maestro",
                "db_star": "star   db",
                "db_unstar": "unstar db",
            }[act.kind]
            print(
                f"  {verb}  {act.history_name}  "
                f"(runs={list(act.affected_run_ids)})"
            )

        if args.dry_run:
            print(f"{prog}: dry-run, {len(plan.actions)} change(s) NOT applied")
            return 0

        from functools import partial
        setter = partial(
            sb.pvt_runner_set_history_lock, session=sess,
        )
        try:
            apply_sync_plan(plan, con=con, set_history_lock=setter)
        except sb.SkillBridgeError as exc:
            print(
                f"{prog}: apply failed mid-plan ({exc}). Re-run to retry.",
                file=sys.stderr,
            )
            return 1
        print(
            f"{prog}: applied {len(plan.actions)} change(s) "
            f"({args.direction})"
        )
    finally:
        con.close()
    return 0


# --- registration -------------------------------------------------------


def add_subparser(sub) -> None:
    """Register all three star-related subcommands."""
    _add_subparser_star(sub)
    _add_subparser_unstar(sub)
    _add_subparser_sync(sub)


def main(argv: Optional[list] = None) -> int:  # pragma: no cover
    parser = argparse.ArgumentParser(prog="pvt-star")
    sub = parser.add_subparsers(dest="cmd", required=True)
    add_subparser(sub)
    ns = parser.parse_args(argv if argv is not None else sys.argv[1:])
    return ns.func(ns)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
