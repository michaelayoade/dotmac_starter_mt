"""A REAL-admission provider, tampered, refuses through a real executor.

`tests/unit/host_source_stance.py`'s `AcceptingHostSourceAdmissionProvider`
is a purely synthetic sequencing double: its `admit_host_source()` returns a
made-up-but-internally-consistent pair without ever calling
`host_source_admission.admit_host_source()` or touching real attestation
evidence. That fixture proves post-gate sequencing; it proves nothing about
what happens when a REAL admission path is fed tampered evidence.

This file closes that gap. `RealAdmissionHostSourceProvider` below is a thin
`HostSourceAdmissionProvider` adapter whose `admit_host_source()` calls the
real, fully-parameterized `host_source_admission.admit_host_source()` with
attestation fixtures supplied at construction time. Each test hands one such
provider — fed with exactly one deliberately wrong fixture value — to a real
`RecoveryExecutor` and asserts the executor's `.run()` raises the EXACT
refusal code that `verify_attestation_pair`/`admit_host_source` name for that
tamper, with zero effectful steps run first.

Fixture construction (`Verifier`, `_pair`, `_policy`, `_root`, and the
signing constants) is reused directly from
`test_deployment_foundation_trusted_host_source.py`, which already exercises
each of these exact tamper shapes at the pure-function `verify_attestation_
pair` layer — this file adds no second, parallel way of constructing
`AttestationEnvelopeV2`/trust-root fixtures, only the executor-level
propagation proof.

Case 6 (`receipt=None` / no real admission via `RefusingHostSourceAdmission
Provider`) is NOT reproduced here: `test_deployment_foundation_host_source_
admission_provider.py::test_an_explicit_refusing_provider_refuses_the_
recovery_executor_too` already proves exactly that shape — an explicitly
supplied `RefusingHostSourceAdmissionProvider` refusing a `RecoveryExecutor`
with a host-source code and zero effects. Duplicating it here would be the
same assertion under a new name, not new coverage.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
from datetime import datetime

import pytest
from dotmac_deployment_foundation.errors import PreconditionFailed
from dotmac_deployment_foundation.host_source import HostSource
from dotmac_deployment_foundation.host_source_admission import (
    HostSourceAdmissionTrace,
    admit_host_source,
)
from dotmac_deployment_foundation.recovery_execution import RecoveryExecutor
from dotmac_deployment_foundation.trusted_host_source import (
    CANDIDATE_ATTESTATION_PURPOSE,
    INSTALLED_OBSERVATION_PURPOSE,
    OBSERVATION_ID_MISMATCH,
    PACKAGE_MISMATCH,
    ROOT_REVOKED,
    SAME_KEY_SIGNED_BOTH,
    SUBJECT_MISMATCH,
    AttestationEnvelopeV2,
    AttestationTrustPolicy,
    AttestationVerifier,
    InstalledHostAttestationSubjectV2,
)

from tests.unit.test_deployment_foundation_recovery_bundle import (
    _evidence,
    _manifest,
    _spec,
)
from tests.unit.test_deployment_foundation_recovery_execution import (
    IMAGE as RECOVERY_IMAGE,
)
from tests.unit.test_deployment_foundation_recovery_execution import (
    RecordingRecoveryEffects,
)
from tests.unit.test_deployment_foundation_trusted_host_source import (
    CANDIDATE_FP,
    CANDIDATE_KEY,
    HOST_FP,
    HOST_KEY,
    NOW,
    VERIFICATION_CONTEXT_DIGEST,
    Verifier,
    _install_stub_reading,
    _pair,
    _policy,
    _root,
)

EXPECTED_HOST_IDENTITY = "host:canonical-a"
EXPECTED_OBSERVATION_ID = "host-observation"
EXPECTED_PACKAGE = "dotmac-deployment-foundation"


@dataclasses.dataclass(slots=True)
class RealAdmissionHostSourceProvider:
    """Thin adapter: the zero-argument `HostSourceAdmissionProvider` method
    to the real, fully-parameterized `host_source_admission.admit_host_
    source()` function.

    Every field here is exactly one of that function's nine parameters,
    supplied at construction time so a test can hand this straight to a real
    `Executor`/`RecoveryExecutor` and let the real admission path run
    (and refuse) against deliberately tampered evidence — never a
    synthetic, self-consistent success like `AcceptingHostSourceAdmission
    Provider`.
    """

    candidate: AttestationEnvelopeV2 | None
    installed: AttestationEnvelopeV2 | None
    verifier: AttestationVerifier
    trust_policy: AttestationTrustPolicy
    expected_host_identity: str
    expected_observation_id: str
    expected_package: str
    verification_context_digest: str
    now: datetime

    def admit_host_source(self) -> tuple[HostSource, HostSourceAdmissionTrace]:
        return admit_host_source(
            candidate=self.candidate,
            installed=self.installed,
            verifier=self.verifier,
            trust_policy=self.trust_policy,
            expected_host_identity=self.expected_host_identity,
            expected_observation_id=self.expected_observation_id,
            expected_package=self.expected_package,
            verification_context_digest=self.verification_context_digest,
            now=self.now,
        )


def _real_recovery_executor(*, admission_provider: RealAdmissionHostSourceProvider):
    effects = RecordingRecoveryEffects()
    executor = RecoveryExecutor(
        _spec(),
        _manifest(),
        effects,
        source_evidence=_evidence(),
        product_image=RECOVERY_IMAGE,
        admission_provider=admission_provider,
    )
    return effects, executor


def _sign_installed(installed: AttestationEnvelopeV2) -> AttestationEnvelopeV2:
    return dataclasses.replace(
        installed,
        signature=hmac.new(
            HOST_KEY, installed.signed_bytes(), hashlib.sha256
        ).hexdigest(),
    )


# ── 1. wrong artifact: candidate/installed name a package that disagrees
# with what the executor expects ────────────────────────────────────────────


def test_wrong_artifact_refuses_with_package_mismatch_through_a_real_executor() -> None:
    """Confirmed by reading `host_source_admission.admit_host_source`
    (`verify_attestation_pair`'s `candidate_subject.package != expected_
    package` check): a package the executor did not expect refuses
    `PACKAGE_MISMATCH`, not `SUBJECT_MISMATCH` — the subject-mismatch codes
    are reserved for a binding/identity disagreement between the two
    attestations themselves, not a caller-expectation mismatch."""
    candidate, installed = _pair()
    provider = RealAdmissionHostSourceProvider(
        candidate=candidate,
        installed=installed,
        verifier=Verifier(),
        trust_policy=_policy(),
        expected_host_identity=EXPECTED_HOST_IDENTITY,
        expected_observation_id=EXPECTED_OBSERVATION_ID,
        expected_package="wrong-package",
        verification_context_digest=VERIFICATION_CONTEXT_DIGEST,
        now=NOW,
    )
    effects, executor = _real_recovery_executor(admission_provider=provider)
    with pytest.raises(PreconditionFailed) as refusal:
        executor.run(bundle={})
    assert refusal.value.code == PACKAGE_MISMATCH
    assert effects.calls == [], (
        "the gate did not fire before the first effect: " f"{effects.calls}"
    )


# ── 2. wrong host: the installed attestation's own subject names a host
# identity that disagrees with what the executor expects ───────────────────


def test_wrong_host_refuses_with_subject_mismatch_through_a_real_executor() -> None:
    """Confirmed by reading `verify_attestation_pair`'s `installed_subject.
    host_identity != expected_host_identity` check: this raises
    `SUBJECT_MISMATCH`, the SAME code the digest-binding mismatch uses
    (`test_deployment_foundation_trusted_host_source.py::test_installed_
    subject_digest_binding_mismatch_refuses_subject_mismatch`), because both
    are the installed subject disagreeing with an authenticated expectation.
    The installed envelope's own `audience` field is left untouched (still
    `host:canonical-a`), so this tampers the SUBJECT's `host_identity`, not
    the envelope's audience — otherwise `_verify` would refuse
    `AUDIENCE_MISMATCH` first, before the subject is ever compared."""
    candidate, installed = _pair()
    installed_subject = InstalledHostAttestationSubjectV2.from_mapping(
        installed.subject_mapping()
    )
    tampered_subject = dataclasses.replace(
        installed_subject, host_identity="host:wrong-host"
    )
    tampered_installed = dataclasses.replace(
        installed, subject=tampered_subject.canonical_document()
    )
    tampered_installed = _sign_installed(tampered_installed)

    provider = RealAdmissionHostSourceProvider(
        candidate=candidate,
        installed=tampered_installed,
        verifier=Verifier(),
        trust_policy=_policy(),
        expected_host_identity=EXPECTED_HOST_IDENTITY,
        expected_observation_id=EXPECTED_OBSERVATION_ID,
        expected_package=EXPECTED_PACKAGE,
        verification_context_digest=VERIFICATION_CONTEXT_DIGEST,
        now=NOW,
    )
    effects, executor = _real_recovery_executor(admission_provider=provider)
    with pytest.raises(PreconditionFailed) as refusal:
        executor.run(bundle={})
    assert refusal.value.code == SUBJECT_MISMATCH
    assert effects.calls == [], (
        "the gate did not fire before the first effect: " f"{effects.calls}"
    )


# ── 3. same-author evidence: candidate and installed signed by the same key ─


def test_same_author_evidence_refuses_with_same_key_signed_both() -> None:
    """Exact fixture pattern reused from `test_deployment_foundation_trusted_
    host_source.py::test_same_key_signed_both_survives_the_refactor_with_
    distinct_roots`: two UNRELATED roots in the policy (so
    `TRUST_ROOTS_NOT_DISTINCT` cannot fire at policy construction), but one
    signing key reused on both envelopes."""
    candidate, installed = _pair()
    installed_same_key = dataclasses.replace(
        installed,
        public_key_fingerprint=CANDIDATE_FP,
        signature=hmac.new(
            CANDIDATE_KEY, installed.signed_bytes(), hashlib.sha256
        ).hexdigest(),
    )
    provider = RealAdmissionHostSourceProvider(
        candidate=candidate,
        installed=installed_same_key,
        verifier=Verifier(),
        trust_policy=_policy(),
        expected_host_identity=EXPECTED_HOST_IDENTITY,
        expected_observation_id=EXPECTED_OBSERVATION_ID,
        expected_package=EXPECTED_PACKAGE,
        verification_context_digest=VERIFICATION_CONTEXT_DIGEST,
        now=NOW,
    )
    effects, executor = _real_recovery_executor(admission_provider=provider)
    with pytest.raises(PreconditionFailed) as refusal:
        executor.run(bundle={})
    assert refusal.value.code == SAME_KEY_SIGNED_BOTH
    assert effects.calls == [], (
        "the gate did not fire before the first effect: " f"{effects.calls}"
    )


# ── 4. revoked key: the candidate trust root is revoked ─────────────────────


def test_revoked_root_refuses_with_root_revoked() -> None:
    """Exact fixture pattern reused from `test_deployment_foundation_trusted_
    host_source.py::test_revocation_and_validity_refuse`'s `{"revoked":
    True}` case, applied to the candidate root."""
    candidate, installed = _pair()
    policy = AttestationTrustPolicy(
        (
            _root(
                CANDIDATE_FP,
                CANDIDATE_ATTESTATION_PURPOSE,
                "starter-release",
                revoked=True,
            ),
        ),
        (_root(HOST_FP, INSTALLED_OBSERVATION_PURPOSE, "target-local-host"),),
        "starter-release-workflow",
        EXPECTED_HOST_IDENTITY,
    )
    provider = RealAdmissionHostSourceProvider(
        candidate=candidate,
        installed=installed,
        verifier=Verifier(),
        trust_policy=policy,
        expected_host_identity=EXPECTED_HOST_IDENTITY,
        expected_observation_id=EXPECTED_OBSERVATION_ID,
        expected_package=EXPECTED_PACKAGE,
        verification_context_digest=VERIFICATION_CONTEXT_DIGEST,
        now=NOW,
    )
    effects, executor = _real_recovery_executor(admission_provider=provider)
    with pytest.raises(PreconditionFailed) as refusal:
        executor.run(bundle={})
    assert refusal.value.code == ROOT_REVOKED
    assert effects.calls == [], (
        "the gate did not fire before the first effect: " f"{effects.calls}"
    )


# ── 5. replay: the expected observation coordinate does not match what the
# installed attestation actually observed ───────────────────────────────────


def test_replay_refuses_with_observation_id_mismatch() -> None:
    """Confirmed by reading `verify_attestation_pair`'s `installed.
    observation_id != expected_observation_id` check
    (`OBSERVATION_ID_MISMATCH`, exact fixture pattern reused from
    `test_deployment_foundation_trusted_host_source.py::test_admission_
    coordinate_mismatch_refuses_before_host_read`'s `expected_observation_id`
    case): a caller expecting a fresh Control dispatch that does not match
    what the installed attestation actually observed — the shape a replayed
    or stale observation coordinate takes."""
    candidate, installed = _pair()
    provider = RealAdmissionHostSourceProvider(
        candidate=candidate,
        installed=installed,
        verifier=Verifier(),
        trust_policy=_policy(),
        expected_host_identity=EXPECTED_HOST_IDENTITY,
        expected_observation_id="stale-or-replayed-dispatch",
        expected_package=EXPECTED_PACKAGE,
        verification_context_digest=VERIFICATION_CONTEXT_DIGEST,
        now=NOW,
    )
    effects, executor = _real_recovery_executor(admission_provider=provider)
    with pytest.raises(PreconditionFailed) as refusal:
        executor.run(bundle={})
    assert refusal.value.code == OBSERVATION_ID_MISMATCH
    assert effects.calls == [], (
        "the gate did not fire before the first effect: " f"{effects.calls}"
    )


# ── the adapter itself satisfies the Protocol and returns real evidence on
# the one path where the fixture is left fully valid ────────────────────────


def test_real_admission_provider_satisfies_the_protocol_and_admits_when_valid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative-control / sensitivity check for the five refusal tests above:
    the SAME adapter, fed an untampered pair, actually admits a real
    `RecoveryExecutor` past the gate and reaches the first real effect —
    proving the five refusals above are real tamper detections and not the
    adapter always refusing regardless of its input. `_install_stub_reading`
    (reused from `test_deployment_foundation_trusted_host_source.py`)
    replaces only the real interpreter reading with one that agrees with the
    fabricated candidate subject — the same seam that file's own positive
    test stubs — never a fabricated `InstalledArtifact` bypassing real
    parsing."""
    from dotmac_deployment_foundation.host_source_admission import (
        HostSourceAdmissionProvider,
    )

    _install_stub_reading(monkeypatch)
    candidate, installed = _pair()
    provider = RealAdmissionHostSourceProvider(
        candidate=candidate,
        installed=installed,
        verifier=Verifier(),
        trust_policy=_policy(),
        expected_host_identity=EXPECTED_HOST_IDENTITY,
        expected_observation_id=EXPECTED_OBSERVATION_ID,
        expected_package=EXPECTED_PACKAGE,
        verification_context_digest=VERIFICATION_CONTEXT_DIGEST,
        now=NOW,
    )
    assert isinstance(provider, HostSourceAdmissionProvider)

    effects, executor = _real_recovery_executor(admission_provider=provider)
    executor.run(bundle={})
    assert executor._host_source is not None
    assert isinstance(executor._host_source_admission_trace, HostSourceAdmissionTrace)
    assert "create_fresh_target" in effects.calls, (
        "the real admission path admitted the executor but the real first "
        f"effect was never reached: {effects.calls}"
    )
