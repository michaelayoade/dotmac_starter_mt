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
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from dotmac_integration.admission import (
    AdmissionDecision,
    admit_installation,
    admit_runtime,
    apply_provider_cooldown,
)
from dotmac_integration.discovery import ConnectorRegistry
from dotmac_integration.execution import LostClaim, claim_delivery
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
    throttle_cooldown_seconds,
)
from dotmac_integration.spi import (
    ConnectorMode,
    DeliveryPlugin,
    DispatchRequest,
    accepts_manifest_digest,
    require_capability_mode,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from dotmac_integration.capability_registry import CapabilityRegistry

__all__ = [
    "DispatchError",
    "DispatchNotAdmitted",
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


class DispatchNotAdmitted(DispatchError):
    """The runtime WILL not dispatch this right now — stop, do not alert.

    The third answer, and it is neither of the other two. `DispatchUnavailable`
    means something is broken; this means something is deliberately switched off
    — the kill switch, a quarantined installation, or a concurrency ceiling —
    and it is expected, reversible, and already visible to whoever switched it.

    A caller that folded this into `DispatchUnavailable` would page an on-call
    engineer every time an operator quarantined a connector. One that folded it
    into `prepare` returning `None` would make a halted deployment look like a
    busy one, so the queue-depth graph climbs while every dashboard says the
    dispatcher is healthy.

    `.decision` carries the closed reason code and, when the refusing rule knows
    one, how long to wait — so a worker can back off correctly without parsing
    the message.
    """

    def __init__(self, decision: AdmissionDecision) -> None:
        super().__init__(decision.detail or decision.reason)
        self.decision = decision

    @property
    def reason(self) -> str:
        """The closed `admission.ADMISSION_REASONS` code."""
        return self.decision.reason


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
    the last thing that happens. The admission checks added later obey the same
    rule: they are refusals, so they run BEFORE the claim, and a halted or
    quarantined runtime therefore leaves the queue exactly as it found it.
    """
    # FIRST, and before a single query. A deployment that has been switched off
    # must not spend a round trip per queued row rediscovering that it is off.
    halted = admit_runtime(policy)
    if not halted.admitted:
        raise DispatchNotAdmitted(halted)

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

    # BEFORE the `enabled` check below, because a quarantined installation is
    # also not enabled and the operator is owed the specific answer: "the
    # platform stopped trusting this", not "this is off". `admit_installation`
    # deliberately declines every other non-enabled state, so the line below
    # keeps raising `DispatchUnavailable` exactly as it did.
    admitted = admit_installation(db, installation, policy=policy, now=now)
    if not admitted.admitted:
        raise DispatchNotAdmitted(admitted)

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


def _connector_error_detail(exc: BaseException) -> str:
    """What may be persisted about an exception a CONNECTOR raised.

    The type, and nothing else. A plugin's exception message is built from
    whatever the plugin knows — which at this point includes the materialized
    secrets and the provider payload it was called with — so the text must not
    reach a stored column.

    A non-identifier type name degrades to `Exception` rather than being
    stored: `type(exc).__name__` is attacker-influenced only via a crafted
    class, but the whole point is that the shape is guaranteed structurally and
    not argued about.
    """
    name = type(exc).__name__
    return name if name.isidentifier() else "Exception"


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
    # a binding pointed at an ingress-only connector used to fail with an
    # AttributeError from inside the lookup — which reads as a broken plugin
    # rather than as a binding pointed at a connector that cannot deliver.
    require_capability_mode(plugin, prepared.capability_id, ConnectorMode.DELIVERY)
    # The `cast` is honest rather than convenient: `registry.plugin` is typed to
    # the BASE protocol and `handler_for` lives on `DeliveryPlugin`.
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
            # TYPE NAME ONLY, and `.isidentifier()` is what makes that
            # structural rather than a convention — a message cannot masquerade
            # as a type name. Identical to `ingress.ConnectorRaised`, which
            # already held this line for the request path.
            #
            # This one is the sharper case of the two. The handler was just
            # handed MATERIALIZED SECRETS (see `secrets=` above), and
            # `error_detail` is PERSISTED — `execution` writes it to
            # `inbox_receipts.error_detail` and `delivery_attempts.error_detail`,
            # both `Text`. So a connector that interpolated a resolved
            # credential into its own exception did not merely log it, it stored
            # it, in a column an operator reads and a support export copies.
            error_detail=_connector_error_detail(exc),
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


def _require_valid_result(
    outcome: Outcome,
    *,
    prepared: PreparedDispatch,
    registry: CapabilityRegistry | None,
) -> None:
    """ADR-0024 § 10.4.3 — the result gate, BEFORE the claim-guarded UPDATE.

    Before the write, so a connector cannot settle a delivery with a shape no
    product can read. After that write the attempt is `delivered` and final; a
    product reading the result would be the first thing to discover the shape
    was wrong, at which point the outbox says the effect succeeded and there is
    nothing left to retry.

    ## Only on a SUCCESS

    A failed attempt has no normalized result to publish, and demanding one
    would refuse to record the failure — turning "the provider returned 500"
    into a permanently unsettled row with a live lease. `RECONCILIATION_REQUIRED`
    is the sharpest case: `invoke` produces it for a connector that RAISED, so
    insisting on a valid result body there would guarantee the one outcome
    nobody may lose is the one that cannot be written.

    ## The refusal reaches the dispatcher, not the row

    Raising leaves the delivery `in_flight` with its lease. That is correct: the
    engine has not decided anything about this attempt, and a lease expiry hands
    it to a worker that will try again. The alternative — recording a
    connector's malformed result as a terminal state — is the outcome this gate
    exists to prevent.
    """
    if outcome.status is not OutcomeStatus.SUCCEEDED:
        return
    from dotmac_integration.capability_registry import capability_registry

    declared = registry if registry is not None else capability_registry()
    contract = declared.get(prepared.capability_id)
    if contract.result_schema is None and outcome.result is None:
        # A capability whose contract publishes no result body, and a connector
        # that returned none. Nothing disagrees, so nothing is refused — this is
        # the fire-and-forget delivery shape, where the effect IS the outcome
        # and there is no normalized body to read. `require_result` would refuse
        # it for a missing schema, which would be true and useless.
        return
    contract.require_result(outcome.result)


def settle(
    db: Any,
    delivery: DeliveryAttempt,
    outcome: Outcome,
    *,
    prepared: PreparedDispatch,
    policy: ExecutionPolicy = DEFAULT_POLICY,
    now: datetime | None = None,
    registry: CapabilityRegistry | None = None,
) -> DeliveryAttempt:
    """Record the outcome in ONE conditional UPDATE guarded by the claim.

    Read-then-write leaves a window: a takeover can happen between reading the
    row and writing the result, and the loser silently overwrites the winner's
    outcome — two outcomes for one attempt, with no way to tell which ran.

    So the guard IS the write. `state`, `attempt_count` and the lease are all in
    the WHERE clause, and `rowcount != 1` means this worker no longer holds the
    claim. The database decides, which also removes the naive/aware timestamp
    comparison the previous read-then-compare version needed.

    ## And backpressure, for the same reason it is not somewhere else

    A provider throttle is learned HERE and nowhere else: this is the moment the
    engine holds both the outcome and a session, immediately after the network
    call has returned. So the installation-wide cooldown is applied here, AFTER
    the claim-guarded write succeeds — a worker that lost its lease must not get
    to delay a queue on the strength of an outcome it was not allowed to record.

    It is still two short statements in one short transaction, and neither waits
    on anything: the pause is a `next_attempt_at`, not a sleep.
    """
    from sqlalchemy import update

    _require_valid_result(outcome, prepared=prepared, registry=registry)
    moment = now or datetime.now(UTC)
    next_state_value = next_state(
        outcome, attempt_count=prepared.attempt_number, policy=policy
    )
    values: dict[str, Any] = {
        "state": next_state_value,
        "leased_until": None,
        "error_code": outcome.error_code,
        "error_detail": outcome.error_detail,
        # Evidence belongs to THIS attempt. A later retry replaces a 429 with
        # the success it actually observed rather than retaining stale facts.
        "provider_reference": outcome.provider_reference,
        "provider_status_code": outcome.provider_status_code,
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

    # Only now — the claim held, so this outcome is this installation's actual
    # observation rather than a stale worker's opinion of it.
    if policy.apply_provider_backpressure:
        cooldown = throttle_cooldown_seconds(outcome, policy=policy)
        if cooldown is not None:
            apply_provider_cooldown(
                db,
                installation_id=prepared.installation_id,
                cooldown_seconds=cooldown,
                now=moment,
                policy=policy,
            )

    db.refresh(delivery)
    return delivery
