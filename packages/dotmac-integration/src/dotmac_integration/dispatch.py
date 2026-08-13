"""The three-phase dispatch seam — prepare, invoke, settle.

The one rule this file exists to enforce: **no database transaction is held
across provider I/O.** A connector call can take thirty seconds or hang until a
socket timeout, and a transaction open for that long holds row locks, blocks the
dispatcher behind it and eventually exhausts the pool. Integrations die of this
far more often than they die of bad payloads.

So a dispatch is three phases with two boundaries, and the middle one runs with
nothing open:

===========  ================================================================
**prepare**  resolve the explicit binding and the IMMUTABLE configuration pin,
             claim the delivery. Database. Short.
**invoke**   materialize referenced secrets and call the plugin. NO session,
             NO transaction. Slow and untrusted.
**settle**   record the typed outcome CONDITIONALLY against the claim.
             Database. Short.
===========  ================================================================

## Why the configuration is pinned in prepare

The revision id is captured with the claim, so the attempt runs against the
configuration that was current when it was claimed. Re-reading in invoke would
let a config change mid-flight produce an outcome recorded against a revision
that never ran.

## Why settle is conditional

A worker whose lease expired mid-call must not overwrite the outcome of the
worker that took over. Settle therefore writes only while the claim still holds,
and reports that it lost rather than silently clobbering.

## Secrets are materialized, never stored

`invoke` asks the caller-supplied resolver to turn `secret_refs` into values, in
memory, for one call. Nothing here writes them anywhere, and ADR-0009 is why the
resolver is injected rather than implemented: the module holds a reference and
the deployment decides how to dereference it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from dotmac_integration.discovery import ConnectorRegistry
from dotmac_integration.execution import claim_delivery, record_delivery_outcome
from dotmac_integration.models import (
    CapabilityBinding,
    ConnectorConfigRevision,
    ConnectorInstallation,
    DeliveryAttempt,
)
from dotmac_integration.policy import DEFAULT_POLICY, ExecutionPolicy
from dotmac_integration.retry import Outcome, OutcomeStatus
from dotmac_integration.spi import DispatchRequest

__all__ = [
    "DispatchError",
    "LostClaim",
    "PreparedDispatch",
    "invoke",
    "prepare",
    "settle",
]

#: Turns `{name: "bao://..."}` into `{name: "value"}`. Injected, never
#: implemented here: ADR-0009 keeps the module holding references and the
#: deployment deciding how to dereference them.
SecretResolver = Callable[[Mapping[str, str]], Mapping[str, str]]


class DispatchError(RuntimeError):
    """A dispatch could not be prepared."""


class LostClaim(RuntimeError):
    """The lease expired and another worker took over before settle."""


@dataclass(frozen=True, slots=True)
class PreparedDispatch:
    """Everything invoke needs, and nothing that holds a connection."""

    delivery_id: UUID
    installation_id: UUID
    binding_id: UUID
    connector_key: str
    capability_id: str
    event_type: str
    payload: dict
    config: dict
    secret_refs: dict
    idempotency_key: str
    #: The revision the attempt is pinned to. Captured with the claim so a
    #: mid-flight configuration change cannot be recorded as though it ran.
    config_revision_id: UUID | None
    attempt_number: int


def prepare(
    db: Any,
    delivery: DeliveryAttempt,
    *,
    registry: ConnectorRegistry,
    policy: ExecutionPolicy = DEFAULT_POLICY,
    now: datetime | None = None,
) -> PreparedDispatch | None:
    """Claim the delivery and read everything the call will need.

    Returns `None` when the claim was lost — the caller moves on rather than
    treating a contended row as an error.
    """
    if not claim_delivery(db, delivery, policy=policy, now=now):
        return None

    binding = (
        db.get(CapabilityBinding, delivery.capability_binding_id)
        if delivery.capability_binding_id
        else None
    )
    if binding is None:
        raise DispatchError(
            f"delivery {delivery.id} names no capability binding; there is "
            "nothing to route it to"
        )
    installation = db.get(ConnectorInstallation, binding.installation_id)
    if installation is None or installation.state != "enabled":
        raise DispatchError(f"installation for binding {binding.id} is not enabled")

    # The plugin must still be installed and compatible — checked here rather
    # than trusted from activation, which may have been months ago.
    registry.require_compatible(installation.connector_key)

    revision = (
        db.get(ConnectorConfigRevision, installation.current_config_revision_id)
        if installation.current_config_revision_id
        else None
    )
    return PreparedDispatch(
        delivery_id=delivery.id,
        installation_id=installation.id,
        binding_id=binding.id,
        connector_key=installation.connector_key,
        capability_id=binding.capability_id,
        event_type=delivery.event_type,
        payload=dict(delivery.payload_json or {}),
        config=dict((revision.config_json if revision else {}) or {}),
        secret_refs=dict((revision.secret_refs if revision else {}) or {}),
        idempotency_key=delivery.idempotency_key,
        config_revision_id=revision.id if revision else None,
        attempt_number=delivery.attempt_count,
    )


def invoke(
    prepared: PreparedDispatch,
    *,
    registry: ConnectorRegistry,
    resolve_secrets: SecretResolver,
) -> Outcome:
    """Call the connector. NO session, NO transaction, by signature.

    `db` is deliberately absent from this function's parameters — the boundary
    is enforced by what a caller CANNOT pass, not by a comment asking them not
    to. A plugin that wants a database has to be given one by someone breaking
    this contract visibly.

    A raising plugin becomes a RETRYABLE outcome rather than an exception: a
    connector that throws has told us nothing about whether the effect landed,
    and treating that as terminal would discard work that may simply have timed
    out.
    """
    plugin = registry.plugin(prepared.connector_key)
    handler = plugin.handler_for(prepared.capability_id)

    request = DispatchRequest(
        capability_id=prepared.capability_id,
        event_type=prepared.event_type,
        payload=prepared.payload,
        config=prepared.config,
        # Materialized for THIS call only. Nothing persists them.
        secrets=dict(resolve_secrets(prepared.secret_refs)),
        idempotency_key=prepared.idempotency_key,
    )
    try:
        outcome = handler(request)
    except Exception as exc:
        return Outcome(
            status=OutcomeStatus.RETRYABLE,
            error_code="connector_raised",
            error_detail=f"{type(exc).__name__}: {exc}",
        )
    if not isinstance(outcome, Outcome):
        return Outcome(
            status=OutcomeStatus.RETRYABLE,
            error_code="connector_contract",
            error_detail=(f"handler returned {type(outcome).__name__}, not an Outcome"),
        )
    return outcome


def settle(
    db: Any,
    delivery: DeliveryAttempt,
    outcome: Outcome,
    *,
    prepared: PreparedDispatch,
    policy: ExecutionPolicy = DEFAULT_POLICY,
    now: datetime | None = None,
) -> DeliveryAttempt:
    """Record the outcome, but ONLY while this worker still holds the claim.

    A worker whose lease expired during a slow provider call must not overwrite
    the result of the worker that took over. `LostClaim` says so out loud
    instead of clobbering, so the caller can log a real event rather than
    silently producing two outcomes for one attempt.
    """
    moment = now or datetime.now(UTC)
    db.refresh(delivery)

    if delivery.attempt_count != prepared.attempt_number:
        raise LostClaim(
            f"delivery {delivery.id} is on attempt {delivery.attempt_count}, "
            f"not {prepared.attempt_number}: this worker's lease expired and "
            "another took over. Refusing to overwrite its outcome"
        )
    # Some drivers (SQLite) hand back a NAIVE timestamp for a timestamptz
    # column. Comparing it directly raises, which would turn a healthy settle
    # into a crash on one backend and not another.
    leased_until = delivery.leased_until
    if leased_until is not None and leased_until.tzinfo is None:
        leased_until = leased_until.replace(tzinfo=UTC)
    if leased_until is not None and leased_until < moment:
        raise LostClaim(
            f"delivery {delivery.id} lease expired at {delivery.leased_until}; "
            "another worker may already be attempting it"
        )

    record_delivery_outcome(delivery, outcome, policy=policy, now=moment)
    db.flush()
    return delivery
