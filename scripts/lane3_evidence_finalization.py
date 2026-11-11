"""Bounded late encryption of runner-held private evidence, without plaintext files.

This is transport preparation, not a proof verifier. The pinned executable and
OS/image are trusted; an age header establishes only the expected transport.
Exposure schema and completeness remain the recording/runner owners' duties.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lane3_evidence_bundle import build_manifest, require_plaintext_bundle

_POLICY = (
    Path(__file__).resolve().parents[1]
    / "docs/inventories/lane3-evidence-encryption-policy.json"
)
_AGE = "/usr/bin/age"
_AGE_HEADER = b"age-encryption.org/v1\n"
_SCHEMA = "lane3.evidence-encryption-policy.v1"
MAX_POLICY_BYTES = 4096
MAX_MEMBER_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_BYTES = 40 * 1024 * 1024
MAX_CIPHERTEXT_BYTES = MAX_ARCHIVE_BYTES + 1024 * 1024
_MAX_TOOL_BYTES = 64 * 1024 * 1024


class FinalizationRefused(ValueError):
    """Evidence, policy, tool or output failed a bounded transport check."""


class FinalizationUnavailable(RuntimeError):
    """Required enrollment or encryption facilities are unavailable."""


@dataclass(frozen=True, slots=True)
class EncryptionContext:
    """A checked snapshot, revalidated against fixed source before finalization."""

    canonical_bytes: bytes
    recipient: str | None
    age_sha256: str | None
    state: str


def _polymod(values: list[int]) -> int:
    checksum = 1
    generators = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    for value in values:
        top = checksum >> 25
        checksum = ((checksum & 0x1FFFFFF) << 5) ^ value
        for index, generator in enumerate(generators):
            if (top >> index) & 1:
                checksum ^= generator
    return checksum


def _recipient_valid(value: object) -> bool:
    if type(value) is not str or len(value) != 62 or not value.startswith("age1"):
        return False
    alphabet = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
    if any(character not in alphabet for character in value[4:]):
        return False
    data = [alphabet.index(character) for character in value[4:]]
    # Native X25519 recipients hold 32 bytes: 52 groups with four zero pad bits.
    expanded_hrp = [ord(c) >> 5 for c in "age"] + [0] + [ord(c) & 31 for c in "age"]
    return data[51] & 15 == 0 and _polymod(expanded_hrp + data) == 1


def _parse_policy(raw: bytes) -> EncryptionContext:
    try:
        if type(raw) is not bytes or not 0 < len(raw) <= MAX_POLICY_BYTES:
            raise ValueError
        document = json.loads(raw)
        if type(document) is not dict or set(document) != {
            "schema",
            "state",
            "recipient",
            "age_sha256",
        }:
            raise ValueError
        if document["schema"] != _SCHEMA:
            raise ValueError
        state, recipient, digest = (
            document[k] for k in ("state", "recipient", "age_sha256")
        )
        if state == "unenrolled":
            if recipient is not None or digest is not None:
                raise ValueError
        elif state == "enrolled":
            if not _recipient_valid(recipient) or type(digest) is not str:
                raise ValueError
            if re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
                raise ValueError
        else:
            raise ValueError
        canonical = (
            json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        if raw != canonical:
            raise ValueError
        return EncryptionContext(raw, recipient, digest, state)
    except (ValueError, TypeError, RecursionError):
        raise FinalizationRefused("encryption-policy-invalid") from None


def _require_tool(expected_digest: str) -> None:
    try:
        metadata = os.lstat(_AGE)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_mode & 0o022
            or not metadata.st_mode & 0o111
            or not 0 < metadata.st_size <= _MAX_TOOL_BYTES
        ):
            raise FinalizationRefused("encryption-tool-unsafe")
        descriptor = os.open(_AGE, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if _tool_identity(opened) != _tool_identity(metadata):
                raise FinalizationRefused("encryption-tool-unsafe")
            hashed = hashlib.sha256()
            remaining = _MAX_TOOL_BYTES + 1
            while remaining:
                chunk = stream.read(min(65536, remaining))
                if not chunk:
                    break
                hashed.update(chunk)
                remaining -= len(chunk)
            if remaining == 0 or "sha256:" + hashed.hexdigest() != expected_digest:
                raise FinalizationRefused("encryption-tool-pin-mismatch")
            # Detect modification during hashing as well as pathname replacement.
            if _tool_identity(os.fstat(stream.fileno())) != _tool_identity(
                opened
            ) or _tool_identity(os.lstat(_AGE)) != _tool_identity(opened):
                raise FinalizationRefused("encryption-tool-unsafe")
    except OSError:
        raise FinalizationUnavailable("encryption-tool-unavailable") from None


def _tool_identity(metadata: os.stat_result) -> tuple[int, ...]:
    # Reads can change atime; mutation-relevant metadata must remain stable.
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def prepare_encryption() -> EncryptionContext:
    """Check fixed protected enrollment and the fixed tool before host contact."""
    try:
        descriptor = os.open(_POLICY, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise FinalizationRefused("encryption-policy-invalid")
            context = _parse_policy(stream.read(MAX_POLICY_BYTES + 1))
    except OSError:
        raise FinalizationUnavailable("encryption-policy-unavailable") from None
    if context.state != "enrolled":
        raise FinalizationUnavailable("encryption-policy-unenrolled")
    if context.age_sha256 is None:
        raise FinalizationRefused("encryption-policy-invalid")
    _require_tool(context.age_sha256)
    return context


def _archive(probe_evidence: bytes, exposure_calls: bytes) -> bytes:
    members = {
        "probe-evidence.json": probe_evidence,
        "exposure-calls.json": exposure_calls,
    }
    try:
        for raw in members.values():
            if type(raw) is not bytes or not 0 < len(raw) <= MAX_MEMBER_BYTES:
                raise ValueError
            if type(json.loads(raw)) is not dict:
                raise ValueError
        manifest = build_manifest(members)
        require_plaintext_bundle(manifest, members)
        storage = io.BytesIO()
        with tarfile.open(
            fileobj=storage, mode="w", format=tarfile.USTAR_FORMAT
        ) as archive:
            for name, raw in [("manifest.json", manifest), *members.items()]:
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.mtime = len(raw), 0o600, 0
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                archive.addfile(info, io.BytesIO(raw))
        raw_archive = storage.getvalue()
        if len(raw_archive) > MAX_ARCHIVE_BYTES:
            raise ValueError
        return raw_archive
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise FinalizationRefused("encryption-members-invalid") from None


def _same_output(directory: int, name: str, descriptor: int) -> bool:
    opened = os.fstat(descriptor)
    named = os.stat(name, dir_fd=directory, follow_symlinks=False)
    return (
        stat.S_ISREG(named.st_mode)
        and (named.st_dev, named.st_ino) == (opened.st_dev, opened.st_ino)
        and opened.st_nlink == 1
        and opened.st_uid == os.geteuid()
        and opened.st_mode & 0o777 == 0o600
    )


def finalize_evidence(
    *,
    probe_evidence: bytes,
    exposure_calls: bytes,
    context: EncryptionContext,
    output: Path,
) -> bytes:
    """Encrypt exactly the two late members and return exact newly written bytes.

    No private input paths, plaintext temporary files, signing, decryption or
    key generation exist here. Existing output names are always refused.
    """
    if type(context) is not EncryptionContext or context != prepare_encryption():
        raise FinalizationRefused("encryption-context-invalid")
    if not isinstance(output, Path) or output.name in {"", ".", ".."}:
        raise FinalizationRefused("encryption-output-invalid")
    plaintext = _archive(probe_evidence, exposure_calls)
    directory: int | None = None
    descriptor: int | None = None
    success = False
    try:
        directory = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        parent = os.fstat(directory)
        if parent.st_uid != os.geteuid() or parent.st_mode & 0o022:
            raise FinalizationRefused("encryption-output-unsafe")
        try:
            descriptor = os.open(
                output.name,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory,
            )
        except FileExistsError:
            raise FinalizationRefused("encryption-output-exists") from None
        os.fchmod(descriptor, 0o600)
        # Keep the original descriptor open for identity/cleanup checks.
        with os.fdopen(os.dup(descriptor), "w+b") as stream:
            completed = subprocess.run(
                [_AGE, "--encrypt", "--recipient", context.recipient],
                input=plaintext,
                stdout=stream,
                stderr=subprocess.DEVNULL,
                shell=False,
                timeout=30,
                check=False,
                env={"LC_ALL": "C", "PATH": "/usr/bin:/bin", "TZ": "UTC"},
            )
            if completed.returncode != 0:
                raise FinalizationRefused("encryption-process-refused")
            stream.flush()
            if not _same_output(directory, output.name, descriptor):
                raise FinalizationRefused("encryption-output-unsafe")
            if not 0 < os.fstat(descriptor).st_size <= MAX_CIPHERTEXT_BYTES:
                raise FinalizationRefused("encryption-output-size")
            stream.seek(0)
            ciphertext = stream.read(MAX_CIPHERTEXT_BYTES + 1)
            if (
                len(ciphertext) > MAX_CIPHERTEXT_BYTES
                or len(ciphertext) != os.fstat(descriptor).st_size
                or not ciphertext.startswith(_AGE_HEADER)
                or not _same_output(directory, output.name, descriptor)
            ):
                raise FinalizationRefused("encryption-output-invalid")
        success = True
        return ciphertext
    except subprocess.TimeoutExpired:
        raise FinalizationUnavailable("encryption-process-timeout") from None
    except OSError:
        raise FinalizationUnavailable("encryption-output-unavailable") from None
    finally:
        if descriptor is not None:
            if not success and directory is not None:
                try:
                    # Never unlink a preexisting or subsequently replaced name.
                    opened = os.fstat(descriptor)
                    named = os.stat(
                        output.name, dir_fd=directory, follow_symlinks=False
                    )
                    if (opened.st_dev, opened.st_ino) == (named.st_dev, named.st_ino):
                        os.unlink(output.name, dir_fd=directory)
                except OSError:
                    pass
            os.close(descriptor)
        if directory is not None:
            os.close(directory)


def main() -> int:
    """Fixed preflight only; inputs cannot choose a recipient, tool or policy."""
    if len(sys.argv) != 1:
        print("REFUSED: encryption-preflight-arguments", file=sys.stderr)
        return 1
    try:
        prepare_encryption()
    except FinalizationRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    except FinalizationUnavailable as exc:
        print(f"INDETERMINATE: {exc}", file=sys.stderr)
        return 2
    print("encryption-preflight=ready")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
