"""Pure codec/refusal canaries; synthetic DTOs never establish provider proof."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.money import Currency, Money
from dotmac_payments import (
    AuthorizedProviderCashV1,
    Conflict,
    MerchantTransportContextV1,
    OriginalProviderCashEvidenceV1,
    QualifyProviderCashV1,
    provider_cash_economic_identity,
    provider_cash_request_fingerprint,
)

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def evidence():
    return OriginalProviderCashEvidenceV1("fixture-owner", "original:1", "v2", "a" * 64)


def attestation():
    request = QualifyProviderCashV1(uuid4(), evidence())
    return AuthorizedProviderCashV1(
        TenantScope(uuid4()),
        request.command_id,
        provider_cash_request_fingerprint(request),
        request.original_evidence,
        MerchantTransportContextV1(
            "psp", "production", "installation:1", "cap:1", "rev:1", "b" * 64
        ),
        uuid4(),
        NOW,
        "capture",
        "transaction:1",
        "checkout:1",
        "succeeded",
        Money.of("100.00", Currency("NGN", 2)),
        NOW - timedelta(days=1),
        NOW,
    )


def test_economic_identity_excludes_retrieval_configuration_and_local_ids():
    original = attestation()
    expected = provider_cash_economic_identity(original, "merchant:1")
    changed = replace(
        original,
        observed_at=NOW + timedelta(days=1),
        context_id=uuid4(),
        transport=replace(
            original.transport,
            config_revision_reference="rev:2",
            config_digest="c" * 64,
        ),
    )
    assert provider_cash_economic_identity(changed, "merchant:1") == expected
    assert provider_cash_economic_identity(original, "merchant:2") != expected
    assert (
        provider_cash_economic_identity(
            replace(
                original, transport=replace(original.transport, environment="sandbox")
            ),
            "merchant:1",
        )
        != expected
    )
    assert (
        provider_cash_economic_identity(
            replace(original, transaction_id="transaction:2"), "merchant:1"
        )
        != expected
    )
    assert (
        provider_cash_economic_identity(replace(original, paid_at=NOW), "merchant:1")
        != expected
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("paid_at", None),
        ("paid_at", datetime(2026, 10, 1)),
        ("status", "pending"),
        ("transaction_id", " "),
        ("money", "100"),
    ],
)
def test_unqualified_capture_is_refused(field, value):
    with pytest.raises(Conflict):
        provider_cash_economic_identity(
            replace(attestation(), **{field: value}), "merchant:1"
        )


def test_forged_nested_precision_is_refused_without_rounding():
    value = Money.of("1.00", Currency("NGN", 2))
    object.__setattr__(value, "amount", Decimal("1.001"))
    with pytest.raises(Conflict):
        provider_cash_economic_identity(
            replace(attestation(), money=value), "merchant:1"
        )


def test_request_fingerprint_binds_full_original():
    request = QualifyProviderCashV1(uuid4(), evidence())
    assert provider_cash_request_fingerprint(
        request
    ) != provider_cash_request_fingerprint(
        replace(request, original_evidence=replace(evidence(), schema_version="v3"))
    )
    with pytest.raises(Conflict):
        provider_cash_request_fingerprint(replace(request, original_evidence={}))


def test_precision_canonicalization_and_full_provenance_exclusion():
    original = attestation()
    same_money = replace(original, money=Money.of("100.0000", Currency("NGN", 2)))
    same_economics = replace(
        same_money,
        scope=TenantScope(uuid4()),
        command_id=uuid4(),
        request_fingerprint="f" * 64,
        original_evidence=replace(evidence(), reference="other:original"),
        verified_at=NOW + timedelta(hours=1),
        observed_at=NOW + timedelta(hours=2),
        transport=replace(
            original.transport,
            installation_reference="other:installation",
            capability_reference="other:capability",
        ),
    )
    assert provider_cash_economic_identity(
        same_economics, "merchant:名"
    ) == provider_cash_economic_identity(original, "merchant:名")
    assert provider_cash_economic_identity(
        original, "a:b"
    ) != provider_cash_economic_identity(
        replace(original, transaction_kind="b:capture"), "a"
    )


def test_same_command_other_transport_changes_request_but_not_command_identity():
    command = QualifyProviderCashV1(uuid4(), evidence())
    other_original = replace(
        command,
        original_evidence=replace(
            command.original_evidence, reference="another:original"
        ),
    )
    assert other_original.command_id == command.command_id
    assert provider_cash_request_fingerprint(
        other_original
    ) != provider_cash_request_fingerprint(command)
