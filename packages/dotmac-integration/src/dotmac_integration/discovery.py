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

## Loading is separate from RUNNING

:func:`discover` resolves each entry point to a :class:`ConnectorPlugin` and
reads its manifest. It does not build handlers, materialize secrets or contact a
provider, so a connector can be inspected — and refused — before any of its call
paths execute. That ordering is the point: a connector that fails its SPI check
must never have reached a provider from this process.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from typing import Final

from dotmac_integration.spi import (
    CURRENT_SPI_VERSION,
    ConnectorManifest,
    ConnectorPlugin,
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
    """The validated set of installed connector PLUGINS.

    Plugins, not bare manifests: the module has to DO something with a connector
    — start the right workers, validate a connection, call a handler — and
    metadata alone cannot run anything.

    Construction is validation, like `NamespaceRegistry`: a registry that exists
    is one whose members have distinct keys and admit the running SPI.
    """

    plugins: tuple[ConnectorPlugin, ...]

    def __post_init__(self) -> None:
        seen: dict[str, str] = {}
        for plugin in self.plugins:
            manifest = plugin.manifest
            previous = seen.get(manifest.connector_key)
            if previous is not None:
                raise DuplicateConnectorError(
                    f"connector key {manifest.connector_key!r} is claimed by "
                    f"both {previous} and {manifest.version} — one key, one "
                    "distribution. A historical manifest belongs INSIDE the "
                    "distribution that supersedes it, not beside it"
                )
            seen[manifest.connector_key] = manifest.version

    @property
    def manifests(self) -> tuple[ConnectorManifest, ...]:
        return tuple(plugin.manifest for plugin in self.plugins)

    def plugin(self, connector_key: str) -> ConnectorPlugin:
        for plugin in self.plugins:
            if plugin.manifest.connector_key == connector_key:
                return plugin
        raise InvalidManifestError(
            f"no installed connector declares key {connector_key!r}; installed: "
            f"{sorted(p.manifest.connector_key for p in self.plugins)}"
        )

    def get(self, connector_key: str) -> ConnectorManifest:
        """The plugin's CURRENT manifest."""
        return self.plugin(connector_key).manifest

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
        return frozenset(p.manifest.connector_key for p in self.plugins)


def _load(point: EntryPoint) -> ConnectorPlugin:
    """Resolve ONE entry point to a plugin.

    A factory is accepted so a distribution can defer building its plugin, but
    the result must satisfy `ConnectorPlugin` — resolving to a bare manifest is
    refused, because a registry of metadata cannot run a connector.
    """
    plugin = point.load()
    if callable(plugin) and not isinstance(plugin, ConnectorPlugin):
        plugin = plugin()
    if isinstance(plugin, ConnectorManifest):
        raise InvalidManifestError(
            f"entry point {point.name!r} resolved to a ConnectorManifest. The "
            "SPI expects a ConnectorPlugin: the module must be able to call a "
            "handler, not only read metadata"
        )
    if not isinstance(plugin, ConnectorPlugin):
        raise InvalidManifestError(
            f"entry point {point.name!r} resolved to {type(plugin).__name__}, "
            "which does not satisfy the ConnectorPlugin protocol"
        )
    return plugin


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
    plugins: list[ConnectorPlugin] = []
    for point in found:
        plugin = _load(point)
        # Refuse here as well as at activation: an incompatible connector should
        # be visible at boot, when someone is watching, rather than at the first
        # dispatch, when nobody is.
        plugin.manifest.spi_range.require(spi_version)
        plugins.append(plugin)
    return ConnectorRegistry(tuple(plugins))
