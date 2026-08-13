"""Pure contracts for file validation and physical lifecycle."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from uuid import UUID

from dotmac_kernel.cache import Scope


class FileError(Exception):
    """Base for refusals made by the files module."""


class UnsafeFile(FileError):
    """The supplied name, size, extension, or content is not permitted."""


class StoredFileNotFound(FileError):
    """No file with this id exists in the requested tenant scope."""


class InvalidFileState(FileError):
    """The requested physical operation is not valid from the current state."""


class ProviderMismatch(FileError):
    """A row was offered to a provider other than the one that stores it."""


class PreparedFileConflict(FileError):
    """A prepared identity was reused with different immutable metadata."""


class StaleObjectRef(FileError):
    """A provider observation no longer describes the stored row."""


class FileState(enum.StrEnum):
    """Repairable physical state; never a domain attachment state."""

    AVAILABLE = "available"
    MISSING = "missing"
    DELETION_PENDING = "deletion_pending"
    PURGED = "purged"


@dataclass(frozen=True, slots=True)
class FilePolicy:
    """A domain-supplied admission policy, not a central domain registry."""

    max_bytes: int
    allowed_extensions: frozenset[str]
    allowed_media_types: frozenset[str]

    def __post_init__(self) -> None:
        if self.max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        if not self.allowed_extensions:
            raise ValueError("allowed_extensions must not be empty")
        if not self.allowed_media_types:
            raise ValueError("allowed_media_types must not be empty")
        if any(
            not item.startswith(".") or item != item.lower()
            for item in self.allowed_extensions
        ):
            raise ValueError("extensions must be lowercase and begin with '.'")
        if any(item != item.lower() for item in self.allowed_media_types):
            raise ValueError("media types must be lowercase")


@dataclass(frozen=True, slots=True)
class PreparedFile:
    """An uploaded object ready to stage in the caller's DB transaction."""

    id: UUID
    scope: Scope
    provider_code: str
    storage_key: str
    original_filename: str
    size_bytes: int
    declared_media_type: str
    detected_media_type: str
    checksum_sha256: str


@dataclass(frozen=True, slots=True)
class StoredObjectRef:
    """Immutable handoff between a DB phase and an external provider phase."""

    id: UUID
    scope: Scope
    provider_code: str
    storage_key: str
    state: FileState
    original_filename: str
    size_bytes: int
    detected_media_type: str
    checksum_sha256: str


__all__ = [
    "FileError",
    "FilePolicy",
    "FileState",
    "InvalidFileState",
    "PreparedFile",
    "PreparedFileConflict",
    "ProviderMismatch",
    "StaleObjectRef",
    "StoredObjectRef",
    "StoredFileNotFound",
    "UnsafeFile",
]
