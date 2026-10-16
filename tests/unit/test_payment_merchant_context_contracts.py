"""Typed configuration-review request binding; no provider-origin verification."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from dotmac_payments import (
    AcceptMerchantContextV1,
    Conflict,
    MerchantConfigurationV1,
    MerchantReviewEvidenceV1,
    MerchantTransportContextV1,
    merchant_review_request_fingerprint,
)


def request() -> AcceptMerchantContextV1:
    return AcceptMerchantContextV1(
        command_id=uuid4(),
        configuration=MerchantConfigurationV1(
            transport=MerchantTransportContextV1(
                provider_type="paystack",
                environment="production",
                installation_reference="installation:123",
                capability_reference="reconcile:123",
                config_revision_reference="revision:1",
                config_digest="a" * 64,
            ),
            physical_merchant_id="business:123",
        ),
        evidence=MerchantReviewEvidenceV1("archive:merchant-review", "b" * 64),
        effective_from=datetime(2026, 10, 1, tzinfo=UTC),
    )


def test_full_review_material_is_bound() -> None:
    value = request()
    original = merchant_review_request_fingerprint(value)
    alternatives = (
        replace(value, command_id=uuid4()),
        replace(
            value,
            configuration=replace(
                value.configuration, physical_merchant_id="business:456"
            ),
        ),
        replace(
            value,
            configuration=replace(
                value.configuration,
                transport=replace(value.configuration.transport, environment="test"),
            ),
        ),
        replace(value, evidence=replace(value.evidence, sha256="c" * 64)),
        replace(value, effective_from=datetime(2026, 10, 2, tzinfo=UTC)),
        replace(value, supersedes_context_id=uuid4()),
    )
    assert all(
        merchant_review_request_fingerprint(alternative) != original
        for alternative in alternatives
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("evidence", {"sha256": "b" * 64}),
        ("effective_from", datetime(2026, 10, 1)),
        ("command_id", "caller-string"),
    ],
)
def test_forged_frozen_request_refuses(field: str, value: object) -> None:
    command = request()
    object.__setattr__(command, field, value)
    with pytest.raises(Conflict):
        merchant_review_request_fingerprint(command)


def test_nested_frozen_transport_is_revalidated() -> None:
    command = request()
    object.__setattr__(command.configuration.transport, "config_digest", True)
    with pytest.raises(Conflict):
        merchant_review_request_fingerprint(command)
