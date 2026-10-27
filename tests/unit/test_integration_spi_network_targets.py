"""SPI 1.7 fleet network targets: ceiling, narrowing, pre-I/O refusal.

ADR-0074. The manifest declares the protocols/ports a connector may ever use
against fleet endpoints; installations narrow that ceiling to CIDRs. Every
refusal here happens at declaration, configuration or pre-flight — never
after a packet.
"""

from __future__ import annotations

import pytest
from dotmac_integration.spi import (
    CapabilityDeclaration,
    ConnectorManifest,
    EgressDeclaration,
    FleetPortRange,
    FleetTargetDeclaration,
    InvalidManifestError,
    NetworkTarget,
    NetworkTargetError,
    SpiRange,
    require_network_target,
    validate_network_targets,
)

CEILING = FleetTargetDeclaration(
    port_ranges=(
        FleetPortRange("udp", 3799, 3799),
        FleetPortRange("udp", 1812, 1813),
    )
)


def _manifest(**overrides: object) -> ConnectorManifest:
    kwargs: dict[str, object] = {
        "connector_key": "fleet_fake",
        "version": "1.0.0",
        "spi_range": SpiRange.parse(">=1.4,<2.0"),
        "capabilities": (CapabilityDeclaration(capability_id="network.fake.v1"),),
        "secret_bindings": (),
        "egress": EgressDeclaration(),
    }
    kwargs.update(overrides)
    return ConnectorManifest(**kwargs)  # type: ignore[arg-type]


# ── Manifest ceiling ────────────────────────────────────────────────────────


def test_a_port_range_must_be_a_bounded_known_protocol() -> None:
    with pytest.raises(InvalidManifestError, match="fleet protocol"):
        FleetPortRange("sctp", 3799, 3799)
    with pytest.raises(InvalidManifestError, match="1 <= first_port"):
        FleetPortRange("udp", 3800, 3799)
    with pytest.raises(InvalidManifestError, match="1 <= first_port"):
        FleetPortRange("udp", 0, 3799)
    with pytest.raises(InvalidManifestError, match="1 <= first_port"):
        FleetPortRange("udp", 3799, 70000)
    with pytest.raises(InvalidManifestError, match="integer port"):
        FleetPortRange("udp", True, 3799)  # type: ignore[arg-type]


def test_a_duplicate_port_range_is_refused() -> None:
    with pytest.raises(InvalidManifestError, match="declared twice"):
        FleetTargetDeclaration(
            port_ranges=(
                FleetPortRange("udp", 3799, 3799),
                FleetPortRange("udp", 3799, 3799),
            )
        )


def test_spi_1_7_minimum_requires_the_declaration() -> None:
    with pytest.raises(InvalidManifestError, match="targets SPI >=1.7"):
        _manifest(spi_range=SpiRange.parse(">=1.7,<2.0"))
    explicit = _manifest(
        spi_range=SpiRange.parse(">=1.7,<2.0"),
        fleet_targets=FleetTargetDeclaration(),
    )
    assert explicit.declares_fleet_targets


def test_fleet_targets_require_runtime_boundaries() -> None:
    with pytest.raises(InvalidManifestError, match="runtime boundaries"):
        _manifest(
            spi_range=SpiRange.parse(">=1.0,<2.0"),
            secret_bindings=None,
            egress=None,
            fleet_targets=CEILING,
        )


def test_a_pre_1_7_manifest_keeps_its_digest() -> None:
    """Appended only when declared — existing installation pins stay valid."""
    legacy = _manifest()
    assert not legacy.declares_fleet_targets
    declared_empty = _manifest(fleet_targets=FleetTargetDeclaration())
    assert declared_empty.digest != legacy.digest


def test_the_ceiling_is_digest_covered() -> None:
    narrow = _manifest(fleet_targets=CEILING)
    wider = _manifest(
        fleet_targets=FleetTargetDeclaration(
            port_ranges=(FleetPortRange("udp", 1, 65535),)
        )
    )
    assert narrow.digest != wider.digest


# ── Installation narrowing ──────────────────────────────────────────────────


def test_a_target_must_be_a_strict_cidr() -> None:
    with pytest.raises(NetworkTargetError, match="explicit"):
        NetworkTarget("10.20.0.1", "udp", 3799, 3799)
    with pytest.raises(NetworkTargetError, match="strict CIDR"):
        NetworkTarget("10.20.0.1/24", "udp", 3799, 3799)
    with pytest.raises(NetworkTargetError, match="strict CIDR"):
        NetworkTarget("nas.example.net/24", "udp", 3799, 3799)


def test_a_target_is_canonicalised() -> None:
    assert NetworkTarget("10.20.0.0/16", "udp", 3799, 3799).cidr == "10.20.0.0/16"
    assert NetworkTarget("2001:db8::/32", "tcp", 8729, 8729).cidr == "2001:db8::/32"


def test_narrowing_must_fit_the_ceiling() -> None:
    inside = NetworkTarget("10.20.0.0/16", "udp", 3799, 3799)
    assert validate_network_targets(CEILING, [inside]) == (inside,)
    assert validate_network_targets(CEILING, []) == ()
    with pytest.raises(NetworkTargetError, match="exceeds"):
        validate_network_targets(
            CEILING, [NetworkTarget("10.20.0.0/16", "udp", 3798, 3799)]
        )
    with pytest.raises(NetworkTargetError, match="exceeds"):
        validate_network_targets(
            CEILING, [NetworkTarget("10.20.0.0/16", "tcp", 3799, 3799)]
        )


def test_no_ceiling_means_deny_all() -> None:
    target = NetworkTarget("10.20.0.0/16", "udp", 3799, 3799)
    with pytest.raises(NetworkTargetError, match="deny-all"):
        validate_network_targets(None, [target])
    with pytest.raises(NetworkTargetError, match="deny-all"):
        validate_network_targets(FleetTargetDeclaration(), [target])


# ── Pre-I/O refusal ─────────────────────────────────────────────────────────


def test_require_network_target_matches_and_refuses() -> None:
    targets = (
        NetworkTarget("10.20.0.0/16", "udp", 3799, 3799),
        NetworkTarget("2001:db8::/32", "tcp", 8728, 8729),
    )
    assert (
        require_network_target(targets, address="10.20.3.4", port=3799, protocol="udp")
        is targets[0]
    )
    assert (
        require_network_target(
            targets, address="2001:db8::1", port=8729, protocol="tcp"
        )
        is targets[1]
    )
    with pytest.raises(NetworkTargetError, match="outside the bound"):
        require_network_target(targets, address="10.21.0.1", port=3799, protocol="udp")
    with pytest.raises(NetworkTargetError, match="outside the bound"):
        require_network_target(targets, address="10.20.3.4", port=1812, protocol="udp")
    with pytest.raises(NetworkTargetError, match="outside the bound"):
        require_network_target(targets, address="10.20.3.4", port=3799, protocol="tcp")
    with pytest.raises(NetworkTargetError, match="outside the bound"):
        require_network_target((), address="10.20.3.4", port=3799, protocol="udp")
    with pytest.raises(NetworkTargetError, match="not an IP address"):
        require_network_target(
            targets, address="nas01.example", port=3799, protocol="udp"
        )
    with pytest.raises(NetworkTargetError, match="not a valid port"):
        require_network_target(targets, address="10.20.3.4", port=0, protocol="udp")
