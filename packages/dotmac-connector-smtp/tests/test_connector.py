"""Manifest, mode discipline, runtime boundaries and SPI conformance."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from dotmac_connector_smtp import CONNECTOR_KEY, MANIFEST, PLUGIN, __version__
from dotmac_connector_smtp.config import SmtpConfigError
from dotmac_connector_smtp.connection import endpoint_config
from dotmac_connector_smtp.plugin import SEND_CAPABILITY
from dotmac_integration.conformance import (
    assert_connector_conforms,
    assert_plugin_conforms,
)
from dotmac_integration.discovery import ConnectorRegistry
from dotmac_integration.runtime_policy import derive_runtime_policy
from dotmac_integration.spi import (
    ConnectorMode,
    NetworkTarget,
    NetworkTargetError,
    SpiVersion,
    require_network_target,
    validate_network_targets,
    verify_plugin_modes,
)

SRC = Path(__file__).resolve().parents[1] / "src" / "dotmac_connector_smtp"


def test_manifest_identity_mode_and_spi_floor() -> None:
    assert MANIFEST.connector_key == CONNECTOR_KEY == "smtp"
    assert MANIFEST.version == __version__ == "0.1.0a1"
    assert MANIFEST.spi_range.minimum == SpiVersion(1, 7)
    assert [(c.capability_id, c.modes) for c in MANIFEST.capabilities] == [
        (SEND_CAPABILITY, frozenset({ConnectorMode.DELIVERY}))
    ]
    assert PLUGIN.modes == frozenset({ConnectorMode.DELIVERY})
    verify_plugin_modes(PLUGIN)
    assert_connector_conforms(MANIFEST)
    assert_plugin_conforms(PLUGIN)


def test_runtime_boundaries_are_declared_deny_by_default() -> None:
    assert MANIFEST.declares_runtime_boundaries and MANIFEST.declares_fleet_targets
    assert MANIFEST.egress is not None and MANIFEST.egress.hosts == ()
    assert {b.name for b in MANIFEST.secret_bindings or ()} == {"smtp_password"}
    ceiling = [
        (r.protocol, r.first_port, r.last_port)
        for r in MANIFEST.fleet_targets.port_ranges  # type: ignore[union-attr]
    ]
    assert ceiling == [("tcp", 587, 587)]
    policy = derive_runtime_policy(ConnectorRegistry((PLUGIN,)))
    assert policy.egress_hosts == ()
    assert policy.secret_bindings == (("smtp", "smtp_password", False),)
    assert policy.fleet_port_ranges == (("smtp", "tcp", 587, 587),)


def test_an_operator_cannot_bind_a_target_above_the_ceiling() -> None:
    ok = [NetworkTarget("198.51.100.0/24", "tcp", 587, 587)]
    assert validate_network_targets(MANIFEST.fleet_targets, ok) == tuple(ok)
    for bad in (
        NetworkTarget("198.51.100.0/24", "tcp", 25, 25),
        NetworkTarget("198.51.100.0/24", "udp", 587, 587),
        NetworkTarget("198.51.100.0/24", "tcp", 587, 588),
    ):
        with pytest.raises(NetworkTargetError):
            validate_network_targets(MANIFEST.fleet_targets, [bad])


def test_require_network_target_semantics_the_connector_relies_on() -> None:
    bound = [NetworkTarget("198.51.100.0/24", "tcp", 587, 587)]
    assert require_network_target(
        bound, address="198.51.100.7", port=587, protocol="tcp"
    )
    for address, port in (("203.0.113.9", 587), ("198.51.100.7", 25)):
        with pytest.raises(NetworkTargetError):
            require_network_target(bound, address=address, port=port, protocol="tcp")
    with pytest.raises(NetworkTargetError):
        require_network_target(
            bound, address="mail.example.test", port=587, protocol="tcp"
        )


def test_no_credential_default_exists_anywhere_in_the_configuration() -> None:
    schema = MANIFEST.capabilities[0].config_schema
    assert "default" not in str(schema)
    assert "password" not in str(schema["properties"])
    with pytest.raises(SmtpConfigError):
        endpoint_config({})


def test_the_connector_imports_no_kernel_and_opens_no_other_transport() -> None:
    imported: set[str] = set()
    for path in SRC.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
    assert "dotmac_kernel" not in imported
    assert not imported & {"httpx", "requests", "aiohttp", "urllib3"}


def test_validate_connection_does_no_io_and_reports_codes_only() -> None:
    cfg = {
        "host": "mail.example.test",
        "address": "198.51.100.7",
        "port": 587,
        "username": "mailer",
        "from_address": "a@mail.example.test",
    }
    assert [d.code for d in PLUGIN.validate_connection(config=cfg, secrets={})] == [
        "password_missing"
    ]
    assert [
        d.code
        for d in PLUGIN.validate_connection(config=cfg, secrets={"smtp_password": "x"})
    ] == ["config_valid"]
    assert [d.code for d in PLUGIN.validate_connection(config={}, secrets={})] == [
        "config_invalid"
    ]


def test_the_schema_allows_port_587_only() -> None:
    from jsonschema import Draft202012Validator

    validator = Draft202012Validator(MANIFEST.capabilities[0].config_schema)
    base = {
        "host": "mail.example.test",
        "address": "198.51.100.7",
        "from_address": "a@mail.example.test",
    }
    assert not list(validator.iter_errors({**base, "port": 587}))
    for port in (25, 465, 588):
        assert list(validator.iter_errors({**base, "port": port}))
