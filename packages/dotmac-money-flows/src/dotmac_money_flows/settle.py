"""F1 Settle: turn one confirmed payment into accepted cash and its application.

Every command runs in the caller's transaction. Nothing here commits, rolls back,
opens a savepoint, reads a clock, or holds an amount. The money facts stay with
Billing and Payments; the instance row carries references and a state only.

Application rule (no partial payments): all confirmed cash lands as available
credit. The balance then pays WHOLE invoices only, through Billing's
``apply_available_credit``; a document that does not fit is left untouched.

* The balance is the whole pooled available credit of the account, not only
  this payment's cash: it includes credit held by earlier ``unapplied``
  instances, opening lots and credit-note excess. F1 may spend all of it.
* The fit test relies on Billing refusing atomically inside its idempotency
  savepoint: a refused draw leaves no posting group and no idempotency record,
  so flows needs no savepoint of its own.
* Bound payment: accepted deductions first, then the bound invoice is paid in
  full if the balance covers what remains, else nothing is applied. The
  instance is ``settled`` either way (cash accepted, remainder as balance);
  ``unapplied`` is only for a bound document that is not collectible.
* Invoice issue (GAP-3, ``apply_balance_on_invoice_issued``): the same pass runs
  when Billing issues a postpaid invoice, so the balance pays it (and any older
  open invoice first). Quiet no-ops return an outcome; failures raise.
* Excess after a paid bound invoice, and every deposit: other open documents,
  oldest due first, whole only, skipping any that does not fit. Gated by the
  ``deposit_application`` policy. No contract-type branching here: prepaid
  customers have no open receivables, so the cash simply stays as balance.

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

Assembly order and permissions:

* Commit the Payments confirmation first. Both ``settle_confirmation`` and
  ``record_settle_refusal`` read it, and the latter runs in a new transaction
  that cannot see an uncommitted confirmation.
* Withholding supplied with a source other than MANUAL is refused before any
  write and the instance stays ``refused``; the person fixes the evidence (or
  the source) and retries.
* ``SettleConfirmationV2`` carries several deductions (C2b). Each is accepted
  through Billing's withholding-discharge command, in order, before the
  whole-invoice pass, so cash plus every accepted deduction decide whether an
  invoice fits. The same component or retry key twice is refused before any
  write (``duplicate_deduction_component``); the rule that deductions come only
  from a MANUAL confirmation is unchanged.
* ``retry_settle`` is a human-triggered command. The assembly must gate it with
  a permission at least as strong as the manual-cash and withholding
  permissions, and pass the actor as ``actor_ref``.
* Billing and Payments state the money: native invoices emit
  ``billing.receivable.exposure.v1`` (v2 is for imported items), and the
  instance row and flows events carry references only.
* The posting instant is the Payments cash observation's ``observed_at``: cash
  is posted on the date it was received, never a date the caller picks.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from dotmac_billing import (
    AcceptSettlementV1,
    AcceptWithholdingDischarge,
    ApplyAvailableCreditCommand,
    BillingRuleViolation,
    GetFundingAvailabilityV1,
    OpenReceivableDocumentV1,
    SettlementFundingLane,
    accept_settlement,
    accept_withholding_discharge,
    apply_available_credit,
    get_document_exposure,
    get_effective_account_terms,
    get_funding_availability,
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
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_money_flows._credit_dating import newest_credit_at
from dotmac_money_flows.contracts import (
    SETTLE_DEFINITION_VERSION,
    WITHHOLDING_SOURCE,
    BalanceAppliedOutcomeV1,
    BalancePassFailed,
    DepositApplication,
    InvoiceIssuedV1,
    SettleAttentionCursorV1,
    SettleConfirmationV1,
    SettleInstanceV1,
    SettlementTargetV1,
    SettleOutcomeV1,
    SettlePolicyV1,
    SettlePorts,
    SettleRefused,
    SettleState,
    confirmation_deductions,
    settle_transition_allowed,
)
from dotmac_money_flows.events import (
    SETTLE_ATTENTION_EVENT_TYPE,
    SETTLE_SETTLED_EVENT_TYPE,
)
from dotmac_money_flows.models import SettleInstance

CONFIRMATION_UNIQUE_CONSTRAINT = "uq_settle_instances_confirmation"
#: Constraint names a duplicate attempt can trip, as ``mf_0001`` creates them.
REPLAY_CONSTRAINTS = frozenset(
    {
        CONFIRMATION_UNIQUE_CONSTRAINT,
        "settle_instances_pkey",
        "uq_settle_instances_tenant_id_id",
    }
)

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
#: Billing refusals that mean "this document does not fit the balance": not
#: enough cash-backed credit, or frozen expected withholding still undischarged.
_SHORT_BALANCE = "billing.credit_insufficient_backing"
_DOES_NOT_FIT = frozenset(
    {_SHORT_BALANCE, "billing.credit_exceeds_non_withholding_room"}
)
_POSTPAID = "postpaid"
_CASH_ACCEPTED = frozenset({"settlement_accepted"})
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
    """True if ``error`` is the duplicate-confirmation violation (a replay).

    A duplicate attempt derives the same deterministic instance id, so the
    database may report the primary key (or the tenant-and-id unique) before the
    confirmation constraint. All three mean the same thing. Matches the
    constraint name the driver reports and nothing else.
    """
    if not isinstance(error, IntegrityError):
        return False
    diag = getattr(error.orig, "diag", None)
    return getattr(diag, "constraint_name", None) in REPLAY_CONSTRAINTS


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
    discharge_refs: tuple[str, ...]
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
        withholding_discharge_refs=_stored_refs(row),
        actor_ref=row.actor_ref,
        resolution_ref=row.resolution_ref,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _stored_refs(row: SettleInstance) -> tuple[str, ...]:
    """Every stored discharge reference; a pre-list row has only the legacy one."""
    if row.withholding_discharge_list_ref:
        return tuple(str(ref) for ref in row.withholding_discharge_list_ref)
    if row.withholding_discharge_ref:
        return (row.withholding_discharge_ref,)
    return ()


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


def _duplicate_deduction(deductions: tuple[Any, ...]) -> bool:
    """True if two deductions name the same frozen component or retry key.

    Billing cannot discharge one component twice under two keys, and one key
    names one discharge, so either repeat is a caller error caught before any
    write.
    """
    components = [getattr(d.tax_evidence, "component_ref", None) for d in deductions]
    keys = [d.idempotency_key for d in deductions]
    return len(set(components)) != len(components) or len(set(keys)) != len(keys)


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
    deductions = confirmation_deductions(command)
    if deductions and source != WITHHOLDING_SOURCE:
        return refuse("withholding_requires_manual_source")
    if len(deductions) > 1 and _duplicate_deduction(deductions):
        return refuse("duplicate_deduction_component")
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
        except BillingRuleViolation:
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
    except BillingRuleViolation:
        return None


def _pay_in_order(
    documents: list[OpenReceivableDocumentV1],
    pay_whole: Callable[[OpenReceivableDocumentV1], str | None],
    *,
    skip: UUID | None = None,
) -> None:
    """The one whole-only pass shared by deposits and invoice issue (GAP-3).

    Oldest due first, whole only, skipping any that does not fit. A refusal for
    lack of balance (the balance only shrinks here) bounds every amount at or
    above it, so those are skipped without another owner call. A
    withholding-room refusal is per document: no bound.
    """
    ceiling: Money | None = None
    for document in order_open_documents(documents):
        if document.document_id == skip:
            continue
        outstanding = document.collectible_receivable
        if not outstanding.is_positive:
            continue
        if ceiling is not None and outstanding >= ceiling:
            continue
        code = pay_whole(document)
        if code == _SHORT_BALANCE and (ceiling is None or outstanding < ceiling):
            ceiling = outstanding


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
            occurred_at=cash.observed_at,
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

    applied: list[UUID] = []
    state = SettleState.SETTLED
    reason: str | None = None

    # Accepted deductions first: the whole-invoice test is made against what
    # remains owing after cash-equivalents (cash + accepted deductions).
    discharge_refs: list[str] = []
    for wht in confirmation_deductions(command):
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
                occurred_at=cash.observed_at,
                certificate_observation=wht.certificate_observation,
                settlement_id=settlement.id,
            ),
        )
        discharge_refs.append(str(discharge.id))

    def pay_whole(document_id: UUID, outstanding: Money) -> str | None:
        """Pay ``document_id`` whole from the balance, or leave it untouched.

        Returns None when paid, or the Billing code when the document does not
        fit. Billing runs the draw atomically (its idempotency wrapper owns a
        savepoint), so a refusal leaves no effect. Any other refusal propagates.
        """
        try:
            apply_available_credit(
                db,
                scope=scope,
                command=ApplyAvailableCreditCommand(
                    billing_account_id=account_id,
                    document_id=document_id,
                    amount=outstanding,
                    occurred_at=cash.observed_at,
                    source_ref=f"money-flows:settle:{instance_id}:{document_id}",
                ),
            )
        except BillingRuleViolation as exc:
            if exc.code not in _DOES_NOT_FIT:
                raise
            return exc.code
        applied.append(document_id)
        return None

    pay_others = plan.policy.deposit_application is DepositApplication.OLDEST_DUE_FIRST
    bound_id = plan.target.bound_document_id
    if bound_id is not None:
        try:
            exposure = get_document_exposure(db, scope=scope, document_id=bound_id)
        except BillingRuleViolation:
            exposure = None
        if exposure is None:
            state, reason = SettleState.UNAPPLIED, "bound_document_unreadable"
            pay_others = False
        elif (
            exposure.billing_account_id != account_id
            or exposure.collectible_receivable.currency != cash.confirmed.currency
            or not exposure.collectible_receivable.is_positive
        ):
            state, reason = SettleState.UNAPPLIED, "bound_document_not_collectible"
            pay_others = False
        else:
            # Whole or nothing. A short payment stays as balance and the
            # invoice stays open: the instance is SETTLED (cash accepted, the
            # remainder sits as available credit) with nothing applied.
            if pay_whole(bound_id, exposure.collectible_receivable) is not None:
                pay_others = False  # nothing is "excess" if the invoice is unpaid
    if pay_others:
        documents = _walk_open_documents(db, scope, account_id, cash.confirmed.currency)
        if documents is None:
            state, reason = SettleState.UNAPPLIED, "open_documents_unreadable"
        else:
            _pay_in_order(
                documents,
                lambda d: pay_whole(d.document_id, d.collectible_receivable),
                skip=bound_id,
            )
    return _Result(state, reason, settlement.id, tuple(discharge_refs), tuple(applied))


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
    discharge_refs: tuple[str, ...],
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
        withholding_discharge_ref=discharge_refs[0] if discharge_refs else None,
        withholding_discharge_list_ref=list(discharge_refs) or None,
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
    discharge_refs: tuple[str, ...],
    actor_ref: str | None,
) -> None:
    if not settle_transition_allowed(SettleState(row.state), state):
        raise SettleRefused("transition_not_allowed")
    row.state = state.value
    row.reason_code = reason_code
    row.policy_version = policy_version
    row.billing_account_id = account_id
    row.settlement_id = settlement_id
    row.withholding_discharge_ref = discharge_refs[0] if discharge_refs else None
    row.withholding_discharge_list_ref = list(discharge_refs) or None
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
            discharge_refs=(),
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
        discharge_refs=result.discharge_refs,
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
                discharge_refs=(),
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
        discharge_refs=(),
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
            discharge_refs=(),
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
        discharge_refs=result.discharge_refs,
        actor_ref=actor_ref,
    )
    return _outcome(row, replayed=False, applied=result.applied)


def apply_balance_on_invoice_issued(
    db: Session,
    *,
    scope: TenantScope,
    request: InvoiceIssuedV1,
    ports: SettlePorts,
) -> BalanceAppliedOutcomeV1:
    """GAP-3: the balance pays whole open invoices when a postpaid invoice issues.

    Runs the same pass as a deposit (``_pay_in_order``: every open invoice of the
    account, oldest due first, whole only, misfits skipped) in the caller's
    transaction. Order: read the account from the invoice, take the Billing
    account lock first (``get_funding_availability``, as F2 does), then read every
    gate fresh. A business no-op posts nothing and returns a quiet outcome whose
    ``reason_code`` names it. A technical or owner failure RAISES and writes
    nothing: no attention row, no event. The caller rolls back and its event
    delivery retries; the deterministic source references make a retry, or the
    next invoice or deposit pass, complete the work without paying twice.

    Applications are dated the later of this invoice's issue date, the paid
    invoice's issue date and the newest cash received since those dates (spent
    or not), read from Billing's account statement; flows reads no clock. The
    source reference carries the document's Billing ``source_version``, so a
    replay of the same state posts nothing while a reversal (a new version)
    can be paid again. Faults raise ``BalancePassFailed``; Billing exceptions
    propagate unchanged. Both are retryable.
    """
    _tenant(scope)
    trigger = request.document_id
    account_id = get_document_exposure(
        db, scope=scope, document_id=trigger
    ).billing_account_id  # immutable, so this read before the lock is safe
    availability = get_funding_availability(
        db, scope=scope, request=GetFundingAvailabilityV1(scope, account_id)
    )  # account lock

    def done(
        reason: str | None, applied: tuple[UUID, ...] = ()
    ) -> BalanceAppliedOutcomeV1:
        return BalanceAppliedOutcomeV1(account_id, applied, reason)

    policy = ports.policy.read(db, scope=scope)
    if policy.deposit_application is not DepositApplication.OLDEST_DUE_FIRST:
        return done("deposit_application_off")
    exposure = get_document_exposure(db, scope=scope, document_id=trigger)
    if exposure.billing_account_id != account_id:
        raise BalancePassFailed("document_account_mismatch")
    if not exposure.collectible_receivable.is_positive:
        return done("invoice_not_open")
    if not availability.unrestricted.is_positive:
        return done("no_balance")
    currency = exposure.collectible_receivable.currency
    documents = _walk_open_documents(db, scope, account_id, currency)
    if documents is None:
        raise BalancePassFailed("open_documents_unreadable")
    issued = {d.document_id: d.issued_at for d in documents}
    if trigger not in issued:
        # Its balance is positive yet Billing does not list it: a fault, not a
        # no-op. Retry; never stay quiet.
        raise BalancePassFailed("invoice_missing_from_open_documents")
    terms = get_effective_account_terms(
        db, scope=scope, account_id=account_id, at=issued[trigger]
    )
    if terms.contract_type.value != _POSTPAID:
        return done("account_not_postpaid")

    # Dating: each application is at or after both invoice dates (``base``) and
    # at or after the newest cash received since then, spent or not. The window
    # opens at the earliest base; opening lots post no ``settlement_accepted``.
    bases = {d.document_id: max(issued[trigger], d.issued_at) for d in documents}
    newest_cash = newest_credit_at(
        db,
        scope,
        account_id,
        currency,
        min(bases.values()),
        kinds=_CASH_ACCEPTED,
    )

    applied: list[UUID] = []

    def pay(document: OpenReceivableDocumentV1) -> str | None:
        base = bases[document.document_id]
        try:
            apply_available_credit(
                db,
                scope=scope,
                command=ApplyAvailableCreditCommand(
                    billing_account_id=account_id,
                    document_id=document.document_id,
                    amount=document.collectible_receivable,
                    occurred_at=base if newest_cash is None else max(base, newest_cash),
                    source_ref=(
                        f"money-flows:settle:issue:{trigger}:{document.document_id}"
                        f":{document.source_version}"
                    ),
                ),
            )
        except BillingRuleViolation as exc:
            if exc.code not in _DOES_NOT_FIT:
                raise
            return exc.code
        applied.append(document.document_id)
        return None

    _pay_in_order(documents, pay)
    return done(None if applied else "no_invoice_fits", tuple(applied))


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
    after: SettleAttentionCursorV1 | None = None,
    limit: int = 100,
) -> tuple[SettleInstanceV1, ...]:
    """Instances needing a person (refused or unapplied), oldest first.

    Keyset paging on ``(created_at, id)``: pass a cursor built from the last
    instance of the previous page. Rows created in one transaction share a
    timestamp, so the id breaks the tie and no row is skipped or repeated.
    """
    tenant_id = _tenant(scope)
    statement = select(SettleInstance).where(
        SettleInstance.tenant_id == tenant_id,
        SettleInstance.state.in_(
            (SettleState.REFUSED.value, SettleState.UNAPPLIED.value)
        ),
    )
    if after is not None:
        statement = statement.where(
            or_(
                SettleInstance.created_at > after.created_at,
                and_(
                    SettleInstance.created_at == after.created_at,
                    SettleInstance.id > after.id,
                ),
            )
        )
    rows = db.execute(
        statement.order_by(SettleInstance.created_at, SettleInstance.id).limit(
            max(1, min(limit, 500))
        )
    ).scalars()
    return tuple(_view(row) for row in rows)


__all__ = [
    "apply_balance_on_invoice_issued",
    "CONFIRMATION_UNIQUE_CONSTRAINT",
    "REPLAY_CONSTRAINTS",
    "get_settle_instance",
    "instance_id_for",
    "is_settle_replay_conflict",
    "list_settle_attention",
    "order_open_documents",
    "record_settle_refusal",
    "retry_settle",
    "settle_confirmation",
]
