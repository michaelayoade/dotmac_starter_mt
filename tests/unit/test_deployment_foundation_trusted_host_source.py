"""V2 custody contract tests; these do not pretend that positive admission exists."""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import hmac
from datetime import UTC, datetime
from typing import ClassVar

import dotmac_deployment_foundation.trusted_host_source as trusted_host_source
import pytest
from dotmac_deployment_foundation.digest import Digest
from dotmac_deployment_foundation.errors import PreconditionFailed, SpecError
from dotmac_deployment_foundation.trusted_host_source import (
    CANDIDATE_ATTESTATION_PURPOSE,
    INSTALLED_OBSERVATION_PURPOSE,
    SAME_KEY_SIGNED_BOTH,
    AttestationEnvelopeV2,
    AttestationTrustPolicy,
    AttestationTrustRootV2,
    CandidateAttestationSubjectV2,
    InstalledHostAttestationSubjectV2,
    verify_attestation_pair,
)

NOW = datetime(2026, 9, 7, tzinfo=UTC)
CANDIDATE_KEY, HOST_KEY = b"candidate", b"host"
CANDIDATE_FP = "sha256:" + hashlib.sha256(CANDIDATE_KEY).hexdigest()
HOST_FP = "sha256:" + hashlib.sha256(HOST_KEY).hexdigest()
ALGORITHM = "ed25519"


class Verifier:
    keys: ClassVar[dict[str, bytes]] = {
        CANDIDATE_FP: CANDIDATE_KEY,
        HOST_FP: HOST_KEY,
    }

    def verify(
        self,
        *,
        public_key: bytes,
        algorithm: str,
        message: bytes,
        signature: str,
    ) -> bool:
        key = public_key
        return (
            algorithm == ALGORITHM
            and key is not None
            and hmac.compare_digest(
                signature, hmac.new(key, message, hashlib.sha256).hexdigest()
            )
        )


def _root(
    fp: str, purpose: str, domain: str, **changes: object
) -> AttestationTrustRootV2:
    values = {
        "public_key_fingerprint": fp,
        "public_key_base64": base64.b64encode(Verifier.keys[fp]).decode(),
        "purpose": purpose,
        "custody_domain": domain,
        "issuer": "test-issuer",
        "key_id": "rotatable-label",
        "algorithm": ALGORITHM,
        "trust_root_version": "control-v3",
        "not_before": "2026-01-01T00:00:00Z",
        "not_after": "2027-01-01T00:00:00Z",
    }
    values.update(changes)
    return AttestationTrustRootV2(**values)  # type: ignore[arg-type]


def _policy() -> AttestationTrustPolicy:
    return AttestationTrustPolicy(
        [_root(CANDIDATE_FP, CANDIDATE_ATTESTATION_PURPOSE, "starter-release")],
        [_root(HOST_FP, INSTALLED_OBSERVATION_PURPOSE, "target-local-host")],
        "starter-release-workflow",
        "host:canonical-a",
    )  # type: ignore[arg-type]


def _envelope(
    *, purpose: str, fp: str, domain: str, subject: dict[str, str], observation_id: str
) -> AttestationEnvelopeV2:
    audience = (
        "starter-release-workflow"
        if purpose == CANDIDATE_ATTESTATION_PURPOSE
        else "host:canonical-a"
    )
    envelope = AttestationEnvelopeV2(
        "TrustedHostAttestation.v2",
        purpose,
        "test-issuer",
        "rotatable-label",
        ALGORITHM,
        fp,
        domain,
        "control-v3",
        "2026-09-07T00:00:00Z",
        "2026-09-07T00:10:00Z",
        audience,
        observation_id,
        subject,
        "placeholder",
    )
    signature = hmac.new(
        Verifier.keys[fp], envelope.signed_bytes(), hashlib.sha256
    ).hexdigest()
    return dataclasses.replace(envelope, signature=signature)


def _pair() -> tuple[AttestationEnvelopeV2, AttestationEnvelopeV2]:
    candidate = CandidateAttestationSubjectV2(
        "dotmac-deployment-foundation",
        "0.4.0a2",
        Digest.parse("a" * 64, where="test"),
        "b" * 40,
        "dotmac/foundation",
        "123",
        "456",
    )
    digest = Digest.of(
        __import__("json")
        .dumps(candidate.canonical_document(), sort_keys=True, separators=(",", ":"))
        .encode()
    )
    host = InstalledHostAttestationSubjectV2(
        "host:canonical-a",
        candidate.package,
        candidate.version,
        candidate.wheel_sha256,
        digest,
    )
    return (
        _envelope(
            purpose=CANDIDATE_ATTESTATION_PURPOSE,
            fp=CANDIDATE_FP,
            domain="starter-release",
            subject=candidate.canonical_document(),
            observation_id="candidate-observation",
        ),
        _envelope(
            purpose=INSTALLED_OBSERVATION_PURPOSE,
            fp=HOST_FP,
            domain="target-local-host",
            subject=host.canonical_document(),
            observation_id="host-observation",
        ),
    )


def test_pair_binds_complete_candidate_to_expected_host() -> None:
    candidate, installed = _pair()
    assert (
        verify_attestation_pair(
            candidate=candidate,
            installed=installed,
            verifier=Verifier(),
            trust_policy=_policy(),
            expected_host_identity="host:canonical-a",
            now=NOW,
        )
        is None
    )


def test_subject_snapshot_does_not_change_after_input_mutation() -> None:
    _, installed = _pair()
    mutable_subject = dict(installed.subject_mapping())
    snapshot = dataclasses.replace(installed, subject=mutable_subject)
    before = snapshot.signed_bytes()
    mutable_subject["version"] = "caller-mutated"
    assert snapshot.signed_bytes() == before
    parsed = InstalledHostAttestationSubjectV2.from_mapping(snapshot.subject_mapping())
    assert parsed.version == "0.4.0a2"


@pytest.mark.parametrize(
    "candidate, installed, expected",
    [
        (None, "installed", "trusted-host-source-attestation-absent"),
        ("candidate", None, "trusted-host-source-attestation-absent"),
    ],
)
def test_missing_halves_refuse_distinctly_from_all_other_failures(
    candidate: object, installed: object, expected: str
) -> None:
    good_candidate, good_installed = _pair()
    with pytest.raises(PreconditionFailed) as raised:
        verify_attestation_pair(
            candidate=good_candidate if candidate else None,
            installed=good_installed if installed else None,
            verifier=Verifier(),
            trust_policy=_policy(),
            expected_host_identity="host:canonical-a",
            now=NOW,
        )
    assert raised.value.code == expected


def test_unknown_material_and_invalid_signature_have_exact_refusals() -> None:
    candidate, installed = _pair()
    unknown = dataclasses.replace(
        candidate, public_key_fingerprint="sha256:" + "e" * 64
    )
    with pytest.raises(PreconditionFailed) as raised:
        verify_attestation_pair(
            candidate=unknown,
            installed=installed,
            verifier=Verifier(),
            trust_policy=_policy(),
            expected_host_identity="host:canonical-a",
            now=NOW,
        )
    assert raised.value.code == "trusted-host-source-key-not-trusted"
    broken = dataclasses.replace(candidate, signature="not-a-signature")
    with pytest.raises(PreconditionFailed) as raised:
        verify_attestation_pair(
            candidate=broken,
            installed=installed,
            verifier=Verifier(),
            trust_policy=_policy(),
            expected_host_identity="host:canonical-a",
            now=NOW,
        )
    assert raised.value.code == "trusted-host-source-signature-invalid"


def test_same_fingerprint_refuses_before_even_invalid_policy_evaluation() -> None:
    candidate, installed = _pair()
    installed = dataclasses.replace(
        installed,
        public_key_fingerprint=CANDIDATE_FP,
        signature=hmac.new(
            CANDIDATE_KEY, installed.signed_bytes(), hashlib.sha256
        ).hexdigest(),
    )
    with pytest.raises(PreconditionFailed) as raised:
        verify_attestation_pair(
            candidate=candidate,
            installed=installed,
            verifier=Verifier(),
            trust_policy=None,  # type: ignore[arg-type]
            expected_host_identity="host:canonical-a",
            now=NOW,
        )
    assert raised.value.code == SAME_KEY_SIGNED_BOTH


def test_distinct_fingerprints_with_same_key_id_are_admitted() -> None:
    candidate, installed = _pair()
    assert candidate.key_id == installed.key_id
    assert candidate.public_key_fingerprint != installed.public_key_fingerprint
    assert (
        verify_attestation_pair(
            candidate=candidate,
            installed=installed,
            verifier=Verifier(),
            trust_policy=_policy(),
            expected_host_identity="host:canonical-a",
            now=NOW,
        )
        is None
    )


def test_policy_coerces_collection_and_refuses_same_material_under_other_id() -> None:
    roots = [_root(CANDIDATE_FP, CANDIDATE_ATTESTATION_PURPOSE, "starter-release")]
    policy = AttestationTrustPolicy(
        roots,
        [_root(HOST_FP, INSTALLED_OBSERVATION_PURPOSE, "target-local-host")],
        "starter-release-workflow",
        "host:canonical-a",
    )  # type: ignore[arg-type]
    roots.clear()
    assert len(policy.candidate_roots) == 1 and isinstance(
        policy.candidate_roots, tuple
    )
    with pytest.raises(SpecError, match="public material"):
        AttestationTrustPolicy(
            (_root(CANDIDATE_FP, CANDIDATE_ATTESTATION_PURPOSE, "starter-release"),),
            (_root(CANDIDATE_FP, INSTALLED_OBSERVATION_PURPOSE, "target-local-host"),),
            "starter-release-workflow",
            "host:canonical-a",
        )


def test_policy_refuses_shared_custody_even_with_distinct_material() -> None:
    with pytest.raises(SpecError, match="custody"):
        AttestationTrustPolicy(
            (_root(CANDIDATE_FP, CANDIDATE_ATTESTATION_PURPOSE, "shared"),),
            (_root(HOST_FP, INSTALLED_OBSERVATION_PURPOSE, "shared"),),
            "starter-release-workflow",
            "host:canonical-a",
        )


@pytest.mark.parametrize(
    "root_changes", [{"revoked": True}, {"not_after": "2026-02-01T00:00:00Z"}]
)
def test_revocation_and_validity_refuse(root_changes: dict[str, object]) -> None:
    candidate, installed = _pair()
    policy = AttestationTrustPolicy(
        (
            _root(
                CANDIDATE_FP,
                CANDIDATE_ATTESTATION_PURPOSE,
                "starter-release",
                **root_changes,
            ),
        ),
        (_root(HOST_FP, INSTALLED_OBSERVATION_PURPOSE, "target-local-host"),),
        "starter-release-workflow",
        "host:canonical-a",
    )
    with pytest.raises(PreconditionFailed):
        verify_attestation_pair(
            candidate=candidate,
            installed=installed,
            verifier=Verifier(),
            trust_policy=policy,
            expected_host_identity="host:canonical-a",
            now=NOW,
        )


def test_key_id_is_bound_by_the_enrolled_root_not_a_rotation_escape() -> None:
    candidate, installed = _pair()
    rotated = dataclasses.replace(candidate, key_id="a-different-label")
    rotated = dataclasses.replace(
        rotated,
        signature=hmac.new(
            Verifier.keys[CANDIDATE_FP], rotated.signed_bytes(), hashlib.sha256
        ).hexdigest(),
    )
    with pytest.raises(PreconditionFailed) as raised:
        verify_attestation_pair(
            candidate=rotated,
            installed=installed,
            verifier=Verifier(),
            trust_policy=_policy(),
            expected_host_identity="host:canonical-a",
            now=NOW,
        )
    assert raised.value.code == "trusted-host-source-root-binding-mismatch"


@pytest.mark.parametrize(
    "change, now, expected",
    [
        ("audience", NOW, "trusted-host-source-audience-mismatch"),
        ("issued_at", NOW, "trusted-host-source-future"),
        ("subject", NOW, "trusted-host-source-attestations-disagree"),
    ],
)
def test_audience_future_and_subject_mismatch_refuse(
    change: str, now: datetime, expected: str
) -> None:
    candidate, installed = _pair()
    if change == "audience":
        candidate = dataclasses.replace(candidate, audience="host:other")
    elif change == "issued_at":
        # Keep the envelope internally valid so verification reaches the
        # intended future-issued refusal instead of rejecting the interval.
        candidate = dataclasses.replace(
            candidate,
            issued_at="2026-09-08T00:00:00Z",
            expires_at="2026-09-08T00:10:00Z",
        )
    elif change == "subject":
        installed = dataclasses.replace(
            installed, subject={**installed.subject, "version": "other"}
        )
    candidate = dataclasses.replace(
        candidate,
        signature=hmac.new(
            Verifier.keys[CANDIDATE_FP], candidate.signed_bytes(), hashlib.sha256
        ).hexdigest(),
    )
    if change == "subject":
        installed = dataclasses.replace(
            installed,
            signature=hmac.new(
                Verifier.keys[HOST_FP], installed.signed_bytes(), hashlib.sha256
            ).hexdigest(),
        )
    with pytest.raises(PreconditionFailed) as raised:
        verify_attestation_pair(
            candidate=candidate,
            installed=installed,
            verifier=Verifier(),
            trust_policy=_policy(),
            expected_host_identity="host:canonical-a",
            now=now,
        )
    assert raised.value.code == expected


def test_no_preverified_result_or_v1_binding_is_public() -> None:
    assert not hasattr(trusted_host_source, "TrustedAttestationBinding")
    assert verify_attestation_pair.__annotations__["return"] in (None, "None")


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-09-07 00:00:00Z",
        "2026-09-07T00:00:00+00:00",
        "2026-09-07t00:00:00Z",
        "2026-09-07T00:00:00z",
    ],
)
def test_timestamp_aliases_are_refused(timestamp: str) -> None:
    with pytest.raises(SpecError) as raised:
        _root(
            CANDIDATE_FP,
            CANDIDATE_ATTESTATION_PURPOSE,
            "starter-release",
            not_before=timestamp,
        )
    assert raised.value.code == trusted_host_source.OBSERVATION_MALFORMED


def test_fractional_canonical_utc_timestamp_is_accepted() -> None:
    root = _root(
        CANDIDATE_FP,
        CANDIDATE_ATTESTATION_PURPOSE,
        "starter-release",
        not_before="2026-01-01T00:00:00.123456Z",
    )
    assert root.not_before.endswith(".123456Z")


@pytest.mark.parametrize("value", [None, [], "subject", {1: "non-string-key"}])
def test_external_subject_types_are_malformed(value: object) -> None:
    with pytest.raises(SpecError) as raised:
        CandidateAttestationSubjectV2.from_mapping(value)
    assert raised.value.code == trusted_host_source.OBSERVATION_MALFORMED


@pytest.mark.parametrize("coordinate", ["repository", "run_id", "artifact_id"])
def test_candidate_artifact_coordinates_are_required(coordinate: str) -> None:
    candidate, _ = _pair()
    subject = dict(candidate.subject_mapping())
    subject.pop(coordinate)
    with pytest.raises(SpecError) as raised:
        CandidateAttestationSubjectV2.from_mapping(subject)
    assert raised.value.code == trusted_host_source.OBSERVATION_MALFORMED


@pytest.mark.parametrize("digest", ["a" * 64, "sha256:" + "A" * 64])
def test_v2_wire_digests_have_one_canonical_spelling(digest: str) -> None:
    candidate, _ = _pair()
    subject = dict(candidate.subject_mapping())
    subject["wheel_sha256"] = digest
    with pytest.raises(SpecError) as raised:
        CandidateAttestationSubjectV2.from_mapping(subject)
    assert raised.value.code == trusted_host_source.OBSERVATION_MALFORMED


def test_public_key_bytes_must_match_the_declared_fingerprint() -> None:
    with pytest.raises(SpecError) as raised:
        _root(
            CANDIDATE_FP,
            CANDIDATE_ATTESTATION_PURPOSE,
            "starter-release",
            public_key_base64=base64.b64encode(HOST_KEY).decode(),
        )
    assert raised.value.code == trusted_host_source.OBSERVATION_MALFORMED


@pytest.mark.parametrize(
    "changes",
    [
        {"candidate": "not-an-envelope"},
        {"trust_policy": "not-a-policy"},
        {"verifier": object()},
        {"now": "not-an-instant"},
    ],
)
def test_malformed_verification_inputs_are_named(changes: dict[str, object]) -> None:
    candidate, installed = _pair()
    arguments: dict[str, object] = {
        "candidate": candidate,
        "installed": installed,
        "verifier": Verifier(),
        "trust_policy": _policy(),
        "expected_host_identity": "host:canonical-a",
        "now": NOW,
    }
    arguments.update(changes)
    with pytest.raises(SpecError) as raised:
        verify_attestation_pair(**arguments)  # type: ignore[arg-type]
    assert raised.value.code == trusted_host_source.OBSERVATION_MALFORMED
