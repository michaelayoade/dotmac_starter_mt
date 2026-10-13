"""Stored invoice facts decode strictly without changing historical evidence."""

from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import timedelta
from decimal import Decimal, localcontext
from uuid import UUID

import pytest
from dotmac_billing import GetInvoiceDocumentFactV1, StoredInvoiceDocumentFactV1
from dotmac_billing._document_facts import decode_invoice_document_fact
from dotmac_billing.errors import BillingRuleViolation
from dotmac_kernel.cache import PlatformScope, TenantScope


@pytest.fixture
def payload():
    money = {"amount": "12.345678", "currency": "NGN", "minor_units": 6}
    party = {
        "legal_name": "Historical seller",
        "address": {
            "line_one": "One Street",
            "country_code": "NG",
            "line_two": None,
            "city": None,
            "region": None,
            "postal_code": None,
        },
        "registered_identifiers": [
            {
                "identifier_type": "registration",
                "identifier_value": "ABC",
                "issuing_country_code": None,
            }
        ],
        "trading_name": None,
    }
    basis = {
        "status": "unknown_unverified",
        "source_authority": "import",
        "evidence_ref": "import:1",
        "payment_terms_code": None,
        "payment_terms_version": None,
        "issued_at": None,
        "effective_at": None,
        "timezone": None,
        "derivation_policy": None,
        "derivation_version": None,
        "override_actor": None,
        "override_reason": None,
        "override_evidence_ref": None,
        "supersedes_basis_ref": None,
    }
    return {
        "scope": {},
        "invoice_id": "11111111-1111-1111-1111-111111111111",
        "fact_version": 1,
        "series_code": "invoice",
        "document_number": "INV-1",
        "document_kind": "invoice",
        "document_state": "issued",
        "document_profile_code": "invoice.standard",
        "document_profile_version": "1",
        "currency": "NGN",
        "minor_units": 6,
        "subtotal": deepcopy(money),
        "tax_total": deepcopy(money),
        "grand_total": deepcopy(money),
        "due_at": None,
        "due_date_basis": basis,
        "seller_snapshot": deepcopy(party),
        "customer_snapshot": deepcopy(party),
        "payment_instructions": {
            "method_code": "bank",
            "bank_name": None,
            "account_name": None,
            "account_reference": None,
            "routing_code": None,
            "narrative": None,
        },
        "brand_asset": {"status": "none", "file_id": None},
        "locale": "en-NG",
        "timezone": "Africa/Lagos",
        "lines": [
            {
                "line_number": 1,
                "description": "Exact historical line",
                "quantity": "1.234",
                "unit_code": "unit",
                "unit_amount": deepcopy(money),
                "pre_tax_amount": deepcopy(money),
                "discount_total": deepcopy(money),
                "tax_amount": deepcopy(money),
                "total_amount": deepcopy(money),
                "price_source_version": "1",
                "discounts": [
                    {
                        "discount_code": "discount",
                        "description": "Frozen discount",
                        "amount": deepcopy(money),
                        "source_version": "1",
                    }
                ],
            }
        ],
        "tax_snapshots": [
            {
                "treatment_code": "vat",
                "jurisdiction_code": "NG",
                "policy_id": "vat",
                "policy_version": "1",
                "rate": "0.075",
                "taxable_basis": deepcopy(money),
                "tax_amount": deepcopy(money),
            }
        ],
        "fx_snapshot": {
            "observation_id": "fx:1",
            "observation_version": "1",
            "base_currency": "USD",
            "quote_currency": "NGN",
            "rate": "1500.123456789",
            "rate_purpose": "billing",
            "observed_at": "2026-08-17T10:00:00+00:00",
            "effective_at": "2026-08-17T10:00:00+00:00",
            "rounding_policy": "half_up",
            "provenance": "provider:1",
        },
        "party_tax_identities": [
            {
                "party_role": "seller",
                "identity_type": "vat",
                "identity_value": "123",
                "country_code": "NG",
                "source_authority": "registry",
                "source_version": "1",
            }
        ],
        "issued_at": "2026-08-17T10:00:00+00:00",
        "frozen_at": "2026-08-17T10:00:00+00:00",
        "source_authority": "internal",
        "correlation_id": "issuance:1",
        "presentation_model_digest": "a" * 64,
    }


def test_decoder_preserves_exact_money_and_typed_nested_values(payload):
    with localcontext() as context:
        context.prec = 3
        fact = decode_invoice_document_fact(payload)
    assert fact.grand_total.amount == Decimal("12.345678")
    assert fact.lines[0].quantity == Decimal("1.234")
    assert fact.fx_snapshot.rate == Decimal("1500.123456789")
    assert fact.tax_snapshots[0].rate == Decimal("0.075")
    assert fact.seller_snapshot.registered_identifiers[0].identifier_value == "ABC"
    assert fact.lines[0].discounts[0].amount == fact.grand_total
    assert fact.party_tax_identities[0].party_role == "seller"
    assert isinstance(fact.lines, tuple)
    with pytest.raises(FrozenInstanceError):
        fact.document_number = "rewritten"


@pytest.mark.parametrize(
    "path,value",
    [
        (("fact_version",), True),
        (("fact_version",), "1"),
        (("grand_total", "amount"), 12.345678),
        (("grand_total", "amount"), "NaN"),
        (("grand_total", "amount"), "12.3456789"),
        (("grand_total", "currency"), "ngn"),
        (("grand_total", "minor_units"), True),
        (("grand_total", "minor_units"), 7),
        (("lines", 0, "quantity"), 1.2),
        (("lines", 0, "discounts"), {}),
        (("document_state",), "draft"),
        (("party_tax_identities", 0, "party_role"), "payer"),
        (("issued_at",), "2026-08-17T10:00:00"),
        (("due_date_basis", "status"), "invented"),
        (("seller_snapshot", "address", "line_one"), 1),
        (("brand_asset", "status"), "file"),
        (("scope",), {"tenant_id": None}),
    ],
)
def test_decoder_refuses_invalid_types_without_coercion(payload, path, value):
    target = payload
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = value
    with pytest.raises(BillingRuleViolation) as error:
        decode_invoice_document_fact(payload)
    assert error.value.code == "billing.document_fact_corrupt"


@pytest.mark.parametrize(
    "path",
    [
        ("fx_snapshot",),
        ("due_at",),
        ("lines", 0, "discounts"),
        ("grand_total", "minor_units"),
        ("seller_snapshot", "trading_name"),
    ],
)
def test_decoder_never_supplies_missing_defaults(payload, path):
    target = payload
    for component in path[:-1]:
        target = target[component]
    del target[path[-1]]
    with pytest.raises(BillingRuleViolation):
        decode_invoice_document_fact(payload)


def test_decoder_refuses_unknown_fields_and_preserves_input(payload):
    before = deepcopy(payload)
    decode_invoice_document_fact(payload)
    assert payload == before
    payload["seller_snapshot"]["future_field"] = "unknown"
    with pytest.raises(BillingRuleViolation):
        decode_invoice_document_fact(payload)


def test_explicit_scope_and_frozen_query_contract(payload):
    payload["scope"] = {"tenant_id": "22222222-2222-2222-2222-222222222222"}
    fact = decode_invoice_document_fact(payload)
    assert fact.scope == TenantScope(UUID(payload["scope"]["tenant_id"]))
    request = GetInvoiceDocumentFactV1(PlatformScope(), fact.invoice_id, 1)
    with pytest.raises(FrozenInstanceError):
        request.fact_version = 2
    envelope = StoredInvoiceDocumentFactV1(
        UUID("33333333-3333-3333-3333-333333333333"),
        "billing.invoice.document.fact.v1",
        fact.frozen_at,
        fact,
    )
    assert envelope.fact == fact
    for version in (0, -1, True, "1"):
        with pytest.raises((ValueError, TypeError)):
            GetInvoiceDocumentFactV1(PlatformScope(), fact.invoice_id, version)


@pytest.mark.parametrize(
    "path",
    [
        ("issued_at",),
        ("frozen_at",),
        ("due_at",),
        ("due_date_basis", "issued_at"),
        ("fx_snapshot", "observed_at"),
    ],
)
@pytest.mark.parametrize(
    "instant",
    [
        "2026-08-17T10:00:00.1234567+00:00",
        "2026-08-17T10:00:00,1234567Z",
        "2026-08-17T10:00:00+01:00:00.1234567",
        "2026-08-17T10:00:00+01:00:00,1234567",
    ],
)
def test_decoder_refuses_datetime_precision_loss(payload, path, instant):
    target = payload
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = instant
    with pytest.raises(BillingRuleViolation) as error:
        decode_invoice_document_fact(payload)
    assert error.value.code == "billing.document_fact_corrupt"
    assert instant not in str(error.value)
    assert error.value.details == {}


@pytest.mark.parametrize(
    "instant",
    [
        "2026-08-17T10:00:00Z",
        "2026-08-17T10:00:00.123456+00:00",
        "2026-08-17T10:00:00+01:00:00.123456",
    ],
)
def test_decoder_preserves_representable_historical_datetime_text(payload, instant):
    payload["issued_at"] = instant
    original = deepcopy(payload)
    fact = decode_invoice_document_fact(payload)
    assert fact.issued_at.isoformat() in (
        "2026-08-17T10:00:00+00:00",
        "2026-08-17T10:00:00.123456+00:00",
        "2026-08-17T10:00:00+01:00:00.123456",
    )
    assert payload == original


@pytest.mark.parametrize("sign,microseconds", [("+", 123456), ("-", -123456)])
@pytest.mark.parametrize("separator", [".", ","])
def test_decoder_preserves_subsecond_utc_offsets(
    payload, sign, microseconds, separator
):
    instant = f"2026-08-17T10:00:00{sign}00:00:00{separator}123456"
    payload["issued_at"] = instant
    payload["fx_snapshot"]["observed_at"] = instant
    fact = decode_invoice_document_fact(payload)
    assert fact.issued_at.utcoffset() == timedelta(microseconds=microseconds)
    assert fact.fx_snapshot.observed_at.utcoffset() == timedelta(
        microseconds=microseconds
    )
    assert fact.issued_at.isoformat() == instant.replace(",", ".")
    assert payload["issued_at"] == instant
