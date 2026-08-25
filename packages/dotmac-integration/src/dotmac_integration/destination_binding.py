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

## Durability

`capability_destination_revisions` is append-only control-plane state. The
product descriptor reconciler adds a revision only when the authenticated
descriptor digest changes; earlier receipts therefore retain the provenance
they were prepared against instead of observing a mutable map. Provider input
is absent from both establishment and reconciliation.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from uuid import UUID, uuid4

from dotmac_integration.capability_registry import (
    CapabilityContract,
    CapabilityRegistry,
    require_declared_for_binding,
)
from dotmac_integration.models import (
    CapabilityBinding,
    CapabilityDestinationRevision,
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
    "ProductPortDescriptorInvalid",
    "ProductPortDescriptorSnapshot",
    "UntrustedDestination",
    "corroborate",
    "destination_client",
    "install_destination_profiles",
    "product_port_descriptor_digest",
    "reconcile_product_port_descriptor",
    "require_corroborated",
    "require_profile",
    "establish_destination",
    "resolve_destination",
]

#: Destinations used to live under this key inside an immutable config
#: revision's `config_json`. They now have their own append-only table
#: (`ig_0004_destinations`). The name is kept ONLY so a deployment carrying a
#: stale block gets told what happened rather than silently routing nowhere:
#: `resolve_destination` refuses, naming this constant, if it finds one.
LEGACY_DESTINATIONS_KEY: str = "destinations"


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


class ProductPortDescriptorInvalid(DestinationBindingError):
    """A product declaration is malformed, dishonest, or for another route."""


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
class ProductPortDescriptorSnapshot:
    """Authenticated product-owned port facts, stored as one immutable unit."""

    schema_version: str
    application: str
    owner_module: str
    capability_id: str
    capability_summary: str
    contract_version: int
    destination_binding_id: UUID
    delivery_path: str
    mirror_path: str
    destination_scope: LocalScope
    activation_state: str
    source_revision: str
    descriptor_digest: str


@dataclass(frozen=True, slots=True)
class DestinationBinding:
    """The trusted answer to "where does this land?".

    Frozen and slotted, and every field a scalar or another frozen value: a
    binding that could be mutated after resolution would reintroduce exactly the
    window this module exists to close — resolve from trusted state, then have
    something later overwrite the answer.

    `destination_revision_id` is provenance, not decoration. It says WHICH
    immutable row established this destination, so an incident can answer "what
    was this routed to on the 3rd?" by reading one row rather than diffing JSON
    across config revisions.
    """

    capability_binding_id: UUID
    capability_id: str
    application: str
    scope: LocalScope
    contract_version: int
    destination_revision_id: UUID
    product_port: ProductPortDescriptorSnapshot | None = None

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.application}/{self.scope} @{self.capability_id}"


_DESCRIPTOR_SCHEMA = "dotmac.io/product-port-descriptor/v1"
_DESCRIPTOR_STATES = frozenset(
    {"configured_disabled", "enabled", "quarantined", "retired"}
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _descriptor_document(
    descriptor: ProductPortDescriptorSnapshot,
) -> dict[str, object]:
    return {
        "schema_version": descriptor.schema_version,
        "application": descriptor.application,
        "owner_module": descriptor.owner_module,
        "capability_id": descriptor.capability_id,
        "capability_summary": descriptor.capability_summary,
        "contract_version": descriptor.contract_version,
        "destination_binding_id": descriptor.destination_binding_id,
        "delivery_path": descriptor.delivery_path,
        "mirror_path": descriptor.mirror_path,
        "destination_scope": {
            "kind": descriptor.destination_scope.kind,
            "ref": descriptor.destination_scope.ref,
        },
        "activation_state": descriptor.activation_state,
        "source_revision": descriptor.source_revision,
    }


def product_port_descriptor_digest(
    descriptor: ProductPortDescriptorSnapshot,
) -> str:
    """The product and reconciler's canonical descriptor digest."""

    material = json.dumps(
        _descriptor_document(descriptor),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _require_relative_product_path(path: str, *, field: str) -> None:
    segments = path.split("/")
    if (
        not path.startswith("/")
        or path.startswith("//")
        or "\\" in path
        or "?" in path
        or "#" in path
        or any(segment in {".", ".."} for segment in segments)
    ):
        raise ProductPortDescriptorInvalid(
            f"{field} must be a same-origin absolute path without traversal, "
            "query, or fragment"
        )


def _require_valid_descriptor(descriptor: ProductPortDescriptorSnapshot) -> None:
    if descriptor.schema_version != _DESCRIPTOR_SCHEMA:
        raise ProductPortDescriptorInvalid(
            f"unsupported product-port descriptor {descriptor.schema_version!r}"
        )
    if not descriptor.owner_module.strip():
        raise ProductPortDescriptorInvalid("descriptor owner_module is required")
    if not descriptor.capability_summary.strip():
        raise ProductPortDescriptorInvalid("descriptor capability_summary is required")
    if descriptor.contract_version < 1:
        raise ProductPortDescriptorInvalid(
            "descriptor contract_version must be positive"
        )
    if descriptor.activation_state not in _DESCRIPTOR_STATES:
        raise ProductPortDescriptorInvalid(
            f"unknown descriptor activation state {descriptor.activation_state!r}"
        )
    if not _SHA256_RE.fullmatch(descriptor.source_revision):
        raise ProductPortDescriptorInvalid(
            "descriptor source_revision must be 64 lowercase hex characters"
        )
    _require_relative_product_path(descriptor.delivery_path, field="delivery_path")
    _require_relative_product_path(descriptor.mirror_path, field="mirror_path")
    computed = product_port_descriptor_digest(descriptor)
    if descriptor.descriptor_digest != computed:
        raise ProductPortDescriptorInvalid(
            "descriptor_digest does not cover the published product-port facts"
        )


def _product_port_snapshot(
    row: CapabilityDestinationRevision,
) -> ProductPortDescriptorSnapshot | None:
    if row.descriptor_digest is None:
        return None
    values = (
        row.descriptor_schema_version,
        row.descriptor_owner_module,
        row.descriptor_capability_summary,
        row.product_binding_id,
        row.delivery_path,
        row.mirror_path,
        row.product_activation_state,
        row.descriptor_source_revision,
    )
    if any(value is None for value in values):  # database constraint in production
        raise ProductPortDescriptorInvalid(
            f"destination revision {row.id} holds an incomplete descriptor snapshot"
        )
    return ProductPortDescriptorSnapshot(
        schema_version=str(row.descriptor_schema_version),
        application=row.application,
        owner_module=str(row.descriptor_owner_module),
        capability_id="",  # filled from the binding by `_destination_binding`
        capability_summary=str(row.descriptor_capability_summary),
        contract_version=row.contract_version,
        destination_binding_id=row.product_binding_id,  # type: ignore[arg-type]
        delivery_path=str(row.delivery_path),
        mirror_path=str(row.mirror_path),
        destination_scope=LocalScope(kind=row.scope_kind, ref=row.scope_ref),
        activation_state=str(row.product_activation_state),
        source_revision=str(row.descriptor_source_revision),
        descriptor_digest=row.descriptor_digest,
    )


def _destination_binding(
    *,
    binding: CapabilityBinding,
    contract: CapabilityContract,
    established: CapabilityDestinationRevision,
) -> DestinationBinding:
    snapshot = _product_port_snapshot(established)
    if snapshot is not None:
        snapshot = ProductPortDescriptorSnapshot(
            schema_version=snapshot.schema_version,
            application=snapshot.application,
            owner_module=snapshot.owner_module,
            capability_id=binding.capability_id,
            capability_summary=snapshot.capability_summary,
            contract_version=snapshot.contract_version,
            destination_binding_id=snapshot.destination_binding_id,
            delivery_path=snapshot.delivery_path,
            mirror_path=snapshot.mirror_path,
            destination_scope=snapshot.destination_scope,
            activation_state=snapshot.activation_state,
            source_revision=snapshot.source_revision,
            descriptor_digest=snapshot.descriptor_digest,
        )
        _require_valid_descriptor(snapshot)
    return DestinationBinding(
        capability_binding_id=binding.id,
        capability_id=binding.capability_id,
        application=established.application,
        scope=LocalScope(kind=established.scope_kind, ref=established.scope_ref),
        contract_version=contract.contract_version,
        destination_revision_id=established.id,
        product_port=snapshot,
    )


# ── Resolution: durable state in, destination out. No payload anywhere. ─────


def _legacy_block_check(revision: ConnectorConfigRevision | None) -> None:
    """A stale destination block must be LOUD, never ignored.

    Silently ignoring one would be the worst available behaviour: an operator
    who wrote a destination the old way would see a configuration that says
    where the traffic goes, and traffic that goes nowhere. Refusing names the
    move and the fix.
    """
    if revision is None:
        return
    config = revision.config_json or {}
    if isinstance(config, Mapping) and LEGACY_DESTINATIONS_KEY in config:
        raise DestinationNotBound(
            f"config revision {revision.id} still carries a "
            f"{LEGACY_DESTINATIONS_KEY!r} block. Destinations moved out of "
            "connector configuration into their own append-only table "
            "(`ig_0004_destinations`) — a routing authority must not share a "
            "lifecycle with endpoint and tuning config. Establish the route "
            "with `establish_destination` and remove the block"
        )


def _current_destination(
    db: Any, capability_binding_id: UUID
) -> CapabilityDestinationRevision:
    """The highest revision for this binding, or a refusal.

    `MAX(revision)` IS the current route: there is no `is_current` flag and no
    pointer column to drift out of agreement with the history.
    """
    from sqlalchemy import select

    row: CapabilityDestinationRevision | None = db.execute(
        select(CapabilityDestinationRevision)
        .where(
            CapabilityDestinationRevision.capability_binding_id == capability_binding_id
        )
        .order_by(CapabilityDestinationRevision.revision.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        raise DestinationNotBound(
            f"capability binding {capability_binding_id} has no destination "
            "revision, so nothing has established where its traffic lands. A "
            "destination is written by an operator BEFORE any provider is "
            "contacted — it is never derived from what arrives"
        )
    return row


def establish_destination(
    db: Any,
    *,
    capability_binding_id: UUID,
    scope: LocalScope,
    registry: CapabilityRegistry,
    established_by: str | None = None,
    reason: str | None = None,
) -> DestinationBinding:
    """Append a new destination revision, or refuse.

    **There is deliberately no `application` parameter.** The destination is the
    application that DECLARED the capability, so a name passed in could only be
    redundant or wrong — and accepting one would make this the single function
    in the module that turns an application name into a routing decision, which
    is precisely the shape its guards exist to keep absent. The operator chooses
    the SCOPE, a decision only they can make; who receives the stream is settled
    by the declaration.

    The owner check still runs on READ, and deriving the value here does not
    make it redundant: a row can arrive by paths that never ran this function (a
    restore, a manual INSERT by a platform operator, a future importer), and a
    capability's declared owner can change after a route was written. Both leave
    a stored application that no longer matches the declaration, and both must
    refuse rather than deliver.

    Appends rather than updates. Re-establishing the SAME destination still
    writes a revision: "an operator reconfirmed this route on the 3rd" is a
    fact worth keeping, and collapsing it would make the history a function of
    what changed rather than of what was decided.
    """
    from sqlalchemy import func, select

    binding = db.get(CapabilityBinding, capability_binding_id)
    if binding is None:
        raise DestinationNotBound(
            f"capability binding {capability_binding_id} does not exist; there "
            "is nothing to route"
        )
    installation = db.get(ConnectorInstallation, binding.installation_id)
    if installation is None:  # pragma: no cover - FK makes this unreachable
        raise DestinationNotBound(
            f"capability binding {capability_binding_id} has no installation"
        )

    contract = require_declared_for_binding(
        registry,
        capability_id=binding.capability_id,
        connector_key=installation.connector_key,
    )
    application = contract.owner.application

    highest = db.execute(
        select(func.max(CapabilityDestinationRevision.revision)).where(
            CapabilityDestinationRevision.capability_binding_id == binding.id
        )
    ).scalar()

    row = CapabilityDestinationRevision(
        id=uuid4(),
        capability_binding_id=binding.id,
        revision=(highest or 0) + 1,
        application=application,
        scope_kind=scope.kind,
        scope_ref=scope.ref,
        contract_version=contract.contract_version,
        established_by=established_by,
        reason=reason,
    )
    db.add(row)
    db.flush()

    return DestinationBinding(
        capability_binding_id=binding.id,
        capability_id=binding.capability_id,
        application=application,
        scope=scope,
        contract_version=contract.contract_version,
        destination_revision_id=row.id,
    )


def reconcile_product_port_descriptor(
    db: Any,
    *,
    capability_binding_id: UUID,
    descriptor: ProductPortDescriptorSnapshot,
    registry: CapabilityRegistry,
    reconciled_by: str | None = None,
) -> DestinationBinding:
    """Idempotently project one authenticated product declaration.

    Network retrieval is deliberately absent. The thin assembly reads and
    authenticates the product endpoint, then gives this transaction a frozen
    value. Re-running repairs a missing or stale projection; an identical digest
    appends nothing.
    """

    from sqlalchemy import func, select

    _require_valid_descriptor(descriptor)
    binding = db.get(CapabilityBinding, capability_binding_id)
    if binding is None:
        raise DestinationNotBound(
            f"capability binding {capability_binding_id} does not exist; there "
            "is nothing to reconcile"
        )
    installation = db.get(ConnectorInstallation, binding.installation_id)
    if installation is None:  # pragma: no cover - FK makes this unreachable
        raise DestinationNotBound(
            f"capability binding {capability_binding_id} has no installation"
        )
    contract = require_declared_for_binding(
        registry,
        capability_id=binding.capability_id,
        connector_key=installation.connector_key,
    )
    if descriptor.capability_id != binding.capability_id:
        raise ProductPortDescriptorInvalid(
            f"descriptor declares {descriptor.capability_id!r}, but binding "
            f"{binding.id} carries {binding.capability_id!r}"
        )
    if descriptor.application != contract.owner.application:
        raise ProductPortDescriptorInvalid(
            f"descriptor application {descriptor.application!r} is not the "
            f"declared owner {contract.owner.application!r}"
        )
    if descriptor.contract_version != contract.contract_version:
        raise ProductPortDescriptorInvalid(
            f"descriptor contract version {descriptor.contract_version} does "
            f"not match {binding.capability_id!r}"
        )

    current = db.execute(
        select(CapabilityDestinationRevision)
        .where(CapabilityDestinationRevision.capability_binding_id == binding.id)
        .order_by(CapabilityDestinationRevision.revision.desc())
        .limit(1)
    ).scalar_one_or_none()
    if (
        current is not None
        and current.descriptor_digest == descriptor.descriptor_digest
    ):
        return _destination_binding(
            binding=binding, contract=contract, established=current
        )

    highest = db.execute(
        select(func.max(CapabilityDestinationRevision.revision)).where(
            CapabilityDestinationRevision.capability_binding_id == binding.id
        )
    ).scalar()
    row = CapabilityDestinationRevision(
        id=uuid4(),
        capability_binding_id=binding.id,
        revision=(highest or 0) + 1,
        application=descriptor.application,
        scope_kind=descriptor.destination_scope.kind,
        scope_ref=descriptor.destination_scope.ref,
        contract_version=descriptor.contract_version,
        descriptor_schema_version=descriptor.schema_version,
        descriptor_owner_module=descriptor.owner_module,
        descriptor_capability_summary=descriptor.capability_summary,
        product_binding_id=descriptor.destination_binding_id,
        delivery_path=descriptor.delivery_path,
        mirror_path=descriptor.mirror_path,
        product_activation_state=descriptor.activation_state,
        descriptor_source_revision=descriptor.source_revision,
        descriptor_digest=descriptor.descriptor_digest,
        established_by=reconciled_by,
        reason="reconcile authenticated product-port descriptor",
    )
    db.add(row)
    db.flush()
    return _destination_binding(binding=binding, contract=contract, established=row)


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
    2. the binding's CURRENT destination revision — the highest, from an
       append-only table an operator writes;
    3. the capability's DECLARED owner from the registry, cross-checked against
       the established application. This is the step that makes an operator
       typo — or a tampered row — a refusal rather than a delivery to the wrong
       application.

    A stale `destinations` block in the connector's configuration is refused
    rather than ignored, so a deployment that missed the move is told.
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
    if revision_id is not None:
        _legacy_block_check(db.get(ConnectorConfigRevision, revision_id))

    contract = require_declared_for_binding(
        registry,
        capability_id=binding.capability_id,
        connector_key=installation.connector_key,
    )
    established = _current_destination(db, binding.id)
    _require_declared_owner(contract, established.application)

    return _destination_binding(
        binding=binding, contract=contract, established=established
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
