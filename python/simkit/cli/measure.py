"""``pvt measure`` subcommand group (Phase 3B §5).

Twelve verbs wired in here, mapping the spec's CLI surface table 1:1.
Offline verbs (``new-template``, ``new-signal-group``, ``new-bundle``,
``list-*``, ``show-template``, ``render``, ``diff``) work without a
live Virtuoso session. Live verbs (``apply``, ``pull``, ``restore``)
invoke :mod:`simkit.skill_bridge` to reach the running Maestro.

Argparse registration + error-to-exit-code mapping mirrors
``simkit.cli.corners``:

* ``0`` — success
* ``2`` — argument/format error or load-time validation failure
* ``3`` — project / sidecar discovery failure (no ``.pvtproject``,
  missing template referenced by a bundle, etc.)
* ``4`` — skillbridge surfaced a ``pvtErr``

The mapping intentionally keeps "user typo" (2) distinct from
"environment misconfigured" (3) and "Cadence said no" (4) so CI scripts
can branch without parsing stderr.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from simkit.measure_bundle import (
    MEASURE_FILE_SUFFIX,
    MeasureBundle,
    MeasureBundleError,
    load_measure_bundle,
    resolve_measurements_dir,
    resolve_signal_groups_dir,
    resolve_templates_dir,
)
from simkit.project import (
    PvtProject,
    PvtProjectError,
    load_pvtproject,
)
from simkit.signal_group import (
    SIGNAL_GROUP_FILE_SUFFIX,
    SignalGroupError,
    load_signal_group,
)
from simkit.template import (
    TEMPLATE_FILE_SUFFIX,
    Template,
    TemplateError,
    load_template,
)
from simkit.template_paste import paste_to_template
from simkit.template_render import RenderError, RenderedRow, render_bundle


_RENDERED_SCHEMA_VERSION = 1

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BUILTINS_DIR = _REPO_ROOT / "config" / "builtins"


# --------------------------------------------------------------------------
# Argparse registration
# --------------------------------------------------------------------------


def add_subparser(sub) -> None:
    p = sub.add_parser(
        "measure",
        help=(
            "Formula-template authoring helpers "
            "(new-template / list-templates / show-template / "
            "new-signal-group / list-signal-groups / new-bundle / "
            "list-bundles / render / apply / pull / diff / restore)."
        ),
        description=(
            "Phase 3B measurement-bundle CLI. Offline verbs: new-template, "
            "list-templates, show-template, new-signal-group, "
            "list-signal-groups, new-bundle, list-bundles, render, diff. "
            "Live-Maestro verbs (via skillbridge): apply, pull, restore."
        ),
    )
    cs = p.add_subparsers(dest="measure_cmd", required=True)

    # --- new-template ----------------------------------------------------
    p_nt = cs.add_parser(
        "new-template",
        help="Paste-import a concrete expression to a .template.json sidecar.",
    )
    p_nt.add_argument("name", help="Template name (^[a-z][a-z0-9_]*$).")
    p_nt.add_argument(
        "--from-expr", required=True, dest="from_expr",
        help="Concrete Cadence expression to import.",
    )
    p_nt.add_argument(
        "--interactive", action="store_true",
        help=(
            "Prompt (y/N) for each numeric literal; default is non-interactive "
            "(numerics retained as literals)."
        ),
    )
    p_nt.add_argument(
        "--out", default=None,
        help=(
            "Output .template.json path. Default: "
            "<templatesDir>/<name>.template.json under the resolved project."
        ),
    )
    p_nt.add_argument(
        "--short-alias", default=None,
        help="Override short_alias (default: auto-derived from the leading function).",
    )
    p_nt.add_argument(
        "--project", default=None,
        help="Path to a .pvtproject. Default: PVT_PROJECT env or cwd walker.",
    )
    p_nt.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing template file with the same name.",
    )
    p_nt.set_defaults(func=_run_new_template)

    # --- list-templates --------------------------------------------------
    p_lt = cs.add_parser(
        "list-templates",
        help="List templates configured under <templatesDir>/.",
    )
    p_lt.add_argument("--project", default=None)
    p_lt.add_argument(
        "--json", dest="as_json", action="store_true",
        help="Emit a JSON array instead of the default table.",
    )
    p_lt.set_defaults(func=_run_list_templates)

    # --- show-template ---------------------------------------------------
    p_st = cs.add_parser(
        "show-template",
        help="Pretty-print a template's body.",
    )
    p_st.add_argument("name", help="Template name (no .template.json suffix).")
    p_st.add_argument("--project", default=None)
    p_st.set_defaults(func=_run_show_template)

    # --- install-builtins ------------------------------------------------
    p_ib = cs.add_parser(
        "install-builtins",
        help=(
            "Copy the shipped builtin templates "
            "(config/builtins/*.template.json) into the active project's "
            "templatesDir/. Refuses to overwrite on name collision unless --force."
        ),
    )
    p_ib.add_argument("--project", default=None)
    p_ib.add_argument(
        "--force", action="store_true",
        help="Overwrite existing templates with matching names.",
    )
    p_ib.add_argument(
        "--list", dest="list_only", action="store_true",
        help="Dry run: print what would be installed; copy nothing.",
    )
    p_ib.add_argument(
        "--names", default=None,
        help=(
            "Comma-delimited subset of builtin template names to install. "
            "Default: all builtins."
        ),
    )
    p_ib.set_defaults(func=_run_install_builtins)

    # --- new-signal-group ------------------------------------------------
    p_nsg = cs.add_parser(
        "new-signal-group",
        help="Create a .siggroup.json sidecar from a comma-delimited path list.",
    )
    p_nsg.add_argument("name", help="Signal group name (^[a-z][a-z0-9_]*$).")
    p_nsg.add_argument(
        "--signals", required=True,
        help="Comma-delimited signal paths; each must start with '/'.",
    )
    p_nsg.add_argument("--out", default=None)
    p_nsg.add_argument("--project", default=None)
    p_nsg.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing signal-group file with the same name.",
    )
    p_nsg.set_defaults(func=_run_new_signal_group)

    # --- list-signal-groups ----------------------------------------------
    p_lsg = cs.add_parser(
        "list-signal-groups",
        help="List signal groups configured under <signalGroupsDir>/.",
    )
    p_lsg.add_argument("--project", default=None)
    p_lsg.add_argument(
        "--json", dest="as_json", action="store_true",
    )
    p_lsg.set_defaults(func=_run_list_signal_groups)

    # --- new-bundle ------------------------------------------------------
    p_nb = cs.add_parser(
        "new-bundle",
        help="Scaffold a .measure.json bundle.",
    )
    p_nb.add_argument("name", help="Bundle name (^[a-z][a-z0-9_]*$).")
    p_nb.add_argument(
        "--templates", required=True,
        help="Comma-delimited template names.",
    )
    p_nb.add_argument(
        "--signal-group", default=None,
        help="Signal group name (omit when no template has a signal param).",
    )
    p_nb.add_argument("--test", required=True, dest="test_name", help="Maestro test name.")
    p_nb.add_argument(
        "--testbench-id", default=None, dest="testbench_id",
        help=(
            "Testbench id (lib/cell/view). Default: resolved via skillbridge "
            "from the live Maestro session (requires --session)."
        ),
    )
    p_nb.add_argument(
        "--session", default=None,
        help="Maestro session id when resolving testbench_id from live session.",
    )
    p_nb.add_argument("--out", default=None)
    p_nb.add_argument("--project", default=None)
    p_nb.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing bundle file with the same name.",
    )
    p_nb.set_defaults(func=_run_new_bundle)

    # --- list-bundles ----------------------------------------------------
    p_lb = cs.add_parser(
        "list-bundles",
        help="List bundles configured under <measurementsDir>/.",
    )
    p_lb.add_argument("--project", default=None)
    p_lb.add_argument("--json", dest="as_json", action="store_true")
    p_lb.set_defaults(func=_run_list_bundles)

    # --- render ----------------------------------------------------------
    p_r = cs.add_parser(
        "render",
        help="Offline render of a bundle to a flat row table.",
    )
    p_r.add_argument("bundle", help="Path to a .measure.json sidecar.")
    p_r.add_argument(
        "--out", default=None,
        help="Output CSV path. Default: <bundle>.rendered.csv next to the sidecar.",
    )
    p_r.add_argument(
        "--json", dest="as_json", action="store_true",
        help="Emit a JSON array of rendered rows instead of the default table.",
    )
    p_r.add_argument("--project", default=None)
    p_r.set_defaults(func=_run_render)

    # --- apply -----------------------------------------------------------
    p_a = cs.add_parser(
        "apply",
        help="Render a bundle and push it to the live Maestro session.",
    )
    p_a.add_argument("bundle")
    p_a.add_argument("--session", default=None)
    p_a.add_argument("--dry-run", dest="dry_run", action="store_true")
    p_a.add_argument(
        "--replace", action="store_true",
        help="Delete each named output first (default: additive in-place update).",
    )
    p_a.add_argument("--project", default=None)
    p_a.set_defaults(func=_run_apply)

    # --- pull ------------------------------------------------------------
    p_pull = cs.add_parser(
        "pull",
        help="Pull the live Outputs table to a .snapshot.json sidecar.",
    )
    p_pull.add_argument("out_path", help="Output path (must end .snapshot.json).")
    p_pull.add_argument("--session", default=None)
    p_pull.add_argument("--test", default="Test", dest="test_name")
    p_pull.add_argument(
        "--include-signals", dest="include_signals", action="store_true",
        help="Also include Type=net (signal-tap) rows.",
    )
    p_pull.add_argument("--project", default=None)
    p_pull.set_defaults(func=_run_pull)

    # --- diff ------------------------------------------------------------
    p_d = cs.add_parser(
        "diff",
        help="Diff two .measure.json bundles, two .snapshot.json files, or mixed.",
    )
    p_d.add_argument("a")
    p_d.add_argument("b")
    p_d.add_argument("--project", default=None)
    p_d.add_argument(
        "--json", dest="as_json", action="store_true",
    )
    p_d.set_defaults(func=_run_diff)

    # --- restore ---------------------------------------------------------
    p_rs = cs.add_parser(
        "restore",
        help="Re-import a snapshot CSV via axlOutputsImportFromFile overwrite.",
    )
    p_rs.add_argument(
        "snapshot",
        help=(
            "Path to a snapshot file. May be either the CSV captured by "
            "axlOutputsExportToFile, or a .snapshot.json from `pvt measure "
            "pull` (we re-emit the embedded rows to a temp CSV before import)."
        ),
    )
    p_rs.add_argument("--session", default=None)
    p_rs.add_argument(
        "--operation", default="merge",
        choices=("overwrite", "merge", "retain"),
    )
    p_rs.add_argument("--test", default=None, dest="test_name")
    p_rs.add_argument("--project", default=None)
    p_rs.set_defaults(func=_run_restore)


# --------------------------------------------------------------------------
# Common helpers
# --------------------------------------------------------------------------


def _load_project(args, cmd: str) -> Optional[PvtProject]:
    try:
        if args.project is not None:
            from simkit.project import _parse_pvtproject  # type: ignore[attr-defined]
            path = Path(args.project).expanduser().resolve()
            if not path.is_file():
                print(
                    f"pvt measure {cmd}: .pvtproject not found: {path}",
                    file=sys.stderr,
                )
                return None
            return _parse_pvtproject(path)
        return load_pvtproject()
    except PvtProjectError as exc:
        print(f"pvt measure {cmd}: {exc}", file=sys.stderr)
        return None


def _split_csv(s: str) -> list[str]:
    return [tok.strip() for tok in s.split(",") if tok.strip()]


def _write_json_file(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# new-template
# --------------------------------------------------------------------------


def _run_new_template(args) -> int:
    project = _load_project(args, "new-template")
    if project is None:
        return 3

    templates_dir = resolve_templates_dir(project)

    if args.out is not None:
        out_path = Path(args.out).expanduser().resolve()
    else:
        out_path = templates_dir / f"{args.name}{TEMPLATE_FILE_SUFFIX}"

    if not out_path.name.endswith(TEMPLATE_FILE_SUFFIX):
        print(
            f"pvt measure new-template: out path must end "
            f"'{TEMPLATE_FILE_SUFFIX}' (got {out_path.name!r})",
            file=sys.stderr,
        )
        return 2

    if out_path.exists() and not args.force:
        print(
            f"pvt measure new-template: refusing to overwrite {out_path} "
            f"(use --force).",
            file=sys.stderr,
        )
        return 2

    prompt_cb = _interactive_prompt if args.interactive else None
    try:
        template = paste_to_template(
            args.from_expr,
            name=args.name,
            short_alias=args.short_alias,
            prompt=prompt_cb,
        )
    except ValueError as exc:
        print(f"pvt measure new-template: {exc}", file=sys.stderr)
        return 2

    _write_json_file(out_path, _template_to_dict(template))

    print(f"wrote -> {out_path}")
    print(
        f"  short_alias={template.short_alias}  "
        f"params={len(template.params)}  "
        f"eval_type={template.eval_type}"
    )
    return 0


def _interactive_prompt(message: str) -> bool:
    """``input()``-backed prompt callback for ``--interactive``.

    Treats Y/y as accept, anything else (incl. blank / Ctrl-D / EOFError)
    as reject. Mirrors the CLI convention used by Phase 1 verbs.
    """
    try:
        answer = input(f"{message} ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


def _template_to_dict(t: Template) -> dict:
    out: dict = {
        "template_schema_version": t.template_schema_version,
        "name": t.name,
        "short_alias": t.short_alias,
        "expression": t.expression,
        "params": [
            {
                k: v for k, v in (
                    ("key", p.key),
                    ("kind", p.kind),
                    ("default", p.default),
                    ("doc", p.doc),
                ) if v is not None
            }
            for p in t.params
        ],
        "eval_type": t.eval_type,
        "plot": t.plot,
        "save": t.save,
    }
    if t.unit is not None:
        out["unit"] = t.unit
    if t.pasted_from is not None:
        out["_pasted_from"] = t.pasted_from
    return out


# --------------------------------------------------------------------------
# list-templates / show-template
# --------------------------------------------------------------------------


def _run_list_templates(args) -> int:
    project = _load_project(args, "list-templates")
    if project is None:
        return 3
    templates_dir = resolve_templates_dir(project)
    listings = _list_sidecars(templates_dir, TEMPLATE_FILE_SUFFIX, _summarise_template)
    if args.as_json:
        print(json.dumps(listings, indent=2))
    else:
        _print_table(
            templates_dir, listings,
            cols=("name", "short_alias", "params", "eval_type", "status"),
            widths=(24, 14, 8, 10, 30),
            headers=("NAME", "ALIAS", "PARAMS", "EVAL", "STATUS"),
            tag=".template.json",
        )
    return 0


def _summarise_template(path: Path) -> dict:
    name = path.name[: -len(TEMPLATE_FILE_SUFFIX)]
    try:
        t = load_template(path)
    except TemplateError as exc:
        return {
            "name": name, "path": str(path),
            "short_alias": None, "params": None,
            "eval_type": None, "status": str(exc),
        }
    return {
        "name": t.name, "path": str(path),
        "short_alias": t.short_alias,
        "params": len(t.params),
        "eval_type": t.eval_type,
        "status": "OK",
    }


def _run_show_template(args) -> int:
    project = _load_project(args, "show-template")
    if project is None:
        return 3
    path = resolve_templates_dir(project) / f"{args.name}{TEMPLATE_FILE_SUFFIX}"
    if not path.is_file():
        print(
            f"pvt measure show-template: template not found: {path}",
            file=sys.stderr,
        )
        return 2
    try:
        t = load_template(path)
    except TemplateError as exc:
        print(f"pvt measure show-template: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(_template_to_dict(t), indent=2))
    return 0


# --------------------------------------------------------------------------
# install-builtins
# --------------------------------------------------------------------------


def _run_install_builtins(args) -> int:
    if not _BUILTINS_DIR.is_dir():
        print(
            f"pvt measure install-builtins: builtins directory not found: "
            f"{_BUILTINS_DIR}",
            file=sys.stderr,
        )
        return 3

    available = sorted(_BUILTINS_DIR.glob(f"*{TEMPLATE_FILE_SUFFIX}"))
    if not available:
        print(
            f"pvt measure install-builtins: no templates in {_BUILTINS_DIR}",
            file=sys.stderr,
        )
        return 3

    if args.names:
        wanted = set(_split_csv(args.names))
        unknown = wanted - {
            p.name[: -len(TEMPLATE_FILE_SUFFIX)] for p in available
        }
        if unknown:
            print(
                f"pvt measure install-builtins: unknown builtin name(s): "
                f"{', '.join(sorted(unknown))}",
                file=sys.stderr,
            )
            return 2
        selected = [
            p for p in available
            if p.name[: -len(TEMPLATE_FILE_SUFFIX)] in wanted
        ]
    else:
        selected = available

    for src in selected:
        try:
            load_template(src)
        except TemplateError as exc:
            print(
                f"pvt measure install-builtins: builtin {src.name} failed "
                f"to load — refusing to install: {exc}",
                file=sys.stderr,
            )
            return 2

    project = _load_project(args, "install-builtins")
    if project is None:
        return 3
    dest_dir = resolve_templates_dir(project)

    plan: list[tuple[Path, Path, str]] = []
    collisions: list[str] = []
    for src in selected:
        dest = dest_dir / src.name
        if dest.exists():
            if args.force:
                plan.append((src, dest, "overwrite"))
            else:
                plan.append((src, dest, "skip"))
                collisions.append(src.name[: -len(TEMPLATE_FILE_SUFFIX)])
        else:
            plan.append((src, dest, "install"))

    if args.list_only:
        print(f"# templatesDir: {dest_dir}")
        for src, dest, action in plan:
            name = src.name[: -len(TEMPLATE_FILE_SUFFIX)]
            print(f"{action:9s}  {name}")
        return 0

    if collisions and not args.force:
        print(
            f"pvt measure install-builtins: refusing to overwrite "
            f"{len(collisions)} existing template(s) in {dest_dir}: "
            f"{', '.join(collisions)}",
            file=sys.stderr,
        )
        print(
            "Re-run with --force to overwrite, or --names to install only "
            "non-colliding entries.",
            file=sys.stderr,
        )
        return 2

    dest_dir.mkdir(parents=True, exist_ok=True)
    installed: list[tuple[str, str]] = []
    for src, dest, action in plan:
        if action == "skip":
            continue
        dest.write_bytes(src.read_bytes())
        installed.append((src.name[: -len(TEMPLATE_FILE_SUFFIX)], action))

    print(f"# templatesDir: {dest_dir}")
    for name, action in installed:
        print(f"{action:9s}  {name}")
    print(f"# {len(installed)} of {len(selected)} installed")
    return 0


# --------------------------------------------------------------------------
# new-signal-group / list-signal-groups
# --------------------------------------------------------------------------


def _run_new_signal_group(args) -> int:
    project = _load_project(args, "new-signal-group")
    if project is None:
        return 3

    signals = _split_csv(args.signals)
    if not signals:
        print(
            "pvt measure new-signal-group: --signals must list at least one path",
            file=sys.stderr,
        )
        return 2

    for sig in signals:
        if not sig.startswith("/"):
            print(
                f"pvt measure new-signal-group: signal {sig!r} must start with '/'",
                file=sys.stderr,
            )
            return 2

    if len(set(signals)) != len(signals):
        print(
            "pvt measure new-signal-group: duplicate signal path in --signals",
            file=sys.stderr,
        )
        return 2

    sg_dir = resolve_signal_groups_dir(project)
    if args.out is not None:
        out_path = Path(args.out).expanduser().resolve()
    else:
        out_path = sg_dir / f"{args.name}{SIGNAL_GROUP_FILE_SUFFIX}"

    if not out_path.name.endswith(SIGNAL_GROUP_FILE_SUFFIX):
        print(
            f"pvt measure new-signal-group: out path must end "
            f"'{SIGNAL_GROUP_FILE_SUFFIX}' (got {out_path.name!r})",
            file=sys.stderr,
        )
        return 2

    if out_path.exists() and not args.force:
        print(
            f"pvt measure new-signal-group: refusing to overwrite {out_path} "
            f"(use --force).",
            file=sys.stderr,
        )
        return 2

    doc = {
        "signal_group_schema_version": 1,
        "name": args.name,
        "signals": signals,
    }
    _write_json_file(out_path, doc)

    # Round-trip through the loader to surface validation errors here, not
    # later in `list-signal-groups`.
    try:
        load_signal_group(out_path)
    except SignalGroupError as exc:
        # Clean up — the file we just wrote is invalid.
        try:
            out_path.unlink()
        except OSError:
            pass
        print(f"pvt measure new-signal-group: {exc}", file=sys.stderr)
        return 2

    print(f"wrote -> {out_path}  signals={len(signals)}")
    return 0


def _run_list_signal_groups(args) -> int:
    project = _load_project(args, "list-signal-groups")
    if project is None:
        return 3
    sg_dir = resolve_signal_groups_dir(project)
    listings = _list_sidecars(sg_dir, SIGNAL_GROUP_FILE_SUFFIX, _summarise_signal_group)
    if args.as_json:
        print(json.dumps(listings, indent=2))
    else:
        _print_table(
            sg_dir, listings,
            cols=("name", "signals", "status"),
            widths=(24, 8, 30),
            headers=("NAME", "SIGNALS", "STATUS"),
            tag=".siggroup.json",
        )
    return 0


def _summarise_signal_group(path: Path) -> dict:
    name = path.name[: -len(SIGNAL_GROUP_FILE_SUFFIX)]
    try:
        sg = load_signal_group(path)
    except SignalGroupError as exc:
        return {
            "name": name, "path": str(path),
            "signals": None, "status": str(exc),
        }
    return {
        "name": sg.name, "path": str(path),
        "signals": len(sg.signals), "status": "OK",
    }


# --------------------------------------------------------------------------
# new-bundle / list-bundles
# --------------------------------------------------------------------------


def _run_new_bundle(args) -> int:
    project = _load_project(args, "new-bundle")
    if project is None:
        return 3

    templates = _split_csv(args.templates)
    if not templates:
        print(
            "pvt measure new-bundle: --templates must list at least one template",
            file=sys.stderr,
        )
        return 2

    templates_dir = resolve_templates_dir(project)
    sg_dir = resolve_signal_groups_dir(project)

    # Pre-flight check: templates must exist on disk.
    for t in templates:
        tp = templates_dir / f"{t}{TEMPLATE_FILE_SUFFIX}"
        if not tp.is_file():
            print(
                f"pvt measure new-bundle: template {t!r} not found at {tp}",
                file=sys.stderr,
            )
            return 3

    if args.signal_group is not None:
        sgp = sg_dir / f"{args.signal_group}{SIGNAL_GROUP_FILE_SUFFIX}"
        if not sgp.is_file():
            print(
                f"pvt measure new-bundle: signal-group {args.signal_group!r} "
                f"not found at {sgp}",
                file=sys.stderr,
            )
            return 3

    # Resolve testbench_id: explicit > live session lookup.
    testbench_id: Optional[str]
    if args.testbench_id is not None:
        testbench_id = args.testbench_id
    else:
        try:
            from simkit.skill_bridge import (
                SkillBridgeError,
                resolve_live_testbench_id,
            )
            testbench_id = resolve_live_testbench_id(session=args.session)
        except (RuntimeError, SkillBridgeError) as exc:
            print(
                f"pvt measure new-bundle: --testbench-id not supplied and "
                f"could not resolve from live session: {exc}",
                file=sys.stderr,
            )
            return 3

    measurements_dir = resolve_measurements_dir(project)
    if args.out is not None:
        out_path = Path(args.out).expanduser().resolve()
    else:
        out_path = measurements_dir / f"{args.name}{MEASURE_FILE_SUFFIX}"

    if not out_path.name.endswith(MEASURE_FILE_SUFFIX):
        print(
            f"pvt measure new-bundle: out path must end "
            f"'{MEASURE_FILE_SUFFIX}' (got {out_path.name!r})",
            file=sys.stderr,
        )
        return 2

    if out_path.exists() and not args.force:
        print(
            f"pvt measure new-bundle: refusing to overwrite {out_path} "
            f"(use --force).",
            file=sys.stderr,
        )
        return 2

    # Build apply entries; pair each template with the supplied signal group
    # iff the template declares a signal-kind param.
    apply_entries = []
    for t in templates:
        tobj = load_template(templates_dir / f"{t}{TEMPLATE_FILE_SUFFIX}")
        entry: dict = {"template": t}
        if tobj.signal_param() is not None:
            if args.signal_group is None:
                print(
                    f"pvt measure new-bundle: template {t!r} requires a signal "
                    f"group; pass --signal-group",
                    file=sys.stderr,
                )
                return 2
            entry["signal_group"] = args.signal_group
        else:
            entry["signal_group"] = None
        apply_entries.append(entry)

    doc = {
        "measure_schema_version": 1,
        "name": args.name,
        "project": project.project,
        "testbench_id": testbench_id,
        "test_name": args.test_name,
        "apply": apply_entries,
    }
    _write_json_file(out_path, doc)

    # Round-trip through the loader.
    try:
        load_measure_bundle(out_path, project=project)
    except MeasureBundleError as exc:
        try:
            out_path.unlink()
        except OSError:
            pass
        print(f"pvt measure new-bundle: {exc}", file=sys.stderr)
        return 2

    print(f"wrote -> {out_path}")
    print(
        f"  templates={','.join(templates)}  "
        f"signal_group={args.signal_group or '(none)'}  "
        f"test={args.test_name}  testbench={testbench_id}"
    )
    return 0


def _run_list_bundles(args) -> int:
    project = _load_project(args, "list-bundles")
    if project is None:
        return 3
    measurements_dir = resolve_measurements_dir(project)
    listings = _list_sidecars(
        measurements_dir, MEASURE_FILE_SUFFIX,
        lambda path: _summarise_bundle(path, project),
    )
    if args.as_json:
        print(json.dumps(listings, indent=2))
    else:
        _print_table(
            measurements_dir, listings,
            cols=("name", "test", "apply", "status"),
            widths=(24, 14, 8, 40),
            headers=("NAME", "TEST", "APPLY", "STATUS"),
            tag=".measure.json",
        )
    return 0


def _summarise_bundle(path: Path, project: PvtProject) -> dict:
    name = path.name[: -len(MEASURE_FILE_SUFFIX)]
    try:
        b = load_measure_bundle(path, project=project)
    except MeasureBundleError as exc:
        return {
            "name": name, "path": str(path),
            "test": None, "apply": None,
            "status": f"ERR: {_strip_path_prefix(str(exc), path)}",
        }
    return {
        "name": b.name, "path": str(path),
        "test": b.test_name, "apply": len(b.apply),
        "status": "OK",
    }


def _strip_path_prefix(msg: str, path: Path) -> str:
    """Remove a redundant ``<path>: `` head from an error message.

    The bundle path already appears in the listing's ``path`` column;
    repeating it inside the STATUS cell wastes width and truncates the
    actually-useful suffix.
    """
    prefix = f"{path}: "
    if msg.startswith(prefix):
        return msg[len(prefix):]
    return msg


# --------------------------------------------------------------------------
# Listing/printing helpers
# --------------------------------------------------------------------------


def _list_sidecars(dir_path: Path, suffix: str, summarise) -> list[dict]:
    if not dir_path.is_dir():
        return []
    out: list[dict] = []
    for path in sorted(dir_path.glob(f"*{suffix}")):
        out.append(summarise(path))
    return out


def _print_table(
    dir_path: Path,
    listings: list[dict],
    *,
    cols: tuple[str, ...],
    widths: tuple[int, ...],
    headers: tuple[str, ...],
    tag: str,
) -> None:
    sep = "  "
    print(f"# dir = {dir_path}")
    print(sep.join(h.ljust(w) for h, w in zip(headers, widths)))
    print(sep.join("-" * w for w in widths))
    if not listings:
        print(f"(no {tag} files found)")
        return
    for row in listings:
        cells: list[str] = []
        for k, w in zip(cols, widths):
            v = row.get(k)
            if v is None:
                s = "-"
            else:
                s = str(v)
            cells.append(_trunc(s, w))
        print(sep.join(c.ljust(w) for c, w in zip(cells, widths)))


def _trunc(s: str, width: int) -> str:
    if len(s) <= width:
        return s
    if width <= 1:
        return s[:width]
    return s[: width - 1] + "…"


# --------------------------------------------------------------------------
# render
# --------------------------------------------------------------------------


def _run_render(args) -> int:
    project = _load_project(args, "render")
    if project is None:
        return 3

    bundle_path = Path(args.bundle).expanduser()
    if not bundle_path.is_file():
        print(
            f"pvt measure render: bundle not found: {bundle_path}",
            file=sys.stderr,
        )
        return 2

    try:
        bundle = load_measure_bundle(bundle_path, project=project)
    except MeasureBundleError as exc:
        print(f"pvt measure render: {exc}", file=sys.stderr)
        return 2

    try:
        rendered = render_bundle(bundle)
    except RenderError as exc:
        print(f"pvt measure render: {exc}", file=sys.stderr)
        return 2

    if args.as_json:
        print(json.dumps(
            [_rendered_row_to_dict(r, bundle.test_name) for r in rendered],
            indent=2,
        ))
        return 0

    if args.out is not None:
        out_path = Path(args.out).expanduser().resolve()
    else:
        # <stem>.rendered.csv next to the sidecar.
        stem = bundle_path.name
        if stem.endswith(MEASURE_FILE_SUFFIX):
            stem = stem[: -len(MEASURE_FILE_SUFFIX)]
        else:
            stem = bundle_path.stem
        out_path = bundle_path.parent / f"{stem}.rendered.csv"

    text = _render_to_csv(rendered, bundle.test_name)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    print(f"rendered {len(rendered)} rows -> {out_path}")
    return 0


def _rendered_row_to_dict(row: RenderedRow, test_name: str) -> dict:
    return {
        "test": test_name,
        "output_name": row.output_name,
        "expression": row.expression,
        "eval_type": row.eval_type,
        "plot": row.plot,
        "save": row.save,
    }


def _render_to_csv(rows: list[RenderedRow], test_name: str) -> str:
    """Seven-column CSV — debug / inspection only, NOT consumed by Maestro.

    v1.3 added the ``spec`` column (Cadence-native passthrough string).
    """
    lines = ["test,output_name,expression,eval_type,plot,save,spec"]
    for r in rows:
        lines.append(
            ",".join((
                test_name,
                r.output_name,
                _csv_escape(r.expression),
                r.eval_type,
                "t" if r.plot else "",
                "t" if r.save else "",
                _csv_escape(r.spec or ""),
            ))
        )
    return "\n".join(lines) + "\n"


def _csv_escape(s: str) -> str:
    """Quote-and-escape a CSV cell when it contains commas / quotes / newlines."""
    if any(ch in s for ch in (",", '"', "\n")):
        return '"' + s.replace('"', '""') + '"'
    return s


# --------------------------------------------------------------------------
# apply
# --------------------------------------------------------------------------


def _render_to_envelope(
    bundle: MeasureBundle,
    rendered: list[RenderedRow],
) -> dict:
    """Build the rendered_schema_version=1 JSON envelope that
    ``pvtMeasurePush`` (skill/pvtMeasure.il) consumes."""
    return {
        "rendered_schema_version": _RENDERED_SCHEMA_VERSION,
        "test": bundle.test_name,
        "rows": [
            {
                "output_name": r.output_name,
                "expression": r.expression,
                "eval_type": r.eval_type,
                "plot": r.plot,
                "save": r.save,
                "spec": r.spec or "",
            }
            for r in rendered
        ],
    }


def _run_apply(args) -> int:
    project = _load_project(args, "apply")
    if project is None:
        return 3

    bundle_path = Path(args.bundle).expanduser()
    if not bundle_path.is_file():
        print(
            f"pvt measure apply: bundle not found: {bundle_path}",
            file=sys.stderr,
        )
        return 2

    try:
        bundle = load_measure_bundle(bundle_path, project=project)
    except MeasureBundleError as exc:
        print(f"pvt measure apply: {exc}", file=sys.stderr)
        return 2

    try:
        rendered = render_bundle(bundle)
    except RenderError as exc:
        print(f"pvt measure apply: {exc}", file=sys.stderr)
        return 2

    if not rendered:
        print(
            "pvt measure apply: render produced 0 rows — nothing to push",
            file=sys.stderr,
        )
        return 2

    envelope = _render_to_envelope(bundle, rendered)

    # Write the envelope to a deterministic tmp path so the SKILL side has a
    # real file to read. The pid-suffixed name keeps parallel invocations
    # from stepping on each other.
    tmp_dir = Path(tempfile.gettempdir())
    tmp_path = tmp_dir / f"_pvt_measure_apply_{os.getpid()}.json"
    tmp_path.write_text(json.dumps(envelope, indent=2) + "\n", encoding="utf-8")

    try:
        from simkit.skill_bridge import (
            SkillBridgeError,
            pvt_measure_push,
        )
        try:
            report = pvt_measure_push(
                tmp_path,
                test_name=bundle.test_name,
                dry_run=args.dry_run,
                replace=args.replace,
                session=args.session,
                pvtproject_path=project.source_path,
            )
        except SkillBridgeError as exc:
            # Even on per-row failure SKILL returns pvtErr with the full
            # report in source; print what we know about the offending row.
            print(f"pvt measure apply: {exc}", file=sys.stderr)
            return 4
    finally:
        if not args.dry_run:
            # On dry-run, keep the tmp file so the user can inspect what
            # would have been pushed.
            try:
                tmp_path.unlink()
            except OSError:
                pass

    marker = " (dry-run)" if args.dry_run else ""
    print(
        f"applied{marker}: pushed {report.n_pushed} row(s) "
        f"to test={bundle.test_name!r}"
    )
    # Width-align the output name column so the spec/reason fields line up.
    name_col = max((len(r.name) for r in report.rows), default=0)
    n_spec_ok = 0
    n_spec_failed = 0
    for r in report.rows:
        spec_text = ""
        if r.spec_status is not None:
            spec_text = f"  spec: {r.spec_status}"
            if r.spec_status == "ok":
                n_spec_ok += 1
            else:
                n_spec_failed += 1
        reason_text = f"  -- {r.reason}" if r.reason else ""
        print(
            f"  [{r.status:<14}] {r.name:<{name_col}}{spec_text}{reason_text}"
        )
    # Summary tail when any row carried a spec — surfaces aggregate without
    # forcing the user to grep the per-row table.
    if n_spec_ok or n_spec_failed:
        if n_spec_failed:
            print(
                f"  spec totals: {n_spec_ok} ok, {n_spec_failed} failed "
                f"(spec failure does not abort the batch — see DECISIONS #45)"
            )
        else:
            print(f"  spec totals: {n_spec_ok} ok")
    if args.dry_run:
        print(f"envelope kept at {tmp_path}")
    return 0


# --------------------------------------------------------------------------
# pull
# --------------------------------------------------------------------------


def _run_pull(args) -> int:
    out_path = Path(args.out_path).expanduser()
    if not out_path.name.endswith(".snapshot.json"):
        print(
            f"pvt measure pull: out_path basename must end '.snapshot.json' "
            f"(got {out_path.name!r})",
            file=sys.stderr,
        )
        return 2

    project = _load_project(args, "pull")
    if project is None:
        return 3

    from simkit.skill_bridge import (
        SkillBridgeError,
        pvt_measure_pull,
    )
    try:
        report = pvt_measure_pull(
            out_path.resolve(),
            test_name=args.test_name,
            include_signals=args.include_signals,
            session=args.session,
            pvtproject_path=project.source_path,
        )
    except SkillBridgeError as exc:
        print(f"pvt measure pull: {exc}", file=sys.stderr)
        return 4

    print(f"pulled {report.n_rows} rows -> {report.path or out_path}")
    return 0


# --------------------------------------------------------------------------
# diff
# --------------------------------------------------------------------------


def _classify(path: Path) -> str:
    if path.name.endswith(MEASURE_FILE_SUFFIX):
        return "bundle"
    if path.name.endswith(".snapshot.json"):
        return "snapshot"
    raise ValueError(
        f"unrecognised file type for diff: {path.name} "
        f"(expected .measure.json or .snapshot.json)"
    )


def _read_snapshot_rows(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(
            f"{path}: snapshot top-level must be a JSON object"
        )
    rows = data.get("rows", [])
    if not isinstance(rows, list):
        raise ValueError(f"{path}: 'rows' must be an array")
    return rows


def _bundle_to_rendered_rows(
    path: Path,
    project: PvtProject,
) -> list[dict]:
    bundle = load_measure_bundle(path, project=project)
    rendered = render_bundle(bundle)
    return [
        {
            "name": r.output_name,
            "type": "expr",
            "expression": r.expression,
            "plot": r.plot,
            "save": r.save,
            "spec": "",
        }
        for r in rendered
    ]


def _bundle_apply_entries(path: Path) -> list[dict]:
    """Read the raw ``apply`` array from a bundle file (no project required).

    Used by bundle-vs-bundle diff: comparison happens at the authoring
    level (which templates, which signal groups, which overrides) rather
    than at the rendered-expression level. This way two bundles can be
    diffed without resolving the templates/signal groups they reference.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level must be a JSON object")
    apply = data.get("apply", [])
    if not isinstance(apply, list):
        raise ValueError(f"{path}: 'apply' must be an array")
    return apply


def _run_diff(args) -> int:
    pa = Path(args.a).expanduser()
    pb = Path(args.b).expanduser()
    if not pa.is_file():
        print(f"pvt measure diff: not found: {pa}", file=sys.stderr)
        return 2
    if not pb.is_file():
        print(f"pvt measure diff: not found: {pb}", file=sys.stderr)
        return 2
    try:
        ta = _classify(pa)
        tb = _classify(pb)
    except ValueError as exc:
        print(f"pvt measure diff: {exc}", file=sys.stderr)
        return 2

    # Bundle-vs-bundle: diff apply entries by (template, signal_group,
    # alias_suffix). Avoids dragging a project context in.
    if ta == "bundle" and tb == "bundle":
        try:
            ea = _bundle_apply_entries(pa)
            eb = _bundle_apply_entries(pb)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"pvt measure diff: {exc}", file=sys.stderr)
            return 2
        return _emit_bundle_diff(ea, eb, args.as_json, pa, pb)

    # snapshot-vs-snapshot
    if ta == "snapshot" and tb == "snapshot":
        try:
            ra = _read_snapshot_rows(pa)
            rb = _read_snapshot_rows(pb)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"pvt measure diff: {exc}", file=sys.stderr)
            return 2
        return _emit_row_diff(ra, rb, args.as_json, pa, pb)

    # Mixed: render the bundle side, then row-diff against the snapshot.
    project = _load_project(args, "diff")
    if project is None:
        return 3

    try:
        if ta == "bundle":
            rendered_a = _bundle_to_rendered_rows(pa, project)
            rendered_b = _read_snapshot_rows(pb)
        else:
            rendered_a = _read_snapshot_rows(pa)
            rendered_b = _bundle_to_rendered_rows(pb, project)
    except (MeasureBundleError, RenderError, OSError, ValueError,
            json.JSONDecodeError) as exc:
        print(f"pvt measure diff: {exc}", file=sys.stderr)
        return 2

    return _emit_row_diff(rendered_a, rendered_b, args.as_json, pa, pb)


def _emit_bundle_diff(
    a_entries: list[dict],
    b_entries: list[dict],
    as_json: bool,
    pa: Path,
    pb: Path,
) -> int:
    def key(entry: dict) -> str:
        tmpl = entry.get("template", "")
        sg = entry.get("signal_group") or ""
        sfx = entry.get("alias_suffix") or ""
        return f"{tmpl}::{sg}::{sfx}"

    a_map = {key(e): e for e in a_entries}
    b_map = {key(e): e for e in b_entries}

    added = sorted(k for k in b_map if k not in a_map)
    removed = sorted(k for k in a_map if k not in b_map)
    changed: list[dict] = []
    identical = 0
    for k in sorted(a_map.keys() & b_map.keys()):
        if a_map[k] != b_map[k]:
            changed.append({"key": k, "a": a_map[k], "b": b_map[k]})
        else:
            identical += 1

    if as_json:
        print(json.dumps({
            "added": added, "removed": removed, "changed": changed,
            "identical_count": identical, "kind": "bundle",
        }, indent=2))
    else:
        print(f"--- {pa} (bundle)")
        print(f"+++ {pb} (bundle)")
        for k in added:
            print(f"+ {k}")
        for k in removed:
            print(f"- {k}")
        for entry in changed:
            print(f"~ {entry['key']}")
        print(f"# identical: {identical}")
    return 1 if (added or removed or changed) else 0


def _emit_row_diff(
    a_rows: list[dict],
    b_rows: list[dict],
    as_json: bool,
    pa: Path,
    pb: Path,
) -> int:
    a_map = {r.get("name", r.get("output_name", "")): r for r in a_rows}
    b_map = {r.get("name", r.get("output_name", "")): r for r in b_rows}
    a_map.pop("", None)
    b_map.pop("", None)

    added = sorted(k for k in b_map if k not in a_map)
    removed = sorted(k for k in a_map if k not in b_map)
    changed: list[dict] = []
    identical = 0
    for k in sorted(a_map.keys() & b_map.keys()):
        ra = a_map[k]
        rb = b_map[k]
        # Only compare the fields that survive a snapshot round-trip.
        field_diffs = {}
        for fld in ("expression", "plot", "save", "type"):
            if ra.get(fld) != rb.get(fld):
                field_diffs[fld] = {"a": ra.get(fld), "b": rb.get(fld)}
        if field_diffs:
            changed.append({"name": k, "fields": field_diffs})
        else:
            identical += 1

    if as_json:
        print(json.dumps({
            "added": added, "removed": removed, "changed": changed,
            "identical_count": identical, "kind": "rows",
        }, indent=2))
    else:
        print(f"--- {pa}")
        print(f"+++ {pb}")
        for k in added:
            print(f"+ {k}")
        for k in removed:
            print(f"- {k}")
        for entry in changed:
            for fname, vals in entry["fields"].items():
                print(
                    f"~ {entry['name']}.{fname}  "
                    f"{json.dumps(vals['a'])} -> {json.dumps(vals['b'])}"
                )
        print(f"# identical: {identical}")
    return 1 if (added or removed or changed) else 0


# --------------------------------------------------------------------------
# restore
# --------------------------------------------------------------------------


def _snapshot_json_to_csv(snapshot_path: Path) -> Path:
    """Re-emit a ``.snapshot.json``'s rows into a Maestro-compatible CSV.

    Produces a temp CSV with the 7-column header
    ``Test,Name,Type,Output,Plot,Save,Spec`` (matches what
    ``axlOutputsExportToFile`` writes). Used by ``pvt measure restore`` when
    handed the JSON form instead of the raw CSV — keeps the user-facing
    contract symmetric ("you ``pull`` to .snapshot.json, you ``restore`` the
    same file") while letting the SKILL layer rely on the canonical CSV
    format.
    """
    data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{snapshot_path}: top-level must be a JSON object")
    rows = data.get("rows", [])
    test = data.get("test", "Test")
    if not isinstance(rows, list):
        raise ValueError(f"{snapshot_path}: 'rows' must be an array")

    lines = ["Test,Name,Type,Output,Plot,Save,Spec"]
    for r in rows:
        if not isinstance(r, dict):
            continue
        lines.append(",".join((
            test,
            str(r.get("name", "")),
            str(r.get("type", "expr")),
            _csv_escape(str(r.get("expression", ""))),
            "t" if r.get("plot") else "",
            "t" if r.get("save") else "",
            str(r.get("spec", "") or ""),
        )))

    tmp_dir = Path(tempfile.mkdtemp(prefix="pvt_measure_restore_"))
    csv_path = tmp_dir / f"{snapshot_path.stem}.csv"
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return csv_path


def _run_restore(args) -> int:
    snap = Path(args.snapshot).expanduser()
    if not snap.is_file():
        print(
            f"pvt measure restore: snapshot not found: {snap}",
            file=sys.stderr,
        )
        return 2

    # When the input is a .snapshot.json we must rematerialise it as a CSV
    # before handing it to axlOutputsImportFromFile (which only consumes the
    # CSV form). We attempt loading project context if --project was passed,
    # but restore does not strictly need it.
    project = _load_project(args, "restore") if args.project else None
    if args.project is not None and project is None:
        return 3

    cleanup_dir: Optional[Path] = None
    try:
        if snap.name.endswith(".snapshot.json"):
            try:
                csv_path = _snapshot_json_to_csv(snap)
                cleanup_dir = csv_path.parent
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                print(f"pvt measure restore: {exc}", file=sys.stderr)
                return 2
        else:
            csv_path = snap

        from simkit.skill_bridge import (
            SkillBridgeError,
            pvt_measure_restore,
        )
        try:
            pvt_measure_restore(
                csv_path,
                operation=args.operation,
                test_name=args.test_name,
                session=args.session,
                pvtproject_path=(project.source_path if project else None),
            )
        except SkillBridgeError as exc:
            print(f"pvt measure restore: {exc}", file=sys.stderr)
            return 4
    finally:
        if cleanup_dir is not None:
            import shutil
            shutil.rmtree(cleanup_dir, ignore_errors=True)

    print(f"restored snapshot {snap.name} (operation={args.operation})")
    return 0
