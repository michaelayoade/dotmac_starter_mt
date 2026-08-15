"""Where an observation LANDS — decided before the provider is ever spoken to.

The fleet invariant this module makes executable (Knowledge
`provider-metadata-never-selects-destination-scope`, priority 98):

    External-provider metadata is corroboration only; destination scope comes
    from a trusted binding and the originating local intent.

`provider-capability-sources.md` § 2.2 records that the invariant currently
holds **by absence** — nothing routes on provider metadata because nothing
routes anywhere at all. The moment an ingress path exists, absence stops being a
proof. This module is the trusted binding that replaces it.

## The ruling, as three sentences and three owners

============================================ ==============================
`dotmac-integration` owns the durable         this module
transport destination binding
the assembly supplies destination profiles    :func:`install_destination_profiles`
and authenticated clients
the destination application owns its accepted its own port; the Integrator
port and every resulting business decision    never reaches past it
============================================ ==============================

A binding names three things, and needs all three:

``application``      WHICH application. Cross-checked against the capability's
                     DECLARED owner (`dotmac_integration.capability_registry`),
                     so even an operator-written configuration cannot route
                     `messaging.receive.v1` anywhere but the application whose
                     communications module declared it.
``scope``            the destination's OWN local scope — its inbox, its queue,
                     its account. Opaque here: the Integrator carries it and
                     never interprets it, because interpreting it would be the
                     Integrator holding an opinion about product structure.
``contract_version`` which version of the capability contract this binding
                     speaks. Taken from the capability id's own `vN` suffix, so
                     it cannot disagree with the id, and matched against what
                     the assembly's profile says it accepts.

## Why the resolver has no payload parameter

The security property is not "we check the payload carefully". It is
**"the payload is not an input"**. :func:`resolve_destination` takes a database
session and a `capability_binding_id`; there is no parameter through which a
provider-influenced byte could reach it, and
`tests/unit/test_integration_destination_binding.py` fails if one is ever added.

Anyone who can influence a provider payload — a webhook body, a header, a
polled record — must not be able to choose which application a message lands
in. The ordering is what enforces it: the binding is resolved from durable
control-plane state **before** provider I/O, and the provider's own claim can
only ever be compared against an answer that already exists.

## Corroboration is a record, not a route

:func:`corroborate` accepts what the provider claimed and returns a
:class:`Corroboration` — a finding. Its return annotation is checked by a guard,
because a future edit that returned a `DestinationBinding` from a function
taking provider input would silently invert the whole invariant while every
existing test still passed.

A disagreement **fails closed** (`provider-capability-sources.md` § 11.2): with
provider metadata naming application B and the binding naming application A, the
observation is routed to A and the disagreement is recorded — never reconciled
toward the payload, and never delivered "to be safe". A provider that
consistently disagrees is a misconfiguration or an attack, and both want an
operator, not a best effort.

## `scope_json` is display-only, and this module proves it

`CapabilityBinding.scope_json` is the one existing column shaped like routing.
Its docstring says it is displayed and never routed on. This module therefore
never reads it — not defensively, not as a fallback, not as a hint — and two
guards say so:

* an AST scan of this file's source finds no reference to the name at all;
* a behavioural test plants a hostile `scope_json` naming a different
  application and proves the resolved destination is unchanged.

Both carry sensitivity proofs. A comment saying "we don't read scope_json"
survives the edit that starts reading it; a test does not.

## Durability, honestly stated

The binding is durable because it is derived from the
:class:`~dotmac_integration.models.ConnectorConfigRevision` — an **immutable,
digested, operator-established** row that `dispatch.prepare` already pins at
claim time. Nothing on this path can be changed by a provider, and nothing can
change mid-flight.

It is deliberately NOT yet its own `capability_destinations` table. That would
require adding to `models.PLATFORM_TABLES` and a new lineage revision, which
belong to the owner of the persistence surface. The shape is designed so that
promotion is a storage change behind :func:`resolve_destination` and not a
contract change: no caller learns where the row lives.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from dotmac_integration.capability_registry import (
    CapabilityContract,
    CapabilityRegistry,
    require_declared_for_binding,
)
from dotmac_integration.models import (
    CapabilityBinding,
    ConnectorConfigRevision,
    ConnectorInstallation,
)

__all__ = [
    "Corroboration",
    "DestinationBinding",
    "DestinationBindingError",
    "DestinationClient",
    "DestinationDisagreement",
    "DestinationNotBound",
    "DestinationProfile",
    "DestinationProfileMissing",
    "LocalScope",
    "UntrustedDestination",
    "corroborate",
    "destination_client",
    "install_destination_profiles",
    "require_corroborated",
    "require_profile",
    "resolve_destination",
]

#: The key an immutable config revision holds its destination bindings under.
#: One block, keyed by capability id, so a single installation serving several
#: capabilities binds each to its own application and scope — which is exactly
#: the topology `models.CapabilityBinding` already supports and the reason a
#: per-installation destination would have been wrong.
DESTINATIONS_KEY: str = "destinations"


class DestinationBindingError(ValueError):
    """A transport destination could not be established from trusted state."""


class DestinationNotBound(DestinationBindingError):
    """No destination is bound for this capability binding."""


class UntrustedDestination(DestinationBindingError):
    """A configured destination is not the capability's declared owner."""


class DestinationDisagreement(DestinationBindingError):
    """Provider metadata contradicts the trusted binding. Fails closed."""


class DestinationProfileMissing(DestinationBindingError):
    """The assembly supplied no authenticated client for this application."""


@dataclass(frozen=True, slots=True)
class LocalScope:
    """The DESTINATION's own name for the stream — carried, never interpreted.

    `kind` and `ref` are the destination application's vocabulary (`inbox`
    /`support`, `queue`/`fiber`). The Integrator neither validates them against
    a list nor derives behaviour from them: doing either would make the
    transport hold an opinion about the destination's internal structure, which
    is the coupling ADR-0024 removes.
    """

    kind: str
    ref: str

    def __post_init__(self) -> None:
        for name in ("kind", "ref"):
            value = getattr(self, name)
            if not value or value.strip() != value:
                raise DestinationBindingError(
                    f"destination scope {name} {value!r} must be a non-empty, "
                    "whitespace-trimmed key"
                )

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.kind}:{self.ref}"


@dataclass(frozen=True, slots=True)
class DestinationBinding:
    """The trusted answer to "where does this land?".

    Frozen and slotted, and every field a scalar or another frozen value: a
    binding that could be mutated after resolution would reintroduce exactly the
    window this module exists to close — resolve from trusted state, then have
    something later overwrite the answer.

    `config_revision_id` is provenance, not decoration. It says WHICH immutable
    revision established this destination, so an incident can answer "what was
    this routed to on the 3rd?" the same way the rest of the control plane does.
    """

    capability_binding_id: UUID
    capability_id: str
    application: str
    scope: LocalScope
    contract_version: int
    config_revision_id: UUID

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.application}/{self.scope} @{self.capability_id}"


# ── Resolution: durable state in, destination out. No payload anywhere. ─────


def _destination_block(
    revision: ConnectorConfigRevision, capability_id: str
) -> Mapping[str, object]:
    config = revision.config_json or {}
    block = config.get(DESTINATIONS_KEY)
    if not isinstance(block, Mapping):
        raise DestinationNotBound(
            f"config revision {revision.id} declares no {DESTINATIONS_KEY!r} "
            f"block, so capability {capability_id!r} has no trusted destination. "
            "A destination is established by an operator on an immutable "
            "revision BEFORE any provider is contacted — it is never derived "
            "from what arrives"
        )
    entry = block.get(capability_id)
    if not isinstance(entry, Mapping):
        raise DestinationNotBound(
            f"config revision {revision.id} binds no destination for capability "
            f"{capability_id!r}; bound: {sorted(str(k) for k in block)}"
        )
    return entry


def _scope_from(entry: Mapping[str, object], capability_id: str) -> LocalScope:
    raw = entry.get("scope")
    if not isinstance(raw, Mapping):
        raise DestinationNotBound(
            f"destination for {capability_id!r} declares no scope. A binding "
            "names the application AND the local scope; an application alone "
            "does not say where inside it an observation belongs"
        )
    kind = raw.get("kind")
    ref = raw.get("ref")
    if not isinstance(kind, str) or not isinstance(ref, str):
        raise DestinationNotBound(
            f"destination scope for {capability_id!r} must declare string "
            f"`kind` and `ref`; got {raw!r}"
        )
    return LocalScope(kind=kind, ref=ref)


def resolve_destination(
    db: Any,
    *,
    capability_binding_id: UUID,
    registry: CapabilityRegistry,
) -> DestinationBinding:
    """The ONE destination this binding transports to, or a refusal.

    **This signature is the security property.** There is no payload, no header
    map, no provider metadata and no free-text application name among its
    parameters, so no provider-influenced value can reach the decision. A guard
    fails the build if one is ever added.

    Resolution order, and each step's reason:

    1. the binding and its installation, joined — a destination for a binding
       that does not exist is not a routing question;
    2. the installation's CURRENT config revision — immutable and digested, so
       what is read is what an operator wrote;
    3. the destination block for this capability id;
    4. the capability's DECLARED owner from the registry, cross-checked against
       the configured application. This is the step that makes an operator
       typo — or a tampered configuration — a refusal rather than a delivery to
       the wrong application.
    """
    from sqlalchemy import select

    row: tuple[CapabilityBinding, ConnectorInstallation] | None = db.execute(
        select(CapabilityBinding, ConnectorInstallation)
        .join(
            ConnectorInstallation,
            ConnectorInstallation.id == CapabilityBinding.installation_id,
        )
        .where(CapabilityBinding.id == capability_binding_id)
    ).first()
    if row is None:
        raise DestinationNotBound(
            f"capability binding {capability_binding_id} does not exist; there "
            "is nothing to route"
        )
    binding, installation = row

    revision_id = installation.current_config_revision_id
    if revision_id is None:
        raise DestinationNotBound(
            f"installation {installation.connector_key}/{installation.name} has "
            "no current config revision, so no destination has been established"
        )
    revision = db.get(ConnectorConfigRevision, revision_id)
    if revision is None:
        raise DestinationNotBound(
            f"installation {installation.connector_key}/{installation.name} "
            f"points at config revision {revision_id}, which does not exist"
        )

    contract = require_declared_for_binding(
        registry,
        capability_id=binding.capability_id,
        connector_key=installation.connector_key,
    )
    entry = _destination_block(revision, binding.capability_id)
    application = entry.get("application")
    if not isinstance(application, str) or not application:
        raise DestinationNotBound(
            f"destination for {binding.capability_id!r} names no application"
        )
    _require_declared_owner(contract, application)

    return DestinationBinding(
        capability_binding_id=binding.id,
        capability_id=binding.capability_id,
        application=application,
        scope=_scope_from(entry, binding.capability_id),
        contract_version=contract.contract_version,
        config_revision_id=revision.id,
    )


def _require_declared_owner(contract: CapabilityContract, application: str) -> None:
    """A destination may only be the capability's DECLARED owner.

    Without this the trusted binding is only as trustworthy as whoever last
    edited a config revision. With it, the two independent authorities — the
    owning application's declaration and the operator's configuration — have to
    agree, and a single compromised or mistaken one cannot redirect a stream.
    """
    if application != contract.owner.application:
        raise UntrustedDestination(
            f"capability {contract.capability_id!r} is declared by "
            f"{contract.owner}, but its destination is configured as "
            f"{application!r}. A capability is transported to the application "
            "that DECLARED it; a configuration cannot reassign a contract's "
            "owner"
        )


# ── Corroboration: compare, record, refuse. Never select. ──────────────────


@dataclass(frozen=True, slots=True)
class Corroboration:
    """A finding about what the provider claimed. NOT a destination.

    Returned by :func:`corroborate` and nothing else. The type exists so the
    "provider metadata is corroboration only" rule has a shape a guard can
    check: a function that takes provider input may return one of these, and may
    not return a :class:`DestinationBinding`.
    """

    agrees: bool
    binding_application: str
    claimed_application: str | None
    detail: str


def corroborate(
    binding: DestinationBinding, *, claimed_application: str | None
) -> Corroboration:
    """Compare the provider's claim against the already-resolved binding.

    Takes the binding FIRST and by position, because there is no calling order
    in which a claim could be evaluated before a destination exists. A claim
    that agrees is corroboration; a claim that disagrees is evidence; neither is
    a route.

    `None` — the provider said nothing about where this belongs — is the common
    case and is not a disagreement. Most providers have no concept of the
    destination application, and treating silence as conflict would fail every
    honest delivery.
    """
    if claimed_application is None:
        return Corroboration(
            agrees=True,
            binding_application=binding.application,
            claimed_application=None,
            detail="provider asserted no destination; binding stands unopposed",
        )
    agrees = claimed_application == binding.application
    return Corroboration(
        agrees=agrees,
        binding_application=binding.application,
        claimed_application=claimed_application,
        detail=(
            "provider claim matches the trusted binding"
            if agrees
            else (
                f"provider claims {claimed_application!r}; the trusted binding "
                f"names {binding.application!r}"
            )
        ),
    )


def require_corroborated(
    binding: DestinationBinding, *, claimed_application: str | None
) -> Corroboration:
    """Corroborate and FAIL CLOSED on disagreement.

    Returns the finding when the claim agrees or is silent; raises otherwise.
    The observation is never rerouted toward the payload and never delivered
    anyway: a provider naming a different application than the trusted binding
    is a misconfiguration or an attack, and both want an operator rather than a
    best effort.
    """
    finding = corroborate(binding, claimed_application=claimed_application)
    if not finding.agrees:
        raise DestinationDisagreement(
            f"{finding.detail}. Routing is not reconciled toward provider "
            "metadata: the observation stays bound to "
            f"{binding.application!r} and this delivery is refused so the "
            "disagreement is investigated"
        )
    return finding


# ── The assembly-supplied seam: profiles and authenticated clients ─────────


@runtime_checkable
class DestinationClient(Protocol):
    """An authenticated client for ONE destination application.

    Supplied by the assembly, exactly like a `SecretSource`: this module holds
    no HTTP client, no base URL, no credential and no retry of its own. It knows
    that a destination is reachable; it does not know how.

    `deliver` takes the resolved binding rather than an application string, so a
    caller physically cannot ask a client to deliver somewhere the binding did
    not name.
    """

    def deliver(
        self, *, binding: DestinationBinding, envelope: Mapping[str, object]
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class DestinationProfile:
    """What the assembly knows about reaching one application.

    `contract_versions` is what the deployed destination actually accepts. It is
    checked at lookup rather than at install because a binding's version comes
    from the capability id, and a destination that has not yet deployed `v2`
    must refuse a `v2` binding loudly instead of receiving a shape it will
    mis-parse.
    """

    application: str
    contract_versions: frozenset[int]
    client: DestinationClient

    def __post_init__(self) -> None:
        if not self.application:
            raise DestinationBindingError("destination profile names no application")
        if not self.contract_versions:
            raise DestinationBindingError(
                f"destination profile for {self.application!r} accepts no "
                "contract version; an application that accepts nothing is not a "
                "destination"
            )


_PROFILES: dict[str, DestinationProfile] = {}


def install_destination_profiles(profiles: Iterable[DestinationProfile]) -> None:
    """Install the assembly's destination profiles. Once, at startup.

    Replaces the whole set rather than merging, so the installed profiles are
    always exactly what one composition statement said — a merge would let a
    stale profile from an earlier call survive a reconfiguration that meant to
    remove it.
    """
    replacement: dict[str, DestinationProfile] = {}
    for profile in profiles:
        if profile.application in replacement:
            raise DestinationBindingError(
                f"two destination profiles supplied for {profile.application!r}; "
                "one application has one authenticated client, or a delivery's "
                "target depends on iteration order"
            )
        replacement[profile.application] = profile
    _PROFILES.clear()
    _PROFILES.update(replacement)


def require_profile(binding: DestinationBinding) -> DestinationProfile:
    """The profile for an ALREADY-RESOLVED binding.

    Takes a `DestinationBinding`, never a string. That is deliberate and is the
    second half of the invariant: even if a provider payload somehow produced an
    application NAME, there is no function here that will turn a name into a
    client. A profile is reachable only from a binding this module resolved.
    """
    profile = _PROFILES.get(binding.application)
    if profile is None:
        raise DestinationProfileMissing(
            f"no destination profile installed for {binding.application!r}; the "
            "composing assembly supplies profiles and authenticated clients — "
            f"installed: {sorted(_PROFILES)}"
        )
    if binding.contract_version not in profile.contract_versions:
        raise DestinationProfileMissing(
            f"{binding.application!r} accepts contract versions "
            f"{sorted(profile.contract_versions)}, but binding "
            f"{binding.capability_id!r} speaks v{binding.contract_version}. The "
            "destination has not deployed this contract; delivering it would "
            "hand over a shape it will mis-parse"
        )
    return profile


def destination_client(binding: DestinationBinding) -> DestinationClient:
    """The authenticated client for a resolved binding. Fails closed."""
    return require_profile(binding).client


def _reset_destination_profiles() -> None:
    """Test seam. Clears the installed profiles."""
    _PROFILES.clear()
