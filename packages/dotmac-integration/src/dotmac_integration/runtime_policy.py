"""Project installed connector declarations into one immutable runtime policy.

This is the bridge between a connector's package metadata and deployment
enforcement.  It deliberately does not know Docker, Podman, OpenBao or a
provider: an assembly renders this value into the mechanism it deploys, and
there is no second list for that assembly to maintain.

A pre-1.3 manifest may still be READ during a bounded adoption window.  It may
not be projected into policy, because treating an omitted boundary as an empty
one would silently grant a legacy connector the appearance of deny-all evidence
without proving what it needs.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from dotmac_integration.discovery import ConnectorRegistry
from dotmac_integration.spi import SecretBindingDeclaration

__all__ = [
    "ConnectorRuntimePolicy",
    "RuntimeBoundaryMissing",
    "RuntimePolicy",
    "derive_runtime_policy",
]


class RuntimeBoundaryMissing(ValueError):
    """An installed connector cannot produce enforceable runtime policy."""


@dataclass(frozen=True, slots=True)
class ConnectorRuntimePolicy:
    """One connector's already-validated declaration."""

    connector_key: str
    manifest_digest: str
    secret_bindings: tuple[SecretBindingDeclaration, ...]
    egress_hosts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RuntimePolicy:
    """The complete policy for one discovered connector registry."""

    connectors: tuple[ConnectorRuntimePolicy, ...]

    @property
    def egress_hosts(self) -> tuple[str, ...]:
        """The exact, deterministic union a deployment may allow."""

        return tuple(
            sorted(
                {
                    host
                    for connector in self.connectors
                    for host in connector.egress_hosts
                }
            )
        )

    @property
    def secret_bindings(self) -> tuple[tuple[str, str, bool], ...]:
        """Connector key, logical binding name and requiredness."""

        return tuple(
            sorted(
                (
                    connector.connector_key,
                    binding.name,
                    binding.required,
                )
                for connector in self.connectors
                for binding in connector.secret_bindings
            )
        )

    @property
    def digest(self) -> str:
        """Stable identity of the installed manifest set this policy projects."""

        material = "|".join(
            f"{connector.connector_key}:{connector.manifest_digest}"
            for connector in self.connectors
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


def derive_runtime_policy(registry: ConnectorRegistry) -> RuntimePolicy:
    """Refuse omission; otherwise project every current manifest exactly once."""

    projected: list[ConnectorRuntimePolicy] = []
    for manifest in sorted(registry.manifests, key=lambda item: item.connector_key):
        if not manifest.declares_runtime_boundaries:
            raise RuntimeBoundaryMissing(
                f"connector {manifest.connector_key!r} has a pre-1.3 manifest "
                "with no runtime boundary declarations; install a connector "
                "release that declares its secret bindings and egress"
            )
        # The predicate above narrows these conceptually.  Keep explicit local
        # names so a future field refactor cannot accidentally default omission
        # to allow/deny policy.
        secret_bindings = manifest.secret_bindings
        egress = manifest.egress
        if secret_bindings is None or egress is None:  # pragma: no cover - guard
            raise RuntimeBoundaryMissing(manifest.connector_key)
        projected.append(
            ConnectorRuntimePolicy(
                connector_key=manifest.connector_key,
                manifest_digest=manifest.digest,
                secret_bindings=tuple(
                    sorted(secret_bindings, key=lambda item: item.name)
                ),
                egress_hosts=tuple(sorted(egress.hosts)),
            )
        )
    return RuntimePolicy(tuple(projected))
