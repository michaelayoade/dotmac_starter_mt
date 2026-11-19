"""Synthetic keys, roots and envelopes for the S2 tests. Nothing here is a real key."""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from dotmac_deployment_foundation.digest import Digest
from dotmac_deployment_foundation.trusted_host_source import (
    ATTESTATION_SCHEMA,
    CANDIDATE_ATTESTATION_PURPOSE,
    INSTALLED_OBSERVATION_PURPOSE,
    AttestationEnvelopeV2,
    CandidateAttestationSubjectV2,
    InstalledHostAttestationSubjectV2,
    candidate_subject_digest,
)

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

CANDIDATE_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
INCARNATION_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(33, 65)))
OTHER_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(65, 97)))

HOST_ID = "host-a"
DISPATCH_ID = "6f1c2f4e-52a8-4d0e-9a1b-2f0c7e6d9b11"
ENROLMENT_UUID = "0a1b2c3d-1111-4222-8333-444455556666"
CANDIDATE_ENROLMENT = "9f8e7d6c-1111-4222-8333-444455556666"
CANDIDATE_AUDIENCE = "dotmac-deployment-foundation"
CANDIDATE_ISSUER = "urn:example:candidate-ceremony"
INSTALLED_ISSUER = "urn:example:installed-ceremony"
CANDIDATE_KEY_ID = "candidate-key-1"
INSTALLED_KEY_ID = "incarnation-key-1"
T0 = "2026-11-19T10:00:00Z"
AUTH_EXPIRES = "2026-11-19T20:00:00Z"
ROOT_NOT_AFTER = "2026-12-31T00:00:00Z"
ROOT_NOT_BEFORE = "2026-11-01T00:00:00Z"


def raw_public(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def fingerprint(key: Ed25519PrivateKey) -> str:
    return "sha256:" + hashlib.sha256(raw_public(key)).hexdigest()


def std_b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def root_document(
    key: Ed25519PrivateKey,
    *,
    purpose: str,
    custody_domain: str,
    issuer: str,
    key_id: str,
    trust_root_version: str,
    **overrides: Any,
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "public_key_fingerprint": fingerprint(key),
        "public_key_base64": std_b64(raw_public(key)),
        "issuer": issuer,
        "key_id": key_id,
        "purpose": purpose,
        "custody_domain": custody_domain,
        "algorithm": "ed25519",
        "trust_root_version": trust_root_version,
        "not_before": ROOT_NOT_BEFORE,
        "not_after": ROOT_NOT_AFTER,
    }
    document.update(overrides)
    return document


def candidate_root(**overrides: Any) -> dict[str, Any]:
    return root_document(
        CANDIDATE_KEY,
        purpose=CANDIDATE_ATTESTATION_PURPOSE,
        custody_domain="candidate_release_signer",
        issuer=CANDIDATE_ISSUER,
        key_id=CANDIDATE_KEY_ID,
        trust_root_version=CANDIDATE_ENROLMENT,
        **overrides,
    )


def installed_root(**overrides: Any) -> dict[str, Any]:
    return root_document(
        INCARNATION_KEY,
        purpose=INSTALLED_OBSERVATION_PURPOSE,
        custody_domain="host_attester",
        issuer=INSTALLED_ISSUER,
        key_id=INSTALLED_KEY_ID,
        trust_root_version=ENROLMENT_UUID,
        **overrides,
    )


def candidate_subject(**overrides: Any) -> CandidateAttestationSubjectV2:
    values: dict[str, Any] = {
        "package": "dotmac-deployment-foundation",
        "version": "0.4.0a2",
        "wheel_sha256": Digest.parse("sha256:" + "a" * 64, where="wheel"),
        "source_revision": "c" * 40,
        "repository": "example/repo",
        "run_id": "1001",
        "artifact_id": "2002",
    }
    values.update(overrides)
    return CandidateAttestationSubjectV2(**values)


def candidate_envelope(
    key: Ed25519PrivateKey = CANDIDATE_KEY, **subject_overrides: Any
) -> AttestationEnvelopeV2:
    unsigned = AttestationEnvelopeV2(
        ATTESTATION_SCHEMA,
        CANDIDATE_ATTESTATION_PURPOSE,
        CANDIDATE_ISSUER,
        CANDIDATE_KEY_ID,
        "ed25519",
        fingerprint(key),
        "candidate_release_signer",
        CANDIDATE_ENROLMENT,
        "2026-11-19T09:00:00Z",
        "2026-12-20T00:00:00Z",
        CANDIDATE_AUDIENCE,
        "candidate-observation",
        candidate_subject(**subject_overrides).canonical_document(),
        "unsigned",
    )
    signature = std_b64(key.sign(unsigned.signed_bytes()))
    return AttestationEnvelopeV2.from_mapping(
        {**unsigned.canonical_document(), "signature": signature}
    )


def installed_subject_for(
    candidate: AttestationEnvelopeV2, host_id: str = HOST_ID
) -> InstalledHostAttestationSubjectV2:
    subject = CandidateAttestationSubjectV2.from_mapping(candidate.subject_mapping())
    return InstalledHostAttestationSubjectV2(
        host_id,
        subject.package,
        subject.version,
        subject.wheel_sha256,
        candidate_subject_digest(subject),
    )


def installed_envelope(
    candidate: AttestationEnvelopeV2,
    *,
    key: Ed25519PrivateKey = INCARNATION_KEY,
    observation_id: str = DISPATCH_ID,
    host_id: str = HOST_ID,
) -> AttestationEnvelopeV2:
    """What the ceremony's transit signature would produce, signed in-process."""
    unsigned = AttestationEnvelopeV2(
        ATTESTATION_SCHEMA,
        INSTALLED_OBSERVATION_PURPOSE,
        INSTALLED_ISSUER,
        INSTALLED_KEY_ID,
        "ed25519",
        fingerprint(key),
        "host_attester",
        ENROLMENT_UUID,
        T0,
        "2026-11-19T12:00:00Z",
        host_id,
        observation_id,
        installed_subject_for(candidate, host_id).canonical_document(),
        "unsigned",
    )
    signature = std_b64(key.sign(unsigned.signed_bytes()))
    return AttestationEnvelopeV2.from_mapping(
        {**unsigned.canonical_document(), "signature": signature}
    )


def write_json(path: Path, document: Any) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path
