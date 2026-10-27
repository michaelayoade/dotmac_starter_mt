"""Manifest, mode discipline, claimed digests and SPI conformance."""

from __future__ import annotations

from dotmac_connector_freeradius import (
    CONNECTOR_KEY,
    MANIFEST,
    PLUGIN,
    __version__,
)
from dotmac_connector_freeradius.plugin import (
    DISCONNECT_CONTRACT_DIGEST,
    OBSERVATION_CONTRACT_DIGEST,
    PROJECTION_CONTRACT_DIGEST,
)
from dotmac_integration.conformance import assert_plugin_conforms
from dotmac_integration.spi import ConnectorMode, SpiVersion, verify_plugin_modes


def test_manifest_identity_modes_and_spi_floor() -> None:
    assert MANIFEST.connector_key == CONNECTOR_KEY == "freeradius"
    assert MANIFEST.version == __version__ == "0.1.0a1"
    assert MANIFEST.spi_range.minimum == SpiVersion(1, 7)
    modes = {
        declaration.capability_id: declaration.modes
        for declaration in MANIFEST.capabilities
    }
    assert modes == {
        "network.access_projection.v1": frozenset({ConnectorMode.PROVISION}),
        "network.access_observation.v1": frozenset({ConnectorMode.POLL}),
        "network.session_disconnect.v1": frozenset({ConnectorMode.PROVISION}),
    }
    verify_plugin_modes(PLUGIN)
    assert_plugin_conforms(PLUGIN)


def test_claimed_digests_are_subs_published_contracts() -> None:
    claims = {
        declaration.capability_id: declaration.claims_contract_digest
        for declaration in MANIFEST.capabilities
    }
    assert claims == {
        "network.access_projection.v1": PROJECTION_CONTRACT_DIGEST,
        "network.access_observation.v1": OBSERVATION_CONTRACT_DIGEST,
        "network.session_disconnect.v1": DISCONNECT_CONTRACT_DIGEST,
    }


def test_boundaries_are_declared() -> None:
    assert MANIFEST.declares_runtime_boundaries
    assert MANIFEST.declares_fleet_targets
    assert {binding.name for binding in MANIFEST.secret_bindings or ()} == {
        "freeradius_dsn",
        "secret_fetch_token",
    }
    assert set(MANIFEST.egress.hosts if MANIFEST.egress else ()) == {
        "freeradius-db",
        "sub",
    }
    ceiling = {
        (item.protocol, item.first_port, item.last_port)
        for item in (
            MANIFEST.fleet_targets.port_ranges if MANIFEST.fleet_targets else ()
        )
    }
    assert ceiling == {("udp", 3799, 3799), ("udp", 1700, 1700)}
