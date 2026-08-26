"""Stateless Keycloak Admin translation for exact managed-identity contracts."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Mapping
from typing import Final, cast
from urllib.parse import urlsplit

from dotmac_integration.spi import (
    CapabilityContractSnapshot,
    CapabilityDeclaration,
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
    ProvisionStep,
    SpiRange,
)
from dotmac_managed_identity_contracts import (
    CAPABILITY_CONTRACTS,
    CAPABILITY_SCHEMAS,
)

from .transport import (
    HttpxKeycloakTransport,
    KeycloakAdminRequest,
    KeycloakAdminResponse,
    KeycloakAdminTransport,
    KeycloakTransportError,
    _admin_credential,
    _canonical_base_endpoint,
)

CONNECTOR_KEY: Final = "keycloak_admin"
VERSION: Final = "0.1.0a1"
REALM_CAPABILITY: Final = "identity.realm.lifecycle.v1"
CLIENT_CAPABILITY: Final = "identity.oidc-client.lifecycle.v1"
USER_CAPABILITY: Final = "identity.user.lifecycle.v1"
_ADMIN_MATERIAL_FIELD: Final = "admin_secret_ref"
_CLIENT_MATERIAL_FIELD: Final = "client_secret_ref"
_RESOURCE_REF_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,126}$"
)
_INTERNAL_REF_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
)
_FQDN_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
_AUDIENCE_MAPPER_NAME: Final = "dotmac-audience"


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
        schema
        for schema in CAPABILITY_SCHEMAS
        if (schema.schema_ref, schema.digest) in expected
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
    capabilities=tuple(_declaration(contract) for contract in CAPABILITY_CONTRACTS),
)


class _ConnectorRefused(RuntimeError):
    def __init__(self, result: ProvisioningResult) -> None:
        self.result = result
        super().__init__(result.error_code or result.status.value)


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


def _http_refusal(
    response: KeycloakAdminResponse,
    *,
    mutation: bool,
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
            error_code="admin_authentication_refused",
            provider_operation_ref=provider_operation_ref,
        )
    elif status == 403:
        result = _result(
            ProvisionResultStatus.TERMINAL,
            error_code="admin_authorization_refused",
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
                if mutation
                else ProvisionResultStatus.RETRYABLE
            ),
            error_code=(
                "provider_outcome_unknown" if mutation else "provider_unavailable"
            ),
            provider_operation_ref=provider_operation_ref,
        )
    elif status == 404:
        result = _result(
            ProvisionResultStatus.NOT_FOUND,
            error_code="resource_not_found",
            provider_operation_ref=provider_operation_ref,
        )
    else:
        result = _result(
            ProvisionResultStatus.TERMINAL,
            error_code="provider_request_refused",
            provider_operation_ref=provider_operation_ref,
        )
    return _ConnectorRefused(result)


def _transport_refusal(
    exc: KeycloakTransportError,
    *,
    mutation: bool,
    provider_operation_ref: str | None = None,
) -> _ConnectorRefused:
    if exc.code in {
        "admin_material_invalid",
        "admin_material_unavailable",
        "endpoint_invalid",
        "method_refused",
        "path_refused",
        "request_document_invalid",
    }:
        code = (
            "admin_endpoint_invalid"
            if exc.code in {"endpoint_invalid", "path_refused"}
            else "connector_request_refused"
        )
        return _ConnectorRefused(
            _result(
                ProvisionResultStatus.TERMINAL,
                error_code=code,
                provider_operation_ref=provider_operation_ref,
            )
        )
    explicit = {
        "admin_authentication_refused": ProvisionResultStatus.TERMINAL,
        "admin_authorization_refused": ProvisionResultStatus.TERMINAL,
        "provider_rate_limited": ProvisionResultStatus.RETRYABLE,
        "provider_redirect_refused": ProvisionResultStatus.TERMINAL,
        "provider_request_refused": ProvisionResultStatus.TERMINAL,
        "provider_response_invalid": ProvisionResultStatus.TERMINAL,
        "provider_unavailable": ProvisionResultStatus.RETRYABLE,
    }
    if exc.code in explicit:
        return _ConnectorRefused(
            _result(
                explicit[exc.code],
                error_code=exc.code,
                provider_operation_ref=provider_operation_ref,
            )
        )
    return _ConnectorRefused(
        _result(
            (
                ProvisionResultStatus.AMBIGUOUS
                if mutation
                else ProvisionResultStatus.RETRYABLE
            ),
            error_code=(
                "provider_outcome_unknown" if mutation else "provider_unavailable"
            ),
            provider_operation_ref=provider_operation_ref,
        )
    )


def _after_mutation(result: ProvisioningResult) -> ProvisioningResult:
    """Turn an unreadable post-mutation state into reconciliation evidence."""

    if result.status in {
        ProvisionResultStatus.NOT_FOUND,
        ProvisionResultStatus.RETRYABLE,
    }:
        return _result(
            ProvisionResultStatus.AMBIGUOUS,
            error_code="provider_outcome_unknown",
            provider_operation_ref=result.provider_operation_ref,
        )
    return result


def _send(
    transport: KeycloakAdminTransport,
    request: KeycloakAdminRequest,
    *,
    expected: frozenset[int],
    mutation: bool,
    provider_operation_ref: str | None = None,
) -> KeycloakAdminResponse:
    try:
        response = transport.request(request)
    except KeycloakTransportError as exc:
        raise _transport_refusal(
            exc,
            mutation=mutation,
            provider_operation_ref=provider_operation_ref,
        ) from None
    if response.status_code not in expected:
        raise _http_refusal(
            response,
            mutation=mutation,
            provider_operation_ref=provider_operation_ref,
        )
    return response


def _json_value(response: KeycloakAdminResponse) -> object:
    try:
        return json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _terminal("provider_response_invalid") from None


def _json_object(response: KeycloakAdminResponse) -> Mapping[str, object]:
    value = _json_value(response)
    if not isinstance(value, Mapping):
        raise _terminal("provider_response_invalid")
    return cast(Mapping[str, object], value)


def _json_objects(response: KeycloakAdminResponse) -> tuple[Mapping[str, object], ...]:
    value = _json_value(response)
    if not isinstance(value, list) or not all(
        isinstance(item, Mapping) for item in value
    ):
        raise _terminal("provider_response_invalid")
    return tuple(cast(Mapping[str, object], item) for item in value)


def _canonical_digest(value: Mapping[str, object]) -> str:
    payload = json.dumps(
        dict(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _required_string(values: Mapping[str, object], name: str) -> str:
    value = values.get(name)
    if not isinstance(value, str) or not value:
        raise _terminal("target_invalid")
    return value


def _safe_ref(value: object, *, code: str = "target_invalid") -> str:
    if not isinstance(value, str) or _RESOURCE_REF_RE.fullmatch(value) is None:
        raise _terminal(code)
    if value.casefold() == "master":
        raise _terminal("master_realm_refused")
    return value


def _admin_endpoint(config: Mapping[str, object]) -> str:
    value = config.get("admin_endpoint")
    if not isinstance(value, str):
        raise _terminal("admin_endpoint_invalid")
    try:
        return _canonical_base_endpoint(value)
    except KeycloakTransportError:
        raise _terminal("admin_endpoint_invalid") from None


def _material(values: Mapping[str, str], field: str) -> str:
    value = values.get(field)
    if not isinstance(value, str) or not value:
        raise _terminal("required_material_unavailable")
    return value


def _admin_material(values: Mapping[str, str]) -> str:
    value = _material(values, _ADMIN_MATERIAL_FIELD)
    try:
        _admin_credential(value)
    except KeycloakTransportError:
        raise _terminal("required_material_invalid") from None
    return value


def _admin_access(
    transport: KeycloakAdminTransport,
    *,
    endpoint: str,
    realm_ref: str,
    held_material: str,
) -> str:
    try:
        return transport.admin_access_token(
            base_endpoint=endpoint,
            realm_ref=realm_ref,
            held_material=held_material,
        )
    except KeycloakTransportError as exc:
        raise _transport_refusal(exc, mutation=False) from None


def _realm_path(realm_ref: str, suffix: str = "") -> str:
    realm = _safe_ref(realm_ref)
    return f"/admin/realms/{realm}{suffix}"


def _require_step(step: ProvisionStep, capability_id: str) -> None:
    if step.endpoint_code != capability_id:
        raise _terminal("endpoint_code_mismatch")


def _require_capability(actual: str, expected: str) -> None:
    if actual != expected:
        raise _terminal("capability_id_mismatch")


def _one_plan_step(
    request: ProvisionPlanRequest, expected_capability: str
) -> ProvisionStep:
    _require_capability(request.capability_id, expected_capability)
    if len(request.steps) != 1:
        raise _terminal("plan_step_count_refused")
    step = request.steps[0]
    _require_step(step, request.capability_id)
    return step


def _issuer_url(public_hostname: str, realm_ref: str) -> str:
    if _FQDN_RE.fullmatch(public_hostname) is None:
        raise _terminal("public_hostname_invalid")
    return f"https://{public_hostname}/realms/{realm_ref}"


def _realm_issuer(document: Mapping[str, object], realm_ref: str) -> str:
    attributes = document.get("attributes")
    if not isinstance(attributes, Mapping):
        raise _terminal("provider_response_invalid")
    issuer = attributes.get("frontendUrl")
    if not isinstance(issuer, str):
        raise _terminal("provider_response_invalid")
    parsed = urlsplit(issuer)
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != f"/realms/{realm_ref}"
    ):
        raise _terminal("provider_response_invalid")
    return issuer


def _realm_evidence(
    document: Mapping[str, object], realm_ref: str
) -> Mapping[str, object]:
    if (
        document.get("realm") != realm_ref
        or document.get("enabled") is not True
        or document.get("defaultSignatureAlgorithm") != "RS256"
    ):
        raise _terminal("security_posture_mismatch")
    issuer = _realm_issuer(document, realm_ref)
    evidence: dict[str, object] = {
        "admin_endpoint_public": False,
        "authorization_code_enabled": True,
        "discovery_url": f"{issuer}/.well-known/openid-configuration",
        "issuer_url": issuer,
        "jwks_uri": f"{issuer}/protocol/openid-connect/certs",
        "realm_ref": realm_ref,
        "signing_algorithm": "RS256",
    }
    evidence["observed_configuration_digest"] = _canonical_digest(evidence)
    return evidence


def _provider_ref(*, realm: str, client: str) -> str:
    document = json.dumps(
        {"client": client, "realm": realm},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    encoded = base64.urlsafe_b64encode(document).decode("ascii").rstrip("=")
    return f"kc1.{encoded}"


def _parse_provider_ref(value: str) -> tuple[str, str]:
    if not value.startswith("kc1."):
        raise _terminal("provider_operation_ref_invalid")
    encoded = value.removeprefix("kc1.")
    try:
        padding = "=" * (-len(encoded) % 4)
        raw = base64.b64decode(encoded + padding, altchars=b"-_", validate=True)
        document = json.loads(raw.decode("ascii"))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        raise _terminal("provider_operation_ref_invalid") from None
    if not isinstance(document, dict) or set(document) != {"client", "realm"}:
        raise _terminal("provider_operation_ref_invalid")
    realm = _safe_ref(document.get("realm"), code="provider_operation_ref_invalid")
    client = document.get("client")
    if not isinstance(client, str) or _INTERNAL_REF_RE.fullmatch(client) is None:
        raise _terminal("provider_operation_ref_invalid")
    return realm, client


def _user_provider_ref(*, realm: str, subject: str) -> str:
    document = json.dumps(
        {"realm": realm, "subject": subject},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    encoded = base64.urlsafe_b64encode(document).decode("ascii").rstrip("=")
    return f"kcu1.{encoded}"


def _parse_user_provider_ref(value: str) -> tuple[str, str]:
    if not value.startswith("kcu1."):
        raise _terminal("provider_operation_ref_invalid")
    encoded = value.removeprefix("kcu1.")
    try:
        padding = "=" * (-len(encoded) % 4)
        raw = base64.b64decode(encoded + padding, altchars=b"-_", validate=True)
        document = json.loads(raw.decode("ascii"))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        raise _terminal("provider_operation_ref_invalid") from None
    if not isinstance(document, dict) or set(document) != {"realm", "subject"}:
        raise _terminal("provider_operation_ref_invalid")
    realm = _safe_ref(document.get("realm"), code="provider_operation_ref_invalid")
    subject = document.get("subject")
    if not isinstance(subject, str) or _INTERNAL_REF_RE.fullmatch(subject) is None:
        raise _terminal("provider_operation_ref_invalid")
    return realm, subject


def _realm_from_issuer(value: str) -> tuple[str, str]:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise _terminal("issuer_url_invalid") from None
    segments = tuple(segment for segment in parsed.path.split("/") if segment)
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port is not None
        and not 1 <= port <= 65535
        or len(segments) < 2
        or segments[-2] != "realms"
    ):
        raise _terminal("issuer_url_invalid")
    realm = _safe_ref(segments[-1], code="issuer_url_invalid")
    canonical = value.rstrip("/")
    return realm, canonical


def _location_ref(location: str | None) -> str | None:
    if location is None:
        return None
    try:
        parsed = urlsplit(location)
    except ValueError:
        return None
    candidate = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    return candidate if _INTERNAL_REF_RE.fullmatch(candidate) else None


class _RealmLifecycleHandler:
    __slots__ = ("_transport",)

    def __init__(self, transport: KeycloakAdminTransport) -> None:
        self._transport = transport

    def plan(self, request: ProvisionPlanRequest) -> ProvisionPlanResult:
        step = _one_plan_step(request, REALM_CAPABILITY)
        _admin_endpoint(request.config)
        _admin_material(request.secrets)
        realm_ref = _safe_ref(step.input.get("realm_ref"))
        return ProvisionPlanResult(
            plan_hash=request.plan_hash,
            steps=request.steps,
            evidence={"changes": ["reconcile"], "realm_ref": realm_ref},
        )

    def apply(self, request: ProvisionApplyRequest) -> ProvisioningResult:
        mutated = False
        try:
            _require_capability(request.capability_id, REALM_CAPABILITY)
            _require_step(request.step, request.capability_id)
            target = request.step.input
            endpoint = _admin_endpoint(request.config)
            admin_credential = _admin_material(request.secrets)
            realm_ref = _safe_ref(target.get("realm_ref"))
            admin_access = _admin_access(
                self._transport,
                endpoint=endpoint,
                realm_ref=realm_ref,
                held_material=admin_credential,
            )
            display_name = _required_string(target, "display_name")
            public_hostname = _required_string(target, "public_hostname")
            issuer = _issuer_url(public_hostname, realm_ref)
            path = _realm_path(realm_ref)
            existing = _send(
                self._transport,
                KeycloakAdminRequest("GET", endpoint, path, admin_access),
                expected=frozenset({200}),
                mutation=False,
            )
            _json_object(existing)
            _send(
                self._transport,
                KeycloakAdminRequest(
                    "PUT",
                    endpoint,
                    path,
                    admin_access,
                    document={
                        "attributes": {"frontendUrl": issuer},
                        "defaultSignatureAlgorithm": "RS256",
                        "displayName": display_name,
                        "enabled": True,
                        "realm": realm_ref,
                    },
                ),
                expected=frozenset({200, 204}),
                mutation=True,
                provider_operation_ref=realm_ref,
            )
            mutated = True
            observed = _send(
                self._transport,
                KeycloakAdminRequest("GET", endpoint, path, admin_access),
                expected=frozenset({200}),
                mutation=False,
                provider_operation_ref=realm_ref,
            )
            evidence = _realm_evidence(_json_object(observed), realm_ref)
            return _result(
                ProvisionResultStatus.SUCCEEDED,
                provider_operation_ref=realm_ref,
                evidence=evidence,
            )
        except _ConnectorRefused as exc:
            if exc.result.status is ProvisionResultStatus.NOT_FOUND:
                if mutated:
                    return _after_mutation(exc.result)
                return _result(
                    ProvisionResultStatus.TERMINAL,
                    error_code="realm_precreation_required",
                )
            return _after_mutation(exc.result) if mutated else exc.result

    def observe(self, request: ProvisionObserveRequest) -> ProvisioningResult:
        try:
            _require_capability(request.capability_id, REALM_CAPABILITY)
            endpoint = _admin_endpoint(request.config)
            admin_credential = _admin_material(request.secrets)
            realm_ref = _safe_ref(request.target.get("realm_ref"))
            if request.provider_operation_ref != realm_ref:
                raise _terminal("provider_operation_ref_invalid")
            admin_access = _admin_access(
                self._transport,
                endpoint=endpoint,
                realm_ref=realm_ref,
                held_material=admin_credential,
            )
            observed = _send(
                self._transport,
                KeycloakAdminRequest(
                    "GET", endpoint, _realm_path(realm_ref), admin_access
                ),
                expected=frozenset({200}),
                mutation=False,
                provider_operation_ref=realm_ref,
            )
            return _result(
                ProvisionResultStatus.SUCCEEDED,
                provider_operation_ref=realm_ref,
                evidence=_realm_evidence(_json_object(observed), realm_ref),
            )
        except _ConnectorRefused as exc:
            return exc.result

    def cancel(self, request: ProvisionCancelRequest) -> ProvisioningResult:
        try:
            _require_capability(request.capability_id, REALM_CAPABILITY)
            realm_ref = _safe_ref(request.target.get("realm_ref"))
            if request.provider_operation_ref != realm_ref:
                raise _terminal("provider_operation_ref_invalid")
            return _result(
                ProvisionResultStatus.CANCELLED,
                provider_operation_ref=realm_ref,
                evidence={"cancelled": False, "realm_ref": realm_ref},
            )
        except _ConnectorRefused as exc:
            return exc.result


def _client_payload(
    target: Mapping[str, object], *, client_material: str
) -> Mapping[str, object]:
    redirect_uris = target.get("redirect_uris")
    if not isinstance(redirect_uris, list | tuple) or not all(
        isinstance(item, str) for item in redirect_uris
    ):
        raise _terminal("target_invalid")
    return {
        "attributes": {
            "dotmac.client_ref": _safe_ref(target.get("client_ref")),
            "id.token.signed.response.alg": "RS256",
            "pkce.code.challenge.method": "S256",
        },
        "clientAuthenticatorType": "client-secret",
        "clientId": _required_string(target, "client_id"),
        "directAccessGrantsEnabled": False,
        "enabled": True,
        "implicitFlowEnabled": False,
        "protocol": "openid-connect",
        "publicClient": False,
        "redirectUris": list(redirect_uris),
        "secret": client_material,
        "serviceAccountsEnabled": False,
        "standardFlowEnabled": True,
    }


def _audience_mapper(audience: str) -> Mapping[str, object]:
    return {
        "config": {
            "access.token.claim": "true",
            "id.token.claim": "true",
            "included.client.audience": audience,
        },
        "name": _AUDIENCE_MAPPER_NAME,
        "protocol": "openid-connect",
        "protocolMapper": "oidc-audience-mapper",
    }


def _find_client(
    transport: KeycloakAdminTransport,
    *,
    endpoint: str,
    admin_material: str,
    realm: str,
    client_id: str,
) -> Mapping[str, object] | None:
    response = _send(
        transport,
        KeycloakAdminRequest(
            "GET",
            endpoint,
            _realm_path(realm, "/clients"),
            admin_material,
            query={"clientId": client_id},
        ),
        expected=frozenset({200}),
        mutation=False,
    )
    matches = tuple(
        item for item in _json_objects(response) if item.get("clientId") == client_id
    )
    if len(matches) > 1:
        raise _terminal("provider_identity_ambiguous")
    return matches[0] if matches else None


def _require_owned_client(
    document: Mapping[str, object], *, expected_client_ref: str
) -> None:
    attributes = document.get("attributes")
    if not isinstance(attributes, Mapping):
        raise _terminal("provider_identity_collision")
    if attributes.get("dotmac.client_ref") != expected_client_ref:
        raise _terminal("provider_identity_collision")


def _client_internal_ref(document: Mapping[str, object]) -> str:
    value = document.get("id")
    if not isinstance(value, str) or _INTERNAL_REF_RE.fullmatch(value) is None:
        raise _terminal("provider_response_invalid")
    return value


def _ensure_mapper(
    transport: KeycloakAdminTransport,
    *,
    endpoint: str,
    admin_material: str,
    realm: str,
    client_ref: str,
    audience: str,
    provider_operation_ref: str,
) -> None:
    path = _realm_path(realm, f"/clients/{client_ref}/protocol-mappers/models")
    response = _send(
        transport,
        KeycloakAdminRequest("GET", endpoint, path, admin_material),
        expected=frozenset({200}),
        mutation=False,
        provider_operation_ref=provider_operation_ref,
    )
    matches = tuple(
        item
        for item in _json_objects(response)
        if item.get("name") == _AUDIENCE_MAPPER_NAME
    )
    if len(matches) > 1:
        raise _terminal("provider_identity_ambiguous")
    payload = _audience_mapper(audience)
    if not matches:
        method = "POST"
        target_path = path
        expected = frozenset({200, 201, 204})
    else:
        mapper_ref = _client_internal_ref(matches[0])
        method = "PUT"
        target_path = f"{path}/{mapper_ref}"
        expected = frozenset({200, 204})
    _send(
        transport,
        KeycloakAdminRequest(
            method,
            endpoint,
            target_path,
            admin_material,
            document=payload,
        ),
        expected=expected,
        mutation=True,
        provider_operation_ref=provider_operation_ref,
    )


def _client_evidence(
    document: Mapping[str, object],
    mappers: tuple[Mapping[str, object], ...],
    *,
    expected_client_ref: str,
    issuer: str,
) -> Mapping[str, object]:
    attributes = document.get("attributes")
    redirect_uris = document.get("redirectUris")
    if not isinstance(attributes, Mapping) or not isinstance(redirect_uris, list):
        raise _terminal("provider_response_invalid")
    mapper_matches = tuple(
        mapper for mapper in mappers if mapper.get("name") == _AUDIENCE_MAPPER_NAME
    )
    if len(mapper_matches) != 1:
        raise _terminal("security_posture_mismatch")
    mapper_config = mapper_matches[0].get("config")
    if not isinstance(mapper_config, Mapping):
        raise _terminal("provider_response_invalid")
    audience = mapper_config.get("included.client.audience")
    client_id = document.get("clientId")
    if not isinstance(audience, str) or not isinstance(client_id, str):
        raise _terminal("provider_response_invalid")
    secure = (
        document.get("enabled") is True
        and document.get("publicClient") is False
        and document.get("clientAuthenticatorType") == "client-secret"
        and document.get("standardFlowEnabled") is True
        and attributes.get("dotmac.client_ref") == expected_client_ref
        and attributes.get("pkce.code.challenge.method") == "S256"
        and attributes.get("id.token.signed.response.alg") == "RS256"
        and mapper_config.get("id.token.claim") == "true"
    )
    if not secure:
        raise _terminal("security_posture_mismatch")
    evidence: dict[str, object] = {
        "audience": audience,
        "authorization_code_enabled": True,
        "client_authentication_required": True,
        "client_id": client_id,
        "client_ref": expected_client_ref,
        "client_secret_configured": True,  # nosec B105 -- typed public evidence
        "discovery_url": f"{issuer}/.well-known/openid-configuration",
        "id_token_signing_algorithm": "RS256",  # nosec B105 -- typed public evidence
        "issuer_url": issuer,
        "pkce_method": "S256",
        "redirect_uris": redirect_uris,
        "require_aud_azp_validation": True,
    }
    evidence["observed_configuration_digest"] = _canonical_digest(evidence)
    return evidence


class _ClientLifecycleHandler:
    __slots__ = ("_transport",)

    def __init__(self, transport: KeycloakAdminTransport) -> None:
        self._transport = transport

    def plan(self, request: ProvisionPlanRequest) -> ProvisionPlanResult:
        step = _one_plan_step(request, CLIENT_CAPABILITY)
        _admin_endpoint(request.config)
        _admin_material(request.secrets)
        _material(request.secrets, _CLIENT_MATERIAL_FIELD)
        client_ref = _safe_ref(step.input.get("client_ref"))
        _realm_from_issuer(_required_string(step.input, "issuer_url"))
        return ProvisionPlanResult(
            plan_hash=request.plan_hash,
            steps=request.steps,
            evidence={"changes": ["reconcile"], "client_ref": client_ref},
        )

    def apply(self, request: ProvisionApplyRequest) -> ProvisioningResult:
        mutated = False
        try:
            _require_capability(request.capability_id, CLIENT_CAPABILITY)
            _require_step(request.step, request.capability_id)
            target = request.step.input
            endpoint = _admin_endpoint(request.config)
            admin_credential = _admin_material(request.secrets)
            client_material = _material(request.secrets, _CLIENT_MATERIAL_FIELD)
            client_ref = _safe_ref(target.get("client_ref"))
            client_id = _required_string(target, "client_id")
            audience = _required_string(target, "audience")
            realm, issuer = _realm_from_issuer(_required_string(target, "issuer_url"))
            admin_access = _admin_access(
                self._transport,
                endpoint=endpoint,
                realm_ref=realm,
                held_material=admin_credential,
            )
            observed_realm = _send(
                self._transport,
                KeycloakAdminRequest("GET", endpoint, _realm_path(realm), admin_access),
                expected=frozenset({200}),
                mutation=False,
            )
            if _realm_issuer(_json_object(observed_realm), realm) != issuer:
                raise _terminal("issuer_url_mismatch")
            payload = _client_payload(target, client_material=client_material)
            existing = _find_client(
                self._transport,
                endpoint=endpoint,
                admin_material=admin_access,
                realm=realm,
                client_id=client_id,
            )
            if existing is None:
                response = _send(
                    self._transport,
                    KeycloakAdminRequest(
                        "POST",
                        endpoint,
                        _realm_path(realm, "/clients"),
                        admin_access,
                        document=payload,
                    ),
                    expected=frozenset({200, 201, 204}),
                    mutation=True,
                )
                mutated = True
                internal_ref = _location_ref(response.location)
                if internal_ref is None:
                    created = _find_client(
                        self._transport,
                        endpoint=endpoint,
                        admin_material=admin_access,
                        realm=realm,
                        client_id=client_id,
                    )
                    if created is None:
                        raise _terminal("provider_response_invalid")
                    internal_ref = _client_internal_ref(created)
            else:
                _require_owned_client(existing, expected_client_ref=client_ref)
                internal_ref = _client_internal_ref(existing)
                _send(
                    self._transport,
                    KeycloakAdminRequest(
                        "PUT",
                        endpoint,
                        _realm_path(realm, f"/clients/{internal_ref}"),
                        admin_access,
                        document=payload,
                    ),
                    expected=frozenset({200, 204}),
                    mutation=True,
                )
                mutated = True
            provider_ref = _provider_ref(realm=realm, client=internal_ref)
            _ensure_mapper(
                self._transport,
                endpoint=endpoint,
                admin_material=admin_access,
                realm=realm,
                client_ref=internal_ref,
                audience=audience,
                provider_operation_ref=provider_ref,
            )
            evidence = self._observe_evidence(
                endpoint=endpoint,
                admin_material=admin_access,
                realm=realm,
                internal_ref=internal_ref,
                client_ref=client_ref,
                issuer=issuer,
                provider_ref=provider_ref,
            )
            return _result(
                ProvisionResultStatus.SUCCEEDED,
                provider_operation_ref=provider_ref,
                evidence=evidence,
            )
        except _ConnectorRefused as exc:
            return _after_mutation(exc.result) if mutated else exc.result

    def _observe_evidence(
        self,
        *,
        endpoint: str,
        admin_material: str,
        realm: str,
        internal_ref: str,
        client_ref: str,
        issuer: str,
        provider_ref: str,
    ) -> Mapping[str, object]:
        client = _send(
            self._transport,
            KeycloakAdminRequest(
                "GET",
                endpoint,
                _realm_path(realm, f"/clients/{internal_ref}"),
                admin_material,
            ),
            expected=frozenset({200}),
            mutation=False,
            provider_operation_ref=provider_ref,
        )
        mappers = _send(
            self._transport,
            KeycloakAdminRequest(
                "GET",
                endpoint,
                _realm_path(realm, f"/clients/{internal_ref}/protocol-mappers/models"),
                admin_material,
            ),
            expected=frozenset({200}),
            mutation=False,
            provider_operation_ref=provider_ref,
        )
        return _client_evidence(
            _json_object(client),
            _json_objects(mappers),
            expected_client_ref=client_ref,
            issuer=issuer,
        )

    def observe(self, request: ProvisionObserveRequest) -> ProvisioningResult:
        try:
            _require_capability(request.capability_id, CLIENT_CAPABILITY)
            endpoint = _admin_endpoint(request.config)
            admin_credential = _admin_material(request.secrets)
            client_ref = _safe_ref(request.target.get("client_ref"))
            realm, internal_ref = _parse_provider_ref(request.provider_operation_ref)
            admin_access = _admin_access(
                self._transport,
                endpoint=endpoint,
                realm_ref=realm,
                held_material=admin_credential,
            )
            observed_realm = _send(
                self._transport,
                KeycloakAdminRequest("GET", endpoint, _realm_path(realm), admin_access),
                expected=frozenset({200}),
                mutation=False,
                provider_operation_ref=request.provider_operation_ref,
            )
            issuer = _realm_issuer(_json_object(observed_realm), realm)
            evidence = self._observe_evidence(
                endpoint=endpoint,
                admin_material=admin_access,
                realm=realm,
                internal_ref=internal_ref,
                client_ref=client_ref,
                issuer=issuer,
                provider_ref=request.provider_operation_ref,
            )
            return _result(
                ProvisionResultStatus.SUCCEEDED,
                provider_operation_ref=request.provider_operation_ref,
                evidence=evidence,
            )
        except _ConnectorRefused as exc:
            return exc.result

    def cancel(self, request: ProvisionCancelRequest) -> ProvisioningResult:
        try:
            _require_capability(request.capability_id, CLIENT_CAPABILITY)
            client_ref = _safe_ref(request.target.get("client_ref"))
            _parse_provider_ref(request.provider_operation_ref)
            return _result(
                ProvisionResultStatus.CANCELLED,
                provider_operation_ref=request.provider_operation_ref,
                evidence={"cancelled": False, "client_ref": client_ref},
            )
        except _ConnectorRefused as exc:
            return exc.result


def _user_attribute(document: Mapping[str, object], name: str) -> str:
    attributes = document.get("attributes")
    if not isinstance(attributes, Mapping):
        raise _terminal("provider_identity_collision")
    value = attributes.get(name)
    if isinstance(value, str):
        return value
    if (
        isinstance(value, list | tuple)
        and len(value) == 1
        and isinstance(value[0], str)
    ):
        return value[0]
    raise _terminal("provider_identity_collision")


def _optional_user_attribute(document: Mapping[str, object], name: str) -> str | None:
    attributes = document.get("attributes")
    if not isinstance(attributes, Mapping):
        raise _terminal("provider_identity_collision")
    value = attributes.get(name)
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if (
        isinstance(value, list | tuple)
        and len(value) == 1
        and isinstance(value[0], str)
    ):
        return value[0]
    raise _terminal("provider_identity_collision")


def _find_user(
    transport: KeycloakAdminTransport,
    *,
    endpoint: str,
    admin_material: str,
    realm: str,
    identity_ref: str,
) -> Mapping[str, object] | None:
    response = _send(
        transport,
        KeycloakAdminRequest(
            "GET",
            endpoint,
            _realm_path(realm, "/users"),
            admin_material,
            query={
                "briefRepresentation": "false",
                "exact": "true",
                "max": "2",
                "q": f"dotmac.identity_ref:{identity_ref}",
            },
        ),
        expected=frozenset({200}),
        mutation=False,
    )
    matches = tuple(
        item
        for item in _json_objects(response)
        if _user_attribute(item, "dotmac.identity_ref") == identity_ref
    )
    if len(matches) > 1:
        raise _terminal("provider_identity_ambiguous")
    return matches[0] if matches else None


def _user_internal_ref(document: Mapping[str, object]) -> str:
    value = document.get("id")
    if not isinstance(value, str) or _INTERNAL_REF_RE.fullmatch(value) is None:
        raise _terminal("provider_response_invalid")
    return value


def _enrollment_parameters(
    target: Mapping[str, object],
) -> tuple[str, str, int, str]:
    client_id = _required_string(target, "enrollment_client_id")
    redirect_uri = _required_string(target, "enrollment_redirect_uri")
    try:
        parsed = urlsplit(redirect_uri)
    except ValueError:
        raise _terminal("target_invalid") from None
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise _terminal("target_invalid")
    lifespan = target.get("enrollment_lifespan_seconds")
    if type(lifespan) is not int or not 300 <= lifespan <= 86400:
        raise _terminal("target_invalid")
    revision = _safe_ref(target.get("enrollment_revision"))
    return client_id, redirect_uri, lifespan, revision


def _user_payload(
    target: Mapping[str, object],
    *,
    creating: bool,
    require_enrollment: bool,
    delivered_revision: str | None,
) -> Mapping[str, object]:
    desired_state = _required_string(target, "desired_lifecycle_state")
    if desired_state not in {"active", "disabled"}:
        raise _terminal("target_invalid")
    attributes: dict[str, object] = {
        "dotmac.identity_ref": [_safe_ref(target.get("identity_ref"))],
    }
    if delivered_revision is not None:
        attributes["dotmac.enrollment_revision"] = [delivered_revision]
    payload: dict[str, object] = {
        "attributes": attributes,
        "email": _required_string(target, "email_address"),
        "enabled": desired_state == "active",
        "firstName": _required_string(target, "given_name"),
        "lastName": _required_string(target, "family_name"),
        "username": _required_string(target, "login_name"),
    }
    # Initial credentials are never transported by this connector. A newly
    # created account must complete provider-owned email verification and
    # password enrolment before it can authenticate. Omitting this key on a
    # later update preserves completed actions instead of re-enrolling users.
    if creating:
        payload["emailVerified"] = False
    if require_enrollment:
        payload["requiredActions"] = ["UPDATE_PASSWORD", "VERIFY_EMAIL"]
    return payload


def _user_evidence(
    document: Mapping[str, object],
    *,
    expected_identity_ref: str,
    issuer: str,
    realm: str,
    sessions_revoked: bool,
) -> Mapping[str, object]:
    if _user_attribute(document, "dotmac.identity_ref") != expected_identity_ref:
        raise _terminal("provider_identity_collision")
    subject = _user_internal_ref(document)
    username = document.get("username")
    email = document.get("email")
    given_name = document.get("firstName")
    family_name = document.get("lastName")
    enabled = document.get("enabled")
    email_verified = document.get("emailVerified")
    required_actions = document.get("requiredActions", [])
    if (
        not isinstance(username, str)
        or not isinstance(email, str)
        or not isinstance(given_name, str)
        or not isinstance(family_name, str)
        or not isinstance(enabled, bool)
        or not isinstance(email_verified, bool)
        or not isinstance(required_actions, list)
        or not all(isinstance(item, str) for item in required_actions)
    ):
        raise _terminal("provider_response_invalid")
    evidence: dict[str, object] = {
        "credential_enrollment_pending": bool(required_actions),
        "email_address": email,
        "email_verified": email_verified,
        "family_name": family_name,
        "given_name": given_name,
        "identity_ref": expected_identity_ref,
        "issuer_url": issuer,
        "lifecycle_state": "active" if enabled else "disabled",
        "login_name": username,
        "realm_ref": realm,
        "sessions_revoked": sessions_revoked,
        "subject": subject,
    }
    evidence["observed_configuration_digest"] = _canonical_digest(evidence)
    return evidence


class _UserLifecycleHandler:
    __slots__ = ("_transport",)

    def __init__(self, transport: KeycloakAdminTransport) -> None:
        self._transport = transport

    def plan(self, request: ProvisionPlanRequest) -> ProvisionPlanResult:
        step = _one_plan_step(request, USER_CAPABILITY)
        _admin_endpoint(request.config)
        _admin_material(request.secrets)
        identity_ref = _safe_ref(step.input.get("identity_ref"))
        realm, _ = _realm_from_issuer(_required_string(step.input, "issuer_url"))
        if realm != _safe_ref(step.input.get("realm_ref")):
            raise _terminal("issuer_url_mismatch")
        _enrollment_parameters(step.input)
        _user_payload(
            step.input,
            creating=True,
            require_enrollment=True,
            delivered_revision=None,
        )
        return ProvisionPlanResult(
            plan_hash=request.plan_hash,
            steps=request.steps,
            evidence={"changes": ["reconcile"], "identity_ref": identity_ref},
        )

    def apply(self, request: ProvisionApplyRequest) -> ProvisioningResult:
        mutated = False
        provider_ref: str | None = None
        try:
            _require_capability(request.capability_id, USER_CAPABILITY)
            _require_step(request.step, request.capability_id)
            target = request.step.input
            endpoint = _admin_endpoint(request.config)
            admin_credential = _admin_material(request.secrets)
            identity_ref = _safe_ref(target.get("identity_ref"))
            realm, issuer = _realm_from_issuer(_required_string(target, "issuer_url"))
            if realm != _safe_ref(target.get("realm_ref")):
                raise _terminal("issuer_url_mismatch")
            admin_access = _admin_access(
                self._transport,
                endpoint=endpoint,
                realm_ref=realm,
                held_material=admin_credential,
            )
            observed_realm = _send(
                self._transport,
                KeycloakAdminRequest("GET", endpoint, _realm_path(realm), admin_access),
                expected=frozenset({200}),
                mutation=False,
            )
            if _realm_issuer(_json_object(observed_realm), realm) != issuer:
                raise _terminal("issuer_url_mismatch")
            existing = _find_user(
                self._transport,
                endpoint=endpoint,
                admin_material=admin_access,
                realm=realm,
                identity_ref=identity_ref,
            )
            enrollment_client_id, enrollment_redirect_uri, lifespan, revision = (
                _enrollment_parameters(target)
            )
            delivered_revision = (
                None
                if existing is None
                else _optional_user_attribute(existing, "dotmac.enrollment_revision")
            )
            needs_enrollment = (
                _required_string(target, "desired_lifecycle_state") == "active"
                and delivered_revision != revision
            )
            if existing is None:
                response = _send(
                    self._transport,
                    KeycloakAdminRequest(
                        "POST",
                        endpoint,
                        _realm_path(realm, "/users"),
                        admin_access,
                        document=_user_payload(
                            target,
                            creating=True,
                            require_enrollment=needs_enrollment,
                            delivered_revision=None,
                        ),
                    ),
                    expected=frozenset({201}),
                    mutation=True,
                )
                mutated = True
                subject = _location_ref(response.location)
                if subject is None:
                    created = _find_user(
                        self._transport,
                        endpoint=endpoint,
                        admin_material=admin_access,
                        realm=realm,
                        identity_ref=identity_ref,
                    )
                    if created is None:
                        raise _terminal("provider_response_invalid")
                    subject = _user_internal_ref(created)
            else:
                subject = _user_internal_ref(existing)
                _send(
                    self._transport,
                    KeycloakAdminRequest(
                        "PUT",
                        endpoint,
                        _realm_path(realm, f"/users/{subject}"),
                        admin_access,
                        document=_user_payload(
                            target,
                            creating=False,
                            require_enrollment=needs_enrollment,
                            delivered_revision=delivered_revision,
                        ),
                    ),
                    expected=frozenset({204}),
                    mutation=True,
                )
                mutated = True
            provider_ref = _user_provider_ref(realm=realm, subject=subject)
            if needs_enrollment:
                _send(
                    self._transport,
                    KeycloakAdminRequest(
                        "PUT",
                        endpoint,
                        _realm_path(realm, f"/users/{subject}/execute-actions-email"),
                        admin_access,
                        query={
                            "client_id": enrollment_client_id,
                            "lifespan": str(lifespan),
                            "redirect_uri": enrollment_redirect_uri,
                        },
                        document=("UPDATE_PASSWORD", "VERIFY_EMAIL"),
                    ),
                    expected=frozenset({204}),
                    mutation=True,
                    provider_operation_ref=provider_ref,
                )
                _send(
                    self._transport,
                    KeycloakAdminRequest(
                        "PUT",
                        endpoint,
                        _realm_path(realm, f"/users/{subject}"),
                        admin_access,
                        document=_user_payload(
                            target,
                            creating=False,
                            require_enrollment=True,
                            delivered_revision=revision,
                        ),
                    ),
                    expected=frozenset({204}),
                    mutation=True,
                    provider_operation_ref=provider_ref,
                )
            sessions_revoked = False
            if _required_string(target, "desired_lifecycle_state") == "disabled":
                _send(
                    self._transport,
                    KeycloakAdminRequest(
                        "POST",
                        endpoint,
                        _realm_path(realm, f"/users/{subject}/logout"),
                        admin_access,
                    ),
                    expected=frozenset({204}),
                    mutation=True,
                    provider_operation_ref=provider_ref,
                )
                sessions_revoked = True
            observed = _send(
                self._transport,
                KeycloakAdminRequest(
                    "GET",
                    endpoint,
                    _realm_path(realm, f"/users/{subject}"),
                    admin_access,
                ),
                expected=frozenset({200}),
                mutation=False,
                provider_operation_ref=provider_ref,
            )
            evidence = _user_evidence(
                _json_object(observed),
                expected_identity_ref=identity_ref,
                issuer=issuer,
                realm=realm,
                sessions_revoked=sessions_revoked,
            )
            if evidence["lifecycle_state"] != target["desired_lifecycle_state"]:
                raise _terminal("security_posture_mismatch")
            return _result(
                ProvisionResultStatus.SUCCEEDED,
                provider_operation_ref=provider_ref,
                evidence=evidence,
            )
        except _ConnectorRefused as exc:
            result = exc.result
            if provider_ref is not None and result.provider_operation_ref is None:
                result = _result(
                    result.status,
                    error_code=result.error_code,
                    provider_operation_ref=provider_ref,
                )
            return _after_mutation(result) if mutated else result

    def observe(self, request: ProvisionObserveRequest) -> ProvisioningResult:
        try:
            _require_capability(request.capability_id, USER_CAPABILITY)
            endpoint = _admin_endpoint(request.config)
            admin_credential = _admin_material(request.secrets)
            identity_ref = _safe_ref(request.target.get("identity_ref"))
            realm, issuer = _realm_from_issuer(
                _required_string(request.target, "issuer_url")
            )
            if realm != _safe_ref(request.target.get("realm_ref")):
                raise _terminal("issuer_url_mismatch")
            ref_realm, subject = _parse_user_provider_ref(
                request.provider_operation_ref
            )
            if ref_realm != realm:
                raise _terminal("provider_operation_ref_invalid")
            admin_access = _admin_access(
                self._transport,
                endpoint=endpoint,
                realm_ref=realm,
                held_material=admin_credential,
            )
            observed = _send(
                self._transport,
                KeycloakAdminRequest(
                    "GET",
                    endpoint,
                    _realm_path(realm, f"/users/{subject}"),
                    admin_access,
                ),
                expected=frozenset({200}),
                mutation=False,
                provider_operation_ref=request.provider_operation_ref,
            )
            return _result(
                ProvisionResultStatus.SUCCEEDED,
                provider_operation_ref=request.provider_operation_ref,
                evidence=_user_evidence(
                    _json_object(observed),
                    expected_identity_ref=identity_ref,
                    issuer=issuer,
                    realm=realm,
                    sessions_revoked=False,
                ),
            )
        except _ConnectorRefused as exc:
            return exc.result

    def cancel(self, request: ProvisionCancelRequest) -> ProvisioningResult:
        try:
            _require_capability(request.capability_id, USER_CAPABILITY)
            identity_ref = _safe_ref(request.target.get("identity_ref"))
            realm, _ = _realm_from_issuer(
                _required_string(request.target, "issuer_url")
            )
            if realm != _safe_ref(request.target.get("realm_ref")):
                raise _terminal("issuer_url_mismatch")
            ref_realm, subject = _parse_user_provider_ref(
                request.provider_operation_ref
            )
            if ref_realm != realm:
                raise _terminal("provider_operation_ref_invalid")
            return _result(
                ProvisionResultStatus.CANCELLED,
                provider_operation_ref=request.provider_operation_ref,
                evidence={
                    "cancelled": False,
                    "identity_ref": identity_ref,
                    "subject": subject,
                },
            )
        except _ConnectorRefused as exc:
            return exc.result


class KeycloakAdminConnector:
    """One stateless SPI plugin with constructor-injected provider transport."""

    __slots__ = ("_handlers", "_transport")

    def __init__(self, transport: KeycloakAdminTransport | None = None) -> None:
        selected = transport if transport is not None else HttpxKeycloakTransport()
        self._transport = selected
        self._handlers: Mapping[str, ProvisioningHandler] = {
            CLIENT_CAPABILITY: _ClientLifecycleHandler(selected),
            REALM_CAPABILITY: _RealmLifecycleHandler(selected),
            USER_CAPABILITY: _UserLifecycleHandler(selected),
        }

    def __repr__(self) -> str:
        return "KeycloakAdminConnector()"

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
            _admin_endpoint(config)
        except _ConnectorRefused:
            return (Diagnostic(ok=False, code="admin_endpoint_invalid"),)
        try:
            _admin_material(cast(Mapping[str, str], secrets))
        except _ConnectorRefused as exc:
            return (Diagnostic(ok=False, code=exc.result.error_code or "invalid"),)
        return (Diagnostic(ok=True, code="configuration_valid"),)


PLUGIN: Final = KeycloakAdminConnector()

__all__ = ["MANIFEST", "PLUGIN", "KeycloakAdminConnector"]
