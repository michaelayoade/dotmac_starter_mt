"""Read-only Billing queries over persisted immutable evidence."""

from __future__ import annotations

import re
from decimal import Decimal, DecimalException
from typing import Any, cast
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
    NonCashCreditEffectV1,
    NonCashCreditGrantV1,
    ReceivablePositionV2,
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


__all__ = [
    "get_receivable_position_v2",
    "get_non_cash_credit_grant",
    "get_non_cash_credit_effect",
    "get_invoice_document_fact",
    "get_withholding_discharge",
]


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


def get_non_cash_credit_grant(
    db: Session, *, scope: Scope, grant_id: UUID
) -> NonCashCreditGrantV1:
    """Return only the immutable original grant, never a mutable balance."""
    return cast(
        NonCashCreditGrantV1,
        _non_cash_evidence(db, scope=scope, row_id=grant_id, grant=True),
    )


def get_non_cash_credit_effect(
    db: Session, *, scope: Scope, effect_id: UUID
) -> NonCashCreditEffectV1:
    return cast(
        NonCashCreditEffectV1,
        _non_cash_evidence(db, scope=scope, row_id=effect_id, grant=False),
    )


def _non_cash_evidence(
    db: Session, *, scope: Scope, row_id: UUID, grant: bool
) -> NonCashCreditGrantV1 | NonCashCreditEffectV1:
    if not isinstance(scope, TenantScope | PlatformScope):
        raise TypeError("noncash query requires explicit scope")
    table = (
        (models.NonCashCreditGrant if grant else models.NonCashCreditEffect)
        if isinstance(scope, TenantScope)
        else (
            models.PlatformNonCashCreditGrant
            if grant
            else models.PlatformNonCashCreditEffect
        )
    ).__table__
    statement = select(table).where(table.c.id == row_id)
    if isinstance(scope, TenantScope):
        statement = statement.where(table.c.tenant_id == scope.tenant_id)
    with db.no_autoflush:
        row = db.execute(statement).mappings().one_or_none()
    if row is None:
        raise BillingRuleViolation("not_found", "noncash credit fact was not found")
    try:
        payload = row["evidence"]
        if (
            not isinstance(payload, dict)
            or fingerprint_of(payload) != row["evidence_digest"]
        ):
            raise ValueError("original evidence digest disagrees")
        fact = _decode(
            payload, NonCashCreditGrantV1 if grant else NonCashCreditEffectV1
        )
        if (
            not isinstance(fact, NonCashCreditGrantV1 | NonCashCreditEffectV1)
            or fact.scope != scope
        ):
            raise ValueError("scope disagrees")
        for name in (
            "billing_account_id",
            "source_owner",
            "source_ref",
            "source_version",
            "authorization_actor_ref",
            "authorization_evidence_ref",
        ):
            if getattr(fact, name) != row[name]:
                raise ValueError("fact envelope disagrees")
        amount = (
            fact.original_amount
            if isinstance(fact, NonCashCreditGrantV1)
            else fact.availability_delta
        )
        if (
            amount.currency.code != row["currency"]
            or amount.currency.minor_units != row["minor_units"]
        ):
            raise ValueError("money envelope disagrees")
        if isinstance(fact, NonCashCreditGrantV1):
            if (
                fact.grant_id != row["id"]
                or amount.amount != row["original_amount"]
                or fact.restricted_document_id != row["restricted_document_id"]
                or fact.replaces_grant_id != row["replaces_grant_id"]
                or fact.replaces_effect_id != row["replaces_effect_id"]
            ):
                raise ValueError("grant envelope disagrees")
            for name in (
                "category",
                "config_owner",
                "config_ref",
                "config_version",
                "config_digest",
            ):
                if getattr(fact.classification, name) != row[name]:
                    raise ValueError("classification envelope disagrees")
        elif (
            fact.effect_id != row["id"]
            or amount.amount != row["availability_delta"]
            or any(
                getattr(fact, name) != row[name]
                for name in (
                    "grant_id",
                    "effect_kind",
                    "document_id",
                    "original_application_id",
                )
            )
        ):
            raise ValueError("effect envelope disagrees")
        return fact
    except (ValueError, TypeError, KeyError, DecimalException) as exc:
        raise BillingRuleViolation(
            "non_cash_credit_evidence_corrupt",
            "stored noncash credit evidence is invalid",
        ) from exc


def get_receivable_position_v2(
    db: Session, *, scope: Scope, billing_account_id: UUID, source_version: int
) -> ReceivablePositionV2:
    """Read and verify an exact immutable four-lane snapshot, without autoflush."""
    if not isinstance(scope, TenantScope | PlatformScope) or source_version < 1:
        raise BillingRuleViolation(
            "position_request_invalid",
            "position requires explicit scope and positive source version",
        )
    tenant = isinstance(scope, TenantScope)
    position = (
        models.ReceivablePositionFactV2
        if tenant
        else models.PlatformReceivablePositionFactV2
    ).__table__
    old_position = (
        models.ReceivablePositionFact
        if tenant
        else models.PlatformReceivablePositionFact
    ).__table__
    effect = (
        models.NonCashCreditEffect if tenant else models.PlatformNonCashCreditEffect
    ).__table__
    group = (models.PostingGroup if tenant else models.PlatformPostingGroup).__table__

    def scoped(statement: Any, table: Any) -> Any:
        return (
            statement.where(table.c.tenant_id == scope.tenant_id)
            if isinstance(scope, TenantScope)
            else statement
        )

    with db.no_autoflush:
        row = (
            db.execute(
                scoped(
                    select(position).where(
                        position.c.billing_account_id == billing_account_id,
                        position.c.source_version == source_version,
                    ),
                    position,
                )
            )
            .mappings()
            .one_or_none()
        )
        original = (
            db.execute(
                scoped(
                    select(old_position).where(
                        old_position.c.billing_account_id == billing_account_id,
                        old_position.c.source_version == source_version,
                    ),
                    old_position,
                )
            )
            .mappings()
            .one_or_none()
        )
        statement = select(
            effect.c.id, effect.c.posting_group_id, effect.c.availability_delta
        ).join(
            group,
            (group.c.id == effect.c.posting_group_id)
            & (group.c.billing_account_id == effect.c.billing_account_id),
        )
        if tenant:
            statement = statement.where(group.c.tenant_id == effect.c.tenant_id)
        rows = db.execute(
            scoped(
                statement.where(
                    effect.c.billing_account_id == billing_account_id,
                    group.c.source_version <= source_version,
                ).order_by(effect.c.id),
                effect,
            )
        ).all()
    if row is None:
        raise BillingRuleViolation(
            "not_found", "exact V2 receivable position was not found"
        )
    try:
        if original is None:
            raise ValueError("position lacks its V1 source")
        for value in rows:
            fact = get_non_cash_credit_effect(db, scope=scope, effect_id=value.id)
            get_non_cash_credit_grant(db, scope=scope, grant_id=fact.grant_id)
        total = sum((value.availability_delta for value in rows), Decimal("0"))
        fingerprint = fingerprint_of(
            {
                "contract": "billing.receivable.position.v2",
                "v1_fingerprint": original["state_fingerprint"],
                "non_cash_effects": [
                    {
                        "id": str(value.id),
                        "posting_group_id": str(value.posting_group_id),
                        "availability_delta": str(value.availability_delta),
                    }
                    for value in rows
                ],
                "available_non_cash_credit": str(total),
            }
        )
        if (
            row["state_fingerprint"] != fingerprint
            or row["rebuild_fingerprint"] != fingerprint
            or total != row["available_non_cash_credit"]
        ):
            raise ValueError("position fingerprint disagrees")
        # Every duplicated source/projection field must retain V1's meaning.
        # IDs, creation timestamps and the intentionally versioned fingerprints
        # belong to each snapshot independently.
        for name in old_position.c.keys():
            if name in {"id", "created_at", "state_fingerprint", "rebuild_fingerprint"}:
                continue
            if row[name] != original[name]:
                raise ValueError("position source envelope disagrees")
        from dotmac_kernel.money import Currency, Money

        currency = Currency(row["currency"], row["minor_units"])
        values = {
            name: row[name]
            for name in (
                "source_owner",
                "billing_account_id",
                "source_version",
                "posting_group_watermark",
                "source_authority",
                "projection_mode",
                "derived_from",
                "completeness",
                "completeness_reason_code",
                "state_fingerprint",
                "observed_at",
                "financial_state",
            )
        }
        for name in (
            "collectible_receivable",
            "available_credit",
            "prepaid_funding",
            "available_non_cash_credit",
        ):
            values[name] = Money.of(row[name], currency)
        return ReceivablePositionV2(scope=scope, **values)
    except (TypeError, ValueError, DecimalException) as exc:
        raise BillingRuleViolation(
            "non_cash_credit_position_corrupt", "stored V2 position is invalid"
        ) from exc
