"""The inbound seam — where a provider fact is admitted (ADR-0006 § 5c).

**STATUS: PROTOTYPE — not a declared owner (2026-08-12).** This module exists as
audit evidence on branch `docs/omni-inbox-sources`. Whether this capability
belongs in the kernel at all is under adjudication in
`docs/superpowers/plans/2026-08-12-fleet-decomposition-matrix.md`. Do not publish,
do not adopt, and read every "Owner of" claim below as a proposal.

**Owner of:** *a provider says something arrived — is it new, and what exactly
did it say?*

The mirror of `dotmac_kernel.delivery_providers`, and built on the same terms:
a `Protocol` and **no clients**. SMTP, IMAP, Meta Cloud API and Twilio webhooks
are product dependencies, exactly as ADR-0009 ships a `SecretSource` seam and no
secret-store client. A product that receives mail brings its own IMAP.

## Why `admit` exists rather than each receiver writing its own row

Because the at-most-once decision must not be optional, and must not be
reimplemented. Provider webhooks are at-least-once by design: Meta retries,
mailbox polling re-reads, a relay redelivers. Every product that has built this
has built its own dedup, and
`docs/inventories/idempotency-sources.md` counted the results — ERP three
mechanisms, Sub three more, each with its own defects.

`admit` does exactly three things, in order:

1. **Delegate at-most-once** to `dotmac_kernel.idempotency.execute_once` with
   `scope="inbound"` and the provider's event identity as the key. Hard rule 21:
   there is ONE owner of "has this been done", and this is a caller of it, not a
   second implementation.
2. **Record the durable fact** — the normalized payload, inside that same
   operation so the two commit together or not at all.
3. **Return the outcome**, with `replayed` telling the caller whether this event
   had already been admitted, so a webhook handler can answer 200 to a retry
   without re-deriving anything.

## What it deliberately does NOT do

- **It does not decide consequences.** Admission is not processing. A consumer
  reads `recorded` observations, does its own work, and calls `mark_processed`
  or `mark_rejected`. That split is the source-of-truth standard's
  observation → decision → consequence, and it is what makes a parsing bug
  recoverable: fix the parser, reprocess the stored payloads.
- **It does not verify signatures.** The adapter does, because every provider
  signs differently and the secret involved is the product's. A receiver that
  returns an observation is asserting it already authenticated the request.
- **It does not route, thread or resolve contacts.** Those are a conversation
  module's and a product's, respectively.

## Verification is the adapter's, and that boundary is load-bearing

`InboundReceiver.verify` is separate from `parse` on purpose. Verification needs
the RAW body — a signature is over bytes, and any re-serialisation breaks it —
whereas parsing wants a structure. Collapsing them into one method makes it easy
to write a receiver that parses first and verifies a re-encoded payload, which
verifies nothing.

Follows the kernel transaction-authority rule: RECEIVES a `Session`, never builds
one; does `add`/`flush`, never `commit`/`rollback`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_kernel import channels as channels_registry
from dotmac_kernel.idempotency import execute_once, fingerprint_of
from dotmac_kernel.inbound_models import (
    OBSERVATION_STATUSES,
    STATUS_PROCESSED,
    STATUS_RECORDED,
    STATUS_REJECTED,
    ConnectedAccount,
    InboundObservation,
)

__all__ = [
    "IDEMPOTENCY_SCOPE",
    "AdmitOutcome",
    "InboundError",
    "InboundReceiver",
    "Observation",
    "admit",
    "connected_account",
    "deactivate_account",
    "list_connected_accounts",
    "mark_processed",
    "mark_rejected",
    "pending_observations",
    "register_connected_account",
]

#: The idempotency scope every inbound admission is keyed under. Distinct from
#: `messaging.process_once`'s `"inbox"` (transport command delivery) — two
#: different concerns that must not share a keyspace.
IDEMPOTENCY_SCOPE = "inbound"


class InboundError(ValueError):
    """A malformed admission — never a "this event is a duplicate" answer."""


@dataclass(frozen=True, slots=True)
class Observation:
    """A normalized provider fact, as a receiver produces it.

    The channel is NOT required to be declared. An observation that cannot be
    recorded is a message silently lost, and "what arrived that we could not
    handle" is precisely the operator question this ledger answers — so an
    unknown channel is admitted and rejected downstream, with a reason.
    `register_connected_account` takes the opposite line, because a misspelled
    channel there is a misconfiguration caught while an operator can still fix
    it.

    `payload` is the receiver's normalized shape, not the provider's raw body.
    Storing the raw body would tie replay to a provider's wire format forever;
    storing the normalized form means a reprocess after a bug fix re-runs the
    parts that were wrong, not the parsing that was already right.
    """

    provider: str
    #: Which of the tenant's accounts it arrived at. See
    #: `dotmac_kernel.inbound_models.ConnectedAccount.account_scope`.
    account_scope: str
    #: The provider's own id for this delivery. The at-most-once key.
    provider_event_id: str
    channel: str
    payload: Mapping[str, object]
    #: When the PROVIDER says it happened, not when we saw it.
    observed_at: datetime

    def __post_init__(self) -> None:
        for name in ("provider", "account_scope", "provider_event_id", "channel"):
            if not (getattr(self, name) or "").strip():
                raise InboundError(f"{name} is required on an observation")
        if not isinstance(self.observed_at, datetime):
            raise InboundError("observed_at must be a datetime")

    @property
    def idempotency_key(self) -> str:
        """The at-most-once key: provider event identity, scoped to the account."""
        return f"{self.provider}:{self.account_scope}:{self.provider_event_id}"

    @property
    def payload_fingerprint(self) -> str:
        """Binds the event id to one payload, so a REPLAY carrying different
        content is a conflict rather than a silently-ignored duplicate."""
        return fingerprint_of(dict(self.payload))


@dataclass(frozen=True, slots=True)
class AdmitOutcome:
    """What admission did."""

    observation_id: UUID
    #: True when this event had already been admitted and nothing was written.
    #: A webhook handler answers 200 on a replay without re-deriving anything.
    replayed: bool


class InboundReceiver(Protocol):
    """A product's adapter for one provider. The kernel ships none.

    `verify` gets the RAW body because a signature is over bytes; `parse` gets
    the structure. See this module's docstring for why they are separate.
    """

    provider: str

    def verify(self, *, headers: Mapping[str, str], body: bytes) -> bool:
        """Whether this request genuinely came from the provider."""
        ...

    def parse(
        self, *, headers: Mapping[str, str], body: bytes
    ) -> Sequence[Observation]:
        """Normalized facts from one request. A batch webhook yields several."""
        ...


# ── Connected accounts ──────────────────────────────────────────────────────


def register_connected_account(
    db: Session,
    tenant_id: UUID,
    *,
    channel: str,
    provider: str,
    account_scope: str,
    display_name: str | None = None,
    credential_name: str | None = None,
    config: Mapping[str, object] | None = None,
    note: str | None = None,
) -> ConnectedAccount:
    """Register, or reactivate and update, one connected account.

    Idempotent on `(tenant, provider, account_scope)`: re-registering an account
    that was deactivated brings it back rather than failing, because "connect
    this mailbox again" is an operator action with an obvious meaning.

    The channel MUST be declared — unlike an observation, which is recorded
    whatever arrives. A misspelled channel here is a configuration error that
    would silently receive nothing, and it is caught at the moment an operator
    can still fix it.
    """
    channel_code = (channel or "").strip().lower()
    channels_registry.channel_spec(channel_code)  # raises if undeclared

    provider_code = (provider or "").strip().lower()
    scope = (account_scope or "").strip()
    if not provider_code or not scope:
        raise InboundError("provider and account_scope are required")

    existing = db.execute(
        select(ConnectedAccount).where(
            ConnectedAccount.tenant_id == tenant_id,
            ConnectedAccount.provider == provider_code,
            ConnectedAccount.account_scope == scope,
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.channel = channel_code
        existing.is_active = True
        if display_name is not None:
            existing.display_name = display_name
        if credential_name is not None:
            existing.credential_name = credential_name
        if config is not None:
            existing.config = dict(config)
        if note is not None:
            existing.note = note
        db.flush()
        return existing

    account = ConnectedAccount(
        tenant_id=tenant_id,
        channel=channel_code,
        provider=provider_code,
        account_scope=scope,
        display_name=display_name,
        credential_name=credential_name,
        config=dict(config) if config is not None else None,
        note=note,
    )
    db.add(account)
    db.flush()
    return account


def connected_account(
    db: Session, tenant_id: UUID, *, provider: str, account_scope: str
) -> ConnectedAccount | None:
    """The account a message arrived at, or None if nobody registered it."""
    return db.execute(
        select(ConnectedAccount).where(
            ConnectedAccount.tenant_id == tenant_id,
            ConnectedAccount.provider == (provider or "").strip().lower(),
            ConnectedAccount.account_scope == (account_scope or "").strip(),
        )
    ).scalar_one_or_none()


def list_connected_accounts(
    db: Session,
    tenant_id: UUID,
    *,
    channel: str | None = None,
    active_only: bool = True,
) -> tuple[ConnectedAccount, ...]:
    """A tenant's accounts, newest first."""
    query = select(ConnectedAccount).where(ConnectedAccount.tenant_id == tenant_id)
    if channel is not None:
        query = query.where(ConnectedAccount.channel == channel.strip().lower())
    if active_only:
        query = query.where(ConnectedAccount.is_active.is_(True))
    query = query.order_by(ConnectedAccount.created_at.desc())
    return tuple(db.execute(query).scalars())


def deactivate_account(
    db: Session, tenant_id: UUID, *, provider: str, account_scope: str
) -> bool:
    """Stop receiving at an account, keeping its history. True if it changed."""
    account = connected_account(
        db, tenant_id, provider=provider, account_scope=account_scope
    )
    if account is None or not account.is_active:
        return False
    account.is_active = False
    db.flush()
    return True


# ── Admission ───────────────────────────────────────────────────────────────


def admit(db: Session, tenant_id: UUID, *, observation: Observation) -> AdmitOutcome:
    """Record a provider fact at most once. See this module's docstring.

    Raises `dotmac_kernel.idempotency.IdempotencyConflict` when the same event
    id arrives carrying DIFFERENT content — a real provider bug or a spoofed
    replay, and not something to paper over by keeping the first version
    silently.
    """
    if not isinstance(observation, Observation):
        raise InboundError("admit requires an Observation")

    def _record(session: Session) -> dict[str, object]:
        row = InboundObservation(
            tenant_id=tenant_id,
            provider=observation.provider,
            account_scope=observation.account_scope,
            provider_event_id=observation.provider_event_id,
            channel=observation.channel,
            payload=dict(observation.payload),
            observed_at=observation.observed_at,
            processing_status=STATUS_RECORDED,
        )
        session.add(row)
        session.flush()
        return {"observation_id": str(row.id)}

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope=IDEMPOTENCY_SCOPE,
        key=observation.idempotency_key,
        operation=_record,
        operation_name=f"inbound:{observation.provider}",
        fingerprint=observation.payload_fingerprint,
    )
    return AdmitOutcome(
        observation_id=UUID(str(outcome.result["observation_id"])),
        replayed=outcome.replayed,
    )


def pending_observations(
    db: Session, tenant_id: UUID, *, channel: str | None = None, limit: int = 100
) -> tuple[InboundObservation, ...]:
    """Admitted facts nothing has acted on yet, oldest first.

    Oldest first because a conversation's messages must be applied in the order
    they were observed; newest-first would reorder a customer's thread in front
    of them.
    """
    query = select(InboundObservation).where(
        InboundObservation.tenant_id == tenant_id,
        InboundObservation.processing_status == STATUS_RECORDED,
    )
    if channel is not None:
        query = query.where(InboundObservation.channel == channel.strip().lower())
    query = query.order_by(InboundObservation.observed_at.asc()).limit(limit)
    return tuple(db.execute(query).scalars())


def _transition(
    db: Session,
    tenant_id: UUID,
    observation_id: UUID,
    *,
    status: str,
    error: str | None,
) -> InboundObservation:
    if status not in OBSERVATION_STATUSES:
        raise InboundError(f"unknown processing status {status!r}")
    row = db.execute(
        select(InboundObservation).where(
            InboundObservation.tenant_id == tenant_id,
            InboundObservation.id == observation_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise InboundError(f"observation {observation_id} not found for this tenant")
    row.processing_status = status
    row.error_code = error
    db.flush()
    return row


def mark_processed(
    db: Session, tenant_id: UUID, *, observation_id: UUID
) -> InboundObservation:
    """The consumer derived its consequences from this fact."""
    return _transition(
        db, tenant_id, observation_id, status=STATUS_PROCESSED, error=None
    )


def mark_rejected(
    db: Session, tenant_id: UUID, *, observation_id: UUID, error_code: str
) -> InboundObservation:
    """The consumer could not use this fact, and why.

    The payload is KEPT. A rejected observation is the row that explains a
    message the customer swears they sent, and it is what a reprocess runs
    against once the reason is fixed.
    """
    if not (error_code or "").strip():
        raise InboundError("a rejection must carry an error_code")
    return _transition(
        db, tenant_id, observation_id, status=STATUS_REJECTED, error=error_code.strip()
    )
