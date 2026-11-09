"""Fixtures that give legacy discharge tests a real frozen withholding component.

A discharge now needs a frozen component of its invoice. These helpers build a
real dotmac-tax determination that matches a legacy discharge command's Tax
evidence and freeze it through the governed
``freeze_issued_invoice_withholding`` command (tenant plane). Where that command
cannot or must not run, a component row is written directly with the same
production row builder:

* the platform plane (Tax evidence is tenant-only, so the command refuses it);
* a second component on an invoice that already has one (the command freezes one
  determination per invoice);
* ``direct=True`` for tests whose subject is a later refusal (a reversed
  issuance, an over-outstanding claim) that the command would pre-empt.

No assertion of any legacy test is changed; only the missing precondition (a
frozen elected component) is supplied.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

from dotmac_billing import (
    InvoiceTaxEvidenceV1,
    TaxDeterminationComponentEvidenceV1,
    TaxDeterminationLineEvidenceV1,
    TaxDeterminationSetEvidenceV1,
    service,
)
from dotmac_billing.commands import FreezeIssuedInvoiceWithholding
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.money import Money
from sqlalchemy import select


def _determination(scope, tax, document) -> InvoiceTaxEvidenceV1:
    zero = Money(Decimal(0), tax.amount.currency)
    gross = Money(tax.taxable_basis.amount + tax.amount.amount, tax.amount.currency)
    component = TaxDeterminationComponentEvidenceV1(
        determination_id=UUID(tax.component_ref),
        determination_set_id=UUID(tax.determination_ref),
        component_sequence=1,
        tax_code_id=uuid5(NAMESPACE_URL, "fixture-wht-code"),
        rule_id=UUID(tax.rule_ref),
        rule_version=int(tax.rule_version),
        treatment_code=tax.treatment_code,
        calculation_base_code=tax.calculation_base_code,
        inclusive=tax.inclusive,
        party_category="wht-elected",
        supply_category=None,
        place_code=None,
        party_classification_id=uuid4(),
        supply_classification_id=None,
        place_classification_id=None,
        base_amount=tax.taxable_basis,
        tax_amount=tax.amount,
        recoverable_amount=zero,
        non_recoverable_amount=tax.amount,
        lines=tuple(
            TaxDeterminationLineEvidenceV1(
                line.sequence, line.taxable_amount, line.rate, line.tax_amount
            )
            for line in tax.lines
        ),
    )
    issue_date = document.issued_at.astimezone(ZoneInfo(document.timezone)).date()
    return InvoiceTaxEvidenceV1(
        "expected_withholding",
        TaxDeterminationSetEvidenceV1(
            tenant_id=scope.tenant_id if isinstance(scope, TenantScope) else uuid4(),
            determination_set_id=UUID(tax.determination_ref),
            jurisdiction_id=uuid5(NAMESPACE_URL, "fixture-wht-jurisdiction"),
            occurred_on=issue_date,
            fact_kind=tax.fact_kind,
            recognition_basis_code=tax.recognition_basis_code,
            transaction_side=tax.transaction_side,
            source_amount=tax.taxable_basis,
            net_amount=tax.taxable_basis,
            tax_amount=tax.amount,
            gross_amount=gross,
            source_ref=tax.source_ref,
            source_version=tax.source_version,
            source_fingerprint=tax.source_fingerprint,
            result_fingerprint=tax.result_fingerprint,
            evidence_ref=tax.evidence_ref,
            counterparty_ref=None,
            supply_ref=None,
            place_ref=None,
            determined_at=tax.determined_at,
            components=(component,),
        ),
    )


def ensure_frozen(db, scope, command, *, direct: bool = False) -> None:
    """Make the component a discharge command cites exist, once."""
    tax = command.tax_evidence
    plane = service._models(scope)
    existing = db.execute(
        select(
            plane.wht_component.document_id, plane.wht_component.component_ref
        ).where(plane.wht_component.document_id == command.document_id)
    ).all()
    if any(row.component_ref == tax.component_ref for row in existing):
        return
    document = db.get(plane.document, command.document_id)
    evidence = _determination(scope, tax, document)
    if isinstance(scope, TenantScope) and not existing and not direct:
        service.freeze_issued_invoice_withholding(
            db,
            scope=scope,
            command=FreezeIssuedInvoiceWithholding(
                scope,
                command.billing_account_id,
                command.document_id,
                f"fixture-freeze:{tax.determination_ref}",
                "finance-user:fixture",
                "fixture: invoice predates frozen withholding",
                evidence,
            ),
        )
        return
    result = evidence.determination
    db.add(
        service._frozen_component_row(
            scope=scope,
            plane=plane,
            document=document,
            obligation_id=document.obligation_id,
            result=result,
            component=result.components[0],
            origin={
                "origin": "late_freeze",
                "command_id": f"fixture-freeze:{tax.determination_ref}",
                "actor_ref": "finance-user:fixture",
                "reason": "fixture: direct component for a legacy test",
            },
        )
    )
    db.flush()


def withholding_command(db, account_id, document_id, *, scope, direct=False, **kw):
    """``_withholding_command`` with its component frozen for the invoice."""
    from tests import test_billing_isolation as billing

    command = billing._withholding_command(account_id, document_id, scope=scope, **kw)
    ensure_frozen(db, scope, command, direct=direct)
    return command
