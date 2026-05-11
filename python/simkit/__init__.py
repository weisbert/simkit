"""Top-level simkit package.

Re-exports the public API from the modules below. Internal helpers stay
private to their own modules.
"""

from simkit.project import (
    ENV_VAR,
    PVTPROJECT_FILENAME,
    PvtProject,
    PvtProjectError,
    PvtProjectNotFoundError,
    PvtProjectValidationError,
    find_pvtproject,
    load_pvtproject,
)

from simkit.errors import (
    AmbiguousSliceError,
    DuplicateArtifactError,
    DuplicateRunError,
    IngestError,
    InvalidArtifactTypeError,
    LabelConflictError,
    MalformedDumpError,
    MissingDumpError,
    RunNotFoundError,
    SchemaVersionError,
    SimkitError,
    SliceNotFoundError,
    ValidationError,
)

from simkit.attach import (
    AttachResult,
    attach_artifact,
)

from simkit.label import (
    LabelResult,
    set_run_label,
)

from simkit.list_runs import (
    RunRow,
    list_runs,
)

from simkit.db import (
    bootstrap,
    connect,
    transaction,
)

from simkit.ingest import (
    IngestResult,
    ingest_dump_dir,
    ingest_run_json,
)

from simkit.validate import (
    Violation,
    validate_dump,
    validate_dump_file,
)


__all__ = [
    # project
    "ENV_VAR",
    "PVTPROJECT_FILENAME",
    "PvtProject",
    "PvtProjectError",
    "PvtProjectNotFoundError",
    "PvtProjectValidationError",
    "find_pvtproject",
    "load_pvtproject",
    # errors
    "AmbiguousSliceError",
    "DuplicateArtifactError",
    "DuplicateRunError",
    "IngestError",
    "InvalidArtifactTypeError",
    "LabelConflictError",
    "MalformedDumpError",
    "MissingDumpError",
    "RunNotFoundError",
    "SchemaVersionError",
    "SimkitError",
    "SliceNotFoundError",
    "ValidationError",
    # db
    "bootstrap",
    "connect",
    "transaction",
    # ingest
    "IngestResult",
    "ingest_dump_dir",
    "ingest_run_json",
    # attach
    "AttachResult",
    "attach_artifact",
    # label
    "LabelResult",
    "set_run_label",
    # list
    "RunRow",
    "list_runs",
    # validate
    "Violation",
    "validate_dump",
    "validate_dump_file",
]
