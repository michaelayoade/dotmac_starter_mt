"""Execution machinery — inbox, outbox, checkpoints, and one idempotency owner.

Ported from `dotmac_sub`'s integration platform. Each mechanism has exactly ONE
owner here, and a connector plugin may never keep a parallel ledger: a plugin
that tracks its own delivery attempts turns "did this send?" into a question
with two answers and no tiebreak.

## At-most-once execution is the KERNEL's, not this module's

ADR-0014 and hard rule 21 give at-most-once execution exactly one owner:
`dotmac_kernel.idempotency`. This module therefore ADAPTS it — via
`execute_once_platform`, the tenant-free contract, which is the right one
because every row here is platform-plane.

Building a second ledger beside it is the specific failure ADR-0014 records, and
it would be easy to do by accident: `delivery_attempts.idempotency_key` looks
like one. It is not. That column deduplicates ENQUEUE — the same logical effect
queued twice is one row. Whether the effect RUNS at most once is a different
question, answered by the kernel.

## The dedup/collision distinction

`receive_verified` treats a repeated `provider_event_id` as a duplicate **only
when the payload digest matches**. A repeat with different content is a provider
identity collision and raises, because silently deduping it discards real data
on the assumption the provider is well behaved. Ported deliberately from Sub,
which got this right.

## Leases, not optimism, for delivery

A delivery is claimed with `leased_until` before it is attempted. Two
dispatchers without that both call the provider, and no downstream idempotency
fully repairs a provider that has already seen two requests.

Checkpoints use the opposite mechanism — an optimistic `version` — because there
the risk is not a double call but a silently skipped window.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from dotmac_integration.models import (
    DeliveryAttempt,
    InboxReceipt,
    PollingCheckpoint,
)
from dotmac_integration.policy import DEFAULT_POLICY, ExecutionPolicy
from dotmac_integration.retry import Outcome, next_state, retry_delay_seconds

__all__ = [
    "CheckpointConflict",
    "ExecutionError",
    "ProviderEventIdentityCollision",
    "advance_checkpoint",
    "claim_delivery",
    "claim_receipt",
    "enqueue_delivery",
    "payload_digest",
    "receive_verified",
    "record_delivery_outcome",
    "record_receipt_outcome",
]


class ExecutionError(RuntimeError):
    """A receipt or delivery cannot move the way the caller asked."""


class ProviderEventIdentityCollision(ExecutionError):
    """One `provider_event_id`, two different payloads.

    Not a duplicate. A provider reusing an event id for different content is a
    provider defect, and deduping it would discard the second payload silently.
    """


class CheckpointConflict(ExecutionError):
    """Another worker advanced this cursor first."""


def payload_digest(payload: Any) -> str:
    """Stable SHA-256 over a payload.

    `sort_keys` so two structurally equal payloads digest equally regardless of
    key order — without it, a provider that reorders JSON turns every redelivery
    into an identity collision.
    """
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


# ── Inbox ───────────────────────────────────────────────────────────────────


def receive_verified(
    db: Any,
    *,
    installation_id: UUID,
    capability_binding_id: UUID,
    provider_event_id: str,
    event_type: str,
    payload: Any,
    headers: dict | None = None,
) -> tuple[InboxReceipt, bool]:
    """Record a verified inbound event. Returns `(receipt, is_new)`.

    "Verified" is the caller's assertion that authenticity was already checked —
    this function records, it does not authenticate. Signature verification
    belongs to the connector that knows the provider's scheme.
    """
    from sqlalchemy import select

    event_id = provider_event_id.strip()
    if not event_id:
        raise ExecutionError("provider_event_id is required to deduplicate")
    digest = payload_digest(payload)

    existing = db.execute(
        select(InboxReceipt).where(
            # BINDING-scoped, as in the source: the binding is what determines
            # which capability handles the event, so two bindings observing one
            # upstream event are two receipts with two consequences.
            InboxReceipt.capability_binding_id == capability_binding_id,
            InboxReceipt.provider_event_id == event_id,
        )
    ).scalar_one_or_none()

    if existing is not None:
        if existing.payload_digest != digest:
            raise ProviderEventIdentityCollision(
                f"provider event {event_id!r} was already received with a "
                "different payload. This is a provider identity collision, not "
                "a redelivery — refusing to discard the new content"
            )
        return existing, False

    receipt = InboxReceipt(
        installation_id=installation_id,
        capability_binding_id=capability_binding_id,
        provider_event_id=event_id,
        event_type=event_type,
        payload_digest=digest,
        payload_json=payload if isinstance(payload, dict) else {"value": payload},
        headers_json=headers,
        state="verified",
    )
    db.add(receipt)
    db.flush()
    return receipt, True


def claim_receipt(receipt: InboxReceipt) -> bool:
    """Take a receipt for processing. False when there is nothing to do.

    Note what is NOT here: Sub's generic claim path branched on
    `error_code == "crm_customer_name_rejected"`, one product's business rule
    inside the shared engine. A connector that needs an error to be terminal
    reports `OutcomeStatus.TERMINAL`, which is the generic way to say it.
    """
    if receipt.state == "processed":
        return False
    if receipt.state == "dead_letter":
        raise ExecutionError(
            "a dead-letter receipt requires an authorized replay, not a claim"
        )
    receipt.state = "processing"
    receipt.attempt_count += 1
    receipt.error_code = None
    receipt.error_detail = None
    return True


def record_receipt_outcome(
    receipt: InboxReceipt,
    outcome: Outcome,
    *,
    consequence: dict | None = None,
    policy: ExecutionPolicy = DEFAULT_POLICY,
) -> InboxReceipt:
    state = next_state(outcome, attempt_count=receipt.attempt_count, policy=policy)
    receipt.state = "processed" if state == "delivered" else state
    receipt.error_code = outcome.error_code
    receipt.error_detail = outcome.error_detail
    if receipt.state == "processed":
        receipt.processed_at = datetime.now(UTC)
        #: What it CAUSED, so a later replay is comparable rather than guessed.
        receipt.consequence_json = consequence or {}
    return receipt


# ── Outbox ──────────────────────────────────────────────────────────────────


def enqueue_delivery(
    db: Any,
    *,
    installation_id: UUID,
    event_type: str,
    idempotency_key: str,
    payload: Any,
    capability_binding_id: UUID | None = None,
) -> tuple[DeliveryAttempt, bool]:
    """Queue an outbound delivery. Returns `(delivery, is_new)`.

    Enqueue-time deduplication only. Whether the effect RUNS at most once is the
    kernel's question — see this module's docstring.
    """
    from sqlalchemy import select

    key = idempotency_key.strip()
    if not key:
        raise ExecutionError("idempotency_key is required to deduplicate enqueue")

    existing = db.execute(
        select(DeliveryAttempt).where(
            DeliveryAttempt.installation_id == installation_id,
            DeliveryAttempt.idempotency_key == key,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    delivery = DeliveryAttempt(
        installation_id=installation_id,
        capability_binding_id=capability_binding_id,
        event_type=event_type,
        idempotency_key=key,
        payload_digest=payload_digest(payload),
        payload_json=payload if isinstance(payload, dict) else {"value": payload},
        state="pending",
        next_attempt_at=datetime.now(UTC),
    )
    db.add(delivery)
    db.flush()
    return delivery, True


def claim_delivery(
    db: Any,
    delivery: DeliveryAttempt,
    *,
    policy: ExecutionPolicy = DEFAULT_POLICY,
    now: datetime | None = None,
) -> bool:
    """Lease a delivery for one attempt. False when another worker holds it.

    A CONDITIONAL UPDATE, not an in-memory mutation. Two dispatchers reading the
    same row, both seeing an expired lease and both assigning `state` in Python
    would each believe they had the claim — the check and the write must be one
    statement for the lease to mean anything.

    `rowcount == 1` is the claim: the database decided, and the loser sees 0.
    """
    from sqlalchemy import or_, update

    moment = now or datetime.now(UTC)
    result = db.execute(
        update(DeliveryAttempt)
        .where(
            DeliveryAttempt.id == delivery.id,
            DeliveryAttempt.state.not_in(
                ("delivered", "dead_letter", "reconciliation_required")
            ),
            or_(
                DeliveryAttempt.leased_until.is_(None),
                DeliveryAttempt.leased_until < moment,
            ),
            # BACKOFF. Without this the public dispatch seam claims a delivery
            # the engine deliberately scheduled for later, and a failing
            # provider is hammered instead of backed off — the retry curve
            # exists and nothing honours it.
            or_(
                DeliveryAttempt.next_attempt_at.is_(None),
                DeliveryAttempt.next_attempt_at <= moment,
            ),
        )
        .values(
            state="in_flight",
            attempt_count=DeliveryAttempt.attempt_count + 1,
            leased_until=moment + timedelta(seconds=policy.lease_seconds),
        )
        # The DATABASE evaluates the predicate, not the session. Letting
        # SQLAlchemy re-evaluate it in Python to synchronise loaded objects
        # would defeat the point of an atomic claim — and it is what decides
        # the race, so it must be decided in one place.
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        return False
    db.refresh(delivery)
    return True


def record_delivery_outcome(
    delivery: DeliveryAttempt,
    outcome: Outcome,
    *,
    policy: ExecutionPolicy = DEFAULT_POLICY,
    now: datetime | None = None,
) -> DeliveryAttempt:
    """Apply an attempt's result, releasing the lease either way."""
    moment = now or datetime.now(UTC)
    delivery.leased_until = None
    delivery.state = next_state(
        outcome, attempt_count=delivery.attempt_count, policy=policy
    )
    delivery.error_code = outcome.error_code
    delivery.error_detail = outcome.error_detail

    if delivery.state == "delivered":
        delivery.delivered_at = moment
        delivery.next_attempt_at = None
        delivery.error_code = None
        delivery.error_detail = None
    elif delivery.state == "retryable":
        delay = retry_delay_seconds(delivery.attempt_count, outcome, policy=policy)
        delivery.next_attempt_at = moment + timedelta(seconds=delay)
    else:
        # dead_letter / reconciliation_required: nothing is due, and leaving a
        # `next_attempt_at` would make a dispatcher pick it up forever.
        delivery.next_attempt_at = None
    return delivery


# ── Checkpoints ─────────────────────────────────────────────────────────────


def advance_checkpoint(
    db: Any,
    *,
    checkpoint: PollingCheckpoint,
    cursor: dict,
    expected_version: int,
    now: datetime | None = None,
) -> PollingCheckpoint:
    """Move a polling cursor, refusing a stale write.

    A CONDITIONAL UPDATE on `version`, not a Python comparison. Comparing in
    memory and then writing leaves a window in which both workers passed the
    check, and the slower write wins — losing the range between the two cursors
    permanently, with nothing to show it happened.
    """
    from sqlalchemy import update

    moment = now or datetime.now(UTC)
    result = db.execute(
        update(PollingCheckpoint)
        .where(
            PollingCheckpoint.id == checkpoint.id,
            PollingCheckpoint.version == expected_version,
        )
        .values(cursor_json=cursor, version=expected_version + 1, advanced_at=moment)
    )
    if result.rowcount != 1:
        raise CheckpointConflict(
            f"checkpoint {checkpoint.job_key!r} is not at version "
            f"{expected_version}; another worker advanced it. Re-read and retry "
            "rather than overwriting"
        )
    db.refresh(checkpoint)
    return checkpoint
