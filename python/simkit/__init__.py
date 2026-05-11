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

from simkit.from_db import (
    load_dump_from_db,
)

from simkit.diff import (
    DiffResult,
    DiffRow,
    NetlistDiff,
    compute_diff,
    compute_netlist_diff,
    compute_results_diff,
    resolve_slice,
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
    # from_db
    "load_dump_from_db",
    # diff
    "DiffResult",
    "DiffRow",
    "NetlistDiff",
    "compute_diff",
    "compute_netlist_diff",
    "compute_results_diff",
    "resolve_slice",
    # validate
    "Violation",
    "validate_dump",
    "validate_dump_file",
]
