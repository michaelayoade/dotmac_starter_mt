"""The connector SPI — what a plugin declares, and what the module checks.

A connector distribution is independently released. The module therefore cannot
import it, cannot enumerate it, and must not contain a provider enum, an import
list or an ``if provider == ...`` branch (ADR-0024 § 7). What it *can* do is
state a contract and refuse anything that does not meet it.

## Three declarations, three refusals

A :class:`ConnectorManifest` declares a stable ``connector_key``, the SPI range
it was built against, and the capabilities it implements. Each maps to exactly
one refusal:

===================== ==========================================================
declaration           refused when
===================== ==========================================================
``connector_key``     two installed distributions claim the same key
``spi_range``         the running module's SPI is outside the declared range
``capabilities``      a binding names a capability the manifest never declared
===================== ==========================================================

All three are checked at **discovery**, again at **startup**, and again at
**activation**. That looks redundant and is not: a distribution can be installed
after discovery ran, and a binding can be activated long after startup, so a
check that happens only once is a check with a window.

## Why the SPI version is stored, not just compared

Sub's platform records ``connector_version`` and ``manifest_digest`` but nothing
about the SPI it was built against — so "an incompatible SPI version refuses
activation" was unenforceable there. Storing the declared range on the
installation is what lets a *later* module upgrade refuse a *previously*
activated binding, which is the case that actually bites: the plugin did not
change, the host did.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Final, Protocol, runtime_checkable

__all__ = [
    "Acknowledgement",
    "InboundEvent",
    "IngressHandler",
    "IngressRequest",
    "InvalidAcknowledgementError",
    "PollHandler",
    "DeliveryPlugin",
    "IngressPlugin",
    "PollPlugin",
    "MODE_PROTOCOLS",
    "ModeContract",
    "ModeNotDeclaredError",
    "require_mode",
    "CURRENT_SPI_VERSION",
    "CapabilityHandler",
    "ConnectorMode",
    "ConnectorPlugin",
    "Diagnostic",
    "DispatchRequest",
    "accepts_manifest_digest",
    "CapabilityDeclaration",
    "ConnectorManifest",
    "SpiRange",
    "SpiVersion",
]


class SpiIncompatibleError(ValueError):
    """A connector's declared SPI range excludes the running module."""


class InvalidManifestError(ValueError):
    """A connector manifest is malformed — refused before it is ever trusted."""


class InvalidAcknowledgementError(ValueError):
    """A connector built an acknowledgement the engine will not emit.

    Raised by the CONSTRUCTOR, so a malformed acknowledgement cannot exist long
    enough to reach a response writer. The engine converts it, like any other
    exception a plugin raises, into a typed refusal that carries only the type
    name.
    """


_KEY_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]{1,118}$")
#: `domain.noun.vN` — e.g. `ticket.observation.v1`. A capability id is a
#: CONTRACT name, so the version is part of the identity rather than a
#: separate column: `ticket.observation.v1` and `.v2` are different contracts a
#: connector may implement independently.
_CAPABILITY_RE: Final[re.Pattern[str]] = re.compile(
    r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+\.v[1-9][0-9]*$"
)
#: `type/subtype`, optionally with a charset. Anchored and character-restricted
#: because this string is written into a RESPONSE HEADER: an unvalidated one
#: carrying CRLF is header injection, and one carrying arbitrary parameters is a
#: connector shaping the response beyond the one knob it was granted.
_MEDIA_TYPE_RE: Final[re.Pattern[str]] = re.compile(
    r"[a-z0-9][a-z0-9!#$&^_.+-]{0,126}/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}"
    r"(; ?charset=[a-zA-Z0-9._-]{1,64})?"
)
#: The empty mapping every envelope defaults to. A shared frozen proxy rather
#: than a `default_factory`: it can be neither mutated nor accumulated into.
_NO_MATERIAL: Final[Mapping[str, str]] = MappingProxyType({})


@dataclass(frozen=True, slots=True, order=True)
class SpiVersion:
    """A ``major.minor`` SPI version.

    Major is the break; minor is additive. There is deliberately no patch
    component — a patch that changes nothing a plugin can observe does not
    belong in a compatibility decision.
    """

    major: int
    minor: int

    def __post_init__(self) -> None:
        if self.major < 1 or self.minor < 0:
            raise InvalidManifestError(f"invalid SPI version {self}")

    @classmethod
    def parse(cls, text: str) -> SpiVersion:
        match = re.fullmatch(r"(\d+)\.(\d+)", text.strip())
        if not match:
            raise InvalidManifestError(
                f"SPI version {text!r} must be `major.minor`, e.g. '1.0'"
            )
        return cls(int(match.group(1)), int(match.group(2)))

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.major}.{self.minor}"


#: The SPI this module implements. A connector declaring a range that excludes
#: it is refused — at discovery, at startup and at activation.
# 1.2. The DELIVERY and POLL contracts are untouched, so 1.0's delivery
# connectors still resolve; what changed is INGRESS, whose three hooks now take
# one immutable `IngressRequest` and hand back a typed `Acknowledgement`.
#
# A signature change would normally deserve a major bump. It is honest as a
# minor here because SPI 1.1 shipped in `dotmac-integration` 0.1.0a3/a4, NEITHER
# of which was ever published: the releases on the index are 0.1.0a1 and
# 0.1.0a2, both SPI 1.0 with no ingress contract at all. There is therefore no
# plugin anywhere built against 1.1's ingress signatures, and a 2.0 would have
# excluded every honest `>=1.0,<2.0` delivery connector to protect a
# compatibility promise nothing ever consumed.
CURRENT_SPI_VERSION: Final[SpiVersion] = SpiVersion(1, 2)


@dataclass(frozen=True, slots=True)
class SpiRange:
    """``>=minimum, <exclusive_maximum``, both required.

    An open-ended range is rejected rather than defaulted. "Works with any
    future SPI" is a claim no connector author can honestly make, and accepting
    it would move the failure from activation — where it is a clear refusal —
    to the first dispatch, where it is a traceback.
    """

    minimum: SpiVersion
    below: SpiVersion

    def __post_init__(self) -> None:
        if not self.minimum < self.below:
            raise InvalidManifestError(
                f"SPI range >={self.minimum},<{self.below} admits no version"
            )

    @classmethod
    def parse(cls, text: str) -> SpiRange:
        match = re.fullmatch(r"\s*>=\s*([\d.]+)\s*,\s*<\s*([\d.]+)\s*", text)
        if not match:
            raise InvalidManifestError(
                f"SPI range {text!r} must be '>=<min>,<<max>', e.g. '>=1.0,<2.0'"
            )
        return cls(SpiVersion.parse(match.group(1)), SpiVersion.parse(match.group(2)))

    def admits(self, version: SpiVersion = CURRENT_SPI_VERSION) -> bool:
        return self.minimum <= version < self.below

    def require(self, version: SpiVersion = CURRENT_SPI_VERSION) -> None:
        if not self.admits(version):
            raise SpiIncompatibleError(
                f"connector declares SPI >={self.minimum},<{self.below} but the "
                f"running module implements {version}"
            )

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f">={self.minimum},<{self.below}"


@dataclass(frozen=True, slots=True)
class CapabilityDeclaration:
    """One capability contract a connector implements.

    `config_schema` is a JSON-schema fragment describing the capability's
    configuration. It may name secret REFERENCES; it may never carry a secret
    value — see `dotmac_integration.secret_refs`.
    """

    capability_id: str
    config_schema: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _CAPABILITY_RE.fullmatch(self.capability_id):
            raise InvalidManifestError(
                f"capability id {self.capability_id!r} must look like "
                "`domain.noun.vN`, e.g. 'ticket.observation.v1'"
            )


@dataclass(frozen=True, slots=True)
class ConnectorManifest:
    """What one connector distribution publishes about itself."""

    connector_key: str
    version: str
    spi_range: SpiRange
    capabilities: tuple[CapabilityDeclaration, ...]

    def __post_init__(self) -> None:
        if not _KEY_RE.fullmatch(self.connector_key):
            raise InvalidManifestError(
                f"connector key {self.connector_key!r} must be lowercase "
                "alphanumeric with underscores"
            )
        if not self.version.strip():
            raise InvalidManifestError(
                f"connector {self.connector_key!r} declares no version"
            )
        if not self.capabilities:
            raise InvalidManifestError(
                f"connector {self.connector_key!r} declares no capabilities — a "
                "connector that implements nothing cannot be bound to anything"
            )
        seen: set[str] = set()
        for capability in self.capabilities:
            if capability.capability_id in seen:
                raise InvalidManifestError(
                    f"connector {self.connector_key!r} declares capability "
                    f"{capability.capability_id!r} twice"
                )
            seen.add(capability.capability_id)

    @property
    def digest(self) -> str:
        """Stable identity of this manifest's CONTRACT.

        Over the fields a consumer can depend on — key, version, SPI range and
        the capability set. Deliberately not over the whole object: a docstring
        change must not invalidate every installation pinned to it.
        """
        import hashlib

        material = "|".join(
            (
                self.connector_key,
                self.version,
                str(self.spi_range),
                ",".join(sorted(self.capability_ids)),
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @property
    def capability_ids(self) -> frozenset[str]:
        return frozenset(c.capability_id for c in self.capabilities)

    def require_declares(self, capability_id: str) -> CapabilityDeclaration:
        """The undeclared-capability refusal.

        A binding may only name a capability the manifest actually declares.
        Without this a typo binds successfully and fails at the first dispatch,
        by which time the operator has been told the integration is live.
        """
        for capability in self.capabilities:
            if capability.capability_id == capability_id:
                return capability
        raise InvalidManifestError(
            f"connector {self.connector_key!r} does not declare capability "
            f"{capability_id!r}; it declares {sorted(self.capability_ids)}"
        )


# ── The executable contract ─────────────────────────────────────────────────


class ConnectorMode(str, Enum):
    """How a connector moves data. Declared, so the runtime knows which workers
    to start rather than discovering it by calling and failing."""

    INGRESS = "ingress"
    POLL = "poll"
    DELIVERY = "delivery"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """One finding from validation. `ok=False` blocks enablement."""

    ok: bool
    code: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class DispatchRequest:
    """What a handler is given. Deliberately NOT a database session.

    A handler receives resolved configuration and MATERIALIZED secrets, never a
    connection — the invoke phase runs outside the database transaction, and
    handing over a session would invite a plugin to hold one across provider
    I/O.
    """

    capability_id: str
    event_type: str
    payload: dict[str, object]
    config: dict[str, object]
    #: Materialized at the boundary, never persisted. See `dispatch.invoke`.
    secrets: dict[str, object]
    idempotency_key: str


@runtime_checkable
class CapabilityHandler(Protocol):
    """What a plugin returns for one capability.

    Returns an `Outcome`-shaped result. It classifies; it does not decide what
    happens next — retry, dead-letter and reconciliation belong to the engine,
    which is why a handler cannot reschedule itself.
    """

    def __call__(self, request: DispatchRequest) -> object: ...


@dataclass(frozen=True, slots=True)
class InboundEvent:
    """One normalized provider fact, ready for `receive_verified`.

    Exactly the triple that function records, and nothing else — an ingress
    handler classifies and shapes; it does not decide what happens next.
    """

    provider_event_id: str
    event_type: str
    payload: dict[str, object]


@dataclass(frozen=True, slots=True, repr=False)
class IngressRequest:
    """ONE immutable envelope for everything that arrived, handed to all three
    ingress hooks unchanged.

    ## Why one object, and why the SAME one

    SPI 1.1 gave each hook its own slice — `challenge` saw query parameters,
    `verify` saw bytes and headers, `normalize` saw bytes and headers. Every
    real provider then wanted a piece it had not been given: Meta's handshake is
    identified by headers as well as `hub.*` parameters, and a connector whose
    `verify` needs a query-string signature could not reach one. Widening one
    hook at a time is how a contract acquires four overloads, so the envelope
    widens once and all three hooks take it.

    Handing the SAME object to `verify` and `normalize` is the load-bearing
    part: what was authenticated and what was interpreted are then provably the
    same bytes. Two parameters can drift — an engine that re-read, re-decoded or
    re-assembled the body between the two calls would authenticate one thing and
    normalize another, and a signature check that guards a different byte string
    guards nothing.

    ## What makes it safe to pass around

    `frozen` and `slots` together: no hook can mutate what a later hook sees,
    and none can smuggle a database session on as an ad-hoc attribute. The
    mappings are copied into `MappingProxyType`, so immutability is real rather
    than promised by the type annotation — a plugin holding `request.headers`
    cannot edit the engine's view.

    `repr=False` is the disclosure rule, and it is the same one
    `PreparedIngress` states: this object is a frame local in every traceback
    that leaves the plugin phase, and it holds the raw body, the signature
    header, and any authorization header or cookie a misconfigured proxy passed
    through. A generated `repr` would render all of it into any log line, error
    report or debugger frame that touched the frame. The values are still THERE
    — this is a rendering rule, not a removal.
    """

    raw_body: bytes = b""
    headers: Mapping[str, str] = _NO_MATERIAL
    params: Mapping[str, str] = _NO_MATERIAL

    def __post_init__(self) -> None:
        if not isinstance(self.raw_body, bytes):
            raise InvalidAcknowledgementError(
                "an ingress request body must be the RAW bytes as received; "
                f"got {type(self.raw_body).__name__}"
            )
        # Copy, then freeze. Copying stops a caller mutating the engine's view
        # after construction; the proxy stops a plugin mutating it at all.
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))


@dataclass(frozen=True, slots=True, repr=False)
class Acknowledgement:
    """What a connector wants written back, and NOTHING about how.

    A provider's handshake is a raw echo, another's delivery reply must be
    `{"status":"ok"}`, and a third rejects anything but an empty 200 body. That
    is provider knowledge, which this module may not hold (ADR-0024 § 7) — so
    the BODY is the connector's, and it is `bytes` rather than `str` for the
    same reason `verify` takes bytes: a provider comparing an exact response
    body is comparing bytes, and an engine-chosen encoding would be a guess.

    ## The line this type draws

    `media_type` is optional and is the ONLY response shaping a connector gets.
    There is deliberately no status code and no header mapping:

    * a **status code** is a retry instruction. 200 means "never send this
      again", 5xx means "send it again", 4xx means "stop and page someone". A
      connector choosing it could discard events the engine believes are safely
      persisted, and that decision belongs to the engine, which is the only
      party that knows whether the batch committed.
    * **arbitrary headers** are a response-splitting surface and a place for
      request material to be echoed back out of the module's sight. Neither is
      needed by any handshake the requirement record describes, so the
      capability is not granted yet rather than granted and policed.

    `media_type` is validated against a strict `type/subtype` shape precisely
    because it IS a header value: an unvalidated one carrying CRLF is header
    injection with extra steps.

    `repr=False`, like `IngressRequest`: a connector is free to echo a slice of
    the request into its acknowledgement — that is what an echo handshake IS —
    so this object must be assumed to hold request material.
    """

    body: bytes = b""
    #: `None` means "the engine picks", and the engine's default depends on the
    #: operation: a handshake is `text/plain` because providers compare the raw
    #: echoed body, a delivery acknowledgement is `application/json`.
    media_type: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.body, bytes):
            raise InvalidAcknowledgementError(
                "an acknowledgement body must be bytes — the engine writes it "
                f"back verbatim and will not guess an encoding; got "
                f"{type(self.body).__name__}"
            )
        if self.media_type is None:
            return
        if not _MEDIA_TYPE_RE.fullmatch(self.media_type):
            raise InvalidAcknowledgementError(
                f"media type {self.media_type!r} is not a bare `type/subtype` "
                "with an optional charset — a response header value is not a "
                "free-text field"
            )

    def resolved(self, default_media_type: str) -> Acknowledgement:
        """This acknowledgement with the engine's default filled in.

        The engine calls it once, on the way out, so an assembly never has to
        decide what an unset media type means.
        """
        if self.media_type is not None:
            return self
        return Acknowledgement(body=self.body, media_type=default_media_type)


@runtime_checkable
class IngressHandler(Protocol):
    """What an INGRESS plugin returns for one capability.

    Three separable jobs, kept separate because they have different inputs and
    different failure meanings. All three receive the same immutable
    :class:`IngressRequest`:

    * `challenge` answers the provider's subscription handshake. It is an
      EXPLICIT operation — the engine reaches it only through
      `ingress.answer_challenge`, which an assembly wires to its own handshake
      route. SPI 1.1 inferred a handshake from an empty body instead, and that
      inference was wrong in both directions: a bodyless POST is still a
      DELIVERY (a provider that signs an empty body and expects it recorded got
      a handshake attempt), and a provider that confirms a subscription with a
      BODIED request could not handshake at all. Returning `None` is a REFUSAL:
      the engine answers 400 and `verify` is never reached.
    * `verify` decides authenticity from `request.raw_body`. It receives the
      body exactly as received, because every provider worth verifying signs the
      bytes and not a re-serialization of them.
    * `normalize` shapes a verified request into events AND the acknowledgement
      the provider should receive. It is never called on an unverified body.

    ## Why `normalize` builds the acknowledgement

    Because it is the last connector code that runs. The engine records the
    batch after `normalize` returns and emits the acknowledgement only once that
    batch has committed — so the acknowledgement must already exist before
    persistence begins. Calling back into the plugin after the commit would put
    provider I/O and plugin exceptions on the far side of a durable write, where
    a raise would answer 5xx for a batch that is safely stored and the provider
    would redeliver it forever.

    `config` reaches `normalize` because the qualifying source needs it: Sub's
    `normalize_inbound_webhook` selects its shape from the provider variant,
    which is configuration. `secrets` deliberately does NOT — normalization that
    needs a secret is doing verification in the wrong place.
    """

    def challenge(
        self,
        request: IngressRequest,
        *,
        config: dict[str, object],
        secrets: dict[str, str],
    ) -> Acknowledgement | None: ...

    def verify(
        self,
        request: IngressRequest,
        *,
        config: dict[str, object],
        secrets: dict[str, str],
    ) -> bool: ...

    def normalize(
        self, request: IngressRequest, *, config: dict[str, object]
    ) -> tuple[tuple[InboundEvent, ...], Acknowledgement | None]: ...


@runtime_checkable
class PollHandler(Protocol):
    """What a POLL plugin returns for one capability.

    Takes the cursor the module persisted and returns the events found plus the
    cursor to persist next. The handler never writes the cursor itself — the
    module owns the checkpoint, so a handler cannot advance past events it
    failed to return.
    """

    def poll(
        self,
        cursor: str | None,
        *,
        config: dict[str, object],
        secrets: dict[str, str],
    ) -> tuple[tuple[InboundEvent, ...], str | None]: ...


@runtime_checkable
class ConnectorPlugin(Protocol):
    """The BASE contract: identity, metadata, and connection validation.

    A plugin object rather than a bare manifest, because the module has to DO
    something with a connector: start the right workers, validate a connection
    before enabling it, and call a handler. Metadata alone cannot run anything.

    `historical_manifests` is the adoption window, and it lives INSIDE the one
    distribution on purpose. Shipping an old manifest as a second distribution
    would claim the same `connector_key` twice, which discovery refuses — so
    the pins a still-installed older revision was adopted against travel with
    the connector that supersedes them.

    ## Why `handler_for` is NOT here

    It was, and `modes` was decorative as a result. `handler_for` moves DELIVERY
    data; an ingress-only connector has no meaningful implementation of it, and
    a base protocol demanding one forces every connector to either lie or raise.
    Worse, dispatch called it without asking whether the plugin declared
    `DELIVERY` at all — so pointing a binding at an ingress connector produced a
    confusing handler error instead of a refusal.

    Each mode now has its own executable protocol, and `conformance` asserts the
    implication in BOTH directions: declaring a mode requires satisfying its
    protocol, and satisfying one requires declaring the mode. A declaration that
    nothing verifies is how `modes` became decorative in the first place.
    """

    @property
    def manifest(self) -> ConnectorManifest: ...

    @property
    def historical_manifests(self) -> tuple[ConnectorManifest, ...]: ...

    @property
    def modes(self) -> frozenset[ConnectorMode]: ...

    def validate_connection(
        self, *, config: dict[str, object], secrets: dict[str, object]
    ) -> tuple[Diagnostic, ...]: ...


@runtime_checkable
class DeliveryPlugin(ConnectorPlugin, Protocol):
    """MODE: DELIVERY. Sends to the provider."""

    def handler_for(self, capability_id: str) -> CapabilityHandler: ...


@runtime_checkable
class IngressPlugin(ConnectorPlugin, Protocol):
    """MODE: INGRESS. Receives provider-initiated traffic."""

    def ingress_handler_for(self, capability_id: str) -> IngressHandler: ...


@runtime_checkable
class PollPlugin(ConnectorPlugin, Protocol):
    """MODE: POLL. Asks the provider on a schedule."""

    def poll_handler_for(self, capability_id: str) -> PollHandler: ...


@dataclass(frozen=True, slots=True)
class ModeContract:
    """What a declared mode obliges a plugin to provide."""

    protocol: type
    #: The factory a conforming plugin must expose for this mode. Named here so
    #: `conformance` has ONE place to look rather than a branch per mode — a
    #: branch is how a new mode gets added with no checks behind it.
    factory: str


#: One entry per mode, so a new mode cannot be added without deciding what makes
#: it runnable — precisely the omission that left `POLL` a label with no
#: machinery behind it.
MODE_PROTOCOLS: Final[dict[ConnectorMode, ModeContract]] = {
    ConnectorMode.DELIVERY: ModeContract(DeliveryPlugin, "handler_for"),
    ConnectorMode.INGRESS: ModeContract(IngressPlugin, "ingress_handler_for"),
    ConnectorMode.POLL: ModeContract(PollPlugin, "poll_handler_for"),
}


class ModeNotDeclaredError(RuntimeError):
    """A plugin was asked to do something it does not declare.

    Raised BEFORE the plugin is called, so the operator sees "this connector
    does not deliver" rather than an AttributeError from inside a handler
    lookup.
    """


def require_mode(plugin: ConnectorPlugin, mode: ConnectorMode) -> None:
    """Refuse a plugin that does not declare AND implement `mode`.

    Both halves matter. A plugin that declares `DELIVERY` without
    `handler_for` fails at call time; one that implements `handler_for` without
    declaring `DELIVERY` never gets its workers started. Neither is usable, and
    neither was detected before.
    """
    contract = MODE_PROTOCOLS[mode]
    key = plugin.manifest.connector_key
    if mode not in plugin.modes:
        raise ModeNotDeclaredError(
            f"connector {key!r} does not declare mode {mode.value!r}; it "
            f"declares {sorted(m.value for m in plugin.modes)}"
        )
    if not isinstance(plugin, contract.protocol):
        raise ModeNotDeclaredError(
            f"connector {key!r} declares mode {mode.value!r} but does not "
            f"implement {contract.protocol.__name__}"
        )


def accepts_manifest_digest(plugin: ConnectorPlugin, digest: str) -> bool:
    """Is an installation pinned to `digest` still adoptable by this plugin?

    Current manifest first, then the historical window. An installation whose
    digest matches neither has been superseded by a connector that no longer
    claims to honour it — which must block adoption rather than proceed and
    hope the shapes still line up.
    """
    if plugin.manifest.digest == digest:
        return True
    return any(m.digest == digest for m in plugin.historical_manifests)
