"""Closed, public, UNAUTHENTICATED offline claims and pinned verifier policy.

Parsing and binding do not authenticate a custodian or confer authorization.
The fixed SSHSIG consumer must authenticate these exact bytes as well as
requiring every binding. This module creates no signed result or grant.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

MAX_RESULT_BYTES = 16384
MAX_POLICY_BYTES = 16384
MAX_SIGNATURE_BYTES = 8192
MAX_SIGNERS_BYTES = 8192
MAX_REVOCATIONS_BYTES = 65536
NAMESPACE = "foundation-rehearsal-verification-v1@dotmac.io"
PRINCIPAL = "foundation-offline-verifier@dotmac.io"
AUDIENCE = "dotmac.foundation.publication"
RESULT_SCHEMA = "lane3.offline-verification-result.v1"
POLICY_SCHEMA = "lane3.offline-verifier-policy.v1"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_REVISION = re.compile(r"[0-9a-f]{40}\Z")
_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
_SSH_TIME = re.compile(r"[0-9]{14}Z\Z")


class OfflineResultRefused(ValueError):
    """A public, stable reason code; never include supplied document text."""


class OfflineResultUnavailable(ValueError):
    """No enrolled verification authority is available."""


@dataclass(frozen=True)
class OfflineResultCoordinates:
    repository_id: int
    run_id: int
    run_attempt: int
    receipt_digest: str
    ciphertext_digest: str
    # CandidateArtifact.v1 record digest, never a distribution file's SHA.
    candidate_artifact_digest: str
    release_revision: str


@dataclass(frozen=True)
class ParsedOfflineResult:
    coordinates: OfflineResultCoordinates
    verifier_source_revision: str
    verifier_profile_digest: str
    policy_digest: str
    verified_at: datetime
    expires_at: datetime
    conclusion: str
    canonical_bytes: bytes

    def sha256_digest(self) -> str:
        return _digest(self.canonical_bytes)


@dataclass(frozen=True)
class VerificationPolicy:
    state: str
    namespace: str
    principal: str
    audience: str
    ssh_keygen_sha256: str | None
    verifier_source_revision: str | None
    verifier_profile_digest: str | None
    allowed_signers_digest: str
    revoked_keys_digest: str
    max_result_age_seconds: int
    future_clock_tolerance_seconds: int
    allowed_signers_bytes: bytes
    revoked_keys_bytes: bytes
    canonical_bytes: bytes

    def sha256_digest(self) -> str:
        return _digest(self.canonical_bytes)


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _refuse(scope: str, reason: str) -> None:
    raise OfflineResultRefused(f"offline_{scope}.{reason}")


def _json(payload: bytes, limit: int, scope: str) -> dict:
    if type(payload) is not bytes or not 0 < len(payload) <= limit:
        _refuse(scope, "size")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                _refuse(scope, "duplicate_key")
            result[key] = value
        return result

    def noninteger(_value):
        _refuse(scope, "number")

    try:
        document = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=pairs,
            parse_float=noninteger,
            parse_constant=noninteger,
        )
        if type(document) is not dict:
            _refuse(scope, "schema")
        canonical = (
            json.dumps(
                document, ensure_ascii=True, sort_keys=True, separators=(",", ":")
            )
            + "\n"
        ).encode("ascii")
        if canonical != payload:
            _refuse(scope, "noncanonical")
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        OverflowError,
        ValueError,
    ) as exc:
        if isinstance(exc, OfflineResultRefused):
            raise
        raise OfflineResultRefused(f"offline_{scope}.encoding") from None
    return document


def _keys(document, keys: set[str], scope: str) -> None:
    if type(document) is not dict or set(document) != keys:
        _refuse(scope, "schema")


def _pattern(value, pattern, scope: str) -> None:
    if type(value) is not str or pattern.fullmatch(value) is None:
        _refuse(scope, "field")


def _time(value, scope: str) -> datetime:
    _pattern(value, _TIME, scope)
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        raise OfflineResultRefused(f"offline_{scope}.time") from None


def _coordinates(document) -> OfflineResultCoordinates:
    _keys(document, set(OfflineResultCoordinates.__dataclass_fields__), "result")
    for name in ("repository_id", "run_id", "run_attempt"):
        if type(document[name]) is not int or not 1 <= document[name] <= 2**63 - 1:
            _refuse("result", "coordinate")
    for name in ("receipt_digest", "ciphertext_digest", "candidate_artifact_digest"):
        _pattern(document[name], _DIGEST, "result")
    _pattern(document["release_revision"], _REVISION, "result")
    return OfflineResultCoordinates(**document)


def parse_result(payload: bytes) -> ParsedOfflineResult:
    """Parse only; the returned claim remains UNAUTHENTICATED."""
    document = _json(payload, MAX_RESULT_BYTES, "result")
    _keys(
        document,
        {
            "schema",
            "purpose",
            "audience",
            "coordinates",
            "verifier_source_revision",
            "verifier_profile_digest",
            "policy_digest",
            "verified_at",
            "expires_at",
            "conclusion",
        },
        "result",
    )
    if (
        document["schema"],
        document["purpose"],
        document["audience"],
        document["conclusion"],
    ) != (RESULT_SCHEMA, NAMESPACE, AUDIENCE, "verified"):
        _refuse("result", "schema")
    _pattern(document["verifier_source_revision"], _REVISION, "result")
    for name in ("verifier_profile_digest", "policy_digest"):
        _pattern(document[name], _DIGEST, "result")
    return ParsedOfflineResult(
        _coordinates(document["coordinates"]),
        document["verifier_source_revision"],
        document["verifier_profile_digest"],
        document["policy_digest"],
        _time(document["verified_at"], "result"),
        _time(document["expires_at"], "result"),
        document["conclusion"],
        payload,
    )


def _trust_bytes(payload: bytes, limit: int) -> str:
    if type(payload) is not bytes or len(payload) > limit:
        _refuse("policy", "trust_size")
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError:
        raise OfflineResultRefused("offline_policy.trust_encoding") from None
    if payload and (
        not payload.endswith(b"\n") or any(ord(c) < 32 and c != "\n" for c in text)
    ):
        _refuse("policy", "trust_encoding")
    return text


def _public_key(value: str) -> None:
    # Syntax only. No custom SSH wire/curve validator: OpenSSH owns key validity.
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise OfflineResultRefused("offline_policy.key_encoding") from None
    if not decoded or base64.b64encode(decoded).decode("ascii") != value:
        _refuse("policy", "key_encoding")


def _signer(text: str) -> None:
    lines = text.splitlines()
    if len(lines) != 1:
        _refuse("policy", "signer")
    parts = lines[0].split(" ")
    if len(parts) != 4 or parts[0] != PRINCIPAL or parts[2] != "ssh-ed25519":
        _refuse("policy", "signer")
    options = {}
    for option in parts[1].split(","):
        match = re.fullmatch(r'([a-z-]+)="([^"\s,]+)"', option)
        if match is None or match[1] in options:
            _refuse("policy", "signer_options")
        options[match[1]] = match[2]
    if (
        set(options) != {"namespaces", "valid-after", "valid-before"}
        or options["namespaces"] != NAMESPACE
    ):
        _refuse("policy", "signer_options")
    times = []
    for name in ("valid-after", "valid-before"):
        _pattern(options[name], _SSH_TIME, "policy")
        try:
            times.append(datetime.strptime(options[name], "%Y%m%d%H%M%SZ"))
        except ValueError:
            raise OfflineResultRefused("offline_policy.signer_time") from None
    if times[0] >= times[1]:
        _refuse("policy", "signer_time")
    _public_key(parts[3])


def parse_policy(
    policy_bytes: bytes, allowed_signers_bytes: bytes, revoked_keys_bytes: bytes
) -> VerificationPolicy:
    """Pin exact local trust bytes; no paths, discovery or fetched roots."""
    document = _json(policy_bytes, MAX_POLICY_BYTES, "policy")
    fields = set(VerificationPolicy.__dataclass_fields__) - {
        "allowed_signers_bytes",
        "revoked_keys_bytes",
        "canonical_bytes",
    }
    _keys(document, fields | {"schema"}, "policy")
    if (
        document["schema"],
        document["namespace"],
        document["principal"],
        document["audience"],
    ) != (POLICY_SCHEMA, NAMESPACE, PRINCIPAL, AUDIENCE):
        _refuse("policy", "schema")
    if document["state"] not in ("unenrolled", "enrolled"):
        _refuse("policy", "state")
    for name, required in (
        ("max_result_age_seconds", 86400),
        ("future_clock_tolerance_seconds", 300),
    ):
        if type(document[name]) is not int or document[name] != required:
            _refuse("policy", "time_policy")
    signers = _trust_bytes(allowed_signers_bytes, MAX_SIGNERS_BYTES)
    revocations = _trust_bytes(revoked_keys_bytes, MAX_REVOCATIONS_BYTES)
    for name, payload in (
        ("allowed_signers_digest", allowed_signers_bytes),
        ("revoked_keys_digest", revoked_keys_bytes),
    ):
        _pattern(document[name], _DIGEST, "policy")
        if document[name] != _digest(payload):
            _refuse("policy", "trust_digest")
    for line in revocations.splitlines():
        parts = line.split(" ")
        if len(parts) != 2 or parts[0] != "ssh-ed25519":
            _refuse("policy", "revocation")
        _public_key(parts[1])
    pins = ("ssh_keygen_sha256", "verifier_source_revision", "verifier_profile_digest")
    if document["state"] == "unenrolled":
        if any(document[name] is not None for name in pins) or signers or revocations:
            _refuse("policy", "unenrolled")
    else:
        for name in pins:
            _pattern(
                document[name],
                _REVISION if name == "verifier_source_revision" else _DIGEST,
                "policy",
            )
        _signer(signers)
    values = {name: document[name] for name in fields}
    return VerificationPolicy(
        **values,
        allowed_signers_bytes=allowed_signers_bytes,
        revoked_keys_bytes=revoked_keys_bytes,
        canonical_bytes=policy_bytes,
    )


def require_result_bindings(
    result: ParsedOfflineResult,
    *,
    expected: OfflineResultCoordinates,
    policy: VerificationPolicy,
    now: datetime,
) -> None:
    """Require current tuple/policy/time bindings, separately from authentication."""
    if policy.state != "enrolled":
        raise OfflineResultUnavailable("offline_policy.unenrolled")
    if (
        type(now) is not datetime
        or now.tzinfo is None
        or now.utcoffset() != timedelta(0)
    ):
        _refuse("result", "clock")
    if type(expected) is not OfflineResultCoordinates:
        _refuse("result", "expected_coordinates")
    # Trusted transport must still preserve integer/digest types: Python's
    # bool/int equality cannot make an invalid expected coordinate match.
    _coordinates(
        {
            name: getattr(expected, name)
            for name in OfflineResultCoordinates.__dataclass_fields__
        }
    )
    if result.coordinates != expected:
        _refuse("result", "coordinates_mismatch")
    if (
        result.verifier_source_revision,
        result.verifier_profile_digest,
        result.policy_digest,
    ) != (
        policy.verifier_source_revision,
        policy.verifier_profile_digest,
        policy.sha256_digest(),
    ):
        _refuse("result", "policy_mismatch")
    age = timedelta(seconds=policy.max_result_age_seconds)
    future = timedelta(seconds=policy.future_clock_tolerance_seconds)
    if (
        result.verified_at > now + future
        or result.verified_at >= result.expires_at
        or result.expires_at - result.verified_at > age
        or now >= result.expires_at
        or now - result.verified_at > age
    ):
        _refuse("result", "time_bounds")
