"""Flush-only Billing persistence service over one shared rule engine."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, fields, is_dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from dotmac_kernel.cache import Scope, TenantScope
from dotmac_kernel.idempotency import (
    IdempotentOutcome,
    Operation,
    execute_once,
    execute_once_platform,
    fingerprint_of,
)
from dotmac_kernel.messaging import enqueue_event, enqueue_platform_event
from dotmac_kernel.money import Currency, Money
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dotmac_billing import models
from dotmac_billing.commands import (
    AllocationCommand,
    CreateDraftDocument,
    DeallocationCommand,
    IssueCreditNote,
    IssueDocument,
    NumberingProvider,
    ReallocationCommand,
    RefundCommand,
    ReversePostingGroupCommand,
)
from dotmac_billing.contracts import (
    DOCUMENT_FACT_CONTRACT,
    AcceptRatedObligationV1,
    AcceptSettlementV1,
    AccountingAllocationEffectV1,
    AccountingEffectV1,
    AccountingFactV1,
    AppliedFxSnapshotV1,
    AppliedTaxSnapshotV1,
    CollectionTiming,
    DueDateBasisStatus,
    DueDateBasisV1,
    FinancialState,
    InvoiceDocumentFactV1,
    InvoiceLineFactV1,
    PartyDocumentSnapshotV1,
    PartyTaxIdentitySnapshotV1,
    PaymentInstructionsSnapshotV1,
    PostalAddressSnapshotV1,
    PresentationAssetReferenceV1,
    ReceivableExposureV1,
    ReceivablePositionV1,
    RecordDocumentArtifactV1,
    RegisteredIdentifierSnapshotV1,
    RepairDocumentArtifactV1,
    ServicePeriodEvidenceV1,
    ServicePeriodStatus,
)
from dotmac_billing.engine import Effect, EffectLane, PositionState, rebuild_position
from dotmac_billing.errors import BillingConflict, BillingRuleViolation

_AccountingEffectKind = Literal[
    "invoice_issued",
    "credit_note_issued",
    "settlement_accepted",
    "allocation",
    "deallocation",
    "reallocation",
    "refund",
    "reversal",
]

_AccountRow = models.BillingAccount | models.PlatformBillingAccount
_ObligationRow = models.RatedObligation | models.PlatformRatedObligation
_DocumentRow = models.BillingDocument | models.PlatformBillingDocument
_SettlementRow = models.ConfirmedSettlement | models.PlatformConfirmedSettlement
_PostingGroupRow = models.PostingGroup | models.PlatformPostingGroup
_DocumentFactRow = models.InvoiceDocumentFact | models.PlatformInvoiceDocumentFact
_ArtifactRow = models.DocumentArtifact | models.PlatformDocumentArtifact


def _money(amount: Decimal, currency_code: str, minor_units: int) -> Money:
    """Rehydrate persisted exact money through the kernel-owned value type."""

    if not 0 <= minor_units <= 6:
        raise BillingRuleViolation(
            "minor_units_unsupported",
            "Billing persistence supports zero through six minor units",
        )
    return Money.of(amount, Currency(currency_code, minor_units))


@dataclass(frozen=True, slots=True)
class _PlaneModels:
    # SQLAlchemy cannot preserve mapped attributes through a union of mirrored
    # tenant/platform classes. Keep the dynamic type at this one private plane
    # dispatch boundary; published contracts and service returns stay concrete.
    account: Any
    obligation: Any
    document: Any
    line: Any
    event: Any
    settlement: Any
    group: Any
    effect: Any
    allocation: Any
    tax: Any
    fx: Any
    party_tax: Any
    document_fact: Any
    artifact: Any
    accounting_fact: Any
    position_fact: Any
    exposure_fact: Any


@dataclass(frozen=True, slots=True)
class _AllocationInput:
    """Typed internal posting detail; deliberately not a published contract."""

    settlement_id: UUID
    document_id: UUID | None
    effect_kind: Literal[
        "allocation", "deallocation", "reallocation", "refund", "reversal"
    ]
    amount_delta: Decimal
    offsets_allocation_id: UUID | None = None


_TENANT = _PlaneModels(
    models.BillingAccount,
    models.RatedObligation,
    models.BillingDocument,
    models.DocumentLine,
    models.DocumentEvent,
    models.ConfirmedSettlement,
    models.PostingGroup,
    models.PostingEffect,
    models.AllocationEffect,
    models.AppliedTaxSnapshot,
    models.AppliedFxSnapshot,
    models.PartyTaxIdentitySnapshot,
    models.InvoiceDocumentFact,
    models.DocumentArtifact,
    models.AccountingFact,
    models.ReceivablePositionFact,
    models.ReceivableExposureFact,
)
_PLATFORM = _PlaneModels(
    models.PlatformBillingAccount,
    models.PlatformRatedObligation,
    models.PlatformBillingDocument,
    models.PlatformDocumentLine,
    models.PlatformDocumentEvent,
    models.PlatformConfirmedSettlement,
    models.PlatformPostingGroup,
    models.PlatformPostingEffect,
    models.PlatformAllocationEffect,
    models.PlatformAppliedTaxSnapshot,
    models.PlatformAppliedFxSnapshot,
    models.PlatformPartyTaxIdentitySnapshot,
    models.PlatformInvoiceDocumentFact,
    models.PlatformDocumentArtifact,
    models.PlatformAccountingFact,
    models.PlatformReceivablePositionFact,
    models.PlatformReceivableExposureFact,
)


def _models(scope: Scope) -> _PlaneModels:
    return _TENANT if isinstance(scope, TenantScope) else _PLATFORM


def _values(scope: Scope, values: dict[str, object]) -> dict[str, object]:
    if isinstance(scope, TenantScope):
        return {"tenant_id": scope.tenant_id, **values}
    return values


def _where_scope(statement: Any, scope: Scope, model: Any) -> Any:
    if isinstance(scope, TenantScope):
        return statement.where(model.tenant_id == scope.tenant_id)
    return statement


def _wire_value(value: object) -> object:
    if isinstance(value, Money):
        return {
            "amount": str(value.amount),
            "currency": value.currency.code,
            "minor_units": value.currency.minor_units,
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID | Decimal):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _wire_value(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): _wire_value(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_wire_value(item) for item in value]
    return value


def _jsonable(value: object) -> dict[str, object]:
    encoded = _wire_value(value)
    if not isinstance(encoded, dict):
        raise TypeError("Billing evidence payloads must encode to an object")
    return cast(dict[str, object], encoded)


def _idempotent(
    db: Session,
    *,
    scope: Scope,
    operation_scope: str,
    key: str,
    fingerprint: str,
    operation: Operation,
) -> IdempotentOutcome:
    if isinstance(scope, TenantScope):
        return execute_once(
            db,
            tenant_id=scope.tenant_id,
            scope=operation_scope,
            key=key,
            fingerprint=fingerprint,
            operation=operation,
        )
    return execute_once_platform(
        db,
        scope=operation_scope,
        key=key,
        fingerprint=fingerprint,
        operation=operation,
    )


def _emit(
    db: Session,
    *,
    scope: Scope,
    event_type: str,
    payload: dict[str, object],
    correlation_id: str | None = None,
) -> None:
    if isinstance(scope, TenantScope):
        enqueue_event(
            db,
            tenant_id=scope.tenant_id,
            event_type=event_type,
            payload=payload,
            correlation_id=correlation_id,
        )
    else:
        enqueue_platform_event(
            db,
            event_type=event_type,
            payload=payload,
            correlation_id=correlation_id,
        )


def _one(
    db: Session,
    *,
    scope: Scope,
    model: Any,
    row_id: UUID,
    lock: bool = False,
) -> Any:
    statement = select(model).where(model.id == row_id)
    statement = _where_scope(statement, scope, model)
    if lock:
        statement = statement.with_for_update()
    row = db.execute(statement).scalar_one_or_none()
    if row is None:
        raise BillingRuleViolation(
            "not_found", f"{model.__name__} was not found", row_id=str(row_id)
        )
    return row


def _require_account_money(account: _AccountRow, money: Money) -> None:
    if (
        account.currency != money.currency.code
        or account.minor_units != money.currency.minor_units
    ):
        raise BillingRuleViolation(
            "money_identity_mismatch",
            "account currency and minor-unit precision must match the command",
        )


def _require_same_money(*amounts: Money) -> None:
    if not amounts:
        return
    identity = (amounts[0].currency.code, amounts[0].currency.minor_units)
    if any(
        (value.currency.code, value.currency.minor_units) != identity
        for value in amounts[1:]
    ):
        raise BillingRuleViolation(
            "mixed_currency", "one Billing command may carry only one currency"
        )


def _require_scope_matches(*, routed: Scope, declared: Scope) -> None:
    if routed != declared:
        raise BillingRuleViolation(
            "scope_mismatch",
            "the routed persistence scope differs from the typed command scope",
        )


def create_billing_account(
    db: Session,
    *,
    scope: Scope,
    external_account_ref: str,
    currency: str,
    minor_units: int,
) -> _AccountRow:
    _money(Decimal("0"), currency, minor_units)
    plane = _models(scope)
    statement = select(plane.account).where(
        plane.account.external_account_ref == external_account_ref,
        plane.account.currency == currency,
    )
    existing = db.execute(
        _where_scope(statement, scope, plane.account)
    ).scalar_one_or_none()
    if existing is not None:
        if existing.minor_units != minor_units:
            raise BillingConflict(
                "account_precision_conflict",
                "the account currency already has a different minor-unit precision",
            )
        return cast(_AccountRow, existing)
    row = plane.account(
        **_values(
            scope,
            {
                "id": uuid4(),
                "external_account_ref": external_account_ref,
                "currency": currency,
                "minor_units": minor_units,
            },
        )
    )
    db.add(row)
    db.flush()
    return cast(_AccountRow, row)


def accept_rated_obligation(
    db: Session,
    *,
    scope: Scope,
    command: AcceptRatedObligationV1,
    accepted_source_kinds: frozenset[str],
) -> _ObligationRow:
    _require_scope_matches(routed=scope, declared=command.scope)
    if command.source_kind not in accepted_source_kinds:
        raise BillingRuleViolation(
            "unknown_obligation_source",
            "obligation source kind is not declared by the assembly",
            source_kind=command.source_kind,
        )
    _require_same_money(
        command.pre_tax_amount, command.tax_amount, command.total_amount
    )
    if (
        command.pre_tax_amount.amount + command.tax_amount.amount
        != command.total_amount.amount
    ):
        raise BillingRuleViolation(
            "obligation_total_mismatch", "pre-tax plus tax must equal total"
        )
    plane = _models(scope)
    account = _one(
        db,
        scope=scope,
        model=plane.account,
        row_id=command.billing_account_id,
        lock=True,
    )
    _require_account_money(account, command.total_amount)
    natural_payload = {
        "contract_line_ref": command.contract_line_ref,
        "contract_version": command.contract_version,
        "charge_component": command.charge_component,
        "source_fact_id": command.source_fact_id,
        "source_fact_version": command.source_fact_version,
        "period_start": command.service_period.starts_at,
        "period_end": command.service_period.ends_at,
        "currency": command.total_amount.currency.code,
    }
    natural_key = fingerprint_of(natural_payload)
    offered = fingerprint_of(asdict(command))

    def operation(session: Session) -> dict[str, object]:
        if command.supersedes_obligation_id is not None:
            # The mutable account lock acquired above serializes corrections.
            # Obligations are append-only, so online roles intentionally lack
            # the UPDATE privilege PostgreSQL requires for ``FOR UPDATE``.
            previous = _one(
                session,
                scope=scope,
                model=plane.obligation,
                row_id=command.supersedes_obligation_id,
            )
            if previous.billing_account_id != command.billing_account_id:
                raise BillingRuleViolation(
                    "obligation_correction_account_mismatch",
                    "a corrected obligation must remain on the original account",
                )
            successor_statement = select(plane.obligation.id).where(
                plane.obligation.supersedes_obligation_id == previous.id
            )
            successor = session.execute(
                _where_scope(successor_statement, scope, plane.obligation)
            ).scalar_one_or_none()
            if successor is not None:
                raise BillingConflict(
                    "obligation_already_superseded",
                    "an accepted obligation may have only one correction successor",
                )
            documented_statement = select(plane.line.id).where(
                plane.line.obligation_id == previous.id
            )
            documented = session.execute(
                _where_scope(documented_statement, scope, plane.line)
            ).scalar_one_or_none()
            if documented is not None:
                raise BillingConflict(
                    "obligation_already_documented",
                    "a documented obligation is corrected by credit note, "
                    "not re-acceptance",
                )
        obligation_id = uuid4()
        row = plane.obligation(
            **_values(
                scope,
                {
                    "id": obligation_id,
                    "billing_account_id": command.billing_account_id,
                    "natural_key_digest": natural_key,
                    "request_fingerprint": offered,
                    "contract_line_ref": command.contract_line_ref,
                    "contract_version": command.contract_version,
                    "charge_component": command.charge_component,
                    "source_system": command.source_system,
                    "source_kind": command.source_kind,
                    "source_fact_id": command.source_fact_id,
                    "source_fact_version": command.source_fact_version,
                    "subject_ref": command.subject_ref,
                    "service_ref": command.service_ref,
                    "service_period_status": command.service_period.status.value,
                    "period_start": command.service_period.starts_at,
                    "period_end": command.service_period.ends_at,
                    "collection_timing": command.collection_timing,
                    "pre_tax_amount": command.pre_tax_amount.amount,
                    "tax_amount": command.tax_amount.amount,
                    "total_amount": command.total_amount.amount,
                    "currency": command.total_amount.currency.code,
                    "minor_units": command.total_amount.currency.minor_units,
                    "rated_at": command.rated_at,
                    "price_version_id": command.price_version_id,
                    "supersedes_obligation_id": command.supersedes_obligation_id,
                },
            )
        )
        session.add(row)
        # The snapshot mappers intentionally expose only scalar foreign-key
        # facts, not ORM relationships.  Flush the obligation explicitly so
        # PostgreSQL never receives an applied-tax/FX child before its parent.
        session.flush()
        for snapshot in command.tax_snapshots:
            _require_same_money(snapshot.taxable_basis, snapshot.tax_amount)
            session.add(
                plane.tax(
                    **_values(
                        scope,
                        {
                            "id": uuid4(),
                            "obligation_id": obligation_id,
                            "document_id": None,
                            "treatment_code": snapshot.treatment_code,
                            "jurisdiction_code": snapshot.jurisdiction_code,
                            "policy_id": snapshot.policy_id,
                            "policy_version": snapshot.policy_version,
                            "rate": snapshot.rate,
                            "taxable_basis": snapshot.taxable_basis.amount,
                            "tax_amount": snapshot.tax_amount.amount,
                            "currency": snapshot.tax_amount.currency.code,
                            "minor_units": snapshot.tax_amount.currency.minor_units,
                        },
                    )
                )
            )
        if command.fx_snapshot is not None:
            fx = command.fx_snapshot
            session.add(
                plane.fx(
                    **_values(
                        scope,
                        {
                            "id": uuid4(),
                            "obligation_id": obligation_id,
                            "document_id": None,
                            **asdict(fx),
                        },
                    )
                )
            )
        session.flush()
        _emit(
            session,
            scope=scope,
            event_type="billing.obligation.accepted.v1",
            payload={
                "obligation_id": str(obligation_id),
                "source_version": command.source_fact_version,
            },
        )
        return {"obligation_id": str(obligation_id)}

    outcome = _idempotent(
        db,
        scope=scope,
        operation_scope="billing.obligation",
        key=natural_key,
        fingerprint=offered,
        operation=operation,
    )
    return cast(
        _ObligationRow,
        _one(
            db,
            scope=scope,
            model=plane.obligation,
            row_id=UUID(str(outcome.result["obligation_id"])),
        ),
    )


def create_draft_document(
    db: Session, *, scope: Scope, command: CreateDraftDocument
) -> _DocumentRow:
    plane = _models(scope)
    obligation = _one(
        db, scope=scope, model=plane.obligation, row_id=command.obligation_id
    )
    # Obligations are append-only and the online role intentionally has no
    # UPDATE privilege, so they cannot be a ``FOR UPDATE`` lock target.  The
    # mutable account is Billing's per-currency serialization root.
    _one(
        db,
        scope=scope,
        model=plane.account,
        row_id=obligation.billing_account_id,
        lock=True,
    )
    offered = fingerprint_of(asdict(command))

    def operation(session: Session) -> dict[str, object]:
        document_id = uuid4()
        document = plane.document(
            **_values(
                scope,
                {
                    "id": document_id,
                    "billing_account_id": obligation.billing_account_id,
                    "obligation_id": obligation.id,
                    "document_kind": "invoice",
                    "credits_document_id": None,
                    "lifecycle": "draft",
                    "series_code": None,
                    "document_number": None,
                    "currency": obligation.currency,
                    "minor_units": obligation.minor_units,
                    "subtotal": obligation.pre_tax_amount,
                    "tax_total": obligation.tax_amount,
                    "grand_total": obligation.total_amount,
                    "due_at": None,
                    "due_date_basis": _jsonable(command.due_date_basis),
                    "document_profile_code": command.document_profile_code,
                    "document_profile_version": command.document_profile_version,
                    "seller_snapshot": _jsonable(command.seller_snapshot),
                    "customer_snapshot": _jsonable(command.customer_snapshot),
                    "payment_instructions": _jsonable(command.payment_instructions),
                    "brand_asset": _jsonable(command.brand_asset),
                    "locale": command.locale,
                    "timezone": command.timezone,
                    "issued_at": None,
                },
            )
        )
        line = plane.line(
            **_values(
                scope,
                {
                    "id": uuid4(),
                    "document_id": document_id,
                    "obligation_id": obligation.id,
                    "line_number": 1,
                    "description": command.description,
                    "quantity": command.quantity,
                    "unit_code": command.unit_code,
                    "unit_amount": obligation.pre_tax_amount,
                    "pre_tax_amount": obligation.pre_tax_amount,
                    "tax_amount": obligation.tax_amount,
                    "total_amount": obligation.total_amount,
                    "currency": obligation.currency,
                    "minor_units": obligation.minor_units,
                    "price_source_version": obligation.price_version_id,
                },
            )
        )
        session.add_all([document, line])
        for identity in command.party_tax_identities:
            session.add(
                plane.party_tax(
                    **_values(
                        scope,
                        {"id": uuid4(), "document_id": document_id, **asdict(identity)},
                    )
                )
            )
        session.flush()
        return {"document_id": str(document_id)}

    outcome = _idempotent(
        db,
        scope=scope,
        operation_scope="billing.document.draft",
        key=str(command.obligation_id),
        fingerprint=offered,
        operation=operation,
    )
    return cast(
        _DocumentRow,
        _one(
            db,
            scope=scope,
            model=plane.document,
            row_id=UUID(str(outcome.result["document_id"])),
        ),
    )


def _next_source_version(
    db: Session, *, scope: Scope, plane: _PlaneModels, account_id: UUID
) -> int:
    statement = select(func.coalesce(func.max(plane.group.source_version), 0)).where(
        plane.group.billing_account_id == account_id
    )
    statement = _where_scope(statement, scope, plane.group)
    return int(db.execute(statement).scalar_one()) + 1


def _receivable_exposure(
    db: Session,
    *,
    scope: Scope,
    plane: _PlaneModels,
    document_id: UUID,
    source_version: int,
    posting_group_watermark: UUID,
    observed_at: datetime,
) -> ReceivableExposureV1:
    """Derive one document-grained collectible fact from immutable evidence."""

    document = _one(
        db, scope=scope, model=plane.document, row_id=document_id, lock=True
    )
    if document.document_kind != "invoice" or document.issued_at is None:
        raise BillingConflict(
            "exposure_document_invalid",
            "a receivable exposure requires one issued invoice",
        )
    if document.obligation_id is None:
        raise BillingConflict(
            "exposure_obligation_missing",
            "an invoice exposure requires rated-obligation evidence",
        )
    obligation = _one(
        db,
        scope=scope,
        model=plane.obligation,
        row_id=document.obligation_id,
    )
    void_statement = select(plane.event.id).where(
        plane.event.document_id == document.id,
        plane.event.event_kind == "voided",
    )
    voided = (
        db.execute(_where_scope(void_statement, scope, plane.event)).first() is not None
    )
    allocated = _document_allocated(db, scope=scope, document_id=document.id)
    credited = _document_credited(db, scope=scope, document_id=document.id)
    resolved_amount = allocated + credited
    outstanding = max(document.grand_total - resolved_amount, Decimal("0"))
    financial_state: FinancialState
    if voided:
        outstanding = Decimal("0")
        financial_state = "cancelled"
    elif outstanding == 0:
        financial_state = "resolved"
    elif resolved_amount > 0:
        financial_state = "partially_resolved"
    else:
        financial_state = "open"
    service_period = ServicePeriodEvidenceV1(
        status=ServicePeriodStatus(obligation.service_period_status),
        starts_at=obligation.period_start,
        ends_at=obligation.period_end,
    )
    due_date_basis = _due_date_basis(document.due_date_basis)
    collectible = _money(outstanding, document.currency, document.minor_units)
    payload = {
        "scope": scope,
        "source_owner": "dotmac-billing",
        "exposure_ref": f"document:{document.id}",
        "billing_account_id": document.billing_account_id,
        "subject_ref": obligation.subject_ref,
        "service_ref": obligation.service_ref,
        "collection_timing": obligation.collection_timing,
        "source_version": source_version,
        "posting_group_watermark": posting_group_watermark,
        "source_authority": "internal",
        "projection_mode": "authoritative",
        "derived_from": "posting_groups",
        "completeness": "complete",
        "completeness_reason_code": None,
        "observed_at": observed_at,
        "service_period": service_period,
        "due_at": document.due_at,
        "due_date_basis": due_date_basis,
        "financial_state": financial_state,
        "collectible_receivable": collectible,
    }
    return ReceivableExposureV1(
        scope=scope,
        source_owner="dotmac-billing",
        exposure_ref=f"document:{document.id}",
        billing_account_id=document.billing_account_id,
        subject_ref=obligation.subject_ref,
        service_ref=obligation.service_ref,
        collection_timing=cast(CollectionTiming, obligation.collection_timing),
        source_version=source_version,
        posting_group_watermark=posting_group_watermark,
        source_authority="internal",
        projection_mode="authoritative",
        derived_from="posting_groups",
        completeness="complete",
        completeness_reason_code=None,
        state_fingerprint=fingerprint_of(_wire_value(payload)),
        observed_at=observed_at,
        service_period=service_period,
        due_at=document.due_at,
        due_date_basis=due_date_basis,
        financial_state=financial_state,
        collectible_receivable=collectible,
    )


def _document_financial_snapshots(
    db: Session,
    *,
    scope: Scope,
    plane: _PlaneModels,
    document: _DocumentRow,
) -> tuple[tuple[AppliedTaxSnapshotV1, ...], AppliedFxSnapshotV1 | None]:
    taxes_statement = select(plane.tax).where(
        (plane.tax.document_id == document.id)
        | (plane.tax.obligation_id == document.obligation_id)
    )
    taxes = tuple(db.execute(_where_scope(taxes_statement, scope, plane.tax)).scalars())
    fx_statement = select(plane.fx).where(
        (plane.fx.document_id == document.id)
        | (plane.fx.obligation_id == document.obligation_id)
    )
    fx = db.execute(_where_scope(fx_statement, scope, plane.fx)).scalars().first()
    return (
        tuple(
            AppliedTaxSnapshotV1(
                treatment_code=row.treatment_code,
                jurisdiction_code=row.jurisdiction_code,
                policy_id=row.policy_id,
                policy_version=row.policy_version,
                rate=row.rate,
                taxable_basis=_money(row.taxable_basis, row.currency, row.minor_units),
                tax_amount=_money(row.tax_amount, row.currency, row.minor_units),
            )
            for row in taxes
        ),
        None
        if fx is None
        else AppliedFxSnapshotV1(
            observation_id=fx.observation_id,
            observation_version=fx.observation_version,
            base_currency=fx.base_currency,
            quote_currency=fx.quote_currency,
            rate=fx.rate,
            rate_purpose=fx.rate_purpose,
            observed_at=fx.observed_at,
            effective_at=fx.effective_at,
            rounding_policy=fx.rounding_policy,
            provenance=fx.provenance,
        ),
    )


def _post_group(
    db: Session,
    *,
    scope: Scope,
    account_id: UUID,
    kind: _AccountingEffectKind,
    currency: str,
    minor_units: int,
    source_ref: str,
    occurred_at: datetime,
    effects: Iterable[tuple[EffectLane, Decimal]],
    allocations: Iterable[_AllocationInput] = (),
    exposure_document_ids: Iterable[UUID] = (),
    reverses_group_id: UUID | None = None,
    tax_snapshots: tuple[AppliedTaxSnapshotV1, ...] = (),
    fx_snapshot: AppliedFxSnapshotV1 | None = None,
) -> _PostingGroupRow:
    plane = _models(scope)
    allocation_inputs = tuple(allocations)
    _one(db, scope=scope, model=plane.account, row_id=account_id, lock=True)
    group_id = uuid4()
    source_version = _next_source_version(
        db, scope=scope, plane=plane, account_id=account_id
    )
    group = plane.group(
        **_values(
            scope,
            {
                "id": group_id,
                "billing_account_id": account_id,
                "group_kind": kind,
                "currency": currency,
                "minor_units": minor_units,
                "source_ref": source_ref,
                "source_version": source_version,
                "reverses_group_id": reverses_group_id,
                "occurred_at": occurred_at,
            },
        )
    )
    db.add(group)
    # Posting models deliberately carry scalar foreign keys instead of ORM
    # relationships. Persist the parent before its effect/allocation children
    # so PostgreSQL never has to infer an insertion order it cannot see.
    db.flush()
    effect_rows = []
    for lane, amount in effects:
        row = plane.effect(
            **_values(
                scope,
                {
                    "id": uuid4(),
                    "posting_group_id": group_id,
                    "billing_account_id": account_id,
                    "lane": lane.value,
                    "amount_delta": amount,
                    "currency": currency,
                    "minor_units": minor_units,
                },
            )
        )
        effect_rows.append(row)
        db.add(row)
    allocation_rows = []
    for allocation in allocation_inputs:
        row = plane.allocation(
            **_values(
                scope,
                {
                    "id": uuid4(),
                    "posting_group_id": group_id,
                    "billing_account_id": account_id,
                    "currency": currency,
                    "minor_units": minor_units,
                    "settlement_id": allocation.settlement_id,
                    "document_id": allocation.document_id,
                    "effect_kind": allocation.effect_kind,
                    "amount_delta": allocation.amount_delta,
                    "offsets_allocation_id": allocation.offsets_allocation_id,
                },
            )
        )
        allocation_rows.append(row)
        db.add(row)
    db.flush()
    position = rebuild_receivable_position(
        db,
        scope=scope,
        billing_account_id=account_id,
        currency=currency,
        minor_units=minor_units,
        verify_latest=False,
    )
    observed_at = datetime.now(UTC)
    collectible_receivable = _money(
        position.collectible_receivable, currency, minor_units
    )
    available_credit = _money(position.available_credit, currency, minor_units)
    prepaid_funding = _money(position.prepaid_funding, currency, minor_units)
    account_financial_state: FinancialState = (
        "resolved" if position.collectible_receivable == 0 else "open"
    )
    typed_position = ReceivablePositionV1(
        scope=scope,
        source_owner="dotmac-billing",
        billing_account_id=account_id,
        source_version=source_version,
        posting_group_watermark=group_id,
        source_authority="internal",
        projection_mode="authoritative",
        derived_from="posting_groups",
        completeness="complete",
        completeness_reason_code=None,
        state_fingerprint=position.state_fingerprint,
        observed_at=observed_at,
        financial_state=account_financial_state,
        collectible_receivable=collectible_receivable,
        available_credit=available_credit,
        prepaid_funding=prepaid_funding,
    )
    position_fact_id = uuid4()
    db.add(
        plane.position_fact(
            **_values(
                scope,
                {
                    "id": position_fact_id,
                    "billing_account_id": account_id,
                    "source_owner": typed_position.source_owner,
                    "source_version": source_version,
                    "posting_group_watermark": group_id,
                    "currency": currency,
                    "minor_units": minor_units,
                    "collectible_receivable": position.collectible_receivable,
                    "available_credit": position.available_credit,
                    "prepaid_funding": position.prepaid_funding,
                    "state_fingerprint": typed_position.state_fingerprint,
                    "rebuild_fingerprint": position.state_fingerprint,
                    "source_authority": "internal",
                    "projection_mode": typed_position.projection_mode,
                    "derived_from": typed_position.derived_from,
                    "completeness": "complete",
                    "completeness_reason_code": None,
                    "financial_state": typed_position.financial_state,
                    "observed_at": typed_position.observed_at,
                },
            )
        )
    )
    affected_documents = {
        *exposure_document_ids,
        *(
            allocation.document_id
            for allocation in allocation_inputs
            if allocation.document_id is not None
        ),
    }
    exposure_facts: list[tuple[UUID, ReceivableExposureV1]] = []
    for document_id in sorted(affected_documents, key=str):
        exposure = _receivable_exposure(
            db,
            scope=scope,
            plane=plane,
            document_id=document_id,
            source_version=source_version,
            posting_group_watermark=group_id,
            observed_at=observed_at,
        )
        exposure_fact_id = uuid4()
        db.add(
            plane.exposure_fact(
                **_values(
                    scope,
                    {
                        "id": exposure_fact_id,
                        "billing_account_id": exposure.billing_account_id,
                        "document_id": document_id,
                        "source_owner": exposure.source_owner,
                        "exposure_ref": exposure.exposure_ref,
                        "subject_ref": exposure.subject_ref,
                        "service_ref": exposure.service_ref,
                        "collection_timing": exposure.collection_timing,
                        "source_version": exposure.source_version,
                        "posting_group_watermark": group_id,
                        "currency": currency,
                        "minor_units": minor_units,
                        "collectible_receivable": (
                            exposure.collectible_receivable.amount
                        ),
                        "state_fingerprint": exposure.state_fingerprint,
                        "source_authority": exposure.source_authority,
                        "projection_mode": exposure.projection_mode,
                        "derived_from": exposure.derived_from,
                        "completeness": exposure.completeness,
                        "completeness_reason_code": (exposure.completeness_reason_code),
                        "financial_state": exposure.financial_state,
                        "observed_at": exposure.observed_at,
                        "service_period": _jsonable(exposure.service_period),
                        "due_at": exposure.due_at,
                        "due_date_basis": _jsonable(exposure.due_date_basis),
                    },
                )
            )
        )
        exposure_facts.append((exposure_fact_id, exposure))
    accounting_fact_id = uuid4()
    reverses_fact_id = None
    if reverses_group_id is not None:
        reversed_fact_statement = select(plane.accounting_fact.id).where(
            plane.accounting_fact.posting_group_id == reverses_group_id,
            plane.accounting_fact.fact_version == 1,
        )
        reverses_fact_id = db.execute(
            _where_scope(reversed_fact_statement, scope, plane.accounting_fact)
        ).scalar_one()
    typed_fact = AccountingFactV1(
        scope=scope,
        source_system="dotmac-billing",
        fact_id=accounting_fact_id,
        fact_version=1,
        billing_account_id=account_id,
        posting_group_id=group_id,
        source_ref=source_ref,
        source_authority="internal",
        effect_kind=kind,
        occurred_at=occurred_at,
        committed_at=observed_at,
        effects=tuple(
            AccountingEffectV1(
                lane=cast(
                    Literal["receivable", "available_credit", "prepaid_funding"],
                    row.lane,
                ),
                amount_delta=_money(row.amount_delta, currency, minor_units),
            )
            for row in effect_rows
        ),
        allocations=tuple(
            AccountingAllocationEffectV1(
                settlement_id=row.settlement_id,
                document_id=row.document_id,
                effect_kind=cast(
                    Literal[
                        "allocation",
                        "deallocation",
                        "reallocation",
                        "refund",
                        "reversal",
                    ],
                    row.effect_kind,
                ),
                amount_delta=_money(row.amount_delta, currency, minor_units),
                offsets_allocation_id=row.offsets_allocation_id,
            )
            for row in allocation_rows
        ),
        reverses_fact_id=reverses_fact_id,
        tax_snapshots=tax_snapshots,
        fx_snapshot=fx_snapshot,
    )
    fact_payload = _jsonable(typed_fact)
    digest = fingerprint_of(fact_payload)
    db.add(
        plane.accounting_fact(
            **_values(
                scope,
                {
                    "id": accounting_fact_id,
                    "posting_group_id": group_id,
                    "fact_version": 1,
                    "source_system": "dotmac-billing",
                    "source_authority": "internal",
                    "effect_kind": kind,
                    "fact_digest": digest,
                    "fact_payload": fact_payload,
                    "reverses_fact_id": reverses_fact_id,
                    "occurred_at": occurred_at,
                },
            )
        )
    )
    db.flush()
    _emit(
        db,
        scope=scope,
        event_type="billing.accounting.fact.v1",
        payload=fact_payload,
    )
    _emit(
        db,
        scope=scope,
        event_type="billing.receivable.position.v1",
        payload={
            "position_fact_id": str(position_fact_id),
            **_jsonable(typed_position),
        },
    )
    for exposure_fact_id, exposure in exposure_facts:
        _emit(
            db,
            scope=scope,
            event_type="billing.receivable.exposure.v1",
            payload={
                "exposure_fact_id": str(exposure_fact_id),
                **_jsonable(exposure),
            },
        )
    return cast(_PostingGroupRow, group)


def _snapshot_mapping(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise BillingConflict(
            "invalid_document_snapshot",
            f"stored {field} is not a string-keyed object",
        )
    return cast(dict[str, object], value)


def _snapshot_optional_text(value: object) -> str | None:
    return None if value is None else str(value)


def _snapshot_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _party_document_snapshot(value: object) -> PartyDocumentSnapshotV1:
    raw = _snapshot_mapping(value, field="party snapshot")
    address_raw = _snapshot_mapping(raw.get("address"), field="party address")
    identifiers_raw = raw.get("registered_identifiers", ())
    if not isinstance(identifiers_raw, list | tuple):
        raise BillingConflict(
            "invalid_document_snapshot",
            "stored registered identifiers are not a list",
        )
    identifiers = tuple(
        RegisteredIdentifierSnapshotV1(
            identifier_type=str(
                _snapshot_mapping(item, field="registered identifier")[
                    "identifier_type"
                ]
            ),
            identifier_value=str(
                _snapshot_mapping(item, field="registered identifier")[
                    "identifier_value"
                ]
            ),
            issuing_country_code=_snapshot_optional_text(
                _snapshot_mapping(item, field="registered identifier").get(
                    "issuing_country_code"
                )
            ),
        )
        for item in identifiers_raw
    )
    return PartyDocumentSnapshotV1(
        legal_name=str(raw["legal_name"]),
        trading_name=_snapshot_optional_text(raw.get("trading_name")),
        address=PostalAddressSnapshotV1(
            line_one=str(address_raw["line_one"]),
            line_two=_snapshot_optional_text(address_raw.get("line_two")),
            city=_snapshot_optional_text(address_raw.get("city")),
            region=_snapshot_optional_text(address_raw.get("region")),
            postal_code=_snapshot_optional_text(address_raw.get("postal_code")),
            country_code=str(address_raw["country_code"]),
        ),
        registered_identifiers=identifiers,
    )


def _payment_instructions(value: object) -> PaymentInstructionsSnapshotV1:
    raw = _snapshot_mapping(value, field="payment instructions")
    return PaymentInstructionsSnapshotV1(
        method_code=str(raw["method_code"]),
        bank_name=_snapshot_optional_text(raw.get("bank_name")),
        account_name=_snapshot_optional_text(raw.get("account_name")),
        account_reference=_snapshot_optional_text(raw.get("account_reference")),
        routing_code=_snapshot_optional_text(raw.get("routing_code")),
        narrative=_snapshot_optional_text(raw.get("narrative")),
    )


def _presentation_asset(value: object) -> PresentationAssetReferenceV1:
    raw = _snapshot_mapping(value, field="presentation asset")
    file_id = raw.get("file_id")
    return PresentationAssetReferenceV1(
        status=cast(Literal["file", "none"], str(raw["status"])),
        file_id=None if file_id is None else UUID(str(file_id)),
    )


def _due_date_basis(value: object) -> DueDateBasisV1:
    raw = _snapshot_mapping(value, field="due-date basis")
    return DueDateBasisV1(
        status=DueDateBasisStatus(str(raw["status"])),
        source_authority=str(raw["source_authority"]),
        evidence_ref=str(raw["evidence_ref"]),
        payment_terms_code=_snapshot_optional_text(raw.get("payment_terms_code")),
        payment_terms_version=_snapshot_optional_text(raw.get("payment_terms_version")),
        issued_at=_snapshot_datetime(raw.get("issued_at")),
        effective_at=_snapshot_datetime(raw.get("effective_at")),
        timezone=_snapshot_optional_text(raw.get("timezone")),
        derivation_policy=_snapshot_optional_text(raw.get("derivation_policy")),
        derivation_version=_snapshot_optional_text(raw.get("derivation_version")),
        override_actor=_snapshot_optional_text(raw.get("override_actor")),
        override_reason=_snapshot_optional_text(raw.get("override_reason")),
        override_evidence_ref=_snapshot_optional_text(raw.get("override_evidence_ref")),
        supersedes_basis_ref=_snapshot_optional_text(raw.get("supersedes_basis_ref")),
    )


def _document_fact(
    db: Session,
    *,
    scope: Scope,
    document: _DocumentRow,
    state: str,
    correlation_id: str,
) -> _DocumentFactRow:
    plane = _models(scope)
    version_statement = select(
        func.coalesce(func.max(plane.document_fact.fact_version), 0)
    ).where(plane.document_fact.document_id == document.id)
    version_statement = _where_scope(version_statement, scope, plane.document_fact)
    fact_version = int(db.execute(version_statement).scalar_one()) + 1
    lines_statement = select(plane.line).where(plane.line.document_id == document.id)
    lines = tuple(
        db.execute(_where_scope(lines_statement, scope, plane.line)).scalars()
    )
    tax_facts, fx_fact = _document_financial_snapshots(
        db,
        scope=scope,
        plane=plane,
        document=document,
    )
    identities_statement = select(plane.party_tax).where(
        plane.party_tax.document_id == document.id
    )
    identities = tuple(
        db.execute(_where_scope(identities_statement, scope, plane.party_tax)).scalars()
    )
    if (
        document.series_code is None
        or document.document_number is None
        or document.issued_at is None
    ):
        raise BillingConflict(
            "invalid_issued_document",
            "an issued document fact requires series, number and issued instant",
        )
    line_facts = tuple(
        InvoiceLineFactV1(
            line_number=line.line_number,
            description=line.description,
            quantity=line.quantity,
            unit_code=line.unit_code,
            unit_amount=_money(line.unit_amount, line.currency, line.minor_units),
            pre_tax_amount=_money(line.pre_tax_amount, line.currency, line.minor_units),
            discount_total=_money(Decimal("0"), line.currency, line.minor_units),
            tax_amount=_money(line.tax_amount, line.currency, line.minor_units),
            total_amount=_money(line.total_amount, line.currency, line.minor_units),
            price_source_version=line.price_source_version,
        )
        for line in lines
    )
    party_tax_facts = tuple(
        PartyTaxIdentitySnapshotV1(
            party_role=cast(Literal["seller", "customer"], row.party_role),
            identity_type=row.identity_type,
            identity_value=row.identity_value,
            country_code=row.country_code,
            source_authority=row.source_authority,
            source_version=row.source_version,
        )
        for row in identities
    )
    provisional = InvoiceDocumentFactV1(
        scope=scope,
        invoice_id=document.id,
        fact_version=fact_version,
        series_code=document.series_code,
        document_number=document.document_number,
        document_kind=cast(
            Literal["invoice", "credit_note", "receipt"], document.document_kind
        ),
        document_state=cast(Literal["issued", "corrected", "cancelled"], state),
        document_profile_code=document.document_profile_code,
        document_profile_version=document.document_profile_version,
        currency=document.currency,
        minor_units=document.minor_units,
        subtotal=_money(document.subtotal, document.currency, document.minor_units),
        tax_total=_money(document.tax_total, document.currency, document.minor_units),
        grand_total=_money(
            document.grand_total, document.currency, document.minor_units
        ),
        due_at=document.due_at,
        due_date_basis=_due_date_basis(document.due_date_basis),
        seller_snapshot=_party_document_snapshot(document.seller_snapshot),
        customer_snapshot=_party_document_snapshot(document.customer_snapshot),
        payment_instructions=_payment_instructions(document.payment_instructions),
        brand_asset=_presentation_asset(document.brand_asset),
        locale=document.locale,
        timezone=document.timezone,
        lines=line_facts,
        tax_snapshots=tax_facts,
        fx_snapshot=fx_fact,
        party_tax_identities=party_tax_facts,
        issued_at=document.issued_at,
        frozen_at=document.issued_at,
        source_authority="internal",
        correlation_id=correlation_id,
        presentation_model_digest="",
    )
    semantic_payload = _jsonable(provisional)
    semantic_payload.pop("presentation_model_digest")
    digest = fingerprint_of(semantic_payload)
    typed_fact = replace(provisional, presentation_model_digest=digest)
    payload = _jsonable(typed_fact)
    fact_id = uuid4()
    fact = plane.document_fact(
        **_values(
            scope,
            {
                "id": fact_id,
                "document_id": document.id,
                "fact_version": fact_version,
                "contract_version": DOCUMENT_FACT_CONTRACT,
                "presentation_model_digest": digest,
                "fact_payload": payload,
            },
        )
    )
    db.add(fact)
    db.flush()
    _emit(
        db,
        scope=scope,
        event_type=DOCUMENT_FACT_CONTRACT,
        payload={"fact_id": str(fact_id), **payload},
        correlation_id=correlation_id,
    )
    return cast(_DocumentFactRow, fact)


def issue_document(
    db: Session,
    *,
    scope: Scope,
    command: IssueDocument,
    numbering: NumberingProvider,
) -> _DocumentRow:
    plane = _models(scope)
    command.due_date_basis.require_collectible(
        native=command.native and command.collectible
    )
    # Serialize before consulting the idempotency ledger.  Otherwise two
    # issuers can both observe a missing key, the loser can wake after the
    # winner committed, and report ``already_issued`` instead of replaying the
    # committed result.
    _one(
        db,
        scope=scope,
        model=plane.document,
        row_id=command.document_id,
        lock=True,
    )
    offered = fingerprint_of(asdict(command))

    def operation(session: Session) -> dict[str, object]:
        document = _one(
            session,
            scope=scope,
            model=plane.document,
            row_id=command.document_id,
            lock=True,
        )
        if document.issued_at is not None:
            raise BillingConflict("already_issued", "document is already issued")
        number = numbering.allocate(
            session,
            scope=scope,
            series_code=command.series_code,
            reference_date=command.reference_date,
            idempotency_key=f"billing:{document.id}",
        )
        document.series_code = command.series_code
        document.document_number = number
        document.due_at = command.due_at
        document.due_date_basis = _jsonable(command.due_date_basis)
        document.lifecycle = "issued"
        document.issued_at = (
            command.due_date_basis.issued_at or command.due_date_basis.effective_at
        )
        if document.issued_at is None:
            raise BillingRuleViolation(
                "issued_at_missing", "issued document requires an evidence instant"
            )
        session.add(
            plane.event(
                **_values(
                    scope,
                    {
                        "id": uuid4(),
                        "document_id": document.id,
                        "event_kind": "issued",
                        "reason": None,
                        "actor_ref": command.actor_ref,
                        "occurred_at": document.issued_at,
                    },
                )
            )
        )
        session.flush()
        sign = (
            Decimal("-1") if document.document_kind == "credit_note" else Decimal("1")
        )
        effect_kind: _AccountingEffectKind = (
            "credit_note_issued"
            if document.document_kind == "credit_note"
            else "invoice_issued"
        )
        accounting_taxes, accounting_fx = _document_financial_snapshots(
            session,
            scope=scope,
            plane=plane,
            document=document,
        )
        group = _post_group(
            session,
            scope=scope,
            account_id=document.billing_account_id,
            kind=effect_kind,
            currency=document.currency,
            minor_units=document.minor_units,
            source_ref=f"document:{document.id}:issue",
            occurred_at=document.issued_at,
            effects=((EffectLane.RECEIVABLE, sign * document.grand_total),),
            exposure_document_ids=(document.id,)
            if document.document_kind == "invoice"
            else (),
            tax_snapshots=accounting_taxes,
            fx_snapshot=accounting_fx,
        )
        fact = _document_fact(
            session,
            scope=scope,
            document=document,
            state="issued",
            correlation_id=command.correlation_id,
        )
        return {
            "document_id": str(document.id),
            "posting_group_id": str(group.id),
            "document_fact_id": str(fact.id),
        }

    outcome = _idempotent(
        db,
        scope=scope,
        operation_scope="billing.document.issue",
        key=str(command.document_id),
        fingerprint=offered,
        operation=operation,
    )
    return cast(
        _DocumentRow,
        _one(
            db,
            scope=scope,
            model=plane.document,
            row_id=UUID(str(outcome.result["document_id"])),
        ),
    )


def void_document(
    db: Session,
    *,
    scope: Scope,
    document_id: UUID,
    actor_ref: str,
    occurred_at: datetime,
    source_ref: str,
) -> _PostingGroupRow:
    plane = _models(scope)
    offered = fingerprint_of(
        {"document_id": document_id, "actor_ref": actor_ref, "occurred_at": occurred_at}
    )

    def operation(session: Session) -> dict[str, object]:
        document = _one(
            session, scope=scope, model=plane.document, row_id=document_id, lock=True
        )
        if document.issued_at is None:
            raise BillingRuleViolation(
                "not_issued", "only an issued document can be voided"
            )
        allocated = _document_allocated(session, scope=scope, document_id=document_id)
        if allocated != 0:
            raise BillingRuleViolation(
                "void_has_allocations", "deallocate the document before voiding it"
            )
        if document.document_kind == "invoice" and _document_credited(
            session, scope=scope, document_id=document_id
        ) != Decimal("0"):
            raise BillingRuleViolation(
                "void_has_credits",
                "void the invoice's credit notes before voiding the invoice",
            )
        session.add(
            plane.event(
                **_values(
                    scope,
                    {
                        "id": uuid4(),
                        "document_id": document_id,
                        "event_kind": "voided",
                        "reason": source_ref,
                        "actor_ref": actor_ref,
                        "occurred_at": occurred_at,
                    },
                )
            )
        )
        group_source_ref = (
            f"credit_note:{document_id}"
            if document.document_kind == "credit_note"
            else f"document:{document_id}:issue"
        )
        group_statement = select(plane.group).where(
            plane.group.source_ref == group_source_ref
        )
        original = session.execute(
            _where_scope(group_statement, scope, plane.group)
        ).scalar_one()
        group = _reverse_group(
            session,
            scope=scope,
            original=original,
            occurred_at=occurred_at,
            source_ref=source_ref,
            exposure_document_ids=(
                (document.credits_document_id,)
                if document.document_kind == "credit_note"
                and document.credits_document_id is not None
                else (document.id,)
                if document.document_kind == "invoice"
                else ()
            ),
        )
        artifacts_statement = select(plane.artifact).where(
            plane.artifact.document_id == document_id,
            plane.artifact.superseded_at.is_(None),
            plane.artifact.withdrawn_at.is_(None),
        )
        for artifact in session.execute(
            _where_scope(artifacts_statement, scope, plane.artifact)
        ).scalars():
            artifact.withdrawn_at = occurred_at
            artifact.withdrawal_reason = source_ref
        session.flush()
        _document_fact(
            session,
            scope=scope,
            document=document,
            state="cancelled",
            correlation_id=source_ref,
        )
        return {"posting_group_id": str(group.id)}

    outcome = _idempotent(
        db,
        scope=scope,
        operation_scope="billing.document.void",
        key=source_ref,
        fingerprint=offered,
        operation=operation,
    )
    return cast(
        _PostingGroupRow,
        _one(
            db,
            scope=scope,
            model=plane.group,
            row_id=UUID(str(outcome.result["posting_group_id"])),
        ),
    )


def issue_credit_note(
    db: Session,
    *,
    scope: Scope,
    command: IssueCreditNote,
    numbering: NumberingProvider,
) -> _DocumentRow:
    plane = _models(scope)
    original = _one(
        db,
        scope=scope,
        model=plane.document,
        row_id=command.original_document_id,
        lock=True,
    )
    if original.issued_at is None or original.document_kind != "invoice":
        raise BillingRuleViolation(
            "credit_target_invalid", "a credit note requires an issued invoice"
        )
    _require_same_money(
        command.pre_tax_amount,
        command.tax_amount,
        command.total_amount,
    )
    if (
        command.pre_tax_amount.amount + command.tax_amount.amount
        != command.total_amount.amount
    ):
        raise BillingRuleViolation(
            "credit_total_mismatch", "credit pre-tax plus tax must equal total"
        )
    for snapshot in command.tax_snapshots:
        _require_same_money(
            command.tax_amount,
            snapshot.taxable_basis,
            snapshot.tax_amount,
        )
    snapshot_tax_total = sum(
        (snapshot.tax_amount.amount for snapshot in command.tax_snapshots),
        start=Decimal("0"),
    )
    if snapshot_tax_total != command.tax_amount.amount:
        raise BillingRuleViolation(
            "credit_tax_snapshot_mismatch",
            "credit tax snapshots must equal the exact credit tax amount",
        )
    _require_account_money(
        _one(db, scope=scope, model=plane.account, row_id=original.billing_account_id),
        command.total_amount,
    )
    outstanding = _document_outstanding(db, scope=scope, document=original)
    if command.total_amount.amount <= 0 or command.total_amount.amount > outstanding:
        raise BillingRuleViolation(
            "credit_exceeds_receivable", "credit note exceeds the open receivable"
        )
    offered = fingerprint_of(asdict(command))

    def operation(session: Session) -> dict[str, object]:
        document_id = uuid4()
        number = numbering.allocate(
            session,
            scope=scope,
            series_code=command.series_code,
            reference_date=command.reference_date,
            idempotency_key=f"billing:credit:{command.correlation_id}",
        )
        document = plane.document(
            **_values(
                scope,
                {
                    "id": document_id,
                    "billing_account_id": original.billing_account_id,
                    "obligation_id": None,
                    "document_kind": "credit_note",
                    "credits_document_id": original.id,
                    "lifecycle": "issued",
                    "series_code": command.series_code,
                    "document_number": number,
                    "currency": command.total_amount.currency.code,
                    "minor_units": command.total_amount.currency.minor_units,
                    "subtotal": command.pre_tax_amount.amount,
                    "tax_total": command.tax_amount.amount,
                    "grand_total": command.total_amount.amount,
                    "due_at": None,
                    "due_date_basis": original.due_date_basis,
                    "document_profile_code": command.document_profile_code,
                    "document_profile_version": command.document_profile_version,
                    "seller_snapshot": original.seller_snapshot,
                    "customer_snapshot": original.customer_snapshot,
                    "payment_instructions": original.payment_instructions,
                    "brand_asset": original.brand_asset,
                    "locale": original.locale,
                    "timezone": original.timezone,
                    "issued_at": command.occurred_at,
                },
            )
        )
        session.add(document)
        session.add(
            plane.line(
                **_values(
                    scope,
                    {
                        "id": uuid4(),
                        "document_id": document_id,
                        "obligation_id": None,
                        "line_number": 1,
                        "description": command.reason,
                        "quantity": Decimal("1"),
                        "unit_code": "credit",
                        "unit_amount": command.pre_tax_amount.amount,
                        "pre_tax_amount": command.pre_tax_amount.amount,
                        "tax_amount": command.tax_amount.amount,
                        "total_amount": command.total_amount.amount,
                        "currency": command.total_amount.currency.code,
                        "minor_units": command.total_amount.currency.minor_units,
                        "price_source_version": f"credit:{original.id}",
                    },
                )
            )
        )
        session.add(
            plane.event(
                **_values(
                    scope,
                    {
                        "id": uuid4(),
                        "document_id": document_id,
                        "event_kind": "issued",
                        "reason": command.reason,
                        "actor_ref": command.actor_ref,
                        "occurred_at": command.occurred_at,
                    },
                )
            )
        )
        for snapshot in command.tax_snapshots:
            session.add(
                plane.tax(
                    **_values(
                        scope,
                        {
                            "id": uuid4(),
                            "obligation_id": None,
                            "document_id": document_id,
                            "treatment_code": snapshot.treatment_code,
                            "jurisdiction_code": snapshot.jurisdiction_code,
                            "policy_id": snapshot.policy_id,
                            "policy_version": snapshot.policy_version,
                            "rate": snapshot.rate,
                            "taxable_basis": snapshot.taxable_basis.amount,
                            "tax_amount": snapshot.tax_amount.amount,
                            "currency": snapshot.tax_amount.currency.code,
                            "minor_units": snapshot.tax_amount.currency.minor_units,
                        },
                    )
                )
            )
        if command.fx_snapshot is not None:
            session.add(
                plane.fx(
                    **_values(
                        scope,
                        {
                            "id": uuid4(),
                            "obligation_id": None,
                            "document_id": document_id,
                            **asdict(command.fx_snapshot),
                        },
                    )
                )
            )
        original_identity_statement = select(plane.party_tax).where(
            plane.party_tax.document_id == original.id
        )
        for identity in session.execute(
            _where_scope(original_identity_statement, scope, plane.party_tax)
        ).scalars():
            session.add(
                plane.party_tax(
                    **_values(
                        scope,
                        {
                            "id": uuid4(),
                            "document_id": document_id,
                            "party_role": identity.party_role,
                            "identity_type": identity.identity_type,
                            "identity_value": identity.identity_value,
                            "country_code": identity.country_code,
                            "source_authority": identity.source_authority,
                            "source_version": identity.source_version,
                        },
                    )
                )
            )
        session.flush()
        group = _post_group(
            session,
            scope=scope,
            account_id=original.billing_account_id,
            kind="credit_note_issued",
            currency=command.total_amount.currency.code,
            minor_units=command.total_amount.currency.minor_units,
            source_ref=f"credit_note:{document_id}",
            occurred_at=command.occurred_at,
            effects=((EffectLane.RECEIVABLE, -command.total_amount.amount),),
            exposure_document_ids=(original.id,),
            tax_snapshots=command.tax_snapshots,
            fx_snapshot=command.fx_snapshot,
        )
        _document_fact(
            session,
            scope=scope,
            document=document,
            state="issued",
            correlation_id=command.correlation_id,
        )
        return {"document_id": str(document_id), "posting_group_id": str(group.id)}

    outcome = _idempotent(
        db,
        scope=scope,
        operation_scope="billing.credit_note.issue",
        key=command.correlation_id,
        fingerprint=offered,
        operation=operation,
    )
    return cast(
        _DocumentRow,
        _one(
            db,
            scope=scope,
            model=plane.document,
            row_id=UUID(str(outcome.result["document_id"])),
        ),
    )


def accept_settlement(
    db: Session,
    *,
    scope: Scope,
    command: AcceptSettlementV1,
    accepted_confirmation_evidence: frozenset[str],
) -> _SettlementRow:
    _require_scope_matches(routed=scope, declared=command.scope)
    if command.confirmation_evidence not in accepted_confirmation_evidence:
        raise BillingRuleViolation(
            "unconfirmed_settlement",
            "only independently confirmed settlement evidence moves money",
            evidence=command.confirmation_evidence,
        )
    plane = _models(scope)
    account = _one(
        db,
        scope=scope,
        model=plane.account,
        row_id=command.billing_account_id,
        lock=True,
    )
    _require_account_money(account, command.amount)
    key = f"{command.source_system}:{command.source_settlement_key}"
    offered = fingerprint_of(
        {
            "amount": command.amount.amount,
            "currency": command.amount.currency.code,
            "minor_units": command.amount.currency.minor_units,
            "occurred_at": command.occurred_at,
            "source_version": command.source_version,
        }
    )

    def operation(session: Session) -> dict[str, object]:
        settlement_id = uuid4()
        session.add(
            plane.settlement(
                **_values(
                    scope,
                    {
                        "id": settlement_id,
                        "billing_account_id": command.billing_account_id,
                        "source_system": command.source_system,
                        "source_settlement_key": command.source_settlement_key,
                        "source_version": command.source_version,
                        "request_fingerprint": offered,
                        "amount": command.amount.amount,
                        "currency": command.amount.currency.code,
                        "minor_units": command.amount.currency.minor_units,
                        "occurred_at": command.occurred_at,
                        "observed_at": command.observed_at,
                        "confirmation_evidence": command.confirmation_evidence,
                        "funding_lane": command.funding_lane.value,
                    },
                )
            )
        )
        session.flush()
        group = _post_group(
            session,
            scope=scope,
            account_id=command.billing_account_id,
            kind="settlement_accepted",
            currency=command.amount.currency.code,
            minor_units=command.amount.currency.minor_units,
            source_ref=f"settlement:{settlement_id}",
            occurred_at=command.occurred_at,
            effects=((EffectLane(command.funding_lane.value), command.amount.amount),),
        )
        _emit(
            session,
            scope=scope,
            event_type="billing.settlement.accepted.v1",
            payload={"settlement_id": str(settlement_id)},
        )
        return {"settlement_id": str(settlement_id), "posting_group_id": str(group.id)}

    outcome = _idempotent(
        db,
        scope=scope,
        operation_scope="billing.settlement",
        key=key,
        fingerprint=offered,
        operation=operation,
    )
    return cast(
        _SettlementRow,
        _one(
            db,
            scope=scope,
            model=plane.settlement,
            row_id=UUID(str(outcome.result["settlement_id"])),
        ),
    )


def _settlement_used(db: Session, *, scope: Scope, settlement_id: UUID) -> Decimal:
    plane = _models(scope)
    statement = select(func.coalesce(func.sum(plane.allocation.amount_delta), 0)).where(
        plane.allocation.settlement_id == settlement_id
    )
    return Decimal(
        db.execute(_where_scope(statement, scope, plane.allocation)).scalar_one()
    )


def _document_allocated(db: Session, *, scope: Scope, document_id: UUID) -> Decimal:
    plane = _models(scope)
    statement = select(func.coalesce(func.sum(plane.allocation.amount_delta), 0)).where(
        plane.allocation.document_id == document_id
    )
    return Decimal(
        db.execute(_where_scope(statement, scope, plane.allocation)).scalar_one()
    )


def _document_credited(db: Session, *, scope: Scope, document_id: UUID) -> Decimal:
    """Return issued, non-void credit notes explicitly tied to one invoice."""

    plane = _models(scope)
    statement = select(plane.document).where(
        plane.document.credits_document_id == document_id,
        plane.document.document_kind == "credit_note",
        plane.document.issued_at.is_not(None),
    )
    credits = db.execute(_where_scope(statement, scope, plane.document)).scalars()
    total = Decimal("0")
    for credit in credits:
        void_statement = select(plane.event.id).where(
            plane.event.document_id == credit.id,
            plane.event.event_kind == "voided",
        )
        if db.execute(_where_scope(void_statement, scope, plane.event)).first() is None:
            total += Decimal(credit.grand_total)
    return total


def _document_outstanding(
    db: Session, *, scope: Scope, document: _DocumentRow
) -> Decimal:
    return max(
        Decimal(document.grand_total)
        - _document_allocated(db, scope=scope, document_id=document.id)
        - _document_credited(db, scope=scope, document_id=document.id),
        Decimal("0"),
    )


def allocate_settlement(
    db: Session, *, scope: Scope, command: AllocationCommand
) -> _PostingGroupRow:
    plane = _models(scope)
    settlement_evidence = _one(
        db,
        scope=scope,
        model=plane.settlement,
        row_id=command.settlement_id,
    )
    _one(
        db,
        scope=scope,
        model=plane.account,
        row_id=settlement_evidence.billing_account_id,
        lock=True,
    )
    offered = fingerprint_of(asdict(command))

    def operation(session: Session) -> dict[str, object]:
        settlement = _one(
            session,
            scope=scope,
            model=plane.settlement,
            row_id=command.settlement_id,
        )
        document = _one(
            session,
            scope=scope,
            model=plane.document,
            row_id=command.document_id,
            lock=True,
        )
        if document.issued_at is None or document.document_kind != "invoice":
            raise BillingRuleViolation(
                "allocation_target_invalid", "allocation requires an issued invoice"
            )
        _require_same_money(
            command.amount,
            _money(settlement.amount, settlement.currency, settlement.minor_units),
            _money(document.grand_total, document.currency, document.minor_units),
        )
        available = settlement.amount - _settlement_used(
            session, scope=scope, settlement_id=settlement.id
        )
        outstanding = _document_outstanding(session, scope=scope, document=document)
        if command.amount.amount <= 0 or command.amount.amount > available:
            raise BillingRuleViolation(
                "allocation_exceeds_settlement",
                "allocation exceeds confirmed settlement",
            )
        if command.amount.amount > outstanding:
            raise BillingRuleViolation(
                "allocation_exceeds_receivable",
                "allocation exceeds document receivable",
            )
        group = _post_group(
            session,
            scope=scope,
            account_id=settlement.billing_account_id,
            kind="allocation",
            currency=settlement.currency,
            minor_units=settlement.minor_units,
            source_ref=command.source_ref,
            occurred_at=command.occurred_at,
            effects=(
                (EffectLane.RECEIVABLE, -command.amount.amount),
                (EffectLane(settlement.funding_lane), -command.amount.amount),
            ),
            allocations=(
                _AllocationInput(
                    settlement_id=settlement.id,
                    document_id=document.id,
                    effect_kind="allocation",
                    amount_delta=command.amount.amount,
                ),
            ),
        )
        return {"posting_group_id": str(group.id)}

    outcome = _idempotent(
        db,
        scope=scope,
        operation_scope="billing.allocation.apply",
        key=command.source_ref,
        fingerprint=offered,
        operation=operation,
    )
    return cast(
        _PostingGroupRow,
        _one(
            db,
            scope=scope,
            model=plane.group,
            row_id=UUID(str(outcome.result["posting_group_id"])),
        ),
    )


def deallocate_settlement(
    db: Session, *, scope: Scope, command: DeallocationCommand
) -> _PostingGroupRow:
    plane = _models(scope)
    original = _one(
        db, scope=scope, model=plane.allocation, row_id=command.allocation_id
    )
    if original.document_id is None or original.amount_delta <= 0:
        raise BillingRuleViolation(
            "deallocation_target_invalid", "target is not an allocation"
        )
    settlement = _one(
        db,
        scope=scope,
        model=plane.settlement,
        row_id=original.settlement_id,
    )
    _one(
        db,
        scope=scope,
        model=plane.account,
        row_id=settlement.billing_account_id,
        lock=True,
    )
    _require_same_money(
        command.amount,
        _money(original.amount_delta, original.currency, original.minor_units),
    )
    offsets_statement = select(
        func.coalesce(func.sum(plane.allocation.amount_delta), 0)
    ).where(plane.allocation.offsets_allocation_id == original.id)
    offset = Decimal(
        db.execute(
            _where_scope(offsets_statement, scope, plane.allocation)
        ).scalar_one()
    )
    remaining = original.amount_delta + offset
    if command.amount.amount <= 0 or command.amount.amount > remaining:
        raise BillingRuleViolation(
            "deallocation_exceeds_allocation",
            "deallocation exceeds the original allocation",
        )
    return _posting_command(
        db,
        scope=scope,
        operation_scope="billing.allocation.deallocate",
        key=command.source_ref,
        fingerprint=fingerprint_of(asdict(command)),
        account_id=settlement.billing_account_id,
        kind="deallocation",
        money=command.amount,
        occurred_at=command.occurred_at,
        effects=(
            (EffectLane.RECEIVABLE, command.amount.amount),
            (EffectLane(settlement.funding_lane), command.amount.amount),
        ),
        allocations=(
            _AllocationInput(
                settlement_id=settlement.id,
                document_id=original.document_id,
                effect_kind="deallocation",
                amount_delta=-command.amount.amount,
                offsets_allocation_id=original.id,
            ),
        ),
    )


def reallocate_settlement(
    db: Session, *, scope: Scope, command: ReallocationCommand
) -> _PostingGroupRow:
    plane = _models(scope)
    settlement = _one(
        db, scope=scope, model=plane.settlement, row_id=command.settlement_id
    )
    _one(
        db,
        scope=scope,
        model=plane.account,
        row_id=settlement.billing_account_id,
        lock=True,
    )
    source = _one(
        db,
        scope=scope,
        model=plane.document,
        row_id=command.from_document_id,
        lock=True,
    )
    target = _one(
        db, scope=scope, model=plane.document, row_id=command.to_document_id, lock=True
    )
    _require_same_money(
        command.amount,
        _money(settlement.amount, settlement.currency, settlement.minor_units),
        _money(source.grand_total, source.currency, source.minor_units),
        _money(target.grand_total, target.currency, target.minor_units),
    )
    if command.amount.amount > _document_allocated(
        db, scope=scope, document_id=source.id
    ):
        raise BillingRuleViolation(
            "reallocation_exceeds_source", "reallocation exceeds source coverage"
        )
    target_open = _document_outstanding(db, scope=scope, document=target)
    if command.amount.amount <= 0 or command.amount.amount > target_open:
        raise BillingRuleViolation(
            "reallocation_exceeds_target", "reallocation exceeds target receivable"
        )
    return _posting_command(
        db,
        scope=scope,
        operation_scope="billing.allocation.reallocate",
        key=command.source_ref,
        fingerprint=fingerprint_of(asdict(command)),
        account_id=settlement.billing_account_id,
        kind="reallocation",
        money=command.amount,
        occurred_at=command.occurred_at,
        effects=(),
        allocations=(
            _AllocationInput(
                settlement_id=settlement.id,
                document_id=source.id,
                effect_kind="reallocation",
                amount_delta=-command.amount.amount,
            ),
            _AllocationInput(
                settlement_id=settlement.id,
                document_id=target.id,
                effect_kind="reallocation",
                amount_delta=command.amount.amount,
            ),
        ),
    )


def refund_settlement(
    db: Session, *, scope: Scope, command: RefundCommand
) -> _PostingGroupRow:
    plane = _models(scope)
    settlement = _one(
        db, scope=scope, model=plane.settlement, row_id=command.settlement_id
    )
    _one(
        db,
        scope=scope,
        model=plane.account,
        row_id=settlement.billing_account_id,
        lock=True,
    )
    _require_same_money(
        command.amount,
        _money(settlement.amount, settlement.currency, settlement.minor_units),
    )
    available = settlement.amount - _settlement_used(
        db, scope=scope, settlement_id=settlement.id
    )
    if command.amount.amount <= 0 or command.amount.amount > available:
        raise BillingRuleViolation(
            "refund_exceeds_available", "refund exceeds unallocated confirmed funding"
        )
    return _posting_command(
        db,
        scope=scope,
        operation_scope="billing.settlement.refund",
        key=command.source_ref,
        fingerprint=fingerprint_of(asdict(command)),
        account_id=settlement.billing_account_id,
        kind="refund",
        money=command.amount,
        occurred_at=command.occurred_at,
        effects=((EffectLane(settlement.funding_lane), -command.amount.amount),),
        allocations=(
            _AllocationInput(
                settlement_id=settlement.id,
                document_id=None,
                effect_kind="refund",
                amount_delta=command.amount.amount,
            ),
        ),
    )


def _posting_command(
    db: Session,
    *,
    scope: Scope,
    operation_scope: str,
    key: str,
    fingerprint: str,
    account_id: UUID,
    kind: _AccountingEffectKind,
    money: Money,
    occurred_at: datetime,
    effects: Iterable[tuple[EffectLane, Decimal]],
    allocations: Iterable[_AllocationInput],
) -> _PostingGroupRow:
    plane = _models(scope)

    def operation(session: Session) -> dict[str, object]:
        group = _post_group(
            session,
            scope=scope,
            account_id=account_id,
            kind=kind,
            currency=money.currency.code,
            minor_units=money.currency.minor_units,
            source_ref=key,
            occurred_at=occurred_at,
            effects=effects,
            allocations=allocations,
        )
        return {"posting_group_id": str(group.id)}

    outcome = _idempotent(
        db,
        scope=scope,
        operation_scope=operation_scope,
        key=key,
        fingerprint=fingerprint,
        operation=operation,
    )
    return cast(
        _PostingGroupRow,
        _one(
            db,
            scope=scope,
            model=plane.group,
            row_id=UUID(str(outcome.result["posting_group_id"])),
        ),
    )


def _reverse_group(
    db: Session,
    *,
    scope: Scope,
    original: _PostingGroupRow,
    occurred_at: datetime,
    source_ref: str,
    exposure_document_ids: Iterable[UUID] = (),
) -> _PostingGroupRow:
    plane = _models(scope)
    already_statement = select(plane.group.id).where(
        plane.group.reverses_group_id == original.id
    )
    if (
        db.execute(_where_scope(already_statement, scope, plane.group)).first()
        is not None
    ):
        raise BillingConflict(
            "already_reversed", "posting group already has a reversal"
        )
    effects_statement = select(plane.effect).where(
        plane.effect.posting_group_id == original.id
    )
    effects = tuple(
        db.execute(_where_scope(effects_statement, scope, plane.effect)).scalars()
    )
    allocations_statement = select(plane.allocation).where(
        plane.allocation.posting_group_id == original.id
    )
    allocations = tuple(
        db.execute(
            _where_scope(allocations_statement, scope, plane.allocation)
        ).scalars()
    )
    return _post_group(
        db,
        scope=scope,
        account_id=original.billing_account_id,
        kind="reversal",
        currency=original.currency,
        minor_units=original.minor_units,
        source_ref=source_ref,
        occurred_at=occurred_at,
        effects=tuple((EffectLane(row.lane), -row.amount_delta) for row in effects),
        allocations=tuple(
            _AllocationInput(
                settlement_id=row.settlement_id,
                document_id=row.document_id,
                effect_kind="reversal",
                amount_delta=-row.amount_delta,
                offsets_allocation_id=row.id,
            )
            for row in allocations
        ),
        exposure_document_ids=exposure_document_ids,
        reverses_group_id=original.id,
    )


def reverse_posting_group(
    db: Session, *, scope: Scope, command: ReversePostingGroupCommand
) -> _PostingGroupRow:
    plane = _models(scope)
    original_evidence = _one(
        db,
        scope=scope,
        model=plane.group,
        row_id=command.posting_group_id,
    )
    _one(
        db,
        scope=scope,
        model=plane.account,
        row_id=original_evidence.billing_account_id,
        lock=True,
    )
    offered = fingerprint_of(asdict(command))

    def operation(session: Session) -> dict[str, object]:
        original = _one(
            session,
            scope=scope,
            model=plane.group,
            row_id=command.posting_group_id,
        )
        group = _reverse_group(
            session,
            scope=scope,
            original=original,
            occurred_at=command.occurred_at,
            source_ref=command.source_ref,
        )
        return {"posting_group_id": str(group.id)}

    outcome = _idempotent(
        db,
        scope=scope,
        operation_scope="billing.posting.reverse",
        key=command.source_ref,
        fingerprint=offered,
        operation=operation,
    )
    return cast(
        _PostingGroupRow,
        _one(
            db,
            scope=scope,
            model=plane.group,
            row_id=UUID(str(outcome.result["posting_group_id"])),
        ),
    )


def rebuild_receivable_position(
    db: Session,
    *,
    scope: Scope,
    billing_account_id: UUID,
    currency: str,
    minor_units: int,
    verify_latest: bool = True,
) -> PositionState:
    plane = _models(scope)
    statement = select(plane.effect).where(
        plane.effect.billing_account_id == billing_account_id,
        plane.effect.currency == currency,
    )
    rows = tuple(db.execute(_where_scope(statement, scope, plane.effect)).scalars())
    rebuilt = rebuild_position(
        (
            Effect(
                lane=EffectLane(row.lane),
                amount=row.amount_delta,
                currency=row.currency,
                minor_units=row.minor_units,
            )
            for row in rows
        ),
        currency=currency,
        minor_units=minor_units,
    )
    if verify_latest:
        latest_statement = (
            select(plane.position_fact)
            .where(
                plane.position_fact.billing_account_id == billing_account_id,
                plane.position_fact.currency == currency,
            )
            .order_by(plane.position_fact.source_version.desc())
            .limit(1)
        )
        latest = db.execute(
            _where_scope(latest_statement, scope, plane.position_fact)
        ).scalar_one_or_none()
        if (
            latest is not None
            and latest.rebuild_fingerprint != rebuilt.state_fingerprint
        ):
            raise BillingConflict(
                "position_hash_mismatch",
                "rebuild hash differs from the latest immutable position fact",
                stored=latest.rebuild_fingerprint,
                rebuilt=rebuilt.state_fingerprint,
            )
    return rebuilt


def record_document_artifact(
    db: Session,
    *,
    scope: Scope,
    command: RecordDocumentArtifactV1,
) -> _ArtifactRow:
    _require_scope_matches(routed=scope, declared=command.scope)
    plane = _models(scope)
    fact_statement = select(plane.document_fact).where(
        plane.document_fact.id == command.fact_id,
        plane.document_fact.fact_version == command.fact_version,
    )
    fact = db.execute(
        _where_scope(fact_statement, scope, plane.document_fact)
    ).scalar_one_or_none()
    if fact is None:
        raise BillingRuleViolation(
            "document_fact_not_found", "document fact does not exist"
        )
    if fact.document_id != command.invoice_id:
        raise BillingConflict(
            "document_fact_identity_mismatch",
            "artifact invoice identity differs from the immutable document fact",
        )
    if fact.presentation_model_digest != command.presentation_model_digest:
        raise BillingConflict(
            "artifact_content_mismatch",
            "artifact semantic content differs from the immutable document fact",
        )
    key = f"{fact.id}:{command.fact_version}:{command.media_type}"
    offered = fingerprint_of(asdict(command))

    def operation(session: Session) -> dict[str, object]:
        document = _one(
            session,
            scope=scope,
            model=plane.document,
            row_id=command.invoice_id,
            lock=True,
        )
        if document.issued_at is None or document.document_number is None:
            raise BillingRuleViolation(
                "document_fact_not_issued",
                "a draft document has no official artifact",
            )
        void_statement = select(plane.event.id).where(
            plane.event.document_id == document.id,
            plane.event.event_kind == "voided",
        )
        if (
            session.execute(
                _where_scope(void_statement, scope, plane.event)
            ).scalar_one_or_none()
            is not None
        ):
            raise BillingRuleViolation(
                "document_cancelled",
                "a cancelled document cannot acquire a new official artifact",
            )
        current_statement = (
            select(plane.artifact)
            .where(
                plane.artifact.document_fact_id == fact.id,
                plane.artifact.media_type == command.media_type,
                plane.artifact.superseded_at.is_(None),
            )
            .with_for_update()
        )
        current = session.execute(
            _where_scope(current_statement, scope, plane.artifact)
        ).scalar_one_or_none()
        if current is not None:
            raise BillingConflict(
                "artifact_already_recorded",
                "an official artifact already exists for the fact version and medium",
            )
        artifact_id = uuid4()
        session.add(
            plane.artifact(
                **_values(
                    scope,
                    {
                        "id": artifact_id,
                        "document_fact_id": fact.id,
                        "document_id": command.invoice_id,
                        "document_number": document.document_number,
                        "fact_version": command.fact_version,
                        "media_type": command.media_type,
                        "file_id": command.file_id,
                        "checksum_sha256": command.checksum_sha256,
                        "byte_length": command.byte_length,
                        "renderer_code": command.renderer_code,
                        "renderer_version": command.renderer_version,
                        "template_version": command.template_version,
                        "presentation_model_digest": command.presentation_model_digest,
                        "rendered_at": command.rendered_at,
                        "correlation_id": command.correlation_id,
                        "issued_by": command.issued_by,
                        "idempotency_key": key,
                        "request_fingerprint": offered,
                        "superseded_at": None,
                        "superseded_by_artifact_id": None,
                        "supersession_reason": None,
                        "withdrawn_at": None,
                        "withdrawal_reason": None,
                    },
                )
            )
        )
        session.flush()
        _emit(
            session,
            scope=scope,
            event_type="billing.document.artifact.recorded.v1",
            payload={"artifact_id": str(artifact_id), **_jsonable(command)},
        )
        return {"artifact_id": str(artifact_id)}

    outcome = _idempotent(
        db,
        scope=scope,
        operation_scope="billing.document.artifact.record",
        key=key,
        fingerprint=offered,
        operation=operation,
    )
    return cast(
        _ArtifactRow,
        _one(
            db,
            scope=scope,
            model=plane.artifact,
            row_id=UUID(str(outcome.result["artifact_id"])),
        ),
    )


def repair_document_artifact(
    db: Session,
    *,
    scope: Scope,
    command: RepairDocumentArtifactV1,
    declared_supersession_reasons: frozenset[str],
) -> _ArtifactRow:
    """Append one physical repair without accepting new document semantics."""

    _require_scope_matches(routed=scope, declared=command.scope)
    if command.supersession_reason not in declared_supersession_reasons:
        raise BillingRuleViolation(
            "unknown_artifact_supersession_reason",
            "artifact supersession reason is not declared by the assembly",
        )
    plane = _models(scope)
    key = (
        f"{command.current_artifact_id}:{command.replacement_file_id}:"
        f"{command.checksum_sha256}"
    )
    offered = fingerprint_of(asdict(command))

    def operation(session: Session) -> dict[str, object]:
        current = _one(
            session,
            scope=scope,
            model=plane.artifact,
            row_id=command.current_artifact_id,
            lock=True,
        )
        if current.superseded_at is not None:
            raise BillingConflict(
                "artifact_repair_stale",
                "artifact repair must name the exact current relation",
            )
        if current.withdrawn_at is not None:
            raise BillingConflict(
                "artifact_withdrawn",
                "a withdrawn official artifact cannot be repaired",
            )
        if current.presentation_model_digest != command.presentation_model_digest:
            raise BillingConflict(
                "artifact_content_mismatch",
                "artifact repair semantic content differs from the official fact",
            )
        replacement_id = uuid4()
        current.superseded_at = datetime.now(UTC)
        current.superseded_by_artifact_id = replacement_id
        current.supersession_reason = command.supersession_reason
        session.flush()
        session.add(
            plane.artifact(
                **_values(
                    scope,
                    {
                        "id": replacement_id,
                        "document_fact_id": current.document_fact_id,
                        "document_id": current.document_id,
                        "document_number": current.document_number,
                        "fact_version": current.fact_version,
                        "media_type": current.media_type,
                        "file_id": command.replacement_file_id,
                        "checksum_sha256": command.checksum_sha256,
                        "byte_length": command.byte_length,
                        "renderer_code": current.renderer_code,
                        "renderer_version": current.renderer_version,
                        "template_version": current.template_version,
                        "presentation_model_digest": (
                            current.presentation_model_digest
                        ),
                        "rendered_at": command.rendered_at,
                        "correlation_id": command.correlation_id,
                        "issued_by": command.issued_by,
                        "idempotency_key": key,
                        "request_fingerprint": offered,
                        "superseded_at": None,
                        "superseded_by_artifact_id": None,
                        "supersession_reason": None,
                        "withdrawn_at": None,
                        "withdrawal_reason": None,
                    },
                )
            )
        )
        session.flush()
        _emit(
            session,
            scope=scope,
            event_type="billing.document.artifact.repaired.v1",
            payload={"artifact_id": str(replacement_id), **_jsonable(command)},
        )
        return {"artifact_id": str(replacement_id)}

    outcome = _idempotent(
        db,
        scope=scope,
        operation_scope="billing.document.artifact.repair",
        key=key,
        fingerprint=offered,
        operation=operation,
    )
    return cast(
        _ArtifactRow,
        _one(
            db,
            scope=scope,
            model=plane.artifact,
            row_id=UUID(str(outcome.result["artifact_id"])),
        ),
    )


__all__ = [
    "accept_rated_obligation",
    "accept_settlement",
    "allocate_settlement",
    "create_billing_account",
    "create_draft_document",
    "deallocate_settlement",
    "issue_credit_note",
    "issue_document",
    "reallocate_settlement",
    "rebuild_receivable_position",
    "record_document_artifact",
    "repair_document_artifact",
    "refund_settlement",
    "reverse_posting_group",
    "void_document",
]
