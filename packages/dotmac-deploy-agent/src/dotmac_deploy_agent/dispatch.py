"""Verify the Control-signed dispatch before acting on anything (ruling R1).

The agent mints `secret_id`s and moves images BEFORE the bindings consume the
dispatch, so it may act only on a dispatch that Control signed and that binds the
plan it reads. CP-response fields are never an authority for what to do.

What is checked, all against the enrolment-pinned `DISPATCH_VERIFICATION_IDENTITY`
(the same file the CP bindings are given):

* the identity file: root-owned, not writable by others, closed schema, canonical
  key, fingerprint derived from the key;
* the envelope: closed statement, purpose `deployment_dispatch`, the pinned
  key id / algorithm / fingerprint, an Ed25519 signature over Control's canonical
  JSON of the statement;
* the binding: statement `target_ref` is this host's, and
  `sha256(canonical_json(plan_snapshot)) == statement.plan_digest`.

The authorization envelope and the consume itself stay the bindings' work.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .canon import DIGEST, canonical_json
from .config import AgentConfig
from .refusal import Refusal
from .wire import decode_b64url, is_denied_public_key

IDENTITY_SCHEMA: Final = "PlatformCpPublicVerificationIdentity.v1"
MAX_IDENTITY_BYTES: Final = 16 * 1024
DISPATCH_SCHEMA: Final = "dotmac.deployment-dispatch"
DISPATCH_VERSION: Final = 1
DISPATCH_PURPOSE: Final = "deployment_dispatch"
_IDENTITY_FIELDS: Final = frozenset(
    (
        "schema",
        "key_id",
        "algorithm",
        "purpose",
        "public_key_b64url",
        "public_key_fingerprint",
    )
)
_STATEMENT_KEYS: Final = frozenset(
    (
        "schema",
        "version",
        "purpose",
        "dispatch_id",
        "authorization_id",
        "authorization_plan_id",
        "authorization_control_version",
        "authorization_key_id",
        "authorization_public_key_fingerprint",
        "authorization_envelope_digest",
        "execution_sequence",
        "attempt_no",
        "rollout_ref",
        "target_id",
        "target_ref",
        "product_code",
        "environment",
        "operation",
        "release_ref",
        "authorized_images",
        "plan_digest",
        "descriptor_digest",
        "execution_plan_digest",
        "issued_at",
        "control_version",
        "key_id",
        "algorithm",
        "public_key_fingerprint",
    )
)
_DISPATCH_ID: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")


@dataclass(frozen=True)
class PinnedIdentity:
    key_id: str
    public_key: bytes
    fingerprint: str


@dataclass(frozen=True)
class VerifiedDispatch:
    dispatch_id: str
    plan_digest: str
    execution_sequence: int
    attempt_no: int
    operation: str = "deploy"


def fingerprint_of(public_key: bytes) -> str:
    """Control's `PublicKeyFingerprintV1`: `sha256:<hex>` over the raw key bytes."""
    return "sha256:" + hashlib.sha256(public_key).hexdigest()


def read_identity(path: Path, *, owner_uid: int = 0) -> PinnedIdentity:
    """Load the pinned dispatch identity, or refuse. Never reads a key from the
    document being judged."""
    refused = Refusal("dispatch_identity_invalid")
    try:
        parent = os.lstat(path.parent)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise refused from None
        raise refused from None
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != owner_uid
            or parent.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != owner_uid
            or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or info.st_size > MAX_IDENTITY_BYTES
        ):
            raise refused
        raw = os.read(descriptor, MAX_IDENTITY_BYTES + 1)
    finally:
        os.close(descriptor)
    try:
        document = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise refused from None
    if (
        not isinstance(document, dict)
        or set(document) != _IDENTITY_FIELDS
        or document["schema"] != IDENTITY_SCHEMA
        or document["purpose"] != DISPATCH_PURPOSE
        or document["algorithm"] != "ed25519"
    ):
        raise refused
    key_id = document["key_id"]
    key = decode_b64url(document["public_key_b64url"], length=32)
    if (
        not isinstance(key_id, str)
        or not key_id.strip()
        or len(key_id) > 200
        or key is None
    ):
        raise refused
    if document["public_key_fingerprint"] != fingerprint_of(
        key
    ) or is_denied_public_key(str(document["public_key_b64url"])):
        raise refused  # a key whose seed is published authenticates nobody
    return PinnedIdentity(key_id, key, fingerprint_of(key))


def _instant(value: object) -> datetime:
    if not isinstance(value, str):
        raise Refusal("dispatch_envelope_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise Refusal("dispatch_envelope_invalid") from None
    if parsed.tzinfo is None:
        raise Refusal("dispatch_envelope_invalid")
    return parsed.astimezone(UTC)


def plan_digest_of(snapshot: Mapping[str, object]) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(snapshot)).hexdigest()


def verify_dispatch(
    envelope: object,
    plan_snapshot: object,
    *,
    config: AgentConfig,
    identity: PinnedIdentity,
    store_now: datetime,
    skew_seconds: int = 60,
    max_age_seconds: int = 6 * 3600,
) -> VerifiedDispatch:
    if not isinstance(envelope, dict) or set(envelope) != {"statement", "signature"}:
        raise Refusal("dispatch_envelope_invalid")
    statement = envelope["statement"]
    signature = decode_b64url(envelope["signature"], length=64)
    if not isinstance(statement, dict) or set(statement) != _STATEMENT_KEYS:
        raise Refusal("dispatch_envelope_invalid")
    if signature is None:
        raise Refusal("dispatch_signature_invalid")
    if (
        statement["schema"] != DISPATCH_SCHEMA
        or statement["version"] != DISPATCH_VERSION
        or statement["purpose"] != DISPATCH_PURPOSE
    ):
        raise Refusal("dispatch_envelope_invalid")
    # The signer must be exactly the pinned identity, for this purpose.
    if (
        statement["key_id"] != identity.key_id
        or statement["algorithm"] != "ed25519"
        or statement["public_key_fingerprint"] != identity.fingerprint
    ):
        raise Refusal("dispatch_signature_invalid")
    try:
        Ed25519PublicKey.from_public_bytes(identity.public_key).verify(
            signature, canonical_json(cast("dict[str, object]", statement))
        )
    except (InvalidSignature, ValueError):
        raise Refusal("dispatch_signature_invalid") from None
    # Signed content is now trusted: bind it to THIS host and THIS plan.
    if (
        statement["target_ref"] != config.target_ref
        or statement["product_code"] != config.product
        or statement["environment"] != config.environment
    ):
        raise Refusal("materials_target_mismatch")
    issued_at = _instant(statement["issued_at"])
    if issued_at > store_now + timedelta(seconds=skew_seconds):
        raise Refusal("dispatch_envelope_invalid")
    # A signed dispatch does not stay servable forever: CP could replay an old one.
    if store_now - issued_at > timedelta(seconds=max_age_seconds):
        raise Refusal("dispatch_stale")
    dispatch_id = statement["dispatch_id"]
    plan_digest = statement["plan_digest"]
    if (
        not isinstance(dispatch_id, str)
        or not _DISPATCH_ID.fullmatch(dispatch_id)
        or not isinstance(plan_digest, str)
        or not DIGEST.fullmatch(plan_digest)
    ):
        raise Refusal("dispatch_envelope_invalid")
    if (
        not isinstance(plan_snapshot, dict)
        or plan_digest_of(plan_snapshot) != plan_digest
    ):
        raise Refusal("dispatch_plan_mismatch")
    sequence, attempt = statement["execution_sequence"], statement["attempt_no"]
    if any(type(n) is not int or n < 1 for n in (sequence, attempt)):
        raise Refusal("dispatch_envelope_invalid")
    operation = statement["operation"]
    if not isinstance(operation, str) or not operation:
        raise Refusal("dispatch_envelope_invalid")
    return VerifiedDispatch(dispatch_id, plan_digest, sequence, attempt, operation)
