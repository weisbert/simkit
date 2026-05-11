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
    DuplicateRunError,
    IngestError,
    MalformedDumpError,
    MissingDumpError,
    SchemaVersionError,
    SimkitError,
    ValidationError,
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
    "DuplicateRunError",
    "IngestError",
    "MalformedDumpError",
    "MissingDumpError",
    "SchemaVersionError",
    "SimkitError",
    "ValidationError",
    # db
    "bootstrap",
    "connect",
    "transaction",
    # ingest
    "IngestResult",
    "ingest_dump_dir",
    "ingest_run_json",
    # validate
    "Violation",
    "validate_dump",
    "validate_dump_file",
]
