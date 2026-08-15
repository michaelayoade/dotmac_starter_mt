"""Who OWNS a capability contract — and the three ways a declaration is wrong.

`provider-capability-sources.md` § 7.2 records the gap this module closes:

    There is no capability-id *registry* anywhere in the fleet. The id is an
    open string checked by a regex, with no declaration, no owner and no
    collision check across distributions.

ADR-0008's answer to an open vocabulary is a declaration registry, and its
governing rule is the one that shapes every line below: **the layer that HOSTS a
vocabulary never enumerates its members.** `dotmac-integration` hosts capability
ids. It therefore contains no capability-id literal of its own — proven, not
promised, by `tests/architecture/test_capability_ownership.py`.

## Three parties, three verbs

=========================== ================================================
party                       verb
=========================== ================================================
the **business domain owner** DECLARES the capability. `messaging.receive.v1`
                            is Sub's communications contract; Sub says what an
                            inbound message means, and nobody else can.
**`dotmac-integration`**    VALIDATES and BINDS the declaration. It refuses a
                            duplicate, an unknown and an orphan. It never mints
                            one.
the **connector plugin**    IMPLEMENTS a declared capability at an accepted
                            version. A manifest is a claim to implement, never
                            a claim to define.
=========================== ================================================

That split is ADR-0030 § 8.2's, restated as executable refusals.

## The declaration cannot be an import

ADR-0024 makes applications independent: the Integrator does not import Sub, and
could not — they are separate deployments with separate databases. So a
declaration travels as DATA, supplied by the composing assembly at startup, in
exactly the shape `dotmac_kernel.secret_sources.install_secret_source` already
established for material the module cannot fetch for itself:

* installed ONCE, by :func:`install_capability_registry`;
* read afterwards as a plain lookup, never a fetch;
* **fail closed** — :func:`capability_registry` raises when nothing was
  installed, because an empty registry and an uninstalled one are different
  facts and treating them alike would let a misconfigured assembly accept every
  binding by declaring nothing.

## Three failures, three exceptions, three messages

A single "invalid capability" error would tell an operator that something is
wrong and not what to do about it. Each of these has a different fix, so each
has its own type and its own sentence:

:class:`DuplicateCapabilityDeclaration`
    Two owners claim one id, or one owner declared it twice. Fixed by the
    domain owners, agreeing which of them owns the contract.

:class:`UnknownCapabilityError`
    A binding — or a connector manifest — names an id nobody declared. Fixed by
    the operator, binding a declared capability instead.

:class:`OrphanCapabilityError`
    A declaration no installed connector implements. Fixed by the deployment,
    installing a connector that implements it or retiring the declaration.

The orphan check is the one that looks optional and is not. A declared
capability with no implementation reads, on an operations screen, exactly like a
working integration: the contract is published, the vocabulary resolves, and
nothing will ever arrive. That is ADR-0008's "dead vocabulary that reads as a
working gate", in the one registry where the silence is indistinguishable from
an idle channel.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

from dotmac_integration.spi import CapabilityDeclaration, ConnectorManifest

__all__ = [
    "EMPTY_REGISTRY",
    "CapabilityContract",
    "CapabilityOwner",
    "CapabilityRegistry",
    "CapabilityRegistryError",
    "CapabilityRegistryNotInstalled",
    "DuplicateCapabilityDeclaration",
    "OrphanCapabilityError",
    "UnknownCapabilityError",
    "capability_registry",
    "contract_from_declaration",
    "install_capability_registry",
    "require_declared_for_binding",
    "require_governable",
    "require_implements_only_declared",
    "require_no_orphans",
]


class CapabilityRegistryError(ValueError):
    """A capability declaration, or a reference to one, is not governable."""


class DuplicateCapabilityDeclaration(CapabilityRegistryError):
    """One capability id is declared twice — ambiguous ownership."""


class UnknownCapabilityError(CapabilityRegistryError):
    """Something names a capability id nobody declared."""


class OrphanCapabilityError(CapabilityRegistryError):
    """A declared capability that no installed connector implements."""


class CapabilityRegistryNotInstalled(CapabilityRegistryError):
    """No assembly supplied a registry. Distinct from an EMPTY registry."""


@dataclass(frozen=True, slots=True)
class CapabilityOwner:
    """The application and module that own a capability's MEANING.

    `application` is an application key in the fleet's own vocabulary (`sub`,
    `erp`, …) — not a URL, not a host and not a connector key. The Integrator
    never resolves it to an address; `dotmac_integration.destination_binding`
    does that only against profiles the assembly supplied.
    """

    application: str
    module: str

    def __post_init__(self) -> None:
        for field_name in ("application", "module"):
            value = getattr(self, field_name)
            if not value or value.strip() != value or value != value.lower():
                raise CapabilityRegistryError(
                    f"capability owner {field_name} {value!r} must be a "
                    "non-empty, lowercase, whitespace-free key"
                )

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.application}/{self.module}"


@dataclass(frozen=True, slots=True)
class CapabilityContract:
    """One capability id, its owner, and the version baked into its identity.

    The id shape is validated by constructing Team 1's frozen
    :class:`~dotmac_integration.spi.CapabilityDeclaration`, so the registry and
    the SPI cannot drift into two opinions about what a legal id looks like. A
    second regex here would be a second answer to one question.
    """

    capability_id: str
    owner: CapabilityOwner
    summary: str

    def __post_init__(self) -> None:
        # Raises `InvalidManifestError` on a malformed id — reused deliberately.
        CapabilityDeclaration(capability_id=self.capability_id)
        if not self.summary.strip():
            raise CapabilityRegistryError(
                f"capability {self.capability_id!r} is declared with no summary; "
                "a contract nobody can describe is not a contract"
            )

    @property
    def domain(self) -> str:
        """Everything before the version — `messaging.receive` for `…receive.v1`."""
        return self.capability_id.rsplit(".", 1)[0]

    @property
    def contract_version(self) -> int:
        """The `vN` suffix. The version is part of the id's IDENTITY, so `.v1`
        and `.v2` are two contracts one owner may publish independently."""
        return int(self.capability_id.rsplit(".v", 1)[1])


@dataclass(frozen=True, slots=True)
class CapabilityRegistry:
    """The declared capability vocabulary — supplied, never authored, here.

    Construction is validation, matching
    :class:`~dotmac_integration.discovery.ConnectorRegistry`: a registry that
    exists is one whose ids are unique. Duplicate ownership is refused at build
    time rather than at first use, so a misconfigured composition fails at boot
    where an operator is watching.
    """

    contracts: tuple[CapabilityContract, ...]

    def __post_init__(self) -> None:
        seen: dict[str, CapabilityContract] = {}
        for contract in self.contracts:
            previous = seen.get(contract.capability_id)
            if previous is not None:
                raise DuplicateCapabilityDeclaration(
                    f"capability {contract.capability_id!r} is declared twice — "
                    f"by {previous.owner} and by {contract.owner}. A capability "
                    "id names one business contract with one owner; two "
                    "declarations mean two answers to what a payload MEANS, and "
                    "the Integrator cannot choose between them"
                )
            seen[contract.capability_id] = contract

    @classmethod
    def from_declarations(
        cls, contracts: Iterable[CapabilityContract]
    ) -> CapabilityRegistry:
        return cls(tuple(contracts))

    @property
    def declared_ids(self) -> frozenset[str]:
        return frozenset(c.capability_id for c in self.contracts)

    def get(self, capability_id: str) -> CapabilityContract:
        """The declared contract, or :class:`UnknownCapabilityError`."""
        for contract in self.contracts:
            if contract.capability_id == capability_id:
                return contract
        raise UnknownCapabilityError(
            f"capability {capability_id!r} is not declared by any owning "
            f"application; declared: {sorted(self.declared_ids)}. The owning "
            "business module declares a capability — the Integrator never "
            "mints one, and a connector never mints one"
        )

    def owned_by(self, application: str) -> tuple[CapabilityContract, ...]:
        return tuple(c for c in self.contracts if c.owner.application == application)


# ── The assembly-supplied seam ──────────────────────────────────────────────
#
# A module-level holder, exactly like `dotmac_kernel.secret_sources`: this
# module can neither fetch a declaration nor invent one, so the composing
# assembly hands it over once at startup. `None` means "nothing installed" and
# is deliberately distinguishable from an empty registry.

_INSTALLED: CapabilityRegistry | None = None

#: The sentinel an uninstalled registry is NOT. Kept as a name so a reader sees
#: that "empty" is a legitimate, reachable state — a deployment integrating with
#: nothing declares nothing — and that it is not what an omission produces.
EMPTY_REGISTRY: Final[CapabilityRegistry] = CapabilityRegistry(())


def install_capability_registry(registry: CapabilityRegistry) -> None:
    """Install the declared vocabulary. Called ONCE, at startup, by the assembly.

    Idempotent only in the trivial sense: installing a second, different
    registry replaces the first. That is a deliberate allowance for a controlled
    reload, and it is why the registry is a value object — a caller cannot
    mutate the installed one into a state nothing validated.
    """
    global _INSTALLED
    _INSTALLED = registry


def capability_registry() -> CapabilityRegistry:
    """The installed registry, or a refusal.

    Never returns an empty registry as a stand-in for an uninstalled one. An
    assembly that forgot to declare its vocabulary would otherwise get a
    registry that answers "nobody declared that" to every question — which is
    the *correct* answer to a real unknown capability and a *silent
    misconfiguration* here, and the two must not look alike.
    """
    if _INSTALLED is None:
        raise CapabilityRegistryNotInstalled(
            "no capability registry was installed; the composing assembly must "
            "call install_capability_registry(...) at startup with the "
            "declarations its owning applications published. A deployment that "
            "integrates with nothing installs EMPTY_REGISTRY explicitly"
        )
    return _INSTALLED


def _reset_capability_registry() -> None:
    """Test seam. Restores the uninstalled state — not an empty registry."""
    global _INSTALLED
    _INSTALLED = None


# ── The three governance refusals ───────────────────────────────────────────


def require_declared_for_binding(
    registry: CapabilityRegistry,
    *,
    capability_id: str,
    connector_key: str | None = None,
) -> CapabilityContract:
    """Refusal 2 (binding side): a binding may only name a DECLARED capability.

    `ConnectorManifest.require_declares` already refuses a binding naming a
    capability the *connector* never implements. This is the other half, and the
    two are not redundant: a connector can happily implement an id no business
    owner ever published, and binding it would create a live integration whose
    payloads have no defined meaning anywhere in the fleet.
    """
    try:
        return registry.get(capability_id)
    except UnknownCapabilityError as exc:
        where = f" (binding connector {connector_key!r})" if connector_key else ""
        raise UnknownCapabilityError(f"{exc}{where}") from exc


def require_implements_only_declared(
    registry: CapabilityRegistry, manifest: ConnectorManifest
) -> None:
    """Refusal 2 (connector side): a plugin implements; it does not declare.

    Every capability a manifest claims must already exist in the registry. A
    connector that could add to the vocabulary by publishing a manifest would be
    a connector deciding what a payload means, which ADR-0024 § 7 and
    `provider-capability-sources.md` § 7.2 both put outside the connector layer.
    """
    undeclared = sorted(manifest.capability_ids - registry.declared_ids)
    if undeclared:
        raise UnknownCapabilityError(
            f"connector {manifest.connector_key!r} v{manifest.version} implements "
            f"undeclared capabilities {undeclared}; declared: "
            f"{sorted(registry.declared_ids)}. A connector IMPLEMENTS a "
            "capability an owning application declared — publishing a manifest "
            "is not a way to mint one"
        )


def require_no_orphans(
    registry: CapabilityRegistry,
    manifests: Sequence[ConnectorManifest],
) -> None:
    """Refusal 3: a declaration nothing implements.

    Reported per-capability WITH its owner, because the fix is the owner's
    decision — install a connector, or retire the declaration — and an error
    that only lists ids sends the operator hunting for who published them.
    """
    implemented: set[str] = set()
    for manifest in manifests:
        implemented |= set(manifest.capability_ids)
    orphans = sorted(
        (c for c in registry.contracts if c.capability_id not in implemented),
        key=lambda c: c.capability_id,
    )
    if orphans:
        listed = ", ".join(f"{c.capability_id} (owner {c.owner})" for c in orphans)
        raise OrphanCapabilityError(
            f"declared capabilities with no installed connector: {listed}. A "
            "declaration nobody implements reads on an operations screen exactly "
            "like a working integration — the contract resolves and nothing ever "
            "arrives. Install a connector that implements it, or retire the "
            "declaration"
        )


def require_governable(
    registry: CapabilityRegistry,
    manifests: Sequence[ConnectorManifest],
    *,
    bound_capability_ids: Iterable[str] = (),
) -> None:
    """All three refusals, in the order an operator can act on them.

    Duplicates first — construction already raised, so reaching here proves the
    registry is unambiguous. Then the unknowns, because an unknown id is a typo
    or an unpublished contract and is cheap to fix. Orphans last: they are the
    only failure whose fix may be "install something".
    """
    for manifest in manifests:
        require_implements_only_declared(registry, manifest)
    for capability_id in bound_capability_ids:
        require_declared_for_binding(registry, capability_id=capability_id)
    require_no_orphans(registry, manifests)


def contract_from_declaration(
    declaration: CapabilityDeclaration,
    *,
    owner: CapabilityOwner,
    summary: str,
) -> CapabilityContract:
    """Adapter for an owner publishing its declaration as SPI-shaped data.

    Exists so an owning application can hand over the same
    :class:`~dotmac_integration.spi.CapabilityDeclaration` value its connector
    authors read, rather than restating the id in a second shape where the two
    can drift.
    """
    return CapabilityContract(
        capability_id=declaration.capability_id, owner=owner, summary=summary
    )
