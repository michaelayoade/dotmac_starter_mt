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
from dotmac_integration.retry import Outcome, OutcomeStatus
from dotmac_integration.spi import (
    CURRENT_SPI_VERSION,
    CapabilityDeclaration,
    ConnectorManifest,
    ConnectorMode,
    ConnectorPlugin,
    Diagnostic,
    DispatchRequest,
    SpiRange,
)

__all__ = [
    "FAKE_CAPABILITY",
    "ConformanceFailure",
    "FakePlugin",
    "assert_connector_conforms",
    "assert_plugin_conforms",
    "fake_manifest",
    "fake_plugin",
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
class FakePlugin:
    """A connector that answers from a dict — no provider, no network.

    The whole installation/configuration/binding/dispatch slice must be provable
    without a provider, and a kit that needed credentials would make every
    author's first encounter with the SPI a secrets problem.

    `outcome` is settable so a test can drive retry, reconciliation and terminal
    paths without inventing a failing provider.
    """

    manifest_: ConnectorManifest = field(default_factory=lambda: fake_manifest())
    historical: tuple[ConnectorManifest, ...] = ()
    modes_: frozenset[ConnectorMode] = frozenset(
        {ConnectorMode.INGRESS, ConnectorMode.DELIVERY}
    )
    outcome: Outcome = field(
        default_factory=lambda: Outcome(status=OutcomeStatus.SUCCEEDED)
    )
    #: Every request the fake was asked to handle, so a test can assert what
    #: crossed the boundary — in particular that secrets arrived materialized
    #: and that no database session did.
    seen: list[DispatchRequest] = field(default_factory=list)
    healthy: bool = True
    #: Make the handler raise, so the engine's "a raising plugin is retryable"
    #: path is testable without inventing a broken provider. A knob rather than
    #: something a test monkeypatches on: this object is frozen precisely so a
    #: test cannot reshape the contract it is meant to be checking.
    raises: BaseException | None = None

    @property
    def manifest(self) -> ConnectorManifest:
        return self.manifest_

    @property
    def historical_manifests(self) -> tuple[ConnectorManifest, ...]:
        return self.historical

    @property
    def modes(self) -> frozenset[ConnectorMode]:
        return self.modes_

    def handler_for(self, capability_id: str):
        self.manifest_.require_declares(capability_id)

        def _handle(request: DispatchRequest) -> Outcome:
            self.seen.append(request)
            if self.raises is not None:
                raise self.raises
            return self.outcome

        return _handle

    def validate_connection(
        self, *, config: dict, secrets: dict
    ) -> tuple[Diagnostic, ...]:
        if self.healthy:
            return (Diagnostic(ok=True, code="reachable"),)
        return (
            Diagnostic(ok=False, code="unreachable", detail="fake is set unhealthy"),
        )


def fake_plugin(**kwargs: object) -> FakePlugin:
    """A conforming plugin, with every knob a negative test needs."""
    return FakePlugin(**kwargs)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class _StaticEntryPoint:
    """An `EntryPoint`-shaped stand-in that loads without importing anything.

    `discover()` takes injected points precisely so the kit does not have to
    install a distribution to test discovery.
    """

    name: str
    plugin: ConnectorPlugin = field(default_factory=fake_plugin)

    def load(self) -> ConnectorPlugin:
        return self.plugin


def fake_registry(
    manifests: Iterable[ConnectorManifest] | None = None,
    *,
    plugins: Iterable[ConnectorPlugin] | None = None,
) -> ConnectorRegistry:
    """Run real discovery over fake entry points.

    Uses `discover()` rather than constructing a `ConnectorRegistry` directly,
    so the kit exercises the same duplicate-key and SPI refusals production
    does. A kit that bypassed them would certify connectors against a contract
    nothing enforces.
    """
    if plugins is not None:
        chosen = list(plugins)
    elif manifests is not None:
        chosen = [FakePlugin(manifest_=m) for m in manifests]
    else:
        chosen = [fake_plugin()]
    points: list[EntryPoint] = [
        _StaticEntryPoint(name=p.manifest.connector_key, plugin=p)  # type: ignore[list-item]
        for p in chosen
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


def assert_plugin_conforms(plugin: ConnectorPlugin) -> None:
    """The EXECUTABLE half of the contract suite.

    `assert_connector_conforms` checks the metadata; this checks that the plugin
    can actually be used — a distribution that declares a capability it cannot
    hand back a handler for passes every metadata check and fails at the first
    dispatch.
    """
    assert_connector_conforms(plugin.manifest)

    if not plugin.modes:
        raise ConformanceFailure(
            f"connector {plugin.manifest.connector_key!r} declares no modes, so "
            "the runtime cannot know which workers to start for it"
        )

    for capability in plugin.manifest.capabilities:
        try:
            handler = plugin.handler_for(capability.capability_id)
        except Exception as exc:
            raise ConformanceFailure(
                f"connector {plugin.manifest.connector_key!r} declares "
                f"{capability.capability_id!r} but returns no handler: {exc}"
            ) from exc
        if not callable(handler):
            raise ConformanceFailure(
                f"handler for {capability.capability_id!r} is not callable"
            )

    # An undeclared capability must be refused, not silently handled.
    try:
        plugin.handler_for("conformance.undeclared.v1")
    except Exception:  # noqa: S110 - the refusal IS the expected path
        pass
    else:
        raise ConformanceFailure(
            f"connector {plugin.manifest.connector_key!r} returned a handler for "
            "a capability it never declared"
        )

    diagnostics = plugin.validate_connection(config={}, secrets={})
    if not isinstance(diagnostics, tuple):
        raise ConformanceFailure("validate_connection must return a tuple")

    # Historical manifests must keep the same key, or the adoption window would
    # smuggle a second connector in under one entry point.
    for historical in plugin.historical_manifests:
        if historical.connector_key != plugin.manifest.connector_key:
            raise ConformanceFailure(
                f"historical manifest {historical.connector_key!r} does not "
                f"match {plugin.manifest.connector_key!r} — the adoption window "
                "is for one connector's own past, not another connector"
            )
