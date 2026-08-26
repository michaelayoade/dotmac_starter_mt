"""The versioned manifest and the plugin that binds delivery + poll together.

One distribution, two modes. DELIVERY performs the registry commands the owning
application decided to send; POLL observes what the registry queued. There is
no INGRESS mode — EPP has no webhook, so there is no HTTP body to authenticate,
and declaring an ingress capability here would make ``verify_plugin_modes``
call a factory this connector cannot honestly provide.

This module owns the manifest and the connection health check; the wire lives
in :mod:`epp` / :mod:`frames`, the SPI adapters in :mod:`delivery` /
:mod:`polling`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from dotmac_integration.spi import (
    CapabilityDeclaration,
    CapabilityHandler,
    ConnectorManifest,
    ConnectorMode,
    Diagnostic,
    EgressDeclaration,
    PollHandler,
    SecretBindingDeclaration,
    SpiRange,
)

from dotmac_connector_nira.delivery import (
    CLIENT_PEM,
    EPP_PASSWORD,
    OUTBOUND_CAPABILITY_IDS,
    OUTBOUND_CONFIG_SCHEMA,
    NiraDeliveryHandler,
    _material,
    _num,
    _text,
)
from dotmac_connector_nira.epp import (
    EppProtocolError,
    EppSession,
    EppTransportError,
    declared_services,
)
from dotmac_connector_nira.polling import MESSAGE_CAPABILITY, NiraPollHandler

CONNECTOR_KEY: Final = "nira"
VERSION: Final = "0.1.0a1"

# The registry declares these in its greeting; the connector needs the objects
# to operate and speaks the fee/secDNS extensions. Confirmed live against
# ote.registry.ng: objURI domain/host/contact, extURI rgp/secDNS/fee + CoCCA
# finance.
_REQUIRED_OBJECTS: Final = frozenset(
    {
        "urn:ietf:params:xml:ns:domain-1.0",
        "urn:ietf:params:xml:ns:host-1.0",
        "urn:ietf:params:xml:ns:contact-1.0",
    }
)

POLL_CONFIG_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["host", "port", "clid", "connect_timeout", "read_timeout"],
    "properties": {
        "host": {"type": "string", "minLength": 1},
        "port": {"type": "integer", "minimum": 1, "maximum": 65535},
        "clid": {"type": "string", "minLength": 1},
        "connect_timeout": {"type": "number", "minimum": 1, "maximum": 120},
        "read_timeout": {"type": "number", "minimum": 1, "maximum": 300},
    },
}

_SECRET_BINDINGS: Final = (
    SecretBindingDeclaration(
        name=EPP_PASSWORD,
        description="Registrar EPP password for the CoCCA registry login.",
    ),
    SecretBindingDeclaration(
        name=CLIENT_PEM,
        required=False,
        description=(
            "Combined client certificate and private key in one PEM, when the "
            "registry requires mutual TLS. Materialized per call, never persisted."
        ),
    ),
)

MANIFEST: Final = ConnectorManifest(
    connector_key=CONNECTOR_KEY,
    version=VERSION,
    spi_range=SpiRange.parse(">=1.4,<2.0"),
    capabilities=(
        *(
            CapabilityDeclaration(
                capability_id=capability_id,
                config_schema=OUTBOUND_CONFIG_SCHEMA,
                modes=frozenset({ConnectorMode.DELIVERY}),
            )
            for capability_id in OUTBOUND_CAPABILITY_IDS
        ),
        CapabilityDeclaration(
            capability_id=MESSAGE_CAPABILITY,
            config_schema=POLL_CONFIG_SCHEMA,
            modes=frozenset({ConnectorMode.POLL}),
        ),
    ),
    secret_bindings=_SECRET_BINDINGS,
    egress=EgressDeclaration(),  # host is per-installation config (OT&E vs prod)
)


@dataclass(frozen=True, slots=True)
class NiraConnector:
    """One independently released NiRA/CoCCA EPP delivery + poll plugin."""

    manifest: ConnectorManifest = MANIFEST
    historical_manifests: tuple[ConnectorManifest, ...] = ()
    modes: frozenset[ConnectorMode] = frozenset(
        {ConnectorMode.DELIVERY, ConnectorMode.POLL}
    )

    def handler_for(self, capability_id: str) -> CapabilityHandler:
        self.manifest.require_declares(capability_id)
        return NiraDeliveryHandler(capability_id)

    def poll_handler_for(self, capability_id: str) -> PollHandler:
        self.manifest.require_declares(capability_id)
        return NiraPollHandler()

    def validate_connection(
        self,
        *,
        config: Mapping[str, object],
        secrets: Mapping[str, object],
    ) -> tuple[Diagnostic, ...]:
        """Open TLS, read the greeting, confirm the registry offers what we need.

        No login is attempted: a greeting proves the channel and the registry's
        declared services without spending a credential, and an unwhitelisted
        source IP fails at login with 2202 — a condition the connector cannot
        resolve and must not mask as a health pass.
        """
        if _material(secrets, EPP_PASSWORD) is None:
            return (Diagnostic(ok=False, code="required_material_unavailable"),)
        host = _text(config.get("host"))
        port = config.get("port")
        connect_timeout = _num(config.get("connect_timeout")) or 15.0
        read_timeout = _num(config.get("read_timeout")) or 30.0
        if host is None or not isinstance(port, int) or isinstance(port, bool):
            return (Diagnostic(ok=False, code="config_incomplete"),)
        client_pem = _material(secrets, CLIENT_PEM)
        session = EppSession(
            host,
            port,
            client_pem=client_pem,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
        )
        try:
            greeting = session.connect()
        except EppTransportError:
            return (Diagnostic(ok=False, code="registry_unreachable"),)
        except EppProtocolError:
            return (Diagnostic(ok=False, code="greeting_unreadable"),)
        finally:
            session.close()
        offered = set(declared_services(greeting).get("objects", ()))
        if not _REQUIRED_OBJECTS.issubset(offered):
            return (Diagnostic(ok=False, code="registry_objects_insufficient"),)
        return ()


PLUGIN: Final = NiraConnector()
