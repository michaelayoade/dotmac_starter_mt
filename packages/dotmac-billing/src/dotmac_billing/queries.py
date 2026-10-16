"""Read-only Billing queries over persisted evidence and current identities."""

from __future__ import annotations

import re
from decimal import Decimal, DecimalException, localcontext
from typing import Any, Literal, NoReturn, cast
from uuid import UUID

from dotmac_kernel.cache import PlatformScope, Scope, TenantScope
from dotmac_kernel.fingerprints import fingerprint_of
from dotmac_kernel.money import Currency, Money, MoneyError
from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_billing import models
from dotmac_billing._document_facts import _decode, decode_invoice_document_fact
from dotmac_billing._opening_values import cash_decimal_context
from dotmac_billing.contracts import (
    DOCUMENT_FACT_CONTRACT,
    BillingAccountObservationV1,
    CurrentCashCreditDrawV1,
    CurrentReceivablePositionV1,
    CurrentSettlementAllocationV1,
    GetBillingAccountObservationV1,
    GetCurrentCashCreditDrawV1,
    GetCurrentReceivablePositionV1,
    GetCurrentSettlementAllocationV1,
    GetInvoiceDocumentFactV1,
    NonCashCreditEffectV1,
    NonCashCreditGrantV1,
    ReceivablePositionV2,
    StoredInvoiceDocumentFactV1,
    WithholdingDischargeEffectV1,
)
from dotmac_billing.errors import BillingRuleViolation


def get_billing_account_observation(
    db: Session, *, scope: Scope, request: GetBillingAccountObservationV1
) -> BillingAccountObservationV1:
    """Observe this statement's persisted account identity without ORM state.

    No lock or cached admission token is supplied. The caller owns the
    transaction and must re-observe after mutable link changes or deletion.
    """
    if not isinstance(request, GetBillingAccountObservationV1):
        raise BillingRuleViolation(
            "account_observation_request_invalid", "unsupported account query request"
        )
    # Validate even a request assembled without the dataclass constructor.
    GetBillingAccountObservationV1(request.scope, request.account_id)
    if not isinstance(scope, TenantScope | PlatformScope) or request.scope != scope:
        raise BillingRuleViolation(
            "scope_mismatch", "account observation request must match the routed scope"
        )
    table = (
        models.BillingAccount.__table__
        if isinstance(scope, TenantScope)
        else models.PlatformBillingAccount.__table__
    )
    statement = select(table).where(table.c.id == request.account_id)
    if isinstance(scope, TenantScope):
        statement = statement.where(table.c.tenant_id == scope.tenant_id)
    with db.no_autoflush:
        row = db.execute(statement).mappings().one_or_none()
    if row is None:
        raise BillingRuleViolation(
            "account_observation_not_found", "billing account was not found"
        )
    try:
        code, minor_units, external_ref = (
            row["currency"],
            row["minor_units"],
            row["external_account_ref"],
        )
        if (
            row["id"] != request.account_id
            or (isinstance(scope, TenantScope) and row["tenant_id"] != scope.tenant_id)
            or not isinstance(external_ref, str)
            or not external_ref.strip()
            or len(external_ref) > 255
            or not isinstance(code, str)
            or not re.fullmatch(r"[A-Z]{3}", code)
            or type(minor_units) is not int
            or not 0 <= minor_units <= 6
        ):
            raise ValueError("invalid persisted account identity")
        return BillingAccountObservationV1(
            scope, row["id"], external_ref, Currency(code, minor_units)
        )
    except (ValueError, TypeError, KeyError, MoneyError) as exc:
        raise BillingRuleViolation(
            "account_observation_corrupt", "stored billing account identity is invalid"
        ) from exc


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
    "get_billing_account_observation",
    "get_current_settlement_allocation",
    "get_current_cash_credit_draw",
    "get_current_receivable_position",
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


def get_current_settlement_allocation(
    db: Session, *, scope: Scope, request: GetCurrentSettlementAllocationV1
) -> CurrentSettlementAllocationV1:
    """Observe exact current allocation under the Billing account lock.

    READ COMMITTED is required: a lock does not refresh an older repeatable-read
    snapshot. Uses persisted scalars, never dirty/cached ORM entities; no flush,
    commit or rollback. The caller retains the lock until transaction end.
    """
    if not isinstance(request, GetCurrentSettlementAllocationV1):
        raise BillingRuleViolation(
            "allocation_observation_request_invalid", "unsupported allocation request"
        )
    GetCurrentSettlementAllocationV1(
        request.scope,
        request.billing_account_id,
        request.posting_group_id,
        request.settlement_id,
        request.document_id,
        request.amount,
    )
    if not isinstance(scope, TenantScope | PlatformScope) or request.scope != scope:
        raise BillingRuleViolation(
            "scope_mismatch", "allocation observation scope disagrees"
        )
    # These are Billing-owned scalar helpers and do not import the command
    # service (which itself consumes this query module).
    from dotmac_billing._receivable_backing import resolve_receivable_backing
    from dotmac_billing.opening_queries import _scoped, _table

    def refused(message: str) -> NoReturn:
        raise BillingRuleViolation("allocation_observation_unavailable", message)

    def rows(name: str, **filters: object) -> list[Any]:
        table = _table(scope, name)
        statement = select(table)
        for key, value in filters.items():
            statement = statement.where(table.c[key] == value)
        return list(db.execute(_scoped(statement, scope, table)).mappings())

    def one(name: str, row_id: UUID) -> Any:
        found = rows(name, id=row_id)
        if len(found) != 1:
            refused("exact allocation identity was not found")
        return found[0]

    currency = request.amount.currency

    def envelope(row: Any) -> None:
        if (
            row["billing_account_id"] != request.billing_account_id
            or row["currency"] != currency.code
            or row["minor_units"] != currency.minor_units
        ):
            refused("allocation account or currency identity disagrees")

    with db.no_autoflush, localcontext(cash_decimal_context()):
        if db.connection().get_isolation_level() != "READ COMMITTED":
            refused("current allocation observation requires READ COMMITTED")
        account = _table(scope, "billing_accounts")
        found = (
            db.execute(
                _scoped(
                    select(account)
                    .where(account.c.id == request.billing_account_id)
                    .with_for_update(),
                    scope,
                    account,
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            found is None
            or found["currency"] != currency.code
            or found["minor_units"] != currency.minor_units
        ):
            refused("allocation account was not found with exact currency")
        group = one("posting_groups", request.posting_group_id)
        settlement = one("confirmed_settlements", request.settlement_id)
        document = one("documents", request.document_id)
        for row in (group, settlement, document):
            envelope(row)
        if (
            group["group_kind"] != "allocation"
            or group["reverses_group_id"] is not None
        ):
            refused("exact posting group is not an original allocation")
        if (
            document["document_kind"] != "invoice"
            or document["issued_at"] is None
            or rows(
                "document_events", document_id=request.document_id, event_kind="voided"
            )
            or not resolve_receivable_backing(
                db, scope=scope, document_id=request.document_id
            ).active
        ):
            refused("current invoice backing is unavailable")
        roots = rows("allocation_effects", posting_group_id=group["id"])
        if len(roots) != 1:
            refused("exact allocation posting group is ambiguous")
        root = roots[0]
        envelope(root)
        if (
            root["settlement_id"] != request.settlement_id
            or root["document_id"] != request.document_id
            or root["effect_kind"] != "allocation"
            or root["offsets_allocation_id"] is not None
            or root["amount_delta"] != request.amount.amount
        ):
            refused("exact original allocation identity or amount disagrees")
        funding_lane = settlement["funding_lane"]
        if funding_lane not in ("available_credit", "prepaid_funding"):
            refused("settlement funding lane is invalid")

        def effects(posting: Any, expected: dict[str, Decimal]) -> None:
            envelope(posting)
            actual = rows("posting_effects", posting_group_id=posting["id"])
            if len(actual) != len(expected):
                refused("posting effects are ambiguous")
            seen: set[str] = set()
            for effect in actual:
                envelope(effect)
                lane = effect["lane"]
                if (
                    lane in seen
                    or lane not in expected
                    or effect["amount_delta"] != expected[lane]
                ):
                    refused("posting effect amount or lane disagrees")
                seen.add(lane)

        effects(
            group,
            {"receivable": -root["amount_delta"], funding_lane: -root["amount_delta"]},
        )
        # Reallocation has no original-allocation pointer. Even if pair totals
        # happen to match, they cannot prove this particular root remains funded.
        if rows(
            "allocation_effects",
            settlement_id=request.settlement_id,
            document_id=request.document_id,
            effect_kind="reallocation",
        ):
            refused("unlinked reallocation cannot prove exact allocation remaining")
        visited: set[UUID] = set()

        def net(parent: Any) -> Decimal:
            envelope(parent)
            if (
                parent["id"] in visited
                or len(visited) >= 128
                or parent["settlement_id"] != request.settlement_id
                or parent["document_id"] != request.document_id
                or not parent["amount_delta"].is_finite()
                or parent["amount_delta"] == 0
            ):
                refused("allocation inverse lineage is malformed")
            visited.add(parent["id"])
            total = parent["amount_delta"]
            children = rows("allocation_effects", offsets_allocation_id=parent["id"])
            reversals = rows(
                "posting_groups", reverses_group_id=parent["posting_group_id"]
            )
            if len(reversals) > 1 or {row["id"] for row in reversals} != {
                row["posting_group_id"]
                for row in children
                if row["effect_kind"] == "reversal"
            }:
                refused("allocation reversal group lacks its exact inverse edge")
            for child in children:
                child_group = one("posting_groups", child["posting_group_id"])
                envelope(child_group)
                if child["effect_kind"] == "deallocation":
                    if (
                        parent["id"] != root["id"]
                        or child_group["group_kind"] != "deallocation"
                        or child_group["reverses_group_id"] is not None
                        or child["amount_delta"] >= 0
                    ):
                        refused("deallocation lineage is malformed")
                elif child["effect_kind"] == "reversal":
                    if (
                        child_group["group_kind"] != "reversal"
                        or child_group["reverses_group_id"]
                        != parent["posting_group_id"]
                        or child["amount_delta"] != -parent["amount_delta"]
                    ):
                        refused("allocation reversal lineage disagrees")
                    if (
                        len(
                            rows(
                                "posting_groups",
                                reverses_group_id=parent["posting_group_id"],
                            )
                        )
                        != 1
                    ):
                        refused("allocation reversal lineage branches")
                else:
                    refused("allocation inverse kind is unsupported")
                if (
                    len(rows("allocation_effects", posting_group_id=child_group["id"]))
                    != 1
                ):
                    refused("allocation inverse posting group is ambiguous")
                effects(
                    child_group,
                    {
                        "receivable": -child["amount_delta"],
                        funding_lane: -child["amount_delta"],
                    },
                )
                total += net(child)
            if (
                parent["amount_delta"] > 0 and not 0 <= total <= parent["amount_delta"]
            ) or (
                parent["amount_delta"] < 0 and not parent["amount_delta"] <= total <= 0
            ):
                refused("allocation inverse net exceeds its original amount")
            return total

        remaining = net(root)
        acceptances = rows(
            "posting_groups",
            source_ref=f"settlement:{request.settlement_id}",
            group_kind="settlement_accepted",
        )
        if len(acceptances) != 1 or acceptances[0]["reverses_group_id"] is not None:
            refused("settlement acceptance backing is ambiguous")
        current = acceptances[0]
        expected = settlement["amount"]
        if (
            not expected.is_finite()
            or expected <= 0
            or not settlement["confirmation_evidence"]
        ):
            refused("confirmed settlement backing is invalid")
        active = True
        lineage: set[UUID] = set()
        while True:
            if current["id"] in lineage:
                refused("settlement reversal lineage cycles")
            lineage.add(current["id"])
            effects(current, {funding_lane: expected})
            if rows("allocation_effects", posting_group_id=current["id"]):
                refused("settlement backing contains unexpected allocation effects")
            children = rows("posting_groups", reverses_group_id=current["id"])
            if not children:
                break
            if len(children) != 1 or children[0]["group_kind"] != "reversal":
                refused("settlement reversal lineage is ambiguous")
            current = children[0]
            expected = -expected
            active = not active
        used = Decimal("0")
        for movement in rows("allocation_effects", settlement_id=request.settlement_id):
            envelope(movement)
            if not movement["amount_delta"].is_finite():
                refused("settlement usage is malformed")
            used += movement["amount_delta"]
        if not 0 <= used <= settlement["amount"]:
            refused("settlement usage exceeds confirmed backing")
        state: Literal["current", "reduced", "removed", "inactive_backing"] = (
            "inactive_backing"
            if not active
            else "removed"
            if remaining == 0
            else "current"
            if remaining == root["amount_delta"]
            else "reduced"
        )
        return CurrentSettlementAllocationV1(
            scope,
            request.billing_account_id,
            request.posting_group_id,
            request.settlement_id,
            request.document_id,
            request.amount,
            Money.of(remaining, currency),
            state,
        )


def get_current_cash_credit_draw(
    db: Session, *, scope: Scope, request: GetCurrentCashCreditDrawV1
) -> CurrentCashCreditDrawV1:
    """Observe exact pooled funding while retaining the account fence to commit."""
    from dotmac_billing._cash_draw_queries import observe_cash_credit_draw

    return observe_cash_credit_draw(db, scope=scope, request=request)


def get_current_receivable_position(
    db: Session, *, scope: Scope, request: GetCurrentReceivablePositionV1
) -> CurrentReceivablePositionV1:
    """Re-observe actual latest persisted account history under its outer fence.

    This is current admission evidence in a read-write READ COMMITTED caller
    transaction. It is distinct from reading a caller-selected historical V2.
    """
    from sqlalchemy import text

    from dotmac_billing.engine import Effect, EffectLane, rebuild_position
    from dotmac_billing.opening_queries import _scoped, _table

    def refuse(message: str) -> NoReturn:
        raise BillingRuleViolation("current_position_unavailable", message)

    if type(request) is not GetCurrentReceivablePositionV1:
        refuse("unsupported current position request")
    request.__post_init__()
    if not isinstance(scope, TenantScope | PlatformScope) or scope != request.scope:
        refuse("current position scope disagrees")
    currency = request.currency

    def rows(name: str) -> list[Any]:
        table = _table(scope, name)
        account_filter = (
            table.c.fact_payload["billing_account_id"].as_string()
            == str(request.billing_account_id)
            if name == "accounting_facts"
            else table.c.billing_account_id == request.billing_account_id
        )
        return list(
            db.execute(
                _scoped(select(table).where(account_filter), scope, table)
            ).mappings()
        )

    with db.no_autoflush, localcontext(cash_decimal_context()):
        if (
            db.connection().get_isolation_level() != "READ COMMITTED"
            or db.execute(text("SHOW transaction_read_only")).scalar_one() != "off"
        ):
            refuse("current position requires read-write READ COMMITTED")
        table = _table(scope, "billing_accounts")
        account = (
            db.execute(
                _scoped(
                    select(table)
                    .where(table.c.id == request.billing_account_id)
                    .with_for_update(),
                    scope,
                    table,
                )
            )
            .mappings()
            .one_or_none()
        )
        if account is None or (account["currency"], account["minor_units"]) != (
            currency.code,
            currency.minor_units,
        ):
            refuse("current position account currency disagrees")
        groups = rows("posting_groups")
        if not groups:
            for name in (
                "posting_effects",
                "allocation_effects",
                "accounting_facts",
                "receivable_position_facts",
                "receivable_position_facts_v2",
                "confirmed_settlements",
                "cash_source_claims",
                "withholding_discharges",
                "non_cash_credit_grants",
                "non_cash_credit_effects",
                "opening_import_receipts",
                "opening_cash_lots",
                "opening_cash_effects",
                "opening_receivable_bases",
                "receivable_exposure_facts",
                "receivable_exposure_facts_v2",
            ):
                if rows(name):
                    refuse("account without posting groups has monetary history")
            if any(row["issued_at"] is not None for row in rows("documents")):
                refuse("account without posting groups contains issued documents")
            return CurrentReceivablePositionV1(
                scope, request.billing_account_id, currency, 0, None
            )
        latest = max(groups, key=lambda row: row["source_version"])
        version = latest["source_version"]
        if (
            type(version) is not int
            or version < 1
            or sorted(row["source_version"] for row in groups)
            != list(range(1, version + 1))
        ):
            refuse("account posting source versions are incomplete or ambiguous")
        for row in groups:
            if (row["currency"], row["minor_units"]) != (
                currency.code,
                currency.minor_units,
            ):
                refuse("account posting currency disagrees")
        position = get_receivable_position_v2(
            db,
            scope=scope,
            billing_account_id=request.billing_account_id,
            source_version=version,
        )
        if (
            position.scope != scope
            or position.billing_account_id != request.billing_account_id
            or position.source_version != version
            or position.posting_group_watermark != latest["id"]
            or position.source_owner != "dotmac-billing"
            or position.source_authority != "internal"
            or position.projection_mode != "authoritative"
            or position.derived_from != "posting_groups"
            or position.completeness != "complete"
            or position.completeness_reason_code is not None
            or any(
                money.currency != currency
                for money in (
                    position.collectible_receivable,
                    position.available_credit,
                    position.prepaid_funding,
                    position.available_non_cash_credit,
                )
            )
        ):
            refuse("latest account position is not exact complete Billing evidence")
        effects = rows("posting_effects")
        group_ids = {row["id"] for row in groups}
        for row in effects:
            if (
                row["posting_group_id"] not in group_ids
                or (row["currency"], row["minor_units"])
                != (currency.code, currency.minor_units)
                or not row["amount_delta"].is_finite()
                or Money.of(row["amount_delta"], currency).amount != row["amount_delta"]
            ):
                refuse("account posting effect envelope is corrupt")
        rebuilt = rebuild_position(
            (
                Effect(
                    EffectLane(row["lane"]),
                    row["amount_delta"],
                    row["currency"],
                    row["minor_units"],
                )
                for row in effects
            ),
            currency=currency.code,
            minor_units=currency.minor_units,
        )
        original = next(
            (
                row
                for row in rows("receivable_position_facts")
                if row["source_version"] == version
            ),
            None,
        )
        if (
            original is None
            or original["state_fingerprint"] != rebuilt.state_fingerprint
            or original["rebuild_fingerprint"] != rebuilt.state_fingerprint
            or any(
                getattr(position, field).amount != getattr(rebuilt, field)
                for field in (
                    "collectible_receivable",
                    "available_credit",
                    "prepaid_funding",
                )
            )
        ):
            refuse("latest position disagrees with persisted monetary backing")
        from dotmac_billing._receivable_backing import resolve_receivable_backing

        collectible = Decimal(0)
        for document in rows("documents"):
            if document["document_kind"] != "invoice" or document["issued_at"] is None:
                continue
            backing = resolve_receivable_backing(
                db, scope=scope, document_id=document["id"]
            )
            if backing.active:
                collectible += backing.remaining
        if collectible != rebuilt.collectible_receivable:
            refuse("current invoice backing disagrees with latest receivable")
        # Re-read under the retained fence, never bind to a caller watermark.
        observed = rows("posting_groups")
        if {row["id"] for row in observed} != group_ids or max(
            row["source_version"] for row in observed
        ) != version:
            refuse("account source version changed during observation")
        return CurrentReceivablePositionV1(
            scope, request.billing_account_id, currency, version, position
        )
