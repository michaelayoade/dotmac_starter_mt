"""F1 Settle: turn one confirmed payment into accepted cash and its application.

Every command runs in the caller's transaction. Nothing here commits, rolls back,
opens a savepoint, reads a clock, or holds an amount. The money facts stay with
Billing and Payments; the instance row carries references and a state only.

Assembly contract for ``settle_confirmation``:

* The caller owns the transaction and calls this synchronously inside the
  confirmation transaction.
* An existing instance for the confirmation is a replay: its outcome is returned
  and no owner is called.
* Refusals decided before any owner write are persisted as a ``refused`` row and
  returned, so the caller may commit.
* An owner exception (held account, owner rule) propagates untouched. The caller
  rolls back, then calls ``record_settle_refusal`` in a NEW transaction.
* A concurrent duplicate that loses the race violates
  ``uq_settle_instances_confirmation``. That is a REPLAY, not an owner refusal:
  roll back and treat it as the replay of the winner (see
  ``is_settle_replay_conflict``). The owner postings are idempotent on their
  deterministic source references, so the loser leaves no effect.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from dotmac_billing import (
    AcceptSettlementV1,
    AcceptWithholdingDischarge,
    AllocationCommand,
    OpenReceivableDocumentV1,
    SettlementFundingLane,
    accept_settlement,
    accept_withholding_discharge,
    allocate_settlement,
    get_document_exposure,
    list_open_receivable_documents,
)
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.messaging import enqueue_event
from dotmac_kernel.money import Money
from dotmac_payments import (
    GetConfirmedCashV1,
    GetPaymentIntentV1,
    get_confirmed_cash_v1,
    get_payment_intent_v1,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_money_flows.contracts import (
    SETTLE_DEFINITION_VERSION,
    WITHHOLDING_SOURCE,
    DepositApplication,
    SettleConfirmationV1,
    SettleInstanceV1,
    SettlementTargetV1,
    SettleOutcomeV1,
    SettlePolicyV1,
    SettlePorts,
    SettleRefused,
    SettleState,
    settle_transition_allowed,
)
from dotmac_money_flows.events import (
    SETTLE_ATTENTION_EVENT_TYPE,
    SETTLE_SETTLED_EVENT_TYPE,
)
from dotmac_money_flows.models import SettleInstance

CONFIRMATION_UNIQUE_CONSTRAINT = "uq_settle_instances_confirmation"

_PAGE_LIMIT = 100
_INSTANCE_NAMESPACE = uuid5(NAMESPACE_URL, "urn:dotmac:money-flows:settle:v1")

#: Billing's confirmation-evidence label for each Payments confirmation source.
_EVIDENCE = {
    "PROVIDER_CALLBACK": "provider_settled",
    "PROVIDER_RECONCILIATION": "provider_settled",
    "TRANSFER_PROOF": "uploaded_proof",
    "MANUAL": "manual_confirmed",
}
_BOUND_PURPOSES = frozenset({"INVOICE_SETTLEMENT", "SERVICE_FEE"})
_DEPOSIT_PURPOSE = "ACCOUNT_CREDIT_DEPOSIT"
_CODE_CHARACTERS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_.-")


def _tenant(scope: TenantScope) -> UUID:
    if not isinstance(scope, TenantScope):
        raise TypeError("money flows requires a TenantScope")
    return scope.tenant_id


def instance_id_for(scope: TenantScope, confirmation_id: UUID) -> UUID:
    """The deterministic instance id of a confirmation.

    Deterministic so that a duplicate attempt derives the same owner source
    references and Billing's own idempotency makes it a no-op.
    """
    return uuid5(_INSTANCE_NAMESPACE, f"{_tenant(scope)}:{confirmation_id}")


def is_settle_replay_conflict(error: BaseException) -> bool:
    """True if ``error`` is the confirmation uniqueness violation (a replay)."""
    if not isinstance(error, IntegrityError):
        return False
    diag = getattr(error.orig, "diag", None)
    if getattr(diag, "constraint_name", None) == CONFIRMATION_UNIQUE_CONSTRAINT:
        return True
    return CONFIRMATION_UNIQUE_CONSTRAINT in str(error.orig)


def order_open_documents(
    documents: list[OpenReceivableDocumentV1],
) -> list[OpenReceivableDocumentV1]:
    """Oldest due first; documents without a verified due date come last.

    Ties break on ``issued_at`` then ``document_id``.
    """

    def key(document: OpenReceivableDocumentV1) -> tuple[Any, ...]:
        verified = (
            document.due_at is not None and document.due_at_basis.value == "verified"
        )
        if verified:
            return (0, document.due_at, document.issued_at, document.document_id)
        return (1, document.issued_at, document.document_id)

    return sorted(documents, key=key)


# --------------------------------------------------------------------------
# Internal decision records
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class _Refusal:
    code: str
    intent_id: UUID
    policy_version: str


@dataclass(frozen=True, slots=True)
class _Plan:
    cash: Any
    intent: Any
    policy: SettlePolicyV1
    target: SettlementTargetV1
    deposit: bool


@dataclass(frozen=True, slots=True)
class _Result:
    state: SettleState
    reason_code: str | None
    settlement_id: UUID
    discharge_ref: str | None
    applied: tuple[UUID, ...]


def _check_code(code: str) -> str:
    if not code or len(code) > 80 or any(c not in _CODE_CHARACTERS for c in code):
        raise ValueError("refusal code must be a short machine code")
    return code


def _find(
    db: Session, tenant_id: UUID, *, confirmation_id: UUID, lock: bool = False
) -> SettleInstance | None:
    statement = select(SettleInstance).where(
        SettleInstance.tenant_id == tenant_id,
        SettleInstance.confirmation_id == confirmation_id,
    )
    if lock:
        statement = statement.with_for_update()
    return db.execute(
        statement.execution_options(populate_existing=True)
    ).scalar_one_or_none()


def _outcome(
    row: SettleInstance, *, replayed: bool, applied: tuple[UUID, ...] = ()
) -> SettleOutcomeV1:
    return SettleOutcomeV1(
        instance_id=row.id,
        state=SettleState(row.state),
        reason_code=row.reason_code,
        version=row.version,
        settlement_id=row.settlement_id,
        billing_account_id=row.billing_account_id,
        replayed=replayed,
        applied_document_ids=applied,
    )


def _view(row: SettleInstance) -> SettleInstanceV1:
    return SettleInstanceV1(
        id=row.id,
        confirmation_id=row.confirmation_id,
        intent_id=row.intent_id,
        state=SettleState(row.state),
        reason_code=row.reason_code,
        policy_version=row.policy_version,
        definition_version=row.definition_version,
        version=row.version,
        billing_account_id=row.billing_account_id,
        settlement_id=row.settlement_id,
        withholding_discharge_ref=row.withholding_discharge_ref,
        actor_ref=row.actor_ref,
        resolution_ref=row.resolution_ref,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _text(value: UUID | None) -> str | None:
    return None if value is None else str(value)


def _emit(db: Session, row: SettleInstance) -> None:
    """Outbox event in the caller's transaction; references only."""
    if SettleState(row.state) is SettleState.SETTLED:
        event_type = SETTLE_SETTLED_EVENT_TYPE
    else:
        event_type = SETTLE_ATTENTION_EVENT_TYPE
    payload: dict[str, object] = {
        "instance_id": str(row.id),
        "confirmation_id": str(row.confirmation_id),
        "intent_id": str(row.intent_id),
        "state": row.state,
        "reason_code": row.reason_code,
        "version": row.version,
        "billing_account_id": _text(row.billing_account_id),
        "settlement_id": _text(row.settlement_id),
    }
    enqueue_event(
        db,
        tenant_id=row.tenant_id,
        event_type=event_type,
        payload=payload,
        correlation_id=f"settle:{row.id}:{row.version}",
    )


# --------------------------------------------------------------------------
# Phase 1: decisions that need no owner write
# --------------------------------------------------------------------------
def _prepare(
    db: Session,
    scope: TenantScope,
    command: SettleConfirmationV1,
    ports: SettlePorts,
) -> _Plan | _Refusal:
    cash = get_confirmed_cash_v1(
        db, scope=scope, query=GetConfirmedCashV1(command.confirmation_id)
    )
    policy = ports.policy.read(db, scope=scope)

    def refuse(code: str) -> _Refusal:
        return _Refusal(code, cash.intent_id, policy.version)

    intent = get_payment_intent_v1(
        db, scope=scope, query=GetPaymentIntentV1(command.intent_reference)
    )
    if intent is None:
        return refuse("intent_not_found")
    if intent.intent_id != cash.intent_id:
        return refuse("intent_mismatch")
    if intent.requested.currency != cash.confirmed.currency:
        return refuse("currency_mismatch")
    source = cash.source.value
    if source not in policy.accepted_confirmation_sources or source not in _EVIDENCE:
        return refuse("source_not_accepted")
    if command.withholding is not None and source != WITHHOLDING_SOURCE:
        return refuse("withholding_requires_manual_source")
    purpose = intent.purpose.value
    if purpose not in _BOUND_PURPOSES and purpose != _DEPOSIT_PURPOSE:
        return refuse("purpose_not_settled_here")
    # The target comes only from the intent's own reference and purpose.
    target = ports.targets.resolve(
        db,
        scope=scope,
        target_reference=intent.target_reference,
        purpose=purpose,
    )
    if target is None:
        return refuse("target_unresolved")
    if purpose in _BOUND_PURPOSES and target.bound_document_id is None:
        return refuse("bound_document_required")
    if purpose == _DEPOSIT_PURPOSE and target.bound_document_id is not None:
        return refuse("deposit_must_not_bind_document")
    if target.bound_document_id is not None:
        try:
            exposure = get_document_exposure(
                db, scope=scope, document_id=target.bound_document_id
            )
        except ValueError:
            exposure = None  # decided after the cash is accepted
        if exposure is not None and (
            exposure.billing_account_id != target.billing_account_id
        ):
            return refuse("document_account_mismatch")
    return _Plan(
        cash=cash,
        intent=intent,
        policy=policy,
        target=target,
        deposit=purpose == _DEPOSIT_PURPOSE,
    )


# --------------------------------------------------------------------------
# Phase 2: owner writes (caller's transaction)
# --------------------------------------------------------------------------
def _walk_open_documents(
    db: Session, scope: TenantScope, account_id: UUID, currency: Any
) -> list[OpenReceivableDocumentV1] | None:
    """Every open document of the account, all pages; None if unreadable."""
    found: list[OpenReceivableDocumentV1] = []
    cursor = None
    seen: list[object] = []
    try:
        while True:
            page = list_open_receivable_documents(
                db,
                scope=scope,
                account=account_id,
                currency=currency,
                cursor=cursor,
                limit=_PAGE_LIMIT,
            )
            found.extend(page.entries)
            cursor = page.next_cursor
            if cursor is None:
                return found
            if cursor in seen:
                return None
            seen.append(cursor)
    except ValueError:
        return None


def _execute(
    db: Session,
    scope: TenantScope,
    command: SettleConfirmationV1,
    plan: _Plan,
    instance_id: UUID,
    *,
    recheck: bool,
) -> _Result | SettleInstance:
    cash = plan.cash
    account_id = plan.target.billing_account_id
    settlement = accept_settlement(
        db,
        scope=scope,
        command=AcceptSettlementV1(
            scope=scope,
            billing_account_id=account_id,
            source_system="payments:" + cash.provider_type,
            source_settlement_key=cash.external_reference,
            source_version="1",
            amount=cash.confirmed,
            occurred_at=command.occurred_at,
            observed_at=cash.observed_at,
            confirmation_evidence=_EVIDENCE[cash.source.value],
            funding_lane=SettlementFundingLane.AVAILABLE_CREDIT,
        ),
        accepted_confirmation_evidence=frozenset(
            _EVIDENCE[s]
            for s in plan.policy.accepted_confirmation_sources
            if s in _EVIDENCE
        ),
    )
    if recheck:
        # The account lock is now held, so a duplicate that won the race has
        # committed. Its row is the answer; Billing's acceptance replayed.
        winner = _find(db, scope.tenant_id, confirmation_id=command.confirmation_id)
        if winner is not None:
            return winner

    remaining: Money = cash.confirmed
    applied: list[UUID] = []
    state = SettleState.SETTLED
    reason: str | None = None

    def allocate(document_id: UUID, amount: Money) -> None:
        allocate_settlement(
            db,
            scope=scope,
            command=AllocationCommand(
                settlement_id=settlement.id,
                document_id=document_id,
                amount=amount,
                occurred_at=command.occurred_at,
                source_ref=f"money-flows:settle:{instance_id}:{document_id}",
            ),
        )
        applied.append(document_id)

    if plan.target.bound_document_id is not None:
        bound_id = plan.target.bound_document_id
        try:
            exposure = get_document_exposure(db, scope=scope, document_id=bound_id)
        except ValueError:
            exposure = None
        if exposure is None:
            state, reason = SettleState.UNAPPLIED, "bound_document_unreadable"
        elif (
            exposure.billing_account_id != account_id
            or exposure.collectible_receivable.currency != remaining.currency
            or not exposure.collectible_receivable.is_positive
        ):
            state, reason = SettleState.UNAPPLIED, "bound_document_not_collectible"
        else:
            collectible = exposure.collectible_receivable
            allocate(bound_id, remaining if remaining <= collectible else collectible)
    elif plan.deposit and (
        plan.policy.deposit_application is DepositApplication.OLDEST_DUE_FIRST
    ):
        documents = _walk_open_documents(db, scope, account_id, remaining.currency)
        if documents is None:
            state, reason = SettleState.UNAPPLIED, "open_documents_unreadable"
        else:
            for document in order_open_documents(documents):
                if not remaining.is_positive:
                    break
                collectible = document.collectible_receivable
                amount = remaining if remaining <= collectible else collectible
                allocate(document.document_id, amount)
                remaining = remaining - amount

    discharge_ref: str | None = None
    wht = command.withholding
    if wht is not None:
        discharge = accept_withholding_discharge(
            db,
            scope=scope,
            command=AcceptWithholdingDischarge(
                scope=scope,
                billing_account_id=account_id,
                document_id=wht.tax_evidence.document_id,
                idempotency_key=wht.idempotency_key,
                source_owner=wht.source_owner,
                source_ref=wht.source_ref,
                claim_ref=wht.claim_ref,
                claimed_amount=wht.claimed,
                accepted_amount=wht.accepted,
                tax_evidence=wht.tax_evidence,
                authorization_actor_ref=wht.authorization_actor_ref,
                authorization_evidence_ref=wht.authorization_evidence_ref,
                occurred_at=command.occurred_at,
                certificate_observation=wht.certificate_observation,
                settlement_id=settlement.id,
            ),
        )
        discharge_ref = str(discharge.id)
    return _Result(state, reason, settlement.id, discharge_ref, tuple(applied))


# --------------------------------------------------------------------------
# Persistence of the outcome
# --------------------------------------------------------------------------
def _insert(
    db: Session,
    tenant_id: UUID,
    instance_id: UUID,
    command: SettleConfirmationV1,
    *,
    intent_id: UUID,
    policy_version: str,
    state: SettleState,
    reason_code: str | None,
    account_id: UUID | None,
    settlement_id: UUID | None,
    discharge_ref: str | None,
    actor_ref: str | None,
) -> SettleInstance:
    row = SettleInstance(
        id=instance_id,
        tenant_id=tenant_id,
        state=state.value,
        reason_code=reason_code,
        policy_version=policy_version,
        definition_version=SETTLE_DEFINITION_VERSION,
        version=0,
        actor_ref=actor_ref,
        confirmation_id=command.confirmation_id,
        intent_id=intent_id,
        billing_account_id=account_id,
        settlement_id=settlement_id,
        withholding_discharge_ref=discharge_ref,
    )
    db.add(row)
    db.flush()
    _emit(db, row)
    return row


def _advance(
    db: Session,
    row: SettleInstance,
    *,
    policy_version: str,
    state: SettleState,
    reason_code: str | None,
    account_id: UUID | None,
    settlement_id: UUID | None,
    discharge_ref: str | None,
    actor_ref: str | None,
) -> None:
    if not settle_transition_allowed(SettleState(row.state), state):
        raise SettleRefused("transition_not_allowed")
    row.state = state.value
    row.reason_code = reason_code
    row.policy_version = policy_version
    row.billing_account_id = account_id
    row.settlement_id = settlement_id
    row.withholding_discharge_ref = discharge_ref
    row.actor_ref = actor_ref
    row.version = row.version + 1
    db.flush()
    _emit(db, row)


# --------------------------------------------------------------------------
# Public commands
# --------------------------------------------------------------------------
def settle_confirmation(
    db: Session,
    *,
    scope: TenantScope,
    command: SettleConfirmationV1,
    ports: SettlePorts,
) -> SettleOutcomeV1:
    """Settle one confirmed payment in the caller's transaction.

    See the module docstring for the assembly contract: replay first, one row
    written at the outcome, owner exceptions propagate, and a
    ``uq_settle_instances_confirmation`` violation is a replay of the winner.
    """
    tenant_id = _tenant(scope)
    existing = _find(db, tenant_id, confirmation_id=command.confirmation_id)
    if existing is not None:
        return _outcome(existing, replayed=True)
    instance_id = instance_id_for(scope, command.confirmation_id)
    prepared = _prepare(db, scope, command, ports)
    if isinstance(prepared, _Refusal):
        row = _insert(
            db,
            tenant_id,
            instance_id,
            command,
            intent_id=prepared.intent_id,
            policy_version=prepared.policy_version,
            state=SettleState.REFUSED,
            reason_code=_check_code(prepared.code),
            account_id=None,
            settlement_id=None,
            discharge_ref=None,
            actor_ref=None,
        )
        return _outcome(row, replayed=False)
    result = _execute(db, scope, command, prepared, instance_id, recheck=True)
    if isinstance(result, SettleInstance):
        return _outcome(result, replayed=True)
    row = _insert(
        db,
        tenant_id,
        instance_id,
        command,
        intent_id=prepared.cash.intent_id,
        policy_version=prepared.policy.version,
        state=result.state,
        reason_code=result.reason_code,
        account_id=prepared.target.billing_account_id,
        settlement_id=result.settlement_id,
        discharge_ref=result.discharge_ref,
        actor_ref=None,
    )
    return _outcome(row, replayed=False, applied=result.applied)


def record_settle_refusal(
    db: Session,
    *,
    scope: TenantScope,
    command: SettleConfirmationV1,
    ports: SettlePorts,
    refusal_code: str,
    actor_ref: str | None = None,
) -> SettleOutcomeV1:
    """Persist the attention row for an owner refusal, in a NEW transaction.

    Call it after the failed transaction rolled back. It checks for an existing
    instance first and never touches one that is not ``refused``; a ``refused``
    instance only has its reason updated. A lost insert race raises the
    confirmation uniqueness violation, which is a replay for the caller.
    """
    tenant_id = _tenant(scope)
    code = _check_code(refusal_code)
    existing = _find(db, tenant_id, confirmation_id=command.confirmation_id, lock=True)
    if existing is not None:
        if existing.state == SettleState.REFUSED.value and existing.reason_code != code:
            _advance(
                db,
                existing,
                policy_version=existing.policy_version,
                state=SettleState.REFUSED,
                reason_code=code,
                account_id=None,
                settlement_id=None,
                discharge_ref=None,
                actor_ref=actor_ref or existing.actor_ref,
            )
            return _outcome(existing, replayed=False)
        return _outcome(existing, replayed=True)
    cash = get_confirmed_cash_v1(
        db, scope=scope, query=GetConfirmedCashV1(command.confirmation_id)
    )
    policy = ports.policy.read(db, scope=scope)
    row = _insert(
        db,
        tenant_id,
        instance_id_for(scope, command.confirmation_id),
        command,
        intent_id=cash.intent_id,
        policy_version=policy.version,
        state=SettleState.REFUSED,
        reason_code=code,
        account_id=None,
        settlement_id=None,
        discharge_ref=None,
        actor_ref=actor_ref,
    )
    return _outcome(row, replayed=False)


def retry_settle(
    db: Session,
    *,
    scope: TenantScope,
    instance_id: UUID,
    expected_version: int,
    actor_ref: str,
    command: SettleConfirmationV1,
    ports: SettlePorts,
) -> SettleOutcomeV1:
    """Retry a ``refused`` instance. No other state may be retried.

    Takes the row ``FOR UPDATE`` and checks ``expected_version`` before any owner
    call, then re-pins the policy: no money has moved for a refused instance.
    A repeat refusal advances the row to the new reason; an owner exception
    propagates and the caller rolls back (then may call
    ``record_settle_refusal``). ``actor_ref`` is an opaque audit reference.
    """
    tenant_id = _tenant(scope)
    if not actor_ref or not actor_ref.strip():
        raise SettleRefused("actor_required")
    row = db.execute(
        select(SettleInstance)
        .where(SettleInstance.tenant_id == tenant_id, SettleInstance.id == instance_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if row is None:
        raise SettleRefused("instance_not_found")
    if row.version != expected_version:
        raise SettleRefused("stale_version")
    if row.state != SettleState.REFUSED.value:
        raise SettleRefused("retry_not_allowed")
    if row.confirmation_id != command.confirmation_id:
        raise SettleRefused("confirmation_mismatch")
    prepared = _prepare(db, scope, command, ports)
    if isinstance(prepared, _Refusal):
        _advance(
            db,
            row,
            policy_version=prepared.policy_version,
            state=SettleState.REFUSED,
            reason_code=_check_code(prepared.code),
            account_id=None,
            settlement_id=None,
            discharge_ref=None,
            actor_ref=actor_ref,
        )
        return _outcome(row, replayed=False)
    result = _execute(db, scope, command, prepared, row.id, recheck=False)
    if isinstance(result, SettleInstance):  # unreachable without a recheck
        raise SettleRefused("unexpected_replay")
    _advance(
        db,
        row,
        policy_version=prepared.policy.version,
        state=result.state,
        reason_code=result.reason_code,
        account_id=prepared.target.billing_account_id,
        settlement_id=result.settlement_id,
        discharge_ref=result.discharge_ref,
        actor_ref=actor_ref,
    )
    return _outcome(row, replayed=False, applied=result.applied)


def get_settle_instance(
    db: Session,
    *,
    scope: TenantScope,
    instance_id: UUID | None = None,
    confirmation_id: UUID | None = None,
) -> SettleInstanceV1 | None:
    """Read one instance by id or by confirmation id (exactly one)."""
    tenant_id = _tenant(scope)
    if (instance_id is None) == (confirmation_id is None):
        raise ValueError("name exactly one of instance_id or confirmation_id")
    statement = select(SettleInstance).where(SettleInstance.tenant_id == tenant_id)
    if instance_id is not None:
        statement = statement.where(SettleInstance.id == instance_id)
    else:
        statement = statement.where(SettleInstance.confirmation_id == confirmation_id)
    row = db.execute(statement).scalar_one_or_none()
    return None if row is None else _view(row)


def list_settle_attention(
    db: Session,
    *,
    scope: TenantScope,
    after: datetime | None = None,
    limit: int = 100,
) -> tuple[SettleInstanceV1, ...]:
    """Instances needing a person (refused or unapplied), oldest first.

    ``after`` pages by ``created_at``.
    """
    tenant_id = _tenant(scope)
    statement = select(SettleInstance).where(
        SettleInstance.tenant_id == tenant_id,
        SettleInstance.state.in_(
            (SettleState.REFUSED.value, SettleState.UNAPPLIED.value)
        ),
    )
    if after is not None:
        statement = statement.where(SettleInstance.created_at > after)
    rows = db.execute(
        statement.order_by(SettleInstance.created_at, SettleInstance.id).limit(
            max(1, min(limit, 500))
        )
    ).scalars()
    return tuple(_view(row) for row in rows)


__all__ = [
    "CONFIRMATION_UNIQUE_CONSTRAINT",
    "get_settle_instance",
    "instance_id_for",
    "is_settle_replay_conflict",
    "list_settle_attention",
    "order_open_documents",
    "record_settle_refusal",
    "retry_settle",
    "settle_confirmation",
]
