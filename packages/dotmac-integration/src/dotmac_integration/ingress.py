"""The three-phase ingress seam — prepare, verify_and_normalize, record_batch.

Mirrors `dispatch.py`, and for the same reason: **no database transaction is
held across a plugin call.** The two deliberate differences are stated where
they occur — there is no lease (nothing pre-exists to claim), and a raising
plugin RAISES rather than becoming a recorded outcome (there is no row to record
it against).

=========================  ===================================================
**prepare_ingress**        resolve the minted endpoint, the manifest pin and
                           the active config revision. Database. Short. Writes
                           nothing.
**verify_and_normalize**   materialize secrets, verify the RAW bytes, then
                           shape them into events AND the acknowledgement. NO
                           session, NO transaction.
**record_batch**           record the WHOLE normalized tuple, atomically.
                           Database. Short.
=========================  ===================================================

## Handshake and delivery are two operations, not one with an inference

`receive` serves deliveries and `answer_challenge` serves handshakes, and
neither falls through to the other. SPI 1.1 inferred a handshake from an empty
body; that was wrong in both directions — a bodyless POST is still a DELIVERY,
and a provider that confirms a subscription with a bodied request could not
handshake at all. The assembly mounts GET on one and POST on the other, so the
operation is stated by the request line rather than guessed from a byte count.

## The connector writes the BODY; the engine decides the STATUS

An ingress status code is a retry instruction — 200 means "never send this
again" — so it stays with the engine, which is the only party that knows whether
the batch committed. A connector supplies only an `Acknowledgement`: body bytes
and, at most, a validated media type. No connector-chosen status, no arbitrary
response headers.

That acknowledgement is built by `normalize`, BEFORE anything is persisted, and
emitted only after the whole batch has committed. `_accepted` therefore runs no
plugin code at all — a plugin call after the commit would put a raise on the far
side of a durable write, answering 5xx for events that are safely stored and
inviting the provider to redeliver them forever.

## The URL addresses ONE minted endpoint, never a pair

`capability_bindings.ingress_endpoint_key` is the whole address. From it this
module derives installation, connector, capability, pinned manifest, active
config revision and secret references. A `(connector_key, capability_id)` pair
could not: several installations may serve one pair — a production and a test
provider account, or two operators' — so the pair names a set, not an endpoint.

## One collision rolls back the WHOLE provider batch

A partial write leaves the provider believing the batch was accepted while some
events were never recorded, and a provider does not resend the ones that landed.
So `record_batch` takes the tuple and never one event: there is no loop for a
caller to put a transaction boundary inside, which makes whole-batch atomicity
structural rather than a convention. `receive_verified` is idempotent on
`(capability_binding_id, provider_event_id)`, so a whole-batch retry after a
refusal is correct rather than duplicative.

The single most important line in this file is that `receive` catches
`IngressRefused` OUTSIDE the `with` block, not inside it. Catching inside would
let the block exit cleanly, persisting the events recorded before the collision
— which the provider, told 5xx, would then redeliver on top of.

## Nothing here logs, and nothing here can

The raw body, the signature headers, the query parameters and the materialized
secrets are ephemeral. Structural mechanisms keep them that way, none of which
relies on care: `IngressRefused.__init__` takes no arguments so there is nothing
to interpolate; `ConnectorRaised` carries a validated TYPE NAME and is raised
`from None`; `IngressOutcome` has no SCALAR field able to hold request material;
`record_batch` passes `headers=None`; and this module installs no logger.

The last three are about what an exception carries out rather than what a field
holds, because that is the route the first four leave open. Every database
error becomes a typed refusal `from None` — a driver's `str` embeds
`[SQL: ...] [parameters: (...)]`, which is the normalized payload verbatim. The
materialized-secrets local is cleared in a `finally` — an escaping exception
pins the frame that holds it, and a reporter capturing frame locals uploads it.
And `PreparedIngress.endpoint_key` is `repr=False`, because the carrier is a
frame local in every one of those tracebacks and the key is a bearer secret.

`IngressRequest` and `Acknowledgement` are `repr=False` for exactly that last
reason. The envelope holds the raw body and every header — including an
authorization header or cookie a misconfigured proxy passed through — and it is
a frame local in every traceback that leaves the plugin phase. The
acknowledgement is assumed to hold request material too, because an echo
handshake is *defined* as returning a slice of the request. Neither is ever
persisted: `record_batch` writes the normalized payload a connector chose, and
nothing else.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Final, cast
from uuid import UUID

from dotmac_integration.discovery import ConnectorRegistry
from dotmac_integration.execution import (
    ExecutionError,
    ProviderEventIdentityCollision,
    receive_verified,
)
from dotmac_integration.models import (
    CapabilityBinding,
    ConnectorConfigRevision,
    ConnectorInstallation,
    InboxReceipt,
)
from dotmac_integration.selection import _usable
from dotmac_integration.spi import (
    Acknowledgement,
    ConnectorMode,
    InboundEvent,
    IngressHandler,
    IngressPlugin,
    IngressRequest,
    ModeNotDeclaredError,
    accepts_manifest_digest,
    require_mode,
)

__all__ = [
    "Acknowledgement",
    "ConnectorContract",
    "ConnectorRaised",
    "ConnectorUnavailable",
    "EndpointNotServiceable",
    "EndpointNotUsable",
    "EndpointUnknown",
    "EventIdentityCollision",
    "IngressCode",
    "IngressError",
    "IngressOutcome",
    "IngressRefused",
    "IngressRequest",
    "ManifestPinUnhonoured",
    "ModeNotAvailable",
    "NotAChallenge",
    "PayloadTooLarge",
    "PreparedIngress",
    "ReceiptWriteFailed",
    "ReceiptWriteRaced",
    "SignatureRejected",
    "UnitOfWork",
    "answer_challenge",
    "challenge_response",
    "prepare_ingress",
    "receive",
    "record_batch",
    "refusal_outcome",
    "verify_and_normalize",
]

#: Turns `{name: "bao://..."}` into `{name: "value"}`. Same shape and same
#: ADR-0009 reasoning as dispatch's: the module holds references and the
#: deployment decides how to dereference them.
SecretResolver = Callable[[Mapping[str, str]], Mapping[str, str]]

#: The deployment decides what a unit of work IS — engine, pool, isolation
#: level, commit-on-clean-exit, roll-back-on-exception. This module decides how
#: MANY there are and where their boundaries fall, because whole-batch
#: atomicity is a correctness property of ingress rather than a deployment
#: preference: hand-composed by a caller it would be enforced only by that
#: caller getting it right.
UnitOfWork = Callable[[], AbstractContextManager[Any]]

#: The engine's defaults, used when a connector leaves `media_type` unset.
#: `text/plain` for a handshake because providers compare the RAW echoed body;
#: JSON for a delivery acknowledgement because that is what an assembly's other
#: routes answer.
_HANDSHAKE_MEDIA_TYPE: Final[str] = "text/plain"
_DELIVERY_MEDIA_TYPE: Final[str] = "application/json"

#: 48 lowercase hex characters — 192 bits, as produced by `secrets.token_hex`.
_ENDPOINT_KEY_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{48}$")

#: The unique index a concurrent redelivery violates. Named here so the racing
#: insert becomes a TYPED refusal instead of a raw driver error reaching a
#: route handler that would have no idea what to do with it.
_RECEIPT_UNIQUE: Final[str] = "uq_inbox_receipts_binding_event"


class IngressCode(str, Enum):
    """The outcome vocabulary.

    `str, Enum` like `OutcomeStatus`, so a structural serializer passes it
    through without the assembly writing a per-type branch.
    """

    ACCEPTED = "accepted"
    CHALLENGE_ANSWERED = "challenge_answered"
    NOT_A_CHALLENGE = "not_a_challenge"
    ENDPOINT_UNKNOWN = "endpoint_unknown"
    ENDPOINT_NOT_USABLE = "endpoint_not_usable"
    CONNECTOR_UNAVAILABLE = "connector_unavailable"
    MANIFEST_PIN_UNHONOURED = "manifest_pin_unhonoured"
    MODE_NOT_AVAILABLE = "mode_not_available"
    SIGNATURE_REJECTED = "signature_rejected"
    CONNECTOR_RAISED = "connector_raised"
    CONNECTOR_CONTRACT = "connector_contract"
    EVENT_IDENTITY_COLLISION = "event_identity_collision"
    RECEIPT_WRITE_RACED = "receipt_write_raced"
    RECEIPT_WRITE_FAILED = "receipt_write_failed"
    PAYLOAD_TOO_LARGE = "payload_too_large"


@dataclass(frozen=True, slots=True)
class IngressOutcome:
    """THE REDACTION BOUNDARY, and the ENGINE'S HALF of the response.

    A consuming assembly serializes the scalar fields of this object into an
    HTTP response with no field allowlist, so a field able to hold a body, a
    header, a query parameter, a secret or the endpoint key would leak it by
    construction. There is no such field among them, and that is checked rather
    than intended.

    ## The one field that DOES carry material, and why it is safe

    `acknowledgement` holds bytes a CONNECTOR built, on purpose, to be written
    back to the provider that sent the request. That is the opposite direction
    from a leak: an echo handshake is *defined* as returning a slice of the
    request to its own sender. It is a typed `Acknowledgement` rather than a
    loose `bytes` so the distinction is visible at every call site, and that
    type is `repr=False` so it still cannot spill into a log line rendered from
    this object.

    The split of authority is the point. The connector owns the BODY and, at
    most, the media type. The engine owns `status_code` and `code` — the retry
    instruction and the failure classification — because only the engine knows
    whether the batch committed, and a status invented anywhere else silently
    destroys events.

    Note the contrast with `dispatch.invoke`, which interpolates
    `f"{type(exc).__name__}: {exc}"` into `error_detail`. On this path that
    string would be built from provider-controlled bytes, so only the type name
    travels — inside the exception, never into a stored or returned field.
    """

    status_code: int
    code: IngressCode
    #: Operator-meaningful ids, both already disclosed in existing error text.
    #: The ENDPOINT KEY is deliberately absent: whoever holds it can drive the
    #: connector's `verify`, and echoing it into a 404 hands it to a scanner.
    installation_id: UUID | None = None
    binding_id: UUID | None = None
    normalized: int = 0
    recorded: int = 0
    duplicates: int = 0
    receipt_ids: tuple[UUID, ...] = ()
    #: What to write back. Present on both SUCCESS paths and `None` on every
    #: refusal — a refusal body is the engine's classification, not something a
    #: connector that may never have run gets to shape. When present its
    #: `media_type` is always resolved, so no assembly has to decide what an
    #: unset one means.
    acknowledgement: Acknowledgement | None = None


# ── Refusals ────────────────────────────────────────────────────────────────


class IngressError(RuntimeError):
    """An inbound request could not be served."""


class IngressRefused(IngressError):
    """Every refusal carries a CONSTANT message and takes no arguments.

    `__init__` accepts nothing, so interpolating a request fragment into a
    refusal is not a discipline someone has to remember — it is impossible.
    `ConnectorRaised` is the one exception and it takes a validated type name.

    `CODE` and `STATUS` are annotated but unassigned here: a grouping base is
    never raised and must not carry a status a subclass could inherit by
    accident. Every LEAF refusal decides both, and the test suite walks the
    hierarchy to make a new one without a decision fail the build.
    """

    MESSAGE: str = ""
    CODE: IngressCode
    STATUS: int

    def __init__(self) -> None:
        super().__init__(self.MESSAGE)


class EndpointUnknown(IngressRefused):
    """No binding carries this key, or the key is malformed.

    ONE refusal for both, deliberately: distinguishing them would turn the
    endpoint into a probing oracle that tells a scanner when it has guessed the
    right SHAPE.
    """

    MESSAGE = "no ingress endpoint is minted for this address"
    CODE = IngressCode.ENDPOINT_UNKNOWN
    STATUS = 404


class EndpointNotUsable(IngressRefused):
    """The binding or its installation is not enabled.

    404 like `EndpointUnknown`, so an outside observer cannot enumerate live
    endpoints by status; the CODE differs so an operator can tell a mistyped URL
    from a disabled binding. The tradeoff is stated rather than hidden: an
    ACCIDENTAL disable loses the events. That is the honest answer, because a
    disabled binding is an operator's stated intent not to receive.
    """

    MESSAGE = "the binding behind this endpoint is not enabled"
    CODE = IngressCode.ENDPOINT_NOT_USABLE
    STATUS = 404


class EndpointNotServiceable(IngressRefused):
    """Grouping base for "our side is wrong" — never raised directly.

    All three subclasses are OUR misconfiguration, and all three answer 503,
    because the provider's redelivery window is the only remaining copy of
    those events.
    """


class ConnectorUnavailable(EndpointNotServiceable):
    """The connector distribution is not installed, or its SPI range excludes
    the running module."""

    MESSAGE = "the connector behind this endpoint is not usable in this runtime"
    CODE = IngressCode.CONNECTOR_UNAVAILABLE
    STATUS = 503


class ManifestPinUnhonoured(EndpointNotServiceable):
    """The installed connector no longer honours this installation's pin."""

    MESSAGE = (
        "the installed connector no longer honours this installation's "
        "manifest pin; adopt the current manifest"
    )
    CODE = IngressCode.MANIFEST_PIN_UNHONOURED
    STATUS = 503


class ModeNotAvailable(EndpointNotServiceable):
    """The connector does not declare or implement INGRESS.

    A STATED refusal, raised before any `ingress_handler_for` lookup — never an
    AttributeError from inside one.
    """

    MESSAGE = "the connector behind this endpoint does not receive"
    CODE = IngressCode.MODE_NOT_AVAILABLE
    STATUS = 503


class SignatureRejected(IngressRefused):
    """`verify` returned False. NOTHING is persisted.

    401, because providers do not retry it — correct here, since redelivering an
    unverifiable body fails identically. There is no table for unverified bodies
    and there must not be one: storing what failed verification is storing
    exactly the material that must not be stored. The evidence is the 401 plus
    the edge's access log.
    """

    MESSAGE = "the request signature was rejected by the connector"
    CODE = IngressCode.SIGNATURE_REJECTED
    STATUS = 401


class NotAChallenge(IngressRefused):
    """`challenge` returned `None`: the connector does not recognise the
    request its own handshake route was given."""

    MESSAGE = "the connector does not recognise this request as a handshake"
    CODE = IngressCode.NOT_A_CHALLENGE
    STATUS = 400


class ConnectorContract(IngressRefused):
    """A plugin returned a shape the engine will not act on.

    `normalize` owes `(tuple[InboundEvent, ...], Acknowledgement | None)` and
    `challenge` owes `Acknowledgement | None`. Same reasoning as
    `dispatch.invoke`'s `connector_contract`: the engine does not iterate, index
    or write back whatever it is handed. It matters more here than it reads,
    because the second element of that pair goes into an HTTP response body — a
    plugin returning a bare tuple of events would otherwise have its first event
    written back to the provider.
    """

    MESSAGE = "the connector returned a shape this engine will not act on"
    CODE = IngressCode.CONNECTOR_CONTRACT
    STATUS = 503


class EventIdentityCollision(IngressRefused):
    """One `provider_event_id`, two different payload digests.

    The WHOLE batch rolls back and the provider is told 5xx, so it redelivers.
    This recurs until an operator acts — that is the intended page, and it is
    strictly better than discarding real content.
    """

    MESSAGE = "a provider event id arrived with different content than before"
    CODE = IngressCode.EVENT_IDENTITY_COLLISION
    STATUS = 503


class ReceiptWriteRaced(IngressRefused):
    """A concurrent insert violated the receipt uniqueness constraint.

    Telling a benign race (same digest, would have been a no-op duplicate) from
    a real collision needs a re-read, which an aborted transaction cannot do. So
    it gets its own code and the same 503: the whole batch rolls back, the
    provider retries, and the retry finds the rows and answers 200 with
    `duplicates`. Honest and self-healing.
    """

    MESSAGE = "a concurrent write recorded this batch first"
    CODE = IngressCode.RECEIPT_WRITE_RACED
    STATUS = 503


class ReceiptWriteFailed(IngressRefused):
    """The batch could not be written, for a reason that is not the race.

    THE DRIVER'S MESSAGE MUST NOT TRAVEL. SQLAlchemy's `StatementError.__str__`
    appends `[SQL: ...] [parameters: (...)]` unless the engine was built with
    `hide_parameters=True`, which no engine in this fleet sets — and those
    parameters are the normalized payload and the provider event id, verbatim.
    An untyped database error reaching the edge is therefore not merely a 500
    with no decided status: it is provider content in an ERROR log line, from a
    module that installs no logger precisely so that cannot happen. This refusal
    is raised `from None`, which is the part that actually stops the string.

    ONE code for the whole family, because the module genuinely cannot tell the
    cases apart from inside an aborted transaction: a connector that normalized
    an over-long id or an unserializable payload, a schema drift on our side, a
    `provider_event_id` that was blank. 503 for the same reason
    `ReceiptWriteRaced` beside it answers 503: the provider's redelivery window
    is the only remaining copy of those events, and `receive_verified` is
    idempotent so the retry is safe. The whole batch rolls back — the
    conversion happens in
    `record_batch` and is caught OUTSIDE the unit of work, exactly as a
    collision is.
    """

    MESSAGE = "the batch could not be recorded"
    CODE = IngressCode.RECEIPT_WRITE_FAILED
    STATUS = 503


class PayloadTooLarge(IngressRefused):
    """DEFINED here, RAISED at the edge.

    A generic request-size limit belongs to the assembly: a byte cap that varies
    by connector would be a connector-specific limit, and this module may not
    hold one. So no function here takes a `max_bytes` parameter and nothing here
    raises this. The edge constructs it and calls `refusal_outcome`, which is
    how it gets the status and the code without authoring either.
    """

    MESSAGE = "the request body exceeded the accepted size"
    CODE = IngressCode.PAYLOAD_TOO_LARGE
    STATUS = 413


class ConnectorRaised(IngressRefused):
    """`verify`, `normalize` or `challenge` threw. Nothing is recorded.

    The ONE refusal taking an argument, and it takes a TYPE NAME only: the
    plugin's exception message is built from provider-controlled bytes, so the
    text must not travel. `.isidentifier()` is what makes that structural — a
    message cannot masquerade as a type name.
    """

    MESSAGE = "the connector raised while handling the request"
    CODE = IngressCode.CONNECTOR_RAISED
    STATUS = 503

    def __init__(self, error_type: str) -> None:
        safe = error_type if error_type.isidentifier() else "Exception"
        RuntimeError.__init__(self, f"{self.MESSAGE} ({safe})")


def refusal_outcome(
    exc: IngressRefused, *, prepared: PreparedIngress | None = None
) -> IngressOutcome:
    """Turn any refusal into the one shape, so no caller authors a status code.

    An ingress status IS a retry instruction to the provider — the one place an
    assembly's invention would silently destroy events. This function is the
    only place a status is chosen, and the EDGE uses it too: it constructs
    `PayloadTooLarge()` and passes it here rather than writing `413` itself.
    """
    return IngressOutcome(
        status_code=exc.STATUS,
        code=exc.CODE,
        installation_id=prepared.installation_id if prepared else None,
        binding_id=prepared.binding_id if prepared else None,
    )


# ── The carrier ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PreparedIngress:
    """Everything the plugin call needs, and nothing that holds a connection.

    Every field is a `UUID`, a `str`, a plain `dict` or `None`. No ORM object
    travels: one would drag a session into the phase whose whole point is not
    having one, and would lazy-load against a transaction that has closed.
    `slots` is load-bearing for the same reason — a session cannot be smuggled
    on as an ad-hoc attribute.

    `endpoint_key` is `repr=False`, and that is the same rule `IngressOutcome`
    states: whoever holds the key can drive the connector's `verify`. The
    carrier is a frame local in every traceback that leaves the plugin phase, so
    a default `repr` puts a bearer secret into any log line, error report or
    debugger frame that renders it. The value is still THERE — this is a
    rendering rule, not a removal.
    """

    endpoint_key: str = field(repr=False)
    installation_id: UUID
    binding_id: UUID
    connector_key: str
    capability_id: str
    config: dict[str, Any] = field(default_factory=dict)
    #: REFERENCES, never values. Materialized only inside
    #: `verify_and_normalize`, into a local that goes out of scope on return.
    secret_refs: dict[str, Any] = field(default_factory=dict)
    config_revision_id: UUID | None = None
    manifest_digest: str = ""


# ── Phase 1: prepare ────────────────────────────────────────────────────────


def prepare_ingress(
    db: Any, *, endpoint_id: str, registry: ConnectorRegistry
) -> PreparedIngress:
    """Resolve the endpoint, the pin and the config revision. Database. Short.

    WRITES NOTHING, so the enclosing unit of work's clean exit is a no-op.

    The order mirrors `dispatch.prepare`, and the first step is the one worth
    naming: the key is validated against a fixed shape BEFORE any query, so an
    unbounded attacker string never reaches SQL and a malformed key is
    indistinguishable from an unknown one to an outside observer.

    :raises EndpointUnknown: malformed key, or no binding carries it.
    :raises EndpointNotUsable: the binding or installation is not enabled.
    :raises ConnectorUnavailable: the connector is missing or SPI-incompatible.
    :raises ManifestPinUnhonoured: the pin is no longer honoured.
    :raises ModeNotAvailable: the connector does not receive.
    """
    from sqlalchemy import select

    if not _ENDPOINT_KEY_RE.fullmatch(endpoint_id):
        raise EndpointUnknown()

    row = db.execute(
        select(CapabilityBinding, ConnectorInstallation)
        .join(
            ConnectorInstallation,
            ConnectorInstallation.id == CapabilityBinding.installation_id,
        )
        .where(CapabilityBinding.ingress_endpoint_key == endpoint_id)
    ).first()
    if row is None:
        raise EndpointUnknown()
    binding, installation = row

    # The same predicate `selection` applies, reused rather than restated: two
    # copies of "usable" drift, and the ingress copy would drift silently
    # because nothing dispatches through it.
    if not _usable(binding, installation):
        raise EndpointNotUsable()

    # Re-checked here rather than trusted from activation, which may have been
    # months ago and cannot know what is installed now.
    try:
        registry.require_compatible(installation.connector_key)
        plugin = registry.plugin(installation.connector_key)
    except Exception as exc:
        raise ConnectorUnavailable() from exc

    if not accepts_manifest_digest(plugin, installation.manifest_digest):
        raise ManifestPinUnhonoured()

    # BEFORE any `ingress_handler_for` lookup, exactly as `dispatch.invoke`
    # does for DELIVERY. `ModeNotDeclaredError`'s message names the connector
    # key and its declared modes — all module-side, so `from exc` is safe here
    # in a way it is not for a plugin's own exception.
    try:
        require_mode(plugin, ConnectorMode.INGRESS)
    except ModeNotDeclaredError as exc:
        raise ModeNotAvailable() from exc

    revision = (
        db.get(ConnectorConfigRevision, installation.current_config_revision_id)
        if installation.current_config_revision_id
        else None
    )
    return PreparedIngress(
        endpoint_key=endpoint_id,
        installation_id=installation.id,
        binding_id=binding.id,
        connector_key=installation.connector_key,
        capability_id=binding.capability_id,
        # `dict(...)` copies, so nothing lazy-loads once the session is gone.
        config=dict((revision.config_json if revision else {}) or {}),
        secret_refs=dict((revision.secret_refs if revision else {}) or {}),
        config_revision_id=revision.id if revision else None,
        manifest_digest=installation.manifest_digest,
    )


# ── Phase 2: the plugin call ────────────────────────────────────────────────


def _handler(prepared: PreparedIngress, registry: ConnectorRegistry) -> IngressHandler:
    """The handler lookup, after `prepare_ingress` proved the mode.

    The `cast` is honest rather than convenient: `registry.plugin` is typed to
    the BASE protocol, and `ingress_handler_for` lives on `IngressPlugin`.
    `require_mode` — which ran in `prepare_ingress`, before anything reached
    this file's plugin phase — is what turned "this connector receives" from an
    assumption into a checked refusal.
    """
    plugin = cast(IngressPlugin, registry.plugin(prepared.connector_key))
    return plugin.ingress_handler_for(prepared.capability_id)


def _events_and_acknowledgement(
    returned: object,
) -> tuple[tuple[InboundEvent, ...], Acknowledgement | None]:
    """Validate `normalize`'s pair before anything is done with either half.

    Checked as a whole rather than element by element on use, because the two
    halves have very different destinations: the first is iterated into the
    database, the second is written into an HTTP response. A plugin that
    returned a bare tuple of events — the SPI 1.1 shape — would otherwise be
    indexed, and its first `InboundEvent` would become the acknowledgement.
    """
    if not isinstance(returned, tuple) or len(returned) != 2:
        raise ConnectorContract()
    events, acknowledgement = returned
    if not isinstance(events, tuple) or not all(
        isinstance(event, InboundEvent) for event in events
    ):
        raise ConnectorContract()
    if acknowledgement is not None and not isinstance(acknowledgement, Acknowledgement):
        raise ConnectorContract()
    return events, acknowledgement


def verify_and_normalize(
    prepared: PreparedIngress,
    *,
    request: IngressRequest,
    registry: ConnectorRegistry,
    resolve_secrets: SecretResolver,
) -> tuple[tuple[InboundEvent, ...], Acknowledgement | None]:
    """Verify the RAW bytes, then shape them. NO session, NO transaction.

    `db` is deliberately absent from this function's parameters — the boundary
    is enforced by what a caller CANNOT pass, not by a comment asking them not
    to. Secrets are materialized here and only here, bound to a local that goes
    out of scope on return.

    ONE `request` object reaches both hooks, and it is the same object. The
    bytes therefore cross UNTOUCHED and — more importantly — what `verify`
    authenticated is provably what `normalize` interpreted. Every provider worth
    verifying signs the bytes and not a re-serialization of them, so a
    decode-and-re-encode anywhere on this path would invalidate a correct HMAC;
    passing two separately-derived copies would let the two hooks disagree
    without anything noticing.

    Returns the events AND the acknowledgement the connector prepared, so the
    caller can commit the batch and only then write a response it already holds.
    Nothing calls back into the plugin afterwards.

    DIVERGES from `dispatch.invoke`, which never raises: there a typed `Outcome`
    is recorded against a pre-existing `DeliveryAttempt` row. Here there is no
    row, so a refusal has nowhere to be recorded and must propagate.

    :raises SignatureRejected: `verify` returned False. `normalize` is never
        reached, and nothing is written anywhere.
    :raises ConnectorRaised: the plugin threw. Only its type name survives.
    :raises ConnectorContract: `normalize` returned something other than
        `(tuple[InboundEvent, ...], Acknowledgement | None)`.
    """
    handler = _handler(prepared, registry)
    # ONE call, ONE local. `prepared` carries references and never values, and
    # nothing on the return path has a field a value could travel in.
    secrets = dict(resolve_secrets(prepared.secret_refs))

    # The `finally` is the second half of "materialized into a local that goes
    # out of scope". Going out of scope is not enough: ANY exception leaving
    # this function pins this frame in its traceback, and an error reporter
    # configured to capture frame locals uploads whatever is bound here — the
    # signing key in plaintext. A `BaseException` makes it worse rather than
    # different: `asyncio.CancelledError` from a disconnecting client walks past
    # `except Exception` untouched, and must, because swallowing a cancellation
    # is worse than the leak. So the binding is cleared on every exit instead.
    try:
        try:
            verified = handler.verify(request, config=prepared.config, secrets=secrets)
        except Exception as exc:
            # `from None` is load-bearing: the plugin's message is built from
            # provider-controlled bytes, and `__cause__` would carry it into any
            # traceback the edge logs.
            raise ConnectorRaised(type(exc).__name__) from None
        if not verified:
            raise SignatureRejected()

        try:
            returned = handler.normalize(request, config=prepared.config)
        except Exception as exc:
            raise ConnectorRaised(type(exc).__name__) from None
    finally:
        secrets = None  # type: ignore[assignment]

    return _events_and_acknowledgement(returned)


def challenge_response(
    prepared: PreparedIngress,
    *,
    request: IngressRequest,
    registry: ConnectorRegistry,
    resolve_secrets: SecretResolver,
) -> Acknowledgement:
    """Answer the provider's subscription handshake. NO session.

    The WHOLE envelope crosses the boundary — headers as well as query
    parameters, which SPI 1.1 withheld. Selecting a key here would be provider
    knowledge in a module that may hold none: which part of the request carries
    the echo, and which part identifies it as a handshake at all, is the
    connector's business.

    :raises NotAChallenge: the connector returned `None`.
    :raises ConnectorRaised: the connector threw.
    :raises ConnectorContract: the connector returned something that is not an
        `Acknowledgement`.
    """
    handler = _handler(prepared, registry)
    secrets = dict(resolve_secrets(prepared.secret_refs))
    # Cleared on every exit, for the reason `verify_and_normalize` states at
    # length: a raising connector pins this frame, and a frame local is what an
    # error reporter uploads. A handshake is exactly when a freshly configured
    # connector throws.
    try:
        answer = handler.challenge(request, config=prepared.config, secrets=secrets)
    except Exception as exc:
        raise ConnectorRaised(type(exc).__name__) from None
    finally:
        secrets = None  # type: ignore[assignment]
    if answer is None:
        raise NotAChallenge()
    if not isinstance(answer, Acknowledgement):
        raise ConnectorContract()
    return answer


# ── Phase 3: record ─────────────────────────────────────────────────────────


def record_batch(
    db: Any, prepared: PreparedIngress, events: tuple[InboundEvent, ...]
) -> tuple[tuple[UUID, bool], ...]:
    """Record the WHOLE tuple. Mutates and flushes; the caller's unit of work
    decides the transaction.

    Takes the tuple, never one event — there is no loop for a caller to place a
    transaction boundary inside, which is what makes whole-batch atomicity
    structural rather than a convention.

    Hands back `(receipt_id, is_new)` VALUES, never the ORM rows. The same rule
    `PreparedIngress` states for the way in holds on the way out: the caller's
    unit of work has closed by the time it reads this, and with
    `expire_on_commit` on — the default, and what the shipped
    `platform_session` uses — an attribute read off a committed row is a refresh
    against a session that no longer exists. That failure lands on the SUCCESS
    path: the batch is durably written and the caller is told it failed, so the
    provider redelivers into the same failure forever. It is the partial-write
    lie inverted, and unlike a collision it never converges.

    Every refusal here leaves the transaction for the enclosing unit of work to
    unwind, and the caller catches them OUTSIDE that block. Catching inside
    would let the block exit cleanly and persist the events recorded before the
    refusal — events the provider, told 5xx, would then redeliver on top of.

    `EventSubscription` is deliberately NOT consulted. Filtering inbound events
    by subscription would force an unsubscribed event to be either dropped (no
    evidence the provider ever sent it) or recorded anyway; the first makes "the
    whole batch was recorded atomically" untrue. That table's own contract is
    outbound.

    :raises EventIdentityCollision: one event id, two payload digests.
    :raises ReceiptWriteRaced: a concurrent insert won the uniqueness race.
    :raises ReceiptWriteFailed: any other write failure. Typed here rather than
        left to propagate, because the driver's own message carries the bound
        parameters — the normalized payload and the provider event id — into
        whatever the edge logs.
    """
    from sqlalchemy.exc import StatementError

    recorded: list[tuple[UUID, bool]] = []
    for event in events:
        try:
            receipt, is_new = cast(
                tuple[InboxReceipt, bool],
                receive_verified(
                    db,
                    installation_id=prepared.installation_id,
                    capability_binding_id=prepared.binding_id,
                    provider_event_id=event.provider_event_id,
                    event_type=event.event_type,
                    payload=event.payload,
                    # ALWAYS None. `headers_json` ends up in every backup, so
                    # forwarding the request headers would put the signature
                    # header — and any authorization or cookie a misconfigured
                    # proxy passed through — permanently in the database. A
                    # connector that needs a provider request id lifts it into
                    # the payload during `normalize`, which makes header
                    # retention a connector decision expressed as normalized
                    # data rather than a blanket copy.
                    headers=None,
                ),
            )
            # Read INSIDE the transaction that produced it. `receipt.id` is
            # populated by the flush `receive_verified` already performed, so
            # this issues no further SQL — and the tuple that leaves this
            # function needs no session to be read.
            recorded.append((receipt.id, is_new))
        except ProviderEventIdentityCollision:
            raise EventIdentityCollision() from None
        except StatementError as exc:
            # `StatementError`, not `IntegrityError`: the family that carries
            # `[parameters: ...]` is broader than the integrity half. A payload
            # the JSON serializer refuses raises a BARE `StatementError` — not
            # even a `DBAPIError` — and an over-long id raises `DataError`, so
            # catching the integrity subtree would still have let the message
            # out.
            #
            # `receive_verified` is SELECT-then-INSERT with no upsert, so two
            # workers racing one redelivery surface here too; that one keeps its
            # own code because an operator reads it differently.
            if _RECEIPT_UNIQUE in str(getattr(exc, "orig", exc)):
                raise ReceiptWriteRaced() from None
            raise ReceiptWriteFailed() from None
        except ExecutionError:
            # The receiver's own refusals — a blank `provider_event_id` is the
            # reachable one. Its message is a constant, so nothing leaks along
            # it; it is converted because an untyped exception leaves the edge
            # inventing a status for a case the module has already decided.
            raise ReceiptWriteFailed() from None
    return tuple(recorded)


# ── The two façades ─────────────────────────────────────────────────────────


def _accepted(
    prepared: PreparedIngress,
    events: tuple[InboundEvent, ...],
    receipts: tuple[tuple[UUID, bool], ...],
    acknowledgement: Acknowledgement | None,
) -> IngressOutcome:
    """Built entirely from values that already exist. NO PLUGIN CODE RUNS HERE.

    That is the rule this function exists to make structural. The batch has
    COMMITTED by the time this is called, and asking the connector anything now
    would put a plugin exception on the far side of a durable write: the events
    are stored, the provider is told 5xx, and it redelivers forever into a
    handler that keeps raising. So the acknowledgement is one the connector
    built during `normalize`, carried here as a value, and the receipts are
    `(UUID, bool)` pairs rather than ORM rows — nothing on this path can touch a
    session or a plugin, so nothing on this path can fail.
    """
    return IngressOutcome(
        status_code=200,
        code=IngressCode.ACCEPTED,
        installation_id=prepared.installation_id,
        binding_id=prepared.binding_id,
        normalized=len(events),
        recorded=sum(1 for _, is_new in receipts if is_new),
        duplicates=sum(1 for _, is_new in receipts if not is_new),
        receipt_ids=tuple(receipt_id for receipt_id, _ in receipts),
        acknowledgement=(acknowledgement or Acknowledgement()).resolved(
            _DELIVERY_MEDIA_TYPE
        ),
    )


def receive(
    open_unit_of_work: UnitOfWork,
    *,
    endpoint_id: str,
    request: IngressRequest,
    registry: ConnectorRegistry,
    resolve_secrets: SecretResolver,
) -> IngressOutcome:
    """The DELIVERY façade. Two units of work, neither spanning the plugin call.

    Takes no session. The deployment's context manager decides what a unit of
    work is and unwinds it on the way out; this function decides that there are
    two of them and where they end.

    ## Delivery and handshake are separate operations

    This function NEVER answers a handshake. SPI 1.1 inferred one from an empty
    body, and that inference was wrong in both directions. A bodyless POST is
    still a delivery — a provider that signs an empty body and expects the event
    recorded got a handshake attempt and a 400, and its events were dropped
    while it was told the endpoint worked. And a provider that confirms a
    subscription with a BODIED request could not handshake at all.

    The assembly mounts a GET route on `answer_challenge` and a POST route on
    this function, so the operation is decided by the request line — which is
    where a protocol states it — rather than guessed from a byte count.
    """
    try:
        with open_unit_of_work() as db:
            prepared = prepare_ingress(db, endpoint_id=endpoint_id, registry=registry)
    except IngressRefused as exc:
        return refusal_outcome(exc)

    # NO SESSION IS LIVE HERE. `verify_and_normalize` has no `db` parameter, so
    # there is nothing to pass even by mistake. It returns the acknowledgement
    # ALREADY BUILT — the last connector code that runs on this path.
    try:
        events, acknowledgement = verify_and_normalize(
            prepared,
            request=request,
            registry=registry,
            resolve_secrets=resolve_secrets,
        )
    except IngressRefused as exc:
        return refusal_outcome(exc, prepared=prepared)

    try:
        with open_unit_of_work() as db:
            receipts = record_batch(db, prepared, events)
    # OUTSIDE the `with`, and that placement is the whole point: catching inside
    # would let the block exit cleanly and commit the events recorded before the
    # refusal.
    except IngressRefused as exc:
        return refusal_outcome(exc, prepared=prepared)

    # Only now, and from values only. A refusal above returns without the
    # acknowledgement ever being emitted, which is correct: a batch that did not
    # commit must not be acknowledged as if it had.
    return _accepted(prepared, events, receipts, acknowledgement)


def answer_challenge(
    open_unit_of_work: UnitOfWork,
    *,
    endpoint_id: str,
    request: IngressRequest,
    registry: ConnectorRegistry,
    resolve_secrets: SecretResolver,
) -> IngressOutcome:
    """The HANDSHAKE façade. Writes nothing, ever.

    An explicit operation with its own route, never something `receive` falls
    through to. It takes the same `IngressRequest` as delivery, so a connector
    whose provider identifies the handshake by a header — not only by a query
    parameter — can see it.

    Returns the same type as `receive`, so an assembly's two route handlers are
    byte-for-byte identical apart from which one they call. The default media
    type is `text/plain` because providers compare the RAW echoed body, and
    wrapping it in JSON fails the handshake; a connector that needs otherwise
    says so on its own `Acknowledgement`.
    """
    try:
        with open_unit_of_work() as db:
            prepared = prepare_ingress(db, endpoint_id=endpoint_id, registry=registry)
    except IngressRefused as exc:
        return refusal_outcome(exc)

    try:
        acknowledgement = challenge_response(
            prepared,
            request=request,
            registry=registry,
            resolve_secrets=resolve_secrets,
        )
    except IngressRefused as exc:
        return refusal_outcome(exc, prepared=prepared)

    return IngressOutcome(
        status_code=200,
        code=IngressCode.CHALLENGE_ANSWERED,
        installation_id=prepared.installation_id,
        binding_id=prepared.binding_id,
        acknowledgement=acknowledgement.resolved(_HANDSHAKE_MEDIA_TYPE),
    )
