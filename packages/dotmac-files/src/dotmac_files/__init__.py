"""Public surface for the optional DotMac files module."""

from dotmac_files.contracts import (
    FileError,
    FilePolicy,
    FileState,
    InvalidFileState,
    PreparedFile,
    PreparedFileConflict,
    ProviderMismatch,
    StaleObjectRef,
    StoredFileNotFound,
    StoredObjectRef,
    UnsafeFile,
)
from dotmac_files.manifest import module
from dotmac_files.models import PlatformStoredFile, TenantStoredFile
from dotmac_files.physical import (
    delete_object,
    delete_orphans,
    list_objects,
    observe_object,
    open_object,
    prepare_upload,
)
from dotmac_files.providers import (
    ObjectInfo,
    ObjectMissing,
    ReadableObject,
    StorageBoundaryViolation,
    StorageConflict,
    StorageError,
    StorageProvider,
    StorageUnavailable,
)
from dotmac_files.service import (
    deletion_target,
    download_target,
    finalize_purge,
    find_orphan_keys,
    get_file,
    reconciliation_target,
    record_presence,
    request_deletion,
    stage_file,
)

__version__ = "0.1.0a1"

__all__ = [
    "FileError",
    "FilePolicy",
    "FileState",
    "InvalidFileState",
    "ObjectInfo",
    "ObjectMissing",
    "PreparedFile",
    "PreparedFileConflict",
    "ProviderMismatch",
    "ReadableObject",
    "StorageConflict",
    "StorageBoundaryViolation",
    "StorageError",
    "StorageProvider",
    "StorageUnavailable",
    "StaleObjectRef",
    "PlatformStoredFile",
    "TenantStoredFile",
    "StoredFileNotFound",
    "StoredObjectRef",
    "UnsafeFile",
    "__version__",
    "delete_object",
    "delete_orphans",
    "deletion_target",
    "download_target",
    "finalize_purge",
    "find_orphan_keys",
    "get_file",
    "list_objects",
    "module",
    "observe_object",
    "open_object",
    "prepare_upload",
    "reconciliation_target",
    "record_presence",
    "request_deletion",
    "stage_file",
]
