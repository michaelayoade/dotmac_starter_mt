"""Cross-owner contract canary for the Sales -> Orders boundary.

The adapter lives in each product assembly.  This proof deliberately contains
no database or catalogue read: every Orders input must be present in the
accepted-Quote owner output, and translation must add no commercial decision.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from dotmac_kernel.money import Currency, Money
from dotmac_orders import (
    ActorRef,
    LineInput,
    SubmitOrderCommand,
    TaxSnapshotV1,
    TermsSnapshotV1,
    TermValueV1,
)
from dotmac_sales import (
    AcceptedQuoteHandoffV1,
    AcceptedQuoteLineV1,
    AcceptedQuoteTaxComponentV1,
    QuoteTermsSnapshotV1,
    QuoteTermValueV1,
)


def _translate(handoff: AcceptedQuoteHandoffV1) -> SubmitOrderCommand:
    money_currency = Currency(handoff.currency, handoff.currency_minor_units)
    return SubmitOrderCommand(
        idempotency_key=str(handoff.event_id),
        order_reference=f"quote:{handoff.quote_id}",
        customer_ref=str(handoff.sales_subject["opaque_id"]),
        currency_code=handoff.currency,
        currency_minor_units=handoff.currency_minor_units,
        lines=tuple(
            LineInput(
                line_key=str(line.line_id),
                description=line.description,
                quantity=Decimal(line.quantity),
                unit_price=Money.of(line.unit_price, money_currency),
                discount=Money.of(line.discount_amount, money_currency),
                taxes=tuple(
                    TaxSnapshotV1(
                        tax_code=tax.tax_code,
                        source_version=tax.source_version,
                        taxable_basis=Money.of(tax.taxable_basis, money_currency),
                        rate=None if tax.rate is None else Decimal(tax.rate),
                        amount=Money.of(tax.amount, money_currency),
                    )
                    for tax in line.taxes
                ),
                price_version_ref=line.price_version_ref,
                terms_ref=line.terms_ref,
                terms_snapshot=TermsSnapshotV1(
                    version_ref=line.terms_snapshot.version_ref,
                    values=tuple(
                        TermValueV1(value.name, value.value)
                        for value in line.terms_snapshot.values
                    ),
                ),
                specification_ref=line.specification_ref,
                source_ref=f"sales-quote-line:{line.line_id}",
                source_version=handoff.accepted_snapshot_sha256,
            )
            for line in handoff.lines
        ),
        coverage_obligation_refs=(handoff.fulfillment_eligibility_requirement_refs),
        submitted_by=ActorRef(
            actor_type=handoff.accepted_by["kind"],
            actor_id=handoff.accepted_by["opaque_id"],
        ),
        submitted_at=handoff.accepted_at,
        source_ref=f"sales-accepted-quote:{handoff.quote_id}",
        source_version=handoff.accepted_snapshot_sha256,
        correlation_id=str(handoff.event_id),
    )


def test_accepted_quote_is_mechanically_translatable_to_order_submission() -> None:
    accepted_at = datetime(2026, 8, 19, 12, tzinfo=UTC)
    handoff = AcceptedQuoteHandoffV1(
        schema_version=1,
        event_id=UUID("10000000-0000-0000-0000-000000000001"),
        tenant_id=UUID("20000000-0000-0000-0000-000000000002"),
        quote_id=UUID("30000000-0000-0000-0000-000000000003"),
        lead_id=UUID("40000000-0000-0000-0000-000000000004"),
        accepted_at=accepted_at,
        accepted_by={"kind": "staff", "opaque_id": "staff-7"},
        sales_subject={"kind": "party", "opaque_id": "party-9", "version": "3"},
        sales_subject_label="Example Limited",
        currency="NGN",
        currency_minor_units=2,
        subtotal="100.00",
        discount_amount="10.00",
        tax_total="6.75",
        total="96.75",
        lines=(
            AcceptedQuoteLineV1(
                line_id=UUID("50000000-0000-0000-0000-000000000005"),
                position=1,
                description="Managed service",
                quantity="1",
                unit_price="100.00",
                gross_amount="100.00",
                discount_amount="10.00",
                tax_amount="6.75",
                amount="96.75",
                catalogue_ref="offer:managed:v2",
                price_version_ref="price:managed:2026-08-19",
                terms_ref="terms:managed:v4",
                terms_snapshot=QuoteTermsSnapshotV1(
                    version_ref="terms:managed:v4",
                    values=(QuoteTermValueV1("minimum_term_months", "12"),),
                ),
                specification_ref="spec:managed:v6",
                taxes=(
                    AcceptedQuoteTaxComponentV1(
                        tax_code="vat",
                        source_version="ng-vat:2026-01",
                        taxable_basis="90.00",
                        rate="7.5",
                        amount="6.75",
                    ),
                ),
            ),
        ),
        fulfillment_eligibility_requirement_refs=("settlement:quote-3",),
        accepted_snapshot_sha256="a" * 64,
    )

    command = _translate(handoff)

    assert command.customer_ref == "party-9"
    assert command.coverage_obligation_refs == ("settlement:quote-3",)
    assert command.lines[0].terms_snapshot.values[0].value == "12"
    assert command.lines[0].taxes[0].amount == Money.of("6.75", Currency("NGN", 2))
    assert command.lines[0].specification_ref == "spec:managed:v6"
