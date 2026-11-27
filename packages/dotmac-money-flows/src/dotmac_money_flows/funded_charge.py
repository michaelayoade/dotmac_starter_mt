"""F2 FundedCharge: pay one prepaid charge from account credit, or hold it pending.

Every command runs in the caller's transaction. Nothing here commits, rolls back,
opens a savepoint, reads a clock, or holds an amount. The money facts stay with
Billing; the instance row carries references and a state only.

Prepaid is pay as you go (whole charges only, oldest first):

* A due charge the balance covers in full is invoiced and paid from available
  credit in the same transaction: ``discharged``. Revenue and VAT are recognised
  at that moment and the prepayment liability falls.
* A charge the balance cannot cover in full stays ``pending``, held by flows
  only: no invoice, no VAT, no receivable, no deadline, no timer, no cancel. The
  service is off exactly while a ``pending`` row exists for it (D-F2-7:
  ``attention_required`` never keeps it off).
* A charge the source no longer holds, or whose account is no longer prepaid at
  the period start, is ``withdrawn`` and nothing is posted.
* An owner refusal on a charge the balance covers is ``attention_required``;
  ``retry_funded_charge`` is the one human path out of it.

The prepaid period starts when it is paid. F2 owns the instant: ``issue_at`` is
the charge's ``due_at`` at start, ``max(due_at, settlement.observed_at)`` on
re-evaluation, and ``max(due_at, settlement.observed_at)`` (or ``due_at``) on
retry; every posting instant comes from an owner fact. The charge source builds
the invoice command for that instant; F2 refuses it unless it carries the charge's
own identity and account, the amount due at ``issue_at`` and a verified service
period starting at ``issue_at``. The contract type and the period length are
Billing account terms, read at ``issue_at``.

GAP-5, the period rule: the period is ``[issue_at, end)`` where ``end`` counts the
terms period (day or month units) from ``issue_at`` in the policy's billing time
zone, clamped to the month end (private ``_service_period``). The command must be
``advance``, due at ``issue_at``, in the policy's time zone. A charge carrying an
enclosing period (``period_starts_at``/``period_ends_at``) is prorated by actual
calendar days when ``policy.proration`` allows: the amount is ``total`` x the days
from ``issue_at`` to the enclosing end / the days in it, rounded half-up once, and
the period is ``[issue_at, enclosing end)``. An enclosing period that disagrees
with the terms at its start, proration off, a wrong amount, a wrong zone or any
other period is ``charge_command_mismatch``. No day left is withdrawn as
``period_elapsed`` (``start_funded_charge`` refuses it and writes no row).

Assembly contract:

* The caller owns the transaction. An existing instance for the charge is a
  replay: its outcome is returned and no owner is called.
* ``start_funded_charge`` writes no row for a charge the source does not hold,
  whose account is not prepaid, or whose enclosing period is already over (refused
  ``period_elapsed``: the source must retire the charge); an owner exception
  propagates untouched. The
  caller rolls back, then calls ``record_funded_charge_attention`` in a NEW
  transaction.
* ``on_settlement_settled`` is routed by the assembly for
  ``money_flows.settle.settled.v1`` and for its own
  ``money_flows.funded_charge.reevaluate.v1``. It examines at most
  ``reevaluation_batch_cap`` pending rows and continues by event, resuming after
  the last row examined. An owner refusal raises ``FundedChargeStepFailed``; the
  transport rolls back, calls ``record_funded_charge_attention`` and returns
  normally so the event is not poisoned.
* ``on_account_credit_changed`` (GAP-L1) is routed by the assembly for every
  tenant ``billing.receivable.position.v2`` and for ``money_flows.funded_charge.
  credit_recheck.v1``. Credit can reach an account with no deposit (an opening
  lot, an admitted held partition, a staff reversal); the same re-evaluation runs
  with the period starting at ``max(due_at, newest credit line since due_at)``.
  A technical fault raises and writes nothing; an owner refusal is a step failure
  recorded with ``continue_credit_recheck=True``.
* ``reevaluate_account_terms`` is called by the assembly when an account's
  contract terms change, and is routed for ``money_flows.funded_charge.
  terms_recheck.v1``. It only withdraws (or sends to attention); it never pays.
* ``retry_funded_charge`` is human-triggered: gate it with a permission and pass
  the actor as ``actor_ref``.
* Lock order, always within one account: the Billing account first (via the
  funding availability or settlement read). ``start_funded_charge`` then takes
  the service advisory lock before it writes its row; re-evaluation, attention
  and retry lock the instance rows first and the service advisory lock after.
  Only one account is ever locked per call, and the funds check always uses the
  row's own account.
* F2 is the only writer of the PREPAID_DEPLETED access signal. The signal is an
  outbox event shaped like the Service Access Policy ``RecordAccessInputV2``;
  its ``source_version`` is the sum of ``version + 1`` over the service's rows,
  which rises with every write.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

from dotmac_billing import (
    AccountTermsV1,
    ApplyAvailableCreditCommand,
    BillingError,
    GetConfirmedSettlementV1,
    GetFundingAvailabilityV1,
    IssuedRatedInvoiceV1,
    IssueRatedInvoiceV3,
    apply_available_credit,
    get_confirmed_settlement,
    get_document_exposure,
    get_effective_account_terms,
    get_funding_availability,
    issue_rated_invoice,
)
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.idempotency import IdempotencyConflict
from dotmac_kernel.messaging import enqueue_event
from dotmac_kernel.money import Money
from dotmac_service_access_policy import AccessSignal, RecordAccessInputV2
from sqlalchemy import BigInteger, Text, and_, exists, func, literal, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from dotmac_money_flows._credit_dating import newest_credit_at
from dotmac_money_flows._service_period import (
    day_span,
    period_end,
    prorated,
    zone_of,
)
from dotmac_money_flows.contracts import (
    FUNDED_CHARGE_DEFINITION_VERSION,
    AccountCreditChangedV1,
    AccountTermsChangedV1,
    BalancePassFailed,
    CreditRecheckOutcomeV1,
    FundedChargeCursorV1,
    FundedChargeInstanceV1,
    FundedChargeOutcomeV1,
    FundedChargePolicyV1,
    FundedChargePorts,
    FundedChargeRefused,
    FundedChargeState,
    FundedChargeStepFailed,
    RatedChargeKeyV1,
    RatedChargeV1,
    SettledFundsV1,
    StartFundedChargeV1,
    funded_charge_transition_allowed,
)
from dotmac_money_flows.events import (
    FUNDED_CHARGE_ACCESS_EVENT_TYPE,
    FUNDED_CHARGE_ATTENTION_EVENT_TYPE,
    FUNDED_CHARGE_CREDIT_RECHECK_EVENT_TYPE,
    FUNDED_CHARGE_DISCHARGED_EVENT_TYPE,
    FUNDED_CHARGE_REEVALUATE_EVENT_TYPE,
    FUNDED_CHARGE_TERMS_RECHECK_EVENT_TYPE,
)
from dotmac_money_flows.models import FundedChargeInstance

#: Constraint names a duplicate attempt can trip, as ``mf_0002`` creates them.
REPLAY_CONSTRAINTS = frozenset(
    {
        "funded_charge_instances_pkey",
        "uq_funded_charge_instances_charge",
        "uq_funded_charge_instances_tenant_id_id",
    }
)

#: The states in which a service is off (D-F2-7). ``attention_required`` is never
#: among them: our own posting errors leave the service on.
_DEFAULT_ACCESS_STATES = ("pending",)

_PAGE_LIMIT = 100
_CHARGE_REF_MAX_BYTES = 1024
_INSTANCE_NAMESPACE = uuid5(NAMESPACE_URL, "urn:dotmac:money-flows:funded-charge:v1")
_PREPAID = "prepaid"
_MISMATCH = "charge_command_mismatch"
_ELAPSED = "period_elapsed"
_SOURCE_ERROR = "charge_source_error"
_VERIFIED = "verified"
_ADVANCE = "advance"
_PRORATE = "actual_calendar_days"
_PRORATIONS = frozenset({_PRORATE, "none"})
_CODE_CHARACTERS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_.-")


def _tenant(scope: TenantScope) -> UUID:
    if not isinstance(scope, TenantScope):
        raise TypeError("money flows requires a TenantScope")
    return scope.tenant_id


def _read_policy(
    db: Session, scope: TenantScope, ports: FundedChargePorts
) -> FundedChargePolicyV1:
    """Read the tenant policy and refuse a value F2 cannot honour.

    An unknown ``proration`` or ``billing_timezone`` raises ``ValueError`` before
    anything is written (as the ``reevaluation_batch_cap`` check does), so the
    event is redelivered rather than a charge being priced on a guess.
    """
    policy = ports.policy.read(db, scope=scope)
    if policy.proration not in _PRORATIONS:
        raise ValueError("proration must be actual_calendar_days or none")
    if zone_of(policy.billing_timezone) is None:
        raise ValueError("billing_timezone must be a known IANA time zone")
    return policy


def _zone(policy: FundedChargePolicyV1) -> ZoneInfo:
    zone = zone_of(policy.billing_timezone)
    if zone is None:  # unreachable behind _read_policy
        raise ValueError("billing_timezone must be a known IANA time zone")
    return zone


def _length_prefixed(*parts: str) -> str:
    return "".join(f"{len(part)}:{part}" for part in parts)


def charge_ref_for(key: RatedChargeKeyV1) -> str:
    """The canonical length-prefixed identity of a charge.

    Derived by flows from the key, so two different keys can never share a
    reference. A reference over 1024 bytes is refused and nothing is written.
    """
    ref = _length_prefixed(
        key.source_system, key.source_kind, key.source_fact_id, key.source_fact_version
    )
    if len(ref.encode("utf-8")) > _CHARGE_REF_MAX_BYTES:
        raise ValueError("charge identity exceeds 1024 bytes")
    return ref


def key_for_charge_ref(charge_ref: str) -> RatedChargeKeyV1:
    """Invert ``charge_ref_for`` (the reference is length-prefixed, so exact)."""
    parts: list[str] = []
    position = 0
    while position < len(charge_ref):
        colon = charge_ref.index(":", position)
        length = int(charge_ref[position:colon])
        parts.append(charge_ref[colon + 1 : colon + 1 + length])
        position = colon + 1 + length
    if len(parts) != 4:
        raise ValueError("not a charge reference")
    return RatedChargeKeyV1(parts[0], parts[1], parts[2], parts[3])


def instance_id_for(scope: TenantScope, charge_ref: str) -> UUID:
    """The deterministic instance id of a charge, so owner references are stable."""
    return uuid5(_INSTANCE_NAMESPACE, f"{_tenant(scope)}:{charge_ref}")


def is_funded_charge_replay_conflict(error: BaseException) -> bool:
    """True if ``error`` is the duplicate-charge violation (a replay).

    Matches the constraint name the driver reports and nothing else.
    """
    if not isinstance(error, IntegrityError):
        return False
    diag = getattr(error.orig, "diag", None)
    return getattr(diag, "constraint_name", None) in REPLAY_CONSTRAINTS


def _check_code(code: str) -> str:
    if not code or len(code) > 80 or any(c not in _CODE_CHARACTERS for c in code):
        raise ValueError("reason code must be a short machine code")
    return code


@dataclass(frozen=True, slots=True)
class _Ctx:
    db: Session
    scope: TenantScope
    tenant_id: UUID
    ports: FundedChargePorts
    policy: FundedChargePolicyV1
    actor_ref: str | None = None
    #: Newest-credit instants read this call, keyed by (window start, currency).
    credit_dates: dict[tuple[datetime, str], datetime | None] = field(
        default_factory=dict
    )

    @property
    def policy_version(self) -> str:
        return self.policy.version


# --------------------------------------------------------------------------
# Reads of our own table
# --------------------------------------------------------------------------
def _find(
    db: Session, tenant_id: UUID, *, charge_ref: str, lock: bool = False
) -> FundedChargeInstance | None:
    statement = select(FundedChargeInstance).where(
        FundedChargeInstance.tenant_id == tenant_id,
        FundedChargeInstance.charge_ref == charge_ref,
    )
    if lock:
        statement = statement.with_for_update()
    return db.execute(
        statement.execution_options(populate_existing=True)
    ).scalar_one_or_none()


def _find_id(
    db: Session, tenant_id: UUID, instance_id: UUID, *, lock: bool = False
) -> FundedChargeInstance | None:
    statement = select(FundedChargeInstance).where(
        FundedChargeInstance.tenant_id == tenant_id,
        FundedChargeInstance.id == instance_id,
    )
    if lock:
        statement = statement.with_for_update()
    return db.execute(
        statement.execution_options(populate_existing=True)
    ).scalar_one_or_none()


def _after(cursor: FundedChargeCursorV1) -> Any:
    return or_(
        FundedChargeInstance.created_at > cursor.created_at,
        and_(
            FundedChargeInstance.created_at == cursor.created_at,
            FundedChargeInstance.id > cursor.id,
        ),
    )


def _has_pending(db: Session, tenant_id: UUID, account_id: UUID) -> bool:
    return bool(
        db.scalar(
            select(
                exists().where(
                    FundedChargeInstance.tenant_id == tenant_id,
                    FundedChargeInstance.billing_account_id == account_id,
                    FundedChargeInstance.state == FundedChargeState.PENDING.value,
                )
            )
        )
    )


def _outcome(row: FundedChargeInstance, *, replayed: bool) -> FundedChargeOutcomeV1:
    return FundedChargeOutcomeV1(
        instance_id=row.id,
        state=FundedChargeState(row.state),
        reason_code=row.reason_code,
        version=row.version,
        billing_account_id=row.billing_account_id,
        document_id=row.document_id,
        replayed=replayed,
    )


def _view(row: FundedChargeInstance) -> FundedChargeInstanceV1:
    return FundedChargeInstanceV1(
        id=row.id,
        charge_ref=row.charge_ref,
        state=FundedChargeState(row.state),
        reason_code=row.reason_code,
        policy_version=row.policy_version,
        definition_version=row.definition_version,
        version=row.version,
        billing_account_id=row.billing_account_id,
        service_ref=row.service_ref,
        obligation_id=row.obligation_id,
        document_id=row.document_id,
        credit_draw_id=row.credit_draw_id,
        funding_settlement_id=row.funding_settlement_id,
        actor_ref=row.actor_ref,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _text(value: UUID | None) -> str | None:
    return None if value is None else str(value)


# --------------------------------------------------------------------------
# Events (caller's transaction; references only)
# --------------------------------------------------------------------------
def _emit(db: Session, row: FundedChargeInstance) -> None:
    state = FundedChargeState(row.state)
    if state is FundedChargeState.DISCHARGED:
        event_type = FUNDED_CHARGE_DISCHARGED_EVENT_TYPE
    elif state is FundedChargeState.ATTENTION_REQUIRED:
        event_type = FUNDED_CHARGE_ATTENTION_EVENT_TYPE
    else:
        return
    payload: dict[str, object] = {
        "instance_id": str(row.id),
        "charge_ref": row.charge_ref,
        "billing_account_id": str(row.billing_account_id),
        "service_ref": row.service_ref,
        "state": row.state,
        "reason_code": row.reason_code,
        "version": row.version,
        "obligation_id": _text(row.obligation_id),
        "document_id": _text(row.document_id),
        "credit_draw_id": _text(row.credit_draw_id),
        "funding_settlement_id": _text(row.funding_settlement_id),
    }
    enqueue_event(
        db,
        tenant_id=row.tenant_id,
        event_type=event_type,
        payload=payload,
        correlation_id=f"funded-charge:{row.id}:{row.version}",
    )


def _enqueue_reevaluate(
    db: Session,
    tenant_id: UUID,
    account_id: UUID,
    settlement_id: UUID,
    after: FundedChargeCursorV1 | None,
) -> None:
    payload: dict[str, object] = {
        "billing_account_id": str(account_id),
        "settlement_id": str(settlement_id),
        "after_created_at": None if after is None else after.created_at.isoformat(),
        "after_id": None if after is None else str(after.id),
    }
    marker = "start" if after is None else str(after.id)
    enqueue_event(
        db,
        tenant_id=tenant_id,
        event_type=FUNDED_CHARGE_REEVALUATE_EVENT_TYPE,
        payload=payload,
        correlation_id=f"funded-charge:reevaluate:{settlement_id}:{marker}",
    )


def _enqueue_terms_recheck(
    db: Session,
    tenant_id: UUID,
    account_id: UUID,
    at: datetime,
    after: FundedChargeCursorV1 | None,
) -> None:
    payload: dict[str, object] = {
        "billing_account_id": str(account_id),
        "at": at.isoformat(),
        "after_created_at": None if after is None else after.created_at.isoformat(),
        "after_id": None if after is None else str(after.id),
    }
    marker = "start" if after is None else str(after.id)
    enqueue_event(
        db,
        tenant_id=tenant_id,
        event_type=FUNDED_CHARGE_TERMS_RECHECK_EVENT_TYPE,
        payload=payload,
        correlation_id=f"funded-charge:terms-recheck:{account_id}:{at.isoformat()}:{marker}",
    )


def _enqueue_credit_recheck(
    db: Session,
    tenant_id: UUID,
    account_id: UUID,
    after: FundedChargeCursorV1 | None,
) -> None:
    payload: dict[str, object] = {
        "billing_account_id": str(account_id),
        "after_created_at": None if after is None else after.created_at.isoformat(),
        "after_id": None if after is None else str(after.id),
    }
    marker = "start" if after is None else str(after.id)
    enqueue_event(
        db,
        tenant_id=tenant_id,
        event_type=FUNDED_CHARGE_CREDIT_RECHECK_EVENT_TYPE,
        payload=payload,
        correlation_id=f"funded-charge:credit-recheck:{account_id}:{marker}",
    )


# --------------------------------------------------------------------------
# The service lock and the access signal
# --------------------------------------------------------------------------
def _service_state(db: Session, tenant_id: UUID, service_ref: str) -> tuple[bool, int]:
    """``(active, source_version)`` of a service, read under its lock."""
    active_rows, version_sum = db.execute(
        select(
            func.count(FundedChargeInstance.id).filter(
                FundedChargeInstance.state.in_(_DEFAULT_ACCESS_STATES)
            ),
            func.coalesce(func.sum(FundedChargeInstance.version + 1), 0),
        ).where(
            FundedChargeInstance.tenant_id == tenant_id,
            FundedChargeInstance.service_ref == service_ref,
        )
    ).one()
    return active_rows > 0, int(version_sum)


def _begin_service(ctx: _Ctx, service_ref: str | None) -> bool:
    """Take the service lock; return whether the service is off before a write."""
    if service_ref is None:
        return False
    name = _length_prefixed("money_flows:service", str(ctx.tenant_id), service_ref)
    ctx.db.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtextextended(literal(name, Text()), literal(0, BigInteger()))
            )
        )
    )
    return _service_state(ctx.db, ctx.tenant_id, service_ref)[0]


def _signal(
    ctx: _Ctx,
    row: FundedChargeInstance,
    was_active: bool,
    observed_at: datetime | None,
) -> None:
    """Emit the PREPAID_DEPLETED observation if the service's state changed."""
    if row.service_ref is None:
        return
    active, source_version = _service_state(ctx.db, ctx.tenant_id, row.service_ref)
    if active == was_active:
        return
    if observed_at is None:
        # No charge to date it by: the database time of the transition.
        ctx.db.refresh(row)
        observed_at = row.updated_at
    observation = RecordAccessInputV2(
        service_reference=row.service_ref,
        signal=AccessSignal.PREPAID_DEPLETED,
        active=active,
        source_reference=f"money_flows:funded_charge:{row.id}",
        observed_at=observed_at,
        source_version=source_version,
        policy_version=ctx.policy_version,
    )
    payload: dict[str, object] = {
        "service_reference": observation.service_reference,
        "signal": observation.signal.value,
        "active": observation.active,
        "source_reference": observation.source_reference,
        "observed_at": observation.observed_at.isoformat(),
        "source_version": observation.source_version,
        "policy_version": observation.policy_version,
    }
    enqueue_event(
        ctx.db,
        tenant_id=ctx.tenant_id,
        event_type=FUNDED_CHARGE_ACCESS_EVENT_TYPE,
        payload=payload,
        correlation_id=f"funded-charge:access:{row.id}:{source_version}",
    )


def _observed(
    row: FundedChargeInstance, charge: RatedChargeV1 | None, issue_at: datetime
) -> datetime | None:
    if FundedChargeState(row.state) is FundedChargeState.DISCHARGED:
        return issue_at
    return None if charge is None else charge.due_at


# --------------------------------------------------------------------------
# Persistence of the outcome
# --------------------------------------------------------------------------
def _write(
    ctx: _Ctx,
    row: FundedChargeInstance | None,
    *,
    charge_ref: str,
    charge: RatedChargeV1 | None,
    state: FundedChargeState,
    reason_code: str | None,
    obligation_id: UUID | None = None,
    document_id: UUID | None = None,
    credit_draw_id: UUID | None = None,
    funding_settlement_id: UUID | None = None,
) -> FundedChargeInstance:
    """Insert the instance (``row`` None) or advance it, then emit its event."""
    if row is None:
        if charge is None:
            raise ValueError("a new instance needs the charge it is for")
        row = FundedChargeInstance(
            id=instance_id_for(ctx.scope, charge_ref),
            tenant_id=ctx.tenant_id,
            state=state.value,
            reason_code=reason_code,
            policy_version=ctx.policy_version,
            definition_version=FUNDED_CHARGE_DEFINITION_VERSION,
            version=0,
            actor_ref=ctx.actor_ref,
            charge_ref=charge_ref,
            billing_account_id=charge.billing_account_id,
            service_ref=charge.service_ref,
            obligation_id=obligation_id,
            document_id=document_id,
            credit_draw_id=credit_draw_id,
            funding_settlement_id=funding_settlement_id,
        )
        ctx.db.add(row)
    else:
        if not funded_charge_transition_allowed(FundedChargeState(row.state), state):
            raise FundedChargeRefused("transition_not_allowed")
        row.state = state.value
        row.reason_code = reason_code
        row.policy_version = ctx.policy_version
        row.obligation_id = obligation_id
        row.document_id = document_id
        row.credit_draw_id = credit_draw_id
        if funding_settlement_id is not None:
            row.funding_settlement_id = funding_settlement_id
        if ctx.actor_ref is not None:
            row.actor_ref = ctx.actor_ref
        row.version = row.version + 1
    ctx.db.flush()
    _emit(ctx.db, row)
    return row


# --------------------------------------------------------------------------
# Owner steps
# --------------------------------------------------------------------------
def _terms(
    db: Session, scope: TenantScope, account_id: UUID, at: datetime
) -> AccountTermsV1:
    """The Billing account terms in force at ``at``."""
    return get_effective_account_terms(db, scope=scope, account_id=account_id, at=at)


def _is_prepaid(
    db: Session, scope: TenantScope, account_id: UUID, at: datetime
) -> bool:
    """The contract type is a Billing account term, read at the period start."""
    return _terms(db, scope, account_id, at).contract_type.value == _PREPAID


def _enclosing(charge: RatedChargeV1) -> tuple[datetime, datetime] | None:
    """The enclosing period of a prorated charge; None for an ordinary one."""
    if charge.period_starts_at is None or charge.period_ends_at is None:
        return None
    return charge.period_starts_at, charge.period_ends_at


def _enclosing_valid(charge: RatedChargeV1) -> bool:
    """Both ends or neither, timezone-aware, and a forward interval."""
    ends = (charge.period_starts_at, charge.period_ends_at)
    if ends == (None, None):
        return True
    if None in ends:
        return False
    start, end = charge.period_starts_at, charge.period_ends_at
    return bool(
        start is not None
        and end is not None
        and start.utcoffset() is not None
        and end.utcoffset() is not None
        and start < end
    )


def _elapsed(
    charge: RatedChargeV1, issue_at: datetime, policy: FundedChargePolicyV1
) -> bool:
    """Nothing left to bill: a prorated charge whose enclosing period is over."""
    enclosing = _enclosing(charge)
    if enclosing is None or not _enclosing_valid(charge):
        return False
    return (
        issue_at >= enclosing[1] or day_span(issue_at, enclosing[1], _zone(policy)) < 1
    )


def _amount_at(
    charge: RatedChargeV1, issue_at: datetime, policy: FundedChargePolicyV1
) -> Money | None:
    """The amount due at ``issue_at``: ``total``, or its share of the days left.

    A pure function of (total, enclosing period, ``issue_at``, policy). None when
    the charge cannot be priced (a malformed or elapsed enclosing period, or
    proration off): F2 then refuses the command, or withdraws an elapsed charge,
    and never guesses an amount. The policy is already validated by
    ``_read_policy``.
    """
    enclosing = _enclosing(charge)
    if enclosing is None:
        return charge.total if _enclosing_valid(charge) else None
    zone = _zone(policy)
    if (
        policy.proration != _PRORATE
        or not _enclosing_valid(charge)
        or not enclosing[0] <= issue_at < enclosing[1]
    ):
        return None
    used = day_span(issue_at, enclosing[1], zone)
    whole = day_span(enclosing[0], enclosing[1], zone)
    if used < 1 or whole < 1:
        return None
    return prorated(charge.total, used, whole)


def _fits(
    ctx: _Ctx, charge: RatedChargeV1, account_id: UUID, issue_at: datetime
) -> bool:
    """Whether the account's spendable credit covers the amount due now.

    ``account_id`` is the instance's own account, never the port's. Reading the
    availability also takes the Billing account lock. A charge that cannot be
    priced "fits": the discharge refuses or withdraws it, so it is never held
    pending behind an amount nobody can compute.
    """
    availability = get_funding_availability(
        ctx.db,
        scope=ctx.scope,
        request=GetFundingAvailabilityV1(ctx.scope, account_id),
    )
    amount = _amount_at(charge, issue_at, ctx.policy)
    return amount is None or bool(availability.unrestricted >= amount)


def _command_matches(
    ctx: _Ctx,
    command: IssueRatedInvoiceV3,
    key: RatedChargeKeyV1,
    account_id: UUID,
    charge: RatedChargeV1,
    issue_at: datetime,
) -> bool:
    """The port's invoice command must be this charge, for this period.

    The period length is the terms period at ``issue_at``. A prorated charge is
    checked against the terms at its own enclosing start (immutable: terms cannot
    be back-dated).
    """
    obligation = command.obligation
    period = obligation.service_period
    policy = ctx.policy
    zone = _zone(policy)
    amount = _amount_at(charge, issue_at, policy)
    if amount is None or command.timezone != policy.billing_timezone:
        return False
    enclosing = _enclosing(charge)
    if enclosing is None:
        terms = _terms(ctx.db, ctx.scope, account_id, issue_at)
        expected_end = period_end(issue_at, terms.period.value, zone)
    else:
        start_terms = _terms(ctx.db, ctx.scope, account_id, enclosing[0])
        expected_end = enclosing[1]
        if period_end(enclosing[0], start_terms.period.value, zone) != expected_end:
            return False
    return bool(
        (
            obligation.source_system,
            obligation.source_kind,
            obligation.source_fact_id,
            obligation.source_fact_version,
        )
        == (
            key.source_system,
            key.source_kind,
            key.source_fact_id,
            key.source_fact_version,
        )
        and obligation.billing_account_id == account_id == charge.billing_account_id
        and obligation.total_amount == amount
        and period.status.value == _VERIFIED
        and period.starts_at == issue_at
        and expected_end is not None
        and period.ends_at == expected_end
        and obligation.collection_timing == _ADVANCE
        and command.due_at == issue_at
    )


def _gate(
    ctx: _Ctx,
    row: FundedChargeInstance,
    charge: RatedChargeV1,
    *,
    charge_ref: str,
    issue_at: datetime,
    funding_settlement_id: UUID | None,
) -> FundedChargeInstance | None:
    """Decide what needs no funds before the funds are looked at (lock held).

    A charge the port reports under another key or account is not this
    instance's charge: attention, no owner write, and no second account is
    locked. An account that is no longer prepaid at the period start withdraws
    the row whatever the balance. None means: go on to the funds check.
    """
    if (
        charge.key != key_for_charge_ref(charge_ref)
        or charge.billing_account_id != row.billing_account_id
        or not _enclosing_valid(charge)
    ):
        return _write(
            ctx,
            row,
            charge_ref=charge_ref,
            charge=charge,
            state=FundedChargeState.ATTENTION_REQUIRED,
            reason_code=_MISMATCH,
            funding_settlement_id=funding_settlement_id,
        )
    if not _is_prepaid(ctx.db, ctx.scope, row.billing_account_id, issue_at):
        return _write(
            ctx,
            row,
            charge_ref=charge_ref,
            charge=charge,
            state=FundedChargeState.WITHDRAWN,
            reason_code="contract_type_changed",
        )
    if _elapsed(charge, issue_at, ctx.policy):
        return _write(
            ctx,
            row,
            charge_ref=charge_ref,
            charge=charge,
            state=FundedChargeState.WITHDRAWN,
            reason_code=_ELAPSED,
        )
    return None


def _discharge(
    ctx: _Ctx,
    row: FundedChargeInstance | None,
    charge: RatedChargeV1,
    *,
    charge_ref: str,
    issue_at: datetime,
    funding_settlement_id: UUID | None,
) -> FundedChargeInstance:
    """Issue the invoice and pay it from credit, or record why not (lock held)."""
    key = key_for_charge_ref(charge_ref)
    account_id = charge.billing_account_id if row is None else row.billing_account_id
    if not _is_prepaid(ctx.db, ctx.scope, account_id, issue_at):
        if row is None:
            raise FundedChargeRefused("not_prepaid")
        return _write(
            ctx,
            row,
            charge_ref=charge_ref,
            charge=charge,
            state=FundedChargeState.WITHDRAWN,
            reason_code="contract_type_changed",
        )
    if _elapsed(charge, issue_at, ctx.policy):
        if row is None:
            raise FundedChargeRefused(_ELAPSED)
        return _write(
            ctx,
            row,
            charge_ref=charge_ref,
            charge=charge,
            state=FundedChargeState.WITHDRAWN,
            reason_code=_ELAPSED,
        )
    try:
        command: IssueRatedInvoiceV3 = ctx.ports.charges.issue_command(
            ctx.db, scope=ctx.scope, key=key, issue_at=issue_at
        )
    except SQLAlchemyError:
        # A database fault is not a bad row: redeliver, do not park the row.
        raise
    except Exception as exc:
        if row is None:
            raise
        raise FundedChargeStepFailed(row.id, _SOURCE_ERROR) from exc
    if not _command_matches(ctx, command, key, account_id, charge, issue_at):
        return _write(
            ctx,
            row,
            charge_ref=charge_ref,
            charge=charge,
            state=FundedChargeState.ATTENTION_REQUIRED,
            reason_code=_MISMATCH,
            funding_settlement_id=funding_settlement_id,
        )
    instance_id = instance_id_for(ctx.scope, charge_ref)
    issued: IssuedRatedInvoiceV1 = issue_rated_invoice(
        ctx.db,
        scope=ctx.scope,
        command=command,
        obligation_sources=ctx.ports.obligation_sources,
        numbering=ctx.ports.numbering,
    )
    exposure = get_document_exposure(
        ctx.db, scope=ctx.scope, document_id=issued.invoice_id
    )
    group = apply_available_credit(
        ctx.db,
        scope=ctx.scope,
        command=ApplyAvailableCreditCommand(
            billing_account_id=account_id,
            document_id=issued.invoice_id,
            amount=exposure.collectible_receivable,
            occurred_at=issue_at,
            source_ref=f"money-flows:funded-charge:{instance_id}:{issued.invoice_id}",
        ),
    )
    return _write(
        ctx,
        row,
        charge_ref=charge_ref,
        charge=charge,
        state=FundedChargeState.DISCHARGED,
        reason_code=None,
        obligation_id=issued.obligation_id,
        document_id=issued.invoice_id,
        credit_draw_id=group.id,
        funding_settlement_id=funding_settlement_id,
    )


def _step_code(error: BaseException) -> str:
    if isinstance(error, BillingError):
        return _check_code(error.code)
    return "idempotency_conflict"


# --------------------------------------------------------------------------
# Public commands
# --------------------------------------------------------------------------
def start_funded_charge(
    db: Session,
    *,
    scope: TenantScope,
    command: StartFundedChargeV1,
    ports: FundedChargePorts,
) -> FundedChargeOutcomeV1:
    """Start one charge at its due time, in the caller's transaction.

    See the module docstring: replay first, no row for an unknown or non-prepaid
    charge (the contract type is read again after the account lock is taken), the
    balance covering the whole charge discharges it at ``due_at``, otherwise it is
    held ``pending``. An owner exception propagates.
    """
    tenant_id = _tenant(scope)
    charge_ref = charge_ref_for(command.key)
    existing = _find(db, tenant_id, charge_ref=charge_ref)
    if existing is not None:
        return _outcome(existing, replayed=True)
    charge = ports.charges.read(db, scope=scope, key=command.key)
    if charge is None:
        raise FundedChargeRefused("charge_not_found")
    # Fast path, no lock and no funds read for a postpaid account; the check
    # under the lock below is the authoritative one.
    if not _is_prepaid(db, scope, charge.billing_account_id, charge.due_at):
        raise FundedChargeRefused("not_prepaid")
    ctx = _Ctx(db, scope, tenant_id, ports, _read_policy(db, scope, ports))
    funded = _fits(ctx, charge, charge.billing_account_id, charge.due_at)  # lock
    winner = _find(db, tenant_id, charge_ref=charge_ref)
    if winner is not None:
        # A duplicate that won the race has committed: its row is the answer.
        return _outcome(winner, replayed=True)
    # The terms are read only now, under the account lock: a terms switch (and
    # its re-check) that committed while this start waited is seen, so a pending
    # row is never inserted behind a re-check that has already run.
    if not _is_prepaid(db, scope, charge.billing_account_id, charge.due_at):
        raise FundedChargeRefused("not_prepaid")
    was_active = _begin_service(ctx, charge.service_ref)
    if funded:
        row = _discharge(
            ctx,
            None,
            charge,
            charge_ref=charge_ref,
            issue_at=charge.due_at,
            funding_settlement_id=None,
        )
    else:
        row = _write(
            ctx,
            None,
            charge_ref=charge_ref,
            charge=charge,
            state=FundedChargeState.PENDING,
            reason_code=None,
        )
    _signal(ctx, row, was_active, _observed(row, charge, charge.due_at))
    return _outcome(row, replayed=False)


def _reevaluate_funded(
    ctx: _Ctx,
    row: FundedChargeInstance,
    funded_at: Callable[[RatedChargeV1], datetime],
    settlement: Any | None,
) -> FundedChargeInstance | None:
    """Re-examine one pending row against new funds; None if it stays pending.

    ``funded_at(charge)`` is the instant the funds reached the account (a
    settlement's ``observed_at``, or the newest credit line for GAP-L1); the
    period starts at ``max(due_at, funded_at(charge))``. ``settlement`` is the
    funding settlement (its id is recorded on the row), or None for GAP-L1.
    """
    key = key_for_charge_ref(row.charge_ref)
    try:
        charge = ctx.ports.charges.read(ctx.db, scope=ctx.scope, key=key)
    except SQLAlchemyError:
        # A database fault is not a bad row: redeliver, do not park the row.
        raise
    except Exception as exc:
        raise FundedChargeStepFailed(row.id, _SOURCE_ERROR) from exc
    was_active = _begin_service(ctx, row.service_ref)
    if charge is None:
        _write(
            ctx,
            row,
            charge_ref=row.charge_ref,
            charge=None,
            state=FundedChargeState.WITHDRAWN,
            reason_code="charge_withdrawn",
        )
        _signal(ctx, row, was_active, None)
        return row
    funding_id = None if settlement is None else settlement.settlement_id
    issue_at = max(charge.due_at, funded_at(charge))
    decided = _gate(
        ctx,
        row,
        charge,
        charge_ref=row.charge_ref,
        issue_at=issue_at,
        funding_settlement_id=funding_id,
    )
    if decided is None and not _fits(ctx, charge, row.billing_account_id, issue_at):
        return None
    if decided is not None:
        _signal(ctx, row, was_active, _observed(row, charge, issue_at))
        return row
    _discharge(
        ctx,
        row,
        charge,
        charge_ref=row.charge_ref,
        issue_at=issue_at,
        funding_settlement_id=funding_id,
    )
    _signal(ctx, row, was_active, _observed(row, charge, issue_at))
    return row


def _reevaluate(
    ctx: _Ctx, row: FundedChargeInstance, settlement: Any
) -> FundedChargeInstance | None:
    """Re-examine one pending row against a settlement's funds (F1 and F2)."""
    return _reevaluate_funded(
        ctx, row, lambda _charge: settlement.observed_at, settlement
    )


def on_settlement_settled(
    db: Session,
    *,
    scope: TenantScope,
    event: SettledFundsV1,
    ports: FundedChargePorts,
) -> tuple[FundedChargeOutcomeV1, ...]:
    """Re-evaluate the account's pending charges against new funds.

    Oldest first, whole charges only, skipping any that does not fit. At most
    ``reevaluation_batch_cap`` rows are examined per call; when more remain a
    ``reevaluate.v1`` event resumes after the last one examined, so nothing is
    dropped and skipped rows are not examined again by the chain. Billing
    refuses a settlement that does not belong to the account.
    """
    tenant_id = _tenant(scope)
    policy = _read_policy(db, scope, ports)
    cap = policy.reevaluation_batch_cap
    if cap < 1:
        raise ValueError("reevaluation_batch_cap must be at least 1")
    settlement = get_confirmed_settlement(
        db,
        scope=scope,
        request=GetConfirmedSettlementV1(
            scope, event.billing_account_id, event.settlement_id
        ),
    )
    ctx = _Ctx(db, scope, tenant_id, ports, policy)
    statement = select(FundedChargeInstance).where(
        FundedChargeInstance.tenant_id == tenant_id,
        FundedChargeInstance.billing_account_id == event.billing_account_id,
        FundedChargeInstance.state == FundedChargeState.PENDING.value,
    )
    if event.after is not None:
        statement = statement.where(_after(event.after))
    rows = list(
        db.execute(
            statement.order_by(FundedChargeInstance.created_at, FundedChargeInstance.id)
            .limit(cap + 1)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalars()
    )
    batch = rows[:cap]
    changed: list[FundedChargeOutcomeV1] = []
    for row in batch:
        try:
            result = _reevaluate(ctx, row, settlement)
        except (BillingError, IdempotencyConflict) as exc:
            raise FundedChargeStepFailed(row.id, _step_code(exc)) from exc
        if result is not None:
            changed.append(_outcome(result, replayed=False))
    if len(rows) > cap:
        last = batch[-1]
        _enqueue_reevaluate(
            db,
            tenant_id,
            event.billing_account_id,
            event.settlement_id,
            FundedChargeCursorV1(last.created_at, last.id),
        )
    return tuple(changed)


def on_account_credit_changed(
    db: Session,
    *,
    scope: TenantScope,
    request: AccountCreditChangedV1,
    ports: FundedChargePorts,
) -> CreditRecheckOutcomeV1:
    """GAP-L1: credit may have reached the account; pay its pending charges.

    Routed by the assembly for every tenant ``billing.receivable.position.v2``
    and for ``money_flows.funded_charge.credit_recheck.v1``. The payload supplies
    only the account; everything else is re-read from Billing, so a mis-routed
    event causes at most an early, harmless re-check.

    1. No pending row: return ``no_pending_charges`` with no lock (safe: a start
       holds the account lock from its funds read to its commit, and every credit
       posting locks the account, so they serialise).
    2. Take the Billing account lock first (as every F2 command does); no
       unrestricted credit returns ``no_balance``.
    3. Lock the pending rows oldest first (``reevaluation_batch_cap`` + 1) and
       run each through the same re-evaluation as a settlement, dated
       ``max(due_at, newest credit line in [due_at, 9999))`` read from Billing's
       account statement (``issue_at = due_at`` when there is none). Whole
       charges only; a misfit is skipped. More rows than the cap enqueue
       ``credit_recheck.v1`` after the last one examined.

    A technical fault (an unreadable statement) raises and writes nothing; the
    caller rolls back and event delivery retries. An owner refusal on a covered
    charge raises ``FundedChargeStepFailed`` as in ``on_settlement_settled``; the
    transport then calls ``record_funded_charge_attention(...,
    continue_credit_recheck=True)``. There is no new source reference: the
    pending to discharged change under the row lock is the exactly-once guard, so
    a replay finds nothing pending.
    """
    tenant_id = _tenant(scope)
    account_id = request.billing_account_id

    def done(
        changed: tuple[FundedChargeOutcomeV1, ...] = (), reason: str | None = None
    ) -> CreditRecheckOutcomeV1:
        return CreditRecheckOutcomeV1(account_id, changed, reason)

    if not _has_pending(db, tenant_id, account_id):
        return done(reason="no_pending_charges")
    policy = _read_policy(db, scope, ports)
    cap = policy.reevaluation_batch_cap
    if cap < 1:
        raise ValueError("reevaluation_batch_cap must be at least 1")
    availability = get_funding_availability(
        db, scope=scope, request=GetFundingAvailabilityV1(scope, account_id)
    )  # account lock
    if not availability.unrestricted.is_positive:
        return done(reason="no_balance")
    ctx = _Ctx(db, scope, tenant_id, ports, policy)
    statement = select(FundedChargeInstance).where(
        FundedChargeInstance.tenant_id == tenant_id,
        FundedChargeInstance.billing_account_id == account_id,
        FundedChargeInstance.state == FundedChargeState.PENDING.value,
    )
    if request.after is not None:
        statement = statement.where(_after(request.after))
    rows = list(
        db.execute(
            statement.order_by(FundedChargeInstance.created_at, FundedChargeInstance.id)
            .limit(cap + 1)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalars()
    )
    if not rows:
        return done(reason="no_pending_charges")

    def funded_at(charge: RatedChargeV1) -> datetime:
        start = charge.due_at
        memo_key = (start, charge.total.currency.code)
        if memo_key not in ctx.credit_dates:
            try:
                ctx.credit_dates[memo_key] = newest_credit_at(
                    db, scope, account_id, charge.total.currency, start
                )
            except BillingError as exc:
                # A statement read is a technical fault, never an owner refusal
                # on a charge: raise, write nothing, let delivery retry.
                raise BalancePassFailed("credit_dating_failed") from exc
        newest = ctx.credit_dates[memo_key]
        return start if newest is None else newest

    batch = rows[:cap]
    changed: list[FundedChargeOutcomeV1] = []
    for row in batch:
        try:
            result = _reevaluate_funded(ctx, row, funded_at, None)
        except (BillingError, IdempotencyConflict) as exc:
            raise FundedChargeStepFailed(row.id, _step_code(exc)) from exc
        if result is not None:
            changed.append(_outcome(result, replayed=False))
    if len(rows) > cap:
        last = batch[-1]
        _enqueue_credit_recheck(
            db, tenant_id, account_id, FundedChargeCursorV1(last.created_at, last.id)
        )
    if changed:
        return done(tuple(changed))
    return done(reason="nothing_fits")


def _recheck_terms(
    ctx: _Ctx, row: FundedChargeInstance, at: datetime
) -> FundedChargeInstance | None:
    """Re-check one pending row against the terms at ``max(due_at, at)``.

    Never pays or issues: a row the gate does not decide is left unchanged
    (None), for the next settlement or retry.
    """
    key = key_for_charge_ref(row.charge_ref)
    try:
        charge = ctx.ports.charges.read(ctx.db, scope=ctx.scope, key=key)
    except SQLAlchemyError:
        # A database fault is not a bad row: redeliver, do not park the row.
        raise
    except Exception as exc:
        raise FundedChargeStepFailed(row.id, _SOURCE_ERROR) from exc
    was_active = _begin_service(ctx, row.service_ref)
    if charge is None:
        _write(
            ctx,
            row,
            charge_ref=row.charge_ref,
            charge=None,
            state=FundedChargeState.WITHDRAWN,
            reason_code="charge_withdrawn",
        )
        _signal(ctx, row, was_active, None)
        return row
    issue_at = max(charge.due_at, at)
    decided = _gate(
        ctx,
        row,
        charge,
        charge_ref=row.charge_ref,
        issue_at=issue_at,
        funding_settlement_id=None,
    )
    if decided is None:
        return None
    _signal(ctx, row, was_active, issue_at)
    return row


def reevaluate_account_terms(
    db: Session,
    *,
    scope: TenantScope,
    request: AccountTermsChangedV1,
    ports: FundedChargePorts,
) -> tuple[FundedChargeOutcomeV1, ...]:
    """Re-check an account's pending charges after its contract terms changed.

    Each pending row is checked at ``max(due_at, request.at)`` through the same
    gate as settlement re-evaluation: a row whose account is no longer prepaid is
    withdrawn (``contract_type_changed``) and the service resumes; a row whose
    charge the source no longer holds is withdrawn (``charge_withdrawn``); a
    mismatch goes to attention; a prorated row whose enclosing period is over at
    that instant is withdrawn (``period_elapsed``). Nothing is ever paid or
    issued here, and
    ``attention_required`` rows are not touched. Idempotent: a repeat finds no
    pending row to change and returns ``()``. The caller must not call before
    ``request.at`` has passed (flows reads no clock). Locks as
    ``on_settlement_settled``; at most ``reevaluation_batch_cap`` rows are
    examined and a ``terms_recheck.v1`` event resumes after the last.
    """
    tenant_id = _tenant(scope)
    at = request.at
    if at.tzinfo is None or at.utcoffset() is None:
        raise FundedChargeRefused("at_not_timezone_aware")
    policy = _read_policy(db, scope, ports)
    cap = policy.reevaluation_batch_cap
    if cap < 1:
        raise ValueError("reevaluation_batch_cap must be at least 1")
    account_id = request.billing_account_id
    get_funding_availability(
        db,
        scope=scope,
        request=GetFundingAvailabilityV1(scope, account_id),
    )  # account lock
    ctx = _Ctx(db, scope, tenant_id, ports, policy)
    statement = select(FundedChargeInstance).where(
        FundedChargeInstance.tenant_id == tenant_id,
        FundedChargeInstance.billing_account_id == account_id,
        FundedChargeInstance.state == FundedChargeState.PENDING.value,
    )
    if request.after is not None:
        statement = statement.where(_after(request.after))
    rows = list(
        db.execute(
            statement.order_by(FundedChargeInstance.created_at, FundedChargeInstance.id)
            .limit(cap + 1)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalars()
    )
    batch = rows[:cap]
    changed: list[FundedChargeOutcomeV1] = []
    for row in batch:
        try:
            result = _recheck_terms(ctx, row, at)
        except (BillingError, IdempotencyConflict) as exc:
            raise FundedChargeStepFailed(row.id, _step_code(exc)) from exc
        if result is not None:
            changed.append(_outcome(result, replayed=False))
    if len(rows) > cap:
        last = batch[-1]
        _enqueue_terms_recheck(
            db,
            tenant_id,
            account_id,
            at,
            FundedChargeCursorV1(last.created_at, last.id),
        )
    return tuple(changed)


def record_funded_charge_attention(
    db: Session,
    *,
    scope: TenantScope,
    code: str,
    ports: FundedChargePorts,
    instance_id: UUID | None = None,
    key: RatedChargeKeyV1 | None = None,
    funding_settlement_id: UUID | None = None,
    actor_ref: str | None = None,
    continue_credit_recheck: bool = False,
) -> FundedChargeOutcomeV1:
    """Persist the attention row for an owner refusal, in a NEW transaction.

    Call it after the failed transaction rolled back, naming the instance or the
    charge key. It takes the account lock first, never touches a discharged or
    withdrawn row, and records an unchanged reason as a replay. When a funding
    settlement is named and the account still has pending rows, the continuation
    event is enqueued so they are not stranded behind the failed one. A step
    failure of ``on_account_credit_changed`` passes ``continue_credit_recheck=True``
    instead: the same, with ``credit_recheck.v1`` (there is no settlement to name).
    """
    tenant_id = _tenant(scope)
    code = _check_code(code)
    if (instance_id is None) == (key is None):
        raise ValueError("name exactly one of instance_id or key")
    charge: RatedChargeV1 | None = None
    if instance_id is not None:
        found = _find_id(db, tenant_id, instance_id)
        if found is None:
            raise FundedChargeRefused("instance_not_found")
        account_id = found.billing_account_id
        charge_ref = found.charge_ref
    else:
        if key is None:
            raise ValueError("name exactly one of instance_id or key")
        charge_ref = charge_ref_for(key)
        found = _find(db, tenant_id, charge_ref=charge_ref)
        if found is None:
            charge = ports.charges.read(db, scope=scope, key=key)
            if charge is None:
                raise FundedChargeRefused("charge_not_found")
            account_id = charge.billing_account_id
        else:
            account_id = found.billing_account_id
    availability = GetFundingAvailabilityV1(scope, account_id)
    get_funding_availability(db, scope=scope, request=availability)  # account lock
    row = _find(db, tenant_id, charge_ref=charge_ref, lock=True)
    ctx = _Ctx(
        db,
        scope,
        tenant_id,
        ports,
        _read_policy(db, scope, ports),
        actor_ref,
    )
    if row is not None:
        state = FundedChargeState(row.state)
        if state not in (
            FundedChargeState.PENDING,
            FundedChargeState.ATTENTION_REQUIRED,
        ) or (
            state is FundedChargeState.ATTENTION_REQUIRED
            and row.reason_code == code
            and funding_settlement_id in (None, row.funding_settlement_id)
        ):
            return _outcome(row, replayed=True)
    if row is not None:
        service_ref = row.service_ref
    else:
        service_ref = None if charge is None else charge.service_ref
    was_active = _begin_service(ctx, service_ref)
    row = _write(
        ctx,
        row,
        charge_ref=charge_ref,
        charge=charge,
        state=FundedChargeState.ATTENTION_REQUIRED,
        reason_code=code,
        funding_settlement_id=funding_settlement_id,
    )
    _signal(ctx, row, was_active, None if charge is None else charge.due_at)
    if funding_settlement_id is not None and _has_pending(db, tenant_id, account_id):
        _enqueue_reevaluate(db, tenant_id, account_id, funding_settlement_id, None)
    if continue_credit_recheck and _has_pending(db, tenant_id, account_id):
        _enqueue_credit_recheck(db, tenant_id, account_id, None)
    return _outcome(row, replayed=False)


def retry_funded_charge(
    db: Session,
    *,
    scope: TenantScope,
    instance_id: UUID,
    expected_version: int,
    actor_ref: str,
    ports: FundedChargePorts,
) -> FundedChargeOutcomeV1:
    """Retry an ``attention_required`` instance. No other state may be retried.

    Takes the account lock, then the row ``FOR UPDATE``, and checks
    ``expected_version`` before the charge source, the terms or any owner write
    is touched. The period starts at ``max(due_at, funding settlement
    observed_at)`` when a funding settlement is recorded, else at ``max(due_at,
    newest credit line since due_at)`` (``due_at`` when there is none). A funded
    charge is discharged; otherwise it returns to ``pending``.
    """
    tenant_id = _tenant(scope)
    if not actor_ref or not actor_ref.strip():
        raise FundedChargeRefused("actor_required")
    found = _find_id(db, tenant_id, instance_id)
    if found is None:
        raise FundedChargeRefused("instance_not_found")
    get_funding_availability(
        db,
        scope=scope,
        request=GetFundingAvailabilityV1(scope, found.billing_account_id),
    )  # account lock
    row = _find_id(db, tenant_id, instance_id, lock=True)
    if row is None:
        raise FundedChargeRefused("instance_not_found")
    if row.version != expected_version:
        raise FundedChargeRefused("stale_version")
    if FundedChargeState(row.state) is not FundedChargeState.ATTENTION_REQUIRED:
        raise FundedChargeRefused("retry_not_allowed")
    ctx = _Ctx(
        db,
        scope,
        tenant_id,
        ports,
        _read_policy(db, scope, ports),
        actor_ref,
    )
    try:
        charge = ports.charges.read(
            db, scope=scope, key=key_for_charge_ref(row.charge_ref)
        )
    except SQLAlchemyError:
        # A database fault is not a bad row: redeliver, do not park the row.
        raise
    except Exception as exc:
        raise FundedChargeStepFailed(row.id, _SOURCE_ERROR) from exc
    was_active = _begin_service(ctx, row.service_ref)
    if charge is None:
        _write(
            ctx,
            row,
            charge_ref=row.charge_ref,
            charge=None,
            state=FundedChargeState.WITHDRAWN,
            reason_code="charge_withdrawn",
        )
        _signal(ctx, row, was_active, None)
        return _outcome(row, replayed=False)
    issue_at = charge.due_at
    if row.funding_settlement_id is not None:
        settlement = get_confirmed_settlement(
            db,
            scope=scope,
            request=GetConfirmedSettlementV1(
                scope, row.billing_account_id, row.funding_settlement_id
            ),
        )
        issue_at = max(charge.due_at, settlement.observed_at)
    else:
        # No funding settlement (a credit re-check, or a start): date it at the
        # newest credit since the due time, so days the customer was cut off are
        # never billed (GAP-L1 Q1).
        newest = newest_credit_at(
            db, scope, row.billing_account_id, charge.total.currency, charge.due_at
        )
        if newest is not None:
            issue_at = max(charge.due_at, newest)
    decided = _gate(
        ctx,
        row,
        charge,
        charge_ref=row.charge_ref,
        issue_at=issue_at,
        funding_settlement_id=None,
    )
    if decided is not None:
        pass
    elif _fits(ctx, charge, row.billing_account_id, issue_at):
        _discharge(
            ctx,
            row,
            charge,
            charge_ref=row.charge_ref,
            issue_at=issue_at,
            funding_settlement_id=None,
        )
    else:
        _write(
            ctx,
            row,
            charge_ref=row.charge_ref,
            charge=charge,
            state=FundedChargeState.PENDING,
            reason_code=None,
        )
    _signal(ctx, row, was_active, _observed(row, charge, issue_at))
    return _outcome(row, replayed=False)


def get_funded_charge_instance(
    db: Session,
    *,
    scope: TenantScope,
    instance_id: UUID | None = None,
    key: RatedChargeKeyV1 | None = None,
) -> FundedChargeInstanceV1 | None:
    """Read one instance by id or by charge key (exactly one)."""
    tenant_id = _tenant(scope)
    if (instance_id is None) == (key is None):
        raise ValueError("name exactly one of instance_id or key")
    statement = select(FundedChargeInstance).where(
        FundedChargeInstance.tenant_id == tenant_id
    )
    if instance_id is not None:
        statement = statement.where(FundedChargeInstance.id == instance_id)
    else:
        if key is None:
            raise ValueError("name exactly one of instance_id or key")
        statement = statement.where(
            FundedChargeInstance.charge_ref == charge_ref_for(key)
        )
    row = db.execute(statement).scalar_one_or_none()
    return None if row is None else _view(row)


def _page(
    db: Session,
    statement: Any,
    after: FundedChargeCursorV1 | None,
    limit: int,
) -> tuple[FundedChargeInstanceV1, ...]:
    if after is not None:
        statement = statement.where(_after(after))
    rows = db.execute(
        statement.order_by(
            FundedChargeInstance.created_at, FundedChargeInstance.id
        ).limit(max(1, min(limit, 500)))
    ).scalars()
    return tuple(_view(row) for row in rows)


def list_pending_funded_charges(
    db: Session,
    *,
    scope: TenantScope,
    billing_account_id: UUID,
    after: FundedChargeCursorV1 | None = None,
    limit: int = _PAGE_LIMIT,
) -> tuple[FundedChargeInstanceV1, ...]:
    """An account's charges due and awaiting payment, oldest first (keyset)."""
    statement = select(FundedChargeInstance).where(
        FundedChargeInstance.tenant_id == _tenant(scope),
        FundedChargeInstance.billing_account_id == billing_account_id,
        FundedChargeInstance.state == FundedChargeState.PENDING.value,
    )
    return _page(db, statement, after, limit)


def list_funded_charge_attention(
    db: Session,
    *,
    scope: TenantScope,
    after: FundedChargeCursorV1 | None = None,
    limit: int = _PAGE_LIMIT,
) -> tuple[FundedChargeInstanceV1, ...]:
    """Instances needing a person (attention required), oldest first (keyset)."""
    statement = select(FundedChargeInstance).where(
        FundedChargeInstance.tenant_id == _tenant(scope),
        FundedChargeInstance.state == FundedChargeState.ATTENTION_REQUIRED.value,
    )
    return _page(db, statement, after, limit)


__all__ = [
    "REPLAY_CONSTRAINTS",
    "charge_ref_for",
    "get_funded_charge_instance",
    "instance_id_for",
    "is_funded_charge_replay_conflict",
    "key_for_charge_ref",
    "list_funded_charge_attention",
    "list_pending_funded_charges",
    "on_account_credit_changed",
    "on_settlement_settled",
    "reevaluate_account_terms",
    "record_funded_charge_attention",
    "retry_funded_charge",
    "start_funded_charge",
]
