"""Versioned partition-manifest files: a generated plan becomes descriptors.

A producer outside this module (for example an opening-data generator) writes
its bounded partition objects through `dotmac-files` and emits ONE manifest
file listing them with their SHA-256 digests.  This module turns that file into
`PartitionDescriptor` values and registers them through the existing
`register_partition_plan`, so leases, checkpoints, claimed-partition settlement
and the `RowApplier` port are reused unchanged.  The manifest carries no domain
vocabulary: it names files, row ranges and digests, never what a row means.

Format `dotmac.imports.partition-manifest`, version 1 (UTF-8 JSON object)::

    {
      "format": "dotmac.imports.partition-manifest",
      "version": 1,
      "source": {"file_id": "<uuid>", "checksum_sha256": "<64 hex>"},
      "partitions": [
        {"ordinal": 0, "start_row": 0, "row_count": 200,
         "file_id": "<uuid>", "byte_size": 4096, "checksum_sha256": "<64 hex>"}
      ],
      "manifest_sha256": "<64 hex>"
    }

`manifest_sha256` is the SHA-256 of the canonical JSON (keys sorted, no
insignificant whitespace, ASCII-escaped, UTF-8) of every other member.  It
detects corruption and partial edits.  It does not authenticate the producer,
because anyone able to edit the file can recompute it: a caller that needs
tamper evidence passes `expected_sha256`, a digest obtained out of band.

Parsing is closed.  An unknown format or version, an unknown member, a duplicate
JSON key, a duplicate partition (ordinal or file id), a non-canonical UUID or
digest, a non-contiguous range, a digest mismatch, or an oversized document is
refused rather than interpreted.  Version 1 never gains members; a new shape is
a new version.

Nothing here opens a database transaction or reads stored bytes, except
`verify_manifest_partitions`, which takes no session so storage I/O never holds
a database lock.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from dotmac_imports.contracts import SourceDocument, SourceMismatch
from dotmac_imports.models import ImportPartition
from dotmac_imports.partitioning import (
    _DIGEST_CHUNK_BYTES,
    PartitionDescriptor,
    PartitionOpener,
    PartitionPlanInvalid,
    _validate_descriptors,
    register_partition_plan,
)

MANIFEST_FORMAT = "dotmac.imports.partition-manifest"
SUPPORTED_MANIFEST_VERSIONS: frozenset[int] = frozenset({1})
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_MANIFEST_PARTITIONS = 100_000

_HEX = frozenset("0123456789abcdef")
_TOP_LEVEL = frozenset({"format", "version", "source", "partitions", "manifest_sha256"})
_SOURCE = frozenset({"file_id", "checksum_sha256"})
_PARTITION = frozenset(
    {"ordinal", "start_row", "row_count", "file_id", "byte_size", "checksum_sha256"}
)


class ManifestError(PartitionPlanInvalid):
    """A partition manifest cannot be used safely."""


class ManifestVersionUnsupported(ManifestError):
    """The manifest declares a format or version this release does not read."""


class ManifestDigestMismatch(ManifestError):
    """A recorded SHA-256 does not match the bytes or document it describes."""


@dataclass(frozen=True, slots=True)
class PartitionManifest:
    """A parsed, internally consistent, immutable partition plan."""

    version: int
    source_file_id: UUID
    source_checksum_sha256: str
    partitions: tuple[PartitionDescriptor, ...]
    manifest_sha256: str

    @property
    def total_rows(self) -> int:
        return sum(item.row_count for item in self.partitions)


def _canonical(document: dict[str, Any]) -> bytes:
    return json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _digest_of(document: dict[str, Any]) -> str:
    body = {key: value for key, value in document.items() if key != "manifest_sha256"}
    return hashlib.sha256(_canonical(body)).hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError("manifest repeats a JSON member")
        result[key] = value
    return result


def _reject_constant(_name: str) -> Any:
    raise ManifestError("manifest contains a non-finite number")


def _uuid(value: object, what: str) -> UUID:
    if isinstance(value, str):
        try:
            parsed = UUID(value)
        except ValueError:
            parsed = None
        if parsed is not None and str(parsed) == value:
            return parsed
    raise ManifestError(f"manifest {what} must be a canonical lowercase UUID")


def _count(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ManifestError(f"manifest {what} must be a non-negative integer")
    return value


def _object(value: object, what: str, allowed: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"manifest {what} must be an object")
    if set(value) != allowed:
        unknown = set(value) - allowed
        raise ManifestError(
            f"manifest {what} has unknown members"
            if unknown
            else f"manifest {what} is missing members"
        )
    return value


def parse_partition_manifest(
    data: bytes, *, expected_sha256: str | None = None
) -> PartitionManifest:
    """Parse and fully verify manifest bytes; never touches storage or a database."""
    if expected_sha256 is not None and not _is_sha256(expected_sha256):
        raise ValueError("expected_sha256 must be a lowercase SHA-256 hex digest")
    if not isinstance(data, bytes | bytearray):
        raise ManifestError("manifest must be bytes")
    if len(data) > MAX_MANIFEST_BYTES:
        raise ManifestError("manifest exceeds the size ceiling")
    try:
        document = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_no_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ManifestError("manifest is not valid UTF-8 JSON") from None
    if not isinstance(document, dict):
        raise ManifestError("manifest must be a JSON object")

    # Identity and version are judged before anything else, so an unknown shape
    # is reported as such and never half-interpreted.
    if document.get("format") != MANIFEST_FORMAT:
        raise ManifestVersionUnsupported("manifest format is not recognised")
    version = document.get("version")
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version not in SUPPORTED_MANIFEST_VERSIONS
    ):
        raise ManifestVersionUnsupported("manifest version is not supported")
    document = _object(document, "document", _TOP_LEVEL)

    recorded = document["manifest_sha256"]
    if not _is_sha256(recorded):
        raise ManifestError("manifest_sha256 must be a lowercase SHA-256")
    if not hmac.compare_digest(recorded, _digest_of(document)):
        raise ManifestDigestMismatch("manifest does not match its own digest")
    if expected_sha256 is not None and not hmac.compare_digest(
        recorded, expected_sha256
    ):
        raise ManifestDigestMismatch("manifest does not match the expected digest")

    source = _object(document["source"], "source", _SOURCE)
    source_file_id = _uuid(source["file_id"], "source file_id")
    if not _is_sha256(source["checksum_sha256"]):
        raise ManifestError("manifest source checksum must be a lowercase SHA-256")

    entries = document["partitions"]
    if not isinstance(entries, list) or not entries:
        raise ManifestError("manifest must list at least one partition")
    if len(entries) > MAX_MANIFEST_PARTITIONS:
        raise ManifestError("manifest lists too many partitions")
    descriptors: list[PartitionDescriptor] = []
    seen_ordinals: set[int] = set()
    seen_files: set[UUID] = set()
    for entry in entries:
        item = _object(entry, "partition", _PARTITION)
        if not _is_sha256(item["checksum_sha256"]):
            raise ManifestError("manifest partition checksum must be lowercase SHA-256")
        ordinal = _count(item["ordinal"], "partition ordinal")
        file_id = _uuid(item["file_id"], "partition file_id")
        if ordinal in seen_ordinals or file_id in seen_files:
            raise ManifestError("manifest lists a partition twice")
        seen_ordinals.add(ordinal)
        seen_files.add(file_id)
        descriptors.append(
            PartitionDescriptor(
                ordinal=ordinal,
                start_row=_count(item["start_row"], "partition start_row"),
                row_count=_count(item["row_count"], "partition row_count"),
                file_id=file_id,
                checksum_sha256=item["checksum_sha256"],
                byte_size=_count(item["byte_size"], "partition byte_size"),
            )
        )
    if [item.ordinal for item in descriptors] != sorted(seen_ordinals):
        raise ManifestError("manifest partitions must be listed in ordinal order")
    ordered = _validate_descriptors(descriptors)
    return PartitionManifest(
        version=version,
        source_file_id=source_file_id,
        source_checksum_sha256=source["checksum_sha256"],
        partitions=ordered,
        manifest_sha256=recorded,
    )


def load_partition_manifest(
    path: str | os.PathLike[str], *, expected_sha256: str | None = None
) -> PartitionManifest:
    """Read a manifest file (bounded) and parse it; the path is never echoed."""
    try:
        with Path(path).open("rb") as stream:
            data = stream.read(MAX_MANIFEST_BYTES + 1)
    except OSError:
        raise ManifestError("manifest file could not be read") from None
    return parse_partition_manifest(data, expected_sha256=expected_sha256)


def render_partition_manifest(
    source: SourceDocument, descriptors: Sequence[PartitionDescriptor]
) -> bytes:
    """Emit the canonical version-1 manifest for a complete, valid plan."""
    ordered = _validate_descriptors(descriptors)
    document: dict[str, Any] = {
        "format": MANIFEST_FORMAT,
        "version": 1,
        "source": {
            "file_id": str(source.file_id),
            "checksum_sha256": source.checksum_sha256,
        },
        "partitions": [
            {
                "ordinal": item.ordinal,
                "start_row": item.start_row,
                "row_count": item.row_count,
                "file_id": str(item.file_id),
                "byte_size": item.byte_size,
                "checksum_sha256": item.checksum_sha256,
            }
            for item in ordered
        ],
    }
    document["manifest_sha256"] = _digest_of(document)
    rendered = _canonical(document)
    parse_partition_manifest(rendered)
    return rendered


def verify_manifest_partitions(
    manifest: PartitionManifest, *, open_partition: PartitionOpener
) -> None:
    """Stream every listed object and prove its size and SHA-256.

    Takes no session: call it before opening the registration transaction.
    """
    for item in manifest.partitions:
        digest = hashlib.sha256()
        size = 0
        try:
            with open_partition(item.file_id) as stream:
                while chunk := stream.read(_DIGEST_CHUNK_BYTES):
                    size += len(chunk)
                    if size > item.byte_size:
                        raise ManifestDigestMismatch(
                            "stored partition exceeds its manifest byte_size"
                        )
                    digest.update(chunk)
        except (OSError, ValueError):
            raise ManifestDigestMismatch(
                "stored partition could not be read for verification"
            ) from None
        if size != item.byte_size or digest.hexdigest() != item.checksum_sha256:
            raise ManifestDigestMismatch(
                "stored partition differs from its manifest descriptor"
            )


def register_manifest_plan(
    db: Session,
    *,
    tenant_id: UUID,
    run_id: UUID,
    source: SourceDocument,
    manifest: PartitionManifest,
) -> tuple[ImportPartition, ...]:
    """Register the manifest's plan on a pending dry run; redelivery is a no-op.

    The manifest must describe exactly this run's source.  Everything else,
    including refusing a different plan for a run that already has one, is the
    existing `register_partition_plan` contract.
    """
    if (
        manifest.source_file_id != source.file_id
        or manifest.source_checksum_sha256 != source.checksum_sha256
    ):
        raise SourceMismatch("partition manifest belongs to another source")
    return register_partition_plan(
        db,
        tenant_id=tenant_id,
        run_id=run_id,
        source=source,
        descriptors=manifest.partitions,
    )


__all__ = [
    "MANIFEST_FORMAT",
    "MAX_MANIFEST_BYTES",
    "MAX_MANIFEST_PARTITIONS",
    "SUPPORTED_MANIFEST_VERSIONS",
    "ManifestDigestMismatch",
    "ManifestError",
    "ManifestVersionUnsupported",
    "PartitionManifest",
    "load_partition_manifest",
    "parse_partition_manifest",
    "register_manifest_plan",
    "render_partition_manifest",
    "verify_manifest_partitions",
]
