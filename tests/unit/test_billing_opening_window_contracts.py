"""Pure structural custody contracts; hosted execution only."""

from dataclasses import FrozenInstanceError, replace
from uuid import uuid4

import pytest
from dotmac_billing.opening_contracts import OpeningEvidenceError
from dotmac_billing.opening_window_contracts import (
    OpeningWindowAdmissionV1,
    OpeningWindowV1,
    opening_admission_population_digest,
)
from dotmac_kernel.cache import PlatformScope

from tests.unit import test_billing_opening_manifest as evidence


def _admission():
    candidate = evidence._candidate()
    return OpeningWindowAdmissionV1(
        candidate.manifest.scope,
        uuid4(),
        uuid4(),
        candidate.partition.account_id,
        "request:1",
        "a" * 64,
        "b" * 64,
    )


def test_population_digest_is_order_independent_full_field_and_scope_bound():
    first = _admission()
    second = replace(first, receipt_id=uuid4(), request_identity="request:2")
    digest = opening_admission_population_digest((first, second))
    assert digest == opening_admission_population_digest((second, first))
    assert digest != opening_admission_population_digest((first,))
    for field, value in (
        ("scope", PlatformScope()),
        ("window_id", uuid4()),
        ("receipt_id", uuid4()),
        ("billing_account_id", uuid4()),
        ("request_identity", "different"),
        ("request_digest", "c" * 64),
        ("manifest_fingerprint", "d" * 64),
    ):
        assert digest != opening_admission_population_digest(
            (replace(first, **{field: value}), second)
        )
    assert opening_admission_population_digest(()) != digest


def test_frozen_full_manifest_window_rejects_mutable_or_coerced_evidence():
    candidate = evidence._candidate()
    window = OpeningWindowV1(
        candidate.manifest.scope,
        uuid4(),
        uuid4(),
        1,
        "a" * 64,
        candidate.manifest,
        "fixture_restricted",
        123,
        "authority:1",
        evidence.NOW,
    )
    with pytest.raises(FrozenInstanceError):
        window.principal_name = "other"
    for field, value in (
        ("principal_oid", True),
        ("manifest", {}),
        ("epoch", str(uuid4())),
        ("principal_name", " "),
    ):
        with pytest.raises(OpeningEvidenceError):
            replace(window, **{field: value})
