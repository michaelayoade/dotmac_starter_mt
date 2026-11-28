"""Typed contracts, ports and errors of the F1 Settle and F2 FundedCharge flows.

Nothing here stores or computes an amount. Amount-bearing values (the owner's
money type) appear only as transient inputs the caller hands to an owner call;
the persisted instance carries opaque references only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from dotmac_kernel.money import Money
from sqlalchemy.orm import Session

SETTLE_DEFINITION_VERSION = "settle.v1"
FUNDED_CHARGE_DEFINITION_VERSION = "funded_charge.v1"

#: Confirmation sources a tenant accepts out of the box. Manual entry and
#: transfer proof stay off until a maker-checker flow is composed.
DEFAULT_ACCEPTED_SOURCES = frozenset({"PROVIDER_CALLBACK", "PROVIDER_RECONCILIATION"})

#: The only source from which a withholding claim is accepted.
WITHHOLDING_SOURCE = "MANUAL"


class SettleState(StrEnum):
    """Where a Settle instance stands."""

    SETTLED = "settled"
    REFUSED = "refused"
    UNAPPLIED = "unapplied"
    RESOLVED = "resolved"


#: States a Settle instance may be first written in (the row is written once, at
#: its outcome). The database trigger holds the same table.
SETTLE_INSERT_STATES = frozenset(
    {SettleState.SETTLED, SettleState.REFUSED, SettleState.UNAPPLIED}
)

#: Allowed transitions out of a non-terminal state. ``settled`` and ``resolved``
#: are terminal and absent. ``refused`` may repeat (a retry refused again).
SETTLE_TRANSITIONS: dict[SettleState, frozenset[SettleState]] = {
    SettleState.REFUSED: frozenset(
        {SettleState.REFUSED, SettleState.SETTLED, SettleState.UNAPPLIED}
    ),
    SettleState.UNAPPLIED: frozenset({SettleState.RESOLVED}),
}


def settle_transition_allowed(old: SettleState, new: SettleState) -> bool:
    return new in SETTLE_TRANSITIONS.get(old, frozenset())


class DepositApplication(StrEnum):
    """What happens to an account-credit deposit after the cash is accepted."""

    NONE = "none"
    OLDEST_DUE_FIRST = "oldest_due_first"


@dataclass(frozen=True, slots=True)
class SettlePolicyV1:
    """Versioned tenant policy for F1, pinned on the instance it decided."""

    version: str
    accepted_confirmation_sources: frozenset[str] = DEFAULT_ACCEPTED_SOURCES
    deposit_application: DepositApplication = DepositApplication.OLDEST_DUE_FIRST


@dataclass(frozen=True, slots=True)
class SettlementTargetV1:
    """What the assembly resolved a payment intent's target reference to."""

    billing_account_id: UUID
    bound_document_id: UUID | None = None


class SettlementTargetResolver(Protocol):
    """Port: resolve an intent's target reference to a billing account.

    It receives only the intent's own ``target_reference`` and ``purpose``.
    Return ``None`` when the reference does not resolve.
    """

    def resolve(
        self,
        db: Session,
        *,
        scope: TenantScope,
        target_reference: str | None,
        purpose: str,
    ) -> SettlementTargetV1 | None: ...


class SettlePolicyReader(Protocol):
    """Port: read the tenant's current F1 policy."""

    def read(self, db: Session, *, scope: TenantScope) -> SettlePolicyV1: ...


@dataclass(frozen=True, slots=True)
class SettlePorts:
    """The assembly-supplied ports F1 reads through."""

    targets: SettlementTargetResolver
    policy: SettlePolicyReader


@dataclass(frozen=True, slots=True)
class SettleWithholdingV1:
    """Withholding evidence supplied with a MANUAL confirmation.

    The document and account come from the Tax evidence and the resolved target;
    these values are handed to Billing and never stored by flows.
    """

    tax_evidence: Any
    claim_ref: str
    claimed: Money
    accepted: Money
    source_owner: str
    source_ref: str
    idempotency_key: str
    authorization_actor_ref: str
    authorization_evidence_ref: str
    certificate_observation: Any | None = None


@dataclass(frozen=True, slots=True)
class SettleConfirmationV1:
    """One confirmed payment to settle.

    ``intent_reference`` addresses the intent exactly as Payments knows it. No
    provider payload is carried: the target is derived only from that intent.
    There is no caller-chosen instant: Billing's claim and postings use the
    Payments cash observation's ``observed_at``, the date the cash was received,
    and a retry uses the same value.
    """

    confirmation_id: UUID
    intent_reference: str
    withholding: SettleWithholdingV1 | None = None


@dataclass(frozen=True, slots=True)
class SettleConfirmationV2(SettleConfirmationV1):
    """One confirmed payment carrying several deductions (C2b).

    ``deductions`` are accepted in the given order, each through Billing's
    withholding-discharge command, before the whole-invoice pass. Each names a
    different Billing frozen withholding component (its own tax code), for
    example withholding tax and agency stamp duty. Like V1 withholding they are
    accepted only on a MANUAL confirmation, and the same component twice is
    refused before any write. ``withholding`` must stay unset: V2 callers name
    every deduction in ``deductions``; a ``withholding`` set as well is read as
    the first deduction, so the duplicate rule still covers it.
    """

    deductions: tuple[SettleWithholdingV1, ...] = ()


def confirmation_deductions(
    command: SettleConfirmationV1,
) -> tuple[SettleWithholdingV1, ...]:
    """The deductions a confirmation carries: V1's one claim or V2's tuple."""
    first = () if command.withholding is None else (command.withholding,)
    if isinstance(command, SettleConfirmationV2):
        return first + tuple(command.deductions)
    return first


@dataclass(frozen=True, slots=True)
class SettleOutcomeV1:
    """What a call decided. References only.

    ``applied_document_ids`` lists documents this call paid whole from the
    balance; it is empty on a replay because the instance does not store it.
    """

    instance_id: UUID
    state: SettleState
    reason_code: str | None
    version: int
    settlement_id: UUID | None
    billing_account_id: UUID | None
    replayed: bool
    applied_document_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class InvoiceIssuedV1:
    """GAP-3 trigger: Billing issued this invoice (the routed issued fact).

    Only the document id is trusted; everything else is re-read from Billing.
    """

    document_id: UUID


@dataclass(frozen=True, slots=True)
class WithholdingAcceptedV1:
    """Trigger: Billing accepted a withholding discharge for this document.

    The assembly maps Billing's accepted fact to it. Every value is verified
    against Billing's stored discharge record and a mismatch raises: the account,
    the document, that it is not a reversal row, and that ``accepted_at`` is
    Billing's ``recorded_at`` instant. ``discharge_id`` keys the idempotent source
    references; the payment dating floor is taken from Billing (the later of
    ``recorded_at`` and the ledger ``occurred_at``). No amount is carried.
    """

    billing_account_id: UUID
    document_id: UUID
    discharge_id: UUID
    accepted_at: datetime


@dataclass(frozen=True, slots=True)
class WithholdingReleasedV1:
    """Trigger: Billing released a frozen expected-withholding component (Q-F).

    The assembly maps ``billing.withholding.released.v1`` (ids only) to it,
    reading ``released_at`` (the database instant Billing recorded) from
    Billing's ``get_withholding_release``. Every value is verified against
    Billing's stored release and a mismatch raises: the account, the document,
    that it is not an undo row, and that ``released_at`` is Billing's
    ``recorded_at`` instant. ``release_id`` keys the idempotent source
    references; the dating floor is Billing's ``recorded_at``, so an application
    never predates the decision. No amount is carried.
    """

    billing_account_id: UUID
    document_id: UUID
    release_id: UUID
    released_at: datetime


@dataclass(frozen=True, slots=True)
class BalanceAppliedOutcomeV1:
    """What an invoice-issued balance pass did. References only.

    ``reason_code`` is None when at least one document was paid; otherwise it
    names the quiet no-op (nothing was posted).
    """

    billing_account_id: UUID
    applied_document_ids: tuple[UUID, ...]
    reason_code: str | None


@dataclass(frozen=True, slots=True)
class SettleAttentionCursorV1:
    """Keyset cursor for ``list_settle_attention`` (the last row's position)."""

    created_at: datetime
    id: UUID


@dataclass(frozen=True, slots=True)
class SettleInstanceV1:
    """Read view of a persisted instance (references and state only)."""

    id: UUID
    confirmation_id: UUID
    intent_id: UUID
    state: SettleState
    reason_code: str | None
    policy_version: str
    definition_version: str
    version: int
    billing_account_id: UUID | None
    settlement_id: UUID | None
    withholding_discharge_ref: str | None
    actor_ref: str | None
    resolution_ref: str | None
    created_at: datetime
    updated_at: datetime
    #: Every discharge reference in acceptance order (C2b). A row written before
    #: the list column existed reports its single legacy reference here.
    withholding_discharge_refs: tuple[str, ...] = ()


class SettleRefused(Exception):
    """A flows-level refusal or caller error with a stable machine code.

    Raised as ``SettleRefused(code)`` for caller errors that write nothing (a
    stale ``expected_version``, retrying an instance that is not refused).
    """

    @property
    def code(self) -> str:
        """The stable machine code (the first argument)."""
        return str(self.args[0])


class BalancePassFailed(Exception):
    """A technical fault in a balance pass: GAP-3 or the GAP-L1 credit re-check.

    Raised as ``BalancePassFailed(code)`` by ``apply_balance_on_invoice_issued``
    (open documents unreadable, a document of another account, an unsplittable
    statement window) and by ``on_account_credit_changed`` (an unreadable or
    unsplittable statement). The caller rolls back and its event delivery
    retries. It is never a business no-op and writes nothing.
    """

    @property
    def code(self) -> str:
        """The stable machine code (the first argument)."""
        return str(self.args[0])


# --------------------------------------------------------------------------
# F2 FundedCharge
# --------------------------------------------------------------------------
class FundedChargeState(StrEnum):
    """Where a FundedCharge instance stands.

    ``pending`` is a due charge the balance does not cover in full: nothing is in
    the ledger. ``discharged`` means the invoice was issued and paid from account
    credit in one transaction. ``attention_required`` means an owner refused to
    post a charge the balance covers. ``withdrawn`` means the charge source no
    longer holds the charge (or the contract is no longer prepaid).
    """

    PENDING = "pending"
    DISCHARGED = "discharged"
    ATTENTION_REQUIRED = "attention_required"
    WITHDRAWN = "withdrawn"


#: States an instance may be first written in. The database trigger holds the
#: same table.
FUNDED_CHARGE_INSERT_STATES = frozenset(
    {
        FundedChargeState.PENDING,
        FundedChargeState.DISCHARGED,
        FundedChargeState.ATTENTION_REQUIRED,
    }
)

#: Allowed transitions out of a non-terminal state (``mf_0002`` trigger graph).
#: ``discharged`` and ``withdrawn`` are terminal and absent.
FUNDED_CHARGE_TRANSITIONS: dict[FundedChargeState, frozenset[FundedChargeState]] = {
    FundedChargeState.PENDING: frozenset(
        {
            FundedChargeState.DISCHARGED,
            FundedChargeState.ATTENTION_REQUIRED,
            FundedChargeState.WITHDRAWN,
        }
    ),
    FundedChargeState.ATTENTION_REQUIRED: frozenset(
        {
            FundedChargeState.ATTENTION_REQUIRED,
            FundedChargeState.PENDING,
            FundedChargeState.DISCHARGED,
            FundedChargeState.WITHDRAWN,
        }
    ),
}


def funded_charge_transition_allowed(
    old: FundedChargeState, new: FundedChargeState
) -> bool:
    return new in FUNDED_CHARGE_TRANSITIONS.get(old, frozenset())


@dataclass(frozen=True, slots=True)
class RatedChargeKeyV1:
    """The immutable identity of one rated charge, as its source knows it.

    The identity must not depend on the service period dates: the period of a
    prepaid charge is unknown while the charge is pending, and the instance's
    ``charge_ref`` never changes. Use, for example, the subscription and a
    renewal sequence.
    """

    source_system: str
    source_kind: str
    source_fact_id: str
    source_fact_version: str


@dataclass(frozen=True, slots=True)
class RatedChargeV1:
    """What the charge source says about one charge right now (a transient input).

    ``total`` is the tax-inclusive price of the FULL period, always. The contract
    type and the period length are Billing account terms, read by F2 from Billing
    at the period start.

    ``period_starts_at`` and ``period_ends_at`` are both None for an ordinary
    charge, whose period is ``[issue_at, end(issue_at))``. Both set means a
    prorated charge: the enclosing service period it co-terminates with, for
    example a mid-period upgrade. The amount due is then ``total`` x the days left
    from ``issue_at`` to ``period_ends_at`` / the days in the enclosing period,
    rounded half-up once (``FundedChargePolicyV1.proration`` must be
    ``actual_calendar_days``). A late payment pays only the remaining days.
    """

    key: RatedChargeKeyV1
    billing_account_id: UUID
    service_ref: str | None
    total: Money
    due_at: datetime
    period_starts_at: datetime | None = None
    period_ends_at: datetime | None = None


class RatedChargeSource(Protocol):
    """Port: the assembly's rating of prepaid charges, over its own contracts.

    * ``read`` returns the charge as the source holds it now, or ``None`` when
      the charge no longer exists (withdrawn, superseded or terminated).
    * ``issue_command`` returns the complete Billing ``IssueRatedInvoiceV3``
      (with Tax evidence) for ``issue_at``. The identity is the key's, the
      service period is ``[issue_at, end)`` with a verified status, where ``end``
      is ``issue_at`` plus the period of the Billing terms effective at
      ``issue_at`` (day or month units, counted from ``issue_at`` in the policy's
      billing time zone and clamped to the month end), or the charge's
      ``period_ends_at`` for a prorated charge. The command's ``timezone`` is the
      policy's, ``collection_timing`` is ``advance``, and ``due_at`` is
      ``issue_at``; ``reference_date`` comes from ``issue_at`` and the total is
      the amount due at ``issue_at``. F2 refuses any other command. The source,
      not flows, schedules the next charge at the end of the paid period: flows
      has no scheduler.
    """

    def read(
        self, db: Session, *, scope: TenantScope, key: RatedChargeKeyV1
    ) -> RatedChargeV1 | None: ...

    def issue_command(
        self,
        db: Session,
        *,
        scope: TenantScope,
        key: RatedChargeKeyV1,
        issue_at: datetime,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class FundedChargePolicyV1:
    """Versioned tenant policy for F2, pinned on the instance it decided.

    ``reevaluation_batch_cap`` bounds the pending charges examined per
    transaction; the rest continue by event. The value is a policy default the
    tenant overrides, not a decision made here.

    ``proration`` is ``actual_calendar_days`` (default: a charge carrying an
    enclosing period is prorated by actual calendar days) or ``none`` (F2 refuses
    a partial period); any other value is refused. ``billing_timezone`` is the
    IANA zone the periods are counted in (default ``UTC``) and must be a known
    zone; the invoice command's ``timezone`` must equal it. Every F2 command
    reads the policy through one check that raises ``ValueError`` for an unknown
    value before anything is written, so the event is redelivered.
    """

    version: str
    reevaluation_batch_cap: int = 20
    proration: str = "actual_calendar_days"
    billing_timezone: str = "UTC"


class FundedChargePolicyReader(Protocol):
    """Port: read the tenant's current F2 policy."""

    def read(self, db: Session, *, scope: TenantScope) -> FundedChargePolicyV1: ...


@dataclass(frozen=True, slots=True)
class FundedChargePorts:
    """The assembly-supplied ports F2 reads through.

    ``obligation_sources`` is Billing's ``ObligationSourceRegistry`` and
    ``numbering`` its ``NumberingProvider``; both are handed to Billing untouched.
    """

    charges: RatedChargeSource
    policy: FundedChargePolicyReader
    obligation_sources: Any
    numbering: Any


@dataclass(frozen=True, slots=True)
class StartFundedChargeV1:
    """Start the funding of one charge at its due time."""

    key: RatedChargeKeyV1


@dataclass(frozen=True, slots=True)
class FundedChargeCursorV1:
    """Keyset cursor on ``(created_at, id)`` (the last row's position)."""

    created_at: datetime
    id: UUID


@dataclass(frozen=True, slots=True)
class SettledFundsV1:
    """New funds on an account: the settlement that landed.

    The account is only a claim: Billing refuses a pair that does not belong
    together. ``after`` is set only on a continuation event and resumes the
    re-evaluation after the last row examined, so skipped rows are not
    re-examined forever.
    """

    billing_account_id: UUID
    settlement_id: UUID
    after: FundedChargeCursorV1 | None = None


@dataclass(frozen=True, slots=True)
class AccountTermsChangedV1:
    """An account's contract terms changed: re-check its pending charges.

    ``at`` is the instant the re-check stands for: the ``effective_from`` of the
    terms version that caused the call. It must be timezone-aware, and the caller
    must not call before ``at`` has passed (flows reads no clock). ``after`` is
    set only on a continuation event.
    """

    billing_account_id: UUID
    at: datetime
    after: FundedChargeCursorV1 | None = None


@dataclass(frozen=True, slots=True)
class AccountCreditChangedV1:
    """Spendable credit may have risen on an account: re-check its pending charges.

    The account is the only claim; everything else is re-read from Billing, so a
    mis-routed event only causes an early, harmless re-check. ``after`` is set
    only on a continuation event.
    """

    billing_account_id: UUID
    after: FundedChargeCursorV1 | None = None


@dataclass(frozen=True, slots=True)
class FundedChargeOutcomeV1:
    """What a call decided. References only."""

    instance_id: UUID
    state: FundedChargeState
    reason_code: str | None
    version: int
    billing_account_id: UUID
    document_id: UUID | None
    replayed: bool


@dataclass(frozen=True, slots=True)
class CreditRecheckOutcomeV1:
    """What an account-credit re-check decided. References only.

    ``reason_code`` names a quiet no-op (``no_pending_charges``, ``no_balance``,
    ``nothing_fits``) and is None when at least one pending charge changed.
    """

    billing_account_id: UUID
    changed: tuple[FundedChargeOutcomeV1, ...]
    reason_code: str | None


@dataclass(frozen=True, slots=True)
class FundedChargeInstanceV1:
    """Read view of a persisted instance (references and state only)."""

    id: UUID
    charge_ref: str
    state: FundedChargeState
    reason_code: str | None
    policy_version: str
    definition_version: str
    version: int
    billing_account_id: UUID
    service_ref: str | None
    obligation_id: UUID | None
    document_id: UUID | None
    credit_draw_id: UUID | None
    funding_settlement_id: UUID | None
    actor_ref: str | None
    created_at: datetime
    updated_at: datetime


class FundedChargeRefused(Exception):
    """A caller error or flows-level refusal with a stable machine code.

    Raised as ``FundedChargeRefused(code)`` for errors that write nothing (an
    unknown charge, a charge that is not prepaid, a stale ``expected_version``).
    """

    @property
    def code(self) -> str:
        """The stable machine code (the first argument)."""
        return str(self.args[0])


class FundedChargeStepFailed(Exception):
    """An owner refused to post one pending charge during re-evaluation.

    Raised as ``FundedChargeStepFailed(instance_id, code)`` from the owner's
    exception. The caller rolls back, then calls ``record_funded_charge_attention``
    in a NEW transaction with these two values.
    """

    @property
    def instance_id(self) -> UUID:
        return UUID(str(self.args[0]))

    @property
    def code(self) -> str:
        return str(self.args[1])


__all__ = [
    "AccountCreditChangedV1",
    "AccountTermsChangedV1",
    "DEFAULT_ACCEPTED_SOURCES",
    "FUNDED_CHARGE_DEFINITION_VERSION",
    "FUNDED_CHARGE_INSERT_STATES",
    "FUNDED_CHARGE_TRANSITIONS",
    "SETTLE_DEFINITION_VERSION",
    "WITHHOLDING_SOURCE",
    "SETTLE_INSERT_STATES",
    "SETTLE_TRANSITIONS",
    "CreditRecheckOutcomeV1",
    "DepositApplication",
    "FundedChargeCursorV1",
    "FundedChargeInstanceV1",
    "FundedChargeOutcomeV1",
    "FundedChargePolicyReader",
    "FundedChargePolicyV1",
    "FundedChargePorts",
    "FundedChargeRefused",
    "FundedChargeState",
    "FundedChargeStepFailed",
    "RatedChargeKeyV1",
    "RatedChargeSource",
    "RatedChargeV1",
    "SettledFundsV1",
    "StartFundedChargeV1",
    "SettleAttentionCursorV1",
    "SettleConfirmationV1",
    "SettleInstanceV1",
    "SettleOutcomeV1",
    "SettlePolicyReader",
    "SettlePolicyV1",
    "SettlePorts",
    "SettleRefused",
    "SettleState",
    "SettleWithholdingV1",
    "SettlementTargetResolver",
    "SettlementTargetV1",
    "funded_charge_transition_allowed",
    "settle_transition_allowed",
]
