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


class SettleRefused(Exception):
    """A flows-level refusal or caller error with a stable machine code.

    Raised as ``SettleRefused(code)`` for caller errors that write nothing (a
    stale ``expected_version``, retrying an instance that is not refused).
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

    ``total`` is the tax-inclusive amount. The contract type is not carried: it
    is a Billing account term, read by F2 from Billing at the period start.
    """

    key: RatedChargeKeyV1
    billing_account_id: UUID
    service_ref: str | None
    total: Money
    due_at: datetime


class RatedChargeSource(Protocol):
    """Port: the assembly's rating of prepaid charges, over its own contracts.

    * ``read`` returns the charge as the source holds it now, or ``None`` when
      the charge no longer exists (withdrawn, superseded or terminated).
    * ``issue_command`` returns the complete Billing ``IssueRatedInvoiceV3``
      (with Tax evidence) for ``issue_at``. The identity is the key's, the
      service period is ``[issue_at, issue_at + the period of the Billing terms
      effective at issue_at)`` with a verified status, and ``reference_date`` and
      ``due_at`` come from ``issue_at``. The source, not flows, schedules the
      next charge at the end of the paid period: flows has no scheduler.
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
    """

    version: str
    reevaluation_batch_cap: int = 20


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
    "AccountTermsChangedV1",
    "DEFAULT_ACCEPTED_SOURCES",
    "FUNDED_CHARGE_DEFINITION_VERSION",
    "FUNDED_CHARGE_INSERT_STATES",
    "FUNDED_CHARGE_TRANSITIONS",
    "SETTLE_DEFINITION_VERSION",
    "WITHHOLDING_SOURCE",
    "SETTLE_INSERT_STATES",
    "SETTLE_TRANSITIONS",
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
