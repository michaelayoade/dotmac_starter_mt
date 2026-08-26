"""Integrator-side client for the closed Dotmac managed-host agent protocol."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, cast

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
from dotmac_managed_host_contracts import (
    BACKUP_RESTORE_LIFECYCLE,
    CAPABILITY_CONTRACTS,
    CAPABILITY_SCHEMAS,
    DEPLOYMENT_BUNDLE_LIFECYCLE,
    HEALTH_PROBE_LIFECYCLE,
)
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from .transport import (
    FailureKind,
    HostAgentRequest,
    HostAgentResponse,
    HostAgentTransport,
    HostAgentTransportError,
    HttpxHostAgentTransport,
    normalize_agent_endpoint,
)

CONNECTOR_KEY: Final = "dotmac_host_agent"
VERSION: Final = "0.1.0a1"
_PROTOCOL_VERSION: Final = 1
_DIGEST_RE: Final[re.Pattern[str]] = re.compile(r"^sha256:[0-9a-f]{64}$")
_ERROR_CODE_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]{1,126}$")
_REFERENCE_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}$"
)
_NO_VALUE: Final[Mapping[str, object]] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class CapabilityActivationGate:
    """The exact target-agent proof required before one binding may run."""

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
        DEPLOYMENT_BUNDLE_LIFECYCLE.capability_id: _activation_gate(
            DEPLOYMENT_BUNDLE_LIFECYCLE,
            code="bundle_catalogue_trust_unproven",
            requirement=(
                "The mTLS-authenticated agent must bind the configured "
                "content-addressed "
                "bundle catalogue to its exact digest, report a valid catalogue "
                "signature, and support bundle operation version 1."
            ),
        ),
        BACKUP_RESTORE_LIFECYCLE.capability_id: _activation_gate(
            BACKUP_RESTORE_LIFECYCLE,
            code="backup_object_semantics_unproven",
            requirement=(
                "The agent must bind the configured storage and prove immutable "
                "version references, object lock, SHA-256 content digests, and restore "
                "by exact "
                "object version."
            ),
        ),
        HEALTH_PROBE_LIFECYCLE.capability_id: _activation_gate(
            HEALTH_PROBE_LIFECYCLE,
            code="health_evidence_contract_unproven",
            requirement=(
                "The agent must declare the closed probe kinds, a maximum timeout no "
                "greater than 300 seconds, bounded response evidence, and SHA-256 "
                "response digests."
            ),
        ),
    }
)


class HostAgentProtocolError(RuntimeError):
    """A stable, material-free refusal from the closed agent protocol."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _ConnectorRefused(RuntimeError):
    def __init__(self, result: ProvisioningResult) -> None:
        self.result = result
        super().__init__(result.error_code or result.status.value)


def _declaration(snapshot: CapabilityContractSnapshot) -> CapabilityDeclaration:
    expected = {
        (schema_ref, schema_digest)
        for operation in snapshot.operations
        for schema_ref, schema_digest in (
            (operation.input_schema_ref, operation.input_schema_digest),
            (operation.output_schema_ref, operation.output_schema_digest),
        )
    }
    documents = tuple(
        document
        for document in CAPABILITY_SCHEMAS
        if (document.schema_ref, document.digest) in expected
    )
    return CapabilityDeclaration(
        capability_id=snapshot.capability_id,
        contract_snapshot=snapshot,
        schema_documents=documents,
    )


MANIFEST: Final = ConnectorManifest(
    connector_key=CONNECTOR_KEY,
    version=VERSION,
    spi_range=SpiRange.parse(">=1.2,<2.0"),
    capabilities=tuple(_declaration(snapshot) for snapshot in CAPABILITY_CONTRACTS),
)

_CONTRACTS: Final[Mapping[str, CapabilityContractSnapshot]] = MappingProxyType(
    {snapshot.capability_id: snapshot for snapshot in CAPABILITY_CONTRACTS}
)
_SCHEMAS: Final[Mapping[str, CapabilitySchemaDocument]] = MappingProxyType(
    {document.schema_ref: document for document in CAPABILITY_SCHEMAS}
)


def activation_gate_for(capability_id: str) -> CapabilityActivationGate | None:
    return ACTIVATION_GATES.get(capability_id)


def _result(
    status: ProvisionResultStatus,
    *,
    provider_operation_ref: str | None = None,
    evidence: Mapping[str, object] = _NO_VALUE,
    error_code: str | None = None,
) -> ProvisioningResult:
    return ProvisioningResult(
        status=status,
        provider_operation_ref=provider_operation_ref,
        evidence=evidence,
        error_code=error_code,
        error_detail=None,
    )


def _contract(capability_id: str) -> CapabilityContractSnapshot:
    snapshot = _CONTRACTS.get(capability_id)
    if snapshot is None:
        raise HostAgentProtocolError("capability_not_declared")
    return snapshot


def _operation_schema(
    capability_id: str,
    operation_code: str,
    *,
    output: bool,
) -> CapabilitySchemaDocument:
    snapshot = _contract(capability_id)
    operation = next(
        (
            candidate
            for candidate in snapshot.operations
            if candidate.operation_code == operation_code
        ),
        None,
    )
    if operation is None:
        raise HostAgentProtocolError("operation_not_declared")
    schema_ref = operation.output_schema_ref if output else operation.input_schema_ref
    schema = _SCHEMAS.get(schema_ref)
    if schema is None:
        raise HostAgentProtocolError("operation_schema_unavailable")
    return schema


def _validate_schema(
    capability_id: str,
    operation_code: str,
    value: Mapping[str, object],
    *,
    output: bool,
) -> None:
    schema = _operation_schema(capability_id, operation_code, output=output)
    try:
        document = json.loads(schema.to_json_bytes())
        Draft202012Validator(document).validate(dict(value))
    except (TypeError, ValueError, ValidationError):
        raise HostAgentProtocolError(
            "agent_evidence_invalid" if output else "target_invalid"
        ) from None


def _string(values: Mapping[str, object], field: str, *, code: str) -> str:
    value = values.get(field)
    if not isinstance(value, str) or not value:
        raise HostAgentProtocolError(code)
    return value


def _endpoint(config: Mapping[str, object]) -> str:
    try:
        return normalize_agent_endpoint(config.get("agent_endpoint"))
    except ValueError:
        raise HostAgentProtocolError("agent_endpoint_invalid") from None


def _identity_ref(config: Mapping[str, object]) -> str:
    return _string(config, "agent_identity_ref", code="agent_identity_ref_invalid")


def _held_material(secrets: Mapping[str, object]) -> str:
    return _string(secrets, "agent_secret_ref", code="agent_material_unavailable")


def _request_id(value: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"dotmac-host-agent:{value}"))


def _json_object(response: HostAgentResponse) -> Mapping[str, object]:
    try:
        value = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HostAgentProtocolError("agent_response_invalid") from None
    if not isinstance(value, dict):
        raise HostAgentProtocolError("agent_response_invalid")
    return cast(Mapping[str, object], value)


def _transport_refusal(error: HostAgentTransportError) -> _ConnectorRefused:
    status = {
        FailureKind.AMBIGUOUS: ProvisionResultStatus.AMBIGUOUS,
        FailureKind.RETRYABLE: ProvisionResultStatus.RETRYABLE,
        FailureKind.TERMINAL: ProvisionResultStatus.TERMINAL,
    }[error.kind]
    return _ConnectorRefused(_result(status, error_code=error.code))


def _http_refusal(response: HostAgentResponse, *, mutating: bool) -> _ConnectorRefused:
    status = response.status_code
    if 300 <= status < 400:
        result = _result(
            ProvisionResultStatus.TERMINAL,
            error_code="agent_redirect_refused",
        )
    elif status == 401:
        result = _result(
            ProvisionResultStatus.TERMINAL,
            error_code="agent_authentication_refused",
        )
    elif status == 403:
        result = _result(
            ProvisionResultStatus.TERMINAL,
            error_code="agent_authorization_refused",
        )
    elif status == 404:
        result = _result(
            ProvisionResultStatus.TERMINAL,
            error_code="agent_route_unavailable",
        )
    elif status == 429:
        result = _result(
            ProvisionResultStatus.RETRYABLE,
            error_code="agent_rate_limited",
        )
    elif status in {408, 409} or status >= 500:
        result = _result(
            (
                ProvisionResultStatus.AMBIGUOUS
                if mutating
                else ProvisionResultStatus.RETRYABLE
            ),
            error_code=("agent_outcome_unknown" if mutating else "agent_unavailable"),
        )
    else:
        result = _result(
            ProvisionResultStatus.TERMINAL,
            error_code="agent_request_refused",
        )
    return _ConnectorRefused(result)


def _send(
    transport: HostAgentTransport,
    request: HostAgentRequest,
    *,
    expected: frozenset[int] = frozenset({200}),
) -> HostAgentResponse:
    try:
        response = transport.request(request)
    except HostAgentTransportError as exc:
        raise _transport_refusal(exc) from None
    if response.status_code not in expected:
        raise _http_refusal(response, mutating=request.mutating)
    return response


def _schema_digests(snapshot: CapabilityContractSnapshot) -> list[str]:
    return sorted(
        {
            digest
            for operation in snapshot.operations
            for digest in (
                operation.input_schema_digest,
                operation.output_schema_digest,
            )
        }
    )


def _activation_document(
    document: Mapping[str, object],
    *,
    snapshot: CapabilityContractSnapshot,
    identity_ref: str,
) -> Mapping[str, object]:
    required = {
        "agent_identity_ref",
        "capability_id",
        "contract_digest",
        "evidence",
        "protocol_version",
        "schema_digests",
    }
    if set(document) != required:
        raise HostAgentProtocolError("agent_activation_document_invalid")
    if (
        document.get("protocol_version") != _PROTOCOL_VERSION
        or document.get("agent_identity_ref") != identity_ref
        or document.get("capability_id") != snapshot.capability_id
        or document.get("contract_digest") != snapshot.digest
        or document.get("schema_digests") != _schema_digests(snapshot)
    ):
        raise HostAgentProtocolError("agent_contract_identity_mismatch")
    evidence = document.get("evidence")
    if not isinstance(evidence, Mapping):
        raise HostAgentProtocolError("agent_activation_document_invalid")
    return cast(Mapping[str, object], evidence)


def _require_bundle_activation(
    evidence: Mapping[str, object], config: Mapping[str, object]
) -> None:
    catalogue_ref = _string(
        config, "bundle_catalogue_ref", code="bundle_catalogue_trust_unproven"
    )
    if (
        _DIGEST_RE.fullmatch(catalogue_ref) is None
        or set(evidence)
        != {
            "bundle_catalogue_digest",
            "bundle_catalogue_ref",
            "bundle_catalogue_signature_valid",
            "bundle_operation_version",
        }
        or evidence.get("bundle_catalogue_ref") != catalogue_ref
        or evidence.get("bundle_catalogue_digest") != catalogue_ref
        or evidence.get("bundle_catalogue_signature_valid") is not True
        or evidence.get("bundle_operation_version") != 1
    ):
        raise HostAgentProtocolError("bundle_catalogue_trust_unproven")


def _require_backup_activation(
    evidence: Mapping[str, object], config: Mapping[str, object]
) -> None:
    storage_ref = _string(
        config, "backup_storage_ref", code="backup_object_semantics_unproven"
    )
    if (
        set(evidence)
        != {
            "backup_storage_ref",
            "content_digest_algorithm",
            "immutable_version_refs",
            "object_lock_enabled",
            "restore_by_exact_version",
        }
        or evidence.get("backup_storage_ref") != storage_ref
        or evidence.get("content_digest_algorithm") != "sha256"
        or evidence.get("immutable_version_refs") is not True
        or evidence.get("object_lock_enabled") is not True
        or evidence.get("restore_by_exact_version") is not True
    ):
        raise HostAgentProtocolError("backup_object_semantics_unproven")


def _require_health_activation(
    evidence: Mapping[str, object], config: Mapping[str, object]
) -> None:
    del config
    max_timeout = evidence.get("max_timeout_seconds")
    body_bound = evidence.get("max_response_bytes")
    if (
        set(evidence)
        != {
            "max_response_bytes",
            "max_timeout_seconds",
            "probe_kinds",
            "response_digest_algorithm",
        }
        or type(max_timeout) is not int
        or not 1 <= max_timeout <= 300
        or type(body_bound) is not int
        or not 1 <= body_bound <= 1_048_576
        or evidence.get("probe_kinds")
        != ["http_roundtrip", "liveness", "readiness", "service"]
        or evidence.get("response_digest_algorithm") != "sha256"
    ):
        raise HostAgentProtocolError("health_evidence_contract_unproven")


_ACTIVATION_VALIDATORS: Final[
    Mapping[
        str,
        Callable[[Mapping[str, object], Mapping[str, object]], None],
    ]
] = MappingProxyType(
    {
        DEPLOYMENT_BUNDLE_LIFECYCLE.capability_id: _require_bundle_activation,
        BACKUP_RESTORE_LIFECYCLE.capability_id: _require_backup_activation,
        HEALTH_PROBE_LIFECYCLE.capability_id: _require_health_activation,
    }
)


def _snapshot_for_config(
    config: Mapping[str, object],
) -> CapabilityContractSnapshot:
    configured = set(config)
    matches: tuple[CapabilityContractSnapshot, ...] = tuple(
        snapshot
        for snapshot in CAPABILITY_CONTRACTS
        if configured
        == {
            field.field_code
            for field in snapshot.config_fields
            if field.value_type.value != "secret_reference"
        }
        | {endpoint.endpoint_code for endpoint in snapshot.endpoint_requirements}
    )
    if len(matches) != 1:
        raise HostAgentProtocolError("capability_configuration_shape_invalid")
    return matches[0]


class _HostLifecycleHandler:
    __slots__ = ("_snapshot", "_transport")

    def __init__(
        self,
        snapshot: CapabilityContractSnapshot,
        transport: HostAgentTransport,
    ) -> None:
        self._snapshot = snapshot
        self._transport = transport

    def _request(
        self,
        request: ProvisionPlanRequest
        | ProvisionApplyRequest
        | ProvisionObserveRequest
        | ProvisionCancelRequest,
        *,
        method: str,
        path: str,
        document: Mapping[str, object] | None = None,
        mutating: bool = False,
        identity: str,
    ) -> HostAgentResponse:
        operation_ref = getattr(request, "operation_ref", "plan")
        return _send(
            self._transport,
            HostAgentRequest(
                method=method,
                base_endpoint=_endpoint(request.config),
                path=path,
                identity_ref=identity,
                held_material=_held_material(request.secrets),
                request_id=_request_id(
                    f"{request.command_id}:{operation_ref}:{method}:{path}"
                ),
                document=document,
                mutating=mutating,
            ),
        )

    def _require_activation(
        self,
        request: ProvisionPlanRequest
        | ProvisionApplyRequest
        | ProvisionObserveRequest
        | ProvisionCancelRequest,
    ) -> str:
        identity = _identity_ref(request.config)
        response = self._request(
            request,
            method="GET",
            path=f"/v1/capabilities/{self._snapshot.capability_id}",
            identity=identity,
        )
        evidence = _activation_document(
            _json_object(response),
            snapshot=self._snapshot,
            identity_ref=identity,
        )
        validator = _ACTIVATION_VALIDATORS[self._snapshot.capability_id]
        validator(evidence, request.config)
        return identity

    def _require_capability(self, actual: str) -> None:
        if actual != self._snapshot.capability_id:
            raise HostAgentProtocolError("capability_handler_mismatch")

    def _one_plan_target(self, request: ProvisionPlanRequest) -> Mapping[str, object]:
        self._require_capability(request.capability_id)
        if len(request.steps) != 1:
            raise HostAgentProtocolError("single_step_required")
        step = request.steps[0]
        if step.endpoint_code != self._snapshot.capability_id:
            raise HostAgentProtocolError("step_contract_invalid")
        _validate_schema(
            request.capability_id,
            "plan",
            step.input,
            output=False,
        )
        return step.input

    def _apply_target(self, request: ProvisionApplyRequest) -> Mapping[str, object]:
        self._require_capability(request.capability_id)
        if request.step.endpoint_code != self._snapshot.capability_id:
            raise HostAgentProtocolError("step_contract_invalid")
        _validate_schema(
            request.capability_id,
            "apply",
            request.step.input,
            output=False,
        )
        return request.step.input

    def _plan_response(
        self,
        response: HostAgentResponse,
        *,
        capability_id: str,
    ) -> Mapping[str, object]:
        document = _json_object(response)
        if (
            set(document)
            != {"capability_id", "evidence", "operation", "protocol_version"}
            or document.get("protocol_version") != _PROTOCOL_VERSION
            or document.get("capability_id") != capability_id
            or document.get("operation") != "plan"
        ):
            raise HostAgentProtocolError("agent_response_invalid")
        evidence = document.get("evidence")
        if not isinstance(evidence, Mapping):
            raise HostAgentProtocolError("agent_response_invalid")
        result = cast(Mapping[str, object], evidence)
        _validate_schema(capability_id, "plan", result, output=True)
        return result

    def _operation_response(
        self,
        response: HostAgentResponse,
        *,
        capability_id: str,
        operation: str,
    ) -> ProvisioningResult:
        document = _json_object(response)
        expected = {
            "capability_id",
            "error_code",
            "evidence",
            "operation",
            "outcome",
            "protocol_version",
            "provider_operation_ref",
        }
        if (
            set(document) != expected
            or document.get("protocol_version") != _PROTOCOL_VERSION
            or document.get("capability_id") != capability_id
            or document.get("operation") != operation
        ):
            raise HostAgentProtocolError("agent_response_invalid")
        try:
            status = ProvisionResultStatus(document.get("outcome"))
        except (TypeError, ValueError):
            raise HostAgentProtocolError("agent_response_invalid") from None
        allowed_outcomes = {
            "apply": {
                ProvisionResultStatus.ACCEPTED,
                ProvisionResultStatus.AMBIGUOUS,
                ProvisionResultStatus.NOT_FOUND,
                ProvisionResultStatus.PENDING,
                ProvisionResultStatus.RETRYABLE,
                ProvisionResultStatus.SUCCEEDED,
                ProvisionResultStatus.TERMINAL,
            },
            "cancel": {
                ProvisionResultStatus.ACCEPTED,
                ProvisionResultStatus.AMBIGUOUS,
                ProvisionResultStatus.CANCELLED,
                ProvisionResultStatus.NOT_FOUND,
                ProvisionResultStatus.PENDING,
                ProvisionResultStatus.RETRYABLE,
                ProvisionResultStatus.TERMINAL,
            },
            "observe": {
                ProvisionResultStatus.NOT_FOUND,
                ProvisionResultStatus.PENDING,
                ProvisionResultStatus.RETRYABLE,
                ProvisionResultStatus.SUCCEEDED,
                ProvisionResultStatus.TERMINAL,
            },
        }
        if status not in allowed_outcomes[operation]:
            raise HostAgentProtocolError("agent_response_invalid")
        provider_ref = document.get("provider_operation_ref")
        if provider_ref is not None and (
            not isinstance(provider_ref, str)
            or _REFERENCE_RE.fullmatch(provider_ref) is None
        ):
            raise HostAgentProtocolError("agent_response_invalid")
        error_code = document.get("error_code")
        evidence = document.get("evidence")
        if not isinstance(evidence, Mapping):
            raise HostAgentProtocolError("agent_response_invalid")
        if status in {
            ProvisionResultStatus.SUCCEEDED,
            ProvisionResultStatus.CANCELLED,
        }:
            if error_code is not None or provider_ref is None:
                raise HostAgentProtocolError("agent_response_invalid")
            _validate_schema(capability_id, operation, evidence, output=True)
        elif status in {
            ProvisionResultStatus.ACCEPTED,
            ProvisionResultStatus.PENDING,
        }:
            if error_code is not None or evidence or provider_ref is None:
                raise HostAgentProtocolError("agent_response_invalid")
        else:
            if (
                not isinstance(error_code, str)
                or _ERROR_CODE_RE.fullmatch(error_code) is None
                or evidence
            ):
                raise HostAgentProtocolError("agent_response_invalid")
        return _result(
            status,
            provider_operation_ref=provider_ref,
            evidence=cast(Mapping[str, object], evidence),
            error_code=error_code,
        )

    def plan(self, request: ProvisionPlanRequest) -> ProvisionPlanResult:
        try:
            target = self._one_plan_target(request)
            identity = self._require_activation(request)
            response = self._request(
                request,
                method="POST",
                path=f"/v1/provision/{self._snapshot.capability_id}/plan",
                identity=identity,
                document={
                    "capability_id": self._snapshot.capability_id,
                    "operation": "plan",
                    "protocol_version": _PROTOCOL_VERSION,
                    "target": dict(target),
                },
            )
            evidence = self._plan_response(
                response, capability_id=self._snapshot.capability_id
            )
        except _ConnectorRefused as exc:
            raise HostAgentProtocolError(
                exc.result.error_code or "agent_plan_refused"
            ) from None
        return ProvisionPlanResult(
            plan_hash=request.plan_hash,
            steps=request.steps,
            evidence=evidence,
        )

    def apply(self, request: ProvisionApplyRequest) -> ProvisioningResult:
        delivered = False
        try:
            target = self._apply_target(request)
            identity = self._require_activation(request)
            response = self._request(
                request,
                method="POST",
                path=f"/v1/provision/{self._snapshot.capability_id}/apply",
                identity=identity,
                document={
                    "capability_id": self._snapshot.capability_id,
                    "idempotency_key": request.idempotency_key,
                    "operation": "apply",
                    "operation_ref": request.operation_ref,
                    "protocol_version": _PROTOCOL_VERSION,
                    "target": dict(target),
                },
                mutating=True,
            )
            delivered = True
            return self._operation_response(
                response,
                capability_id=self._snapshot.capability_id,
                operation="apply",
            )
        except _ConnectorRefused as exc:
            return exc.result
        except HostAgentProtocolError as exc:
            return _result(
                (
                    ProvisionResultStatus.AMBIGUOUS
                    if delivered
                    else ProvisionResultStatus.TERMINAL
                ),
                error_code=("agent_outcome_unknown" if delivered else exc.code),
            )

    def observe(self, request: ProvisionObserveRequest) -> ProvisioningResult:
        try:
            self._require_capability(request.capability_id)
            _validate_schema(
                request.capability_id,
                "observe",
                request.target,
                output=False,
            )
            identity = self._require_activation(request)
            response = self._request(
                request,
                method="POST",
                path=f"/v1/provision/{self._snapshot.capability_id}/observe",
                identity=identity,
                document={
                    "capability_id": self._snapshot.capability_id,
                    "operation": "observe",
                    "operation_ref": request.operation_ref,
                    "protocol_version": _PROTOCOL_VERSION,
                    "provider_operation_ref": request.provider_operation_ref,
                    "target": dict(request.target),
                },
            )
            return self._operation_response(
                response,
                capability_id=self._snapshot.capability_id,
                operation="observe",
            )
        except _ConnectorRefused as exc:
            return exc.result
        except HostAgentProtocolError as exc:
            return _result(ProvisionResultStatus.TERMINAL, error_code=exc.code)

    def cancel(self, request: ProvisionCancelRequest) -> ProvisioningResult:
        delivered = False
        try:
            self._require_capability(request.capability_id)
            _validate_schema(
                request.capability_id,
                "cancel",
                request.target,
                output=False,
            )
            identity = self._require_activation(request)
            response = self._request(
                request,
                method="POST",
                path=f"/v1/provision/{self._snapshot.capability_id}/cancel",
                identity=identity,
                document={
                    "capability_id": self._snapshot.capability_id,
                    "idempotency_key": request.idempotency_key,
                    "operation": "cancel",
                    "operation_ref": request.operation_ref,
                    "protocol_version": _PROTOCOL_VERSION,
                    "provider_operation_ref": request.provider_operation_ref,
                    "reason": request.reason,
                    "target": dict(request.target),
                },
                mutating=True,
            )
            delivered = True
            return self._operation_response(
                response,
                capability_id=self._snapshot.capability_id,
                operation="cancel",
            )
        except _ConnectorRefused as exc:
            return exc.result
        except HostAgentProtocolError as exc:
            return _result(
                (
                    ProvisionResultStatus.AMBIGUOUS
                    if delivered
                    else ProvisionResultStatus.TERMINAL
                ),
                error_code=("agent_outcome_unknown" if delivered else exc.code),
            )


class DotmacHostAgentConnector:
    """One stateless connector; it is a client, never the target agent."""

    __slots__ = ("_handlers", "_transport")

    def __init__(self, transport: HostAgentTransport | None = None) -> None:
        selected = transport if transport is not None else HttpxHostAgentTransport()
        self._transport = selected
        self._handlers: Mapping[str, ProvisioningHandler] = MappingProxyType(
            {
                snapshot.capability_id: _HostLifecycleHandler(snapshot, selected)
                for snapshot in CAPABILITY_CONTRACTS
            }
        )

    def __repr__(self) -> str:
        return "DotmacHostAgentConnector()"

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
        MANIFEST.require_declares(capability_id)
        return self._handlers[capability_id]

    def validate_connection(
        self, *, config: dict[str, object], secrets: dict[str, object]
    ) -> tuple[Diagnostic, ...]:
        try:
            endpoint = _endpoint(config)
            identity = _identity_ref(config)
            material = _held_material(secrets)
            snapshot = _snapshot_for_config(config)
        except HostAgentProtocolError as exc:
            return (Diagnostic(ok=False, code=exc.code),)
        try:
            response = _send(
                self._transport,
                HostAgentRequest(
                    method="GET",
                    base_endpoint=endpoint,
                    path=f"/v1/capabilities/{snapshot.capability_id}",
                    identity_ref=identity,
                    held_material=material,
                    request_id=_request_id(
                        f"validate-connection:{snapshot.capability_id}"
                    ),
                ),
            )
            evidence = _activation_document(
                _json_object(response),
                snapshot=snapshot,
                identity_ref=identity,
            )
            _ACTIVATION_VALIDATORS[snapshot.capability_id](evidence, config)
        except _ConnectorRefused as exc:
            return (
                Diagnostic(
                    ok=False,
                    code=exc.result.error_code or "agent_connection_refused",
                ),
            )
        except HostAgentProtocolError as exc:
            return (Diagnostic(ok=False, code=exc.code),)
        return (
            Diagnostic(
                ok=True,
                code=(
                    snapshot.capability_code.replace(".", "_").replace("-", "_")
                    + "_valid"
                ),
            ),
        )


PLUGIN: Final = DotmacHostAgentConnector()

__all__ = [
    "ACTIVATION_GATES",
    "MANIFEST",
    "PLUGIN",
    "CapabilityActivationGate",
    "DotmacHostAgentConnector",
    "HostAgentProtocolError",
    "activation_gate_for",
]
