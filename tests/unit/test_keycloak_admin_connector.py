"""Keycloak Admin connector acceptance against an injected transport."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for package in (
    "dotmac-kernel",
    "dotmac-integration",
    "dotmac-managed-identity-contracts",
    "dotmac-connector-keycloak-admin",
):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))

from dotmac_connector_keycloak_admin import (  # noqa: E402
    MANIFEST,
    PLUGIN,
    HttpxKeycloakTransport,
    KeycloakAdminConnector,
    KeycloakAdminRequest,
    KeycloakAdminResponse,
    KeycloakTransportError,
)
from dotmac_integration.conformance import assert_plugin_conforms  # noqa: E402
from dotmac_integration.spi import (  # noqa: E402
    ConnectorMode,
    ProvisionApplyRequest,
    ProvisionCancelRequest,
    ProvisionObserveRequest,
    ProvisionPlanRequest,
    ProvisionResultStatus,
    ProvisionStep,
)

REALM_CAPABILITY = "identity.realm.lifecycle.v1"
CLIENT_CAPABILITY = "identity.oidc-client.lifecycle.v1"
USER_CAPABILITY = "identity.user.lifecycle.v1"
ADMIN_ENDPOINT = "https://admin.idp.internal"
ISSUER = "https://idp.example.net/realms/customer-a"
ADMIN_CREDENTIAL_SECRET = "admin-client-secret-value"
ADMIN_MATERIAL = json.dumps(
    {
        "client_id": "dotmac-integrator",
        "client_secret": ADMIN_CREDENTIAL_SECRET,
    },
    separators=(",", ":"),
    sort_keys=True,
)
ADMIN_ACCESS_TOKEN = "admin-access-token-value"
CLIENT_MATERIAL = "client-material-value"


@dataclass
class ScriptedTransport:
    responses: list[KeycloakAdminResponse | KeycloakTransportError]
    requests: list[KeycloakAdminRequest] = field(default_factory=list)
    token_requests: list[tuple[str, str]] = field(default_factory=list)
    token_result: str | KeycloakTransportError = ADMIN_ACCESS_TOKEN

    def admin_access_token(
        self, *, base_endpoint: str, realm_ref: str, held_material: str
    ) -> str:
        assert held_material == ADMIN_MATERIAL
        self.token_requests.append((base_endpoint, realm_ref))
        if isinstance(self.token_result, KeycloakTransportError):
            raise self.token_result
        return self.token_result

    def request(self, request: KeycloakAdminRequest) -> KeycloakAdminResponse:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("unexpected transport request")
        result = self.responses.pop(0)
        if isinstance(result, KeycloakTransportError):
            raise result
        return result


def _response(
    status: int, payload: object | None = None, *, location: str | None = None
) -> KeycloakAdminResponse:
    body = b"" if payload is None else json.dumps(payload).encode("utf-8")
    return KeycloakAdminResponse(status_code=status, body=body, location=location)


def _realm_document() -> dict[str, object]:
    return {
        "attributes": {"frontendUrl": ISSUER},
        "realm": "customer-a",
        "displayName": "Customer A",
        "enabled": True,
        "defaultSignatureAlgorithm": "RS256",
    }


def _client_document() -> dict[str, object]:
    return {
        "id": "provider-client-17",
        "clientId": "workspace",
        "enabled": True,
        "publicClient": False,
        "clientAuthenticatorType": "client-secret",
        "standardFlowEnabled": True,
        "redirectUris": ["https://workspace.example.net/login/callback"],
        "attributes": {
            "dotmac.client_ref": "workspace-client",
            "pkce.code.challenge.method": "S256",
            "id.token.signed.response.alg": "RS256",
        },
    }


def _mapper_document() -> dict[str, object]:
    return {
        "id": "mapper-3",
        "name": "dotmac-audience",
        "protocol": "openid-connect",
        "protocolMapper": "oidc-audience-mapper",
        "config": {
            "included.client.audience": "workspace",
            "id.token.claim": "true",
            "access.token.claim": "true",
        },
    }


def _user_document(*, enabled: bool = True) -> dict[str, object]:
    return {
        "attributes": {"dotmac.identity_ref": ["person-17"]},
        "email": "person@example.net",
        "emailVerified": False,
        "enabled": enabled,
        "firstName": "Example",
        "id": "8b707dab-ec50-4a2e-a7dc-896e411cfdf1",
        "lastName": "Person",
        "requiredActions": ["UPDATE_PASSWORD", "VERIFY_EMAIL"],
        "username": "person.17",
    }


def _step(capability_id: str, target: Mapping[str, object]) -> ProvisionStep:
    return ProvisionStep(
        step_key="identity-1",
        endpoint_code=capability_id,
        depends_on=(),
        input=target,
    )


def _realm_target() -> dict[str, object]:
    return {
        "display_name": "Customer A",
        "public_hostname": "idp.example.net",
        "realm_ref": "customer-a",
    }


def _client_target() -> dict[str, object]:
    return {
        "audience": "workspace",
        "authorization_code_enabled": True,
        "client_authentication_required": True,
        "client_id": "workspace",
        "client_ref": "workspace-client",
        "id_token_signing_algorithm": "RS256",
        "issuer_url": ISSUER,
        "pkce_method": "S256",
        "redirect_uris": ["https://workspace.example.net/login/callback"],
        "require_aud_azp_validation": True,
    }


def _user_target(*, state: str = "active") -> dict[str, object]:
    return {
        "desired_lifecycle_state": state,
        "email_address": "person@example.net",
        "enrollment_client_id": "workspace",
        "enrollment_lifespan_seconds": 3600,
        "enrollment_redirect_uri": "https://workspace.example.net/applications",
        "enrollment_revision": "onboarding-1",
        "family_name": "Person",
        "given_name": "Example",
        "identity_ref": "person-17",
        "issuer_url": ISSUER,
        "login_name": "person.17",
        "realm_ref": "customer-a",
    }


def _config(capability_id: str, *, endpoint: str = ADMIN_ENDPOINT) -> dict[str, object]:
    config: dict[str, object] = {"admin_endpoint": endpoint}
    if capability_id == REALM_CAPABILITY:
        config["identity_policy_ref"] = "managed-identity-v1"
    return config


def _secrets(capability_id: str) -> dict[str, str]:
    values = {"admin_secret_ref": ADMIN_MATERIAL}
    if capability_id == CLIENT_CAPABILITY:
        values["client_secret_ref"] = CLIENT_MATERIAL
    return values


def _apply_request(
    capability_id: str,
    target: Mapping[str, object],
    *,
    endpoint: str = ADMIN_ENDPOINT,
) -> ProvisionApplyRequest:
    return ProvisionApplyRequest(
        capability_id=capability_id,
        command_id="cmd-1",
        operation_ref="operation-1",
        plan_hash="sha256:" + "1" * 64,
        step=_step(capability_id, target),
        config=_config(capability_id, endpoint=endpoint),
        secrets=_secrets(capability_id),
        idempotency_key="cmd-1/identity-1",
    )


def test_manifest_is_the_exact_three_capability_provision_plugin() -> None:
    assert MANIFEST.connector_key == "keycloak_admin"
    assert MANIFEST.version == "0.1.0a1"
    assert str(MANIFEST.spi_range) == ">=1.2,<2.0"
    assert tuple(item.capability_id for item in MANIFEST.capabilities) == (
        CLIENT_CAPABILITY,
        REALM_CAPABILITY,
        USER_CAPABILITY,
    )
    assert PLUGIN.modes == frozenset({ConnectorMode.PROVISION})
    assert_plugin_conforms(PLUGIN)


def test_plan_is_pure_and_preserves_the_signed_target() -> None:
    transport = ScriptedTransport([])
    connector = KeycloakAdminConnector(transport=transport)
    handler = connector.provisioning_handler_for(REALM_CAPABILITY)
    step = _step(REALM_CAPABILITY, _realm_target())
    request = ProvisionPlanRequest(
        capability_id=REALM_CAPABILITY,
        command_id="cmd-plan",
        plan_hash="sha256:" + "2" * 64,
        steps=(step,),
        config=_config(REALM_CAPABILITY),
        secrets=_secrets(REALM_CAPABILITY),
    )

    result = handler.plan(request)

    assert result.plan_hash == request.plan_hash
    assert result.steps == request.steps
    assert result.evidence == {"changes": ["reconcile"], "realm_ref": "customer-a"}
    assert transport.requests == []
    assert transport.token_requests == []


def test_handler_refuses_a_request_for_another_declared_capability() -> None:
    transport = ScriptedTransport([])
    realm_handler = KeycloakAdminConnector(
        transport=transport
    ).provisioning_handler_for(REALM_CAPABILITY)

    result = realm_handler.apply(_apply_request(CLIENT_CAPABILITY, _client_target()))

    assert result.status is ProvisionResultStatus.TERMINAL
    assert result.error_code == "capability_id_mismatch"
    assert transport.token_requests == []
    assert transport.requests == []


def test_realm_apply_updates_only_a_precreated_non_master_realm() -> None:
    transport = ScriptedTransport(
        [
            _response(200, _realm_document()),
            _response(204),
            _response(200, _realm_document()),
        ]
    )
    handler = KeycloakAdminConnector(transport=transport).provisioning_handler_for(
        REALM_CAPABILITY
    )

    result = handler.apply(_apply_request(REALM_CAPABILITY, _realm_target()))

    assert result.status is ProvisionResultStatus.SUCCEEDED
    assert result.provider_operation_ref == "customer-a"
    assert result.evidence["issuer_url"] == ISSUER
    assert result.evidence["signing_algorithm"] == "RS256"
    assert [request.method for request in transport.requests] == ["GET", "PUT", "GET"]
    assert {request.path for request in transport.requests} == {
        "/admin/realms/customer-a"
    }
    assert transport.requests[1].document == {
        "attributes": {"frontendUrl": ISSUER},
        "defaultSignatureAlgorithm": "RS256",
        "displayName": "Customer A",
        "enabled": True,
        "realm": "customer-a",
    }


def test_realm_creation_and_master_realm_authority_are_refused() -> None:
    absent = ScriptedTransport([_response(404)])
    absent_result = (
        KeycloakAdminConnector(transport=absent)
        .provisioning_handler_for(REALM_CAPABILITY)
        .apply(_apply_request(REALM_CAPABILITY, _realm_target()))
    )
    assert absent_result.status is ProvisionResultStatus.TERMINAL
    assert absent_result.error_code == "realm_precreation_required"
    assert [request.path for request in absent.requests] == ["/admin/realms/customer-a"]

    master = ScriptedTransport([])
    result = (
        KeycloakAdminConnector(transport=master)
        .provisioning_handler_for(REALM_CAPABILITY)
        .apply(
            _apply_request(
                REALM_CAPABILITY,
                {**_realm_target(), "realm_ref": "master"},
            )
        )
    )
    assert result.status is ProvisionResultStatus.TERMINAL
    assert result.error_code == "master_realm_refused"
    assert master.requests == []
    assert master.token_requests == []


def test_client_apply_uses_held_material_and_returns_only_public_evidence() -> None:
    transport = ScriptedTransport(
        [
            _response(200, _realm_document()),
            _response(200, []),
            _response(
                201,
                location=(
                    "https://admin.idp.internal/admin/realms/customer-a/clients/"
                    "provider-client-17"
                ),
            ),
            _response(200, []),
            _response(201),
            _response(200, _client_document()),
            _response(200, [_mapper_document()]),
        ]
    )
    handler = KeycloakAdminConnector(transport=transport).provisioning_handler_for(
        CLIENT_CAPABILITY
    )

    result = handler.apply(_apply_request(CLIENT_CAPABILITY, _client_target()))

    assert result.status is ProvisionResultStatus.SUCCEEDED
    assert result.evidence["client_ref"] == "workspace-client"
    assert result.evidence["client_secret_configured"] is True
    assert result.evidence["id_token_signing_algorithm"] == "RS256"
    assert result.evidence["pkce_method"] == "S256"
    serialized = json.dumps(dict(result.evidence), sort_keys=True)
    assert ADMIN_MATERIAL not in serialized
    assert ADMIN_CREDENTIAL_SECRET not in serialized
    assert CLIENT_MATERIAL not in serialized
    create = transport.requests[2]
    assert create.path == "/admin/realms/customer-a/clients"
    assert create.document is not None
    assert create.document["secret"] == CLIENT_MATERIAL
    assert CLIENT_MATERIAL not in repr(create)
    assert ADMIN_MATERIAL not in repr(create)
    assert ADMIN_ACCESS_TOKEN not in repr(create)
    assert all(
        request.path.startswith("/admin/realms/customer-a")
        for request in transport.requests
    )


def test_existing_client_identity_must_match_the_dotmac_resource_ref() -> None:
    colliding = _client_document()
    colliding["attributes"] = {
        "dotmac.client_ref": "someone-elses-client",
        "id.token.signed.response.alg": "RS256",
        "pkce.code.challenge.method": "S256",
    }
    transport = ScriptedTransport(
        [
            _response(200, _realm_document()),
            _response(200, [colliding]),
        ]
    )

    result = (
        KeycloakAdminConnector(transport=transport)
        .provisioning_handler_for(CLIENT_CAPABILITY)
        .apply(_apply_request(CLIENT_CAPABILITY, _client_target()))
    )

    assert result.status is ProvisionResultStatus.TERMINAL
    assert result.error_code == "provider_identity_collision"
    assert [request.method for request in transport.requests] == ["GET", "GET"]


def test_user_apply_locates_only_by_stable_reference_and_returns_subject() -> None:
    subject = "8b707dab-ec50-4a2e-a7dc-896e411cfdf1"
    transport = ScriptedTransport(
        [
            _response(200, _realm_document()),
            _response(200, []),
            _response(
                201,
                location=(
                    "https://admin.idp.internal/admin/realms/customer-a/users/"
                    f"{subject}"
                ),
            ),
            _response(204),
            _response(204),
            _response(200, _user_document()),
        ]
    )
    result = (
        KeycloakAdminConnector(transport=transport)
        .provisioning_handler_for(USER_CAPABILITY)
        .apply(_apply_request(USER_CAPABILITY, _user_target()))
    )

    assert result.status is ProvisionResultStatus.SUCCEEDED
    assert result.evidence["identity_ref"] == "person-17"
    assert result.evidence["issuer_url"] == ISSUER
    assert result.evidence["subject"] == subject
    assert result.evidence["email_address"] == "person@example.net"
    lookup = transport.requests[1]
    assert lookup.query == {
        "briefRepresentation": "false",
        "exact": "true",
        "max": "2",
        "q": "dotmac.identity_ref:person-17",
    }
    create = transport.requests[2]
    assert create.document is not None
    assert create.document["attributes"] == {"dotmac.identity_ref": ["person-17"]}
    assert create.document["requiredActions"] == [
        "UPDATE_PASSWORD",
        "VERIFY_EMAIL",
    ]
    assert "credentials" not in create.document
    enrollment = transport.requests[3]
    assert enrollment.path.endswith(
        "/users/8b707dab-ec50-4a2e-a7dc-896e411cfdf1/execute-actions-email"
    )
    assert enrollment.query == {
        "client_id": "workspace",
        "lifespan": "3600",
        "redirect_uri": "https://workspace.example.net/applications",
    }
    assert enrollment.document == ("UPDATE_PASSWORD", "VERIFY_EMAIL")
    marker = transport.requests[4]
    assert marker.document is not None
    assert marker.document["attributes"] == {
        "dotmac.enrollment_revision": ["onboarding-1"],
        "dotmac.identity_ref": ["person-17"],
    }


def test_user_disable_revokes_provider_sessions_before_success() -> None:
    transport = ScriptedTransport(
        [
            _response(200, _realm_document()),
            _response(200, [_user_document()]),
            _response(204),
            _response(204),
            _response(200, _user_document(enabled=False)),
        ]
    )
    result = (
        KeycloakAdminConnector(transport=transport)
        .provisioning_handler_for(USER_CAPABILITY)
        .apply(_apply_request(USER_CAPABILITY, _user_target(state="disabled")))
    )

    assert result.status is ProvisionResultStatus.SUCCEEDED
    assert result.evidence["lifecycle_state"] == "disabled"
    assert result.evidence["sessions_revoked"] is True
    assert [request.method for request in transport.requests] == [
        "GET",
        "GET",
        "PUT",
        "POST",
        "GET",
    ]
    assert transport.requests[3].path.endswith(
        "/users/8b707dab-ec50-4a2e-a7dc-896e411cfdf1/logout"
    )


def test_user_identity_collision_is_refused_before_mutation() -> None:
    colliding = _user_document()
    colliding["attributes"] = {"dotmac.identity_ref": ["someone-else"]}
    transport = ScriptedTransport(
        [_response(200, _realm_document()), _response(200, [colliding])]
    )
    result = (
        KeycloakAdminConnector(transport=transport)
        .provisioning_handler_for(USER_CAPABILITY)
        .apply(_apply_request(USER_CAPABILITY, _user_target()))
    )

    assert result.status is ProvisionResultStatus.TERMINAL
    assert result.error_code == "provider_identity_collision"
    assert [request.method for request in transport.requests] == ["GET", "GET"]


def test_client_issuer_must_match_the_observed_realm_before_mutation() -> None:
    observed_realm = _realm_document()
    observed_realm["attributes"] = {
        "frontendUrl": "https://different.example.net/realms/customer-a"
    }
    transport = ScriptedTransport([_response(200, observed_realm)])

    result = (
        KeycloakAdminConnector(transport=transport)
        .provisioning_handler_for(CLIENT_CAPABILITY)
        .apply(_apply_request(CLIENT_CAPABILITY, _client_target()))
    )

    assert result.status is ProvisionResultStatus.TERMINAL
    assert result.error_code == "issuer_url_mismatch"
    assert [request.method for request in transport.requests] == ["GET"]


def test_replay_reconciles_the_owned_client_without_creating_a_duplicate() -> None:
    transport = ScriptedTransport(
        [
            _response(200, _realm_document()),
            _response(200, [_client_document()]),
            _response(204),
            _response(200, [_mapper_document()]),
            _response(204),
            _response(200, _client_document()),
            _response(200, [_mapper_document()]),
        ]
    )

    result = (
        KeycloakAdminConnector(transport=transport)
        .provisioning_handler_for(CLIENT_CAPABILITY)
        .apply(_apply_request(CLIENT_CAPABILITY, _client_target()))
    )

    assert result.status is ProvisionResultStatus.SUCCEEDED
    assert not any(
        request.method == "POST" and request.path.endswith("/clients")
        for request in transport.requests
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://admin.idp.internal",
        "https://user:pass@admin.idp.internal",
        "https://admin.idp.internal?next=https://evil.example",
        "https://admin.idp.internal/#fragment",
        "https://admin.idp.internal/%2e%2e/master",
    ],
)
def test_unsafe_admin_endpoint_is_refused_before_transport(endpoint: str) -> None:
    transport = ScriptedTransport([])
    result = (
        KeycloakAdminConnector(transport=transport)
        .provisioning_handler_for(REALM_CAPABILITY)
        .apply(_apply_request(REALM_CAPABILITY, _realm_target(), endpoint=endpoint))
    )
    assert result.status is ProvisionResultStatus.TERMINAL
    assert result.error_code == "admin_endpoint_invalid"
    assert transport.requests == []
    assert transport.token_requests == []


@pytest.mark.parametrize(
    ("provider_status", "expected_status", "expected_code"),
    [
        (302, ProvisionResultStatus.TERMINAL, "provider_redirect_refused"),
        (401, ProvisionResultStatus.TERMINAL, "admin_authentication_refused"),
        (403, ProvisionResultStatus.TERMINAL, "admin_authorization_refused"),
        (429, ProvisionResultStatus.RETRYABLE, "provider_rate_limited"),
        (503, ProvisionResultStatus.AMBIGUOUS, "provider_outcome_unknown"),
    ],
)
def test_mutating_outcomes_map_to_stable_provider_neutral_results(
    provider_status: int,
    expected_status: ProvisionResultStatus,
    expected_code: str,
) -> None:
    transport = ScriptedTransport(
        [_response(200, _realm_document()), _response(provider_status)]
    )
    result = (
        KeycloakAdminConnector(transport=transport)
        .provisioning_handler_for(REALM_CAPABILITY)
        .apply(_apply_request(REALM_CAPABILITY, _realm_target()))
    )
    assert result.status is expected_status
    assert result.error_code == expected_code
    assert result.error_detail is None


def test_unknown_mutation_result_is_ambiguous_and_material_free() -> None:
    transport = ScriptedTransport(
        [
            _response(200, _realm_document()),
            KeycloakTransportError("timeout"),
        ]
    )
    result = (
        KeycloakAdminConnector(transport=transport)
        .provisioning_handler_for(REALM_CAPABILITY)
        .apply(_apply_request(REALM_CAPABILITY, _realm_target()))
    )
    assert result.status is ProvisionResultStatus.AMBIGUOUS
    assert result.error_code == "provider_outcome_unknown"
    assert result.error_detail is None
    assert ADMIN_MATERIAL not in repr(result)
    assert ADMIN_ACCESS_TOKEN not in repr(result)


def test_admin_authentication_refusal_is_terminal_before_admin_rest() -> None:
    transport = ScriptedTransport(
        [], token_result=KeycloakTransportError("admin_authentication_refused")
    )

    result = (
        KeycloakAdminConnector(transport=transport)
        .provisioning_handler_for(REALM_CAPABILITY)
        .apply(_apply_request(REALM_CAPABILITY, _realm_target()))
    )

    assert result.status is ProvisionResultStatus.TERMINAL
    assert result.error_code == "admin_authentication_refused"
    assert result.error_detail is None
    assert transport.requests == []
    assert ADMIN_CREDENTIAL_SECRET not in repr(result)


def test_unreadable_post_mutation_state_is_ambiguous_not_blindly_retryable() -> None:
    transport = ScriptedTransport(
        [
            _response(200, _realm_document()),
            _response(204),
            KeycloakTransportError("timeout"),
        ]
    )

    result = (
        KeycloakAdminConnector(transport=transport)
        .provisioning_handler_for(REALM_CAPABILITY)
        .apply(_apply_request(REALM_CAPABILITY, _realm_target()))
    )

    assert result.status is ProvisionResultStatus.AMBIGUOUS
    assert result.error_code == "provider_outcome_unknown"
    assert result.provider_operation_ref == "customer-a"
    assert result.error_detail is None


def test_observe_reads_by_opaque_provider_ref_and_cancel_is_a_noop() -> None:
    transport = ScriptedTransport(
        [
            _response(200, _realm_document()),
            _response(200, _client_document()),
            _response(200, [_mapper_document()]),
        ]
    )
    handler = KeycloakAdminConnector(transport=transport).provisioning_handler_for(
        CLIENT_CAPABILITY
    )
    provider_ref = (
        "kc1.eyJjbGllbnQiOiJwcm92aWRlci1jbGllbnQtMTciLCJyZWFsbSI6ImN1c3RvbWVy" "LWEifQ"
    )
    observed = handler.observe(
        ProvisionObserveRequest(
            capability_id=CLIENT_CAPABILITY,
            command_id="cmd-observe",
            operation_ref="operation-1",
            plan_hash="sha256:" + "3" * 64,
            step_key="identity-1",
            provider_operation_ref=provider_ref,
            target={"client_ref": "workspace-client"},
            config=_config(CLIENT_CAPABILITY),
            secrets=_secrets(CLIENT_CAPABILITY),
        )
    )
    assert observed.status is ProvisionResultStatus.SUCCEEDED
    assert observed.evidence["issuer_url"] == ISSUER
    assert transport.requests[1].path == (
        "/admin/realms/customer-a/clients/provider-client-17"
    )

    cancelled = handler.cancel(
        ProvisionCancelRequest(
            capability_id=CLIENT_CAPABILITY,
            command_id="cmd-cancel",
            operation_ref="operation-1",
            plan_hash="sha256:" + "4" * 64,
            step_key="identity-1",
            provider_operation_ref=provider_ref,
            target={"client_ref": "workspace-client"},
            reason="operator_requested",
            idempotency_key="cmd-cancel/identity-1",
            config=_config(CLIENT_CAPABILITY),
            secrets=_secrets(CLIENT_CAPABILITY),
        )
    )
    assert cancelled.status is ProvisionResultStatus.CANCELLED
    assert cancelled.evidence == {
        "cancelled": False,
        "client_ref": "workspace-client",
    }
    assert len(transport.requests) == 3


def test_default_plugin_owns_real_transport_without_network_on_import() -> None:
    assert isinstance(PLUGIN, KeycloakAdminConnector)
    transport = PLUGIN._transport
    assert isinstance(transport, HttpxKeycloakTransport)
    assert "Client" not in repr(transport)


def test_transport_envelopes_never_render_material_and_bound_provider_bytes() -> None:
    request = KeycloakAdminRequest(
        "POST",
        ADMIN_ENDPOINT,
        "/admin/realms/customer-a/clients",
        ADMIN_ACCESS_TOKEN,
        document={"secret": CLIENT_MATERIAL},
    )
    assert ADMIN_MATERIAL not in repr(request)
    assert ADMIN_ACCESS_TOKEN not in repr(request)
    assert CLIENT_MATERIAL not in repr(request)

    with pytest.raises(KeycloakTransportError) as refusal:
        KeycloakAdminResponse(status_code=200, body=b"x" * 1_048_577)
    assert refusal.value.code == "response_too_large"


def test_real_transport_refuses_a_non_bundle_admin_credential_before_io() -> None:
    transport = HttpxKeycloakTransport()
    malformed = f"not-json:{ADMIN_CREDENTIAL_SECRET}"
    try:
        with pytest.raises(KeycloakTransportError) as refusal:
            transport.admin_access_token(
                base_endpoint=ADMIN_ENDPOINT,
                realm_ref="customer-a",
                held_material=malformed,
            )
    finally:
        transport.close()

    assert refusal.value.code == "admin_material_invalid"
    assert ADMIN_CREDENTIAL_SECRET not in repr(refusal.value)
