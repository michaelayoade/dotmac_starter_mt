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
from dataclasses import dataclass, field
from enum import Enum
from typing import Final, Protocol, runtime_checkable

__all__ = [
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
CURRENT_SPI_VERSION: Final[SpiVersion] = SpiVersion(1, 0)


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
    config_schema: dict = field(default_factory=dict)

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
    payload: dict
    config: dict
    #: Materialized at the boundary, never persisted. See `dispatch.invoke`.
    secrets: dict
    idempotency_key: str


@runtime_checkable
class CapabilityHandler(Protocol):
    """What a plugin returns for one capability.

    Returns an `Outcome`-shaped result. It classifies; it does not decide what
    happens next — retry, dead-letter and reconciliation belong to the engine,
    which is why a handler cannot reschedule itself.
    """

    def __call__(self, request: DispatchRequest) -> object: ...


@runtime_checkable
class ConnectorPlugin(Protocol):
    """One entry point per connector key resolves to one of these.

    A plugin object rather than a bare manifest, because the module has to DO
    something with a connector: start the right workers, validate a connection
    before enabling it, and call a handler. Metadata alone cannot run anything.

    `historical_manifests` is the adoption window, and it lives INSIDE the one
    distribution on purpose. Shipping an old manifest as a second distribution
    would claim the same `connector_key` twice, which discovery refuses — so
    the pins a still-installed older revision was adopted against travel with
    the connector that supersedes them.
    """

    @property
    def manifest(self) -> ConnectorManifest: ...

    @property
    def historical_manifests(self) -> tuple[ConnectorManifest, ...]: ...

    @property
    def modes(self) -> frozenset[ConnectorMode]: ...

    def handler_for(self, capability_id: str) -> CapabilityHandler: ...

    def validate_connection(
        self, *, config: dict, secrets: dict
    ) -> tuple[Diagnostic, ...]: ...


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
