"""Authenticate a bounded offline claim with the protected OpenSSH policy.

The OS/image and root-owned executable are trusted assumptions. Tool hashing
does not contain a compromised OS. A signature authenticates principal claims;
it does not prove which code that principal executed or create a full proof.
"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from lane3_offline_result import (
    MAX_POLICY_BYTES,
    MAX_RESULT_BYTES,
    MAX_REVOCATIONS_BYTES,
    MAX_SIGNATURE_BYTES,
    MAX_SIGNERS_BYTES,
    NAMESPACE,
    PRINCIPAL,
    OfflineResultCoordinates,
    OfflineResultRefused,
    OfflineResultUnavailable,
    ParsedOfflineResult,
    VerificationPolicy,
    parse_policy,
    parse_result,
    require_result_bindings,
)

_SSH_KEYGEN = "/usr/bin/ssh-keygen"
_MAX_TOOL_BYTES = 32 * 1024 * 1024


def _bounded(raw: bytes, limit: int, reason: str, *, empty: bool = False) -> None:
    if type(raw) is not bytes or len(raw) > limit or (not raw and not empty):
        raise OfflineResultRefused(reason)


def _require_tool(expected_digest: str) -> None:
    """Read a fixed, root-owned, non-symlink regular tool before executing it."""
    try:
        metadata = os.lstat(_SSH_KEYGEN)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_mode & 0o022
            or not metadata.st_mode & 0o111
            or metadata.st_uid != 0
            or metadata.st_size > _MAX_TOOL_BYTES
        ):
            raise OfflineResultRefused("offline-tool-unsafe")
        descriptor = os.open(_SSH_KEYGEN, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino, opened.st_mode, opened.st_uid) != (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_mode,
                metadata.st_uid,
            ):
                raise OfflineResultRefused("offline-tool-unsafe")
            hashed = hashlib.sha256()
            remaining = _MAX_TOOL_BYTES + 1
            while remaining:
                chunk = stream.read(min(65536, remaining))
                if not chunk:
                    break
                hashed.update(chunk)
                remaining -= len(chunk)
            if remaining == 0:
                raise OfflineResultRefused("offline-tool-unsafe")
            if "sha256:" + hashed.hexdigest() != expected_digest:
                raise OfflineResultRefused("offline-tool-pin-mismatch")
    except OSError:
        raise OfflineResultUnavailable("offline-tool-unavailable") from None


def require_authenticated_result(
    payload: bytes,
    signature: bytes,
    *,
    expected: OfflineResultCoordinates,
    policy: VerificationPolicy,
    now: datetime,
) -> ParsedOfflineResult:
    """Verify original bytes and current bindings; never return a parsed claim alone."""
    if type(policy) is not VerificationPolicy:
        raise OfflineResultRefused("offline-policy-invalid")
    _bounded(policy.canonical_bytes, MAX_POLICY_BYTES, "offline-policy-size")
    _bounded(
        policy.allowed_signers_bytes,
        MAX_SIGNERS_BYTES,
        "offline-signers-size",
        empty=True,
    )
    _bounded(
        policy.revoked_keys_bytes,
        MAX_REVOCATIONS_BYTES,
        "offline-revocations-size",
        empty=True,
    )
    # Reparse at the trust boundary: a directly constructed dataclass is no
    # substitute for the schema owner's protected-policy validation.
    checked_policy = parse_policy(
        policy.canonical_bytes, policy.allowed_signers_bytes, policy.revoked_keys_bytes
    )
    if policy != checked_policy:
        raise OfflineResultRefused("offline-policy-invalid")
    if policy.state != "enrolled":
        raise OfflineResultUnavailable("offline-policy-unenrolled")
    if policy.namespace != NAMESPACE or policy.principal != PRINCIPAL:
        raise OfflineResultRefused("offline-policy-invalid")
    _bounded(payload, MAX_RESULT_BYTES, "offline-result-size")
    _bounded(signature, MAX_SIGNATURE_BYTES, "offline-signature-size")
    result = parse_result(payload)
    require_result_bindings(result, expected=expected, policy=policy, now=now)
    tool_digest = policy.ssh_keygen_sha256
    if tool_digest is None:
        raise OfflineResultRefused("offline-policy-invalid")
    _require_tool(tool_digest)
    try:
        with tempfile.TemporaryDirectory(prefix="lane3-offline-") as directory:
            paths = [
                Path(directory) / name for name in ("allowed", "signature", "revoked")
            ]
            for path, raw in zip(
                paths,
                (policy.allowed_signers_bytes, signature, policy.revoked_keys_bytes),
                strict=True,
            ):
                with path.open("xb") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(raw)
            completed = subprocess.run(
                [
                    _SSH_KEYGEN,
                    "-Y",
                    "verify",
                    "-f",
                    str(paths[0]),
                    "-I",
                    PRINCIPAL,
                    "-n",
                    NAMESPACE,
                    "-s",
                    str(paths[1]),
                    "-r",
                    str(paths[2]),
                ],
                input=payload,
                shell=False,
                timeout=10,
                env={"LC_ALL": "C", "PATH": "/usr/bin:/bin", "TZ": "UTC"},
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
    except subprocess.TimeoutExpired:
        raise OfflineResultUnavailable("offline-tool-timeout") from None
    except OSError:
        raise OfflineResultUnavailable("offline-tool-unavailable") from None
    if completed.returncode != 0:
        raise OfflineResultRefused("offline-signature-invalid")
    return result
