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
from typing import Final, Protocol, runtime_checkable

__all__ = [
    "InboundEvent",
    "IngressHandler",
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


_KEY_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]{1,118}$")
#: `domain.noun.vN` — e.g. `ticket.observation.v1`. A capability id is a
#: CONTRACT name, so the version is part of the identity rather than a
#: separate column: `ticket.observation.v1` and `.v2` are different contracts a
#: connector may implement independently.
_CAPABILITY_RE: Final[re.Pattern[str]] = re.compile(
    r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+\.v[1-9][0-9]*$"
)


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
# 1.1, not 2.0. The change is ADDITIVE: the base protocol lost `handler_for`,
# but a delivery connector implementing all five original members still
# satisfies both `ConnectorPlugin` and `DeliveryPlugin`, so every plugin that
# worked against 1.0 still resolves. What is new is that ingress and poll became
# expressible, and that declaring a mode is now checked.
CURRENT_SPI_VERSION: Final[SpiVersion] = SpiVersion(1, 1)


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


@runtime_checkable
class IngressHandler(Protocol):
    """What an INGRESS plugin returns for one capability.

    Three separable jobs, kept separate because they have different inputs and
    different failure meanings:

    * `challenge` answers the provider's subscription handshake. It is consulted
      for a request carrying NO BYTES — a protocol fact, since a bodyless
      request cannot carry a signed payload, rather than a guess from the HTTP
      verb. Returning `None` there is a REFUSAL, not a fall-through to the
      delivery path: the engine answers 400 and `verify` is never reached.
      Offering every bodied delivery to `challenge` first would let a plugin
      that returns non-`None` by accident silently swallow a batch of real
      events, so the engine does not. A connector whose provider confirms a
      subscription with a BODIED request is therefore not yet serviceable —
      stated here rather than discovered at integration time.
    * `verify` decides authenticity from the RAW bytes. It receives the body
      exactly as received, because every provider worth verifying signs the
      bytes and not a re-serialization of them.
    * `normalize` shapes verified bytes into events. It is never called on an
      unverified body.

    `config` reaches `normalize` because the qualifying source needs it: Sub's
    `normalize_inbound_webhook` selects its shape from the provider variant,
    which is configuration. `secrets` deliberately does NOT — normalization that
    needs a secret is doing verification in the wrong place.
    """

    def challenge(
        self,
        params: Mapping[str, str],
        *,
        config: dict[str, object],
        secrets: dict[str, str],
    ) -> str | None: ...

    def verify(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
        *,
        config: dict[str, object],
        secrets: dict[str, str],
    ) -> bool: ...

    def normalize(
        self, raw_body: bytes, headers: Mapping[str, str], *, config: dict[str, object]
    ) -> tuple[InboundEvent, ...]: ...


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
