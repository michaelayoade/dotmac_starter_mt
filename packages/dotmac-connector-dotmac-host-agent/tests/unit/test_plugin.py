from __future__ import annotations

import json
from collections.abc import Mapping

import pytest
from dotmac_connector_dotmac_host_agent import (
    ACTIVATION_GATES,
    MANIFEST,
    DotmacHostAgentConnector,
    HostAgentProtocolError,
    HostAgentRequest,
    HostAgentResponse,
)
from dotmac_integration.spi import (
    ConnectorMode,
    ProvisionApplyRequest,
    ProvisionPlanRequest,
    ProvisionResultStatus,
    ProvisionStep,
    verify_plugin_modes,
)
from dotmac_managed_host_contracts import (
    BACKUP_RESTORE_LIFECYCLE,
    CAPABILITY_CONTRACTS,
    DEPLOYMENT_BUNDLE_LIFECYCLE,
    HEALTH_PROBE_LIFECYCLE,
)


class _Transport:
    def __init__(self, responses: list[HostAgentResponse]) -> None:
        self.responses = responses
        self.requests: list[HostAgentRequest] = []

    def request(self, request: HostAgentRequest) -> HostAgentResponse:
        self.requests.append(request)
        return self.responses.pop(0)


def _response(document: Mapping[str, object], status: int = 200) -> HostAgentResponse:
    return HostAgentResponse(status, json.dumps(dict(document)).encode())


def _schema_digests(capability_id: str) -> list[str]:
    declaration = MANIFEST.require_declares(capability_id)
    return sorted(document.digest for document in declaration.schema_documents)


def _activation(
    capability_id: str, evidence: Mapping[str, object]
) -> HostAgentResponse:
    declaration = MANIFEST.require_declares(capability_id)
    assert declaration.contract_snapshot is not None
    return _response(
        {
            "agent_identity_ref": "host-agent/seabone-1",
            "capability_id": capability_id,
            "contract_digest": declaration.contract_snapshot.digest,
            "evidence": dict(evidence),
            "protocol_version": 1,
            "schema_digests": _schema_digests(capability_id),
        }
    )


def _health_activation() -> HostAgentResponse:
    return _activation(
        HEALTH_PROBE_LIFECYCLE.capability_id,
        {
            "max_response_bytes": 1_048_576,
            "max_timeout_seconds": 300,
            "probe_kinds": [
                "http_roundtrip",
                "liveness",
                "readiness",
                "service",
            ],
            "response_digest_algorithm": "sha256",
        },
    )


def _bundle_activation(catalogue_ref: str) -> HostAgentResponse:
    return _activation(
        DEPLOYMENT_BUNDLE_LIFECYCLE.capability_id,
        {
            "bundle_catalogue_digest": catalogue_ref,
            "bundle_catalogue_ref": catalogue_ref,
            "bundle_catalogue_signature_valid": True,
            "bundle_operation_version": 1,
        },
    )


def _config(**extra: object) -> dict[str, object]:
    return {
        "agent_endpoint": "https://agent.seabone.test:8443",
        "agent_identity_ref": "host-agent/seabone-1",
        **extra,
    }


def _secrets() -> dict[str, str]:
    return {"agent_secret_ref": "held-mtls-material"}


def _health_target() -> dict[str, object]:
    return {
        "expected_response_digest": "sha256:" + "1" * 64,
        "host_ref": "seabone-1",
        "probe_kind": "readiness",
        "probe_ref": "mailcow/readiness",
        "timeout_seconds": 30,
    }


def _step(capability_id: str, target: Mapping[str, object]) -> ProvisionStep:
    return ProvisionStep(
        step_key="host-operation",
        endpoint_code=capability_id,
        depends_on=(),
        input=target,
    )


def test_manifest_embeds_all_three_exact_contracts_and_schemas() -> None:
    connector = DotmacHostAgentConnector(_Transport([]))

    assert MANIFEST.capability_ids == {
        snapshot.capability_id for snapshot in CAPABILITY_CONTRACTS
    }
    assert connector.modes == {ConnectorMode.PROVISION}
    verify_plugin_modes(connector)
    for snapshot in CAPABILITY_CONTRACTS:
        declaration = MANIFEST.require_declares(snapshot.capability_id)
        assert declaration.contract_snapshot == snapshot
        assert len(declaration.schema_documents) == 8


def test_exact_owner_configuration_can_pass_integration_activation_gate() -> None:
    """Endpoints have one declaration and remain values in connector config."""

    for snapshot in CAPABILITY_CONTRACTS:
        field_codes = {field.field_code for field in snapshot.config_fields}
        endpoint_codes = {
            endpoint.endpoint_code for endpoint in snapshot.endpoint_requirements
        }
        assert not (field_codes & endpoint_codes)


def test_each_capability_has_an_exact_named_activation_gate() -> None:
    assert set(ACTIVATION_GATES) == MANIFEST.capability_ids
    for capability_id, gate in ACTIVATION_GATES.items():
        declaration = MANIFEST.require_declares(capability_id)
        assert declaration.contract_snapshot is not None
        assert gate.contract_digest == declaration.contract_snapshot.digest
        assert gate.operation_schema_digests == tuple(_schema_digests(capability_id))
        assert gate.code and gate.requirement


def test_health_plan_uses_closed_route_and_preserves_signed_step() -> None:
    evidence = {
        "changes": ["run closed readiness probe"],
        "desired_state_digest": "sha256:" + "2" * 64,
        "host_ref": "seabone-1",
        "probe_ref": "mailcow/readiness",
    }
    transport = _Transport(
        [
            _health_activation(),
            _response(
                {
                    "capability_id": HEALTH_PROBE_LIFECYCLE.capability_id,
                    "evidence": evidence,
                    "operation": "plan",
                    "protocol_version": 1,
                }
            ),
        ]
    )
    connector = DotmacHostAgentConnector(transport)
    handler = connector.provisioning_handler_for(HEALTH_PROBE_LIFECYCLE.capability_id)
    step = _step(HEALTH_PROBE_LIFECYCLE.capability_id, _health_target())
    request = ProvisionPlanRequest(
        capability_id=HEALTH_PROBE_LIFECYCLE.capability_id,
        command_id="cmd-1",
        plan_hash="sha256:" + "3" * 64,
        steps=(step,),
        config=_config(),
        secrets=_secrets(),
    )

    result = handler.plan(request)

    assert result.steps == request.steps
    assert result.plan_hash == request.plan_hash
    assert result.evidence == evidence
    assert [request.path for request in transport.requests] == [
        "/v1/capabilities/host.health-probe.lifecycle.v1",
        "/v1/provision/host.health-probe.lifecycle.v1/plan",
    ]
    sent = transport.requests[1].document
    assert sent is not None
    assert set(sent) == {"capability_id", "operation", "protocol_version", "target"}
    assert sent["target"] == _health_target()


def test_bundle_plan_refuses_non_content_addressed_catalogue() -> None:
    transport = _Transport([_bundle_activation("catalogue/current")])
    connector = DotmacHostAgentConnector(transport)
    handler = connector.provisioning_handler_for(
        DEPLOYMENT_BUNDLE_LIFECYCLE.capability_id
    )
    target = {
        "artifact_digest": "sha256:" + "1" * 64,
        "bundle_operation_code": "install",
        "bundle_operation_version": 1,
        "bundle_ref": "mailcow",
        "configuration_digest": "sha256:" + "2" * 64,
        "desired_bundle_version": "2026.08.1",
        "host_ref": "seabone-1",
    }
    request = ProvisionPlanRequest(
        capability_id=DEPLOYMENT_BUNDLE_LIFECYCLE.capability_id,
        command_id="cmd-1",
        plan_hash="sha256:" + "3" * 64,
        steps=(_step(DEPLOYMENT_BUNDLE_LIFECYCLE.capability_id, target),),
        config=_config(bundle_catalogue_ref="catalogue/current"),
        secrets=_secrets(),
    )

    with pytest.raises(HostAgentProtocolError) as exc:
        handler.plan(request)

    assert exc.value.code == "bundle_catalogue_trust_unproven"


def test_health_apply_accepts_only_exact_public_evidence() -> None:
    evidence = {
        "health_state": "healthy",
        "host_ref": "seabone-1",
        "latency_milliseconds": 42,
        "observed_at": "2026-08-17T12:00:00Z",
        "probe_kind": "readiness",
        "probe_ref": "mailcow/readiness",
        "response_digest": "sha256:" + "4" * 64,
    }
    transport = _Transport(
        [
            _health_activation(),
            _response(
                {
                    "capability_id": HEALTH_PROBE_LIFECYCLE.capability_id,
                    "error_code": None,
                    "evidence": evidence,
                    "operation": "apply",
                    "outcome": "succeeded",
                    "protocol_version": 1,
                    "provider_operation_ref": "health-op-1",
                }
            ),
        ]
    )
    connector = DotmacHostAgentConnector(transport)
    handler = connector.provisioning_handler_for(HEALTH_PROBE_LIFECYCLE.capability_id)
    result = handler.apply(
        ProvisionApplyRequest(
            capability_id=HEALTH_PROBE_LIFECYCLE.capability_id,
            command_id="cmd-1",
            operation_ref="operation-1",
            plan_hash="sha256:" + "3" * 64,
            step=_step(HEALTH_PROBE_LIFECYCLE.capability_id, _health_target()),
            config=_config(),
            secrets=_secrets(),
            idempotency_key="idem-1",
        )
    )

    assert result.status is ProvisionResultStatus.SUCCEEDED
    assert result.evidence == evidence
    sent = transport.requests[1].document
    assert sent is not None
    assert set(sent) == {
        "capability_id",
        "idempotency_key",
        "operation",
        "operation_ref",
        "protocol_version",
        "target",
    }
    assert not ({"command_id", "plan_hash", "secret"} & set(sent))


def test_invalid_success_document_after_apply_is_ambiguous() -> None:
    transport = _Transport(
        [
            _health_activation(),
            _response({"unexpected": "document"}),
        ]
    )
    connector = DotmacHostAgentConnector(transport)
    handler = connector.provisioning_handler_for(HEALTH_PROBE_LIFECYCLE.capability_id)
    result = handler.apply(
        ProvisionApplyRequest(
            capability_id=HEALTH_PROBE_LIFECYCLE.capability_id,
            command_id="cmd-1",
            operation_ref="operation-1",
            plan_hash="sha256:" + "3" * 64,
            step=_step(HEALTH_PROBE_LIFECYCLE.capability_id, _health_target()),
            config=_config(),
            secrets=_secrets(),
            idempotency_key="idem-1",
        )
    )

    assert result.status is ProvisionResultStatus.AMBIGUOUS
    assert result.error_code == "agent_outcome_unknown"


def test_backup_binding_requires_all_object_semantics() -> None:
    incomplete = _activation(
        BACKUP_RESTORE_LIFECYCLE.capability_id,
        {
            "backup_storage_ref": "backup/seabone",
            "content_digest_algorithm": "sha256",
            "immutable_version_refs": True,
            "object_lock_enabled": False,
            "restore_by_exact_version": True,
        },
    )
    connector = DotmacHostAgentConnector(_Transport([incomplete]))
    validation_secrets: dict[str, object] = {"agent_secret_ref": "held-mtls-material"}

    diagnostics = connector.validate_connection(
        config=_config(backup_storage_ref="backup/seabone"),
        secrets=validation_secrets,
    )

    assert any(
        diagnostic.code == "backup_object_semantics_unproven" and not diagnostic.ok
        for diagnostic in diagnostics
    )
