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
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from dotmac_integration.discovery import ConnectorRegistry
from dotmac_integration.execution import claim_delivery
from dotmac_integration.models import (
    CapabilityBinding,
    ConnectorConfigRevision,
    ConnectorInstallation,
    DeliveryAttempt,
)
from dotmac_integration.policy import DEFAULT_POLICY, ExecutionPolicy
from dotmac_integration.retry import (
    Outcome,
    OutcomeStatus,
    next_state,
    retry_delay_seconds,
)
from dotmac_integration.spi import (
    ConnectorMode,
    DeliveryPlugin,
    DispatchRequest,
    accepts_manifest_digest,
    require_mode,
)

__all__ = [
    "DispatchError",
    "DispatchUnavailable",
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


class DispatchUnavailable(DispatchError):
    """The CONFIGURATION cannot serve this dispatch — alert and stop.

    Deliberately distinct from `prepare` returning `None`:

        None                  the database did not grant this worker a claim.
                              Normal under contention; the caller moves on.
        DispatchUnavailable   a disabled installation, a missing binding or
                              plugin, or a manifest pin the installed connector
                              no longer honours. Nothing will fix itself, and a
                              caller that treated this as contention would idle
                              silently on a misconfiguration.
    """


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
    payload: dict[str, object]
    config: dict[str, object]
    secret_refs: dict[str, str]
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
    """Validate first, THEN claim. Returns `None` only when the claim was lost.

    Order matters and this is the whole reason the function is shaped this way.
    Claiming before validating leaves a misconfigured delivery `in_flight` with
    a live lease when preflight raises: nothing retries it until the lease
    expires, and the queue reports busy rather than broken — the worst of both.

    So everything that can refuse runs against unclaimed state, and the claim is
    the last thing that happens.
    """
    binding = (
        db.get(CapabilityBinding, delivery.capability_binding_id)
        if delivery.capability_binding_id
        else None
    )
    if binding is None:
        raise DispatchUnavailable(
            f"delivery {delivery.id} names no capability binding; there is "
            "nothing to route it to"
        )
    if binding.state != "enabled":
        raise DispatchUnavailable(
            f"binding {binding.id} is {binding.state!r}, not enabled"
        )

    installation = db.get(ConnectorInstallation, binding.installation_id)
    if installation is None:
        raise DispatchUnavailable(f"binding {binding.id} has no installation")
    if installation.state != "enabled":
        raise DispatchUnavailable(
            f"installation {installation.name!r} is {installation.state!r}, "
            "not enabled"
        )

    # The plugin must still be installed and SPI-compatible — checked here
    # rather than trusted from activation, which may have been months ago.
    try:
        registry.require_compatible(installation.connector_key)
        plugin = registry.plugin(installation.connector_key)
    except Exception as exc:
        raise DispatchUnavailable(
            f"connector {installation.connector_key!r} is not usable in this "
            f"runtime: {exc}"
        ) from exc

    # And it must still honour the manifest pin this installation was adopted
    # against. A connector that superseded the pin without keeping it in its
    # historical window no longer implements the payload shape the installation
    # was configured for, so the call must not reach a provider.
    if not accepts_manifest_digest(plugin, installation.manifest_digest):
        raise DispatchUnavailable(
            f"installation {installation.name!r} is pinned to manifest "
            f"{installation.manifest_digest[:12]}, which connector "
            f"{installation.connector_key!r} v{plugin.manifest.version} no "
            "longer honours. Adopt the current manifest before dispatching"
        )

    revision = (
        db.get(ConnectorConfigRevision, installation.current_config_revision_id)
        if installation.current_config_revision_id
        else None
    )

    # LAST: nothing below can refuse, so nothing can strand a claimed row.
    if not claim_delivery(db, delivery, policy=policy, now=now):
        return None

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

    A raising plugin becomes RECONCILIATION_REQUIRED, NOT retryable. A throw
    tells us nothing about whether the effect LANDED — a socket closed mid-write
    may have been fully applied at the provider — so retrying risks doing it
    twice and dead-lettering hides it. Only an explicit connector outcome may
    request a retry, because the connector is the only party that knows the
    effect did not happen.
    """
    plugin = registry.plugin(prepared.connector_key)
    # BEFORE the lookup, not after. `handler_for` lives on `DeliveryPlugin`, so
    # an ingress-only connector behind a binding used to fail with an
    # AttributeError from inside the lookup — which reads as a broken plugin
    # rather than as a binding pointed at a connector that cannot deliver.
    require_mode(plugin, ConnectorMode.DELIVERY)
    # The `cast` is honest rather than convenient: `registry.plugin` is typed to
    # the BASE protocol, and `handler_for` moved to `DeliveryPlugin`.
    # `require_mode` on the line above is what turned "this connector delivers"
    # from an assumption into a checked refusal, so the cast asserts something
    # already proven rather than something hoped for.
    handler = cast(DeliveryPlugin, plugin).handler_for(prepared.capability_id)

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
            status=OutcomeStatus.RECONCILIATION_REQUIRED,
            error_code="connector_raised",
            error_detail=f"{type(exc).__name__}: {exc}",
        )
    if not isinstance(outcome, Outcome):
        return Outcome(
            # Same reasoning: a handler that returned the wrong type may still
            # have performed the call before returning it.
            status=OutcomeStatus.RECONCILIATION_REQUIRED,
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
    """Record the outcome in ONE conditional UPDATE guarded by the claim.

    Read-then-write leaves a window: a takeover can happen between reading the
    row and writing the result, and the loser silently overwrites the winner's
    outcome — two outcomes for one attempt, with no way to tell which ran.

    So the guard IS the write. `state`, `attempt_count` and the lease are all in
    the WHERE clause, and `rowcount != 1` means this worker no longer holds the
    claim. The database decides, which also removes the naive/aware timestamp
    comparison the previous read-then-compare version needed.
    """
    from sqlalchemy import update

    moment = now or datetime.now(UTC)
    next_state_value = next_state(
        outcome, attempt_count=prepared.attempt_number, policy=policy
    )
    values: dict[str, Any] = {
        "state": next_state_value,
        "leased_until": None,
        "error_code": outcome.error_code,
        "error_detail": outcome.error_detail,
    }
    if next_state_value == "delivered":
        values.update(
            delivered_at=moment,
            next_attempt_at=None,
            error_code=None,
            error_detail=None,
        )
    elif next_state_value == "retryable":
        values["next_attempt_at"] = moment + timedelta(
            seconds=retry_delay_seconds(prepared.attempt_number, outcome, policy=policy)
        )
    else:
        # dead_letter / reconciliation_required: nothing is due, and leaving a
        # schedule would make a dispatcher pick it up forever.
        values["next_attempt_at"] = None

    result = db.execute(
        update(DeliveryAttempt)
        .where(
            DeliveryAttempt.id == delivery.id,
            DeliveryAttempt.state == "in_flight",
            # The attempt number this worker claimed. A takeover increments it.
            DeliveryAttempt.attempt_count == prepared.attempt_number,
            DeliveryAttempt.leased_until.is_not(None),
            DeliveryAttempt.leased_until >= moment,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise LostClaim(
            f"delivery {delivery.id}: this worker no longer holds the claim it "
            f"took at attempt {prepared.attempt_number} — the lease expired or "
            "another worker took over. Refusing to overwrite its outcome"
        )
    db.refresh(delivery)
    return delivery
