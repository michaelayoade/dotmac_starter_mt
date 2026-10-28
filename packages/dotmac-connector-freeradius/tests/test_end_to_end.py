"""End to end: durable engine -> connector -> SQL target, no fakes in between
except the credential owner's fetch and the NAS.

This is the composition the suite cutover will run: Sub's plan is submitted
to the durable provisioning engine, the engine claims and invokes THIS
connector, the connector fetches the secret once, writes the incumbent's
rows into a real database, and the operation settles succeeded.
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from dotmac_connector_freeradius.plugin import FreeRadiusConnector
from dotmac_connector_freeradius.ports import CoaOutcome
from dotmac_integration import (
    CapabilityBinding,
    CapabilityContract,
    CapabilityOwner,
    CapabilityRegistry,
    ConnectorConfigRevision,
    ConnectorInstallation,
    ConnectorRegistry,
    SchemaGrace,
    add_binding,
    create_draft,
    enable,
    put_config_revision,
    set_binding_enabled,
)
from dotmac_integration import module as integration_module
from dotmac_integration.provisioning import (
    ProvisionOperation,
    ProvisionStepAttempt,
    invoke_provision,
    prepare_provision,
    settle_provision,
    submit_provision_operation,
)
from dotmac_integration.spi import ProvisionStep
from dotmac_kernel.audit_actions import AuditActionRegistry, install_audit_actions
from dotmac_kernel.models_platform import PlatformAdmin, PlatformAuditEvent
from sqlalchemy.orm import Session

HELD = "held-test-material"
GRACE = SchemaGrace(
    reason="e2e fixtures use Sub's published schemas via the contract below",
    retire_after=date(2099, 12, 31),
    tracked_by="packages/dotmac-connector-freeradius/tests/test_end_to_end.py",
)

STEP_INPUT = {
    "subject_ref": "svc-1",
    "username": "user@dotmac",
    "desired_state": "enabled",
    "policy_code": "active",
    "policy_version": "v1",
    "attributes": [{"key": "framed_ipv4_address", "value": "10.0.0.5"}],
    "credential_id": "cred-1",
    "secret_version": 3,
    "projection_request_ref": "proj-1",
    "expected_fingerprint": "a" * 64,
    "previous_username": None,
    "valid_until": None,
}


class _Fetch:
    def __init__(self) -> None:
        self.calls = 0

    def fetch_secret(
        self, *, credential_id, secret_version, projection_request_ref, operation_ref
    ):
        self.calls += 1
        assert (credential_id, secret_version, projection_request_ref) == (
            "cred-1",
            3,
            "proj-1",
        )
        return HELD


class _Coa:
    def disconnect(self, **kwargs):
        return CoaOutcome(outcome="acknowledged")


def _schema(extra: dict | None = None) -> dict:
    schema = {
        "type": "object",
        "properties": {"subject_ref": {"type": "string"}},
        "required": ["subject_ref"],
        "additionalProperties": True,
    }
    return schema


def test_engine_drives_connector_into_the_database(tmp_path) -> None:
    install_audit_actions(AuditActionRegistry.from_manifests([integration_module]))
    radius_engine = sa.create_engine(f"sqlite:///{tmp_path}/radius.db")
    with radius_engine.begin() as connection:
        for statement in (
            "CREATE TABLE radcheck "
            "(username TEXT, attribute TEXT, op TEXT, value TEXT)",
            "CREATE TABLE radreply "
            "(username TEXT, attribute TEXT, op TEXT, value TEXT)",
            "CREATE TABLE radusergroup "
            "(username TEXT, groupname TEXT, priority INTEGER)",
            "CREATE TABLE radacct (radacctid INTEGER, username TEXT, "
            "nasipaddress TEXT, acctsessionid TEXT, acctstatustype TEXT, "
            "acctinputoctets INTEGER, acctoutputoctets INTEGER, "
            "acctsessiontime INTEGER, acctupdatetime TEXT, "
            "acctstarttime TEXT, acctstoptime TEXT)",
            "CREATE TABLE radpostauth (id INTEGER, username TEXT, "
            "reply TEXT, authdate TEXT, nasipaddress TEXT)",
            "CREATE TABLE nas (nasname TEXT, shortname TEXT, secret TEXT)",
        ):
            connection.execute(sa.text(statement))

    fetch = _Fetch()
    plugin = FreeRadiusConnector(secret_fetch=fetch, coa_port=_Coa())
    registry = ConnectorRegistry((plugin,))
    contracts = CapabilityRegistry(
        tuple(
            CapabilityContract(
                capability_id=capability_id,
                owner=CapabilityOwner(application="sub", module="network"),
                summary="e2e",
                command_schema=_schema(),
                result_schema=_schema(),
                observation_schema=_schema(),
            )
            for capability_id in (
                "network.access_projection.v1",
                "network.access_observation.v1",
                "network.session_disconnect.v1",
            )
        )
    )

    engine = sa.create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_intg": None}},
    )
    for model in (
        ConnectorInstallation,
        ConnectorConfigRevision,
        CapabilityBinding,
        ProvisionOperation,
        ProvisionStepAttempt,
        PlatformAdmin,
        PlatformAuditEvent,
    ):
        model.__table__.create(engine)

    dsn = f"sqlite:///{tmp_path}/radius.db"
    with Session(engine) as db:
        installation = create_draft(
            db, registry=registry, connector_key="freeradius", name="primary"
        )
        binding = add_binding(
            db,
            installation,
            registry=registry,
            capability_id="network.access_projection.v1",
        )
        put_config_revision(db, installation, registry=registry, config={})
        enable(
            db,
            installation,
            registry=registry,
            secrets={"freeradius_dsn": dsn},
        )
        set_binding_enabled(db, installation, binding, registry=registry, enabled=True)

        operation, is_new = submit_provision_operation(
            db,
            capability_binding_id=binding.id,
            command_id="cmd-e2e",
            steps=(
                ProvisionStep(
                    step_key="project",
                    endpoint_code="radius.projection.upsert",
                    input=STEP_INPUT,
                ),
            ),
            registry=registry,
            contracts=contracts,
        )
        assert is_new
        for _ in range(2):  # plan, then apply
            prepared = prepare_provision(db, operation, registry=registry)
            assert prepared is not None
            result = invoke_provision(
                prepared,
                registry=registry,
                resolve_secrets=lambda refs: {"freeradius_dsn": dsn},
            )
            operation = settle_provision(db, prepared=prepared, result=result)
        assert operation.state == "succeeded"
        assert fetch.calls == 1

    with radius_engine.connect() as connection:
        rows = connection.execute(
            sa.text("SELECT attribute, value FROM radcheck ORDER BY attribute")
        ).all()
    assert ("Cleartext-Password", HELD) in rows
    assert ("Simultaneous-Use", "1") in rows
