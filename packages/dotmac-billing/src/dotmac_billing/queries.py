"""Read-only Billing queries over persisted immutable evidence."""

from __future__ import annotations

import re
from decimal import DecimalException
from typing import cast
from uuid import UUID

from dotmac_kernel.cache import PlatformScope, Scope, TenantScope
from dotmac_kernel.fingerprints import fingerprint_of
from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_billing import models
from dotmac_billing._document_facts import _decode, decode_invoice_document_fact
from dotmac_billing.contracts import (
    DOCUMENT_FACT_CONTRACT,
    GetInvoiceDocumentFactV1,
    StoredInvoiceDocumentFactV1,
    WithholdingDischargeEffectV1,
)
from dotmac_billing.errors import BillingRuleViolation


def get_invoice_document_fact(
    db: Session, *, scope: Scope, request: GetInvoiceDocumentFactV1
) -> StoredInvoiceDocumentFactV1:
    """Read an exact version without consulting live documents or ORM state.

    The caller owns the transaction. In particular, dirty objects in its identity
    map must neither override persisted evidence nor autoflush during this read.
    """
    if not isinstance(scope, TenantScope | PlatformScope) or request.scope != scope:
        raise BillingRuleViolation(
            "scope_mismatch", "document fact request must match the routed scope"
        )
    table = (
        models.InvoiceDocumentFact.__table__
        if isinstance(scope, TenantScope)
        else models.PlatformInvoiceDocumentFact.__table__
    )
    statement = select(table).where(
        table.c.document_id == request.invoice_id,
        table.c.fact_version == request.fact_version,
    )
    if isinstance(scope, TenantScope):
        statement = statement.where(table.c.tenant_id == scope.tenant_id)
    with db.no_autoflush:
        row = db.execute(statement).mappings().one_or_none()
    if row is None:
        raise BillingRuleViolation(
            "document_fact_not_found",
            "the exact invoice document fact version is unavailable",
        )
    if row["contract_version"] != DOCUMENT_FACT_CONTRACT:
        raise BillingRuleViolation(
            "document_fact_contract_unsupported",
            "stored invoice document fact contract is unsupported",
        )
    # Verify the ORIGINAL JSON before constructing typed values. Re-encoding
    # them could normalize Decimal/datetime text and change a historical hash.
    payload = row["fact_payload"]
    if not isinstance(payload, dict):
        raise BillingRuleViolation(
            "document_fact_corrupt", "stored invoice document fact must be a V1 object"
        )
    payload_digest = payload.get("presentation_model_digest")
    if (
        not isinstance(payload_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", payload_digest) is None
    ):
        raise BillingRuleViolation(
            "document_fact_corrupt",
            "stored invoice document fact requires a V1 presentation digest",
        )
    semantic_payload = dict(payload)
    semantic_payload.pop("presentation_model_digest")
    if (
        payload_digest != row["presentation_model_digest"]
        or fingerprint_of(semantic_payload) != row["presentation_model_digest"]
    ):
        raise BillingRuleViolation(
            "document_fact_digest_mismatch",
            "stored invoice document fact presentation digest disagrees",
        )
    fact = decode_invoice_document_fact(payload)
    if (
        fact.scope != scope
        or fact.invoice_id != row["document_id"]
        or fact.fact_version != row["fact_version"]
    ):
        raise BillingRuleViolation(
            "document_fact_corrupt",
            "stored invoice document fact envelope disagrees with its payload",
        )
    return StoredInvoiceDocumentFactV1(
        fact_id=row["id"],
        contract_version="billing.invoice.document.fact.v1",
        recorded_at=row["created_at"],
        fact=fact,
    )


__all__ = ["get_invoice_document_fact", "get_withholding_discharge"]


def get_withholding_discharge(
    db: Session, *, scope: Scope, discharge_id: UUID
) -> WithholdingDischargeEffectV1:
    """Read immutable discharge evidence; claims and certificates have no balance."""
    if not isinstance(scope, TenantScope | PlatformScope):
        raise TypeError("withholding query requires an explicit scope")
    table = (
        models.WithholdingDischarge.__table__
        if isinstance(scope, TenantScope)
        else models.PlatformWithholdingDischarge.__table__
    )
    statement = select(table).where(table.c.id == discharge_id)
    if isinstance(scope, TenantScope):
        statement = statement.where(table.c.tenant_id == scope.tenant_id)
    with db.no_autoflush:
        row = db.execute(statement).mappings().one_or_none()
    if row is None:
        raise BillingRuleViolation("not_found", "withholding discharge was not found")
    try:
        effect = cast(
            WithholdingDischargeEffectV1,
            _decode(row["evidence"], WithholdingDischargeEffectV1),
        )
        if (
            effect.discharge_id != row["id"]
            or effect.document_id != row["document_id"]
            or effect.accepted_delta.amount != row["amount_delta"]
            or effect.accepted_delta.currency.code != row["currency"]
            or effect.accepted_delta.currency.minor_units != row["minor_units"]
            or effect.claimed_amount.amount != row["claimed_amount"]
            or effect.claimed_amount.currency != effect.accepted_delta.currency
            or effect.claim_ref != row["claim_ref"]
            or effect.settlement_id != row["settlement_id"]
            or effect.allocation_id != row["allocation_id"]
            or effect.reverses_discharge_id != row["reverses_discharge_id"]
            or effect.tax_evidence.scope != scope
            or effect.tax_evidence.document_id != row["document_id"]
            or effect.tax_evidence.billing_account_id != row["billing_account_id"]
            or effect.tax_evidence.amount.currency != effect.accepted_delta.currency
            or effect.tax_evidence.source_owner != row["tax_source_owner"]
            or effect.tax_evidence.determination_ref != row["determination_ref"]
            or effect.tax_evidence.component_ref != row["component_ref"]
        ):
            raise ValueError("stored withholding identities disagree")
        return effect
    except (ValueError, TypeError, DecimalException) as exc:
        raise BillingRuleViolation(
            "withholding_evidence_corrupt", "stored withholding evidence is invalid"
        ) from exc
