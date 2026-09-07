"""Observe the identity of one already-selected Foundation wheel.

This module makes an observation of local bytes; it does not select, download,
or authenticate those bytes.  In particular, it accepts no expected digest,
version, source coordinate, metadata, or caller-supplied manifest.  A protected
Control workflow remains the bootstrap trust root: it fetches immutable
coordinates and independently hashes the result before this semantic evidence
can be used.

The semantic manifest is deliberately not an installed-content-integrity claim.
It describes all non-directory members except the selected top-level
``.dist-info/RECORD``.  That RECORD is packaging metadata, not an independently
verified assertion that every file on disk still has those bytes.
"""

from __future__ import annotations

import hashlib
import re
import stat
import unicodedata
import zipfile
from csv import Error as CsvError
from csv import reader as csv_reader
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import strict
from io import BytesIO, StringIO
from pathlib import Path, PurePosixPath
from typing import Final

__all__ = [
    "ARTIFACT_IDENTITY_SCHEMA",
    "ArtifactIdentity",
    "ArtifactIdentityError",
    "observe_artifact_identity",
]

ARTIFACT_IDENTITY_SCHEMA: Final = "ArtifactIdentity.v1"
_SEMANTIC_DOMAIN: Final = b"dotmac.foundation.artifact-semantic-manifest.v1\x00"
_DIST_INFO_NORMALIZE = re.compile(r"[-_.]+")
_CORE_METADATA_VERSION = re.compile(r"^[12]\.\d+$")
_CONCRETE_PATH_TYPE: Final = type(Path())
_SUPPORTED_WHEEL_MAJOR: Final = 1


class ArtifactIdentityError(ValueError):
    """The supplied local path cannot be observed as one wheel."""


@dataclass(frozen=True, slots=True)
class ArtifactIdentity:
    """Immutable facts observed from one wheel's bytes.

    ``wheel_sha256`` identifies these exact archive bytes.  In contrast,
    ``semantic_manifest_sha256`` identifies canonical member names and bytes,
    excluding ``RECORD`` and ZIP packing.  Neither field asserts the integrity
    of a later installed copy.
    """

    schema: str
    size_bytes: int
    distribution_name: str
    distribution_version: str
    wheel_sha256: str
    semantic_manifest_sha256: str


def observe_artifact_identity(wheel_path: Path) -> ArtifactIdentity:
    """Read one local wheel and return facts derived only from its actual bytes."""
    if not isinstance(wheel_path, Path):
        raise ArtifactIdentityError("wheel path must be a pathlib.Path")
    if type(wheel_path) is not _CONCRETE_PATH_TYPE:
        raise ArtifactIdentityError(
            "wheel path must be the concrete platform Path type"
        )
    if not wheel_path.exists():
        raise ArtifactIdentityError(f"wheel path does not exist: {wheel_path}")
    if not wheel_path.is_file():
        raise ArtifactIdentityError(f"wheel path is not a regular file: {wheel_path}")
    if wheel_path.suffix != ".whl":
        raise ArtifactIdentityError(f"wheel path must end in .whl: {wheel_path.name}")

    try:
        snapshot = wheel_path.read_bytes()
        with zipfile.ZipFile(BytesIO(snapshot)) as archive:
            entries = archive.infolist()
            _require_safe_unique_entries(entries)
            metadata_path, wheel_metadata_path, record_path = _require_dist_info_files(
                entries
            )
            metadata = archive.read(metadata_path)
            distribution_name, distribution_version = _distribution_metadata(metadata)
            _require_matching_dist_info(
                metadata_path, distribution_name, distribution_version
            )
            _require_wheel_metadata(archive.read(wheel_metadata_path))
            _require_readable_record(archive.read(record_path))
            semantic_digest = _semantic_manifest_digest(archive, entries, record_path)
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise ArtifactIdentityError(
            f"wheel is not a readable ZIP archive: {wheel_path}"
        ) from error

    return ArtifactIdentity(
        schema=ARTIFACT_IDENTITY_SCHEMA,
        size_bytes=len(snapshot),
        distribution_name=distribution_name,
        distribution_version=distribution_version,
        wheel_sha256=_sha256_bytes(snapshot),
        semantic_manifest_sha256=semantic_digest,
    )


def _require_safe_unique_entries(entries: list[zipfile.ZipInfo]) -> None:
    seen: set[str] = set()
    portable_names: dict[str, str] = {}
    files: set[str] = set()
    directories: set[str] = set()
    for entry in entries:
        name = entry.filename
        raw_parts = name.split("/")
        if entry.is_dir():
            raw_parts = raw_parts[:-1]
        path = PurePosixPath(name)
        if (
            not name
            or "\\" in name
            or path.is_absolute()
            or not raw_parts
            or any(part in {"", ".", ".."} for part in raw_parts)
        ):
            raise ArtifactIdentityError(f"wheel contains unsafe member path: {name!r}")
        if name in seen:
            raise ArtifactIdentityError(f"wheel contains duplicate member: {name!r}")
        seen.add(name)
        portable = unicodedata.normalize("NFC", name).casefold()
        existing = portable_names.setdefault(portable, name)
        if existing != name:
            raise ArtifactIdentityError(
                "wheel contains casefold/NFC-colliding members: "
                f"{existing!r} and {name!r}"
            )
        if stat.S_IFMT(entry.external_attr >> 16) == stat.S_IFLNK:
            raise ArtifactIdentityError(
                f"wheel contains symbolic-link member: {name!r}"
            )
        if entry.is_dir():
            directories.add(_portable_path(name.removesuffix("/")))
        else:
            files.add(_portable_path(name))
            parts = PurePosixPath(name).parts
            directories.update(
                _portable_path("/".join(parts[:index]))
                for index in range(1, len(parts))
            )
    ambiguous = sorted(files & directories)
    if ambiguous:
        raise ArtifactIdentityError(
            "wheel contains file/directory prefix conflict: " f"{ambiguous!r}"
        )


def _require_dist_info_files(entries: list[zipfile.ZipInfo]) -> tuple[str, str, str]:
    dist_info_roots = {
        PurePosixPath(entry.filename).parts[0]
        for entry in entries
        if PurePosixPath(entry.filename).parts[0].endswith(".dist-info")
    }
    if len(dist_info_roots) != 1:
        raise ArtifactIdentityError(
            "wheel must contain exactly one .dist-info directory, got "
            f"{sorted(dist_info_roots)!r}"
        )
    root = next(iter(dist_info_roots))
    required = tuple(f"{root}/{name}" for name in ("METADATA", "WHEEL", "RECORD"))
    for path in required:
        if sum(entry.filename == path for entry in entries) != 1:
            raise ArtifactIdentityError(f"wheel must contain exactly one {path!r}")
    return required


def _distribution_metadata(metadata: bytes) -> tuple[str, str]:
    try:
        message = BytesParser(policy=strict).parsebytes(metadata)
    except (UnicodeDecodeError, ValueError) as error:
        raise ArtifactIdentityError("wheel METADATA is malformed") from error
    metadata_versions = message.get_all("Metadata-Version", [])
    if len(metadata_versions) != 1 or not _CORE_METADATA_VERSION.fullmatch(
        metadata_versions[0].strip()
    ):
        raise ArtifactIdentityError(
            "wheel METADATA must contain exactly one sane Metadata-Version"
        )
    names = message.get_all("Name", [])
    versions = message.get_all("Version", [])
    if len(names) != 1 or not names[0].strip():
        raise ArtifactIdentityError(
            "wheel METADATA must contain exactly one non-empty Name"
        )
    if len(versions) != 1 or not versions[0].strip():
        raise ArtifactIdentityError(
            "wheel METADATA must contain exactly one non-empty Version"
        )
    return names[0].strip(), versions[0].strip()


def _portable_path(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def _require_matching_dist_info(name: str, distribution: str, version: str) -> None:
    actual = name.removesuffix("/METADATA")
    normalized = _DIST_INFO_NORMALIZE.sub("_", distribution).lower()
    expected = f"{normalized}-{version}.dist-info"
    if actual != expected:
        raise ArtifactIdentityError(
            f"wheel .dist-info directory {actual!r} does not match METADATA "
            f"Name/Version; expected {expected!r}"
        )


def _require_wheel_metadata(metadata: bytes) -> None:
    try:
        message = BytesParser(policy=strict).parsebytes(metadata)
    except (UnicodeDecodeError, ValueError) as error:
        raise ArtifactIdentityError("wheel WHEEL metadata is malformed") from error
    wheel_versions = message.get_all("Wheel-Version", [])
    if len(wheel_versions) != 1 or not wheel_versions[0].strip():
        raise ArtifactIdentityError(
            "wheel WHEEL metadata must contain exactly one non-empty Wheel-Version"
        )
    major, separator, remainder = wheel_versions[0].strip().partition(".")
    if not separator or not major.isdigit() or not remainder.isdigit():
        raise ArtifactIdentityError("wheel WHEEL metadata has an invalid Wheel-Version")
    if int(major) != _SUPPORTED_WHEEL_MAJOR:
        raise ArtifactIdentityError(
            "wheel WHEEL metadata uses unsupported Wheel-Version "
            f"{wheel_versions[0]!r}"
        )


def _require_readable_record(record: bytes) -> None:
    try:
        rows = list(csv_reader(StringIO(record.decode("utf-8")), strict=True))
    except (CsvError, UnicodeDecodeError) as error:
        raise ArtifactIdentityError("wheel RECORD is not readable CSV") from error
    if not rows or any(len(row) != 3 or not row[0] for row in rows):
        raise ArtifactIdentityError(
            "wheel RECORD must contain non-empty three-column CSV rows"
        )


def _semantic_manifest_digest(
    archive: zipfile.ZipFile, entries: list[zipfile.ZipInfo], record_path: str
) -> str:
    frames = [_SEMANTIC_DOMAIN, _frame("schema", ARTIFACT_IDENTITY_SCHEMA.encode())]
    for entry in sorted(entries, key=lambda item: item.filename):
        if entry.is_dir() or entry.filename == record_path:
            continue
        payload = archive.read(entry)
        frames.extend(
            (
                _frame("path", entry.filename.encode("utf-8")),
                _frame("size", str(len(payload)).encode("ascii")),
                _frame("sha256", hashlib.sha256(payload).digest()),
            )
        )
    return f"sha256:{hashlib.sha256(b''.join(frames)).hexdigest()}"


def _frame(tag: str, payload: bytes) -> bytes:
    return (
        tag.encode("ascii") + b":" + str(len(payload)).encode("ascii") + b":" + payload
    )


def _sha256_bytes(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"
