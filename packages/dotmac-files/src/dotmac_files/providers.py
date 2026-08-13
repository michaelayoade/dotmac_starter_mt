"""Provider seam for object stores; vendor SDKs belong in adapter packages."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import IO, Protocol


class StorageError(Exception):
    """A storage provider could not complete an operation."""


class ObjectMissing(StorageError):
    """The provider has no object at the requested trusted key."""


class StorageUnavailable(StorageError):
    """The provider is temporarily unavailable."""


class StorageConflict(StorageError):
    """A write would replace different bytes at an immutable key."""


class StorageBoundaryViolation(StorageError):
    """A provider returned a key outside the trusted prefix it was given."""


class ReadableObject(Protocol):
    """The minimum readable stream returned by a provider."""

    def read(self, size: int = -1) -> bytes:
        """Read bytes from the object."""
        ...

    def close(self) -> None:
        """Release provider resources."""
        ...


class StorageProvider(Protocol):
    """Provider-neutral operations over immutable trusted keys."""

    code: str

    def put(
        self,
        key: str,
        content: IO[bytes],
        *,
        content_type: str,
        size_bytes: int,
        checksum_sha256: str,
    ) -> None:
        """Write one immutable object, or prove identical replay."""
        ...

    def open(self, key: str) -> ReadableObject:
        """Open an object for streaming."""
        ...

    def exists(self, key: str) -> bool:
        """Whether the exact object exists."""
        ...

    def delete(self, key: str) -> None:
        """Idempotently remove an object."""
        ...

    def list(self, prefix: str) -> Iterable[ObjectInfo]:
        """List objects beneath an already trusted scope prefix."""
        ...


@dataclass(frozen=True, slots=True)
class ObjectInfo:
    """Minimal provider observation used by the orphan reconciler."""

    key: str
    size_bytes: int
    last_modified: datetime


__all__ = [
    "ObjectInfo",
    "ObjectMissing",
    "ReadableObject",
    "StorageConflict",
    "StorageBoundaryViolation",
    "StorageError",
    "StorageProvider",
    "StorageUnavailable",
]
