"""The target request/response signing contract (DESIGN-rev3 s2.6).

Every request carries an Ed25519 signature from the TargetCredential PoP key over
`(method, path, sha256(body), credential_id, target_ref, issued_at, nonce)`.
Every response is signed under the purpose `cp-target-response.v1`.

The byte layout, header names, path shape and encodings are owned by Deployment
Control (`contract/target_api_v1.json`, ruling R1); this module is the agent's
copy, pinned by the vendored vectors (`tests/unit/test_deploy_agent_conformance.py`).
Keys and signatures are canonical UNPADDED base64url.
"""

from __future__ import annotations

import base64
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .canon import sha256_hex
from .refusal import Refusal

REQUEST_PURPOSE: Final = "dotmac.target_request.v1"
RESPONSE_PURPOSE: Final = "cp-target-response.v1"
BASE_PATH: Final = "/platform/deploy/target/v1"

H_CREDENTIAL: Final = "X-Dotmac-Credential-Id"
H_TARGET: Final = "X-Dotmac-Target-Ref"
H_ISSUED_AT: Final = "X-Dotmac-Issued-At"
H_NONCE: Final = "X-Dotmac-Nonce"
H_SIGNATURE: Final = "X-Dotmac-Signature"
H_RESPONSE_SIGNATURE: Final = "X-Dotmac-Response-Signature"
MAX_SKEW_SECONDS: Final = 60


def _line(value: str) -> str:
    if "\n" in value or "\r" in value or "\x00" in value:
        raise Refusal("materials_invalid")
    return value


def request_message(
    *,
    method: str,
    path: str,
    body: bytes,
    credential_id: str,
    target_ref: str,
    issued_at: str,
    nonce: str,
) -> bytes:
    parts = [
        REQUEST_PURPOSE,
        method.upper(),
        path,
        sha256_hex(body),
        credential_id,
        target_ref,
        issued_at,
        nonce,
    ]
    return "\n".join(_line(part) for part in parts).encode("utf-8")


def response_message(
    *, method: str, path: str, request_nonce: str, status: int, body: bytes
) -> bytes:
    """Bound to the request nonce: a captured response answers nothing else."""
    parts = [
        RESPONSE_PURPOSE,
        method.upper(),
        path,
        request_nonce,
        str(status),
        sha256_hex(body),
    ]
    return "\n".join(_line(part) for part in parts).encode("utf-8")


def utc_stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_stamp(value: str) -> datetime:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise Refusal("response_malformed") from None
    return parsed.replace(tzinfo=UTC)


def new_nonce() -> str:
    return secrets.token_hex(16)


def encode_b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_b64url(value: object, *, length: int) -> bytes | None:
    """Canonical unpadded base64url of exactly `length` bytes, else None."""
    if (
        not isinstance(value, str)
        or not value
        or "=" in value
        or value != value.strip()
    ):
        return None
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except ValueError:
        return None
    if len(raw) != length or encode_b64url(raw) != value:
        return None
    return raw


@dataclass(frozen=True)
class PopSigner:
    """The target PoP private key, held in memory only. Never printed, never stored."""

    credential_id: str
    _key: Ed25519PrivateKey

    @classmethod
    def from_seed(cls, credential_id: str, raw: bytes) -> PopSigner:
        """`raw` is the credential file: unpadded base64url of the 32-byte seed."""
        try:
            text = raw.decode("ascii").strip()
        except UnicodeDecodeError:
            raise Refusal("config_invalid") from None
        seed = decode_b64url(text, length=32)
        if seed is None:
            raise Refusal("config_invalid")
        return cls(credential_id, Ed25519PrivateKey.from_private_bytes(seed))

    def sign(self, message: bytes) -> str:
        return encode_b64url(self._key.sign(message))

    def __repr__(self) -> str:
        return f"PopSigner(credential_id={self.credential_id!r})"


def verify_response(
    public_keys_b64url: tuple[str, ...], message: bytes, signature_b64: str | None
) -> None:
    signature = decode_b64url(signature_b64, length=64)
    if signature is None:
        raise Refusal("response_signature_invalid")
    for encoded in public_keys_b64url:
        raw = decode_b64url(encoded, length=32)
        if raw is None:
            raise Refusal("config_invalid")
        key = Ed25519PublicKey.from_public_bytes(raw)
        try:
            key.verify(signature, message)
        except InvalidSignature:
            continue
        return
    raise Refusal("response_signature_invalid")
