"""Stateless Contabo translation at the exact owner-contract boundary."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Final, cast

from dotmac_domains_contracts import DNS_AUTHORITATIVE
from dotmac_integration.spi import (
    CapabilityContractSnapshot,
    CapabilityDeclaration,
    CapabilitySchemaDocument,
    ConnectorManifest,
    ConnectorMode,
    Diagnostic,
    ProvisionApplyRequest,
    ProvisionCancelRequest,
    ProvisioningHandler,
    ProvisioningResult,
    ProvisionObserveRequest,
    ProvisionPlanRequest,
    ProvisionPlanResult,
    ProvisionResultStatus,
    SpiRange,
)
from dotmac_managed_infrastructure_contracts import (
    CAPABILITY_SCHEMAS as INFRASTRUCTURE_SCHEMAS,
)
from dotmac_managed_infrastructure_contracts import (
    FIREWALL_LIFECYCLE,
    INSTANCE_LIFECYCLE,
    NETWORK_LIFECYCLE,
    VOLUME_LIFECYCLE,
)

from .transport import (
    ContaboRequest,
    ContaboResponse,
    ContaboTransport,
    ContaboTransportError,
    FailureKind,
    HttpxContaboTransport,
    normalize_api_endpoint,
)

CONNECTOR_KEY: Final = "contabo"
VERSION: Final = "0.1.0a1"
FIREWALL_CAPABILITY: Final = FIREWALL_LIFECYCLE.capability_id
_API_MATERIAL_FIELD: Final = "api_secret_ref"
_REF_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}$")
_FIREWALL_ID_RE: Final[re.Pattern[str]] = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-" r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


@dataclass(frozen=True, slots=True)
class CapabilityActivationGate:
    """A named reason an owner capability is not honestly declared yet."""

    capability_id: str
    contract_digest: str
    code: str
    operation_schema_digests: tuple[str, ...]
    requirement: str


def _activation_gate(
    snapshot: CapabilityContractSnapshot,
    *,
    code: str,
    requirement: str,
) -> CapabilityActivationGate:
    schema_digests = {
        digest
        for operation in snapshot.operations
        for digest in (
            operation.input_schema_digest,
            operation.output_schema_digest,
        )
    }
    return CapabilityActivationGate(
        capability_id=snapshot.capability_id,
        contract_digest=snapshot.digest,
        code=code,
        operation_schema_digests=tuple(sorted(schema_digests)),
        requirement=requirement,
    )


ACTIVATION_GATES: Final[Mapping[str, CapabilityActivationGate]] = MappingProxyType(
    {
        DNS_AUTHORITATIVE.capability_id: _activation_gate(
            DNS_AUTHORITATIVE,
            code="dns_observation_state_unavailable",
            requirement=(
                "A stateless observe call has only resource identity; Contabo records "
                "do "
                "not retain owner requirement_kind/resource_ref, and the API does not "
                "provide the owner contract's TLS or assigned-nameserver evidence."
            ),
        ),
        INSTANCE_LIFECYCLE.capability_id: _activation_gate(
            INSTANCE_LIFECYCLE,
            code="instance_product_mapping_unavailable",
            requirement=(
                "Contabo create requires provider imageId/productId and secret IDs, "
                "while the owner target intentionally exposes only artifact and "
                "instance-type "
                "identities; no approved provider mapping is installed."
            ),
        ),
        NETWORK_LIFECYCLE.capability_id: _activation_gate(
            NETWORK_LIFECYCLE,
            code="network_cidr_control_unavailable",
            requirement=(
                "Contabo allocates the private-network CIDR and accepts no "
                "DNS-resolver "
                "configuration, but the owner contract requires both as desired state."
            ),
        ),
        VOLUME_LIFECYCLE.capability_id: _activation_gate(
            VOLUME_LIFECYCLE,
            code="block_volume_api_unavailable",
            requirement=(
                "The current official API exposes object storage and instance-local "
                "disk addons, not an attachable block-volume lifecycle matching this "
                "contract."
            ),
        ),
    }
)


class ContaboActivationError(RuntimeError):
    """Stable refusal for a capability or valid target Contabo cannot prove."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _ConnectorRefused(RuntimeError):
    def __init__(self, result: ProvisioningResult) -> None:
        self.result = result
        super().__init__(result.error_code or result.status.value)


def _declaration(
    snapshot: CapabilityContractSnapshot,
    documents: Sequence[CapabilitySchemaDocument],
) -> CapabilityDeclaration:
    expected = {
        (schema_ref, schema_digest)
        for operation in snapshot.operations
        for schema_ref, schema_digest in (
            (operation.input_schema_ref, operation.input_schema_digest),
            (operation.output_schema_ref, operation.output_schema_digest),
        )
    }
    schemas = tuple(
        document
        for document in documents
        if (document.schema_ref, document.digest) in expected
    )
    return CapabilityDeclaration(
        capability_id=snapshot.capability_id,
        contract_snapshot=snapshot,
        schema_documents=schemas,
    )


MANIFEST: Final = ConnectorManifest(
    connector_key=CONNECTOR_KEY,
    version=VERSION,
    spi_range=SpiRange.parse(">=1.2,<2.0"),
    capabilities=(_declaration(FIREWALL_LIFECYCLE, INFRASTRUCTURE_SCHEMAS),),
)


def activation_gate_for(capability_id: str) -> CapabilityActivationGate | None:
    """Return the named, exact-contract gate for an undeclared family."""

    return ACTIVATION_GATES.get(capability_id)


def _result(
    status: ProvisionResultStatus,
    *,
    error_code: str | None = None,
    provider_operation_ref: str | None = None,
    evidence: Mapping[str, object] | None = None,
) -> ProvisioningResult:
    return ProvisioningResult(
        status=status,
        provider_operation_ref=provider_operation_ref,
        evidence={} if evidence is None else evidence,
        error_code=error_code,
        error_detail=None,
    )


def _terminal(code: str) -> _ConnectorRefused:
    return _ConnectorRefused(_result(ProvisionResultStatus.TERMINAL, error_code=code))


def _canonical_digest(value: object) -> str:
    try:
        body = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise _terminal("target_invalid") from None
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _string(values: Mapping[str, object], field: str) -> str:
    value = values.get(field)
    if not isinstance(value, str) or not value:
        raise _terminal("target_invalid")
    return value


def _resource_ref(target: Mapping[str, object]) -> str:
    value = _string(target, "resource_ref")
    if _REF_RE.fullmatch(value) is None:
        raise _terminal("target_invalid")
    return value


def _require_capability(actual: str) -> None:
    if actual != FIREWALL_CAPABILITY:
        gate = activation_gate_for(actual)
        raise ContaboActivationError(
            gate.code if gate is not None else "capability_not_declared"
        )


def _one_plan_step(request: ProvisionPlanRequest) -> Mapping[str, object]:
    _require_capability(request.capability_id)
    if len(request.steps) != 1:
        raise ContaboActivationError("single_step_required")
    step = request.steps[0]
    if step.endpoint_code != FIREWALL_CAPABILITY or step.depends_on:
        raise ContaboActivationError("step_contract_invalid")
    return step.input


def _require_apply_step(request: ProvisionApplyRequest) -> None:
    _require_capability(request.capability_id)
    if (
        request.step.endpoint_code != FIREWALL_CAPABILITY
        or request.step.depends_on
        or not request.step.step_key
    ):
        raise _terminal("step_contract_invalid")


def _endpoint(config: Mapping[str, object]) -> str:
    try:
        return normalize_api_endpoint(config.get("api_endpoint"))
    except ValueError:
        raise _terminal("api_endpoint_invalid") from None


def _held_material(secrets: Mapping[str, object]) -> str:
    value = secrets.get(_API_MATERIAL_FIELD)
    if not isinstance(value, str) or not value:
        raise _terminal("api_material_unavailable")
    return value


def _request_id(material: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"dotmac-contabo:{material}"))


def _transport_result(error: ContaboTransportError) -> ProvisioningResult:
    status = {
        FailureKind.AMBIGUOUS: ProvisionResultStatus.AMBIGUOUS,
        FailureKind.RETRYABLE: ProvisionResultStatus.RETRYABLE,
        FailureKind.TERMINAL: ProvisionResultStatus.TERMINAL,
    }[error.kind]
    return _result(status, error_code=error.code)


def _http_refusal(
    response: ContaboResponse,
    *,
    mutating: bool,
    provider_operation_ref: str | None = None,
) -> _ConnectorRefused:
    status = response.status_code
    if 300 <= status < 400:
        result = _result(
            ProvisionResultStatus.TERMINAL,
            error_code="provider_redirect_refused",
            provider_operation_ref=provider_operation_ref,
        )
    elif status == 401:
        result = _result(
            ProvisionResultStatus.TERMINAL,
            error_code="provider_authentication_refused",
            provider_operation_ref=provider_operation_ref,
        )
    elif status == 403:
        result = _result(
            ProvisionResultStatus.TERMINAL,
            error_code="provider_authorization_refused",
            provider_operation_ref=provider_operation_ref,
        )
    elif status == 404:
        result = _result(
            ProvisionResultStatus.NOT_FOUND,
            error_code="provider_resource_not_found",
            provider_operation_ref=provider_operation_ref,
        )
    elif status == 429:
        result = _result(
            ProvisionResultStatus.RETRYABLE,
            error_code="provider_rate_limited",
            provider_operation_ref=provider_operation_ref,
        )
    elif status in {408, 409} or status >= 500:
        result = _result(
            (
                ProvisionResultStatus.AMBIGUOUS
                if mutating
                else ProvisionResultStatus.RETRYABLE
            ),
            error_code=(
                "provider_outcome_unknown" if mutating else "provider_unavailable"
            ),
            provider_operation_ref=provider_operation_ref,
        )
    else:
        result = _result(
            ProvisionResultStatus.TERMINAL,
            error_code="provider_request_refused",
            provider_operation_ref=provider_operation_ref,
        )
    return _ConnectorRefused(result)


def _send(
    transport: ContaboTransport,
    request: ContaboRequest,
    *,
    expected: frozenset[int],
    provider_operation_ref: str | None = None,
) -> ContaboResponse:
    try:
        response = transport.request(request)
    except ContaboTransportError as exc:
        result = _transport_result(exc)
        raise _ConnectorRefused(
            _result(
                result.status,
                error_code=result.error_code,
                provider_operation_ref=provider_operation_ref,
            )
        ) from None
    if response.status_code not in expected:
        raise _http_refusal(
            response,
            mutating=request.mutating,
            provider_operation_ref=provider_operation_ref,
        )
    return response


def _rows(response: ContaboResponse) -> tuple[Mapping[str, object], ...]:
    try:
        value = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _terminal("provider_response_invalid") from None
    data = value.get("data") if isinstance(value, dict) else None
    if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
        raise _terminal("provider_response_invalid")
    return tuple(cast(Mapping[str, object], row) for row in data)


def _firewall_name(resource_ref: str) -> str:
    return "dm-" + hashlib.sha256(resource_ref.encode("utf-8")).hexdigest()[:40]


def _firewall_marker(resource_ref: str) -> str:
    return (
        "dotmac-ref-sha256:" + hashlib.sha256(resource_ref.encode("utf-8")).hexdigest()
    )


def _provider_ref(resource_ref: str) -> str:
    return "name:" + _firewall_name(resource_ref)


def _provider_id(row: Mapping[str, object]) -> str:
    value = row.get("firewallId")
    if not isinstance(value, str) or _FIREWALL_ID_RE.fullmatch(value) is None:
        raise _terminal("provider_response_invalid")
    return value


def _provider_rule(rule: Mapping[str, object]) -> Mapping[str, object]:
    direction = rule.get("direction")
    protocol = rule.get("protocol")
    ports = rule.get("destination_ports")
    cidrs = rule.get("source_cidrs")
    description = rule.get("description")
    if direction != "ingress":
        raise _terminal("firewall_egress_rules_unsupported")
    if protocol == "any":
        raise _terminal("firewall_any_protocol_unsupported")
    if protocol not in {"icmp", "tcp", "udp"}:
        raise _terminal("target_invalid")
    if not isinstance(ports, list) or not all(
        type(port) is int and 1 <= port <= 65535 for port in ports
    ):
        raise _terminal("target_invalid")
    if protocol == "icmp" and ports:
        raise _terminal("firewall_icmp_ports_unsupported")
    if not isinstance(cidrs, list) or not all(isinstance(cidr, str) for cidr in cidrs):
        raise _terminal("target_invalid")
    ipv4: list[str] = []
    ipv6: list[str] = []
    for cidr in cidrs:
        try:
            network = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            raise _terminal("target_invalid") from None
        (ipv4 if network.version == 4 else ipv6).append(str(network))
    if description is not None and not isinstance(description, str):
        raise _terminal("target_invalid")
    return {
        "action": "accept",
        "destPorts": [str(port) for port in ports],
        "displayName": description or "",
        "protocol": protocol,
        "srcCidr": {"ipv4": sorted(ipv4), "ipv6": sorted(ipv6)},
        "status": "active",
    }


def _owner_rule(rule: Mapping[str, object]) -> Mapping[str, object]:
    if rule.get("action") != "accept" or rule.get("status") != "active":
        raise _terminal("provider_rule_shape_unsupported")
    protocol = rule.get("protocol")
    if protocol not in {"icmp", "tcp", "udp"}:
        raise _terminal("provider_rule_shape_unsupported")
    ports = rule.get("destPorts")
    if not isinstance(ports, list):
        raise _terminal("provider_response_invalid")
    destination_ports: list[int] = []
    for value in ports:
        if not isinstance(value, str) or not value.isdigit():
            raise _terminal("provider_rule_shape_unsupported")
        port = int(value)
        if not 1 <= port <= 65535:
            raise _terminal("provider_response_invalid")
        destination_ports.append(port)
    source = rule.get("srcCidr")
    if not isinstance(source, Mapping):
        raise _terminal("provider_response_invalid")
    source_cidrs: list[str] = []
    for family in ("ipv4", "ipv6"):
        values = source.get(family, [])
        if not isinstance(values, list) or not all(
            isinstance(value, str) for value in values
        ):
            raise _terminal("provider_response_invalid")
        for value in values:
            if value == "AnyIPv4":
                source_cidrs.append("0.0.0.0/0")
                continue
            if value == "AnyIPv6":
                source_cidrs.append("::/0")
                continue
            try:
                source_cidrs.append(str(ipaddress.ip_network(value, strict=False)))
            except ValueError:
                raise _terminal("provider_response_invalid") from None
    result: dict[str, object] = {
        "destination_ports": sorted(set(destination_ports)),
        "direction": "ingress",
        "protocol": protocol,
        "source_cidrs": sorted(set(source_cidrs)),
    }
    display = rule.get("displayName")
    if isinstance(display, str) and display:
        result["description"] = display
    return result


def _normalized_owner_rules(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list) or not all(
        isinstance(rule, Mapping) for rule in value
    ):
        raise _terminal("target_invalid")
    normalized = tuple(
        _owner_rule(_provider_rule(cast(Mapping[str, object], rule))) for rule in value
    )
    return tuple(sorted(normalized, key=lambda rule: json.dumps(rule, sort_keys=True)))


def _rules_digest(rules: Sequence[Mapping[str, object]]) -> str:
    return _canonical_digest(list(rules))


def _desired_rules(target: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    _string(target, "desired_rules_digest")
    return _normalized_owner_rules(target.get("firewall_rules"))


def _provider_rules(rules: Sequence[Mapping[str, object]]) -> Mapping[str, object]:
    return {"inbound": [_provider_rule(rule) for rule in rules]}


def _observed_rules(row: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    rules = row.get("rules")
    if not isinstance(rules, Mapping) or set(rules) != {"inbound"}:
        raise _terminal("provider_response_invalid")
    inbound = rules.get("inbound")
    if not isinstance(inbound, list) or not all(
        isinstance(rule, Mapping) for rule in inbound
    ):
        raise _terminal("provider_response_invalid")
    normalized = tuple(
        _owner_rule(cast(Mapping[str, object], rule)) for rule in inbound
    )
    return tuple(sorted(normalized, key=lambda rule: json.dumps(rule, sort_keys=True)))


def _desired_state(target: Mapping[str, object]) -> str:
    value = target.get("desired_lifecycle_state")
    if value not in {"absent", "disabled", "enabled"}:
        raise _terminal("target_invalid")
    return value


class _FirewallHandler:
    __slots__ = ("_clock", "_transport")

    def __init__(
        self,
        transport: ContaboTransport,
        clock: Callable[[], datetime],
    ) -> None:
        self._transport = transport
        self._clock = clock

    def _request(
        self,
        request: ProvisionApplyRequest | ProvisionObserveRequest,
        *,
        method: str,
        path: str,
        query: Mapping[str, str] | None = None,
        document: Mapping[str, object] | None = None,
        mutating: bool = False,
        provider_operation_ref: str | None = None,
    ) -> ContaboResponse:
        return _send(
            self._transport,
            ContaboRequest(
                method=method,
                base_endpoint=_endpoint(request.config),
                path=path,
                held_material=_held_material(request.secrets),
                request_id=_request_id(
                    f"{request.command_id}:{request.operation_ref}:{method}:{path}"
                ),
                query={} if query is None else query,
                document=document,
                mutating=mutating,
            ),
            expected=(frozenset({200, 201, 204}) if mutating else frozenset({200})),
            provider_operation_ref=provider_operation_ref,
        )

    def _find(
        self,
        request: ProvisionApplyRequest | ProvisionObserveRequest,
        resource_ref: str,
    ) -> Mapping[str, object] | None:
        response = self._request(
            request,
            method="GET",
            path="/v1/firewalls",
            query={"name": _firewall_name(resource_ref), "size": "100"},
        )
        matches = tuple(
            row
            for row in _rows(response)
            if row.get("name") == _firewall_name(resource_ref)
        )
        if len(matches) > 1:
            raise _terminal("provider_identity_collision")
        if not matches:
            return None
        row = matches[0]
        if row.get("description") != _firewall_marker(resource_ref):
            raise _terminal("provider_identity_collision")
        return row

    def _evidence(
        self,
        *,
        resource_ref: str,
        row: Mapping[str, object] | None,
    ) -> Mapping[str, object]:
        if row is None:
            state = "absent"
            rules: tuple[Mapping[str, object], ...] = ()
            provider_resource_ref = _provider_ref(resource_ref)
        else:
            status = row.get("status")
            if status not in {"active", "inactive"}:
                raise _terminal("provider_state_unsupported")
            state = "enabled" if status == "active" else "disabled"
            rules = _observed_rules(row)
            provider_resource_ref = _provider_id(row)
        projection: dict[str, object] = {
            "lifecycle_state": state,
            "observed_rules_digest": _rules_digest(rules),
            "provider_resource_ref": provider_resource_ref,
            "resource_ref": resource_ref,
            "rule_count": len(rules),
        }
        projection["observed_configuration_digest"] = _canonical_digest(projection)
        observed_at = self._clock().astimezone(UTC).isoformat().replace("+00:00", "Z")
        return {**projection, "observed_at": observed_at}

    def plan(self, request: ProvisionPlanRequest) -> ProvisionPlanResult:
        target = _one_plan_step(request)
        try:
            _endpoint(request.config)
            _held_material(request.secrets)
            resource_ref = _resource_ref(target)
            state = _desired_state(target)
            _desired_rules(target)
        except _ConnectorRefused as exc:
            raise ContaboActivationError(
                exc.result.error_code or "target_invalid"
            ) from None
        changes = [
            "delete Contabo firewall if present"
            if state == "absent"
            else "reconcile Contabo inbound firewall rules"
        ]
        return ProvisionPlanResult(
            plan_hash=request.plan_hash,
            steps=request.steps,
            evidence={
                "changes": changes,
                "desired_state_digest": _canonical_digest(dict(target)),
                "resource_ref": resource_ref,
            },
        )

    def apply(self, request: ProvisionApplyRequest) -> ProvisioningResult:
        provider_ref: str | None = None
        mutated = False
        try:
            _require_apply_step(request)
            target = request.step.input
            resource_ref = _resource_ref(target)
            provider_ref = _provider_ref(resource_ref)
            state = _desired_state(target)
            desired_rules = _desired_rules(target)
            row = self._find(request, resource_ref)
            if state == "absent":
                if row is not None:
                    firewall_id = _provider_id(row)
                    self._request(
                        request,
                        method="DELETE",
                        path=f"/v1/firewalls/{firewall_id}",
                        mutating=True,
                        provider_operation_ref=provider_ref,
                    )
                    mutated = True
                return _result(
                    ProvisionResultStatus.SUCCEEDED,
                    provider_operation_ref=provider_ref,
                    evidence=self._evidence(resource_ref=resource_ref, row=None),
                )
            desired_status = "active" if state == "enabled" else "inactive"
            if row is None:
                self._request(
                    request,
                    method="POST",
                    path="/v1/firewalls",
                    document={
                        "description": _firewall_marker(resource_ref),
                        "name": _firewall_name(resource_ref),
                        "rules": _provider_rules(desired_rules),
                        "status": desired_status,
                    },
                    mutating=True,
                    provider_operation_ref=provider_ref,
                )
                mutated = True
            else:
                firewall_id = _provider_id(row)
                if row.get("status") != desired_status:
                    self._request(
                        request,
                        method="PATCH",
                        path=f"/v1/firewalls/{firewall_id}",
                        document={"status": desired_status},
                        mutating=True,
                        provider_operation_ref=provider_ref,
                    )
                    mutated = True
                if _observed_rules(row) != desired_rules:
                    self._request(
                        request,
                        method="PUT",
                        path=f"/v1/firewalls/{firewall_id}",
                        document={"rules": _provider_rules(desired_rules)},
                        mutating=True,
                        provider_operation_ref=provider_ref,
                    )
                    mutated = True
            observed = self._find(request, resource_ref)
            if observed is None:
                raise _terminal("provider_outcome_unknown")
            evidence = self._evidence(resource_ref=resource_ref, row=observed)
            if evidence["lifecycle_state"] != state or evidence[
                "observed_rules_digest"
            ] != _rules_digest(desired_rules):
                raise _terminal("provider_outcome_unknown")
            return _result(
                ProvisionResultStatus.SUCCEEDED,
                provider_operation_ref=provider_ref,
                evidence=evidence,
            )
        except _ConnectorRefused as exc:
            if mutated and exc.result.status in {
                ProvisionResultStatus.NOT_FOUND,
                ProvisionResultStatus.RETRYABLE,
                ProvisionResultStatus.TERMINAL,
            }:
                return _result(
                    ProvisionResultStatus.AMBIGUOUS,
                    error_code="provider_outcome_unknown",
                    provider_operation_ref=provider_ref,
                )
            return exc.result
        except ContaboActivationError as exc:
            return _result(ProvisionResultStatus.TERMINAL, error_code=exc.code)

    def observe(self, request: ProvisionObserveRequest) -> ProvisioningResult:
        try:
            _require_capability(request.capability_id)
            resource_ref = _resource_ref(request.target)
            expected_ref = _provider_ref(resource_ref)
            if request.provider_operation_ref != expected_ref:
                raise _terminal("provider_operation_ref_invalid")
            row = self._find(request, resource_ref)
            return _result(
                ProvisionResultStatus.SUCCEEDED,
                provider_operation_ref=expected_ref,
                evidence=self._evidence(resource_ref=resource_ref, row=row),
            )
        except _ConnectorRefused as exc:
            return exc.result
        except ContaboActivationError as exc:
            return _result(ProvisionResultStatus.TERMINAL, error_code=exc.code)

    def cancel(self, request: ProvisionCancelRequest) -> ProvisioningResult:
        try:
            _require_capability(request.capability_id)
            resource_ref = _resource_ref(request.target)
            expected_ref = _provider_ref(resource_ref)
            if request.provider_operation_ref != expected_ref:
                raise _terminal("provider_operation_ref_invalid")
            return _result(
                ProvisionResultStatus.CANCELLED,
                provider_operation_ref=expected_ref,
                evidence={"cancelled": False, "resource_ref": resource_ref},
            )
        except _ConnectorRefused as exc:
            return exc.result
        except ContaboActivationError as exc:
            return _result(ProvisionResultStatus.TERMINAL, error_code=exc.code)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ContaboConnector:
    """One stateless SPI plugin with a constructor-injected real transport."""

    __slots__ = ("_handler", "_transport")

    def __init__(
        self,
        transport: ContaboTransport | None = None,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        selected = transport if transport is not None else HttpxContaboTransport()
        self._transport = selected
        self._handler: ProvisioningHandler = _FirewallHandler(selected, clock)

    def __repr__(self) -> str:
        return "ContaboConnector()"

    @property
    def manifest(self) -> ConnectorManifest:
        return MANIFEST

    @property
    def historical_manifests(self) -> tuple[ConnectorManifest, ...]:
        return ()

    @property
    def modes(self) -> frozenset[ConnectorMode]:
        return frozenset({ConnectorMode.PROVISION})

    def provisioning_handler_for(self, capability_id: str) -> ProvisioningHandler:
        gate = activation_gate_for(capability_id)
        if gate is not None:
            raise ContaboActivationError(gate.code)
        MANIFEST.require_declares(capability_id)
        return self._handler

    def validate_connection(
        self, *, config: dict[str, object], secrets: dict[str, object]
    ) -> tuple[Diagnostic, ...]:
        try:
            endpoint = _endpoint(config)
            held_material = _held_material(secrets)
            response = self._transport.request(
                ContaboRequest(
                    method="GET",
                    base_endpoint=endpoint,
                    path="/v1/firewalls",
                    held_material=held_material,
                    request_id=_request_id("validate-connection"),
                    query={"size": "1"},
                )
            )
            if response.status_code != 200:
                return (Diagnostic(ok=False, code="provider_connection_refused"),)
            _rows(response)
        except _ConnectorRefused as exc:
            return (
                Diagnostic(
                    ok=False,
                    code=exc.result.error_code or "configuration_invalid",
                ),
            )
        except ContaboTransportError as exc:
            return (Diagnostic(ok=False, code=exc.code),)
        return (Diagnostic(ok=True, code="provider_connection_valid"),)


PLUGIN: Final = ContaboConnector()

__all__ = [
    "ACTIVATION_GATES",
    "MANIFEST",
    "PLUGIN",
    "CapabilityActivationGate",
    "ContaboActivationError",
    "ContaboConnector",
    "activation_gate_for",
]
