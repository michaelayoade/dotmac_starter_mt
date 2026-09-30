"""V2 custody contract tests; these do not pretend that positive admission exists."""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import ClassVar

import dotmac_deployment_foundation.host_source_admission as host_source_admission
import dotmac_deployment_foundation.trusted_host_source as trusted_host_source
import pytest
from dotmac_deployment_foundation.digest import Digest
from dotmac_deployment_foundation.errors import PreconditionFailed, SpecError
from dotmac_deployment_foundation.host_source import (
    DISAGREES,
    DISTRIBUTION,
    InstalledArtifact,
    read_installed_artifact,
)
from dotmac_deployment_foundation.host_source_admission import (
    HostSourceAdmissionTrace,
    admit_host_source,
)
from dotmac_deployment_foundation.trusted_host_source import (
    CANDIDATE_ATTESTATION_PURPOSE,
    INSTALLED_OBSERVATION_PURPOSE,
    SAME_KEY_SIGNED_BOTH,
    AttestationEnvelopeV2,
    AttestationPairVerificationResultV1,
    AttestationTrustPolicy,
    AttestationTrustRootV2,
    CandidateAttestationSubjectV2,
    InstalledHostAttestationSubjectV2,
    attestation_envelope_digest,
    candidate_subject_digest,
    verify_attestation_pair,
    verify_candidate_attestation,
)

NOW = datetime(2026, 9, 7, tzinfo=UTC)
CANDIDATE_KEY, HOST_KEY = b"candidate", b"host"
CANDIDATE_FP = "sha256:" + hashlib.sha256(CANDIDATE_KEY).hexdigest()
HOST_FP = "sha256:" + hashlib.sha256(HOST_KEY).hexdigest()
ALGORITHM = "ed25519"
#: Opaque, blind-echoed value — Foundation never parses or interprets this;
#: it is Control's own value, carried through unchanged (see
#: AttestationPairVerificationResultV1).
VERIFICATION_CONTEXT_DIGEST = "sha256:" + "ab" * 32


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
    result = verify_attestation_pair(
        candidate=candidate,
        installed=installed,
        verifier=Verifier(),
        trust_policy=_policy(),
        expected_host_identity="host:canonical-a",
        expected_observation_id="host-observation",
        expected_package="dotmac-deployment-foundation",
        verification_context_digest=VERIFICATION_CONTEXT_DIGEST,
        now=NOW,
    )
    assert isinstance(result, AttestationPairVerificationResultV1)


def test_pair_result_echoes_digest_and_carries_real_computed_envelope_digests() -> None:
    """Fixed-vector positive test: the returned result's envelope digests
    must equal independently-computed digests of the SAME parsed envelope
    objects — proving they are the real computed values, not a copy of any
    presentation-supplied input — and the context digest must be echoed back
    byte-for-byte, proving the blind-echo contract."""
    candidate, installed = _pair()
    expected_candidate_digest = str(attestation_envelope_digest(candidate))
    expected_installed_digest = str(attestation_envelope_digest(installed))
    result = verify_attestation_pair(
        candidate=candidate,
        installed=installed,
        verifier=Verifier(),
        trust_policy=_policy(),
        expected_host_identity="host:canonical-a",
        expected_observation_id="host-observation",
        expected_package="dotmac-deployment-foundation",
        verification_context_digest=VERIFICATION_CONTEXT_DIGEST,
        now=NOW,
    )
    assert result.verification_context_digest == VERIFICATION_CONTEXT_DIGEST
    assert result.candidate_attestation_envelope_digest == expected_candidate_digest
    assert result.installed_attestation_envelope_digest == expected_installed_digest
    # The widened result reports EVERYTHING actually verified -- the real
    # expectations checked, the real trust-policy audiences, and the real
    # matched roots -- not placeholders and not a caller-supplied echo.
    policy = _policy()
    assert result.expected_host_identity == "host:canonical-a"
    assert result.expected_observation_id == "host-observation"
    assert result.expected_package == "dotmac-deployment-foundation"
    assert result.candidate_audience == policy.candidate_audience
    assert result.installed_audience == policy.installed_audience
    candidate_root = policy.candidate_roots[0]
    installed_root = policy.installed_roots[0]
    assert result.candidate_root == trusted_host_source.AttestationVerifiedRootV1(
        public_key_fingerprint=candidate_root.public_key_fingerprint,
        trust_root_version=candidate_root.trust_root_version,
        key_id=candidate_root.key_id,
        algorithm=candidate_root.algorithm,
        purpose=candidate_root.purpose,
        custody_domain=candidate_root.custody_domain,
        issuer=candidate_root.issuer,
    )
    assert result.installed_root == trusted_host_source.AttestationVerifiedRootV1(
        public_key_fingerprint=installed_root.public_key_fingerprint,
        trust_root_version=installed_root.trust_root_version,
        key_id=installed_root.key_id,
        algorithm=installed_root.algorithm,
        purpose=installed_root.purpose,
        custody_domain=installed_root.custody_domain,
        issuer=installed_root.issuer,
    )


@pytest.mark.parametrize("bad_value", ["", None])
def test_verification_context_digest_is_required(bad_value: object) -> None:
    candidate, installed = _pair()
    with pytest.raises(SpecError) as raised:
        verify_attestation_pair(
            candidate=candidate,
            installed=installed,
            verifier=Verifier(),
            trust_policy=_policy(),
            expected_host_identity="host:canonical-a",
            expected_observation_id="host-observation",
            expected_package="dotmac-deployment-foundation",
            verification_context_digest=bad_value,  # type: ignore[arg-type]
            now=NOW,
        )
    assert raised.value.code == trusted_host_source.OBSERVATION_MALFORMED


def test_attestation_envelope_digest_fixed_vector_and_signature_sensitive() -> None:
    candidate, _ = _pair()
    assert str(attestation_envelope_digest(candidate)) == (
        "sha256:24179cc1b3932bf768b07bdeb4eb2f18eacdfa0ebd7484da361186c7929e38b1"
    )
    changed = dataclasses.replace(candidate, signature="changed")
    assert attestation_envelope_digest(changed) != attestation_envelope_digest(
        candidate
    )


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        (
            "expected_observation_id",
            "wrong-dispatch",
            trusted_host_source.OBSERVATION_ID_MISMATCH,
        ),
        ("expected_package", "wrong-package", trusted_host_source.PACKAGE_MISMATCH),
    ],
)
def test_pair_refuses_coordinate_substitution(
    field: str, value: str, code: str
) -> None:
    candidate, installed = _pair()
    kwargs = {
        "candidate": candidate,
        "installed": installed,
        "verifier": Verifier(),
        "trust_policy": _policy(),
        "expected_host_identity": "host:canonical-a",
        "expected_observation_id": "host-observation",
        "expected_package": "dotmac-deployment-foundation",
        "verification_context_digest": VERIFICATION_CONTEXT_DIGEST,
        "now": NOW,
    }
    kwargs[field] = value
    with pytest.raises(PreconditionFailed) as raised:
        verify_attestation_pair(**kwargs)  # type: ignore[arg-type]
    assert raised.value.code == code


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
            expected_observation_id="host-observation",
            expected_package="dotmac-deployment-foundation",
            verification_context_digest=VERIFICATION_CONTEXT_DIGEST,
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
            expected_observation_id="host-observation",
            expected_package="dotmac-deployment-foundation",
            verification_context_digest=VERIFICATION_CONTEXT_DIGEST,
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
            expected_observation_id="host-observation",
            expected_package="dotmac-deployment-foundation",
            verification_context_digest=VERIFICATION_CONTEXT_DIGEST,
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
            expected_observation_id="host-observation",
            expected_package="dotmac-deployment-foundation",
            verification_context_digest=VERIFICATION_CONTEXT_DIGEST,
            now=NOW,
        )
    assert raised.value.code == SAME_KEY_SIGNED_BOTH


def test_distinct_fingerprints_with_same_key_id_are_admitted() -> None:
    candidate, installed = _pair()
    assert candidate.key_id == installed.key_id
    assert candidate.public_key_fingerprint != installed.public_key_fingerprint
    result = verify_attestation_pair(
        candidate=candidate,
        installed=installed,
        verifier=Verifier(),
        trust_policy=_policy(),
        expected_host_identity="host:canonical-a",
        expected_observation_id="host-observation",
        expected_package="dotmac-deployment-foundation",
        verification_context_digest=VERIFICATION_CONTEXT_DIGEST,
        now=NOW,
    )
    assert isinstance(result, AttestationPairVerificationResultV1)


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
            expected_observation_id="host-observation",
            expected_package="dotmac-deployment-foundation",
            verification_context_digest=VERIFICATION_CONTEXT_DIGEST,
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
            expected_observation_id="host-observation",
            expected_package="dotmac-deployment-foundation",
            verification_context_digest=VERIFICATION_CONTEXT_DIGEST,
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
            expected_observation_id="host-observation",
            expected_package="dotmac-deployment-foundation",
            verification_context_digest=VERIFICATION_CONTEXT_DIGEST,
            now=now,
        )
    assert raised.value.code == expected


def test_no_preverified_result_or_v1_binding_is_public() -> None:
    assert not hasattr(trusted_host_source, "TrustedAttestationBinding")
    assert verify_attestation_pair.__annotations__["return"] in (
        AttestationPairVerificationResultV1,
        "AttestationPairVerificationResultV1",
    )


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
        "expected_observation_id": "host-observation",
        "expected_package": "dotmac-deployment-foundation",
        "verification_context_digest": VERIFICATION_CONTEXT_DIGEST,
        "now": NOW,
    }
    arguments.update(changes)
    with pytest.raises(SpecError) as raised:
        verify_attestation_pair(**arguments)  # type: ignore[arg-type]
    assert raised.value.code == trusted_host_source.OBSERVATION_MALFORMED


# ── the candidate-verification seam ─────────────────────────────────────────


def test_seam_returns_the_authenticated_candidate_subject() -> None:
    """A trusted host workload gets the verified subject back, not a bare
    envelope and not something it handed in itself."""
    candidate, _ = _pair()
    subject = verify_candidate_attestation(
        candidate=candidate, verifier=Verifier(), trust_policy=_policy(), now=NOW
    )
    assert isinstance(subject, CandidateAttestationSubjectV2)
    assert subject.package == "dotmac-deployment-foundation"
    assert subject.version == "0.4.0a2"


def test_seam_negative_control_refuses_a_key_outside_the_resolved_roots() -> None:
    """A valid-looking envelope signed by a key not in the Control-resolved
    candidate roots is refused, not silently trusted."""
    candidate, _ = _pair()
    outside_root_key = dataclasses.replace(
        candidate, public_key_fingerprint="sha256:" + "e" * 64
    )
    with pytest.raises(PreconditionFailed) as raised:
        verify_candidate_attestation(
            candidate=outside_root_key,
            verifier=Verifier(),
            trust_policy=_policy(),
            now=NOW,
        )
    assert raised.value.code == "trusted-host-source-key-not-trusted"


def test_seam_signature_has_no_parameter_a_caller_could_use_to_bypass_it() -> None:
    """The signature itself, not a docstring, makes the bypass inexpressible:
    no subject, receipt, digest, or bare-root parameter exists to smuggle a
    caller-chosen fact past verification."""
    import inspect

    parameters = set(inspect.signature(verify_candidate_attestation).parameters)
    assert parameters == {"candidate", "verifier", "trust_policy", "now"}
    for forbidden in ("subject", "receipt", "digest", "root", "roots"):
        assert forbidden not in parameters


def test_module_carries_no_mutable_trust_state_a_caller_could_poison() -> None:
    """There is no module-level mutable (a registry, a cache, a default root
    list) through which a caller could inject trust material instead of
    going through the resolved `AttestationTrustPolicy` parameter."""
    for name, value in vars(trusted_host_source).items():
        if name.startswith("_") or not isinstance(value, list | dict | set):
            continue
        pytest.fail(f"unexpected module-level mutable {name!r} = {value!r}")


def test_pair_reuses_the_seam_rather_than_a_parallel_implementation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`verify_attestation_pair` must call the shared seam for its candidate
    half. Replacing the seam with a spy proves the call happens and its
    return value is what feeds the rest of the pair check, rather than the
    pair re-deriving the subject some other way.

    The shared seam is `_verify_candidate_and_root` -- the ONE candidate
    verification code path both the public `verify_candidate_attestation`
    wrapper and `verify_attestation_pair` call through (see that function's
    docstring)."""
    candidate, installed = _pair()
    calls: list[dict[str, object]] = []
    real_seam = trusted_host_source._verify_candidate_and_root

    def spy(
        **kwargs: object,
    ) -> tuple[
        CandidateAttestationSubjectV2, trusted_host_source.AttestationTrustRootV2
    ]:
        calls.append(kwargs)
        return real_seam(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(trusted_host_source, "_verify_candidate_and_root", spy)
    result = trusted_host_source.verify_attestation_pair(
        candidate=candidate,
        installed=installed,
        verifier=Verifier(),
        trust_policy=_policy(),
        expected_host_identity="host:canonical-a",
        expected_observation_id="host-observation",
        expected_package="dotmac-deployment-foundation",
        verification_context_digest=VERIFICATION_CONTEXT_DIGEST,
        now=NOW,
    )
    assert isinstance(result, AttestationPairVerificationResultV1)
    assert len(calls) == 1
    assert calls[0]["candidate"] is candidate

    # If the spy returns a WRONG subject, the pair's own downstream digest
    # binding check must be the thing that catches it -- proving the pair
    # actually consumes the seam's return value rather than recomputing an
    # equivalent subject in parallel.
    def wrong_subject_spy(
        **kwargs: object,
    ) -> tuple[
        CandidateAttestationSubjectV2, trusted_host_source.AttestationTrustRootV2
    ]:
        subject, root = real_seam(**kwargs)  # type: ignore[arg-type]
        return dataclasses.replace(subject, version="tampered"), root

    monkeypatch.setattr(
        trusted_host_source, "_verify_candidate_and_root", wrong_subject_spy
    )
    with pytest.raises(PreconditionFailed) as raised:
        trusted_host_source.verify_attestation_pair(
            candidate=candidate,
            installed=installed,
            verifier=Verifier(),
            trust_policy=_policy(),
            expected_host_identity="host:canonical-a",
            expected_observation_id="host-observation",
            expected_package="dotmac-deployment-foundation",
            verification_context_digest=VERIFICATION_CONTEXT_DIGEST,
            now=NOW,
        )
    assert raised.value.code == "trusted-host-source-subject-mismatch"


def test_a_defect_in_shared_verification_surfaces_in_both_entry_points() -> None:
    """Plant: neuter the crypto seam so it accepts anything. If the seam and
    `verify_attestation_pair` shared one code path, the SAME defect makes
    BOTH wrongly accept a bad signature. Two independent implementations
    could disagree; this one plant proves they do not exist here."""
    candidate, installed = _pair()
    bad_signature = dataclasses.replace(candidate, signature="not-a-signature")

    # Sensitivity control: with the real verifier, both refuse.
    with pytest.raises(PreconditionFailed) as seam_control:
        verify_candidate_attestation(
            candidate=bad_signature,
            verifier=Verifier(),
            trust_policy=_policy(),
            now=NOW,
        )
    assert seam_control.value.code == "trusted-host-source-signature-invalid"
    with pytest.raises(PreconditionFailed) as pair_control:
        verify_attestation_pair(
            candidate=bad_signature,
            installed=installed,
            verifier=Verifier(),
            trust_policy=_policy(),
            expected_host_identity="host:canonical-a",
            expected_observation_id="host-observation",
            expected_package="dotmac-deployment-foundation",
            verification_context_digest=VERIFICATION_CONTEXT_DIGEST,
            now=NOW,
        )
    assert pair_control.value.code == "trusted-host-source-signature-invalid"

    class AlwaysTrueVerifier:
        def verify(self, **_: object) -> bool:
            return True

    subject = verify_candidate_attestation(
        candidate=bad_signature,
        verifier=AlwaysTrueVerifier(),
        trust_policy=_policy(),
        now=NOW,
    )
    assert subject.package == "dotmac-deployment-foundation"
    result = verify_attestation_pair(
        candidate=bad_signature,
        installed=installed,
        verifier=AlwaysTrueVerifier(),
        trust_policy=_policy(),
        expected_host_identity="host:canonical-a",
        expected_observation_id="host-observation",
        expected_package="dotmac-deployment-foundation",
        verification_context_digest=VERIFICATION_CONTEXT_DIGEST,
        now=NOW,
    )
    assert isinstance(result, AttestationPairVerificationResultV1)


def test_no_parallel_candidate_verification_call_site_exists() -> None:
    """Static guard: `verify_attestation_pair` must call the shared seam
    (`_verify_candidate_and_root`) rather than repeating an inline
    `_verify(..., CANDIDATE_ATTESTATION_PURPOSE, ...)` call. Fails if a
    future change reintroduces a second, parallel candidate-authentication
    code path."""
    import inspect

    pair_source = inspect.getsource(verify_attestation_pair)
    assert "_verify_candidate_and_root(" in pair_source
    assert "CANDIDATE_ATTESTATION_PURPOSE" not in pair_source

    module_source = inspect.getsource(trusted_host_source)
    # Exactly one call-site use as an argument (the declaration and the
    # __all__ / import entries are the only other legitimate occurrences).
    assert module_source.count("        CANDIDATE_ATTESTATION_PURPOSE,\n") == 1


def test_expired_candidate_attestation_refuses_stale() -> None:
    """Not previously covered in this file: `now` strictly after the
    candidate's own `expires_at` must refuse STALE, before the installed half
    is even reached."""
    candidate, installed = _pair()
    after_expiry = datetime(2026, 9, 7, 1, 0, 0, tzinfo=UTC)
    with pytest.raises(PreconditionFailed) as raised:
        verify_attestation_pair(
            candidate=candidate,
            installed=installed,
            verifier=Verifier(),
            trust_policy=_policy(),
            expected_host_identity="host:canonical-a",
            expected_observation_id="host-observation",
            expected_package="dotmac-deployment-foundation",
            verification_context_digest=VERIFICATION_CONTEXT_DIGEST,
            now=after_expiry,
        )
    assert raised.value.code == trusted_host_source.STALE


def test_installed_subject_digest_binding_mismatch_refuses_subject_mismatch() -> None:
    """Not previously covered in this file: a genuine binding mismatch — the
    installed subject's own `candidate_subject_digest` does not match the one
    actually computed from the authenticated candidate subject — while
    `package`/`version`/`wheel_sha256` still agree between the two subjects,
    so ATTESTATIONS_DISAGREE cannot be the thing that fires instead."""
    candidate, installed = _pair()
    installed_subject = InstalledHostAttestationSubjectV2.from_mapping(
        installed.subject_mapping()
    )
    tampered_subject = dataclasses.replace(
        installed_subject,
        candidate_subject_digest=Digest.parse("f" * 64, where="test"),
    )
    tampered_installed = dataclasses.replace(
        installed, subject=tampered_subject.canonical_document()
    )
    tampered_installed = dataclasses.replace(
        tampered_installed,
        signature=hmac.new(
            HOST_KEY, tampered_installed.signed_bytes(), hashlib.sha256
        ).hexdigest(),
    )
    with pytest.raises(PreconditionFailed) as raised:
        verify_attestation_pair(
            candidate=candidate,
            installed=tampered_installed,
            verifier=Verifier(),
            trust_policy=_policy(),
            expected_host_identity="host:canonical-a",
            expected_observation_id="host-observation",
            expected_package="dotmac-deployment-foundation",
            verification_context_digest=VERIFICATION_CONTEXT_DIGEST,
            now=NOW,
        )
    assert raised.value.code == trusted_host_source.SUBJECT_MISMATCH


def test_same_key_signed_both_survives_the_refactor_with_distinct_roots() -> None:
    """Re-proves SAME_KEY_SIGNED_BOTH after the refactor. Building a policy
    from one shared key trips TRUST_ROOTS_NOT_DISTINCT at construction
    before verify_attestation_pair ever runs, so this uses two UNRELATED
    roots in the policy but reuses one signing key on both envelopes."""
    candidate, installed = _pair()
    installed_same_key = dataclasses.replace(
        installed,
        public_key_fingerprint=CANDIDATE_FP,
        signature=hmac.new(
            CANDIDATE_KEY, installed.signed_bytes(), hashlib.sha256
        ).hexdigest(),
    )
    with pytest.raises(PreconditionFailed) as raised:
        verify_attestation_pair(
            candidate=candidate,
            installed=installed_same_key,
            verifier=Verifier(),
            trust_policy=_policy(),
            expected_host_identity="host:canonical-a",
            expected_observation_id="host-observation",
            expected_package="dotmac-deployment-foundation",
            verification_context_digest=VERIFICATION_CONTEXT_DIGEST,
            now=NOW,
        )
    assert raised.value.code == SAME_KEY_SIGNED_BOTH


# ── admit_host_source — synthetic, unwired ──────────────────────────────────


class _StubInstalledMetadata:
    """A fake INSTALLATION, not a fake read: real `read_installed_artifact`
    parsing (RECORD, direct_url.json) runs unchanged against this reader."""

    def __init__(self, *, version: str, digest_hex: str) -> None:
        self._version = version
        self._digest_hex = digest_hex

    def version(self, distribution: str) -> str:
        return self._version

    def read_text(self, distribution: str, filename: str) -> str | None:
        if filename == "RECORD":
            return "dotmac_deployment_foundation/__init__.py,sha256=abc123,10\n"
        if filename == "direct_url.json":
            return json.dumps(
                {"archive_info": {"hashes": {"sha256": self._digest_hex}}}
            )
        return None


def _install_stub_reading(
    monkeypatch: pytest.MonkeyPatch,
    *,
    distribution: str = DISTRIBUTION,
    version: str = "0.4.0a2",
    digest_hex: str = "a" * 64,
) -> None:
    """Replace `admit_host_source`'s call site with the REAL
    `read_installed_artifact`, stubbed only at the `metadata=`/`distribution`
    seams that function already exposes for tests — never a fabricated
    `InstalledArtifact` bypassing its own parsing, and never a new parameter
    on `admit_host_source` itself."""
    stub_metadata = _StubInstalledMetadata(version=version, digest_hex=digest_hex)
    monkeypatch.setattr(
        host_source_admission,
        "read_installed_artifact",
        lambda: read_installed_artifact(distribution, metadata=stub_metadata),
    )


def test_admit_host_source_binds_verified_pair_to_the_installed_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate, installed = _pair()
    _install_stub_reading(monkeypatch)
    authenticated_candidate = verify_candidate_attestation(
        candidate=candidate, verifier=Verifier(), trust_policy=_policy(), now=NOW
    )

    host_source, trace = admit_host_source(
        candidate=candidate,
        installed=installed,
        verifier=Verifier(),
        trust_policy=_policy(),
        expected_host_identity="host:canonical-a",
        expected_observation_id="host-observation",
        expected_package="dotmac-deployment-foundation",
        verification_context_digest=VERIFICATION_CONTEXT_DIGEST,
        now=NOW,
    )

    assert host_source.distribution == authenticated_candidate.package
    assert host_source.version == authenticated_candidate.version
    assert host_source.artifact_digest == authenticated_candidate.wheel_sha256
    assert host_source.source_revision == authenticated_candidate.source_revision
    assert host_source.repository == authenticated_candidate.repository
    assert host_source.run_id == authenticated_candidate.run_id
    assert host_source.artifact_id == authenticated_candidate.artifact_id
    assert host_source.read_from == "direct_url.json archive_info.hashes.sha256"

    assert trace.candidate_subject_digest == candidate_subject_digest(
        authenticated_candidate
    )
    assert trace.host_observation_id == installed.observation_id
    assert trace.host_identity == "host:canonical-a"
    assert trace.candidate_signer_fingerprint == candidate.public_key_fingerprint
    assert trace.candidate_trust_root_version == candidate.trust_root_version
    assert trace.installed_signer_fingerprint == installed.public_key_fingerprint
    assert trace.installed_trust_root_version == installed.trust_root_version


def test_host_source_admission_trace_is_frozen_and_slotted() -> None:
    trace = HostSourceAdmissionTrace(
        candidate_subject_digest=Digest.parse("a" * 64, where="test"),
        host_observation_id="host-observation",
        host_identity="host:canonical-a",
        candidate_signer_fingerprint=CANDIDATE_FP,
        candidate_trust_root_version="control-v3",
        installed_signer_fingerprint=HOST_FP,
        installed_trust_root_version="control-v3",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        trace.host_identity = "other"  # type: ignore[misc]
    assert not hasattr(trace, "__dict__")


@pytest.mark.parametrize(
    "field, distribution, version, digest_hex",
    [
        ("distribution", "a-different-distribution", "0.4.0a2", "a" * 64),
        ("version", DISTRIBUTION, "9.9.9", "a" * 64),
        ("artifact_digest", DISTRIBUTION, "0.4.0a2", "b" * 64),
    ],
)
def test_admit_host_source_refuses_when_the_real_reading_disagrees(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    distribution: str,
    version: str,
    digest_hex: str,
) -> None:
    candidate, installed = _pair()
    _install_stub_reading(
        monkeypatch, distribution=distribution, version=version, digest_hex=digest_hex
    )
    with pytest.raises(PreconditionFailed) as raised:
        admit_host_source(
            candidate=candidate,
            installed=installed,
            verifier=Verifier(),
            trust_policy=_policy(),
            expected_host_identity="host:canonical-a",
            expected_observation_id="host-observation",
            expected_package="dotmac-deployment-foundation",
            verification_context_digest=VERIFICATION_CONTEXT_DIGEST,
            now=NOW,
        )
    assert raised.value.code == DISAGREES
    assert field in str(raised.value)


def test_admit_host_source_never_reads_the_interpreter_before_pair_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plant: a same-key-signed pair must be refused by `verify_attestation_
    pair` before `read_installed_artifact` is ever called. The stand-in fails
    the test outright if it is invoked at all."""
    candidate, installed = _pair()
    installed_same_key = dataclasses.replace(
        installed,
        public_key_fingerprint=CANDIDATE_FP,
        signature=hmac.new(
            CANDIDATE_KEY, installed.signed_bytes(), hashlib.sha256
        ).hexdigest(),
    )

    def _must_not_be_called() -> InstalledArtifact:
        pytest.fail(
            "read_installed_artifact must not run before pair verification succeeds"
        )

    monkeypatch.setattr(
        host_source_admission, "read_installed_artifact", _must_not_be_called
    )
    with pytest.raises(PreconditionFailed) as raised:
        admit_host_source(
            candidate=candidate,
            installed=installed_same_key,
            verifier=Verifier(),
            trust_policy=_policy(),
            expected_host_identity="host:canonical-a",
            expected_observation_id="host-observation",
            expected_package="dotmac-deployment-foundation",
            verification_context_digest=VERIFICATION_CONTEXT_DIGEST,
            now=NOW,
        )
    assert raised.value.code == SAME_KEY_SIGNED_BOTH


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        (
            "expected_observation_id",
            "wrong-dispatch",
            trusted_host_source.OBSERVATION_ID_MISMATCH,
        ),
        (
            "expected_package",
            "wrong-package",
            trusted_host_source.PACKAGE_MISMATCH,
        ),
    ],
)
def test_admission_coordinate_mismatch_refuses_before_host_read(
    monkeypatch: pytest.MonkeyPatch, field: str, value: str, code: str
) -> None:
    candidate, installed = _pair()

    def _must_not_be_called() -> InstalledArtifact:
        pytest.fail("host artifact read must follow coordinate verification")

    monkeypatch.setattr(
        host_source_admission, "read_installed_artifact", _must_not_be_called
    )
    kwargs = {
        "candidate": candidate,
        "installed": installed,
        "verifier": Verifier(),
        "trust_policy": _policy(),
        "expected_host_identity": "host:canonical-a",
        "expected_observation_id": "host-observation",
        "expected_package": "dotmac-deployment-foundation",
        "verification_context_digest": VERIFICATION_CONTEXT_DIGEST,
        "now": NOW,
    }
    kwargs[field] = value
    with pytest.raises(PreconditionFailed) as raised:
        admit_host_source(**kwargs)  # type: ignore[arg-type]
    assert raised.value.code == code


def test_admit_host_source_signature_has_exactly_the_documented_parameters() -> None:
    import inspect

    parameters = set(inspect.signature(admit_host_source).parameters)
    assert parameters == {
        "candidate",
        "installed",
        "verifier",
        "trust_policy",
        "expected_host_identity",
        "expected_observation_id",
        "expected_package",
        "verification_context_digest",
        "now",
    }
    for forbidden in (
        "receipt",
        "subject",
        "metadata",
        "installed_artifact",
        "distribution",
        "digest",
        "root",
        "roots",
    ):
        assert forbidden not in parameters


def test_admit_host_source_and_trace_are_exported_from_the_top_level_package() -> None:
    import dotmac_deployment_foundation as package

    assert "admit_host_source" in package.__all__
    assert "HostSourceAdmissionTrace" in package.__all__
    assert package.admit_host_source is admit_host_source
    assert package.HostSourceAdmissionTrace is HostSourceAdmissionTrace
    assert package.attestation_envelope_digest is attestation_envelope_digest
