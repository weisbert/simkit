"""`.review.json` sidecar loader and validator.

Implements the Phase 3A §1 spec (docs/phase3a_orchestrator_spec.md), DECISIONS
#50-#52. Pure-Python, stdlib-only. Loads the review-suite sidecar; resolves
union / bundle references relative to the review file; computes effective
on_failure for each item via deep-merge (item overrides suite).

A ``Review`` does not actually run anything — that is Phase 3A §5's orchestrator.
This module is the schema gatekeeper and the in-memory shape the orchestrator
iterates.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from simkit.errors import SimkitError


REVIEW_FILE_SUFFIX = ".review.json"

_REVIEW_NAME_RE = re.compile(r"^[a-z0-9_-]+$")
# Item names are surfaced in log lines + report tables. Allow unicode word
# chars (covers CJK item names like "干扰仿真"), digits, dash, underscore,
# whitespace, and a few punctuation marks engineers actually use in setup
# notes (slash, dot, plus, hash, parentheses).
_ITEM_NAME_RE = re.compile(r"^[\w\-\s./+#()]+$", re.UNICODE)
_TEST_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_SUPPORTED_REVIEW_SCHEMA_VERSIONS = frozenset({1, 2})

_VALID_POLICY_VALUES = frozenset({"skip", "halt"})
_KNOWN_ON_FAILURE_KEYS = frozenset({
    "default", "corner_policy", "item_policy", "strategies",
})
_KNOWN_STRATEGY_ENTRY_REQUIRED = frozenset({"name"})

# `ic_from` (schema v2 only, DECISIONS #57): cross-item IC piping. Consumer item
# names an earlier item whose Spectre per-corner IC file is fed into this item's
# analysis as readns / readic. See docs/phase3a_orchestrator_spec.md §2.5.
_VALID_IC_FILE_KINDS = frozenset({"fc", "ic", "dc"})
_VALID_IC_MODES = frozenset({"readns", "readic"})
_KNOWN_IC_FROM_KEYS = frozenset({"item", "file", "mode", "subdir"})
_REQUIRED_IC_FROM_KEYS = frozenset({"item", "file", "mode"})

# Phase 3A v1 ships exactly one built-in strategy name (DECISIONS #52). The
# loader does NOT enforce membership — user-defined strategies are valid by
# design. This list is informational; surfaced via `--list-known-strategies`.
_V1_BUILTIN_STRATEGY_NAMES = ("naive_retry",)


class ReviewError(SimkitError):
    """Base class for `.review.json` loader errors."""


class ReviewSchemaVersionError(ReviewError):
    """A sidecar declared a ``review_schema_version`` the loader does not support."""


class ReviewMalformedError(ReviewError):
    """A sidecar is unreadable / not parseable as JSON / not a JSON object."""


class ReviewValidationError(ReviewError):
    """A sidecar parsed cleanly but failed schema validation per spec §2."""


# ---------------------------------------------------------------------------
# Shape


@dataclass(frozen=True)
class StrategyEntry:
    """One entry in an ``on_failure.strategies`` chain.

    The ``name`` keys the strategy class to instantiate (built-in or user-
    plugin); ``params`` carries any other JSON-object keys verbatim for the
    strategy class to interpret. v1 ships ``naive_retry`` only (DECISIONS #52);
    ``gmin_bump`` and ``trans_pss_ic`` arrive in v1.1.
    """

    name: str
    max_attempts: int = 1
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OnFailurePolicy:
    """Effective failure policy for one item — already merged with suite-level.

    Phase 3A v1 (DECISIONS #51): default is per-corner skip; an item with
    ``item_policy="halt"`` aborts the whole review on any failed corner.
    """

    default: str = "skip"  # "skip" | "halt"
    corner_policy: str = "skip"
    item_policy: str = "skip"
    strategies: tuple[StrategyEntry, ...] = ()


@dataclass(frozen=True)
class IcFromRef:
    """Cross-item IC pointer on a consumer item (schema v2; DECISIONS #57).

    The orchestrator, before each corner of the consumer item, sets the test's
    PSS / HB analysis to read ``spectre.<file>`` from the same corner's result
    directory of the named source item, via ``mode`` = ``readns`` (soft hint)
    or ``readic`` (hard IC). ``item`` references another item in the same
    review by ``name``; the source item must appear earlier in ``items[]``
    and share the same resolved ``union`` path as the consumer.

    ``subdir`` is an optional explicit override of the per-test simulator
    output sub-directory. Empty default = let ``ic_source.resolve_ic_path``
    try the registered candidates (``netlist`` for Spectre, ``psf`` for
    Alps) and pick the first that has the file. Set this when running an
    exotic simulator whose dir name isn't in the registry.
    """

    item: str
    file: str  # "fc" | "ic" | "dc"
    mode: str  # "readns" | "readic"
    subdir: str | None = None


@dataclass(frozen=True)
class ReviewItem:
    """One row of a review — own tests / own union / own bundle / own policy.

    ``union`` and ``bundle`` are stored as absolute resolved paths (relative
    paths in the source JSON are resolved against the review file's parent
    directory).  ``bundle`` is ``None`` when the source declared null or
    omitted the key entirely — meaning "do not touch the Outputs table".
    ``ic_from`` is set only on schema-v2 items that declare cross-item IC
    piping (see ``IcFromRef`` and spec §2.5).
    """

    name: str
    tests: tuple[str, ...]
    union: Path
    bundle: Path | None
    enabled: bool
    on_failure: OnFailurePolicy
    ic_from: IcFromRef | None = None


@dataclass(frozen=True)
class Review:
    review_schema_version: int
    name: str
    project: str
    items: tuple[ReviewItem, ...]
    source_path: Path


# ---------------------------------------------------------------------------
# Loader


def load_review(path: Path | str) -> Review:
    """Load + validate a ``.review.json``; return the typed ``Review``.

    Raises ``ReviewMalformedError`` if the file is unreadable / unparseable,
    ``ReviewSchemaVersionError`` if the version is unsupported, or
    ``ReviewValidationError`` for any schema invariant. Item-level paths are
    resolved relative to the review file's directory; whether those paths
    actually exist on disk is checked by ``validate_paths_exist``, not here
    (so the typed shape is usable in pure-Python tests that don't carry the
    referenced sidecars).
    """
    p = Path(path).expanduser().resolve()

    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise ReviewMalformedError(f"{p}: invalid JSON — {exc}") from exc
    except OSError as exc:
        raise ReviewMalformedError(f"{p}: cannot read — {exc}") from exc

    if not isinstance(data, dict):
        raise ReviewMalformedError(
            f"{p}: top-level must be a JSON object, got {type(data).__name__}"
        )

    schema_version = _validate_schema_version(p, data)
    name = _validate_name(p, data)
    project = _validate_required_str(p, data, "project")

    raw_suite_on_failure = data.get("on_failure")
    suite_on_failure_dict = _validate_on_failure(
        p, raw_suite_on_failure, where="on_failure"
    )

    items = _validate_items(p, data, suite_on_failure_dict, schema_version)
    items = _resolve_ic_from_cross_refs(p, items)

    return Review(
        review_schema_version=schema_version,
        name=name,
        project=project,
        items=items,
        source_path=p,
    )


def _validate_schema_version(path: Path, data: dict) -> int:
    if "review_schema_version" not in data:
        raise ReviewSchemaVersionError(
            f"{path}: missing required field 'review_schema_version'"
        )
    raw = data["review_schema_version"]
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ReviewSchemaVersionError(
            f"{path}: 'review_schema_version' must be an integer"
        )
    if raw not in _SUPPORTED_REVIEW_SCHEMA_VERSIONS:
        raise ReviewSchemaVersionError(
            f"{path}: review_schema_version {raw} not supported "
            f"(supported: {sorted(_SUPPORTED_REVIEW_SCHEMA_VERSIONS)})"
        )
    return raw


def _validate_name(path: Path, data: dict) -> str:
    name = _validate_required_str(path, data, "name")
    if not _REVIEW_NAME_RE.match(name):
        raise ReviewValidationError(
            f"{path}: 'name' {name!r} does not match ^[a-z0-9_-]+$"
        )
    basename = path.name
    if not basename.endswith(REVIEW_FILE_SUFFIX):
        raise ReviewValidationError(
            f"{path}: filename must end with '{REVIEW_FILE_SUFFIX}' "
            f"(got {basename!r})"
        )
    expected = basename[: -len(REVIEW_FILE_SUFFIX)]
    if expected != name:
        raise ReviewValidationError(
            f"{path}: 'name' {name!r} must equal filename basename "
            f"{expected!r}"
        )
    return name


def _validate_required_str(path: Path, data: dict, key: str) -> str:
    if key not in data:
        raise ReviewValidationError(f"{path}: missing required field {key!r}")
    value = data[key]
    if not isinstance(value, str) or value == "":
        raise ReviewValidationError(
            f"{path}: {key!r} must be a non-empty string"
        )
    return value


# ---------------------------------------------------------------------------
# on_failure shape


def _validate_on_failure(
    path: Path, raw: Any, *, where: str
) -> dict[str, Any]:
    """Validate an ``on_failure`` object; return it as a normalised dict (or
    an empty dict if ``raw`` is None / missing). Strategy entries are
    validated for shape; the strategy NAME is NOT looked up against a known
    registry, because user-plugins are valid (DECISIONS #52)."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ReviewValidationError(
            f"{path}: {where!r} must be a JSON object, got {type(raw).__name__}"
        )
    unknown = set(raw.keys()) - _KNOWN_ON_FAILURE_KEYS
    if unknown:
        raise ReviewValidationError(
            f"{path}: {where!r} has unknown keys {sorted(unknown)}; "
            f"known: {sorted(_KNOWN_ON_FAILURE_KEYS)}"
        )
    for policy_key in ("default", "corner_policy", "item_policy"):
        if policy_key in raw and raw[policy_key] not in _VALID_POLICY_VALUES:
            raise ReviewValidationError(
                f"{path}: {where}.{policy_key} must be one of "
                f"{sorted(_VALID_POLICY_VALUES)}, got {raw[policy_key]!r}"
            )
    if "strategies" in raw:
        strats = raw["strategies"]
        if not isinstance(strats, list):
            raise ReviewValidationError(
                f"{path}: {where}.strategies must be an array, "
                f"got {type(strats).__name__}"
            )
        for i, entry in enumerate(strats):
            _validate_strategy_entry(path, entry, where=f"{where}.strategies[{i}]")
    return raw


def _validate_strategy_entry(path: Path, raw: Any, *, where: str) -> None:
    if not isinstance(raw, dict):
        raise ReviewValidationError(
            f"{path}: {where} must be a JSON object, got {type(raw).__name__}"
        )
    missing = _KNOWN_STRATEGY_ENTRY_REQUIRED - set(raw.keys())
    if missing:
        raise ReviewValidationError(
            f"{path}: {where} missing required keys {sorted(missing)}"
        )
    if not isinstance(raw["name"], str) or raw["name"] == "":
        raise ReviewValidationError(
            f"{path}: {where}.name must be a non-empty string"
        )
    if "max_attempts" in raw:
        ma = raw["max_attempts"]
        if isinstance(ma, bool) or not isinstance(ma, int) or ma < 1:
            raise ReviewValidationError(
                f"{path}: {where}.max_attempts must be a positive integer, "
                f"got {ma!r}"
            )


def _merge_on_failure(
    suite: dict[str, Any], item: dict[str, Any]
) -> OnFailurePolicy:
    """Deep-merge item on top of suite, then materialise an OnFailurePolicy.

    Rule (DECISIONS #50): object keys merge, arrays (``strategies``) replace
    wholesale. Item keys win on conflict.
    """
    merged = deepcopy(suite)
    for k, v in item.items():
        merged[k] = deepcopy(v)

    default = merged.get("default", "skip")
    corner_policy = merged.get("corner_policy", default)
    item_policy = merged.get("item_policy", default)

    raw_strategies = merged.get("strategies", [])
    strategies = tuple(
        _strategy_entry_from_dict(s) for s in raw_strategies
    )
    return OnFailurePolicy(
        default=default,
        corner_policy=corner_policy,
        item_policy=item_policy,
        strategies=strategies,
    )


def _strategy_entry_from_dict(raw: dict[str, Any]) -> StrategyEntry:
    name = raw["name"]
    max_attempts = int(raw.get("max_attempts", 1))
    params = {
        k: v
        for k, v in raw.items()
        if k not in {"name", "max_attempts"}
    }
    return StrategyEntry(name=name, max_attempts=max_attempts, params=params)


# ---------------------------------------------------------------------------
# Items


def _validate_items(
    path: Path, data: dict, suite_on_failure: dict[str, Any], schema_version: int,
) -> tuple[ReviewItem, ...]:
    if "items" not in data:
        raise ReviewValidationError(f"{path}: missing required field 'items'")
    raw = data["items"]
    if not isinstance(raw, list):
        raise ReviewValidationError(f"{path}: 'items' must be a JSON array")
    if len(raw) == 0:
        raise ReviewValidationError(f"{path}: 'items' must be non-empty")

    review_dir = path.parent
    seen_names: set[str] = set()
    out: list[ReviewItem] = []
    for i, raw_item in enumerate(raw):
        item = _validate_item(
            path, i, raw_item, review_dir, suite_on_failure, schema_version,
        )
        if item.name in seen_names:
            raise ReviewValidationError(
                f"{path}: items[{i}] duplicates name {item.name!r} "
                f"(item names must be unique within a review)"
            )
        seen_names.add(item.name)
        out.append(item)
    return tuple(out)


def _validate_item(
    path: Path,
    idx: int,
    raw: Any,
    review_dir: Path,
    suite_on_failure: dict[str, Any],
    schema_version: int,
) -> ReviewItem:
    where = f"items[{idx}]"
    if not isinstance(raw, dict):
        raise ReviewValidationError(
            f"{path}: {where} must be a JSON object, got {type(raw).__name__}"
        )

    # name
    if "name" not in raw:
        raise ReviewValidationError(
            f"{path}: {where} missing required field 'name'"
        )
    name = raw["name"]
    if not isinstance(name, str) or name == "":
        raise ReviewValidationError(
            f"{path}: {where}.name must be a non-empty string"
        )
    if not _ITEM_NAME_RE.match(name):
        raise ReviewValidationError(
            f"{path}: {where}.name {name!r} contains disallowed characters "
            f"(allowed: word chars, dash, underscore, whitespace, ./+#())"
        )

    # tests
    if "tests" not in raw:
        raise ReviewValidationError(
            f"{path}: {where} missing required field 'tests'"
        )
    raw_tests = raw["tests"]
    if not isinstance(raw_tests, list) or len(raw_tests) == 0:
        raise ReviewValidationError(
            f"{path}: {where}.tests must be a non-empty JSON array"
        )
    tests: list[str] = []
    for j, t in enumerate(raw_tests):
        if not isinstance(t, str) or t == "":
            raise ReviewValidationError(
                f"{path}: {where}.tests[{j}] must be a non-empty string, got {t!r}"
            )
        if not _TEST_NAME_RE.match(t):
            raise ReviewValidationError(
                f"{path}: {where}.tests[{j}] {t!r} does not match "
                f"^[A-Za-z_][A-Za-z0-9_]*$"
            )
        tests.append(t)
    # uniqueness within the item
    if len(set(tests)) != len(tests):
        raise ReviewValidationError(
            f"{path}: {where}.tests has duplicate entries: {tests}"
        )

    # union
    if "union" not in raw:
        raise ReviewValidationError(
            f"{path}: {where} missing required field 'union'"
        )
    raw_union = raw["union"]
    if not isinstance(raw_union, str) or raw_union == "":
        raise ReviewValidationError(
            f"{path}: {where}.union must be a non-empty string (path)"
        )
    union_path = (review_dir / raw_union).resolve()

    # bundle (optional, may be null)
    raw_bundle = raw.get("bundle")
    if raw_bundle is None:
        bundle_path: Path | None = None
    elif isinstance(raw_bundle, str) and raw_bundle:
        bundle_path = (review_dir / raw_bundle).resolve()
    else:
        raise ReviewValidationError(
            f"{path}: {where}.bundle must be a non-empty string or null, "
            f"got {raw_bundle!r}"
        )

    # enabled
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ReviewValidationError(
            f"{path}: {where}.enabled must be true / false / omitted, "
            f"got {enabled!r}"
        )

    # on_failure (item-level)
    raw_item_on_failure = raw.get("on_failure")
    item_on_failure_dict = _validate_on_failure(
        path, raw_item_on_failure, where=f"{where}.on_failure"
    )
    effective_policy = _merge_on_failure(suite_on_failure, item_on_failure_dict)

    # ic_from (schema v2 only — rejected on v1 with a "bump to 2" pointer)
    ic_from_ref = _validate_ic_from(
        path, raw.get("ic_from"), where=f"{where}.ic_from",
        schema_version=schema_version,
    )

    # unknown keys
    known_item_keys = {
        "name", "tests", "union", "bundle", "enabled", "on_failure", "ic_from",
    }
    unknown = set(raw.keys()) - known_item_keys
    if unknown:
        raise ReviewValidationError(
            f"{path}: {where} has unknown keys {sorted(unknown)}; "
            f"known: {sorted(known_item_keys)}"
        )

    return ReviewItem(
        name=name,
        tests=tuple(tests),
        union=union_path,
        bundle=bundle_path,
        enabled=enabled,
        on_failure=effective_policy,
        ic_from=ic_from_ref,
    )


def _validate_ic_from(
    path: Path, raw: Any, *, where: str, schema_version: int,
) -> IcFromRef | None:
    """Shape-validate the per-item ``ic_from`` object; cross-refs come later.

    Returns ``None`` if the field is absent or null. Rejects the field
    entirely on schema_version=1 (the orchestrator's per-corner control
    loop only exists in v2; silently ignoring would mean PSS runs cold).
    """
    if raw is None:
        return None
    if schema_version < 2:
        raise ReviewSchemaVersionError(
            f"{path}: {where} requires review_schema_version >= 2 "
            f"(got {schema_version}); bump the version to use ic_from"
        )
    if not isinstance(raw, dict):
        raise ReviewValidationError(
            f"{path}: {where} must be a JSON object, got {type(raw).__name__}"
        )
    missing = _REQUIRED_IC_FROM_KEYS - set(raw.keys())
    if missing:
        raise ReviewValidationError(
            f"{path}: {where} missing required keys {sorted(missing)}"
        )
    unknown = set(raw.keys()) - _KNOWN_IC_FROM_KEYS
    if unknown:
        raise ReviewValidationError(
            f"{path}: {where} has unknown keys {sorted(unknown)}; "
            f"known: {sorted(_KNOWN_IC_FROM_KEYS)}"
        )
    item_ref = raw["item"]
    if not isinstance(item_ref, str) or item_ref == "":
        raise ReviewValidationError(
            f"{path}: {where}.item must be a non-empty string"
        )
    file_kind = raw["file"]
    if file_kind not in _VALID_IC_FILE_KINDS:
        raise ReviewValidationError(
            f"{path}: {where}.file must be one of "
            f"{sorted(_VALID_IC_FILE_KINDS)}, got {file_kind!r}"
        )
    mode = raw["mode"]
    if mode not in _VALID_IC_MODES:
        raise ReviewValidationError(
            f"{path}: {where}.mode must be one of "
            f"{sorted(_VALID_IC_MODES)}, got {mode!r}"
        )
    subdir = raw.get("subdir")
    if subdir is not None and (not isinstance(subdir, str) or subdir == ""):
        raise ReviewValidationError(
            f"{path}: {where}.subdir must be a non-empty string or omitted, "
            f"got {subdir!r}"
        )
    return IcFromRef(item=item_ref, file=file_kind, mode=mode, subdir=subdir)


def _resolve_ic_from_cross_refs(
    path: Path, items: tuple[ReviewItem, ...],
) -> tuple[ReviewItem, ...]:
    """Validate cross-item refs for every item carrying ``ic_from``:

    1. Referenced ``item`` must exist by name in the same review.
    2. Referenced item must appear EARLIER in ``items[]`` than the consumer
       (sequential execution = source must finish before consumer needs IC).
    3. Referenced item must share the same resolved ``union`` path as the
       consumer (per-corner pairing is positional under union explode order;
       different unions = ambiguous mapping, deferred to v2.x).
    4. No self-reference.

    Items are not mutated — only cross-refs are checked; the typed shape
    already carries the validated ``IcFromRef``. Returns the input tuple.
    """
    by_name: dict[str, int] = {it.name: i for i, it in enumerate(items)}
    for i, item in enumerate(items):
        if item.ic_from is None:
            continue
        ref = item.ic_from
        src_name = ref.item
        if src_name == item.name:
            raise ReviewValidationError(
                f"{path}: items[{i}] {item.name!r}.ic_from.item references "
                f"itself; ic_from must point at a different earlier item"
            )
        if src_name not in by_name:
            raise ReviewValidationError(
                f"{path}: items[{i}] {item.name!r}.ic_from.item={src_name!r} "
                f"does not match any item name in this review "
                f"(available: {sorted(by_name)})"
            )
        src_idx = by_name[src_name]
        if src_idx >= i:
            raise ReviewValidationError(
                f"{path}: items[{i}] {item.name!r}.ic_from.item={src_name!r} "
                f"must appear earlier in items[] (source at index {src_idx}, "
                f"consumer at index {i}); reorder items so source runs first"
            )
        src_item = items[src_idx]
        if src_item.union != item.union:
            raise ReviewValidationError(
                f"{path}: items[{i}] {item.name!r}.ic_from cross-corner "
                f"pairing requires source + consumer to share the same union. "
                f"Source {src_name!r} uses {src_item.union}; consumer uses "
                f"{item.union}. (v2 limitation; different-union mapping "
                f"deferred to v2.x.)"
            )
    return items


# ---------------------------------------------------------------------------
# Cross-reference + path-exists checks


@dataclass(frozen=True)
class PathIssue:
    item_name: str
    kind: str  # "union" | "bundle"
    path: Path
    reason: str  # "missing" | "not_a_file" | "wrong_suffix"


def validate_paths_exist(review: Review) -> list[PathIssue]:
    """Return one ``PathIssue`` per referenced sidecar that does not resolve
    on disk or has the wrong suffix. Empty list = all paths OK.

    This is split out from ``load_review`` so the typed shape is usable in
    tests where the referenced sidecars don't exist (e.g. example file in
    config/ that points at hypothetical unions/ and bundles/).
    """
    issues: list[PathIssue] = []
    for item in review.items:
        for kind, p, suffix in (
            ("union", item.union, ".union.json"),
            ("bundle", item.bundle, ".measure.json"),
        ):
            if p is None:
                continue
            if not p.exists():
                issues.append(
                    PathIssue(item.name, kind, p, "missing")
                )
            elif not p.is_file():
                issues.append(
                    PathIssue(item.name, kind, p, "not_a_file")
                )
            elif not str(p).endswith(suffix):
                issues.append(
                    PathIssue(item.name, kind, p, f"wrong_suffix (expected {suffix})")
                )
    return issues


def check_project_match(review: Review, pvtproject_project: str) -> None:
    """Raise if ``review.project`` does not match the enclosing pvtproject.

    Catches misplaced files (e.g. dropping a `foo.review.json` into the wrong
    project's reviews/ dir).
    """
    if review.project != pvtproject_project:
        raise ReviewValidationError(
            f"{review.source_path}: review.project={review.project!r} does not "
            f"match enclosing .pvtproject:project={pvtproject_project!r}"
        )


# ---------------------------------------------------------------------------
# CLI: `python -m simkit.review validate <path>`


def _cli_validate(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser().resolve()
    try:
        review = load_review(path)
    except ReviewError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"OK: review_schema_version={review.review_schema_version} "
          f"name={review.name!r} project={review.project!r} "
          f"items={len(review.items)}")

    for i, item in enumerate(review.items):
        bundle_disp = item.bundle if item.bundle else "(none)"
        strat_names = [s.name for s in item.on_failure.strategies]
        enabled_marker = "" if item.enabled else " [DISABLED]"
        print(f"  [{i}] {item.name!r}{enabled_marker}")
        print(f"      tests:  {list(item.tests)}")
        print(f"      union:  {item.union}")
        print(f"      bundle: {bundle_disp}")
        print(f"      on_failure: corner={item.on_failure.corner_policy} "
              f"item={item.on_failure.item_policy} "
              f"strategies={strat_names}")
        if item.ic_from is not None:
            subdir_suffix = (f" subdir={item.ic_from.subdir!r}"
                             if item.ic_from.subdir else "")
            print(f"      ic_from:    item={item.ic_from.item!r} "
                  f"file={item.ic_from.file} mode={item.ic_from.mode}"
                  f"{subdir_suffix}")

    issues = validate_paths_exist(review)
    if issues:
        print(f"\nPATH CHECK: {len(issues)} issue(s):", file=sys.stderr)
        for it in issues:
            print(f"  {it.item_name}: {it.kind} → {it.path} [{it.reason}]",
                  file=sys.stderr)
        if args.strict_paths:
            return 3
    else:
        print("\nPATH CHECK: all referenced sidecars exist")

    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m simkit.review",
        description="Validate a .review.json sidecar (Phase 3A §1).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_validate = sub.add_parser("validate", help="Validate a .review.json")
    p_validate.add_argument("path", help="Path to .review.json")
    p_validate.add_argument(
        "--strict-paths",
        action="store_true",
        help="Exit non-zero if any referenced union/bundle file is missing",
    )
    p_validate.set_defaults(func=_cli_validate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
