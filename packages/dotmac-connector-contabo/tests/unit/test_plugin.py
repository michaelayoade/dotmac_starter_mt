from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime

import pytest
from dotmac_connector_contabo import (
    ACTIVATION_GATES,
    MANIFEST,
    ContaboActivationError,
    ContaboConnector,
    ContaboRequest,
    ContaboResponse,
    ContaboTransportError,
    FailureKind,
)
from dotmac_domains_contracts import DNS_AUTHORITATIVE
from dotmac_integration.spi import (
    ConnectorMode,
    ProvisionApplyRequest,
    ProvisionPlanRequest,
    ProvisionResultStatus,
    ProvisionStep,
    verify_plugin_modes,
)
from dotmac_managed_infrastructure_contracts import (
    FIREWALL_LIFECYCLE,
    INSTANCE_LIFECYCLE,
    NETWORK_LIFECYCLE,
    VOLUME_LIFECYCLE,
)
from jsonschema import Draft202012Validator


def _digest(value: object) -> str:
    payload = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class _Transport:
    def __init__(
        self, responses: list[ContaboResponse | ContaboTransportError]
    ) -> None:
        self.responses = responses
        self.requests: list[ContaboRequest] = []

    def request(self, request: ContaboRequest) -> ContaboResponse:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, ContaboTransportError):
            raise response
        return response


def _body(rows: list[Mapping[str, object]]) -> bytes:
    return json.dumps({"data": rows}).encode()


def _validate_output(operation_code: str, value: Mapping[str, object]) -> None:
    declaration = MANIFEST.require_declares(FIREWALL_LIFECYCLE.capability_id)
    operation = next(
        operation
        for operation in FIREWALL_LIFECYCLE.operations
        if operation.operation_code == operation_code
    )
    document = next(
        schema
        for schema in declaration.schema_documents
        if schema.schema_ref == operation.output_schema_ref
    )
    Draft202012Validator(json.loads(document.to_json_bytes())).validate(dict(value))


def _target(
    *,
    state: str = "enabled",
    rules: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    selected = (
        [
            {
                "description": "HTTPS",
                "destination_ports": [443],
                "direction": "ingress",
                "protocol": "tcp",
                "source_cidrs": ["0.0.0.0/0", "::/0"],
            }
        ]
        if rules is None
        else rules
    )
    normalized: list[dict[str, object]] = []
    for rule in selected:
        source_cidrs = rule["source_cidrs"]
        assert isinstance(source_cidrs, list)
        assert all(isinstance(cidr, str) for cidr in source_cidrs)
        normalized.append({**rule, "source_cidrs": sorted(source_cidrs)})
    return {
        "configuration_digest": "sha256:" + "1" * 64,
        "desired_lifecycle_state": state,
        "desired_rules_digest": _digest(normalized),
        "firewall_rules": selected,
        "resource_ref": "customer-a/web-firewall",
    }


def _step(target: Mapping[str, object]) -> ProvisionStep:
    return ProvisionStep(
        step_key="firewall",
        endpoint_code=FIREWALL_LIFECYCLE.capability_id,
        depends_on=(),
        input=target,
    )


def _config() -> dict[str, object]:
    return {
        "account_ref": "customer-a",
        "api_endpoint": "https://api.contabo.com",
        "region_code": "EU",
    }


def _secrets() -> dict[str, str]:
    return {"api_secret_ref": "held-json"}


def _row(*, status: str = "active") -> dict[str, object]:
    resource_hash = hashlib.sha256(b"customer-a/web-firewall").hexdigest()
    return {
        "description": "dotmac-ref-sha256:" + resource_hash,
        "firewallId": "b943b25a-c8b5-4570-9135-4bbaa7615b81",
        "name": "dm-" + resource_hash[:40],
        "rules": {
            "inbound": [
                {
                    "action": "accept",
                    "destPorts": ["443"],
                    "displayName": "HTTPS",
                    "protocol": "tcp",
                    "srcCidr": {"ipv4": ["0.0.0.0/0"], "ipv6": ["::/0"]},
                    "status": "active",
                }
            ]
        },
        "status": status,
    }


def test_manifest_is_exact_and_mode_shape_conforms() -> None:
    connector = ContaboConnector(_Transport([]))

    assert MANIFEST.capability_ids == {FIREWALL_LIFECYCLE.capability_id}
    assert connector.modes == {ConnectorMode.PROVISION}
    verify_plugin_modes(connector)
    declaration = MANIFEST.require_declares(FIREWALL_LIFECYCLE.capability_id)
    assert declaration.contract_snapshot == FIREWALL_LIFECYCLE
    assert len(declaration.schema_documents) == 8


def test_every_undeclared_exact_owner_contract_has_a_named_gate() -> None:
    expected = {
        DNS_AUTHORITATIVE.capability_id,
        INSTANCE_LIFECYCLE.capability_id,
        NETWORK_LIFECYCLE.capability_id,
        VOLUME_LIFECYCLE.capability_id,
    }

    assert set(ACTIVATION_GATES) == expected
    assert all(
        gate.code
        and gate.requirement
        and gate.contract_digest.startswith("sha256:")
        and len(gate.operation_schema_digests) == 8
        for gate in ACTIVATION_GATES.values()
    )
    connector = ContaboConnector(_Transport([]))
    for capability_id in expected:
        with pytest.raises(ContaboActivationError) as exc:
            connector.provisioning_handler_for(capability_id)
        assert exc.value.code == ACTIVATION_GATES[capability_id].code


def test_plan_refuses_provider_subset_that_owner_schema_permits() -> None:
    target = _target(
        rules=[
            {
                "destination_ports": [443],
                "direction": "egress",
                "protocol": "tcp",
                "source_cidrs": ["0.0.0.0/0"],
            }
        ]
    )
    connector = ContaboConnector(_Transport([]))
    handler = connector.provisioning_handler_for(FIREWALL_LIFECYCLE.capability_id)
    request = ProvisionPlanRequest(
        capability_id=FIREWALL_LIFECYCLE.capability_id,
        command_id="cmd-1",
        plan_hash="sha256:" + "2" * 64,
        steps=(_step(target),),
        config=_config(),
        secrets=_secrets(),
    )

    with pytest.raises(ContaboActivationError) as exc:
        handler.plan(request)

    assert exc.value.code == "firewall_egress_rules_unsupported"


def test_plan_preserves_the_signed_step_and_matches_exact_owner_output() -> None:
    target = _target()
    connector = ContaboConnector(_Transport([]))
    handler = connector.provisioning_handler_for(FIREWALL_LIFECYCLE.capability_id)
    request = ProvisionPlanRequest(
        capability_id=FIREWALL_LIFECYCLE.capability_id,
        command_id="cmd-1",
        plan_hash="sha256:" + "2" * 64,
        steps=(_step(target),),
        config=_config(),
        secrets=_secrets(),
    )

    result = handler.plan(request)

    assert result.plan_hash == request.plan_hash
    assert result.steps == request.steps
    _validate_output("plan", result.evidence)


def test_create_reconciles_by_deterministic_name_and_returns_public_evidence() -> None:
    target = _target()
    transport = _Transport(
        [
            ContaboResponse(200, _body([])),
            ContaboResponse(201, _body([_row()])),
            ContaboResponse(200, _body([_row()])),
        ]
    )
    connector = ContaboConnector(
        transport, clock=lambda: datetime(2026, 8, 17, 12, tzinfo=UTC)
    )
    handler = connector.provisioning_handler_for(FIREWALL_LIFECYCLE.capability_id)
    result = handler.apply(
        ProvisionApplyRequest(
            capability_id=FIREWALL_LIFECYCLE.capability_id,
            command_id="cmd-1",
            operation_ref="op-1",
            plan_hash="sha256:" + "2" * 64,
            step=_step(target),
            config=_config(),
            secrets=_secrets(),
            idempotency_key="idem-1",
        )
    )

    assert result.status is ProvisionResultStatus.SUCCEEDED
    assert result.provider_operation_ref is not None
    assert result.provider_operation_ref.startswith("name:dm-")
    assert result.evidence["lifecycle_state"] == "enabled"
    assert result.evidence["observed_at"] == "2026-08-17T12:00:00Z"
    assert result.evidence["rule_count"] == 1
    _validate_output("apply", result.evidence)
    assert [request.method for request in transport.requests] == ["GET", "POST", "GET"]
    assert transport.requests[1].document is not None
    assert set(transport.requests[1].document) == {
        "description",
        "name",
        "rules",
        "status",
    }


def test_unknown_create_outcome_is_ambiguous_and_carries_reconciliation_ref() -> None:
    target = _target()
    transport = _Transport(
        [
            ContaboResponse(200, _body([])),
            ContaboTransportError("provider_outcome_unknown", FailureKind.AMBIGUOUS),
        ]
    )
    connector = ContaboConnector(transport)
    handler = connector.provisioning_handler_for(FIREWALL_LIFECYCLE.capability_id)
    result = handler.apply(
        ProvisionApplyRequest(
            capability_id=FIREWALL_LIFECYCLE.capability_id,
            command_id="cmd-1",
            operation_ref="op-1",
            plan_hash="sha256:" + "2" * 64,
            step=_step(target),
            config=_config(),
            secrets=_secrets(),
            idempotency_key="idem-1",
        )
    )

    assert result.status is ProvisionResultStatus.AMBIGUOUS
    assert result.error_code == "provider_outcome_unknown"
    assert result.provider_operation_ref is not None
    assert result.provider_operation_ref.startswith("name:dm-")


def test_foreign_rule_shape_is_not_silently_projected() -> None:
    row = _row()
    rules = row["rules"]
    assert isinstance(rules, dict)
    inbound = rules["inbound"]
    assert isinstance(inbound, list)
    assert isinstance(inbound[0], dict)
    inbound[0]["destPorts"] = ["80-90"]
    transport = _Transport([ContaboResponse(200, _body([row]))])
    connector = ContaboConnector(transport)
    handler = connector.provisioning_handler_for(FIREWALL_LIFECYCLE.capability_id)
    result = handler.apply(
        ProvisionApplyRequest(
            capability_id=FIREWALL_LIFECYCLE.capability_id,
            command_id="cmd-1",
            operation_ref="op-1",
            plan_hash="sha256:" + "2" * 64,
            step=_step(_target()),
            config=_config(),
            secrets=_secrets(),
            idempotency_key="idem-1",
        )
    )

    assert result.status is ProvisionResultStatus.TERMINAL
    assert result.error_code == "provider_rule_shape_unsupported"
