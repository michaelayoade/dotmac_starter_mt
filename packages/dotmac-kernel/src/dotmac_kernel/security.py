"""Auth primitives for the starter.

Password storage is Argon2id via `argon2-cffi` (control-plane security
Task 5) with transparent legacy-hash verification: hashes written before
the switch used stdlib PBKDF2-HMAC-SHA256 and still verify — login paths
upgrade them in place on the next successful login
(`password_needs_rehash`). JWTs stay stdlib HS256.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from argon2 import PasswordHasher
from argon2 import exceptions as argon2_exceptions

from dotmac_kernel.config import settings

# Legacy scheme parameters — kept ONLY so pre-Task-5 hashes keep verifying
# until their owner logs in and gets upgraded.
PBKDF2_ITERATIONS = 210_000
_LEGACY_PREFIX = "pbkdf2_sha256$"
_ARGON2_PREFIX = "$argon2id$"

# OWASP Password Storage Cheat Sheet (2025-05 revision): "Use Argon2id with
# a minimum configuration of 19 MiB of memory, an iteration count of 2, and
# 1 degree of parallelism."
_ARGON2_MEMORY_KIB = 19 * 1024
_ARGON2_ITERATIONS = 2
_ARGON2_PARALLELISM = 1

_hasher = PasswordHasher(
    time_cost=_ARGON2_ITERATIONS,
    memory_cost=_ARGON2_MEMORY_KIB,
    parallelism=_ARGON2_PARALLELISM,
)


def hash_password(password: str) -> str:
    """Argon2id ($argon2id$v=19$m=...,t=...,p=...$salt$digest)."""
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Dispatch on the stored hash's scheme prefix: Argon2id, or the legacy
    PBKDF2 format for hashes written before Task 5 (upgraded on login via
    `password_needs_rehash`). Anything else verifies false."""
    if password_hash.startswith(_ARGON2_PREFIX):
        try:
            return _hasher.verify(password_hash, password)
        except argon2_exceptions.Argon2Error:
            return False
    if password_hash.startswith(_LEGACY_PREFIX):
        return _verify_legacy_pbkdf2(password, password_hash)
    return False


def password_needs_rehash(password_hash: str) -> bool:
    """True when a successfully verified hash should be re-written: legacy
    scheme, or Argon2id with outdated parameters."""
    if password_hash.startswith(_LEGACY_PREFIX):
        return True
    if password_hash.startswith(_ARGON2_PREFIX):
        return _hasher.check_needs_rehash(password_hash)
    return True


def _verify_legacy_pbkdf2(password: str, password_hash: str) -> bool:
    try:
        _scheme, iterations, salt_b64, digest_b64 = password_hash.split("$", 3)
        salt = base64.urlsafe_b64decode(salt_b64.encode())
        expected = base64.urlsafe_b64decode(digest_b64.encode())
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def issue_access_token(party_id: UUID, tenant_id: UUID) -> tuple[str, datetime]:
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.jwt_ttl_seconds)
    payload = {
        "sub": str(party_id),
        "tenant_id": str(tenant_id),
        "exp": int(expires_at.timestamp()),
        "jti": secrets.token_urlsafe(16),
    }
    return encode_jwt(payload), expires_at


def decode_access_token(token: str) -> dict[str, object] | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    signing_input = f"{parts[0]}.{parts[1]}".encode()
    expected = _sign(signing_input)
    if not hmac.compare_digest(expected, parts[2]):
        return None
    try:
        payload = json.loads(_b64decode(parts[1]).decode())
    except (ValueError, json.JSONDecodeError):
        return None
    exp = payload.get("exp")
    if not isinstance(exp, int) or exp < int(datetime.now(UTC).timestamp()):
        return None
    return payload


def hash_token(token: str) -> str:
    return hmac.new(
        settings.session_hash_secret.encode(),
        token.encode(),
        hashlib.sha256,
    ).hexdigest()


def encode_jwt(payload: dict[str, object]) -> str:
    """Encode+sign a JWT payload (HS256). Public because
    `dotmac_kernel.platform_auth` issues platform-audience tokens through the
    same signer — one signing implementation, two token populations
    distinguished by the `aud` claim."""
    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = _b64encode(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode()
    return f"{header_b64}.{payload_b64}.{_sign(signing_input)}"


def _sign(signing_input: bytes) -> str:
    digest = hmac.new(
        settings.jwt_secret.encode(), signing_input, hashlib.sha256
    ).digest()
    return _b64encode(digest)


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode())


__all__ = [
    "hash_password",
    "verify_password",
    "password_needs_rehash",
    "hash_token",
    "issue_access_token",
    "decode_access_token",
]
