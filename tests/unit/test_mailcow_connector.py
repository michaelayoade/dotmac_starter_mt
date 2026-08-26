"""Mailcow connector acceptance over an injected, value-free transport."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for package in (
    "dotmac-kernel",
    "dotmac-integration",
    "dotmac-managed-email-contracts",
    "dotmac-connector-mailcow",
):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))

from dotmac_connector_mailcow import (  # noqa: E402
    MANIFEST,
    PLUGIN,
    HttpxMailcowTransport,
    MailcowConnector,
    MailcowRequest,
    MailcowResponse,
    MailcowTransportError,
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

CAPABILITY_ID = "email.lifecycle.v1"
ENDPOINT = "https://mail.example.net"
API_MATERIAL = "held-mailcow-api-material"


@dataclass
class ScriptedTransport:
    responses: list[MailcowResponse | MailcowTransportError]
    requests: list[MailcowRequest] = field(default_factory=list)

    def __bool__(self) -> bool:
        """Remain a valid injected collaborator even when deliberately falsey."""

        return False

    def request(self, request: MailcowRequest) -> MailcowResponse:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("unexpected Mailcow request")
        response = self.responses.pop(0)
        if isinstance(response, MailcowTransportError):
            raise response
        return response


def _response(status: int, payload: object) -> MailcowResponse:
    return MailcowResponse(
        status_code=status,
        body=json.dumps(payload, separators=(",", ":")).encode(),
    )


def _target(kind: str, **values: object) -> dict[str, object]:
    return {
        "application_ref": "mail-primary",
        "desired_lifecycle_state": "enabled",
        "resource_kind": kind,
        "resource_ref": f"{kind}-primary",
        **values,
    }


def _step(target: Mapping[str, object]) -> ProvisionStep:
    return ProvisionStep(
        step_key="email-1",
        endpoint_code=CAPABILITY_ID,
        depends_on=(),
        input=target,
    )


def _config() -> dict[str, object]:
    return {"admin_endpoint": ENDPOINT}


def _secrets() -> dict[str, str]:
    return {"admin_secret_ref": API_MATERIAL}


def _connection_secrets() -> dict[str, object]:
    return {"admin_secret_ref": API_MATERIAL}


def _apply(target: Mapping[str, object]) -> ProvisionApplyRequest:
    return ProvisionApplyRequest(
        capability_id=CAPABILITY_ID,
        command_id="command-1",
        operation_ref="operation-1",
        plan_hash="sha256:" + "1" * 64,
        step=_step(target),
        config=_config(),
        secrets=_secrets(),
        idempotency_key="operation-1/email-1",
    )


def test_manifest_is_one_exact_stateless_email_provision_plugin() -> None:
    assert MANIFEST.connector_key == "mailcow"
    assert MANIFEST.version == "0.1.0a1"
    assert str(MANIFEST.spi_range) == ">=1.2,<2.0"
    assert tuple(item.capability_id for item in MANIFEST.capabilities) == (
        CAPABILITY_ID,
    )
    assert PLUGIN.modes == frozenset({ConnectorMode.PROVISION})
    assert_plugin_conforms(PLUGIN)


def test_plan_is_pure_and_preserves_the_product_owned_target() -> None:
    target = _target("domain", domain_name="customer.example")
    transport = ScriptedTransport([])
    handler = MailcowConnector(transport).provisioning_handler_for(CAPABILITY_ID)
    request = ProvisionPlanRequest(
        capability_id=CAPABILITY_ID,
        command_id="plan-1",
        plan_hash="sha256:" + "2" * 64,
        steps=(_step(target),),
        config=_config(),
        secrets=_secrets(),
    )

    result = handler.plan(request)

    assert result.plan_hash == request.plan_hash
    assert result.steps == request.steps
    assert result.evidence == {
        "changes": ["reconcile"],
        "resource_kind": "domain",
        "resource_ref": "domain-primary",
    }
    assert transport.requests == []


def test_existing_mailbox_is_disabled_without_ever_carrying_a_password() -> None:
    target = _target(
        "mailbox",
        delivery_enabled=False,
        desired_lifecycle_state="disabled",
        domain_name="customer.example",
        mailbox_local_part="person",
        quota_bytes=1_073_741_824,
    )
    existing = {
        "active": 1,
        "domain": "customer.example",
        "local_part": "person",
        "quota": 1_073_741_824,
        "username": "person@customer.example",
    }
    disabled = {**existing, "active": 0}
    transport = ScriptedTransport(
        [
            _response(200, existing),
            _response(200, [{"type": "success", "msg": "mailbox_modified"}]),
            _response(200, disabled),
        ]
    )

    result = (
        MailcowConnector(transport)
        .provisioning_handler_for(CAPABILITY_ID)
        .apply(_apply(target))
    )

    assert result.status is ProvisionResultStatus.SUCCEEDED
    assert result.evidence["delivery_enabled"] is False
    assert result.evidence["mailbox_ref"] == "person@customer.example"
    assert [request.method for request in transport.requests] == ["GET", "POST", "GET"]
    mutation = transport.requests[1]
    assert mutation.path == "/api/v1/edit/mailbox"
    assert mutation.document == {
        "attr": {"active": "0", "quota": "1024"},
        "items": ["person@customer.example"],
    }
    rendered = json.dumps(dict(mutation.document or {}), sort_keys=True)
    assert "password" not in rendered.casefold()
    assert API_MATERIAL not in repr(mutation)


def test_mailbox_creation_uses_oidc_authsource_without_password_material() -> None:
    mailbox = _target(
        "mailbox",
        dav_access_enabled=True,
        delivery_enabled=True,
        domain_name="customer.example",
        eas_access_enabled=False,
        imap_access_enabled=True,
        mailbox_local_part="person",
        pop3_access_enabled=False,
        quota_bytes=1_073_741_824,
        sieve_access_enabled=True,
        smtp_access_enabled=True,
        webmail_access_enabled=True,
    )
    created = {
        "active": 1,
        "domain": "customer.example",
        "local_part": "person",
        "quota": 1_073_741_824,
        "username": "person@customer.example",
    }
    transport = ScriptedTransport(
        [
            _response(200, []),
            _response(200, [{"type": "success", "msg": "mailbox_added"}]),
            _response(200, created),
        ]
    )
    mailbox_result = (
        MailcowConnector(transport)
        .provisioning_handler_for(CAPABILITY_ID)
        .apply(_apply(mailbox))
    )

    assert mailbox_result.status is ProvisionResultStatus.SUCCEEDED
    assert [item.method for item in transport.requests] == ["GET", "POST", "GET"]
    mutation = transport.requests[1]
    assert mutation.path == "/api/v1/add/mailbox"
    assert mutation.document == {
        "active": "1",
        "authsource": "generic-oidc",
        "domain": "customer.example",
        "force_pw_update": "0",
        "force_tfa": "0",
        "local_part": "person",
        "name": "person",
        "protocol_access": ["imap", "smtp", "sieve", "dav"],
        "quota": "1024",
        "sogo_access": "1",
        "template": "",
    }
    assert "password" not in json.dumps(mutation.document).casefold()


def test_app_password_creation_fails_before_provider_io() -> None:
    password = _target(
        "app_password",
        domain_name="customer.example",
        mailbox_local_part="person",
    )
    no_calls = ScriptedTransport([])
    password_result = (
        MailcowConnector(no_calls)
        .provisioning_handler_for(CAPABILITY_ID)
        .apply(_apply(password))
    )
    assert password_result.status is ProvisionResultStatus.TERMINAL
    assert password_result.error_code == "secret_write_boundary_required"
    assert no_calls.requests == []


def test_domain_creation_carries_exact_capacity_and_no_backup_mx_policy() -> None:
    target = _target(
        "domain",
        backup_mx_enabled=False,
        domain_alias_limit=200,
        domain_mailbox_limit=50,
        domain_name="customer.example",
        domain_quota_bytes=107_374_182_400,
        global_address_list_enabled=True,
        mailbox_quota_default_bytes=1_073_741_824,
        mailbox_quota_max_bytes=10_737_418_240,
        relay_all_recipients_enabled=False,
        relay_unknown_recipients_enabled=False,
    )
    created = {"active": 1, "domain_name": "customer.example"}
    transport = ScriptedTransport(
        [
            _response(200, []),
            _response(200, [{"type": "success", "msg": "domain_added"}]),
            _response(200, created),
        ]
    )

    result = (
        MailcowConnector(transport)
        .provisioning_handler_for(CAPABILITY_ID)
        .apply(_apply(target))
    )

    assert result.status is ProvisionResultStatus.SUCCEEDED
    mutation = transport.requests[1]
    assert mutation.path == "/api/v1/add/domain"
    assert mutation.document == {
        "active": "1",
        "aliases": 200,
        "backupmx": "0",
        "defquota": 1024,
        "description": "customer.example",
        "domain": "customer.example",
        "gal": "1",
        "key_size": 0,
        "mailboxes": 50,
        "maxquota": 10240,
        "quota": 102400,
        "relay_all_recipients": "0",
        "relay_unknown_only": "0",
        "restart_sogo": "0",
        "tags": [],
        "template": "",
    }


def test_oidc_application_refuses_activation_until_immutable_subject_is_proven() -> (
    None
):
    target = _target(
        "application",
        oidc_account_creation_enabled=False,
        oidc_client_id="mailcow",
        oidc_email_linking_enabled=False,
        oidc_enabled=True,
        oidc_id_token_signing_algorithm="RS256",
        oidc_issuer_url="https://idp.example.net/realms/customer",
        oidc_logout_uri="https://mail.example.net/",
        oidc_mailpassword_flow_enabled=False,
        oidc_pkce_method="S256",
        oidc_redirect_uri="https://mail.example.net/",
        oidc_require_aud_azp_validation=True,
        oidc_subject_binding="immutable_issuer_subject",
    )
    transport = ScriptedTransport([])

    result = (
        MailcowConnector(transport)
        .provisioning_handler_for(CAPABILITY_ID)
        .apply(_apply(target))
    )

    assert result.status is ProvisionResultStatus.TERMINAL
    assert result.error_code == "immutable_subject_mapping_unverified"
    assert transport.requests == []


def test_dkim_projects_only_the_public_record_and_digest() -> None:
    public_record = "v=DKIM1;k=rsa;p=PUBLICKEY"
    transport = ScriptedTransport(
        [_response(200, {"dkim_selector": "dkim", "dkim_txt": public_record})]
    )

    result = (
        MailcowConnector(transport)
        .provisioning_handler_for(CAPABILITY_ID)
        .observe(
            ProvisionObserveRequest(
                capability_id=CAPABILITY_ID,
                command_id="observe-1",
                operation_ref="operation-1",
                plan_hash="sha256:" + "1" * 64,
                step_key="email-1",
                provider_operation_ref="customer.example",
                target={
                    "application_ref": "mail-primary",
                    "resource_kind": "dkim",
                    "resource_ref": "dkim-primary",
                },
                config=_config(),
                secrets=_secrets(),
            )
        )
    )

    assert result.status is ProvisionResultStatus.SUCCEEDED
    serialized = json.dumps(dict(result.evidence), sort_keys=True)
    assert public_record in serialized
    assert "private" not in serialized.casefold()
    assert API_MATERIAL not in serialized


def test_cancel_is_a_synchronous_noop_with_typed_evidence() -> None:
    transport = ScriptedTransport([])
    result = (
        MailcowConnector(transport)
        .provisioning_handler_for(CAPABILITY_ID)
        .cancel(
            ProvisionCancelRequest(
                capability_id=CAPABILITY_ID,
                command_id="cancel-1",
                operation_ref="operation-1",
                plan_hash="sha256:" + "1" * 64,
                step_key="email-1",
                provider_operation_ref="mailbox-primary",
                target={
                    "application_ref": "mail-primary",
                    "resource_kind": "mailbox",
                    "resource_ref": "mailbox-primary",
                },
                reason="operator_requested",
                idempotency_key="cancel-1/email-1",
                config=_config(),
                secrets=_secrets(),
            )
        )
    )

    assert result.status is ProvisionResultStatus.CANCELLED
    assert result.evidence == {
        "cancelled": True,
        "resource_kind": "mailbox",
        "resource_ref": "mailbox-primary",
    }
    assert transport.requests == []


def test_mailcow_danger_envelope_is_terminal_even_when_http_is_200() -> None:
    target = _target(
        "quota",
        domain_name="customer.example",
        mailbox_local_part="person",
        quota_bytes=1_073_741_824,
    )
    existing = {
        "active": 1,
        "domain": "customer.example",
        "local_part": "person",
        "quota": 536_870_912,
        "username": "person@customer.example",
    }
    transport = ScriptedTransport(
        [
            _response(200, existing),
            _response(200, [{"type": "danger", "msg": "validation_failed"}]),
        ]
    )

    result = (
        MailcowConnector(transport)
        .provisioning_handler_for(CAPABILITY_ID)
        .apply(_apply(target))
    )

    assert result.status is ProvisionResultStatus.TERMINAL
    assert result.error_code == "provider_rejected"
    assert result.error_detail is None


def test_connection_validation_and_real_transport_hold_the_security_boundary() -> None:
    connector = MailcowConnector(HttpxMailcowTransport())
    assert (
        connector.validate_connection(config=_config(), secrets=_connection_secrets())
        == ()
    )
    assert connector.validate_connection(
        config={"admin_endpoint": "http://mail.example.net"},
        secrets=_connection_secrets(),
    )
    assert connector.validate_connection(config=_config(), secrets={})
    assert API_MATERIAL not in repr(HttpxMailcowTransport())
