"""Typed contracts, ports and the refusal error of the F1 Settle flow.

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
    ``occurred_at`` is the business instant for the owner postings.
    """

    confirmation_id: UUID
    intent_reference: str
    occurred_at: datetime
    withholding: SettleWithholdingV1 | None = None


@dataclass(frozen=True, slots=True)
class SettleOutcomeV1:
    """What a call decided. References only.

    ``applied_document_ids`` lists documents this call allocated cash to; it is
    empty on a replay because the instance does not store it.
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


__all__ = [
    "DEFAULT_ACCEPTED_SOURCES",
    "SETTLE_DEFINITION_VERSION",
    "WITHHOLDING_SOURCE",
    "SETTLE_INSERT_STATES",
    "SETTLE_TRANSITIONS",
    "DepositApplication",
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
    "settle_transition_allowed",
]
