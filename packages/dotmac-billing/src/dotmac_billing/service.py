"""Flush-only Billing persistence service over one shared rule engine."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, fields, is_dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from enum import Enum
from functools import wraps
from typing import Any, Literal, ParamSpec, TypeVar, cast
from uuid import UUID, uuid4

from dotmac_kernel.cache import PlatformScope, Scope, TenantScope
from dotmac_kernel.idempotency import (
    IdempotencyConflict,
    IdempotentOutcome,
    Operation,
    execute_once,
    execute_once_platform,
    fingerprint_of,
)
from dotmac_kernel.messaging import enqueue_event, enqueue_platform_event
from dotmac_kernel.money import Currency, Money
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_billing import models
from dotmac_billing._opening_values import (
    cash_decimal_context,
    minor_units_of,
    money_from_minor,
)
from dotmac_billing._receivable_backing import resolve_receivable_backing
from dotmac_billing.commands import (
    AcceptWithholdingDischarge,
    AllocationCommand,
    ApplyAvailableCreditCommand,
    ApplyNonCashCredit,
    CreateDraftDocument,
    CreateMultiLineDraftDocument,
    DeallocationCommand,
    DraftDocumentLine,
    GrantNonCashCredit,
    IssueCreditNote,
    IssueDocument,
    NumberingProvider,
    ReallocationCommand,
    ReduceNonCashCreditGrant,
    RefundCommand,
    ReverseNonCashCreditApplication,
    ReversePostingGroupCommand,
    ReverseWithholdingDischarge,
)
from dotmac_billing.contracts import (
    DOCUMENT_FACT_CONTRACT,
    AcceptRatedObligationV1,
    AcceptSettlementV1,
    AccountingAllocationEffectV1,
    AccountingEffectV1,
    AccountingFactV1,
    AccountingFactV2,
    AccountingFactV3,
    AppliedFxSnapshotV1,
    AppliedTaxSnapshotV1,
    CollectionTiming,
    DueDateBasisStatus,
    DueDateBasisV1,
    FinancialState,
    InvoiceDocumentFactV1,
    InvoiceLineFactV1,
    NonCashCreditEffectV1,
    NonCashCreditGrantV1,
    PartyDocumentSnapshotV1,
    PartyTaxIdentitySnapshotV1,
    PaymentInstructionsSnapshotV1,
    PostalAddressSnapshotV1,
    PresentationAssetReferenceV1,
    ReceivableExposureV1,
    ReceivablePositionV1,
    ReceivablePositionV2,
    RecordDocumentArtifactV1,
    RegisteredIdentifierSnapshotV1,
    RepairDocumentArtifactV1,
    ServicePeriodEvidenceV1,
    ServicePeriodStatus,
    WithholdingDischargeEffectV1,
    _non_cash_identity,
    _withholding_money,
)
from dotmac_billing.engine import Effect, EffectLane, PositionState, rebuild_position
from dotmac_billing.errors import BillingConflict, BillingRuleViolation
from dotmac_billing.opening_authority import (
    OpeningCashRefundCorrectionVerificationRequestV1,
    OpeningCashRefundCorrectionVerificationV1,
    OpeningCashRefundSubjectV1,
    OpeningCashRefundVerificationRequestV1,
    OpeningCashRefundVerificationV1,
    OpeningCashRefundVerifier,
    OpeningImportReversalVerificationRequestV1,
    OpeningImportReversalVerificationV1,
    OpeningImportVerificationRequestV1,
    OpeningImportVerificationV1,
    OpeningImportVerifier,
    opening_cash_command_digest,
)
from dotmac_billing.opening_cash_contracts import (
    AccountingFactV4,
    AccountingSettlementAllocationV4,
    CashCreditSourceOrderV1,
    OpeningCashEffectV1,
    OpeningCashLotFactV1,
    OpeningCashPostingKind,
    OpeningImportReceiptV1,
)
from dotmac_billing.opening_commands import (
    CorrectOpeningCashRefundV1,
    ImportOpeningAccountV1,
    OpeningCashMovementEvidenceV1,
    RecordOpeningCashRefundV1,
    ReverseCashCreditDrawV1,
    ReverseOpeningAccountImportV1,
)
from dotmac_billing.opening_contracts import (
    OpeningPreviewRequestV1,
    OpeningVerificationUnavailable,
)
from dotmac_billing.opening_queries import (
    get_accounting_fact_v4,
    get_opening_cash_effect,
    get_opening_cash_lot,
    get_opening_cash_lot_position,
    get_opening_cash_target,
    get_opening_import_receipt,
)
from dotmac_billing.opening_receivable_contracts import (
    AccountingFactV5,
    ImportOpeningAccountV2,
    OpeningAccountImportVerifierV2,
    OpeningImportReceiptV2,
    OpeningImportReversalVerificationRequestV2,
    OpeningImportReversalVerificationV2,
    OpeningReceivableBasisV1,
    ReceivableExposureV2,
)
from dotmac_billing.opening_receivable_queries import (
    get_opening_import_receipt_v2,
    get_opening_receivable_basis,
    get_opening_receivable_position,
)
from dotmac_billing.opening_receivable_validation import (
    _opening_replay_identity,
    opening_account_command_digest_v2,
    opening_account_import_blockers_v2,
    opening_withholding_component_identity,
    verify_opening_account_import_v2,
)
from dotmac_billing.opening_validation import (
    opening_manifest_fingerprint,
    preview_opening_account_import,
)
from dotmac_billing.queries import (
    get_non_cash_credit_effect,
    get_non_cash_credit_grant,
    get_withholding_discharge,
)

_AccountingEffectKind = Literal[
    "opening_account_imported",
    "opening_account_import_reversed",
    "opening_cash_imported",
    "opening_cash_applied",
    "cash_credit_draw_reversed",
    "opening_cash_refunded",
    "opening_cash_refund_corrected",
    "opening_cash_import_reversed",
    "non_cash_credit_granted",
    "non_cash_credit_applied",
    "non_cash_credit_application_reversed",
    "non_cash_credit_reduced",
    "withholding_accepted",
    "withholding_reversed",
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


_P = ParamSpec("_P")
_R = TypeVar("_R")


def _exact_cash_operation(function: Callable[_P, _R]) -> Callable[_P, _R]:
    @wraps(function)
    def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        with localcontext(cash_decimal_context()):
            return function(*args, **kwargs)

    return wrapped


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
    cash_source_claim: Any
    opening_receipt: Any
    opening_lot: Any
    opening_effect: Any
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
    withholding: Any
    non_cash_grant: Any
    non_cash_effect: Any
    position_fact_v2: Any


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
    allocation_id: UUID | None = None


_TENANT = _PlaneModels(
    models.BillingAccount,
    models.RatedObligation,
    models.BillingDocument,
    models.DocumentLine,
    models.DocumentEvent,
    models.ConfirmedSettlement,
    models.CashSourceClaim,
    models.OpeningImportReceipt,
    models.OpeningCashLot,
    models.OpeningCashEffect,
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
    models.WithholdingDischarge,
    models.NonCashCreditGrant,
    models.NonCashCreditEffect,
    models.ReceivablePositionFactV2,
)
_PLATFORM = _PlaneModels(
    models.PlatformBillingAccount,
    models.PlatformRatedObligation,
    models.PlatformBillingDocument,
    models.PlatformDocumentLine,
    models.PlatformDocumentEvent,
    models.PlatformConfirmedSettlement,
    models.PlatformCashSourceClaim,
    models.PlatformOpeningImportReceipt,
    models.PlatformOpeningCashLot,
    models.PlatformOpeningCashEffect,
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
    models.PlatformWithholdingDischarge,
    models.PlatformNonCashCreditGrant,
    models.PlatformNonCashCreditEffect,
    models.PlatformReceivablePositionFactV2,
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
                    "billing_account_id": obligation.billing_account_id,
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
        session.add(document)
        session.flush()
        session.add(line)
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


def _require_line_input(line: DraftDocumentLine, obligation: _ObligationRow) -> None:
    quantity = line.quantity
    if (
        not quantity.is_finite()
        or quantity <= 0
        or quantity >= Decimal("1e14")
        or quantity != quantity.quantize(Decimal("0.000001"))
    ):
        raise BillingRuleViolation(
            "invalid_line_quantity", "quantity must be positive and exact at six places"
        )
    if (
        not line.description.strip()
        or not line.unit_code.strip()
        or len(line.unit_code) > 40
    ):
        raise BillingRuleViolation(
            "invalid_line_description", "description and unit code are required"
        )
    amount = line.unit_amount
    if (
        amount.currency.code != obligation.currency
        or amount.currency.minor_units != obligation.minor_units
    ):
        raise BillingRuleViolation(
            "line_currency_mismatch", "line money must match its obligation"
        )
    with localcontext() as context:
        context.prec = 60  # Numeric(20,6) operands multiply without context rounding.
        product = amount.amount * quantity
    if (
        not amount.amount.is_finite()
        or amount.amount < 0
        or product != obligation.pre_tax_amount
    ):
        raise BillingRuleViolation(
            "line_amount_mismatch",
            "explicit unit price times quantity must exactly equal rated pre-tax",
        )


def _require_homogeneous(obligations: tuple[_ObligationRow, ...]) -> None:
    attributes = (
        "billing_account_id",
        "currency",
        "minor_units",
        "subject_ref",
        "service_ref",
        "collection_timing",
        "service_period_status",
        "period_start",
        "period_end",
    )
    anchor = obligations[0]
    if any(
        any(getattr(row, attr) != getattr(anchor, attr) for attr in attributes)
        for row in obligations
    ):
        raise BillingRuleViolation(
            "heterogeneous_document",
            "invoice obligations must share account, currency and exposure evidence",
        )


def _require_undocumented(
    db: Session, *, scope: Scope, plane: _PlaneModels, obligation_id: UUID
) -> None:
    for model, predicate, code in (
        (
            plane.line,
            plane.line.obligation_id == obligation_id,
            "obligation_already_documented",
        ),
        (
            plane.obligation,
            plane.obligation.supersedes_obligation_id == obligation_id,
            "obligation_superseded",
        ),
    ):
        if (
            db.execute(
                _where_scope(select(model.id).where(predicate), scope, model)
            ).first()
            is not None
        ):
            raise BillingConflict(
                code, "obligation is no longer eligible for draft authoring"
            )


def create_multi_line_draft_document(
    db: Session, *, scope: Scope, command: CreateMultiLineDraftDocument
) -> _DocumentRow:
    plane = _models(scope)
    account = _one(
        db,
        scope=scope,
        model=plane.account,
        row_id=command.billing_account_id,
        lock=True,
    )
    offered = fingerprint_of(asdict(command))

    def operation(session: Session) -> dict[str, object]:
        if not command.idempotency_key.strip() or len(command.lines) < 2:
            raise BillingRuleViolation(
                "empty_document",
                "multiline draft requires an idempotency key and at least two lines",
            )
        ids = tuple(line.obligation_id for line in command.lines)
        if len(set(ids)) != len(ids):
            raise BillingRuleViolation(
                "duplicate_obligation", "an obligation may appear only once"
            )
        obligations = tuple(
            _one(session, scope=scope, model=plane.obligation, row_id=key)
            for key in ids
        )
        _require_homogeneous(obligations)
        for line, row in zip(command.lines, obligations, strict=True):
            if row.billing_account_id != account.id:
                raise BillingRuleViolation(
                    "line_account_mismatch",
                    "obligation must belong to the selected account",
                )
            _require_account_money(account, line.unit_amount)
            _require_line_input(line, row)
            _require_undocumented(
                session, scope=scope, plane=plane, obligation_id=row.id
            )
        _require_obligation_taxes(
            session, scope=scope, plane=plane, obligations=obligations
        )
        _obligation_fx(session, scope=scope, plane=plane, obligation_ids=ids)
        anchor = obligations[0]
        document_id = uuid4()
        header_fields = (
            "due_date_basis",
            "document_profile_code",
            "document_profile_version",
            "seller_snapshot",
            "customer_snapshot",
            "payment_instructions",
            "brand_asset",
            "locale",
            "timezone",
        )
        document = plane.document(
            **_values(
                scope,
                {
                    "id": document_id,
                    "billing_account_id": account.id,
                    "obligation_id": anchor.id,
                    "authoring_mode": "explicit_multiline",
                    "document_kind": "invoice",
                    "credits_document_id": None,
                    "lifecycle": "draft",
                    "series_code": None,
                    "document_number": None,
                    "currency": anchor.currency,
                    "minor_units": anchor.minor_units,
                    "subtotal": sum(
                        (row.pre_tax_amount for row in obligations), Decimal("0")
                    ),
                    "tax_total": sum(
                        (row.tax_amount for row in obligations), Decimal("0")
                    ),
                    "grand_total": sum(
                        (row.total_amount for row in obligations), Decimal("0")
                    ),
                    "due_at": None,
                    "issued_at": None,
                    **{
                        name: _wire_value(getattr(command, name))
                        for name in header_fields
                    },
                },
            )
        )
        session.add(document)
        session.flush()
        for index, (line, row) in enumerate(
            zip(command.lines, obligations, strict=True), 1
        ):
            session.add(
                plane.line(
                    **_values(
                        scope,
                        {
                            "id": uuid4(),
                            "billing_account_id": account.id,
                            "document_id": document_id,
                            "obligation_id": row.id,
                            "line_number": index,
                            "description": line.description,
                            "quantity": line.quantity,
                            "unit_code": line.unit_code,
                            "unit_amount": line.unit_amount.amount,
                            "pre_tax_amount": row.pre_tax_amount,
                            "tax_amount": row.tax_amount,
                            "total_amount": row.total_amount,
                            "currency": row.currency,
                            "minor_units": row.minor_units,
                            "price_source_version": row.price_version_id,
                        },
                    )
                )
            )
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
        operation_scope="billing.document.multiline_draft",
        key=command.idempotency_key,
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


def _validate_invoice_lines(
    db: Session, *, scope: Scope, plane: _PlaneModels, document: _DocumentRow
) -> None:
    lines = tuple(
        db.execute(
            _where_scope(
                select(plane.line)
                .where(plane.line.document_id == document.id)
                .order_by(plane.line.line_number),
                scope,
                plane.line,
            )
        ).scalars()
    )
    explicit = document.authoring_mode == "explicit_multiline"
    if explicit and len(lines) < 2:
        raise BillingRuleViolation(
            "multiline_lines_missing",
            "explicit multiline invoice requires at least two lines",
        )
    if not lines or any(line.obligation_id is None for line in lines):
        raise BillingRuleViolation(
            "invoice_obligation_missing", "invoice lines require rated obligations"
        )
    obligations = tuple(
        _one(db, scope=scope, model=plane.obligation, row_id=line.obligation_id)
        for line in lines
    )
    _require_homogeneous(obligations)
    if tuple(line.line_number for line in lines) != tuple(range(1, len(lines) + 1)):
        raise BillingRuleViolation(
            "invalid_line_order", "invoice line numbers must be contiguous"
        )
    successors = db.execute(
        _where_scope(
            select(plane.obligation.id).where(
                plane.obligation.supersedes_obligation_id.in_(
                    tuple(row.id for row in obligations)
                )
            ),
            scope,
            plane.obligation,
        )
    ).first()
    if successors is not None:
        raise BillingConflict(
            "obligation_superseded", "invoice contains superseded rated evidence"
        )
    if (
        document.obligation_id != obligations[0].id
        or document.billing_account_id != obligations[0].billing_account_id
    ):
        raise BillingRuleViolation(
            "invoice_anchor_mismatch",
            "invoice anchor must identify its first obligation",
        )
    # The shipped single-line authoring contract uses rated pre-tax as unit amount,
    # independently of quantity. Preserve that historical contract; the new path
    # validates explicit price multiplication at authoring and multiline issuance.
    for line, row in zip(lines, obligations, strict=True):
        unit_money = _money(line.unit_amount, line.currency, line.minor_units)
        if unit_money.amount != line.unit_amount:
            raise BillingRuleViolation(
                "line_precision_mismatch",
                "stored unit price must be exact at currency precision",
            )
        if explicit:
            _require_line_input(
                DraftDocumentLine(
                    row.id,
                    line.description,
                    line.quantity,
                    line.unit_code,
                    unit_money,
                ),
                row,
            )
        if (
            line.billing_account_id != document.billing_account_id
            or line.currency != row.currency
            or line.minor_units != row.minor_units
            or line.price_source_version != row.price_version_id
            or (line.pre_tax_amount, line.tax_amount, line.total_amount)
            != (row.pre_tax_amount, row.tax_amount, row.total_amount)
        ):
            raise BillingRuleViolation(
                "invoice_line_evidence_mismatch",
                "draft line differs from rated evidence",
            )
    if (document.currency, document.minor_units) != (
        obligations[0].currency,
        obligations[0].minor_units,
    ):
        raise BillingRuleViolation(
            "invoice_currency_mismatch", "invoice currency differs from its obligations"
        )
    if (document.subtotal, document.tax_total, document.grand_total) != tuple(
        sum((getattr(row, name) for row in obligations), Decimal("0"))
        for name in ("pre_tax_amount", "tax_amount", "total_amount")
    ):
        raise BillingRuleViolation(
            "invoice_total_mismatch", "invoice totals must equal rated line evidence"
        )
    if explicit:
        _require_obligation_taxes(db, scope=scope, plane=plane, obligations=obligations)
    _document_financial_snapshots(db, scope=scope, plane=plane, document=document)


def _next_source_version(
    db: Session, *, scope: Scope, plane: _PlaneModels, account_id: UUID
) -> int:
    statement = select(func.coalesce(func.max(plane.group.source_version), 0)).where(
        plane.group.billing_account_id == account_id
    )
    statement = _where_scope(statement, scope, plane.group)
    return int(db.execute(statement).scalar_one()) + 1


@_exact_cash_operation
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
    backing = resolve_receivable_backing(
        db, scope=scope, document_id=document.id, source_version=source_version
    )
    outstanding = backing.remaining
    resolved_amount = backing.initial_amount - outstanding
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


def _require_obligation_taxes(
    db: Session,
    *,
    scope: Scope,
    plane: _PlaneModels,
    obligations: tuple[_ObligationRow, ...],
) -> None:
    ids = tuple(row.id for row in obligations)
    snapshots = tuple(
        db.execute(
            _where_scope(
                select(plane.tax).where(plane.tax.obligation_id.in_(ids)),
                scope,
                plane.tax,
            )
        ).scalars()
    )
    for obligation in obligations:
        matching = tuple(
            snapshot
            for snapshot in snapshots
            if snapshot.obligation_id == obligation.id
        )
        with localcontext() as context:
            context.prec = 60
            tax_total = sum(
                (snapshot.tax_amount for snapshot in matching), Decimal("0")
            )
        for snapshot in matching:
            if (snapshot.currency, snapshot.minor_units) != (
                obligation.currency,
                obligation.minor_units,
            ):
                raise BillingRuleViolation(
                    "tax_money_identity_mismatch",
                    "tax snapshots must match rated currency and precision",
                )
            for amount in (snapshot.taxable_basis, snapshot.tax_amount):
                if (
                    not amount.is_finite()
                    or amount < 0
                    or _money(amount, snapshot.currency, snapshot.minor_units).amount
                    != amount
                ):
                    raise BillingRuleViolation(
                        "tax_money_precision_mismatch",
                        "tax snapshot amounts must be exact nonnegative money",
                    )
        if tax_total != obligation.tax_amount:
            raise BillingRuleViolation(
                "tax_snapshot_total_mismatch",
                "tax snapshot amounts must sum exactly to rated tax",
            )


def _fx_fact(row: Any) -> AppliedFxSnapshotV1:
    return AppliedFxSnapshotV1(
        observation_id=row.observation_id,
        observation_version=row.observation_version,
        base_currency=row.base_currency,
        quote_currency=row.quote_currency,
        rate=row.rate,
        rate_purpose=row.rate_purpose,
        observed_at=row.observed_at,
        effective_at=row.effective_at,
        rounding_policy=row.rounding_policy,
        provenance=row.provenance,
    )


def _obligation_fx(
    db: Session, *, scope: Scope, plane: _PlaneModels, obligation_ids: tuple[UUID, ...]
) -> AppliedFxSnapshotV1 | None:
    if not obligation_ids:
        return None
    rows = tuple(
        db.execute(
            _where_scope(
                select(plane.fx).where(plane.fx.obligation_id.in_(obligation_ids)),
                scope,
                plane.fx,
            )
        ).scalars()
    )
    snapshots = []
    for obligation_id in obligation_ids:
        matching = tuple(row for row in rows if row.obligation_id == obligation_id)
        if len(matching) > 1:
            raise BillingRuleViolation(
                "ambiguous_fx", "an obligation must have at most one FX snapshot"
            )
        snapshots.append(None if not matching else _fx_fact(matching[0]))
    if any(snapshot != snapshots[0] for snapshot in snapshots):
        raise BillingRuleViolation(
            "heterogeneous_fx",
            "all obligation FX snapshots must be absent or identical",
        )
    return snapshots[0]


def _document_financial_snapshots(
    db: Session,
    *,
    scope: Scope,
    plane: _PlaneModels,
    document: _DocumentRow,
) -> tuple[tuple[AppliedTaxSnapshotV1, ...], AppliedFxSnapshotV1 | None]:
    ids_statement = (
        select(plane.line.obligation_id)
        .where(
            plane.line.document_id == document.id, plane.line.obligation_id.is_not(None)
        )
        .order_by(plane.line.line_number)
    )
    obligation_ids = tuple(
        db.execute(_where_scope(ids_statement, scope, plane.line)).scalars()
    )
    predicate = plane.tax.document_id == document.id
    if obligation_ids:
        predicate = predicate | plane.tax.obligation_id.in_(obligation_ids)
    taxes = tuple(
        db.execute(
            _where_scope(select(plane.tax).where(predicate), scope, plane.tax)
        ).scalars()
    )
    if (
        document.document_kind == "invoice"
        and document.authoring_mode == "explicit_multiline"
        and any(row.document_id == document.id for row in taxes)
    ):
        raise BillingRuleViolation(
            "multiline_document_tax_forbidden",
            "explicit multiline invoice tax must derive from its line obligations",
        )
    order = {key: index for index, key in enumerate(obligation_ids)}
    taxes = tuple(
        sorted(
            taxes,
            key=lambda row: (
                order.get(row.obligation_id, len(order)),
                row.treatment_code,
                row.jurisdiction_code,
                row.policy_id,
                row.policy_version,
                str(row.rate),
                str(row.taxable_basis),
                str(row.tax_amount),
            ),
        )
    )
    fx = _obligation_fx(db, scope=scope, plane=plane, obligation_ids=obligation_ids)
    document_fx = tuple(
        db.execute(
            _where_scope(
                select(plane.fx).where(plane.fx.document_id == document.id),
                scope,
                plane.fx,
            )
        ).scalars()
    )
    if len(document_fx) > 1 or (
        document_fx and obligation_ids and _fx_fact(document_fx[0]) != fx
    ):
        raise BillingRuleViolation(
            "heterogeneous_fx", "document FX must agree with its obligation snapshots"
        )
    if document_fx:
        fx = _fx_fact(document_fx[0])
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
        fx,
    )


@_exact_cash_operation
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
    withholding: WithholdingDischargeEffectV1 | None = None,
    withholding_source_owner: str | None = None,
    non_cash_grant: NonCashCreditGrantV1 | None = None,
    non_cash_effects: tuple[NonCashCreditEffectV1, ...] = (),
    non_cash_request_digest: str | None = None,
    opening_group_id: UUID | None = None,
    opening_receipt: OpeningImportReceiptV1 | OpeningImportReceiptV2 | None = None,
    opening_bases: tuple[OpeningReceivableBasisV1, ...] = (),
    opening_lots: tuple[OpeningCashLotFactV1, ...] = (),
    opening_effects: tuple[OpeningCashEffectV1, ...] = (),
    cash_source_order: tuple[CashCreditSourceOrderV1, ...] = (),
    corrects_posting_group_id: UUID | None = None,
) -> _PostingGroupRow:
    plane = _models(scope)
    allocation_inputs = tuple(allocations)
    _one(db, scope=scope, model=plane.account, row_id=account_id, lock=True)
    group_id = opening_group_id or uuid4()
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
    _persist_opening_records(
        db,
        scope=scope,
        group_id=group_id,
        receipt=opening_receipt,
        lots=opening_lots,
        effects=opening_effects,
    )
    _persist_opening_receivables(db, scope=scope, bases=opening_bases)
    if non_cash_grant is not None:
        payload = _jsonable(non_cash_grant)
        db.add(
            plane.non_cash_grant(
                **_values(
                    scope,
                    {
                        "id": non_cash_grant.grant_id,
                        "billing_account_id": account_id,
                        "posting_group_id": group_id,
                        "restricted_document_id": non_cash_grant.restricted_document_id,
                        "replaces_grant_id": non_cash_grant.replaces_grant_id,
                        "replaces_effect_id": non_cash_grant.replaces_effect_id,
                        "original_amount": non_cash_grant.original_amount.amount,
                        "currency": currency,
                        "minor_units": minor_units,
                        "source_owner": non_cash_grant.source_owner,
                        "source_ref": non_cash_grant.source_ref,
                        "source_version": non_cash_grant.source_version,
                        **{
                            name: getattr(non_cash_grant.classification, name)
                            for name in (
                                "category",
                                "config_owner",
                                "config_ref",
                                "config_version",
                                "config_digest",
                            )
                        },
                        "authorization_actor_ref": (
                            non_cash_grant.authorization_actor_ref
                        ),
                        "authorization_evidence_ref": (
                            non_cash_grant.authorization_evidence_ref
                        ),
                        "request_digest": non_cash_request_digest,
                        "evidence_digest": fingerprint_of(payload),
                        "evidence": payload,
                    },
                )
            )
        )
        db.flush()
    for non_cash_effect in non_cash_effects:
        payload = _jsonable(non_cash_effect)
        db.add(
            plane.non_cash_effect(
                **_values(
                    scope,
                    {
                        "id": non_cash_effect.effect_id,
                        "billing_account_id": account_id,
                        "grant_id": non_cash_effect.grant_id,
                        "posting_group_id": group_id,
                        "document_id": non_cash_effect.document_id,
                        "original_application_id": (
                            non_cash_effect.original_application_id
                        ),
                        "effect_kind": non_cash_effect.effect_kind,
                        "availability_delta": non_cash_effect.availability_delta.amount,
                        "currency": currency,
                        "minor_units": minor_units,
                        "source_owner": non_cash_effect.source_owner,
                        "source_ref": non_cash_effect.source_ref,
                        "source_version": non_cash_effect.source_version,
                        "authorization_actor_ref": (
                            non_cash_effect.authorization_actor_ref
                        ),
                        "authorization_evidence_ref": (
                            non_cash_effect.authorization_evidence_ref
                        ),
                        "request_digest": non_cash_request_digest,
                        "evidence_digest": fingerprint_of(payload),
                        "evidence": payload,
                    },
                )
            )
        )
    db.flush()
    if withholding is not None:
        db.add(
            plane.withholding(
                **_values(
                    scope,
                    {
                        "id": withholding.discharge_id,
                        "billing_account_id": account_id,
                        "document_id": withholding.document_id,
                        "posting_group_id": group_id,
                        "source_owner": withholding_source_owner,
                        "source_ref": source_ref,
                        "tax_source_owner": withholding.tax_evidence.source_owner,
                        "determination_ref": withholding.tax_evidence.determination_ref,
                        "component_ref": withholding.tax_evidence.component_ref,
                        "claim_ref": withholding.claim_ref,
                        "claimed_amount": withholding.claimed_amount.amount,
                        "amount_delta": withholding.accepted_delta.amount,
                        "currency": currency,
                        "minor_units": minor_units,
                        "evidence": _jsonable(withholding),
                        "settlement_id": withholding.settlement_id,
                        "allocation_id": withholding.allocation_id,
                        "reverses_discharge_id": withholding.reverses_discharge_id,
                    },
                )
            )
        )
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
                    "id": allocation.allocation_id or uuid4(),
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
    typed_position_v2 = _non_cash_position(db, scope=scope, base=typed_position)
    position_fact_v2_id = uuid4()
    db.add(
        plane.position_fact_v2(
            **_values(
                scope,
                {
                    "id": position_fact_v2_id,
                    "billing_account_id": account_id,
                    "source_owner": typed_position_v2.source_owner,
                    "source_version": source_version,
                    "posting_group_watermark": group_id,
                    "currency": currency,
                    "minor_units": minor_units,
                    "collectible_receivable": position.collectible_receivable,
                    "available_credit": position.available_credit,
                    "prepaid_funding": position.prepaid_funding,
                    "available_non_cash_credit": (
                        typed_position_v2.available_non_cash_credit.amount
                    ),
                    "state_fingerprint": typed_position_v2.state_fingerprint,
                    "rebuild_fingerprint": typed_position_v2.state_fingerprint,
                    "source_authority": "internal",
                    "projection_mode": "authoritative",
                    "derived_from": "posting_groups",
                    "completeness": "complete",
                    "completeness_reason_code": None,
                    "financial_state": typed_position_v2.financial_state,
                    "observed_at": observed_at,
                },
            )
        )
    )
    affected_documents = {
        *(item.document_id for item in opening_bases),
        *(item.document_id for item in opening_effects if item.document_id is not None),
        *exposure_document_ids,
        *(
            allocation.document_id
            for allocation in allocation_inputs
            if allocation.document_id is not None
        ),
    }
    exposure_facts: list[tuple[UUID, ReceivableExposureV1]] = []
    imported_exposure_facts: list[ReceivableExposureV2] = []
    for document_id in sorted(affected_documents, key=str):
        candidate = _one(db, scope=scope, model=plane.document, row_id=document_id)
        if candidate.authoring_mode == "imported_open_item":
            imported_exposure_facts.append(
                _persist_imported_exposure(
                    db,
                    scope=scope,
                    document_id=document_id,
                    source_version=source_version,
                    group_id=group_id,
                    observed_at=observed_at,
                )
            )
            continue
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
        )
        reverses_fact_id = db.execute(
            _where_scope(reversed_fact_statement, scope, plane.accounting_fact)
        ).scalar_one()
    typed_fact: (
        AccountingFactV1
        | AccountingFactV2
        | AccountingFactV3
        | AccountingFactV4
        | AccountingFactV5
    )
    if isinstance(opening_receipt, OpeningImportReceiptV2):
        typed_fact = AccountingFactV5(
            scope,
            "dotmac-billing",
            accounting_fact_id,
            5,
            account_id,
            group_id,
            source_ref,
            "internal",
            cast(Any, kind),
            occurred_at,
            observed_at,
            tuple(
                AccountingEffectV1(
                    cast(Any, row.lane), _money(row.amount_delta, currency, minor_units)
                )
                for row in effect_rows
            ),
            opening_receipt,
            opening_bases
            or tuple(
                get_opening_receivable_basis(db, scope=scope, basis_id=value)
                for value in opening_receipt.basis_ids
            ),
            opening_effects,
            corrects_posting_group_id,
        )
    elif opening_effects or kind == "cash_credit_draw_reversed":
        typed_fact = AccountingFactV4(
            scope,
            "dotmac-billing",
            accounting_fact_id,
            4,
            account_id,
            group_id,
            source_ref,
            "internal",
            cast(OpeningCashPostingKind, kind),
            occurred_at,
            observed_at,
            tuple(
                AccountingEffectV1(
                    cast(
                        Literal["receivable", "available_credit", "prepaid_funding"],
                        row.lane,
                    ),
                    _money(row.amount_delta, currency, minor_units),
                )
                for row in effect_rows
            ),
            tuple(
                AccountingSettlementAllocationV4(
                    row.id,
                    AccountingAllocationEffectV1(
                        row.settlement_id,
                        row.document_id,
                        cast(
                            Literal[
                                "allocation",
                                "deallocation",
                                "reallocation",
                                "refund",
                                "reversal",
                            ],
                            row.effect_kind,
                        ),
                        _money(row.amount_delta, currency, minor_units),
                        row.offsets_allocation_id,
                    ),
                )
                for row in allocation_rows
            ),
            opening_effects,
            cash_source_order
            or tuple(
                CashCreditSourceOrderV1(index, None, item.effect_id)
                for index, item in enumerate(opening_effects, 1)
            ),
            opening_receipt,
            corrects_posting_group_id,
        )
    elif non_cash_effects:
        typed_fact = AccountingFactV3(
            scope=scope,
            source_system="dotmac-billing",
            fact_id=accounting_fact_id,
            fact_version=3,
            billing_account_id=account_id,
            posting_group_id=group_id,
            source_ref=source_ref,
            source_authority="internal",
            effect_kind=cast(
                Literal[
                    "non_cash_credit_granted",
                    "non_cash_credit_applied",
                    "non_cash_credit_application_reversed",
                    "non_cash_credit_reduced",
                ],
                kind,
            ),
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
            non_cash_credit_effects=non_cash_effects,
            non_cash_credit_grant=non_cash_grant,
        )
    elif withholding is not None:
        typed_fact = AccountingFactV2(
            scope=scope,
            source_system="dotmac-billing",
            fact_id=accounting_fact_id,
            fact_version=2,
            billing_account_id=account_id,
            posting_group_id=group_id,
            source_ref=source_ref,
            source_authority="internal",
            effect_kind=cast(
                Literal["withholding_accepted", "withholding_reversed"], kind
            ),
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
            withholding_discharges=(withholding,),
            reverses_fact_id=reverses_fact_id,
        )
    else:
        typed_fact = AccountingFactV1(
            scope=scope,
            source_system="dotmac-billing",
            fact_id=accounting_fact_id,
            fact_version=1,
            billing_account_id=account_id,
            posting_group_id=group_id,
            source_ref=source_ref,
            source_authority="internal",
            effect_kind=cast(
                Literal[
                    "invoice_issued",
                    "credit_note_issued",
                    "settlement_accepted",
                    "allocation",
                    "deallocation",
                    "reallocation",
                    "refund",
                    "reversal",
                ],
                kind,
            ),
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
                    "fact_version": typed_fact.fact_version,
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
    if isinstance(typed_fact, AccountingFactV5):
        _emit(
            db,
            scope=scope,
            event_type="billing.accounting.fact.v5",
            payload=fact_payload,
        )
    elif isinstance(typed_fact, AccountingFactV4):
        _emit(
            db,
            scope=scope,
            event_type="billing.accounting.fact.v4",
            payload=fact_payload,
        )
    elif non_cash_effects:
        _emit(
            db,
            scope=scope,
            event_type="billing.accounting.fact.v3",
            payload=fact_payload,
        )
    elif withholding is not None:
        _emit(
            db,
            scope=scope,
            event_type="billing.accounting.fact.v2",
            payload=fact_payload,
        )
    else:
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
    _emit(
        db,
        scope=scope,
        event_type="billing.receivable.position.v2",
        payload={
            "position_fact_id": str(position_fact_v2_id),
            **_jsonable(typed_position_v2),
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
    for imported_exposure in imported_exposure_facts:
        _emit(
            db,
            scope=scope,
            event_type="billing.receivable.exposure.v2",
            payload=_jsonable(imported_exposure),
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
    lines_statement = (
        select(plane.line)
        .where(plane.line.document_id == document.id)
        .order_by(plane.line.line_number)
    )
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
    document_evidence = _one(
        db, scope=scope, model=plane.document, row_id=command.document_id
    )
    _one(
        db,
        scope=scope,
        model=plane.account,
        row_id=document_evidence.billing_account_id,
        lock=True,
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
        # A locking SELECT does not replace an existing ORM identity. The
        # pre-lock read may therefore still hold a draft header or lines from
        # before a concurrent writer committed. Preserve this owner's pending
        # edits, then read the locked persisted snapshot before issuing facts.
        session.flush()
        session.refresh(document)
        session.execute(
            _where_scope(
                select(plane.line)
                .where(plane.line.document_id == document.id)
                .execution_options(populate_existing=True),
                scope,
                plane.line,
            )
        ).scalars().all()
        if document.issued_at is not None:
            raise BillingConflict("already_issued", "document is already issued")
        if document.document_kind == "invoice":
            _validate_invoice_lines(
                session, scope=scope, plane=plane, document=document
            )
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
    document_evidence = _one(db, scope=scope, model=plane.document, row_id=document_id)
    _one(
        db,
        scope=scope,
        model=plane.account,
        row_id=document_evidence.billing_account_id,
        lock=True,
    )
    offered = fingerprint_of(
        {"document_id": document_id, "actor_ref": actor_ref, "occurred_at": occurred_at}
    )

    def operation(session: Session) -> dict[str, object]:
        document = _one(
            session, scope=scope, model=plane.document, row_id=document_id, lock=True
        )
        if document.authoring_mode == "imported_open_item":
            raise BillingRuleViolation(
                "opening_account_owner_required",
                "imported principal requires its full opening unwind owner",
            )
        if document.issued_at is None:
            raise BillingRuleViolation(
                "not_issued", "only an issued document can be voided"
            )
        if _document_withheld(session, scope=scope, document_id=document_id) != 0:
            raise BillingRuleViolation(
                "void_has_withholding", "reverse accepted withholding before voiding"
            )
        if (
            _document_non_cash_applied(session, scope=scope, document_id=document_id)
            != 0
        ):
            raise BillingRuleViolation(
                "void_has_non_cash_credit", "reverse credit applications before voiding"
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
    original_evidence = _one(
        db, scope=scope, model=plane.document, row_id=command.original_document_id
    )
    _one(
        db,
        scope=scope,
        model=plane.account,
        row_id=original_evidence.billing_account_id,
        lock=True,
    )
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
    offered = fingerprint_of(asdict(command))

    def operation(session: Session) -> dict[str, object]:
        # Exact replay precedes mutable admission: a full credit has already
        # consumed this receivable. The original remains locked across both
        # idempotency lookup and first-time admission.
        _require_historical_source_unclaimed(
            session,
            scope=scope,
            operation="billing.credit_note.issue",
            source_owner="dotmac-billing",
            source_ref=command.correlation_id,
        )
        outstanding = _document_outstanding(session, scope=scope, document=original)
        if (
            command.total_amount.amount <= 0
            or command.total_amount.amount > outstanding
        ):
            raise BillingRuleViolation(
                "credit_exceeds_receivable", "credit note exceeds the open receivable"
            )
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
                    "lifecycle": "draft",
                    "series_code": command.series_code,
                    "document_number": None,
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
                    "issued_at": None,
                },
            )
        )
        session.add(document)
        session.flush()
        session.add(
            plane.line(
                **_values(
                    scope,
                    {
                        "id": uuid4(),
                        "document_id": document_id,
                        "billing_account_id": original.billing_account_id,
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
        # Finish authoring before freezing every issued document's line set.
        # A flush orders the line INSERT before the parent becomes issued;
        # transaction commit/rollback remains the caller's responsibility.
        session.flush()
        document.document_number = number
        document.lifecycle = "issued"
        document.issued_at = command.occurred_at
        session.flush()
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


def _require_cash_source_unclaimed(
    db: Session, *, scope: Scope, command: AcceptSettlementV1
) -> None:
    plane = _models(scope)
    existing = db.execute(
        _where_scope(
            select(plane.cash_source_claim.id).where(
                plane.cash_source_claim.source_system == command.source_system,
                plane.cash_source_claim.source_settlement_key
                == command.source_settlement_key,
            ),
            scope,
            plane.cash_source_claim,
        )
    ).first()
    if existing is not None:
        raise BillingConflict(
            "cash_source_claimed", "cash source identity is already claimed"
        )


def _claim_cash_source(
    db: Session, *, scope: Scope, settlement: _SettlementRow
) -> None:
    """Append a real settlement's identity inside its existing Kernel operation."""
    plane = _models(scope)
    db.add(
        plane.cash_source_claim(
            **_values(
                scope,
                {
                    "id": uuid4(),
                    "settlement_id": settlement.id,
                    "billing_account_id": settlement.billing_account_id,
                    "source_system": settlement.source_system,
                    "source_settlement_key": settlement.source_settlement_key,
                    "currency": settlement.currency,
                    "minor_units": settlement.minor_units,
                },
            )
        )
    )
    db.flush()


def _require_cash_source_claim(
    db: Session, *, scope: Scope, settlement_id: UUID, command: AcceptSettlementV1
) -> None:
    """Persisted scalar evidence only; replay must never repair missing claims."""
    plane = _models(scope)
    binding = db.execute(
        _where_scope(
            select(
                plane.cash_source_claim.billing_account_id,
                plane.cash_source_claim.source_system,
                plane.cash_source_claim.source_settlement_key,
                plane.cash_source_claim.currency,
                plane.cash_source_claim.minor_units,
            ).where(plane.cash_source_claim.settlement_id == settlement_id),
            scope,
            plane.cash_source_claim,
        )
    ).one_or_none()
    if binding is None or tuple(binding) != (
        command.billing_account_id,
        command.source_system,
        command.source_settlement_key,
        command.amount.currency.code,
        command.amount.currency.minor_units,
    ):
        raise BillingRuleViolation(
            "cash_source_claim_integrity",
            "settlement cash-source claim is missing or inconsistent",
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
        _require_cash_source_unclaimed(session, scope=scope, command=command)
        settlement_id = uuid4()
        settlement = plane.settlement(
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
        session.add(settlement)
        session.flush()
        _claim_cash_source(session, scope=scope, settlement=settlement)
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

    outcome = _idempotent_cash_claim(
        db,
        scope=scope,
        operation_scope="billing.settlement",
        key=key,
        fingerprint=offered,
        operation=operation,
    )
    settlement_id = UUID(str(outcome.result["settlement_id"]))
    # Keep the published key and fingerprint above byte-for-byte compatible.
    # Their historical omissions are bound against persisted scalars, never a
    # retained ORM entity whose attributes may differ from immutable evidence.
    binding = db.execute(
        _where_scope(
            select(
                plane.settlement.billing_account_id,
                plane.settlement.funding_lane,
                plane.settlement.confirmation_evidence,
                plane.settlement.source_system,
                plane.settlement.source_settlement_key,
            ).where(plane.settlement.id == settlement_id),
            scope,
            plane.settlement,
        )
    ).one()
    if tuple(binding) != (
        command.billing_account_id,
        command.funding_lane.value,
        command.confirmation_evidence,
        command.source_system,
        command.source_settlement_key,
    ):
        raise IdempotencyConflict(
            "Idempotency key was already used in scope 'billing.settlement' "
            "with a different request"
        )
    _require_cash_source_claim(
        db, scope=scope, settlement_id=settlement_id, command=command
    )
    return cast(
        _SettlementRow,
        db.execute(
            _where_scope(
                select(plane.settlement)
                .where(plane.settlement.id == settlement_id)
                .execution_options(populate_existing=True),
                scope,
                plane.settlement,
            )
        ).scalar_one(),
    )


def _settlement_used(db: Session, *, scope: Scope, settlement_id: UUID) -> Decimal:
    plane = _models(scope)
    statement = select(func.coalesce(func.sum(plane.allocation.amount_delta), 0)).where(
        plane.allocation.settlement_id == settlement_id
    )
    return Decimal(
        db.execute(_where_scope(statement, scope, plane.allocation)).scalar_one()
    )


@_exact_cash_operation
def _document_allocated(db: Session, *, scope: Scope, document_id: UUID) -> Decimal:
    plane = _models(scope)
    statement = select(func.coalesce(func.sum(plane.allocation.amount_delta), 0)).where(
        plane.allocation.document_id == document_id
    )
    ordinary = Decimal(
        db.execute(_where_scope(statement, scope, plane.allocation)).scalar_one()
    )
    return ordinary + _opening_document_applied(
        db, scope=scope, document_id=document_id
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


def _document_withheld(db: Session, *, scope: Scope, document_id: UUID) -> Decimal:
    plane = _models(scope)
    statement = select(
        func.coalesce(func.sum(plane.withholding.amount_delta), 0)
    ).where(plane.withholding.document_id == document_id)
    return Decimal(
        db.execute(_where_scope(statement, scope, plane.withholding)).scalar_one()
    )


@_exact_cash_operation
def _document_outstanding(
    db: Session, *, scope: Scope, document: _DocumentRow
) -> Decimal:
    backing = resolve_receivable_backing(db, scope=scope, document_id=document.id)
    return backing.remaining


def _settlement_has_active_backing(
    db: Session, *, scope: Scope, settlement: _SettlementRow
) -> bool:
    """Resolve the immutable acceptance/reversal chain, refusing ambiguity."""

    plane = _models(scope)
    statement = select(plane.group).where(
        plane.group.source_ref == f"settlement:{settlement.id}",
        plane.group.group_kind == "settlement_accepted",
    )
    roots = tuple(db.execute(_where_scope(statement, scope, plane.group)).scalars())
    if len(roots) != 1:
        raise BillingRuleViolation(
            "settlement_backing_ambiguous",
            "settlement acceptance evidence is ambiguous",
        )
    current = roots[0]
    active = True
    visited: set[UUID] = set()
    expected = Decimal(settlement.amount)
    while True:
        if (
            current.id in visited
            or current.billing_account_id != settlement.billing_account_id
            or current.currency != settlement.currency
            or current.minor_units != settlement.minor_units
            or (not visited and current.reverses_group_id is not None)
            or (visited and current.group_kind != "reversal")
        ):
            raise BillingRuleViolation(
                "settlement_backing_ambiguous",
                "settlement reversal lineage is ambiguous",
            )
        visited.add(current.id)
        effects_statement = select(plane.effect).where(
            plane.effect.posting_group_id == current.id
        )
        effects = tuple(
            db.execute(_where_scope(effects_statement, scope, plane.effect)).scalars()
        )
        if len(effects) != 1 or any(
            row.billing_account_id != settlement.billing_account_id
            or row.currency != settlement.currency
            or row.minor_units != settlement.minor_units
            or row.lane != settlement.funding_lane
            or row.amount_delta != expected
            for row in effects
        ):
            raise BillingRuleViolation(
                "settlement_backing_ambiguous",
                "settlement funding effects are ambiguous",
            )
        children_statement = select(plane.group).where(
            plane.group.reverses_group_id == current.id
        )
        children = tuple(
            db.execute(_where_scope(children_statement, scope, plane.group)).scalars()
        )
        if not children:
            return active
        if len(children) != 1:
            raise BillingRuleViolation(
                "settlement_backing_ambiguous", "settlement reversal lineage branches"
            )
        current = children[0]
        active = not active
        expected = -expected


def _require_live_invoice_issuance(
    db: Session, *, scope: Scope, document: _DocumentRow
) -> None:
    """Require either genuine live issuance or an authenticated opening basis."""
    backing = resolve_receivable_backing(db, scope=scope, document_id=document.id)
    if not backing.active:
        raise BillingRuleViolation(
            "invoice_issuance_not_live",
            "invoice requires one unreversed, structurally bound issuance posting"
            if backing.basis_id is None
            else "invoice backing is no longer active",
        )


def _require_credit_target(
    db: Session, *, scope: Scope, document: _DocumentRow, account_id: UUID
) -> None:
    if document.billing_account_id != account_id:
        raise BillingRuleViolation(
            "allocation_account_mismatch",
            "invoice and funding must belong to the same account",
        )
    if document.issued_at is None or document.document_kind != "invoice":
        raise BillingRuleViolation(
            "allocation_target_invalid", "allocation requires an issued invoice"
        )
    plane = _models(scope)
    statement = select(plane.event.id).where(
        plane.event.document_id == document.id, plane.event.event_kind == "voided"
    )
    if db.execute(_where_scope(statement, scope, plane.event)).first() is not None:
        raise BillingRuleViolation(
            "document_cancelled", "a cancelled invoice cannot receive credit"
        )


def _require_withholding_command(
    *, scope: Scope, command: AcceptWithholdingDischarge
) -> None:
    if not isinstance(scope, TenantScope | PlatformScope):
        raise TypeError("withholding requires an explicit scope")
    _require_scope_matches(routed=scope, declared=command.scope)
    evidence = command.tax_evidence
    _require_scope_matches(routed=scope, declared=evidence.scope)
    if (
        evidence.document_id != command.document_id
        or evidence.billing_account_id != command.billing_account_id
    ):
        raise BillingRuleViolation(
            "withholding_evidence_scope",
            "Tax evidence must bind this invoice and account",
        )
    for text, maximum in (
        (command.idempotency_key, 255),
        (command.source_owner, 120),
        (command.source_ref, 255),
        (command.claim_ref, 255),
        (command.authorization_actor_ref, 255),
        (command.authorization_evidence_ref, 255),
    ):
        if not isinstance(text, str) or not text.strip() or len(text) > maximum:
            raise BillingRuleViolation(
                "withholding_evidence_required",
                "withholding requires bounded source and audit evidence",
            )
    if command.occurred_at.tzinfo is None:
        raise BillingRuleViolation(
            "withholding_instant_invalid", "withholding requires an aware instant"
        )
    _withholding_money(command.claimed_amount, positive=True)
    _withholding_money(command.accepted_amount, positive=True)
    _require_same_money(
        command.claimed_amount, command.accepted_amount, evidence.amount
    )
    if (
        not command.accepted_amount.amount
        <= command.claimed_amount.amount
        <= evidence.amount.amount
    ):
        raise BillingRuleViolation(
            "withholding_exceeds_evidence",
            "accepted <= claimed <= determined withholding is required",
        )
    if command.allocation_id is not None and command.settlement_id is None:
        raise BillingRuleViolation(
            "withholding_cash_link_invalid",
            "allocation evidence requires its actual settlement",
        )


def _non_cash_position(
    db: Session, *, scope: Scope, base: ReceivablePositionV1
) -> ReceivablePositionV2:
    plane = _models(scope)
    table = plane.non_cash_effect.__table__
    statement = select(
        table.c.id, table.c.posting_group_id, table.c.availability_delta
    ).where(table.c.billing_account_id == base.billing_account_id)
    if isinstance(scope, TenantScope):
        statement = statement.where(table.c.tenant_id == scope.tenant_id)
    rows = db.execute(statement.order_by(table.c.id)).all()
    validated_grants: set[UUID] = set()
    for row in rows:
        fact = get_non_cash_credit_effect(db, scope=scope, effect_id=row.id)
        if fact.grant_id not in validated_grants:
            get_non_cash_credit_grant(db, scope=scope, grant_id=fact.grant_id)
            validated_grants.add(fact.grant_id)
    available = sum((row.availability_delta for row in rows), Decimal("0"))
    if available < 0:
        raise BillingConflict(
            "non_cash_credit_negative",
            "immutable noncash effects have a negative balance",
        )
    fingerprint = fingerprint_of(
        {
            "contract": "billing.receivable.position.v2",
            "v1_fingerprint": base.state_fingerprint,
            "non_cash_effects": [
                {
                    "id": str(row.id),
                    "posting_group_id": str(row.posting_group_id),
                    "availability_delta": str(row.availability_delta),
                }
                for row in rows
            ],
            "available_non_cash_credit": str(available),
        }
    )
    values = {field.name: getattr(base, field.name) for field in fields(base)}
    values["state_fingerprint"] = fingerprint
    return ReceivablePositionV2(
        **values,
        available_non_cash_credit=_money(
            available,
            base.available_credit.currency.code,
            base.available_credit.currency.minor_units,
        ),
    )


def _verified_non_cash_sum(
    db: Session,
    *,
    scope: Scope,
    grant_id: UUID | None = None,
    document_id: UUID | None = None,
    application_id: UUID | None = None,
) -> Decimal:
    plane = _models(scope)
    table = plane.non_cash_effect.__table__
    statement = select(table.c.id)
    if grant_id is not None:
        statement = statement.where(table.c.grant_id == grant_id)
    if document_id is not None:
        statement = statement.where(table.c.document_id == document_id)
    if application_id is not None:
        statement = statement.where(table.c.original_application_id == application_id)
    if isinstance(scope, TenantScope):
        statement = statement.where(table.c.tenant_id == scope.tenant_id)
    with db.no_autoflush:
        ids = db.execute(statement).scalars().all()
    total = Decimal("0")
    for effect_id in ids:
        effect = get_non_cash_credit_effect(db, scope=scope, effect_id=effect_id)
        get_non_cash_credit_grant(db, scope=scope, grant_id=effect.grant_id)
        total += effect.availability_delta.amount
    return total


def _document_non_cash_applied(
    db: Session, *, scope: Scope, document_id: UUID
) -> Decimal:
    return -_verified_non_cash_sum(db, scope=scope, document_id=document_id)


def _non_cash_available(db: Session, *, scope: Scope, grant_id: UUID) -> Decimal:
    return _verified_non_cash_sum(db, scope=scope, grant_id=grant_id)


_NonCashCommand = (
    GrantNonCashCredit
    | ApplyNonCashCredit
    | ReverseNonCashCreditApplication
    | ReduceNonCashCreditGrant
)


def _non_cash_digest(command: _NonCashCommand) -> str:
    values = asdict(command)
    values.pop("idempotency_key")
    return fingerprint_of(values)


def _non_cash_replay(
    db: Session, *, scope: Scope, command: _NonCashCommand, grant: bool, digest: str
) -> NonCashCreditGrantV1 | NonCashCreditEffectV1 | None:
    plane = _models(scope)
    table = (plane.non_cash_grant if grant else plane.non_cash_effect).__table__
    statement = select(table.c.id, table.c.request_digest).where(
        table.c.source_owner == command.source_owner,
        table.c.source_ref == command.source_ref,
    )
    if isinstance(scope, TenantScope):
        statement = statement.where(table.c.tenant_id == scope.tenant_id)
    with db.no_autoflush:
        row = db.execute(statement).one_or_none()
    if row is None:
        if isinstance(command, _NonCashCommand):
            _require_historical_source_unclaimed(
                db,
                scope=scope,
                operation="billing.non_cash_credit.grant"
                if grant
                else "billing.non_cash_credit.apply",
                source_owner=command.source_owner,
                source_ref=command.source_ref,
            )
        return None
    if row.request_digest != digest:
        raise BillingConflict(
            "non_cash_credit_source_conflict",
            "source identity already binds different immutable credit evidence",
        )
    return (
        get_non_cash_credit_grant(db, scope=scope, grant_id=row.id)
        if grant
        else get_non_cash_credit_effect(db, scope=scope, effect_id=row.id)
    )


def _require_non_cash_command(scope: Scope, command: _NonCashCommand) -> None:
    if not isinstance(scope, TenantScope | PlatformScope):
        raise TypeError("noncash command requires explicit scope")
    _require_scope_matches(routed=scope, declared=command.scope)
    _non_cash_identity(
        command.source_owner,
        command.source_ref,
        command.source_version,
        command.authorization_actor_ref,
        command.authorization_evidence_ref,
        command.idempotency_key,
    )
    if command.occurred_at.tzinfo is None:
        raise BillingRuleViolation(
            "non_cash_credit_evidence_required",
            "noncash command requires an aware instant",
        )
    if command.amount is not None:
        _withholding_money(command.amount, positive=True)


def _non_cash_effect(
    command: _NonCashCommand,
    *,
    grant_id: UUID,
    account_id: UUID,
    kind: Literal["grant", "application", "application_reversal", "unused_reduction"],
    amount: Money,
    document_id: UUID | None = None,
    original_application_id: UUID | None = None,
) -> NonCashCreditEffectV1:
    return NonCashCreditEffectV1(
        scope=command.scope,
        effect_id=uuid4(),
        grant_id=grant_id,
        billing_account_id=account_id,
        effect_kind=kind,
        availability_delta=amount,
        source_owner=command.source_owner,
        source_ref=command.source_ref,
        source_version=command.source_version,
        authorization_actor_ref=command.authorization_actor_ref,
        authorization_evidence_ref=command.authorization_evidence_ref,
        occurred_at=command.occurred_at,
        document_id=document_id,
        original_application_id=original_application_id,
    )


def grant_non_cash_credit(
    db: Session, *, scope: Scope, command: GrantNonCashCredit
) -> NonCashCreditGrantV1:
    """Grant ordinary non-tax commercial value; adopting assembly authorizes finance.

    No Payment, Settlement, allocation or tax determination is fabricated. The
    immutable instrument records subtotal=total=original_amount and tax=zero.
    """
    _require_non_cash_command(scope, command)
    if not isinstance(command.apply_on_issue, bool):
        raise BillingRuleViolation(
            "non_cash_credit_invalid", "apply_on_issue must be a boolean"
        )
    digest = _non_cash_digest(command)
    plane = _models(scope)

    def operation(session: Session) -> dict[str, object]:
        replay = _non_cash_replay(
            session, scope=scope, command=command, grant=True, digest=digest
        )
        if replay is not None:
            return {"grant_id": str(cast(NonCashCreditGrantV1, replay).grant_id)}
        account = _one(
            session,
            scope=scope,
            model=plane.account,
            row_id=command.billing_account_id,
            lock=True,
        )
        replay = _non_cash_replay(
            session, scope=scope, command=command, grant=True, digest=digest
        )
        if replay is not None:
            return {"grant_id": str(cast(NonCashCreditGrantV1, replay).grant_id)}
        _require_account_money(account, command.amount)
        grant = NonCashCreditGrantV1(
            scope,
            uuid4(),
            account.id,
            command.amount,
            command.classification,
            command.source_owner,
            command.source_ref,
            command.source_version,
            command.authorization_actor_ref,
            command.authorization_evidence_ref,
            command.occurred_at,
            command.restricted_document_id,
            command.replaces_grant_id,
            command.replaces_effect_id,
        )
        if (command.replaces_grant_id is None) != (command.replaces_effect_id is None):
            raise BillingRuleViolation(
                "non_cash_credit_replacement_mismatch",
                "replacement requires both the original grant and its reduction",
            )
        if (
            command.replaces_grant_id is not None
            and command.replaces_effect_id is not None
        ):
            replaced = get_non_cash_credit_grant(
                session, scope=scope, grant_id=command.replaces_grant_id
            )
            _one(
                session,
                scope=scope,
                model=plane.non_cash_grant,
                row_id=replaced.grant_id,
                lock=True,
            )
            correction = get_non_cash_credit_effect(
                session, scope=scope, effect_id=command.replaces_effect_id
            )
            _one(
                session,
                scope=scope,
                model=plane.non_cash_effect,
                row_id=correction.effect_id,
                lock=True,
            )
            if (
                replaced.billing_account_id != account.id
                or replaced.original_amount.currency != command.amount.currency
                or replaced.restricted_document_id != command.restricted_document_id
                or correction.grant_id != replaced.grant_id
                or correction.effect_kind != "unused_reduction"
            ):
                raise BillingRuleViolation(
                    "non_cash_credit_replacement_mismatch",
                    "replacement must bind the same restricted grant "
                    "and its unused reduction",
                )
            statement = select(plane.non_cash_grant.id).where(
                plane.non_cash_grant.replaces_effect_id == correction.effect_id
            )
            prior = (
                session.execute(_where_scope(statement, scope, plane.non_cash_grant))
                .scalars()
                .all()
            )
            restored = sum(
                (
                    get_non_cash_credit_grant(
                        session, scope=scope, grant_id=identity
                    ).original_amount.amount
                    for identity in prior
                ),
                Decimal("0"),
            )
            if restored + command.amount.amount > -correction.availability_delta.amount:
                raise BillingRuleViolation(
                    "non_cash_credit_replacement_exceeded",
                    "replacement exceeds the specific unused reduction",
                )
        granted = _non_cash_effect(
            command,
            grant_id=grant.grant_id,
            account_id=account.id,
            kind="grant",
            amount=command.amount,
        )
        non_cash_effects: tuple[NonCashCreditEffectV1, ...] = (granted,)
        ar_effects: tuple[tuple[EffectLane, Decimal], ...] = ()
        documents: tuple[UUID, ...] = ()
        if command.restricted_document_id is not None:
            invoice = _one(
                session,
                scope=scope,
                model=plane.document,
                row_id=command.restricted_document_id,
                lock=True,
            )
            _require_credit_target(
                session, scope=scope, document=invoice, account_id=account.id
            )
            _require_live_invoice_issuance(session, scope=scope, document=invoice)
            _require_same_money(
                command.amount,
                _money(invoice.grand_total, invoice.currency, invoice.minor_units),
            )
            if command.apply_on_issue:
                amount = min(
                    command.amount.amount,
                    _document_outstanding(session, scope=scope, document=invoice),
                )
                if amount > 0:
                    applied = replace(
                        _non_cash_effect(
                            command,
                            grant_id=grant.grant_id,
                            account_id=account.id,
                            kind="application",
                            amount=_money(
                                -amount, account.currency, account.minor_units
                            ),
                            document_id=invoice.id,
                        ),
                        source_owner="dotmac-billing",
                        source_ref=f"apply-on-issue:{grant.grant_id}",
                    )
                    non_cash_effects += (applied,)
                    ar_effects = ((EffectLane.RECEIVABLE, -amount),)
                    documents = (invoice.id,)
        _post_group(
            session,
            scope=scope,
            account_id=account.id,
            kind="non_cash_credit_granted",
            currency=account.currency,
            minor_units=account.minor_units,
            source_ref=command.source_ref,
            occurred_at=command.occurred_at,
            effects=ar_effects,
            exposure_document_ids=documents,
            non_cash_grant=grant,
            non_cash_effects=non_cash_effects,
            non_cash_request_digest=digest,
        )
        return {"grant_id": str(grant.grant_id)}

    outcome = _idempotent(
        db,
        scope=scope,
        operation_scope="billing.non_cash_credit.grant",
        key=command.idempotency_key,
        fingerprint=digest,
        operation=operation,
    )
    return get_non_cash_credit_grant(
        db, scope=scope, grant_id=UUID(str(outcome.result["grant_id"]))
    )


def _non_cash_change(
    db: Session,
    *,
    scope: Scope,
    command: ApplyNonCashCredit
    | ReverseNonCashCreditApplication
    | ReduceNonCashCreditGrant,
) -> NonCashCreditEffectV1:
    _require_non_cash_command(scope, command)
    digest = _non_cash_digest(command)
    operation_name = (
        "apply"
        if isinstance(command, ApplyNonCashCredit)
        else "reverse_application"
        if isinstance(command, ReverseNonCashCreditApplication)
        else "reduce"
    )

    def operation(session: Session) -> dict[str, object]:
        replay = _non_cash_replay(
            session, scope=scope, command=command, grant=False, digest=digest
        )
        if replay is not None:
            return {"effect_id": str(cast(NonCashCreditEffectV1, replay).effect_id)}
        original = (
            get_non_cash_credit_effect(
                session, scope=scope, effect_id=command.application_id
            )
            if isinstance(command, ReverseNonCashCreditApplication)
            else None
        )
        grant_id = (
            original.grant_id
            if original
            else cast(ApplyNonCashCredit | ReduceNonCashCreditGrant, command).grant_id
        )
        grant = get_non_cash_credit_grant(session, scope=scope, grant_id=grant_id)
        plane = _models(scope)
        _one(
            session,
            scope=scope,
            model=plane.account,
            row_id=grant.billing_account_id,
            lock=True,
        )
        _one(
            session, scope=scope, model=plane.non_cash_grant, row_id=grant_id, lock=True
        )
        replay = _non_cash_replay(
            session, scope=scope, command=command, grant=False, digest=digest
        )
        if replay is not None:
            return {"effect_id": str(cast(NonCashCreditEffectV1, replay).effect_id)}
        available = _non_cash_available(session, scope=scope, grant_id=grant_id)
        document_id = None
        original_application_id = None
        if isinstance(command, ApplyNonCashCredit):
            document_id = command.document_id
            if (
                grant.restricted_document_id is not None
                and grant.restricted_document_id != document_id
            ):
                raise BillingRuleViolation(
                    "non_cash_credit_invoice_restricted",
                    "grant is restricted to another invoice",
                )
            invoice = _one(
                session,
                scope=scope,
                model=plane.document,
                row_id=document_id,
                lock=True,
            )
            _require_credit_target(
                session,
                scope=scope,
                document=invoice,
                account_id=grant.billing_account_id,
            )
            _require_live_invoice_issuance(session, scope=scope, document=invoice)
            _require_same_money(
                grant.original_amount,
                _money(invoice.grand_total, invoice.currency, invoice.minor_units),
            )
            cap = min(
                available, _document_outstanding(session, scope=scope, document=invoice)
            )
            effect_kind: Literal[
                "grant", "application", "application_reversal", "unused_reduction"
            ] = "application"
            group_kind: _AccountingEffectKind = "non_cash_credit_applied"
            sign = -1
        elif original is not None:
            if original.effect_kind != "application":
                raise BillingRuleViolation(
                    "non_cash_credit_correction_terminal",
                    "only an original application may be reversed",
                )
            _one(
                session,
                scope=scope,
                model=plane.non_cash_effect,
                row_id=original.effect_id,
                lock=True,
            )
            document_id = original.document_id
            if document_id is None:
                raise BillingRuleViolation(
                    "non_cash_credit_evidence_corrupt", "application has no invoice"
                )
            _one(
                session,
                scope=scope,
                model=plane.document,
                row_id=document_id,
                lock=True,
            )
            restored = _verified_non_cash_sum(
                session, scope=scope, application_id=original.effect_id
            )
            cap = -original.availability_delta.amount - restored
            effect_kind, group_kind, sign = (
                "application_reversal",
                "non_cash_credit_application_reversed",
                1,
            )
            original_application_id = original.effect_id
        else:
            cap = available
            effect_kind, group_kind, sign = (
                "unused_reduction",
                "non_cash_credit_reduced",
                -1,
            )
        amount = command.amount.amount if command.amount else cap
        if command.amount:
            _require_same_money(grant.original_amount, command.amount)
        if amount <= 0 or amount > cap:
            raise BillingRuleViolation(
                "non_cash_credit_room_exceeded",
                "credit change exceeds available grant, invoice "
                "or unreversed application room",
            )
        effect = _non_cash_effect(
            command,
            grant_id=grant_id,
            account_id=grant.billing_account_id,
            kind=effect_kind,
            amount=_money(
                sign * amount,
                grant.original_amount.currency.code,
                grant.original_amount.currency.minor_units,
            ),
            document_id=document_id,
            original_application_id=original_application_id,
        )
        ar_effects = (
            ((EffectLane.RECEIVABLE, effect.availability_delta.amount),)
            if document_id is not None
            else ()
        )
        _post_group(
            session,
            scope=scope,
            account_id=grant.billing_account_id,
            kind=group_kind,
            currency=grant.original_amount.currency.code,
            minor_units=grant.original_amount.currency.minor_units,
            source_ref=command.source_ref,
            occurred_at=command.occurred_at,
            effects=ar_effects,
            exposure_document_ids=(document_id,) if document_id else (),
            non_cash_effects=(effect,),
            non_cash_request_digest=digest,
        )
        return {"effect_id": str(effect.effect_id)}

    outcome = _idempotent(
        db,
        scope=scope,
        operation_scope=f"billing.non_cash_credit.{operation_name}",
        key=command.idempotency_key,
        fingerprint=digest,
        operation=operation,
    )
    return get_non_cash_credit_effect(
        db, scope=scope, effect_id=UUID(str(outcome.result["effect_id"]))
    )


def apply_non_cash_credit(
    db: Session, *, scope: Scope, command: ApplyNonCashCredit
) -> NonCashCreditEffectV1:
    return _non_cash_change(db, scope=scope, command=command)


def reverse_non_cash_credit_application(
    db: Session, *, scope: Scope, command: ReverseNonCashCreditApplication
) -> NonCashCreditEffectV1:
    return _non_cash_change(db, scope=scope, command=command)


def reduce_non_cash_credit_grant(
    db: Session, *, scope: Scope, command: ReduceNonCashCreditGrant
) -> NonCashCreditEffectV1:
    return _non_cash_change(db, scope=scope, command=command)


def accept_withholding_discharge(
    db: Session, *, scope: Scope, command: AcceptWithholdingDischarge
) -> models.WithholdingDischarge | models.PlatformWithholdingDischarge:
    """Accept one invoice's evidenced deduction as AR discharge, never as cash.

    The trusted assembly must authorize finance acceptance. Actor and evidence
    references are immutable audit inputs; this API cannot attest permissions.
    """
    _require_withholding_command(scope=scope, command=command)
    plane = _models(scope)
    account = _one(
        db,
        scope=scope,
        model=plane.account,
        row_id=command.billing_account_id,
        lock=True,
    )
    document = _one(db, scope=scope, model=plane.document, row_id=command.document_id)
    if document.billing_account_id != account.id:
        raise BillingRuleViolation(
            "withholding_account_mismatch", "withholding invoice and account must agree"
        )
    _require_account_money(account, command.accepted_amount)
    _require_same_money(
        command.accepted_amount,
        _money(document.grand_total, document.currency, document.minor_units),
    )

    def operation(session: Session) -> dict[str, object]:
        _require_historical_source_unclaimed(
            session,
            scope=scope,
            operation="billing.withholding.accept",
            source_owner=command.source_owner,
            source_ref=command.source_ref,
        )
        _require_historical_source_unclaimed(
            session,
            scope=scope,
            operation="billing.withholding.tax_component",
            source_owner=command.tax_evidence.source_owner,
            source_ref=opening_withholding_component_identity(
                tax_source_owner=command.tax_evidence.source_owner,
                determination_ref=command.tax_evidence.determination_ref,
                component_ref=command.tax_evidence.component_ref,
            ),
            determination_ref=command.tax_evidence.determination_ref,
            component_ref=command.tax_evidence.component_ref,
        )
        duplicate = select(plane.withholding.id).where(
            plane.withholding.source_owner == command.source_owner,
            plane.withholding.source_ref == command.source_ref,
        )
        if (
            session.execute(_where_scope(duplicate, scope, plane.withholding)).first()
            is not None
        ):
            raise BillingConflict(
                "withholding_source_reused",
                "withholding source already has an immutable acceptance",
            )
        tax_duplicate = select(plane.withholding.id).where(
            plane.withholding.tax_source_owner == command.tax_evidence.source_owner,
            plane.withholding.determination_ref
            == command.tax_evidence.determination_ref,
            plane.withholding.component_ref == command.tax_evidence.component_ref,
            plane.withholding.reverses_discharge_id.is_(None),
        )
        if (
            session.execute(
                _where_scope(tax_duplicate, scope, plane.withholding)
            ).first()
            is not None
        ):
            raise BillingConflict(
                "withholding_component_reused",
                "Tax component already has an immutable acceptance",
            )
        invoice = _one(
            session,
            scope=scope,
            model=plane.document,
            row_id=command.document_id,
            lock=True,
        )
        _require_credit_target(
            session, scope=scope, document=invoice, account_id=account.id
        )
        _require_live_invoice_issuance(session, scope=scope, document=invoice)
        if command.accepted_amount.amount > _document_outstanding(
            session, scope=scope, document=invoice
        ):
            raise BillingRuleViolation(
                "withholding_exceeds_receivable",
                "accepted withholding exceeds the invoice's outstanding receivable",
            )
        if command.settlement_id is not None:
            settlement = _one(
                session,
                scope=scope,
                model=plane.settlement,
                row_id=command.settlement_id,
            )
            if settlement.billing_account_id != account.id:
                raise BillingRuleViolation(
                    "withholding_cash_link_invalid",
                    "actual cash belongs to another account",
                )
            _require_same_money(
                command.accepted_amount,
                _money(settlement.amount, settlement.currency, settlement.minor_units),
            )
            if not _settlement_has_active_backing(
                session, scope=scope, settlement=settlement
            ):
                raise BillingRuleViolation(
                    "withholding_cash_link_invalid",
                    "linked settlement has no active actual-cash backing",
                )
        if command.allocation_id is not None:
            allocation = _one(
                session,
                scope=scope,
                model=plane.allocation,
                row_id=command.allocation_id,
            )
            if (
                allocation.billing_account_id != account.id
                or allocation.document_id != invoice.id
                or allocation.settlement_id != command.settlement_id
                or allocation.amount_delta <= 0
            ):
                raise BillingRuleViolation(
                    "withholding_cash_link_invalid",
                    "cash allocation must bind the same invoice and actual settlement",
                )
        effect = WithholdingDischargeEffectV1(
            discharge_id=uuid4(),
            document_id=invoice.id,
            claim_ref=command.claim_ref,
            claimed_amount=command.claimed_amount,
            accepted_delta=command.accepted_amount,
            tax_evidence=command.tax_evidence,
            authorization_actor_ref=command.authorization_actor_ref,
            authorization_evidence_ref=command.authorization_evidence_ref,
            certificate_observation=command.certificate_observation,
            settlement_id=command.settlement_id,
            allocation_id=command.allocation_id,
        )
        _post_group(
            session,
            scope=scope,
            account_id=account.id,
            kind="withholding_accepted",
            currency=account.currency,
            minor_units=account.minor_units,
            source_ref=command.source_ref,
            occurred_at=command.occurred_at,
            effects=((EffectLane.RECEIVABLE, -command.accepted_amount.amount),),
            exposure_document_ids=(invoice.id,),
            withholding=effect,
            withholding_source_owner=command.source_owner,
        )
        return {"discharge_id": str(effect.discharge_id)}

    outcome = _idempotent(
        db,
        scope=scope,
        operation_scope="billing.withholding.accept",
        key=command.idempotency_key,
        fingerprint=fingerprint_of(asdict(command)),
        operation=operation,
    )
    return cast(
        models.WithholdingDischarge | models.PlatformWithholdingDischarge,
        _one(
            db,
            scope=scope,
            model=plane.withholding,
            row_id=UUID(str(outcome.result["discharge_id"])),
        ),
    )


def reverse_withholding_discharge(
    db: Session, *, scope: Scope, command: ReverseWithholdingDischarge
) -> models.WithholdingDischarge | models.PlatformWithholdingDischarge:
    """Append the exact inverse with original Tax/claim/cash provenance retained."""
    if not isinstance(scope, TenantScope | PlatformScope):
        raise TypeError("withholding requires an explicit scope")
    _require_scope_matches(routed=scope, declared=command.scope)
    if (
        any(
            not isinstance(text, str) or not text.strip() or len(text) > 255
            for text in (
                command.idempotency_key,
                command.source_ref,
                command.authorization_actor_ref,
                command.authorization_evidence_ref,
            )
        )
        or command.occurred_at.tzinfo is None
    ):
        raise BillingRuleViolation(
            "withholding_evidence_required",
            "correction requires source, audit identity and aware instant",
        )
    plane = _models(scope)
    original = _one(
        db, scope=scope, model=plane.withholding, row_id=command.discharge_id
    )
    _one(
        db,
        scope=scope,
        model=plane.account,
        row_id=original.billing_account_id,
        lock=True,
    )

    def operation(session: Session) -> dict[str, object]:
        if original.reverses_discharge_id is not None:
            raise BillingRuleViolation(
                "withholding_correction_terminal",
                "a withholding correction cannot itself be reversed",
            )
        existing = select(plane.withholding.id).where(
            plane.withholding.reverses_discharge_id == original.id
        )
        if (
            session.execute(_where_scope(existing, scope, plane.withholding)).first()
            is not None
        ):
            raise BillingRuleViolation(
                "already_reversed", "withholding discharge already has its correction"
            )
        _one(
            session,
            scope=scope,
            model=plane.document,
            row_id=original.document_id,
            lock=True,
        )
        frozen = get_withholding_discharge(
            session, scope=scope, discharge_id=original.id
        )
        inverse = replace(
            frozen,
            discharge_id=uuid4(),
            accepted_delta=_money(
                -original.amount_delta, original.currency, original.minor_units
            ),
            reverses_discharge_id=original.id,
            authorization_actor_ref=command.authorization_actor_ref,
            authorization_evidence_ref=command.authorization_evidence_ref,
        )
        _post_group(
            session,
            scope=scope,
            account_id=original.billing_account_id,
            kind="withholding_reversed",
            currency=original.currency,
            minor_units=original.minor_units,
            source_ref=command.source_ref,
            occurred_at=command.occurred_at,
            effects=((EffectLane.RECEIVABLE, original.amount_delta),),
            exposure_document_ids=(original.document_id,),
            reverses_group_id=original.posting_group_id,
            withholding=inverse,
            withholding_source_owner="dotmac-billing",
        )
        return {"discharge_id": str(inverse.discharge_id)}

    outcome = _idempotent(
        db,
        scope=scope,
        operation_scope="billing.withholding.reverse",
        key=command.idempotency_key,
        fingerprint=fingerprint_of(asdict(command)),
        operation=operation,
    )
    return cast(
        models.WithholdingDischarge | models.PlatformWithholdingDischarge,
        _one(
            db,
            scope=scope,
            model=plane.withholding,
            row_id=UUID(str(outcome.result["discharge_id"])),
        ),
    )


@_exact_cash_operation
def apply_available_credit(
    db: Session, *, scope: Scope, command: ApplyAvailableCreditCommand
) -> _PostingGroupRow:
    """Draw exact confirmed-cash room into one invoice, without creating cash.

    Extracted from Sub AccountCreditApplications.apply/_source_payments:
    account serialization, account/source room caps and deterministic min loop.
    """

    plane = _models(scope)
    account = _one(
        db,
        scope=scope,
        model=plane.account,
        row_id=command.billing_account_id,
        lock=True,
    )

    def operation(session: Session) -> dict[str, object]:
        _require_account_money(account, command.amount)
        if command.amount.amount <= 0:
            raise BillingRuleViolation(
                "credit_amount_nonpositive", "credit draw amount must be positive"
            )
        document = _one(
            session,
            scope=scope,
            model=plane.document,
            row_id=command.document_id,
            lock=True,
        )
        _require_credit_target(
            session, scope=scope, document=document, account_id=account.id
        )
        _require_same_money(
            command.amount,
            _money(document.grand_total, document.currency, document.minor_units),
        )
        position = rebuild_receivable_position(
            session,
            scope=scope,
            billing_account_id=account.id,
            currency=account.currency,
            minor_units=account.minor_units,
        )
        if command.amount.amount > position.available_credit:
            raise BillingRuleViolation(
                "credit_insufficient_backing",
                "insufficient cash-backed available credit",
            )
        if command.amount.amount > _document_outstanding(
            session, scope=scope, document=document
        ):
            raise BillingRuleViolation(
                "allocation_exceeds_receivable",
                "allocation exceeds document receivable",
            )
        statement = (
            select(plane.settlement)
            .where(
                plane.settlement.billing_account_id == account.id,
                plane.settlement.currency == command.amount.currency.code,
                plane.settlement.minor_units == command.amount.currency.minor_units,
                plane.settlement.funding_lane == EffectLane.AVAILABLE_CREDIT.value,
            )
            .order_by(plane.settlement.occurred_at, plane.settlement.id)
        )
        sources = session.execute(
            _where_scope(statement, scope, plane.settlement)
        ).scalars()
        remaining = command.amount.amount
        group_id = uuid4()
        digest = fingerprint_of(asdict(command))
        allocations: list[_AllocationInput] = []
        opening: list[OpeningCashEffectV1] = []
        source_order: list[CashCreditSourceOrderV1] = []
        for settlement in sources:
            if not _settlement_has_active_backing(
                session, scope=scope, settlement=settlement
            ):
                continue
            used = _settlement_used(session, scope=scope, settlement_id=settlement.id)
            if used < 0 or used > settlement.amount:
                raise BillingRuleViolation(
                    "settlement_room_ambiguous",
                    "settlement consumption exceeds its evidence",
                )
            settlement_room = settlement.amount - used
            amount = min(remaining, settlement_room)
            if amount > 0:
                allocations.append(
                    _AllocationInput(
                        settlement_id=settlement.id,
                        document_id=document.id,
                        effect_kind="allocation",
                        amount_delta=amount,
                        allocation_id=uuid4(),
                    )
                )
                source_order.append(
                    CashCreditSourceOrderV1(
                        len(source_order) + 1, allocations[-1].allocation_id, None
                    )
                )
                remaining -= amount
            if remaining == 0:
                break
        if remaining > 0:
            lot_ids = tuple(
                session.execute(
                    _where_scope(
                        select(plane.opening_lot.id)
                        .where(
                            plane.opening_lot.billing_account_id == account.id,
                            plane.opening_lot.currency == account.currency,
                            plane.opening_lot.minor_units == account.minor_units,
                        )
                        .order_by(
                            plane.opening_lot.created_at,
                            plane.opening_lot.receipt_id,
                            plane.opening_lot.sequence,
                            plane.opening_lot.id,
                        ),
                        scope,
                        plane.opening_lot,
                    )
                ).scalars()
            )
            for lot_id in lot_ids:
                room = get_opening_cash_lot_position(
                    session, scope=scope, lot_id=lot_id
                )
                if room.unwound:
                    continue
                quantity = min(
                    minor_units_of(
                        _money(remaining, account.currency, account.minor_units)
                    ),
                    minor_units_of(room.remaining),
                )
                if quantity == 0:
                    continue
                amount = money_from_minor(
                    quantity, account.currency, account.minor_units
                )
                effect = _opening_effect(
                    scope=scope,
                    lot=room.lot,
                    group_id=group_id,
                    sequence=len(opening) + 1,
                    kind="application",
                    amount=amount,
                    source_ref=command.source_ref,
                    request_digest=digest,
                    occurred_at=command.occurred_at,
                    authorization_ref="billing.finance.assembly",
                    document_id=document.id,
                )
                opening.append(effect)
                source_order.append(
                    CashCreditSourceOrderV1(
                        len(source_order) + 1, None, effect.effect_id
                    )
                )
                remaining -= amount.amount
                if remaining == 0:
                    break
        if remaining != 0:
            raise BillingRuleViolation(
                "credit_insufficient_backing",
                "insufficient cash-backed available credit",
            )
        group = _post_group(
            session,
            scope=scope,
            account_id=account.id,
            kind="opening_cash_applied" if opening else "allocation",
            currency=account.currency,
            minor_units=account.minor_units,
            source_ref=command.source_ref,
            occurred_at=command.occurred_at,
            effects=(
                (EffectLane.RECEIVABLE, -command.amount.amount),
                (EffectLane.AVAILABLE_CREDIT, -command.amount.amount),
            ),
            allocations=allocations,
            opening_group_id=group_id if opening else None,
            opening_effects=tuple(opening),
            cash_source_order=tuple(source_order) if opening else (),
        )
        return {"posting_group_id": str(group.id)}

    outcome = _idempotent(
        db,
        scope=scope,
        operation_scope="billing.credit.apply",
        key=command.source_ref,
        fingerprint=fingerprint_of(asdict(command)),
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
        if not _settlement_has_active_backing(
            session, scope=scope, settlement=settlement
        ):
            raise BillingRuleViolation(
                "settlement_reversed",
                "allocation requires active confirmed settlement backing",
            )
        _require_credit_target(
            session,
            scope=scope,
            document=document,
            account_id=settlement.billing_account_id,
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
        opening_allocation_group_id=original.posting_group_id,
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
        opening_document_id=source.id,
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
    opening_allocation_group_id: UUID | None = None,
    opening_document_id: UUID | None = None,
) -> _PostingGroupRow:
    plane = _models(scope)

    def operation(session: Session) -> dict[str, object]:
        _require_legacy_opening_guard(
            session,
            scope=scope,
            allocation_group_id=opening_allocation_group_id,
            document_id=opening_document_id,
        )
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


def _require_non_cash_reversal_owner(
    db: Session, *, scope: Scope, original: _PostingGroupRow
) -> None:
    _one(
        db,
        scope=scope,
        model=_models(scope).account,
        row_id=original.billing_account_id,
        lock=True,
    )
    if original.group_kind in (
        "non_cash_credit_granted",
        "non_cash_credit_applied",
        "non_cash_credit_application_reversed",
        "non_cash_credit_reduced",
    ):
        raise BillingRuleViolation(
            "non_cash_credit_owner_required",
            "use noncash application reversal or unused grant reduction",
        )
    if original.group_kind == "invoice_issued":
        plane = _models(scope)
        statement = select(plane.exposure_fact.document_id).where(
            plane.exposure_fact.posting_group_watermark == original.id,
            plane.exposure_fact.billing_account_id == original.billing_account_id,
        )
        for document_id in db.execute(
            _where_scope(statement, scope, plane.exposure_fact)
        ).scalars():
            _one(db, scope=scope, model=plane.document, row_id=document_id, lock=True)
            if (
                _document_non_cash_applied(db, scope=scope, document_id=document_id)
                != 0
            ):
                raise BillingRuleViolation(
                    "invoice_has_non_cash_credit",
                    "reverse credit applications before invoice issuance",
                )


@_exact_cash_operation
def _reverse_group(
    db: Session,
    *,
    scope: Scope,
    original: _PostingGroupRow,
    occurred_at: datetime,
    source_ref: str,
    exposure_document_ids: Iterable[UUID] = (),
) -> _PostingGroupRow:
    if original.group_kind in ("withholding_accepted", "withholding_reversed"):
        raise BillingRuleViolation(
            "withholding_owner_required", "use the withholding correction command"
        )
    _require_non_cash_reversal_owner(db, scope=scope, original=original)
    _require_opening_reversal_owner(db, scope=scope, original=original)
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
    _require_non_cash_reversal_owner(db, scope=scope, original=original_evidence)
    _require_opening_reversal_owner(db, scope=scope, original=original_evidence)
    offered = fingerprint_of(asdict(command))

    def operation(session: Session) -> dict[str, object]:
        original = _one(
            session,
            scope=scope,
            model=plane.group,
            row_id=command.posting_group_id,
        )
        if original.group_kind in ("withholding_accepted", "withholding_reversed"):
            raise BillingRuleViolation(
                "withholding_owner_required", "use the withholding correction command"
            )
        if original.group_kind == "invoice_issued":
            raise BillingRuleViolation(
                "invoice_correction_owner_required",
                "use void_document or the invoice correction owner "
                "for invoice issuance",
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


@_exact_cash_operation
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
    # Read persisted scalar values rather than identity-map entities. Newly
    # flushed ORM amounts can retain the input exponent while NUMERIC(20,6)
    # reads have six decimal places; mixing them changes the existing hash.
    statement = select(
        plane.effect.lane,
        plane.effect.amount_delta,
        plane.effect.currency,
        plane.effect.minor_units,
    ).where(
        plane.effect.billing_account_id == billing_account_id,
        plane.effect.currency == currency,
    )
    rows = tuple(db.execute(_where_scope(statement, scope, plane.effect)).all())
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
    "import_opening_account",
    "record_opening_cash_refund",
    "correct_opening_cash_refund",
    "reverse_cash_credit_draw",
    "reverse_opening_account_import",
    "grant_non_cash_credit",
    "apply_non_cash_credit",
    "reverse_non_cash_credit_application",
    "reduce_non_cash_credit_grant",
    "accept_withholding_discharge",
    "reverse_withholding_discharge",
    "accept_rated_obligation",
    "accept_settlement",
    "allocate_settlement",
    "apply_available_credit",
    "create_billing_account",
    "create_draft_document",
    "create_multi_line_draft_document",
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


def _persist_opening_records(
    db: Session,
    *,
    scope: Scope,
    group_id: UUID,
    receipt: OpeningImportReceiptV1 | OpeningImportReceiptV2 | None,
    lots: tuple[OpeningCashLotFactV1, ...],
    effects: tuple[OpeningCashEffectV1, ...],
) -> None:
    if receipt is None and not lots and not effects:
        return
    plane = _models(scope)
    if receipt is not None:
        if receipt.scope != scope or receipt.posting_group_id != group_id:
            raise BillingRuleViolation(
                "opening_evidence_integrity", "opening receipt group binding disagrees"
            )
        payload = _jsonable(receipt)
        db.add(
            plane.opening_receipt(
                **_values(
                    scope,
                    {
                        "id": receipt.receipt_id,
                        "receipt_kind": receipt.receipt_kind,
                        "original_receipt_id": receipt.original_receipt_id,
                        "billing_account_id": receipt.billing_account_id,
                        "posting_group_id": group_id,
                        "manifest_fingerprint": receipt.manifest_fingerprint,
                        "request_identity": receipt.request_identity,
                        "request_digest": receipt.request_digest,
                        "currency": receipt.total_residual.currency.code,
                        "minor_units": receipt.total_residual.currency.minor_units,
                        "total_residual": receipt.total_residual.amount,
                        "expected_target_fingerprint": (
                            receipt.target.expected_position_fingerprint
                        ),
                        "expected_target_watermark": receipt.target.posting_watermark,
                        "migration_authority_ref": (
                            receipt.verification.migration_authority_ref
                        ),
                        "recorded_at": receipt.recorded_at,
                        "evidence": payload,
                        "evidence_digest": fingerprint_of(payload),
                    },
                )
            )
        )
        db.flush()
    for lot in lots:
        if lot.scope != scope or lot.posting_group_id != group_id:
            raise BillingRuleViolation(
                "opening_evidence_integrity", "opening lot group binding disagrees"
            )
        source = lot.source
        currency, units = (
            lot.initial_amount.currency.code,
            lot.initial_amount.currency.minor_units,
        )
        payload = _jsonable(lot)
        db.add(
            plane.opening_lot(
                **_values(
                    scope,
                    {
                        "id": lot.lot_id,
                        "receipt_id": lot.receipt_id,
                        "billing_account_id": lot.billing_account_id,
                        "posting_group_id": group_id,
                        "sequence": lot.sequence,
                        "source_system": source.source_system,
                        "source_settlement_key": source.source_settlement_key,
                        "currency": currency,
                        "minor_units": units,
                        "gross_amount": money_from_minor(
                            source.gross_minor, currency, units
                        ).amount,
                        "historical_consumed": money_from_minor(
                            sum(item.amount_minor for item in source.consumptions),
                            currency,
                            units,
                        ).amount,
                        "residual_amount": lot.initial_amount.amount,
                        "recorded_at": lot.recorded_at,
                        "evidence": payload,
                        "evidence_digest": fingerprint_of(payload),
                    },
                )
            )
        )
    if lots:
        db.flush()
    for lot in sorted(
        lots,
        key=lambda item: (item.source.source_system, item.source.source_settlement_key),
    ):
        db.add(
            plane.cash_source_claim(
                **_values(
                    scope,
                    {
                        "id": uuid4(),
                        "settlement_id": None,
                        "opening_cash_lot_id": lot.lot_id,
                        "billing_account_id": lot.billing_account_id,
                        "source_system": lot.source.source_system,
                        "source_settlement_key": lot.source.source_settlement_key,
                        "currency": lot.initial_amount.currency.code,
                        "minor_units": lot.initial_amount.currency.minor_units,
                    },
                )
            )
        )
    if lots:
        db.flush()
    for effect in effects:
        if effect.scope != scope or effect.posting_group_id != group_id:
            raise BillingRuleViolation(
                "opening_evidence_integrity", "opening effect group binding disagrees"
            )
        payload = _jsonable(effect)
        db.add(
            plane.opening_effect(
                **_values(
                    scope,
                    {
                        "id": effect.effect_id,
                        "lot_id": effect.lot_id,
                        "billing_account_id": effect.billing_account_id,
                        "posting_group_id": group_id,
                        "sequence": effect.sequence,
                        "effect_kind": effect.effect_kind,
                        "amount_delta": effect.availability_delta.amount,
                        "currency": effect.amount.currency.code,
                        "minor_units": effect.amount.currency.minor_units,
                        "document_id": effect.document_id,
                        "original_effect_id": effect.original_effect_id,
                        "source_ref": effect.source_ref,
                        "authorization_ref": effect.authorization_ref,
                        "request_digest": effect.request_digest,
                        "recorded_at": effect.recorded_at,
                        "evidence": payload,
                        "evidence_digest": fingerprint_of(payload),
                    },
                )
            )
        )
    if effects:
        db.flush()


def _opening_document_applied(
    db: Session, *, scope: Scope, document_id: UUID
) -> Decimal:
    plane = _models(scope)
    ids = tuple(
        db.execute(
            _where_scope(
                select(plane.opening_effect.id).where(
                    plane.opening_effect.document_id == document_id
                ),
                scope,
                plane.opening_effect,
            )
        ).scalars()
    )
    values = tuple(
        get_opening_cash_effect(db, scope=scope, effect_id=value) for value in ids
    )
    if not values:
        return Decimal("0")
    amount = -sum(minor_units_of(item.availability_delta) for item in values)
    if amount < 0:
        raise BillingRuleViolation(
            "opening_room_integrity", "opening invoice consumption cannot be negative"
        )
    currency = values[0].amount.currency
    return money_from_minor(amount, currency.code, currency.minor_units).amount


def _require_opening_reversal_owner(
    db: Session, *, scope: Scope, original: _PostingGroupRow
) -> None:
    _require_mixed_settlement_chain_owner(db, scope=scope, original=original)
    plane = _models(scope)
    present = db.execute(
        _where_scope(
            select(plane.opening_effect.id).where(
                plane.opening_effect.posting_group_id == original.id
            ),
            scope,
            plane.opening_effect,
        )
    ).first()
    if present is not None or original.group_kind in {
        "opening_cash_imported",
        "opening_cash_applied",
        "cash_credit_draw_reversed",
        "opening_cash_refunded",
        "opening_cash_refund_corrected",
        "opening_cash_import_reversed",
        "opening_account_imported",
        "opening_account_import_reversed",
    }:
        raise BillingRuleViolation(
            "opening_cash_owner_required",
            "use the complete opening cash correction owner",
        )


def _opening_effect(
    *,
    scope: Scope,
    lot: OpeningCashLotFactV1,
    group_id: UUID,
    sequence: int,
    kind: Literal[
        "recognition",
        "application",
        "application_inverse",
        "refund",
        "refund_correction",
        "import_unwind",
    ],
    amount: Money,
    source_ref: str,
    request_digest: str,
    occurred_at: datetime,
    authorization_ref: str,
    document_id: UUID | None = None,
    original_effect_id: UUID | None = None,
    movement_evidence: OpeningCashMovementEvidenceV1 | None = None,
    movement_verification: OpeningCashRefundVerificationV1
    | OpeningCashRefundCorrectionVerificationV1
    | None = None,
) -> OpeningCashEffectV1:
    return OpeningCashEffectV1(
        scope,
        uuid4(),
        lot.lot_id,
        lot.billing_account_id,
        group_id,
        sequence,
        kind,
        amount,
        document_id,
        original_effect_id,
        source_ref,
        authorization_ref,
        movement_evidence,
        movement_verification,
        request_digest,
        occurred_at,
        datetime.now(UTC),
    )


def _qualified_import(
    command: ImportOpeningAccountV1,
    verifier: OpeningImportVerifier | None,
) -> OpeningImportVerificationV1:
    if verifier is None:
        raise BillingRuleViolation(
            "opening_verifier_unavailable", "trusted import qualification is required"
        )
    digest = opening_cash_command_digest(command)
    manifest_digest = opening_manifest_fingerprint(command.manifest)
    try:
        verified = verifier.verify_import(
            OpeningImportVerificationRequestV1(command, digest, manifest_digest)
        )
    except OpeningVerificationUnavailable as exc:
        raise BillingRuleViolation(
            "opening_verifier_unavailable", "import qualification is unavailable"
        ) from exc
    if (
        type(verified) is not OpeningImportVerificationV1
        or verified.operation != "billing.opening.import.v1"
        or verified.scope != command.scope
        or verified.command_digest != digest
        or verified.manifest_fingerprint != manifest_digest
        or verified.partition != command.partition
        or verified.target != command.target
        or verified.installation_id != command.manifest.capture.installation_id
        or verified.migration_authority_ref != command.migration_authority_ref
        or verified.source_authority_ref
        != command.manifest.capture.source_authority_ref
    ):
        raise BillingRuleViolation(
            "opening_authority_binding",
            "import qualification does not bind this exact command",
        )
    return verified


def _require_pristine_opening_target(
    db: Session,
    *,
    scope: Scope,
    command: ImportOpeningAccountV1 | ImportOpeningAccountV2,
) -> None:
    plane = _models(scope)
    actual = get_opening_cash_target(
        db, scope=scope, billing_account_id=command.partition.account_id
    )
    if actual != command.target:
        raise BillingConflict(
            "opening_target_changed",
            "opening target fingerprint or posting watermark changed",
        )
    for model in (
        plane.group,
        plane.settlement,
        plane.obligation,
        plane.document,
        plane.withholding,
        plane.non_cash_grant,
        plane.opening_receipt,
    ):
        if (
            db.execute(
                _where_scope(
                    select(model.id).where(
                        model.billing_account_id == command.partition.account_id
                    ),
                    scope,
                    model,
                )
            ).first()
            is not None
        ):
            raise BillingRuleViolation(
                "opening_target_not_pristine",
                "opening import requires a financially pristine account",
            )


@_exact_cash_operation
def import_opening_account(
    db: Session,
    *,
    scope: Scope,
    command: ImportOpeningAccountV1,
    verifier: OpeningImportVerifier | None = None,
) -> OpeningImportReceiptV1:
    if type(command) is not ImportOpeningAccountV1:
        raise BillingRuleViolation(
            "opening_command_invalid", "expected ImportOpeningAccountV1"
        )
    _require_scope_matches(routed=scope, declared=command.scope)
    if (
        command.manifest.scope != scope
        or command.target.scope != scope
        or command.target.account_id != command.partition.account_id
        or (command.target.currency, command.target.precision)
        != (command.partition.currency, command.partition.precision)
    ):
        raise BillingRuleViolation(
            "opening_scope_binding", "opening command scope and partition must agree"
        )
    verified = _qualified_import(command, verifier)
    # The trusted import operation above qualifies proof independently. Reuse
    # preview structural/cash checks without inventing a permissive preview port.
    preview = preview_opening_account_import(
        OpeningPreviewRequestV1(
            command.manifest,
            command.partition,
            command.target,
            command.migration_authority_ref,
        )
    )
    blockers = tuple(
        item for item in preview.blockers if item.code != "verifier_unavailable"
    )
    if blockers:
        raise BillingRuleViolation(
            "opening_manifest_blocked",
            "opening manifest has counted blockers",
            blockers=tuple((item.code, item.count) for item in blockers),
        )
    for control in command.manifest.controls:
        for field in fields(control):
            if (
                field.name not in {"partition", "available_cash_credit_minor"}
                and getattr(control, field.name) != 0
            ):
                raise BillingRuleViolation(
                    "opening_lane_unsupported",
                    "complete opening manifest contains unsupported lanes",
                )
    sources = tuple(
        item
        for item in command.manifest.cash_lots
        if item.partition == command.partition
    )
    total = sum(item.residual_minor for item in sources)
    if total <= 0:
        raise BillingRuleViolation(
            "opening_cash_nonpositive",
            "selected opening cash partition must be positive",
        )
    amount = money_from_minor(
        total, command.partition.currency, command.partition.precision
    )
    for item in sources:
        money_from_minor(
            item.gross_minor, command.partition.currency, command.partition.precision
        )
        money_from_minor(
            sum(effect.amount_minor for effect in item.consumptions),
            command.partition.currency,
            command.partition.precision,
        )
    plane = _models(scope)
    account = _one(
        db,
        scope=scope,
        model=plane.account,
        row_id=command.partition.account_id,
        lock=True,
    )
    _require_account_money(account, amount)
    digest = opening_cash_command_digest(command)

    def operation(session: Session) -> dict[str, object]:
        _require_pristine_opening_target(session, scope=scope, command=command)
        for source in sources:
            existing = session.execute(
                _where_scope(
                    select(plane.cash_source_claim.id).where(
                        plane.cash_source_claim.source_system == source.source_system,
                        plane.cash_source_claim.source_settlement_key
                        == source.source_settlement_key,
                    ),
                    scope,
                    plane.cash_source_claim,
                )
            ).first()
            if existing is not None:
                raise BillingConflict(
                    "cash_source_claimed",
                    "opening cash source identity is permanently claimed",
                )
        receipt_id, group_id = uuid4(), uuid4()
        recorded_at = datetime.now(UTC)
        lots = tuple(
            OpeningCashLotFactV1(
                scope,
                uuid4(),
                receipt_id,
                account.id,
                group_id,
                index,
                source,
                money_from_minor(
                    source.residual_minor, account.currency, account.minor_units
                ),
                recorded_at,
            )
            for index, source in enumerate(sources, 1)
        )
        receipt = OpeningImportReceiptV1(
            scope,
            receipt_id,
            "import",
            account.id,
            command.manifest,
            verified.manifest_fingerprint,
            command.partition,
            command.target,
            verified,
            amount,
            tuple(lot.lot_id for lot in lots),
            group_id,
            None,
            command.idempotency_key,
            digest,
            command.occurred_at,
            recorded_at,
        )
        opening = tuple(
            _opening_effect(
                scope=scope,
                lot=lot,
                group_id=group_id,
                sequence=index,
                kind="recognition",
                amount=lot.initial_amount,
                source_ref=command.idempotency_key,
                request_digest=digest,
                occurred_at=command.occurred_at,
                authorization_ref=command.migration_authority_ref,
            )
            for index, lot in enumerate(lots, 1)
        )
        _post_group(
            session,
            scope=scope,
            account_id=account.id,
            kind="opening_cash_imported",
            currency=account.currency,
            minor_units=account.minor_units,
            source_ref=command.idempotency_key,
            occurred_at=command.occurred_at,
            effects=((EffectLane.AVAILABLE_CREDIT, amount.amount),),
            opening_group_id=group_id,
            opening_receipt=receipt,
            opening_lots=lots,
            opening_effects=opening,
        )
        return {"receipt_id": str(receipt_id), "posting_group_id": str(group_id)}

    outcome = _idempotent_cash_claim(
        db,
        scope=scope,
        operation_scope="billing.opening.import.v1",
        key=command.idempotency_key,
        fingerprint=digest,
        operation=operation,
    )
    return get_opening_import_receipt(
        db, scope=scope, receipt_id=UUID(str(outcome.result["receipt_id"]))
    )


def _refund_subject(lot: OpeningCashLotFactV1) -> OpeningCashRefundSubjectV1:
    return OpeningCashRefundSubjectV1(
        lot.scope,
        lot.billing_account_id,
        lot.initial_amount.currency.code,
        lot.initial_amount.currency.minor_units,
        lot.lot_id,
        fingerprint_of(_jsonable(lot)),
        lot.source.source_system,
        lot.source.source_settlement_key,
    )


@_exact_cash_operation
def record_opening_cash_refund(
    db: Session,
    *,
    scope: Scope,
    command: RecordOpeningCashRefundV1,
    verifier: OpeningCashRefundVerifier | None = None,
) -> OpeningCashEffectV1:
    if type(command) is not RecordOpeningCashRefundV1:
        raise BillingRuleViolation(
            "opening_command_invalid", "expected RecordOpeningCashRefundV1"
        )
    _require_scope_matches(routed=scope, declared=command.scope)
    lot = get_opening_cash_lot(db, scope=scope, lot_id=command.lot_id)
    _require_same_money(command.amount, lot.initial_amount)
    if verifier is None:
        raise BillingRuleViolation(
            "opening_verifier_unavailable", "refund outcome qualification is required"
        )
    digest = opening_cash_command_digest(command)
    subject = _refund_subject(lot)
    try:
        verified = verifier.verify_refund(
            OpeningCashRefundVerificationRequestV1(command, digest, subject)
        )
    except OpeningVerificationUnavailable as exc:
        raise BillingRuleViolation(
            "opening_verifier_unavailable", "refund qualification is unavailable"
        ) from exc
    if (
        type(verified) is not OpeningCashRefundVerificationV1
        or verified.operation != "billing.opening.cash.refund.v1"
        or verified.command_digest != digest
        or verified.subject != subject
    ):
        raise BillingRuleViolation(
            "opening_authority_binding",
            "refund qualification does not bind this exact outcome",
        )
    plane = _models(scope)
    _one(db, scope=scope, model=plane.account, row_id=lot.billing_account_id, lock=True)

    def operation(session: Session) -> dict[str, object]:
        position = get_opening_cash_lot_position(
            session, scope=scope, lot_id=lot.lot_id
        )
        if position.unwound or minor_units_of(command.amount) > minor_units_of(
            position.remaining
        ):
            raise BillingRuleViolation(
                "opening_refund_exceeds_room",
                "refund exceeds unused active opening cash",
            )
        group_id = uuid4()
        effect = _opening_effect(
            scope=scope,
            lot=lot,
            group_id=group_id,
            sequence=1,
            kind="refund",
            amount=command.amount,
            source_ref=command.idempotency_key,
            request_digest=digest,
            occurred_at=command.occurred_at,
            authorization_ref=command.authorization_ref,
            movement_evidence=command.evidence,
            movement_verification=verified,
        )
        _post_group(
            session,
            scope=scope,
            account_id=lot.billing_account_id,
            kind="opening_cash_refunded",
            currency=command.amount.currency.code,
            minor_units=command.amount.currency.minor_units,
            source_ref=command.idempotency_key,
            occurred_at=command.occurred_at,
            effects=((EffectLane.AVAILABLE_CREDIT, -command.amount.amount),),
            opening_group_id=group_id,
            opening_effects=(effect,),
        )
        return {"effect_id": str(effect.effect_id), "posting_group_id": str(group_id)}

    outcome = _idempotent(
        db,
        scope=scope,
        operation_scope="billing.opening.cash.refund.v1",
        key=command.idempotency_key,
        fingerprint=digest,
        operation=operation,
    )
    return get_opening_cash_effect(
        db, scope=scope, effect_id=UUID(str(outcome.result["effect_id"]))
    )


@_exact_cash_operation
def correct_opening_cash_refund(
    db: Session,
    *,
    scope: Scope,
    command: CorrectOpeningCashRefundV1,
    verifier: OpeningCashRefundVerifier | None = None,
) -> OpeningCashEffectV1:
    if type(command) is not CorrectOpeningCashRefundV1:
        raise BillingRuleViolation(
            "opening_command_invalid", "expected CorrectOpeningCashRefundV1"
        )
    _require_scope_matches(routed=scope, declared=command.scope)
    original = get_opening_cash_effect(
        db, scope=scope, effect_id=command.refund_effect_id
    )
    if original.effect_kind != "refund" or original.amount != command.amount:
        raise BillingRuleViolation(
            "opening_refund_correction_invalid",
            "only the full exact named refund can be corrected",
        )
    lot = get_opening_cash_lot(db, scope=scope, lot_id=original.lot_id)
    digest = opening_cash_command_digest(command)
    subject = _refund_subject(lot)
    original_digest = fingerprint_of(_jsonable(original))
    if verifier is None:
        raise BillingRuleViolation(
            "opening_verifier_unavailable",
            "independent refund correction proof is required",
        )
    try:
        verified = verifier.verify_refund_correction(
            OpeningCashRefundCorrectionVerificationRequestV1(
                command, digest, subject, original.effect_id, original_digest
            )
        )
    except OpeningVerificationUnavailable as exc:
        raise BillingRuleViolation(
            "opening_verifier_unavailable",
            "refund correction qualification is unavailable",
        ) from exc
    if (
        type(verified) is not OpeningCashRefundCorrectionVerificationV1
        or verified.operation != "billing.opening.cash.refund.correct.v1"
        or verified.command_digest != digest
        or verified.subject != subject
        or verified.refund_effect_id != original.effect_id
        or verified.refund_evidence_digest != original_digest
        or original.movement_evidence is None
        or command.evidence.digest == original.movement_evidence.digest
        or type(original.movement_verification) is not OpeningCashRefundVerificationV1
        or verified.correction_outcome_proof_ref
        == original.movement_verification.outcome_proof_ref
    ):
        raise BillingRuleViolation(
            "opening_authority_binding",
            "new independent correction proof must bind the original refund",
        )
    plane = _models(scope)
    _one(db, scope=scope, model=plane.account, row_id=lot.billing_account_id, lock=True)

    def operation(session: Session) -> dict[str, object]:
        position = get_opening_cash_lot_position(
            session, scope=scope, lot_id=lot.lot_id
        )
        if (
            position.unwound
            or session.execute(
                _where_scope(
                    select(plane.opening_effect.id).where(
                        plane.opening_effect.original_effect_id == original.effect_id,
                        plane.opening_effect.effect_kind == "refund_correction",
                    ),
                    scope,
                    plane.opening_effect,
                )
            ).first()
            is not None
        ):
            raise BillingConflict(
                "opening_refund_already_corrected",
                "refund correction is full and one-time",
            )
        group_id = uuid4()
        effect = _opening_effect(
            scope=scope,
            lot=lot,
            group_id=group_id,
            sequence=1,
            kind="refund_correction",
            amount=command.amount,
            source_ref=command.idempotency_key,
            request_digest=digest,
            occurred_at=command.occurred_at,
            authorization_ref=command.authorization_ref,
            original_effect_id=original.effect_id,
            movement_evidence=command.evidence,
            movement_verification=verified,
        )
        _post_group(
            session,
            scope=scope,
            account_id=lot.billing_account_id,
            kind="opening_cash_refund_corrected",
            currency=command.amount.currency.code,
            minor_units=command.amount.currency.minor_units,
            source_ref=command.idempotency_key,
            occurred_at=command.occurred_at,
            effects=((EffectLane.AVAILABLE_CREDIT, command.amount.amount),),
            opening_group_id=group_id,
            opening_effects=(effect,),
            corrects_posting_group_id=original.posting_group_id,
        )
        return {"effect_id": str(effect.effect_id), "posting_group_id": str(group_id)}

    outcome = _idempotent(
        db,
        scope=scope,
        operation_scope="billing.opening.cash.refund.correct.v1",
        key=command.idempotency_key,
        fingerprint=digest,
        operation=operation,
    )
    return get_opening_cash_effect(
        db, scope=scope, effect_id=UUID(str(outcome.result["effect_id"]))
    )


def _cash_draw_remaining(
    db: Session,
    *,
    scope: Scope,
    fact: AccountingFactV4,
) -> tuple[dict[UUID, int], dict[UUID, int]]:
    plane = _models(scope)
    ordinary: dict[UUID, int] = {}
    opening: dict[UUID, int] = {}
    for item in fact.settlement_allocations:
        allocation = item.allocation
        if allocation.effect_kind != "allocation" or allocation.document_id is None:
            raise BillingRuleViolation(
                "opening_draw_integrity",
                "cash draw must bind positive original allocations",
            )
        offsets = Decimal(
            db.execute(
                _where_scope(
                    select(
                        func.coalesce(func.sum(plane.allocation.amount_delta), 0)
                    ).where(
                        plane.allocation.offsets_allocation_id
                        == item.allocation_effect_id
                    ),
                    scope,
                    plane.allocation,
                )
            ).scalar_one()
        )
        original = minor_units_of(allocation.amount_delta)
        room = original + minor_units_of(
            _money(
                offsets,
                allocation.amount_delta.currency.code,
                allocation.amount_delta.currency.minor_units,
            )
        )
        if original <= 0 or not 0 <= room <= original:
            raise BillingRuleViolation(
                "opening_draw_integrity",
                "ordinary inverse exceeds its concrete original",
            )
        ordinary[item.allocation_effect_id] = room
    for effect in fact.opening_cash_effects:
        if effect.effect_kind != "application" or effect.document_id is None:
            raise BillingRuleViolation(
                "opening_draw_integrity",
                "cash draw must bind original opening applications",
            )
        restored = Decimal(
            db.execute(
                _where_scope(
                    select(
                        func.coalesce(func.sum(plane.opening_effect.amount_delta), 0)
                    ).where(
                        plane.opening_effect.original_effect_id == effect.effect_id,
                        plane.opening_effect.effect_kind == "application_inverse",
                    ),
                    scope,
                    plane.opening_effect,
                )
            ).scalar_one()
        )
        original = minor_units_of(effect.amount)
        room = original - minor_units_of(
            _money(
                restored,
                effect.amount.currency.code,
                effect.amount.currency.minor_units,
            )
        )
        if not 0 <= room <= original:
            raise BillingRuleViolation(
                "opening_draw_integrity",
                "opening inverse exceeds its concrete original",
            )
        opening[effect.effect_id] = room
    return ordinary, opening


@_exact_cash_operation
def reverse_cash_credit_draw(
    db: Session,
    *,
    scope: Scope,
    command: ReverseCashCreditDrawV1,
) -> _PostingGroupRow:
    """Finance assembly authorization remains the ordinary Billing perimeter.

    authorization_ref retains its audit binding; it is not itself a permission.
    This command restores credit/AR and never attests external cash receipt.
    """
    if type(command) is not ReverseCashCreditDrawV1:
        raise BillingRuleViolation(
            "opening_command_invalid", "expected ReverseCashCreditDrawV1"
        )
    _require_scope_matches(routed=scope, declared=command.scope)
    fact = get_accounting_fact_v4(
        db, scope=scope, posting_group_id=command.draw_posting_group_id
    )
    if fact.effect_kind != "opening_cash_applied":
        raise BillingRuleViolation(
            "opening_draw_invalid",
            "inverse requires an original opening-bearing cash draw",
        )
    plane = _models(scope)
    account = _one(
        db, scope=scope, model=plane.account, row_id=fact.billing_account_id, lock=True
    )
    _require_account_money(account, command.amount)
    digest = opening_cash_command_digest(command)

    def operation(session: Session) -> dict[str, object]:
        ordinary_room, opening_room = _cash_draw_remaining(
            session, scope=scope, fact=fact
        )
        remaining = minor_units_of(command.amount)
        if remaining > sum(ordinary_room.values()) + sum(opening_room.values()):
            raise BillingRuleViolation(
                "opening_draw_inverse_exceeds_room",
                "inverse exceeds remaining original draw",
            )
        group_id = uuid4()
        ordinary_by_id = {
            item.allocation_effect_id: item.allocation
            for item in fact.settlement_allocations
        }
        opening_by_id = {item.effect_id: item for item in fact.opening_cash_effects}
        allocations: list[_AllocationInput] = []
        opening: list[OpeningCashEffectV1] = []
        order: list[CashCreditSourceOrderV1] = []
        for source in fact.cash_source_order:
            if remaining == 0:
                break
            if source.allocation_effect_id is not None:
                original_id = source.allocation_effect_id
                original = ordinary_by_id[original_id]
                quantity = min(remaining, ordinary_room[original_id])
                if quantity == 0:
                    continue
                allocation_id = uuid4()
                amount = money_from_minor(
                    -quantity, account.currency, account.minor_units
                )
                allocations.append(
                    _AllocationInput(
                        original.settlement_id,
                        original.document_id,
                        "reversal",
                        amount.amount,
                        original_id,
                        allocation_id,
                    )
                )
                order.append(
                    CashCreditSourceOrderV1(len(order) + 1, allocation_id, None)
                )
            else:
                original_id = cast(UUID, source.opening_effect_id)
                original_opening = opening_by_id[original_id]
                quantity = min(remaining, opening_room[original_id])
                if quantity == 0:
                    continue
                lot = get_opening_cash_lot(
                    session, scope=scope, lot_id=original_opening.lot_id
                )
                if get_opening_cash_lot_position(
                    session, scope=scope, lot_id=lot.lot_id
                ).unwound:
                    raise BillingRuleViolation(
                        "opening_import_unwound",
                        "cannot restore an unwound opening principal",
                    )
                effect = _opening_effect(
                    scope=scope,
                    lot=lot,
                    group_id=group_id,
                    sequence=len(opening) + 1,
                    kind="application_inverse",
                    amount=money_from_minor(
                        quantity, account.currency, account.minor_units
                    ),
                    source_ref=command.idempotency_key,
                    request_digest=digest,
                    occurred_at=command.occurred_at,
                    authorization_ref=command.authorization_ref,
                    document_id=original_opening.document_id,
                    original_effect_id=original_id,
                )
                opening.append(effect)
                order.append(
                    CashCreditSourceOrderV1(len(order) + 1, None, effect.effect_id)
                )
            remaining -= quantity
        if remaining != 0:
            raise BillingRuleViolation(
                "opening_draw_integrity",
                "draw source order does not cover its exact room",
            )
        group = _post_group(
            session,
            scope=scope,
            account_id=account.id,
            kind="cash_credit_draw_reversed",
            currency=account.currency,
            minor_units=account.minor_units,
            source_ref=command.idempotency_key,
            occurred_at=command.occurred_at,
            effects=(
                (EffectLane.RECEIVABLE, command.amount.amount),
                (EffectLane.AVAILABLE_CREDIT, command.amount.amount),
            ),
            allocations=allocations,
            opening_group_id=group_id,
            opening_effects=tuple(opening),
            cash_source_order=tuple(order),
            corrects_posting_group_id=fact.posting_group_id,
        )
        return {"posting_group_id": str(group.id)}

    outcome = _idempotent(
        db,
        scope=scope,
        operation_scope="billing.cash.credit.draw.reverse.v1",
        key=command.idempotency_key,
        fingerprint=digest,
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


@_exact_cash_operation
def reverse_opening_account_import(
    db: Session,
    *,
    scope: Scope,
    command: ReverseOpeningAccountImportV1,
    verifier: OpeningImportVerifier | None = None,
) -> OpeningImportReceiptV1:
    if type(command) is not ReverseOpeningAccountImportV1:
        raise BillingRuleViolation(
            "opening_command_invalid", "expected ReverseOpeningAccountImportV1"
        )
    _require_scope_matches(routed=scope, declared=command.scope)
    original = get_opening_import_receipt(
        db, scope=scope, receipt_id=command.receipt_id
    )
    if isinstance(original, OpeningImportReceiptV2):
        raise BillingRuleViolation(
            "opening_account_owner_required", "use the complete V2 account unwind owner"
        )
    if original.receipt_kind != "import":
        raise BillingRuleViolation(
            "opening_import_reverse_invalid",
            "only an original opening import can unwind",
        )
    digest = opening_cash_command_digest(command)
    original_digest = fingerprint_of(_jsonable(original))
    if verifier is None:
        raise BillingRuleViolation(
            "opening_verifier_unavailable",
            "fresh migration authority is required for unwind",
        )
    request = OpeningImportReversalVerificationRequestV1(
        command,
        digest,
        original_digest,
        original.posting_group_id,
        original.billing_account_id,
        original.total_residual.currency.code,
        original.total_residual.currency.minor_units,
    )
    try:
        verified = verifier.verify_import_reversal(request)
    except OpeningVerificationUnavailable as exc:
        raise BillingRuleViolation(
            "opening_verifier_unavailable", "import unwind qualification is unavailable"
        ) from exc
    if (
        type(verified) is not OpeningImportReversalVerificationV1
        or verified.operation != "billing.opening.import.reverse.v1"
        or verified.scope != scope
        or verified.command_digest != digest
        or verified.receipt_id != original.receipt_id
        or verified.receipt_evidence_digest != original_digest
        or verified.original_posting_group_id != original.posting_group_id
        or verified.billing_account_id != original.billing_account_id
        or (verified.currency, verified.precision)
        != (
            original.total_residual.currency.code,
            original.total_residual.currency.minor_units,
        )
        or verified.migration_authority_ref != command.migration_authority_ref
    ):
        raise BillingRuleViolation(
            "opening_authority_binding",
            "unwind qualification does not bind the exact original import",
        )
    plane = _models(scope)
    account = _one(
        db,
        scope=scope,
        model=plane.account,
        row_id=original.billing_account_id,
        lock=True,
    )

    def operation(session: Session) -> dict[str, object]:
        if (
            session.execute(
                _where_scope(
                    select(plane.opening_receipt.id).where(
                        plane.opening_receipt.original_receipt_id == original.receipt_id
                    ),
                    scope,
                    plane.opening_receipt,
                )
            ).first()
            is not None
        ):
            raise BillingConflict(
                "opening_import_already_unwound",
                "opening principal has already been unwound",
            )
        lots = tuple(
            get_opening_cash_lot(session, scope=scope, lot_id=value)
            for value in original.lot_ids
        )
        for lot in lots:
            room = get_opening_cash_lot_position(
                session, scope=scope, lot_id=lot.lot_id
            )
            if room.unwound or room.remaining != lot.initial_amount:
                raise BillingRuleViolation(
                    "opening_import_has_dependents",
                    "reverse all net opening uses and refunds before unwind",
                )
        receipt_id, group_id = uuid4(), uuid4()
        receipt = OpeningImportReceiptV1(
            scope,
            receipt_id,
            "unwind",
            account.id,
            original.manifest,
            original.manifest_fingerprint,
            original.partition,
            original.target,
            verified,
            original.total_residual,
            original.lot_ids,
            group_id,
            original.receipt_id,
            command.idempotency_key,
            digest,
            command.occurred_at,
            datetime.now(UTC),
        )
        # Zero-residual sources retain their claims and provenance; no invalid
        # zero-quantity unwind effect is invented for them.
        opening = tuple(
            _opening_effect(
                scope=scope,
                lot=lot,
                group_id=group_id,
                sequence=index,
                kind="import_unwind",
                amount=lot.initial_amount,
                source_ref=command.idempotency_key,
                request_digest=digest,
                occurred_at=command.occurred_at,
                authorization_ref=command.migration_authority_ref,
            )
            for index, lot in enumerate(
                (item for item in lots if minor_units_of(item.initial_amount) > 0), 1
            )
        )
        _post_group(
            session,
            scope=scope,
            account_id=account.id,
            kind="opening_cash_import_reversed",
            currency=account.currency,
            minor_units=account.minor_units,
            source_ref=command.idempotency_key,
            occurred_at=command.occurred_at,
            effects=((EffectLane.AVAILABLE_CREDIT, -original.total_residual.amount),),
            opening_group_id=group_id,
            opening_receipt=receipt,
            opening_effects=opening,
            corrects_posting_group_id=original.posting_group_id,
        )
        return {"receipt_id": str(receipt_id), "posting_group_id": str(group_id)}

    outcome = _idempotent(
        db,
        scope=scope,
        operation_scope="billing.opening.import.reverse.v1",
        key=command.idempotency_key,
        fingerprint=digest,
        operation=operation,
    )
    return get_opening_import_receipt(
        db, scope=scope, receipt_id=UUID(str(outcome.result["receipt_id"]))
    )


def _require_legacy_opening_guard(
    db: Session,
    *,
    scope: Scope,
    allocation_group_id: UUID | None = None,
    document_id: UUID | None = None,
) -> None:
    plane = _models(scope)
    if allocation_group_id is not None:
        original = _one(db, scope=scope, model=plane.group, row_id=allocation_group_id)
        _require_opening_reversal_owner(db, scope=scope, original=original)
    if document_id is not None:
        groups = tuple(
            db.execute(
                _where_scope(
                    select(plane.opening_effect.posting_group_id).where(
                        plane.opening_effect.document_id == document_id,
                        plane.opening_effect.effect_kind == "application",
                    ),
                    scope,
                    plane.opening_effect,
                )
            ).scalars()
        )
        for group_id in set(groups):
            fact = get_accounting_fact_v4(db, scope=scope, posting_group_id=group_id)
            ordinary, opening = _cash_draw_remaining(db, scope=scope, fact=fact)
            if sum(ordinary.values()) + sum(opening.values()) > 0:
                raise BillingRuleViolation(
                    "opening_cash_owner_required",
                    "reverse the complete mixed draw before legacy reallocation",
                )


def _idempotent_cash_claim(
    db: Session,
    *,
    scope: Scope,
    operation_scope: str,
    key: str,
    fingerprint: str,
    operation: Operation,
) -> IdempotentOutcome:
    try:
        return _idempotent(
            db,
            scope=scope,
            operation_scope=operation_scope,
            key=key,
            fingerprint=fingerprint,
            operation=operation,
        )
    except IntegrityError as exc:
        diagnostic = getattr(exc.orig, "diag", None)
        expected = (
            "uq_cash_claim_source"
            if isinstance(scope, TenantScope)
            else "uq_platform_cash_claim_source"
        )
        if (
            getattr(exc.orig, "sqlstate", None) == "23505"
            and getattr(diagnostic, "constraint_name", None) == expected
        ):
            raise BillingConflict(
                "cash_source_claimed", "cash source identity is permanently claimed"
            ) from exc
        raise


def _require_mixed_settlement_chain_owner(
    db: Session,
    *,
    scope: Scope,
    original: _PostingGroupRow,
) -> None:
    plane = _models(scope)
    root = original
    visited: set[UUID] = set()
    while root.reverses_group_id is not None:
        if root.id in visited:
            raise BillingRuleViolation(
                "settlement_backing_ambiguous", "posting reversal lineage is ambiguous"
            )
        visited.add(root.id)
        root = _one(db, scope=scope, model=plane.group, row_id=root.reverses_group_id)
    if root.group_kind != "settlement_accepted":
        return
    prefix = "settlement:"
    try:
        settlement_id = UUID(root.source_ref.removeprefix(prefix))
    except (ValueError, AttributeError) as exc:
        raise BillingRuleViolation(
            "settlement_backing_ambiguous", "settlement root reference is malformed"
        ) from exc
    settlement = db.execute(
        _where_scope(
            select(plane.settlement).where(plane.settlement.id == settlement_id),
            scope,
            plane.settlement,
        )
    ).scalar_one_or_none()
    if (
        settlement is None
        or root.source_ref != f"settlement:{settlement_id}"
        or settlement.billing_account_id != root.billing_account_id
        or settlement.currency != root.currency
        or settlement.minor_units != root.minor_units
    ):
        raise BillingRuleViolation(
            "settlement_backing_ambiguous",
            "settlement root has no exact actual receipt",
        )
    rows = db.execute(
        _where_scope(
            select(plane.allocation.id, plane.allocation.amount_delta)
            .join(plane.group, plane.group.id == plane.allocation.posting_group_id)
            .where(
                plane.allocation.settlement_id == settlement_id,
                plane.group.group_kind == "opening_cash_applied",
            ),
            scope,
            plane.allocation,
        )
    ).all()
    for row in rows:
        offsets = Decimal(
            db.execute(
                _where_scope(
                    select(
                        func.coalesce(func.sum(plane.allocation.amount_delta), 0)
                    ).where(plane.allocation.offsets_allocation_id == row.id),
                    scope,
                    plane.allocation,
                )
            ).scalar_one()
        )
        if row.amount_delta != offsets.copy_negate():
            raise BillingRuleViolation(
                "opening_cash_owner_required",
                "reverse complete mixed draws before correcting "
                "their settlement backing",
            )


def _receivable_model(scope: Scope, name: str) -> Any:
    return getattr(
        models, ("Platform" if isinstance(scope, PlatformScope) else "") + name
    )


def _persist_opening_receivables(
    db: Session,
    *,
    scope: Scope,
    bases: tuple[OpeningReceivableBasisV1, ...],
) -> None:
    model = _receivable_model(scope, "OpeningReceivableBasis")
    claims = _receivable_model(scope, "OpeningHistoricalDischargeClaim")
    seen: set[tuple[str, str, str, str]] = set()
    for basis in bases:
        payload = _jsonable(basis)
        db.add(
            model(
                **_values(
                    scope,
                    {
                        "id": basis.basis_id,
                        "receipt_id": basis.receipt_id,
                        "document_id": basis.document_id,
                        "billing_account_id": basis.billing_account_id,
                        "posting_group_id": basis.posting_group_id,
                        "source_version": basis.source_version,
                        "sequence": basis.sequence,
                        "installation_id": basis.installation_id,
                        "source_system": basis.source_system,
                        "source_invoice_key": basis.source_invoice_key,
                        "currency": basis.initial_receivable.currency.code,
                        "minor_units": basis.initial_receivable.currency.minor_units,
                        "initial_receivable": basis.initial_receivable.amount,
                        "evidence": payload,
                        "evidence_digest": fingerprint_of(payload),
                        "recorded_at": basis.recorded_at,
                    },
                )
            )
        )
    if bases:
        db.flush()
    for basis in bases:
        for discharge in basis.source.historical_discharges:
            for binding in discharge.replay_bindings:
                key = _opening_replay_identity(binding)
                if key in seen:
                    continue
                seen.add(key)
                _require_historical_source_unclaimed(
                    db,
                    scope=scope,
                    operation=binding.operation,
                    source_owner=binding.source_owner,
                    source_ref=binding.source_ref,
                    determination_ref=binding.determination_ref,
                    component_ref=binding.component_ref,
                )
                db.add(
                    claims(
                        **_values(
                            scope,
                            {
                                "id": uuid4(),
                                "basis_id": basis.basis_id,
                                "billing_account_id": basis.billing_account_id,
                                "operation": binding.operation,
                                "source_owner": binding.source_owner,
                                "source_ref": binding.source_ref,
                                "evidence_digest": binding.evidence_digest,
                                "determination_ref": binding.determination_ref,
                                "component_ref": binding.component_ref,
                            },
                        )
                    )
                )
    if bases:
        db.flush()


def _require_historical_source_unclaimed(
    db: Session,
    *,
    scope: Scope,
    operation: str,
    source_owner: str,
    source_ref: str,
    determination_ref: str | None = None,
    component_ref: str | None = None,
) -> None:
    # A friendly early refusal only. The shared immediate physical unique index
    # remains the serialization authority even under an older RR snapshot.
    namespace = {
        "billing.withholding.accept": "withholding.source",
        "billing.withholding.tax_component": "withholding.tax_component",
        "billing.non_cash_credit.grant": "non_cash.grant",
        "billing.non_cash_credit.apply": "non_cash.effect",
        "billing.credit_note.issue": "credit_note.issue",
    }[operation]
    if operation == "billing.withholding.tax_component":
        if determination_ref is None or component_ref is None:
            raise BillingRuleViolation(
                "historical_replay_mapping_incomplete",
                "raw Tax component identity is required",
            )
        key1, key2 = determination_ref, component_ref
    else:
        key1, key2 = source_ref, ""
    model = _receivable_model(scope, "ReceivableSourceClaim")
    exists = db.execute(
        _where_scope(
            select(model.id).where(
                model.namespace == namespace,
                model.source_owner == source_owner,
                model.key1 == key1,
                model.key2 == key2,
            ),
            scope,
            model,
        )
    ).first()
    if exists is not None:
        raise BillingConflict(
            "historical_discharge_source_claimed",
            "receivable source identity is permanently occupied",
        )


def _persist_imported_exposure(
    db: Session,
    *,
    scope: Scope,
    document_id: UUID,
    source_version: int,
    group_id: UUID,
    observed_at: datetime,
) -> ReceivableExposureV2:
    model = _receivable_model(scope, "OpeningReceivableBasis")
    basis_id = db.execute(
        _where_scope(
            select(model.id).where(model.document_id == document_id), scope, model
        )
    ).scalar_one()
    basis = get_opening_receivable_basis(db, scope=scope, basis_id=basis_id)
    backing = resolve_receivable_backing(
        db, scope=scope, document_id=document_id, source_version=source_version
    )
    remaining = _money(
        backing.remaining,
        basis.initial_receivable.currency.code,
        basis.initial_receivable.currency.minor_units,
    )
    state: FinancialState = (
        "cancelled"
        if not backing.active
        else "resolved"
        if backing.remaining == 0
        else "partially_resolved"
        if backing.remaining < backing.initial_amount
        else "open"
    )
    fact = ReceivableExposureV2(
        scope,
        "dotmac-billing",
        "internal",
        "authoritative",
        "posting_groups",
        "complete",
        None,
        f"document:{document_id}",
        document_id,
        basis.billing_account_id,
        basis_id,
        source_version,
        group_id,
        basis.source.collection,
        state,
        remaining,
        "pending",
        observed_at,
    )
    semantic = _jsonable(fact)
    semantic.pop("state_fingerprint")
    fact = replace(fact, state_fingerprint=fingerprint_of(semantic))
    payload = _jsonable(fact)
    exposure = _receivable_model(scope, "ReceivableExposureFactV2")
    db.add(
        exposure(
            **_values(
                scope,
                {
                    "id": uuid4(),
                    "basis_id": basis_id,
                    "billing_account_id": basis.billing_account_id,
                    "document_id": document_id,
                    "posting_group_id": group_id,
                    "source_version": source_version,
                    "currency": remaining.currency.code,
                    "minor_units": remaining.currency.minor_units,
                    "collectible_receivable": remaining.amount,
                    "evidence": payload,
                    "evidence_digest": fingerprint_of(payload),
                    "recorded_at": observed_at,
                },
            )
        )
    )
    db.flush()
    return fact


@_exact_cash_operation
def import_opening_account_v2(
    db: Session,
    *,
    scope: Scope,
    command: ImportOpeningAccountV2,
    verifier: OpeningAccountImportVerifierV2 | None = None,
) -> OpeningImportReceiptV2:
    if type(command) is not ImportOpeningAccountV2:
        raise BillingRuleViolation(
            "opening_command_invalid", "expected ImportOpeningAccountV2"
        )
    _require_scope_matches(routed=scope, declared=command.scope)
    try:
        verified = verify_opening_account_import_v2(command, verifier)
    except OpeningVerificationUnavailable as exc:
        raise BillingRuleViolation(
            "opening_verifier_unavailable", "import qualification is unavailable"
        ) from exc
    if verified is None:
        raise BillingRuleViolation(
            "opening_authority_binding",
            "trusted V2 qualification must bind this complete command",
        )
    blockers = opening_account_import_blockers_v2(command)
    if blockers:
        raise BillingRuleViolation(
            "opening_manifest_blocked",
            "opening manifest has counted blockers",
            blockers=tuple((x.code, x.count) for x in blockers),
        )
    sources = tuple(
        x for x in command.manifest.cash_lots if x.partition == command.partition
    )
    items = tuple(
        x for x in command.manifest.receivables if x.partition == command.partition
    )
    cash_minor, ar_minor = (
        sum(x.residual_minor for x in sources),
        sum(x.residual_minor for x in items),
    )
    if cash_minor < 0 or ar_minor < 0 or cash_minor + ar_minor <= 0:
        raise BillingRuleViolation(
            "opening_partition_nonpositive",
            "selected complete partition must recognize positive backing",
        )
    amount = money_from_minor(
        cash_minor, command.partition.currency, command.partition.precision
    )
    ar = money_from_minor(
        ar_minor, command.partition.currency, command.partition.precision
    )
    plane = _models(scope)
    account = _one(
        db,
        scope=scope,
        model=plane.account,
        row_id=command.partition.account_id,
        lock=True,
    )
    _require_account_money(account, amount)
    digest = opening_account_command_digest_v2(command)

    def operation(session: Session) -> dict[str, object]:
        _require_pristine_opening_target(session, scope=scope, command=command)
        for source in sources:
            if (
                session.execute(
                    _where_scope(
                        select(plane.cash_source_claim.id).where(
                            plane.cash_source_claim.source_system
                            == source.source_system,
                            plane.cash_source_claim.source_settlement_key
                            == source.source_settlement_key,
                        ),
                        scope,
                        plane.cash_source_claim,
                    )
                ).first()
                is not None
            ):
                raise BillingConflict(
                    "cash_source_claimed", "cash source identity is permanently claimed"
                )
        basis_model = _receivable_model(scope, "OpeningReceivableBasis")
        for item in items:
            occupied = session.execute(
                _where_scope(
                    select(basis_model.id).where(
                        basis_model.installation_id
                        == command.manifest.capture.installation_id,
                        basis_model.source_system
                        == command.manifest.capture.source_system,
                        basis_model.source_invoice_key == item.source_invoice_key,
                    ),
                    scope,
                    basis_model,
                )
            ).first()
            if occupied is not None:
                raise BillingConflict(
                    "opening_invoice_source_claimed",
                    "original invoice identity is permanently claimed",
                )
        receipt_id, group_id, recorded_at = uuid4(), uuid4(), datetime.now(UTC)
        bases = []
        for index, item in enumerate(items, 1):
            original = item.original
            if original is None:
                raise BillingRuleViolation(
                    "original_documentary_evidence_missing",
                    "original document is required",
                )
            document_id = uuid4()
            session.add(
                plane.document(
                    **_values(
                        scope,
                        {
                            "id": document_id,
                            "billing_account_id": account.id,
                            "obligation_id": None,
                            "authoring_mode": "imported_open_item",
                            "document_kind": "invoice",
                            "lifecycle": "issued",
                            "series_code": None,
                            "document_number": original.document_number,
                            "currency": account.currency,
                            "minor_units": account.minor_units,
                            "subtotal": original.subtotal.amount,
                            "tax_total": original.tax_total.amount,
                            "grand_total": original.grand_total.amount,
                            "due_at": item.collection.due_at,
                            "due_date_basis": _jsonable(item.collection.due_date_basis),
                            "document_profile_code": original.document_profile_code,
                            "document_profile_version": (
                                original.document_profile_version
                            ),
                            "seller_snapshot": _jsonable(original.seller),
                            "customer_snapshot": _jsonable(original.customer),
                            "payment_instructions": _jsonable(
                                original.payment_instructions
                            ),
                            "brand_asset": _jsonable(original.brand_asset),
                            "locale": original.locale,
                            "timezone": original.timezone,
                            "issued_at": original.issued_at,
                        },
                    )
                )
            )
            bases.append(
                OpeningReceivableBasisV1(
                    scope,
                    uuid4(),
                    receipt_id,
                    document_id,
                    account.id,
                    group_id,
                    1,
                    index,
                    command.manifest.capture.installation_id,
                    command.manifest.capture.source_system,
                    item.source_invoice_key,
                    item,
                    money_from_minor(
                        item.residual_minor, account.currency, account.minor_units
                    ),
                    verified.manifest_fingerprint,
                    recorded_at,
                )
            )
        session.flush()
        lots = tuple(
            OpeningCashLotFactV1(
                scope,
                uuid4(),
                receipt_id,
                account.id,
                group_id,
                index,
                source,
                money_from_minor(
                    source.residual_minor, account.currency, account.minor_units
                ),
                recorded_at,
            )
            for index, source in enumerate(sources, 1)
        )
        receipt = OpeningImportReceiptV2(
            scope,
            receipt_id,
            "import",
            account.id,
            command.manifest,
            verified.manifest_fingerprint,
            command.partition,
            command.target,
            verified,
            amount,
            ar,
            tuple(x.lot_id for x in lots),
            tuple(x.basis_id for x in bases),
            group_id,
            None,
            command.idempotency_key,
            digest,
            command.occurred_at,
            recorded_at,
        )
        opening = tuple(
            _opening_effect(
                scope=scope,
                lot=lot,
                group_id=group_id,
                sequence=index,
                kind="recognition",
                amount=lot.initial_amount,
                source_ref=command.idempotency_key,
                request_digest=digest,
                occurred_at=command.occurred_at,
                authorization_ref=command.migration_authority_ref,
            )
            for index, lot in enumerate(lots, 1)
        )
        _post_group(
            session,
            scope=scope,
            account_id=account.id,
            kind="opening_account_imported",
            currency=account.currency,
            minor_units=account.minor_units,
            source_ref=command.idempotency_key,
            occurred_at=command.occurred_at,
            effects=(
                (EffectLane.AVAILABLE_CREDIT, amount.amount),
                (EffectLane.RECEIVABLE, ar.amount),
            ),
            opening_group_id=group_id,
            opening_receipt=receipt,
            opening_lots=lots,
            opening_effects=opening,
            opening_bases=tuple(bases),
        )
        return {"receipt_id": str(receipt_id), "posting_group_id": str(group_id)}

    outcome = _idempotent_cash_claim(
        db,
        scope=scope,
        operation_scope="billing.opening.import.v2",
        key=command.idempotency_key,
        fingerprint=digest,
        operation=operation,
    )
    return get_opening_import_receipt_v2(
        db, scope=scope, receipt_id=UUID(str(outcome.result["receipt_id"]))
    )


@_exact_cash_operation
def reverse_opening_account_import_v2(
    db: Session,
    *,
    scope: Scope,
    command: ReverseOpeningAccountImportV1,
    verifier: OpeningAccountImportVerifierV2 | None = None,
) -> OpeningImportReceiptV2:
    if type(command) is not ReverseOpeningAccountImportV1:
        raise BillingRuleViolation(
            "opening_command_invalid", "expected opening unwind command"
        )
    _require_scope_matches(routed=scope, declared=command.scope)
    if len(command.idempotency_key) > 255 or len(command.migration_authority_ref) > 255:
        raise BillingRuleViolation(
            "opening_command_storage_invalid", "opening unwind identity exceeds storage"
        )
    original = get_opening_import_receipt_v2(
        db, scope=scope, receipt_id=command.receipt_id
    )
    if original.receipt_kind != "import":
        raise BillingRuleViolation(
            "opening_import_reverse_invalid",
            "only original complete principal can unwind",
        )
    digest = fingerprint_of(
        {"domain": "billing.opening.account.reverse.v2", "command": _jsonable(command)}
    )
    original_digest = fingerprint_of(_jsonable(original))
    if verifier is None:
        raise BillingRuleViolation(
            "opening_verifier_unavailable", "fresh migration authority is required"
        )
    request = OpeningImportReversalVerificationRequestV2(
        command,
        digest,
        original_digest,
        original.posting_group_id,
        original.billing_account_id,
        original.total_residual.currency.code,
        original.total_residual.currency.minor_units,
    )
    try:
        verified = verifier.verify_import_reversal_v2(request)
    except OpeningVerificationUnavailable as exc:
        raise BillingRuleViolation(
            "opening_verifier_unavailable", "unwind qualification is unavailable"
        ) from exc
    if (
        type(verified) is not OpeningImportReversalVerificationV2
        or verified.operation != "billing.opening.import.reverse.v2"
        or verified.scope != scope
        or verified.command_digest != digest
        or verified.receipt_id != original.receipt_id
        or verified.receipt_evidence_digest != original_digest
        or verified.original_posting_group_id != original.posting_group_id
        or verified.billing_account_id != original.billing_account_id
        or (verified.currency, verified.precision)
        != (request.currency, request.precision)
        or verified.migration_authority_ref != command.migration_authority_ref
    ):
        raise BillingRuleViolation(
            "opening_authority_binding",
            "unwind proof must bind the exact original complete principal",
        )
    plane = _models(scope)
    account = _one(
        db,
        scope=scope,
        model=plane.account,
        row_id=original.billing_account_id,
        lock=True,
    )

    def operation(session: Session) -> dict[str, object]:
        if (
            session.execute(
                _where_scope(
                    select(plane.opening_receipt.id).where(
                        plane.opening_receipt.original_receipt_id
                        == original.receipt_id,
                    ),
                    scope,
                    plane.opening_receipt,
                )
            ).first()
            is not None
        ):
            raise BillingConflict(
                "opening_import_already_unwound", "complete principal already unwound"
            )
        for basis_id in original.basis_ids:
            receivable_position = get_opening_receivable_position(
                session, scope=scope, basis_id=basis_id
            )
            if (
                receivable_position.unwound
                or receivable_position.remaining
                != receivable_position.basis.initial_receivable
            ):
                raise BillingRuleViolation(
                    "opening_import_has_dependents",
                    "reverse all post-import receivable effects through their owners",
                )
        lots = tuple(
            get_opening_cash_lot(session, scope=scope, lot_id=value)
            for value in original.lot_ids
        )
        for lot in lots:
            cash_position = get_opening_cash_lot_position(
                session, scope=scope, lot_id=lot.lot_id
            )
            if cash_position.unwound or cash_position.remaining != lot.initial_amount:
                raise BillingRuleViolation(
                    "opening_import_has_dependents",
                    "reverse all post-import cash uses and refunds "
                    "through their owners",
                )
        receipt_id, group_id = uuid4(), uuid4()
        receipt = replace(
            original,
            receipt_id=receipt_id,
            receipt_kind="unwind",
            verification=verified,
            posting_group_id=group_id,
            original_receipt_id=original.receipt_id,
            request_identity=command.idempotency_key,
            request_digest=digest,
            occurred_at=command.occurred_at,
            recorded_at=datetime.now(UTC),
        )
        opening = tuple(
            _opening_effect(
                scope=scope,
                lot=lot,
                group_id=group_id,
                sequence=index,
                kind="import_unwind",
                amount=lot.initial_amount,
                source_ref=command.idempotency_key,
                request_digest=digest,
                occurred_at=command.occurred_at,
                authorization_ref=command.migration_authority_ref,
            )
            for index, lot in enumerate(
                (x for x in lots if minor_units_of(x.initial_amount) > 0), 1
            )
        )
        _post_group(
            session,
            scope=scope,
            account_id=account.id,
            kind="opening_account_import_reversed",
            currency=account.currency,
            minor_units=account.minor_units,
            source_ref=command.idempotency_key,
            occurred_at=command.occurred_at,
            effects=(
                (EffectLane.AVAILABLE_CREDIT, -original.total_residual.amount),
                (EffectLane.RECEIVABLE, -original.total_receivable.amount),
            ),
            exposure_document_ids=tuple(
                get_opening_receivable_basis(
                    session, scope=scope, basis_id=x
                ).document_id
                for x in original.basis_ids
            ),
            opening_group_id=group_id,
            opening_receipt=receipt,
            opening_effects=opening,
            corrects_posting_group_id=original.posting_group_id,
        )
        return {"receipt_id": str(receipt_id), "posting_group_id": str(group_id)}

    outcome = _idempotent(
        db,
        scope=scope,
        operation_scope="billing.opening.import.reverse.v2",
        key=command.idempotency_key,
        fingerprint=digest,
        operation=operation,
    )
    return get_opening_import_receipt_v2(
        db, scope=scope, receipt_id=UUID(str(outcome.result["receipt_id"]))
    )
