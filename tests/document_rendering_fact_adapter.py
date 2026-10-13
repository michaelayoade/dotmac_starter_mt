"""Test assembly: map public Billing DTOs to public renderer DTOs by value.

This is qualification evidence, not a production adapter or runtime adoption.
"""

from dataclasses import fields

from dotmac_billing import StoredInvoiceDocumentFactV1
from dotmac_document_rendering import (
    DiscountV2,
    DocumentLineV2,
    DueDateBasisV2,
    FxSnapshotV2,
    InvoiceDocumentFactV2,
    PartySnapshotV2,
    PartyTaxIdentityV2,
    PaymentInstructionsV2,
    PostalAddressV2,
    PresentationAssetV2,
    RecordedLineReferenceV2,
    RegisteredIdentifierV2,
    TaxSnapshotV2,
)


def _values(value):
    return {item.name: getattr(value, item.name) for item in fields(value)}


def map_stored_fact(stored: StoredInvoiceDocumentFactV1) -> InvoiceDocumentFactV2:
    fact = stored.fact

    def party(value):
        return PartySnapshotV2(
            legal_name=value.legal_name,
            trading_name=value.trading_name,
            address=PostalAddressV2(**_values(value.address)),
            registered_identifiers=tuple(
                RegisteredIdentifierV2(**_values(identifier))
                for identifier in value.registered_identifiers
            ),
        )

    basis = _values(fact.due_date_basis)
    basis["status"] = fact.due_date_basis.status.value
    lines = tuple(
        DocumentLineV2(
            **{
                key: value for key, value in _values(line).items() if key != "discounts"
            },
            discounts=tuple(DiscountV2(**_values(item)) for item in line.discounts),
            recorded_line=RecordedLineReferenceV2(
                emitter="dotmac-billing",
                fact_id=stored.fact_id,
                fact_version=fact.fact_version,
                line_number=line.line_number,
            ),
        )
        for line in fact.lines
    )
    return InvoiceDocumentFactV2(
        contract_version=2,
        emitter="dotmac-billing",
        persisted_fact_id=stored.fact_id,
        persisted_contract_version=stored.contract_version,
        recorded_at=stored.recorded_at,
        original_presentation_model_digest=fact.presentation_model_digest,
        series_version=None,
        **{
            key: value
            for key, value in _values(fact).items()
            if key
            not in {
                "presentation_model_digest",
                "due_date_basis",
                "seller_snapshot",
                "customer_snapshot",
                "payment_instructions",
                "brand_asset",
                "lines",
                "tax_snapshots",
                "fx_snapshot",
                "party_tax_identities",
            }
        },
        due_date_basis=DueDateBasisV2(**basis),
        seller_snapshot=party(fact.seller_snapshot),
        customer_snapshot=party(fact.customer_snapshot),
        payment_instructions=PaymentInstructionsV2(
            **_values(fact.payment_instructions)
        ),
        brand_asset=PresentationAssetV2(**_values(fact.brand_asset)),
        lines=lines,
        tax_snapshots=tuple(
            TaxSnapshotV2(**_values(item)) for item in fact.tax_snapshots
        ),
        fx_snapshot=FxSnapshotV2(**_values(fact.fx_snapshot))
        if fact.fx_snapshot
        else None,
        party_tax_identities=tuple(
            PartyTaxIdentityV2(**_values(item)) for item in fact.party_tax_identities
        ),
    )
