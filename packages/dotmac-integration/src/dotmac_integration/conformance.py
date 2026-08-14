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

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from dotmac_integration.discovery import ConnectorRegistry, discover
from dotmac_integration.retry import Outcome, OutcomeStatus
from dotmac_integration.spi import (
    CURRENT_SPI_VERSION,
    MODE_PROTOCOLS,
    CapabilityDeclaration,
    CapabilityHandler,
    ConnectorManifest,
    ConnectorMode,
    ConnectorPlugin,
    Diagnostic,
    DispatchRequest,
    InboundEvent,
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
    #: What `normalize` returns. A TUPLE by default and empty by default, so a
    #: test must opt into events rather than inherit one it did not think about.
    inbound: tuple[InboundEvent, ...] = ()
    #: Drive the rejected-signature path without owning a signing secret.
    signature_valid: bool = True
    #: Every raw body `verify` was handed, so a test can assert the EXACT bytes
    #: crossed the boundary — a re-serialization would invalidate a real HMAC.
    verified: list[bytes] = field(default_factory=list)
    #: Every raw body `normalize` was handed. Kept SEPARATE from `verified` so a
    #: test can assert normalization never ran on a body whose signature was
    #: rejected — "nothing was written" would still pass if it had.
    normalized: list[bytes] = field(default_factory=list)
    #: Every params mapping `challenge` was handed, WHOLE. A test asserts the
    #: entire mapping crossed rather than a key the module selected, because
    #: selecting one would be provider knowledge in a module that may hold none.
    challenged: list[Mapping[str, str]] = field(default_factory=list)
    #: Every `config` mapping an ingress call was handed, and every `secrets`
    #: one. Recorded because "no database session reached the plugin" is
    #: otherwise asserted about the raw bodies — where it reduces to "bytes are
    #: not a Session", true for reasons that have nothing to do with the engine
    #: — while the route a session would actually take is a `dict[str, Any]`
    #: config VALUE, which no field-type check inspects.
    configs_seen: list[dict] = field(default_factory=list)
    secrets_seen: list[dict] = field(default_factory=list)
    #: Make `verify` and `challenge` throw, driving the engine's
    #: `ConnectorRaised` path — including the assertion that the thrown
    #: message, built from provider-controlled bytes, never escapes.
    ingress_raises: BaseException | None = None
    #: The same for `normalize`, separately: a throw AFTER a good signature is
    #: the case where a partial batch would otherwise be tempting.
    normalize_raises: BaseException | None = None
    #: Return a `list` instead of a `tuple`, driving `ConnectorContract`. A
    #: plugin that hands back the wrong container is not a plugin whose output
    #: the engine may iterate.
    ingress_contract_broken: bool = False

    @property
    def manifest(self) -> ConnectorManifest:
        return self.manifest_

    @property
    def historical_manifests(self) -> tuple[ConnectorManifest, ...]:
        return self.historical

    @property
    def modes(self) -> frozenset[ConnectorMode]:
        return self.modes_

    def handler_for(self, capability_id: str) -> CapabilityHandler:
        self.manifest_.require_declares(capability_id)

        def _handle(request: DispatchRequest) -> Outcome:
            self.seen.append(request)
            if self.raises is not None:
                raise self.raises
            return self.outcome

        return _handle

    def ingress_handler_for(self, capability_id: str):
        """An ingress handler that needs no provider and no credentials.

        `verify` answers from the `signature_valid` knob rather than computing an
        HMAC: the kit exists to let an author drive the ENGINE's paths — accepted,
        rejected, batch rollback — without owning a signing secret. A real
        connector's verification is its own business and is tested against its
        own vectors.
        """
        self.manifest_.require_declares(capability_id)
        fake = self

        class _Ingress:
            def challenge(
                self, params: Mapping[str, str], *, config: dict, secrets: dict
            ) -> str | None:
                fake.challenged.append(dict(params))
                fake.configs_seen.append(config)
                fake.secrets_seen.append(secrets)
                if fake.ingress_raises is not None:
                    raise fake.ingress_raises
                # A NEUTRAL key. The kit ships inside a module that may not name
                # a provider, and a real provider's handshake parameter is that
                # provider's business — the engine hands over the whole mapping
                # precisely so the connector, not this module, picks the key.
                return params.get("challenge")

            def verify(
                self,
                raw_body: bytes,
                headers: Mapping[str, str],
                *,
                config: dict,
                secrets: dict,
            ) -> bool:
                fake.verified.append(raw_body)
                fake.configs_seen.append(config)
                fake.secrets_seen.append(secrets)
                if fake.ingress_raises is not None:
                    raise fake.ingress_raises
                return fake.signature_valid

            def normalize(
                self, raw_body: bytes, headers: Mapping[str, str], *, config: dict
            ) -> tuple[InboundEvent, ...]:
                fake.normalized.append(raw_body)
                fake.configs_seen.append(config)
                if fake.normalize_raises is not None:
                    raise fake.normalize_raises
                if fake.ingress_contract_broken:
                    return list(fake.inbound)  # type: ignore[return-value]
                return fake.inbound

        return _Ingress()

    def validate_connection(
        self, *, config: dict[str, object], secrets: dict[str, object]
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
    # Structurally EntryPoint-shaped, deliberately not an EntryPoint: `discover`
    # takes injected points so the kit never has to install a distribution to
    # test discovery. Typed as the protocol's structural shape rather than
    # silenced — the previous `# type: ignore[list-item]` named an error code
    # mypy no longer raises here, which is its own kind of stale.
    points: list[Any] = [
        _StaticEntryPoint(name=p.manifest.connector_key, plugin=p) for p in chosen
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

    key = plugin.manifest.connector_key

    # ── The mode implication, BOTH ways ─────────────────────────────────────
    #
    # One direction alone is not enough. A plugin declaring DELIVERY without
    # `handler_for` fails at the first dispatch; one implementing `handler_for`
    # without declaring DELIVERY never gets its workers started. Both are
    # unusable, both used to pass, and only checking both catches both.
    for mode, contract in MODE_PROTOCOLS.items():
        declares = mode in plugin.modes
        implements = isinstance(plugin, contract.protocol)
        if declares and not implements:
            raise ConformanceFailure(
                f"connector {key!r} declares mode {mode.value!r} but does not "
                f"implement {contract.protocol.__name__} "
                f"(missing {contract.factory!r})"
            )
        if implements and not declares:
            raise ConformanceFailure(
                f"connector {key!r} implements {contract.factory!r} but does not "
                f"declare mode {mode.value!r}, so the runtime will never start "
                "the workers that would call it"
            )

    # ── Each declared mode's factory, over every declared capability ────────
    for mode in sorted(plugin.modes, key=lambda m: m.value):
        factory = getattr(plugin, MODE_PROTOCOLS[mode].factory)
        for capability in plugin.manifest.capabilities:
            try:
                handler = factory(capability.capability_id)
            except Exception as exc:
                raise ConformanceFailure(
                    f"connector {key!r} declares {capability.capability_id!r} "
                    f"for mode {mode.value!r} but returns no handler: {exc}"
                ) from exc
            if handler is None:
                raise ConformanceFailure(
                    f"{mode.value} handler for {capability.capability_id!r} is None"
                )

        # An undeclared capability must be refused, not silently handled. The
        # check is now per MODE — an ingress-only connector has no `handler_for`
        # to probe — but it keeps the positive-assertion form rather than
        # `except ...: pass`: bandit reads try/except/pass as a swallowed error
        # (B110), and the suppression that used to sit here named ruff's
        # equivalent code instead of bandit's, aimed at the wrong tool.
        refused = False
        try:
            factory("conformance.undeclared.v1")
        except Exception:
            refused = True
        if not refused:
            raise ConformanceFailure(
                f"connector {key!r} returned a {mode.value} handler for a "
                "capability it never declared"
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
