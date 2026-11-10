"""Lane 3 host-source admission: refusal by default, exact delegation wired.

The unwired provider must stay byte-for-byte the reference refusal. The
wired provider must forward exactly the typed inputs it was constructed
with to Foundation's one public admission function — no discovered
values, no second verification path. The Ed25519 verifier is exercised
against envelopes signed the way the real producer signs them.
"""

from __future__ import annotations

import base64
import dataclasses
import datetime
import hashlib
import pathlib
import sys
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from dotmac_deployment_foundation.errors import PreconditionFailed
from dotmac_deployment_foundation.host_source_admission import (
    HostSourceAdmissionProvider,
    RefusingHostSourceAdmissionProvider,
)
from dotmac_deployment_foundation.trusted_host_source import (
    ATTESTATION_SCHEMA,
    AttestationEnvelopeV2,
)

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import lane3_host_source_admission as admission  # noqa: E402

_NOW = datetime.datetime(2026, 11, 10, tzinfo=datetime.UTC)


def _signed_envelope(private_key: Ed25519PrivateKey) -> AttestationEnvelopeV2:
    public = private_key.public_key()
    raw_public = public.public_bytes_raw()
    unsigned = AttestationEnvelopeV2(
        schema=ATTESTATION_SCHEMA,
        purpose="candidate-attestation",
        issuer="gate0-attester-01",
        key_id="attester-v1",
        algorithm="ed25519",
        public_key_fingerprint=f"sha256:{hashlib.sha256(raw_public).hexdigest()}",
        custody_domain="gate0",
        trust_root_version="v1",
        issued_at="2026-11-10T00:00:00Z",
        expires_at="2026-11-11T00:00:00Z",
        audience="lane3",
        observation_id="obs-1",
        subject={"package": "dotmac-deployment-foundation"},
        signature="unsigned",
    )
    signature = base64.b64encode(private_key.sign(unsigned.signed_bytes())).decode()
    return dataclasses.replace(unsigned, signature=signature)


def test_unwired_provider_refuses_exactly_like_the_reference() -> None:
    provider = admission.Lane3HostSourceAdmissionProvider()

    assert isinstance(provider, HostSourceAdmissionProvider)
    with pytest.raises(PreconditionFailed) as ours:
        provider.admit_host_source()
    with pytest.raises(PreconditionFailed) as reference:
        RefusingHostSourceAdmissionProvider().admit_host_source()
    assert ours.value.code == reference.value.code
    assert ours.value.code


def test_partial_inputs_are_a_construction_error() -> None:
    with pytest.raises(ValueError, match="all-or-nothing"):
        admission.Lane3HostSourceAdmissionProvider(expected_host_identity="host-1")


def test_wired_provider_forwards_exact_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = _signed_envelope(Ed25519PrivateKey.generate())
    installed = _signed_envelope(Ed25519PrivateKey.generate())
    policy = object()
    seen: dict[str, Any] = {}

    def fake_admit(**kwargs: Any) -> tuple[str, str]:
        seen.update(kwargs)
        return ("host-source", "trace")

    monkeypatch.setattr(admission, "_foundation_admit_host_source", fake_admit)
    provider = admission.Lane3HostSourceAdmissionProvider(
        candidate=candidate,
        installed=installed,
        trust_policy=policy,  # type: ignore[arg-type]
        expected_host_identity="host-1",
        expected_observation_id="obs-1",
        verification_context_digest="sha256:context",
        clock=lambda: _NOW,
    )

    assert provider.admit_host_source() == ("host-source", "trace")
    assert seen["candidate"] is candidate
    assert seen["installed"] is installed
    assert seen["trust_policy"] is policy
    assert seen["expected_host_identity"] == "host-1"
    assert seen["expected_observation_id"] == "obs-1"
    assert seen["expected_package"] == "dotmac-deployment-foundation"
    assert seen["verification_context_digest"] == "sha256:context"
    assert seen["now"] is _NOW
    assert isinstance(seen["verifier"], admission.Ed25519AttestationVerifier)


def test_verifier_accepts_a_genuine_signature_and_refuses_tampering() -> None:
    private_key = Ed25519PrivateKey.generate()
    envelope = _signed_envelope(private_key)
    public = private_key.public_key().public_bytes_raw()
    verifier = admission.Ed25519AttestationVerifier()

    assert verifier.verify(
        public_key=public,
        algorithm="ed25519",
        message=envelope.signed_bytes(),
        signature=envelope.signature,
    )
    assert not verifier.verify(
        public_key=public,
        algorithm="ed25519",
        message=envelope.signed_bytes() + b"x",
        signature=envelope.signature,
    )
    assert not verifier.verify(
        public_key=public,
        algorithm="hmac-sha256",
        message=envelope.signed_bytes(),
        signature=envelope.signature,
    )
    assert not verifier.verify(
        public_key=b"short",
        algorithm="ed25519",
        message=envelope.signed_bytes(),
        signature=envelope.signature,
    )
    assert not verifier.verify(
        public_key=public,
        algorithm="ed25519",
        message=envelope.signed_bytes(),
        signature="not-base64!!",
    )
