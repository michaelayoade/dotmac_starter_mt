"""Stateless SPI 1.2 adapter for the constrained Nextcloud management API."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final

from dotmac_integration.spi import (
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
)

from .declaration import MANIFEST
from .transport import (
    FailureKind,
    HttpxNextcloudTransport,
    ManagementRequest,
    NextcloudTransport,
    NextcloudTransportError,
    normalize_management_endpoint,
)

_OIDC_CAPABILITY: Final = "collaboration.user-oidc.configuration.lifecycle.v1"
_APPLICATION_CAPABILITY: Final = "collaboration.application.lifecycle.v1"
_ACCOUNT_CAPABILITY: Final = "collaboration.user-group-quota.lifecycle.v1"
_ROUNDTRIP_CAPABILITY: Final = "collaboration.file-roundtrip.lifecycle.v1"
_SAFE_REFERENCE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,319}$"
)
_SAFE_CODE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_.:-]{0,158}[a-z0-9]$")
_STATUSES: Final[Mapping[str, ProvisionResultStatus]] = {
    status.value: status for status in ProvisionResultStatus
}
_FORBIDDEN_EVIDENCE_TOKENS: Final = frozenset(
    {
        "authorization",
        "credential",
        "password",
        "token",
        "secret",
    }
)
_FORBIDDEN_EVIDENCE_COMPOUNDS: Final = frozenset(
    {"apikey", "privatekey", "recoverycode"}
)
_SAFE_SECRET_METADATA_KEYS: Final = frozenset({"client_secret_configured"})


class NextcloudContractError(RuntimeError):
    """The closed private-management response did not keep its contract."""


class NextcloudConfigurationError(RuntimeError):
    """Required held installation material is absent before provider I/O."""


def _string(mapping: Mapping[str, object], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise NextcloudContractError(f"{key}_required")
    return value


def _optional_string(mapping: Mapping[str, object], key: str) -> str | None:
    value = mapping.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise NextcloudContractError(f"{key}_invalid")
    return value


def _safe_reference(mapping: Mapping[str, object], key: str) -> str | None:
    value = _optional_string(mapping, key)
    if value is not None and _SAFE_REFERENCE.fullmatch(value) is None:
        raise NextcloudContractError(f"{key}_invalid")
    return value


def _safe_code(mapping: Mapping[str, object], key: str) -> str | None:
    value = _optional_string(mapping, key)
    if value is not None and _SAFE_CODE.fullmatch(value) is None:
        raise NextcloudContractError(f"{key}_invalid")
    return value


def _require_no_secret_output(value: object) -> None:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            if not isinstance(raw_key, str):
                raise NextcloudContractError("provider_evidence_key_invalid")
            normalized = re.sub(r"[^a-z0-9]+", "_", raw_key.casefold()).strip("_")
            tokens = frozenset(normalized.split("_"))
            compound = normalized.replace("_", "")
            if raw_key not in _SAFE_SECRET_METADATA_KEYS and (
                tokens & _FORBIDDEN_EVIDENCE_TOKENS
                or any(marker in compound for marker in _FORBIDDEN_EVIDENCE_COMPOUNDS)
            ):
                raise NextcloudContractError("provider_secret_output_forbidden")
            _require_no_secret_output(item)
    elif isinstance(value, list | tuple):
        for item in value:
            _require_no_secret_output(item)


def _evidence(document: Mapping[str, object]) -> dict[str, object]:
    value = document.get("evidence")
    if not isinstance(value, Mapping):
        raise NextcloudContractError("provider_evidence_required")
    evidence = dict(value)
    _require_no_secret_output(evidence)
    return evidence


def _endpoint(config: Mapping[str, object]) -> str:
    value = config.get("management_endpoint")
    if not isinstance(value, str) or not value:
        raise NextcloudConfigurationError("management_endpoint_required")
    return value


def _held_material(secrets: Mapping[str, str], key: str) -> str:
    value = secrets.get(key)
    if not isinstance(value, str) or not value:
        raise NextcloudConfigurationError("required_material_unavailable")
    return value


def _installation_context(config: Mapping[str, object]) -> dict[str, object]:
    """Only declared non-secret operation context crosses to the facade."""

    context: dict[str, object] = {}
    for key in ("backup_storage_ref", "release_channel_ref"):
        value = config.get(key)
        if value is not None:
            if not isinstance(value, str) or not value:
                raise NextcloudConfigurationError(f"{key}_invalid")
            context[key] = value
    return context


def _transport_result(error: NextcloudTransportError) -> ProvisioningResult:
    status = {
        FailureKind.AMBIGUOUS: ProvisionResultStatus.AMBIGUOUS,
        FailureKind.NOT_FOUND: ProvisionResultStatus.NOT_FOUND,
        FailureKind.RETRYABLE: ProvisionResultStatus.RETRYABLE,
        FailureKind.TERMINAL: ProvisionResultStatus.TERMINAL,
    }[error.kind]
    return ProvisioningResult(status=status, error_code=error.code)


def _cancel_not_found_evidence(
    capability_id: str, target: Mapping[str, object]
) -> dict[str, object]:
    if capability_id == _APPLICATION_CAPABILITY:
        return {
            "application_ref": _string(target, "application_ref"),
            "cancelled": True,
        }
    if capability_id == _OIDC_CAPABILITY:
        return {
            "cancelled": True,
            "oidc_configuration_ref": _string(target, "oidc_configuration_ref"),
        }
    if capability_id == _ACCOUNT_CAPABILITY:
        return {
            "cancelled": True,
            "resource_kind": _string(target, "resource_kind"),
            "resource_ref": _string(target, "resource_ref"),
        }
    if capability_id == _ROUNDTRIP_CAPABILITY:
        return {
            "cancelled": True,
            "cleanup_succeeded": True,
            "roundtrip_ref": _string(target, "roundtrip_ref"),
        }
    raise NextcloudContractError("capability_unsupported")


class NextcloudProvisioningHandler:
    """One provider-neutral handler over four exact owner declarations."""

    def __init__(self, transport: NextcloudTransport) -> None:
        self._transport = transport

    def _invoke(
        self,
        *,
        capability_id: str,
        operation: str,
        body: Mapping[str, object],
        mutating: bool,
        config: Mapping[str, object],
        secrets: Mapping[str, str],
    ) -> Mapping[str, object]:
        MANIFEST.require_declares(capability_id)
        return self._transport.invoke(
            management_endpoint=_endpoint(config),
            management_authorization=_held_material(secrets, "management_secret_ref"),
            client_secret=(
                _held_material(secrets, "client_secret_ref")
                if capability_id == _OIDC_CAPABILITY and operation == "apply"
                else None
            ),
            request=ManagementRequest(
                capability_id=capability_id,
                operation=operation,
                body=body,
                mutating=mutating,
            ),
        )

    def plan(self, request: ProvisionPlanRequest) -> ProvisionPlanResult:
        if len(request.steps) != 1:
            raise NextcloudContractError("plan_requires_exactly_one_step")
        step = request.steps[0]
        response = self._invoke(
            capability_id=request.capability_id,
            operation="plan",
            body={
                "command_id": request.command_id,
                "plan_hash": request.plan_hash,
                "step_key": step.step_key,
                "target": dict(step.input),
                "installation_context": _installation_context(request.config),
            },
            mutating=False,
            config=request.config,
            secrets=request.secrets,
        )
        return ProvisionPlanResult(
            plan_hash=request.plan_hash,
            steps=request.steps,
            evidence=_evidence(response),
        )

    def apply(self, request: ProvisionApplyRequest) -> ProvisioningResult:
        try:
            response = self._invoke(
                capability_id=request.capability_id,
                operation="apply",
                body={
                    "command_id": request.command_id,
                    "idempotency_key": request.idempotency_key,
                    "operation_ref": request.operation_ref,
                    "plan_hash": request.plan_hash,
                    "step_key": request.step.step_key,
                    "target": dict(request.step.input),
                    "installation_context": _installation_context(request.config),
                },
                mutating=True,
                config=request.config,
                secrets=request.secrets,
            )
            return self._result(response)
        except NextcloudTransportError as exc:
            return _transport_result(exc)
        except NextcloudConfigurationError:
            return ProvisioningResult(
                status=ProvisionResultStatus.TERMINAL,
                error_code="connector_configuration_invalid",
            )
        except NextcloudContractError:
            return ProvisioningResult(
                status=ProvisionResultStatus.AMBIGUOUS,
                error_code="provider_contract_invalid",
            )

    def observe(self, request: ProvisionObserveRequest) -> ProvisioningResult:
        try:
            response = self._invoke(
                capability_id=request.capability_id,
                operation="observe",
                body={
                    "command_id": request.command_id,
                    "operation_ref": request.operation_ref,
                    "plan_hash": request.plan_hash,
                    "provider_operation_ref": request.provider_operation_ref,
                    "step_key": request.step_key,
                    "target": dict(request.target),
                    "installation_context": _installation_context(request.config),
                },
                mutating=False,
                config=request.config,
                secrets=request.secrets,
            )
            return self._result(response)
        except NextcloudTransportError as exc:
            return _transport_result(exc)
        except NextcloudConfigurationError:
            return ProvisioningResult(
                status=ProvisionResultStatus.TERMINAL,
                error_code="connector_configuration_invalid",
            )
        except NextcloudContractError:
            return ProvisioningResult(
                status=ProvisionResultStatus.TERMINAL,
                error_code="provider_contract_invalid",
            )

    def cancel(self, request: ProvisionCancelRequest) -> ProvisioningResult:
        try:
            response = self._invoke(
                capability_id=request.capability_id,
                operation="cancel",
                body={
                    "command_id": request.command_id,
                    "idempotency_key": request.idempotency_key,
                    "operation_ref": request.operation_ref,
                    "plan_hash": request.plan_hash,
                    "provider_operation_ref": request.provider_operation_ref,
                    "reason": request.reason,
                    "step_key": request.step_key,
                    "target": dict(request.target),
                    "installation_context": _installation_context(request.config),
                },
                mutating=True,
                config=request.config,
                secrets=request.secrets,
            )
            return self._result(response)
        except NextcloudTransportError as exc:
            if exc.kind is FailureKind.NOT_FOUND:
                return ProvisioningResult(
                    status=ProvisionResultStatus.NOT_FOUND,
                    provider_operation_ref=request.provider_operation_ref,
                    evidence=_cancel_not_found_evidence(
                        request.capability_id, request.target
                    ),
                    error_code=exc.code,
                )
            return _transport_result(exc)
        except NextcloudConfigurationError:
            return ProvisioningResult(
                status=ProvisionResultStatus.TERMINAL,
                error_code="connector_configuration_invalid",
            )
        except NextcloudContractError:
            return ProvisioningResult(
                status=ProvisionResultStatus.AMBIGUOUS,
                error_code="provider_contract_invalid",
            )

    @staticmethod
    def _result(document: Mapping[str, object]) -> ProvisioningResult:
        raw_status = _string(document, "status")
        try:
            status = _STATUSES[raw_status]
        except KeyError:
            raise NextcloudContractError("provider_status_unsupported") from None
        return ProvisioningResult(
            status=status,
            provider_operation_ref=_safe_reference(document, "provider_operation_ref"),
            evidence=_evidence(document),
            error_code=_safe_code(document, "error_code"),
        )


class NextcloudConnector:
    """Metadata-discovered connector with an injectable, stateless transport."""

    def __init__(self, transport: NextcloudTransport | None = None) -> None:
        self._handler: ProvisioningHandler = NextcloudProvisioningHandler(
            transport if transport is not None else HttpxNextcloudTransport()
        )

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
        return self._handler

    def validate_connection(
        self,
        *,
        config: dict[str, object],
        secrets: dict[str, object],
    ) -> tuple[Diagnostic, ...]:
        endpoint = config.get("management_endpoint")
        try:
            normalize_management_endpoint(endpoint)
        except ValueError as exc:
            return (Diagnostic(ok=False, code=str(exc)),)
        material = secrets.get("management_secret_ref")
        if not isinstance(material, str) or not material:
            return (Diagnostic(ok=False, code="required_material_unavailable"),)
        if "client_secret_ref" in config:
            client_material = secrets.get("client_secret_ref")
            if not isinstance(client_material, str) or not client_material:
                return (Diagnostic(ok=False, code="required_material_unavailable"),)
        return ()


PLUGIN: Final = NextcloudConnector()

__all__ = [
    "NextcloudConnector",
    "NextcloudConfigurationError",
    "NextcloudContractError",
    "NextcloudProvisioningHandler",
    "PLUGIN",
]
