"""Manifest and plugin for the FreeRADIUS connector (SPI >=1.7,<2.0).

Claimed digests below are Sub's published contract digests (Sub
`app/services/integrations/network_capability_contracts.py`, golden-pinned on
both sides). A schema change is a new capability id and a new claim here —
never a silent edit to either side.

Egress names the two deployment aliases this connector is designed to run
beside: the FreeRADIUS database service and the product service that owns
the credential fetch endpoint. A deployment that names its services
differently needs a reviewed connector release; that stiffness is the
manifest's job, not an oversight.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

import sqlalchemy as sa
from dotmac_integration.spi import (
    CapabilityDeclaration,
    ConnectorManifest,
    ConnectorMode,
    Diagnostic,
    EgressDeclaration,
    FleetPortRange,
    FleetTargetDeclaration,
    PollHandler,
    ProvisioningHandler,
    SecretBindingDeclaration,
    SpiRange,
    canonical_digest,
)

from dotmac_connector_freeradius.coa import UdpCoaPort
from dotmac_connector_freeradius.config import CONFIG_SCHEMA, target_config
from dotmac_connector_freeradius.database import SqlAlchemyRadiusDb
from dotmac_connector_freeradius.polling import FreeRadiusPollHandler
from dotmac_connector_freeradius.ports import (
    CoaPort,
    RadiusDbPort,
    SecretFetchPort,
)
from dotmac_connector_freeradius.provisioning import (
    DISCONNECT_CAPABILITY,
    PROJECTION_CAPABILITY,
    DbFactory,
    FreeRadiusProvisioningHandler,
)

CONNECTOR_KEY: Final = "freeradius"
OBSERVATION_CAPABILITY: Final = "network.access_observation.v1"

FREERADIUS_DSN: Final = "freeradius_dsn"
SECRET_FETCH_TOKEN: Final = "secret_fetch_token"

PROJECTION_CONTRACT_DIGEST: Final = (
    "4240e12e512486a9a20202addc779e8d2dbac041fcd53e06903472eb239195b2"
)
OBSERVATION_CONTRACT_DIGEST: Final = (
    "4b1ef5ffc129d4b6f8317996786299bf011815595b2529a672848a1b4a3d69c3"
)
DISCONNECT_CONTRACT_DIGEST: Final = (
    "e683ccdc57de2a0a3df80c27216815c2a4cfa678f5af1e119213ea599c63c6ed"
)

MANIFEST = ConnectorManifest(
    connector_key=CONNECTOR_KEY,
    version="0.1.0a1",
    spi_range=SpiRange.parse(">=1.7,<2.0"),
    capabilities=(
        CapabilityDeclaration(
            capability_id=PROJECTION_CAPABILITY,
            config_schema=CONFIG_SCHEMA,
            modes=frozenset({ConnectorMode.PROVISION}),
            claims_contract_digest=PROJECTION_CONTRACT_DIGEST,
        ),
        CapabilityDeclaration(
            capability_id=OBSERVATION_CAPABILITY,
            config_schema=CONFIG_SCHEMA,
            modes=frozenset({ConnectorMode.POLL}),
            claims_contract_digest=OBSERVATION_CONTRACT_DIGEST,
        ),
        CapabilityDeclaration(
            capability_id=DISCONNECT_CAPABILITY,
            config_schema=CONFIG_SCHEMA,
            modes=frozenset({ConnectorMode.PROVISION}),
            claims_contract_digest=DISCONNECT_CONTRACT_DIGEST,
        ),
    ),
    secret_bindings=(
        SecretBindingDeclaration(
            name=FREERADIUS_DSN,
            description=(
                "SQLAlchemy DSN for the FreeRADIUS SQL target. The role needs "
                "SELECT/INSERT/DELETE on radcheck, radreply and radusergroup, "
                "SELECT on radacct, radpostauth and nas."
            ),
        ),
        SecretBindingDeclaration(
            name=SECRET_FETCH_TOKEN,
            required=False,
            description=(
                "Credential the assembly's secret-fetch transport presents to "
                "the credential owner's mutually-authenticated fetch endpoint "
                "(ADR-0076 section 7). Unused until that transport is bound."
            ),
        ),
    ),
    egress=EgressDeclaration(hosts=("freeradius-db", "sub")),
    fleet_targets=FleetTargetDeclaration(
        port_ranges=(
            FleetPortRange("udp", 3799, 3799),
            FleetPortRange("udp", 1700, 1700),
        )
    ),
)


class _EngineCache:
    """One engine per (DSN, tables) — connectors are process-global."""

    def __init__(self) -> None:
        self._ports: dict[str, SqlAlchemyRadiusDb] = {}

    def db_for(
        self, config: Mapping[str, object], secrets: Mapping[str, object]
    ) -> RadiusDbPort:
        settings = target_config(config)
        dsn_value = secrets.get(FREERADIUS_DSN)
        dsn = dsn_value if isinstance(dsn_value, str) else None
        if not dsn:
            raise RuntimeError("freeradius_dsn secret binding is not materialized")
        key = canonical_digest(
            {
                "dsn": dsn,
                "tables": [
                    settings.radcheck_table,
                    settings.radreply_table,
                    settings.radusergroup_table,
                    settings.radacct_table,
                    settings.radpostauth_table,
                    settings.nas_table,
                ],
            }
        )
        port = self._ports.get(key)
        if port is None:
            port = SqlAlchemyRadiusDb(
                sa.create_engine(dsn, pool_pre_ping=True),
                radcheck_table=settings.radcheck_table,
                radreply_table=settings.radreply_table,
                radusergroup_table=settings.radusergroup_table,
                radacct_table=settings.radacct_table,
                radpostauth_table=settings.radpostauth_table,
                nas_table=settings.nas_table,
            )
            self._ports[key] = port
        return port


class FreeRadiusConnector:
    """Process-global plugin; ports are assembly-bindable seams."""

    def __init__(
        self,
        *,
        secret_fetch: SecretFetchPort | None = None,
        coa_port: CoaPort | None = None,
        db_factory: DbFactory | None = None,
    ) -> None:
        self._cache = _EngineCache()
        self._db_factory: DbFactory = db_factory or self._cache.db_for
        # None means per-request resolution from capability config (the
        # production path); an instance is a test/assembly double.
        self._secret_fetch = secret_fetch
        self._coa = coa_port or UdpCoaPort()

    @property
    def manifest(self) -> ConnectorManifest:
        return MANIFEST

    @property
    def historical_manifests(self) -> tuple[ConnectorManifest, ...]:
        return ()

    @property
    def modes(self) -> frozenset[ConnectorMode]:
        return frozenset({ConnectorMode.PROVISION, ConnectorMode.POLL})

    def provisioning_handler_for(self, capability_id: str) -> ProvisioningHandler:
        self.manifest.require_declares(capability_id)
        return FreeRadiusProvisioningHandler(
            db_factory=self._db_factory,
            secret_fetch=self._secret_fetch,
            coa_port=self._coa,
        )

    def poll_handler_for(self, capability_id: str) -> PollHandler:
        self.manifest.require_declares(capability_id)
        return FreeRadiusPollHandler(db_factory=self._db_factory)

    def validate_connection(
        self, *, config: dict[str, object], secrets: dict[str, object]
    ) -> tuple[Diagnostic, ...]:
        string_secrets = {
            name: value for name, value in secrets.items() if isinstance(value, str)
        }
        try:
            self._db_factory(config, string_secrets).ping()
        except Exception:
            return (Diagnostic(ok=False, code="target_unreachable"),)
        return (Diagnostic(ok=True, code="target_reachable"),)


PLUGIN = FreeRadiusConnector()

__all__ = [
    "CONNECTOR_KEY",
    "DISCONNECT_CONTRACT_DIGEST",
    "FREERADIUS_DSN",
    "MANIFEST",
    "OBSERVATION_CAPABILITY",
    "OBSERVATION_CONTRACT_DIGEST",
    "PLUGIN",
    "PROJECTION_CONTRACT_DIGEST",
    "SECRET_FETCH_TOKEN",
    "FreeRadiusConnector",
]
