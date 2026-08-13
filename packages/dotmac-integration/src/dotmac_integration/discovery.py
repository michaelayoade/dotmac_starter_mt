"""Package-metadata discovery — the module never imports a connector by name.

ADR-0024 § 7: "the control plane contains no fixed provider enum, import list or
``if provider == ...`` branch." That is enforceable only if the set of installed
connectors is read from the environment rather than written in this package, so
discovery walks entry points in the :data:`ENTRY_POINT_GROUP` group and nothing
else.

## What discovery refuses

* **A duplicate ``connector_key``.** Two installed distributions claiming one
  key is ambiguous ownership, and resolving it by load order would make the
  winner depend on which wheel was installed second.
* **An SPI range that excludes this module.** Checked here so an incompatible
  connector is visible at boot, not at the first dispatch.
* **A malformed manifest.** Refused before any of its values are trusted.

Discovery is FAIL-CLOSED as a set: one bad distribution refuses the whole
registry rather than silently offering the rest. A registry that quietly drops
a connector is worse than one that will not start — the operator configured
that connector for a reason and would be told, by silence, that it is running.

## Loading is separate from discovering

:func:`discover` returns manifests. It does not import connector call paths or
construct handlers, so a manifest can be inspected — and refused — without
executing plugin code. That ordering is the point: a connector that fails its
SPI check must never have run in this process.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from typing import Final

from dotmac_integration.spi import (
    CURRENT_SPI_VERSION,
    ConnectorManifest,
    InvalidManifestError,
    SpiVersion,
)

__all__ = [
    "ENTRY_POINT_GROUP",
    "ConnectorRegistry",
    "DuplicateConnectorError",
    "discover",
]

#: The one group a connector distribution registers itself in. Named after the
#: distribution rather than the module so a reader grepping their own
#: `pyproject.toml` finds the contract.
ENTRY_POINT_GROUP: Final[str] = "dotmac_integration.connectors"


class DuplicateConnectorError(ValueError):
    """Two installed distributions claim one `connector_key`."""


@dataclass(frozen=True, slots=True)
class ConnectorRegistry:
    """The validated set of installed connectors.

    Construction is validation, like `NamespaceRegistry`: a registry that exists
    is one whose members have distinct keys and admit the running SPI.
    """

    manifests: tuple[ConnectorManifest, ...]

    def __post_init__(self) -> None:
        seen: dict[str, str] = {}
        for manifest in self.manifests:
            previous = seen.get(manifest.connector_key)
            if previous is not None:
                raise DuplicateConnectorError(
                    f"connector key {manifest.connector_key!r} is claimed by "
                    f"both {previous} and {manifest.version} — one key, one "
                    "distribution"
                )
            seen[manifest.connector_key] = manifest.version

    def get(self, connector_key: str) -> ConnectorManifest:
        for manifest in self.manifests:
            if manifest.connector_key == connector_key:
                return manifest
        raise InvalidManifestError(
            f"no installed connector declares key {connector_key!r}; installed: "
            f"{sorted(m.connector_key for m in self.manifests)}"
        )

    def require_compatible(
        self, connector_key: str, *, spi_version: SpiVersion = CURRENT_SPI_VERSION
    ) -> ConnectorManifest:
        """The startup/activation re-check.

        Discovery already refused an incompatible range, but a distribution can
        be installed after discovery ran and a binding activated long after
        startup — so the check is repeated wherever a decision depends on it.
        """
        manifest = self.get(connector_key)
        manifest.spi_range.require(spi_version)
        return manifest

    @property
    def keys(self) -> frozenset[str]:
        return frozenset(m.connector_key for m in self.manifests)


def _load(point: EntryPoint) -> ConnectorManifest:
    manifest = point.load()
    if callable(manifest):
        manifest = manifest()
    if not isinstance(manifest, ConnectorManifest):
        raise InvalidManifestError(
            f"entry point {point.name!r} resolved to {type(manifest).__name__}, "
            "not a ConnectorManifest"
        )
    return manifest


def discover(
    *,
    spi_version: SpiVersion = CURRENT_SPI_VERSION,
    points: Iterable[EntryPoint] | None = None,
) -> ConnectorRegistry:
    """Read installed connectors from package metadata and validate the set.

    :param points: injected in tests and by the conformance kit. Production
        callers pass nothing and get the real environment.
    """
    found = list(
        points if points is not None else entry_points(group=ENTRY_POINT_GROUP)
    )
    manifests: list[ConnectorManifest] = []
    for point in found:
        manifest = _load(point)
        # Refuse here as well as at activation: an incompatible connector should
        # be visible at boot, when someone is watching, rather than at the first
        # dispatch, when nobody is.
        manifest.spi_range.require(spi_version)
        manifests.append(manifest)
    return ConnectorRegistry(tuple(manifests))
