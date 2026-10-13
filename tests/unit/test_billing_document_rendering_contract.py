"""Public Billing stored-fact to V2 assembly qualification; no runtime wiring."""

from dataclasses import fields
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from dotmac_billing import (
    AppliedFxSnapshotV1,
    AppliedTaxSnapshotV1,
    DiscountSnapshotV1,
    DueDateBasisV1,
    InvoiceDocumentFactV1,
    InvoiceLineFactV1,
    PartyDocumentSnapshotV1,
    PartyTaxIdentitySnapshotV1,
    PaymentInstructionsSnapshotV1,
    PostalAddressSnapshotV1,
    PresentationAssetReferenceV1,
    RegisteredIdentifierSnapshotV1,
    StoredInvoiceDocumentFactV1,
)
from dotmac_document_rendering import LabelledValue, Table
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.money import Currency, Money

from tests.document_rendering_fact_adapter import map_stored_fact

NOW = datetime(2026, 10, 13, 16, 0, tzinfo=UTC)
FACT_ID = UUID("30000000-0000-0000-0000-000000000003")


def money(amount: str) -> Money:
    return Money.of(amount, Currency("NGN", 2))


def stored_fact() -> StoredInvoiceDocumentFactV1:
    address = PostalAddressSnapshotV1(
        line_one="10 Test Road",
        line_two="Floor 2",
        city="Lagos",
        region="Lagos State",
        postal_code="100001",
        country_code="NG",
    )
    seller = PartyDocumentSnapshotV1(
        legal_name="Seller <Limited>",
        trading_name="Frozen Seller & Co",
        address=address,
        registered_identifiers=(
            RegisteredIdentifierSnapshotV1("company", "RC-123", "NG"),
            RegisteredIdentifierSnapshotV1("licence", "LIC-456", "NG"),
        ),
    )
    customer = PartyDocumentSnapshotV1(
        legal_name="Customer Limited",
        trading_name="Customer Trading",
        address=address,
        registered_identifiers=(
            RegisteredIdentifierSnapshotV1("company", "RC-CUSTOMER", "NG"),
            RegisteredIdentifierSnapshotV1("branch", "BRANCH-2", None),
        ),
    )
    fact = InvoiceDocumentFactV1(
        scope=TenantScope(UUID("10000000-0000-0000-0000-000000000001")),
        invoice_id=UUID("20000000-0000-0000-0000-000000000002"),
        fact_version=7,
        series_code="invoice",
        document_number="INV-2026-0013",
        document_kind="invoice",
        document_state="issued",
        document_profile_code="invoice.standard",
        document_profile_version="opaque:v7",
        currency="NGN",
        minor_units=2,
        subtotal=money("900.00"),
        tax_total=money("135.00"),
        grand_total=money("990.00"),
        due_at=None,
        due_date_basis=DueDateBasisV1.unknown_unverified(
            source_authority="historical-source", evidence_ref="due:not-recorded"
        ),
        seller_snapshot=seller,
        customer_snapshot=customer,
        payment_instructions=PaymentInstructionsSnapshotV1(
            method_code="bank_transfer",
            bank_name="Frozen Bank",
            account_name="Frozen Seller",
            account_reference="1234567890",
            routing_code="000001",
            narrative="Use invoice reference <unchanged>",
        ),
        brand_asset=PresentationAssetReferenceV1.none(),
        locale="en",
        timezone="Africa/Lagos",
        lines=tuple(
            InvoiceLineFactV1(
                line_number=i,
                description=f"Service line {i}",
                quantity=Decimal("2.500"),
                unit_code="GB",
                unit_amount=money("40.00"),
                pre_tax_amount=money("100.00"),
                discount_total=money("5.00"),
                tax_amount=money("15.00"),
                total_amount=money("110.00"),
                price_source_version=f"recorded-price:{i}",
                discounts=(
                    DiscountSnapshotV1(
                        "loyalty", "Frozen discount", money("5.00"), "3"
                    ),
                ),
            )
            for i in range(1, 10)
        ),
        tax_snapshots=(
            AppliedTaxSnapshotV1(
                "standard",
                "source-zone",
                "policy",
                "policy:3",
                Decimal("0.1500"),
                money("900.00"),
                money("135.00"),
            ),
        ),
        fx_snapshot=AppliedFxSnapshotV1(
            "fx:1",
            "fx:version:2",
            "NGN",
            "USD",
            Decimal("0.000600"),
            "presentation",
            NOW,
            NOW,
            "source-rounded",
            "frozen-provider",
        ),
        party_tax_identities=(
            PartyTaxIdentitySnapshotV1(
                "seller", "TIN", "SELLER-TIN", "NG", "party", "4"
            ),
            PartyTaxIdentitySnapshotV1(
                "seller", "VAT", "SELLER-VAT", "NG", "party", "5"
            ),
            PartyTaxIdentitySnapshotV1(
                "customer", "TIN", "CUSTOMER-TIN", "NG", "party", "6"
            ),
        ),
        issued_at=NOW,
        frozen_at=NOW,
        source_authority="internal",
        correlation_id="stored-fact:7",
        presentation_model_digest="a" * 64,
    )
    return StoredInvoiceDocumentFactV1(
        FACT_ID, "billing.invoice.document.fact.v1", NOW, fact
    )


def projected_values(projection):
    values = {}
    for section in projection.sections:
        for block in section.blocks:
            if isinstance(block, LabelledValue):
                values[block.value.source_field] = block.value
            elif isinstance(block, Table):
                for row in block.rows:
                    for value in row:
                        values[value.source_field] = value
    return values


def test_public_producer_mapping_retains_all_frozen_fields_without_fabrication():
    stored = stored_fact()
    mapped = map_stored_fact(stored)
    assert mapped.persisted_fact_id == stored.fact_id
    assert mapped.recorded_at == stored.recorded_at
    assert mapped.fact_version == stored.fact.fact_version
    assert (
        mapped.original_presentation_model_digest
        == stored.fact.presentation_model_digest
    )
    assert mapped.document_profile_version == "opaque:v7"
    assert mapped.series_version is None
    assert mapped.due_at is None
    assert mapped.payment_instructions.payment_link is None
    assert (
        mapped.payment_instructions.narrative
        == stored.fact.payment_instructions.narrative
    )
    assert mapped.seller_snapshot.trading_name == "Frozen Seller & Co"
    assert len(mapped.seller_snapshot.registered_identifiers) == 2
    assert len(mapped.customer_snapshot.registered_identifiers) == 2
    assert len(mapped.party_tax_identities) == 3
    assert len(mapped.lines) == 9
    for original, line in zip(stored.fact.lines, mapped.lines, strict=True):
        for name in (
            item.name for item in fields(original) if item.name != "discounts"
        ):
            assert getattr(line, name) == getattr(original, name), name
        assert line.recorded_line.fact_id == stored.fact_id
        assert line.recorded_line.fact_version == stored.fact.fact_version
        assert line.recorded_line.line_number == original.line_number
        assert line.recorded_line.emitter == "dotmac-billing"
        assert line.discounts[0].amount == original.discounts[0].amount
