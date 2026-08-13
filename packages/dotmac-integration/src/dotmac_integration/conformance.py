"""The shared fake-plugin conformance kit.

Two jobs, and the second is the one that makes it worth shipping:

1. give this module's own tests a connector with **no provider and no network
   I/O**, so the SPI is exercisable in a unit test;
2. give every future connector author the same contract suite, so "does my
   plugin conform?" is a function call rather than an argument.

Shipped as library code rather than left in `tests/` precisely because of (2).
A conformance kit that lives in the host's test tree cannot be imported by the
distribution it is meant to certify.

## Nothing here reaches the network

The fake connector answers from a dict. That is the point: the whole
installation/configuration/binding slice must be provable without a provider,
and a kit that needed credentials would make every author's first experience of
the SPI a secrets problem.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from importlib.metadata import EntryPoint

from dotmac_integration.discovery import ConnectorRegistry, discover
from dotmac_integration.spi import (
    CURRENT_SPI_VERSION,
    CapabilityDeclaration,
    ConnectorManifest,
    SpiRange,
)

__all__ = [
    "FAKE_CAPABILITY",
    "ConformanceFailure",
    "assert_connector_conforms",
    "fake_manifest",
    "fake_registry",
]

#: A capability id used only by the kit. Deliberately not a real contract name
#: so a fake can never be mistaken for one in a catalog dump.
FAKE_CAPABILITY: str = "conformance.echo.v1"


class ConformanceFailure(AssertionError):
    """A connector manifest does not satisfy the SPI contract."""


def fake_manifest(
    *,
    connector_key: str = "conformance_fake",
    version: str = "1.0.0",
    spi_range: str | None = None,
    capabilities: Sequence[str] = (FAKE_CAPABILITY,),
) -> ConnectorManifest:
    """A valid manifest, with every knob a negative test needs to break."""
    default_range = f">={CURRENT_SPI_VERSION},<{CURRENT_SPI_VERSION.major + 1}.0"
    return ConnectorManifest(
        connector_key=connector_key,
        version=version,
        spi_range=SpiRange.parse(spi_range or default_range),
        capabilities=tuple(
            CapabilityDeclaration(capability_id=c) for c in capabilities
        ),
    )


@dataclass(frozen=True, slots=True)
class _StaticEntryPoint:
    """An `EntryPoint`-shaped stand-in that loads without importing anything.

    `discover()` takes injected points precisely so the kit does not have to
    install a distribution to test discovery.
    """

    name: str
    manifest: ConnectorManifest = field(default_factory=fake_manifest)

    def load(self) -> ConnectorManifest:
        return self.manifest


def fake_registry(
    manifests: Iterable[ConnectorManifest] | None = None,
) -> ConnectorRegistry:
    """Run real discovery over fake entry points.

    Uses `discover()` rather than constructing a `ConnectorRegistry` directly,
    so the kit exercises the same duplicate-key and SPI refusals production
    does. A kit that bypassed them would certify connectors against a contract
    nothing enforces.
    """
    chosen = list(manifests) if manifests is not None else [fake_manifest()]
    points: list[EntryPoint] = [
        _StaticEntryPoint(name=m.connector_key, manifest=m)  # type: ignore[list-item]
        for m in chosen
    ]
    return discover(points=points)


def assert_connector_conforms(manifest: ConnectorManifest) -> None:
    """The contract suite every connector distribution must pass.

    Call it from the distribution's own tests::

        from dotmac_integration.conformance import assert_connector_conforms
        from my_connector import MANIFEST

        def test_conforms() -> None:
            assert_connector_conforms(MANIFEST)
    """
    if not isinstance(manifest, ConnectorManifest):
        raise ConformanceFailure(
            f"expected a ConnectorManifest, got {type(manifest).__name__}"
        )

    if not manifest.spi_range.admits(CURRENT_SPI_VERSION):
        raise ConformanceFailure(
            f"connector {manifest.connector_key!r} declares SPI "
            f"{manifest.spi_range} which excludes the running module "
            f"{CURRENT_SPI_VERSION}"
        )

    if not manifest.capabilities:
        raise ConformanceFailure(
            f"connector {manifest.connector_key!r} declares no capabilities"
        )

    # Discovery must accept it ALONE — a connector that only validates
    # alongside its neighbours is not independently releasable.
    try:
        registry = fake_registry([manifest])
    except Exception as exc:
        raise ConformanceFailure(
            f"connector {manifest.connector_key!r} is not discoverable on its "
            f"own: {exc}"
        ) from exc

    resolved = registry.get(manifest.connector_key)
    for capability in manifest.capabilities:
        # `require_declares` is what activation calls; a connector whose own
        # declarations do not satisfy it could never be bound.
        resolved.require_declares(capability.capability_id)
