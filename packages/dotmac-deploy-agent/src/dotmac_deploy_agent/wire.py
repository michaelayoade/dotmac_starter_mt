"""The target request/response signing contract (DESIGN-rev3 s2.6).

Every request carries an Ed25519 signature from the TargetCredential PoP key over
`(method, path, sha256(body), credential_id, target_ref, issued_at, nonce)`.
Every response is signed under the purpose `cp-target-response.v1`.

The design fixes the signed tuple and the response purpose, not the byte layout
or header names. Both live ONLY in this module so that aligning with the CP
implementation (WP5b) is a one-file change, pinned by the vectors in the tests.
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


@dataclass(frozen=True)
class PopSigner:
    """The target PoP private key, held in memory only. Never printed, never stored."""

    credential_id: str
    _key: Ed25519PrivateKey

    @classmethod
    def from_seed(cls, credential_id: str, raw: bytes) -> PopSigner:
        """`raw` is the credential file: base64 of the 32-byte Ed25519 seed."""
        try:
            seed = base64.b64decode(raw.strip(), validate=True)
            key = Ed25519PrivateKey.from_private_bytes(seed)
        except ValueError:
            raise Refusal("config_invalid") from None
        return cls(credential_id, key)

    def sign(self, message: bytes) -> str:
        return base64.b64encode(self._key.sign(message)).decode("ascii")

    def __repr__(self) -> str:
        return f"PopSigner(credential_id={self.credential_id!r})"


def verify_response(
    public_keys_b64: tuple[str, ...], message: bytes, signature_b64: str | None
) -> None:
    if signature_b64 is None:
        raise Refusal("response_signature_invalid")
    try:
        signature = base64.b64decode(signature_b64, validate=True)
    except ValueError:
        raise Refusal("response_signature_invalid") from None
    for encoded in public_keys_b64:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(encoded))
        try:
            key.verify(signature, message)
        except InvalidSignature:
            continue
        return
    raise Refusal("response_signature_invalid")
