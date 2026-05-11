"""Shared exception hierarchy for simkit.

The base ``SimkitError`` is the umbrella for every simkit-raised exception.
``IngestError`` covers the JSON-dump-to-DuckDB load path; subclasses pin
specific failure modes so callers (CLI, batch tooling) can branch on them.
``ValidationError`` is a subclass of ``IngestError`` so an ``except IngestError``
catcher still matches when the inline validator (see DECISIONS #17) raises.

Mirrors ``project.PvtProjectError`` in style: shallow class hierarchy, no
behaviour beyond ``__init__`` for ``ValidationError`` which carries the
violations list.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List

if TYPE_CHECKING:  # pragma: no cover - typing only
    from simkit.validate import Violation


class SimkitError(Exception):
    """Base class for every simkit-raised exception."""


class IngestError(SimkitError):
    """Base class for ingest-time failures."""


class MalformedDumpError(IngestError):
    """A run.json file is missing required keys, has the wrong type for a
    field, or otherwise fails the shape contract in docs/schema.md §2."""


class SchemaVersionError(IngestError):
    """A run.json declared a ``schema_version`` the ingester does not support."""


class DuplicateRunError(IngestError):
    """A run.json's ``run_id`` is already present in the target DB and the
    caller did not opt into ``replace`` / ``skip`` semantics."""


class MissingDumpError(IngestError):
    """A dump-dir or run.json path was supplied but does not exist or is not
    of an acceptable shape (e.g. directory with neither ``run.json`` nor
    ``runs/*/run.json``)."""


class ValidationError(IngestError):
    """Raised when the inline validator finds at least one ``severity='error'``
    Violation. Subclass of ``IngestError`` so existing ingest-error catchers
    still match (see DECISIONS #17).

    Attributes:
        violations: the full violation list returned by ``validate_dump``,
            including any warnings. Callers may filter by severity.
    """

    def __init__(self, violations: "List[Violation]"):
        self.violations = list(violations)
        errors = [v for v in self.violations if v.severity == "error"]
        first = errors[0] if errors else (self.violations[0] if self.violations else None)
        if first is not None:
            summary = (
                f"{len(errors)} validation error(s) "
                f"(first: {first.code} at {first.path}: {first.message})"
            )
        else:  # pragma: no cover - defensive
            summary = "validation failed"
        super().__init__(summary)


# ---------------------------------------------------------------------------
# Post-hoc operations on already-ingested runs (§5).
# ---------------------------------------------------------------------------


class RunNotFoundError(SimkitError):
    """A ``run_id`` referenced by a §5 CLI verb is not in the ``runs`` table."""


class InvalidArtifactTypeError(SimkitError):
    """``pvt attach`` was given an artifact type outside the schema enum."""


class DuplicateArtifactError(SimkitError):
    """``pvt attach`` would collide with an existing artifact row or file."""


class LabelConflictError(SimkitError):
    """``pvt label`` would overwrite a non-null label without ``--force``."""


class SliceNotFoundError(SimkitError):
    """``pvt diff`` could not resolve a slice argument to a unique run."""


class AmbiguousSliceError(SimkitError):
    """``pvt diff`` slice argument matched more than one run."""
