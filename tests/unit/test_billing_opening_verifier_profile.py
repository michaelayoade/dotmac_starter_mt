"""D-8: the Starter-native verifier is composed from a pinned trust profile."""

from __future__ import annotations

from dataclasses import replace

import pytest
from dotmac_billing.opening_contracts import (
    OpeningEvidenceError,
    OpeningVerificationUnavailable,
)
from dotmac_billing.opening_receivable_contracts import OpeningImportVerificationV2
from dotmac_billing.opening_receivable_validation import (
    preview_opening_account_import_v2,
)
from dotmac_billing.opening_verification import (
    ProfileBoundAuthorityReaderV1,
    StarterNativeOpeningImportVerifierV2,
    build_starter_native_opening_import_verifier_v2,
    opening_verifier_profile_v1,
)

from tests.unit import test_billing_opening_qualification as qualification
from tests.unit.test_billing_opening_verification import TrustedPorts

AUDIENCE = "billing-test-audience"


def profile_for(ports, **over):
    values = {
        "name": "production",
        "scope": ports.command.scope,
        "installation_id": ports.policy.installation_id,
        "audience": AUDIENCE,
        "source_key_fingerprint": ports.policy.source_key.public_key_fingerprint,
        "grant_key_fingerprint": ports.policy.grant_key.public_key_fingerprint,
        "max_validity_seconds": 7200,
        "max_revocation_age_seconds": 60,
    }
    values.update(over)
    return opening_verifier_profile_v1(**values)


def verifier_for(ports, profile=None):
    return build_starter_native_opening_import_verifier_v2(
        profile=profile or profile_for(ports),
        authority=ports,
        source=ports,
        custody=ports,
        grants=ports,
        windows=ports,
        clock=ports,
    )


def test_production_profile_verifies_the_exact_signed_command():
    ports = TrustedPorts()
    verifier = verifier_for(ports)
    assert isinstance(verifier, StarterNativeOpeningImportVerifierV2)
    request = qualification._request(ports.command)
    result = verifier.verify_import_v2(request)
    assert type(result) is OpeningImportVerificationV2
    assert result.command_digest == request.command_digest
    assert result.partition == ports.command.partition
    preview = preview_opening_account_import_v2(ports.command, verifier=verifier)
    assert preview.admissible, preview.blockers


def test_import_reversal_is_never_granted_by_this_verifier():
    ports = TrustedPorts()
    assert verifier_for(ports).verify_import_reversal_v2(object()) is None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: replace(p, audience="another-audience"),
        lambda p: replace(
            p, grant_key=replace(p.grant_key, public_key_fingerprint="f" * 64)
        ),
        lambda p: replace(
            p, source_key=replace(p.source_key, public_key_fingerprint="e" * 64)
        ),
        lambda p: replace(p, max_validity_seconds=7201),
        lambda p: replace(p, max_revocation_age_seconds=61),
        lambda p: replace(p, installation_id="foreign-installation"),
    ],
)
def test_a_policy_outside_the_pinned_profile_is_unavailable_never_verified(mutation):
    ports = TrustedPorts()
    pinned = profile_for(ports)  # the deployment pins the genuine policy first
    ports.policy = mutation(ports.policy)  # then the stored policy drifts
    with pytest.raises(OpeningVerificationUnavailable):
        verifier_for(ports, pinned).verify_import_v2(
            qualification._request(ports.command)
        )


def test_a_missing_policy_stays_unavailable():
    ports = TrustedPorts()
    pinned = profile_for(ports)
    ports.policy = None
    with pytest.raises(OpeningVerificationUnavailable):
        verifier_for(ports, pinned).verify_import_v2(
            qualification._request(ports.command)
        )


def test_the_profile_covers_only_its_own_scope_and_installation():
    ports = TrustedPorts()
    reader = ProfileBoundAuthorityReaderV1(ports, profile_for(ports))
    with pytest.raises(OpeningVerificationUnavailable):
        reader.current_policy(scope=ports.command.scope, installation_id="other")
    with pytest.raises(OpeningVerificationUnavailable):
        reader.current_revocations(
            scope=ports.command.scope, installation_id="other", policy_id="policy-v1"
        )


def test_rehearsal_and_production_namespaces_cannot_be_mixed():
    ports = TrustedPorts()
    with pytest.raises(OpeningEvidenceError):
        profile_for(ports, audience="rehearsal:" + AUDIENCE)  # production name
    with pytest.raises(OpeningEvidenceError):
        profile_for(ports, name="rehearsal")  # no rehearsal: prefix
    rehearsal = profile_for(ports, name="rehearsal", audience="rehearsal:" + AUDIENCE)
    # the rehearsal-pinned profile cannot verify the production-audience statements
    with pytest.raises(OpeningVerificationUnavailable):
        verifier_for(ports, rehearsal).verify_import_v2(
            qualification._request(ports.command)
        )


@pytest.mark.parametrize(
    "bad",
    [
        {"source_key_fingerprint": "not-hex"},
        {"grant_key_fingerprint": "A" * 64},
        {"max_validity_seconds": 0},
        {"max_revocation_age_seconds": -1},
        {"name": "staging"},
    ],
)
def test_profile_construction_refuses_unpinned_or_unbounded_values(bad):
    ports = TrustedPorts()
    with pytest.raises(OpeningEvidenceError):
        profile_for(ports, **bad)


def test_source_and_grant_pins_must_differ():
    ports = TrustedPorts()
    same = ports.policy.source_key.public_key_fingerprint
    with pytest.raises(OpeningEvidenceError):
        profile_for(ports, grant_key_fingerprint=same)
