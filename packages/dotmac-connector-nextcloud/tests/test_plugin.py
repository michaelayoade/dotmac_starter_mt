from __future__ import annotations

from collections.abc import Mapping

import pytest
from dotmac_connector_nextcloud import CAPABILITY_IDS, MANIFEST, NextcloudConnector
from dotmac_connector_nextcloud.transport import (
    FailureKind,
    ManagementRequest,
    NextcloudTransportError,
)
from dotmac_integration.conformance import assert_plugin_conforms
from dotmac_integration.spi import (
    ConnectorMode,
    ProvisionApplyRequest,
    ProvisionCancelRequest,
    ProvisionObserveRequest,
    ProvisionPlanRequest,
    ProvisionResultStatus,
    ProvisionStep,
)
from jsonschema import Draft202012Validator, ValidationError

_DIGEST = "sha256:" + "a" * 64
_FAKE_HELD_CLIENT_MATERIAL = "fixture-client-material"


class FakeTransport:
    def __init__(self, response: Mapping[str, object]) -> None:
        self.response = response
        self.seen: list[tuple[str, str | None, ManagementRequest]] = []
        self.error: NextcloudTransportError | None = None

    def __bool__(self) -> bool:
        """Prove collaborator selection uses identity rather than truthiness."""

        return False

    def invoke(
        self,
        *,
        management_endpoint: str,
        management_authorization: str,
        client_secret: str | None,
        request: ManagementRequest,
    ) -> Mapping[str, object]:
        assert management_endpoint == "https://cloud.example.test"
        self.seen.append((management_authorization, client_secret, request))
        if self.error is not None:
            raise self.error
        return self.response


def _step(capability_id: str, target: Mapping[str, object]) -> ProvisionStep:
    return ProvisionStep(
        step_key="step-primary",
        endpoint_code=capability_id,
        depends_on=(),
        input=target,
    )


def _config() -> dict[str, object]:
    return {"management_endpoint": "https://cloud.example.test"}


def _secrets(*, oidc: bool = False) -> dict[str, str]:
    values = {"management_secret_ref": "held-authorization-material"}
    if oidc:
        values["client_secret_ref"] = _FAKE_HELD_CLIENT_MATERIAL
    return values


def test_manifest_publishes_only_exact_owner_contracts() -> None:
    assert MANIFEST.connector_key == "nextcloud"
    assert MANIFEST.capability_ids == frozenset(CAPABILITY_IDS)
    assert CAPABILITY_IDS == (
        "collaboration.application.lifecycle.v1",
        "collaboration.file-roundtrip.lifecycle.v1",
        "collaboration.user-group-quota.lifecycle.v1",
        "collaboration.user-oidc.configuration.lifecycle.v1",
    )
    for declaration in MANIFEST.capabilities:
        assert declaration.contract_snapshot is not None
        assert len(declaration.schema_documents) == 8


@pytest.mark.parametrize(
    ("capability_id", "operation", "evidence"),
    [
        (
            "collaboration.application.lifecycle.v1",
            "plan",
            {
                "action": "ensure_active",
                "application_ref": "collaboration-primary",
                "changes": ["ensure exact admitted application release"],
            },
        ),
        (
            "collaboration.file-roundtrip.lifecycle.v1",
            "apply",
            {
                "application_ref": "collaboration-primary",
                "cleanup_succeeded": True,
                "completed_at": "2026-08-17T12:00:00+00:00",
                "digest_matches": True,
                "logical_path": "/dotmac/probe.txt",
                "read_digest": _DIGEST,
                "read_succeeded": True,
                "roundtrip_ref": "roundtrip-primary",
                "user_ref": "user-primary",
                "write_digest": _DIGEST,
                "write_succeeded": True,
            },
        ),
        (
            "collaboration.user-group-quota.lifecycle.v1",
            "observe",
            {
                "application_ref": "collaboration-primary",
                "group_ref": "group-primary",
                "lifecycle_state": "present",
                "membership_present": True,
                "observed_configuration_digest": _DIGEST,
                "resource_kind": "group_membership",
                "resource_ref": "membership-primary",
                "user_ref": "user-primary",
            },
        ),
        (
            "collaboration.user-oidc.configuration.lifecycle.v1",
            "apply",
            {
                "account_creation_mode": "preprovisioned_only",
                "application_ref": "collaboration-primary",
                "audience": "nextcloud",
                "backchannel_logout_enabled": True,
                "client_id": "nextcloud",
                "client_secret_configured": True,
                "direct_login_mode": "break_glass",
                "email_linking_enabled": False,
                "identity_binding_key": "issuer_subject",
                "id_token_signing_algorithm": "RS256",
                "issuer_url": "https://identity.example.test/realms/dotmac",
                "observed_configuration_digest": _DIGEST,
                "oidc_configuration_ref": "collaboration-primary-oidc",
                "pkce_method": "S256",
                "redirect_uris": ["https://cloud.example.test/apps/user_oidc/code"],
                "require_aud_azp_validation": True,
                "session_provenance_required": True,
                "session_revocation_required": True,
                "subject_claim": "sub",
                "subject_mapping_mode": "immutable",
            },
        ),
    ],
)
def test_provider_fixtures_match_exact_held_owner_outputs(
    capability_id: str,
    operation: str,
    evidence: Mapping[str, object],
) -> None:
    declaration = MANIFEST.require_declares(capability_id)
    operation_pin = next(
        item for item in declaration.operations if item.operation_code == operation
    )
    document = declaration.require_schema(
        operation_pin.output_schema_ref,
        operation_pin.output_schema_digest,
    )
    Draft202012Validator(document.to_mapping()).validate(dict(evidence))


def test_owner_input_schema_refuses_a_planted_provider_field() -> None:
    capability_id = "collaboration.user-group-quota.lifecycle.v1"
    declaration = MANIFEST.require_declares(capability_id)
    operation_pin = next(
        item for item in declaration.operations if item.operation_code == "apply"
    )
    document = declaration.require_schema(
        operation_pin.input_schema_ref,
        operation_pin.input_schema_digest,
    )
    target = {
        "application_ref": "collaboration-primary",
        "desired_state": "present",
        "identity_issuer": "https://identity.example.test/realms/dotmac",
        "identity_subject": "subject-primary",
        "nextcloud_user_id": "provider-owned-field",
        "resource_kind": "user",
        "resource_ref": "user-primary",
        "user_ref": "user-primary",
    }
    with pytest.raises(ValidationError):
        Draft202012Validator(document.to_mapping()).validate(target)


def test_plugin_keeps_the_spi_provision_shape() -> None:
    plugin = NextcloudConnector(FakeTransport({"evidence": {}}))
    assert plugin.modes == frozenset({ConnectorMode.PROVISION})
    assert_plugin_conforms(plugin)


def test_plan_passes_exact_identity_and_target_to_closed_transport() -> None:
    capability_id = "collaboration.application.lifecycle.v1"
    target = {
        "action": "ensure_active",
        "application_ref": "collaboration-primary",
        "artifact_digest": _DIGEST,
        "configuration_digest": _DIGEST,
        "target_version": "31.0.8",
    }
    response = {
        "evidence": {
            "action": "ensure_active",
            "application_ref": "collaboration-primary",
            "changes": ["ensure exact admitted application release"],
        }
    }
    transport = FakeTransport(response)
    handler = NextcloudConnector(transport).provisioning_handler_for(capability_id)
    result = handler.plan(
        ProvisionPlanRequest(
            capability_id=capability_id,
            command_id="plan-primary",
            plan_hash=_DIGEST,
            steps=(_step(capability_id, target),),
            config={
                **_config(),
                "backup_storage_ref": "backup-store-primary",
                "release_channel_ref": "stable",
            },
            secrets=_secrets(),
        )
    )
    assert result.evidence == response["evidence"]
    authorization, client_secret, request = transport.seen[0]
    assert authorization == "held-authorization-material"
    assert client_secret is None
    assert request.body["target"] == target
    assert request.body["installation_context"] == {
        "backup_storage_ref": "backup-store-primary",
        "release_channel_ref": "stable",
    }
    assert request.operation == "plan"
    assert request.mutating is False


def test_oidc_apply_keeps_all_security_gates_and_material_out_of_body() -> None:
    capability_id = "collaboration.user-oidc.configuration.lifecycle.v1"
    target = {
        "account_creation_mode": "preprovisioned_only",
        "application_ref": "collaboration-primary",
        "audience": "nextcloud",
        "backchannel_logout_enabled": True,
        "client_id": "nextcloud",
        "direct_login_mode": "break_glass",
        "email_linking_enabled": False,
        "identity_binding_key": "issuer_subject",
        "id_token_signing_algorithm": "RS256",
        "issuer_url": "https://identity.example.test/realms/dotmac",
        "oidc_configuration_ref": "collaboration-primary-oidc",
        "pkce_method": "S256",
        "redirect_uris": ["https://cloud.example.test/apps/user_oidc/code"],
        "require_aud_azp_validation": True,
        "session_provenance_required": True,
        "session_revocation_required": True,
        "subject_claim": "sub",
        "subject_mapping_mode": "immutable",
    }
    transport = FakeTransport(
        {
            "status": "succeeded",
            "provider_operation_ref": "oidc:collaboration-primary",
            "evidence": {
                **target,
                "client_secret_configured": True,
                "observed_configuration_digest": _DIGEST,
            },
        }
    )
    handler = NextcloudConnector(transport).provisioning_handler_for(capability_id)
    result = handler.apply(
        ProvisionApplyRequest(
            capability_id=capability_id,
            command_id="apply-oidc-primary",
            operation_ref="operation-oidc-primary",
            plan_hash=_DIGEST,
            step=_step(capability_id, target),
            config=_config(),
            secrets=_secrets(oidc=True),
            idempotency_key="apply-oidc-primary/step-primary",
        )
    )
    assert result.status is ProvisionResultStatus.SUCCEEDED
    authorization, client_secret, request = transport.seen[0]
    assert authorization == "held-authorization-material"
    assert client_secret == _FAKE_HELD_CLIENT_MATERIAL
    assert request.body["target"] == target
    assert "client_secret_ref" not in request.body
    assert "management_secret_ref" not in request.body
    assert _FAKE_HELD_CLIENT_MATERIAL not in repr(request)


def test_mutating_ambiguous_outcome_is_not_relabelled_retryable() -> None:
    capability_id = "collaboration.user-group-quota.lifecycle.v1"
    transport = FakeTransport({})
    transport.error = NextcloudTransportError(
        "provider_outcome_ambiguous", FailureKind.AMBIGUOUS
    )
    handler = NextcloudConnector(transport).provisioning_handler_for(capability_id)
    result = handler.apply(
        ProvisionApplyRequest(
            capability_id=capability_id,
            command_id="apply-user-primary",
            operation_ref="operation-user-primary",
            plan_hash=_DIGEST,
            step=_step(
                capability_id,
                {
                    "application_ref": "collaboration-primary",
                    "desired_state": "present",
                    "identity_issuer": "https://identity.example.test/realms/dotmac",
                    "identity_subject": "subject-primary",
                    "resource_kind": "user",
                    "resource_ref": "user-primary",
                    "user_ref": "user-primary",
                },
            ),
            config=_config(),
            secrets=_secrets(),
            idempotency_key="apply-user-primary/step-primary",
        )
    )
    assert result.status is ProvisionResultStatus.AMBIGUOUS
    assert result.error_code == "provider_outcome_ambiguous"


def test_replay_and_collision_material_is_forwarded_not_cached_by_connector() -> None:
    capability_id = "collaboration.user-group-quota.lifecycle.v1"
    transport = FakeTransport(
        {
            "status": "succeeded",
            "provider_operation_ref": "group:primary",
            "evidence": {
                "application_ref": "collaboration-primary",
                "group_ref": "group-primary",
                "lifecycle_state": "present",
                "observed_configuration_digest": _DIGEST,
                "resource_kind": "group",
                "resource_ref": "group-primary",
            },
        }
    )
    handler = NextcloudConnector(transport).provisioning_handler_for(capability_id)
    target = {
        "application_ref": "collaboration-primary",
        "desired_state": "present",
        "group_ref": "group-primary",
        "resource_kind": "group",
        "resource_ref": "group-primary",
    }

    def request(target_document: Mapping[str, object]) -> ProvisionApplyRequest:
        return ProvisionApplyRequest(
            capability_id=capability_id,
            command_id="apply-group-primary",
            operation_ref="operation-group-primary",
            plan_hash=_DIGEST,
            step=_step(capability_id, target_document),
            config=_config(),
            secrets=_secrets(),
            idempotency_key="apply-group-primary/step-primary",
        )

    handler.apply(request(target))
    handler.apply(request(target))
    handler.apply(request({**target, "desired_state": "absent"}))

    assert len(transport.seen) == 3
    bodies = [seen_request.body for _, _, seen_request in transport.seen]
    assert bodies[0] == bodies[1]
    assert bodies[2]["target"] != bodies[0]["target"]
    assert {body["idempotency_key"] for body in bodies} == {
        "apply-group-primary/step-primary"
    }


def test_observe_and_cancel_use_only_the_schema_restricted_immutable_target() -> None:
    capability_id = "collaboration.user-group-quota.lifecycle.v1"
    target = {
        "application_ref": "collaboration-primary",
        "resource_kind": "group_membership",
        "resource_ref": "membership-primary",
    }
    observed = {
        "application_ref": "collaboration-primary",
        "group_ref": "group-primary",
        "lifecycle_state": "present",
        "membership_present": True,
        "observed_configuration_digest": _DIGEST,
        "resource_kind": "group_membership",
        "resource_ref": "membership-primary",
        "user_ref": "user-primary",
    }
    transport = FakeTransport(
        {
            "status": "succeeded",
            "provider_operation_ref": "membership:primary",
            "evidence": observed,
        }
    )
    handler = NextcloudConnector(transport).provisioning_handler_for(capability_id)
    observation = handler.observe(
        ProvisionObserveRequest(
            capability_id=capability_id,
            command_id="observe-membership-primary",
            operation_ref="operation-membership-primary",
            plan_hash=_DIGEST,
            step_key="step-primary",
            provider_operation_ref="membership:primary",
            target=target,
            config=_config(),
            secrets=_secrets(),
        )
    )
    assert observation.status is ProvisionResultStatus.SUCCEEDED
    assert transport.seen[0][2].body["target"] == target
    assert transport.seen[0][2].mutating is False

    transport.response = {
        "status": "cancelled",
        "provider_operation_ref": "membership:primary",
        "evidence": {
            "cancelled": True,
            "resource_kind": "group_membership",
            "resource_ref": "membership-primary",
        },
    }
    cancellation = handler.cancel(
        ProvisionCancelRequest(
            capability_id=capability_id,
            command_id="cancel-membership-primary",
            operation_ref="operation-membership-primary",
            plan_hash=_DIGEST,
            step_key="step-primary",
            provider_operation_ref="membership:primary",
            target=target,
            reason="approval-withdrawn",
            idempotency_key="cancel-membership-primary/step-primary",
            config=_config(),
            secrets=_secrets(),
        )
    )
    assert cancellation.status is ProvisionResultStatus.CANCELLED
    assert transport.seen[1][2].body["target"] == target
    assert transport.seen[1][2].body["reason"] == "approval-withdrawn"
    assert transport.seen[1][2].mutating is True

    transport.error = NextcloudTransportError(
        "provider_resource_not_found", FailureKind.NOT_FOUND
    )
    absent = handler.cancel(
        ProvisionCancelRequest(
            capability_id=capability_id,
            command_id="cancel-membership-absent",
            operation_ref="operation-membership-primary",
            plan_hash=_DIGEST,
            step_key="step-primary",
            provider_operation_ref="membership:primary",
            target=target,
            reason="approval-withdrawn",
            idempotency_key="cancel-membership-absent/step-primary",
            config=_config(),
            secrets=_secrets(),
        )
    )
    assert absent.status is ProvisionResultStatus.NOT_FOUND
    assert absent.evidence == {
        "cancelled": True,
        "resource_kind": "group_membership",
        "resource_ref": "membership-primary",
    }


@pytest.mark.parametrize(
    "forbidden_key",
    ["password", "generated_password", "client_secret", "api_key", "token"],
)
def test_generated_secret_output_fails_closed(forbidden_key: str) -> None:
    capability_id = "collaboration.file-roundtrip.lifecycle.v1"
    transport = FakeTransport(
        {
            "status": "succeeded",
            "provider_operation_ref": "roundtrip:primary",
            "evidence": {forbidden_key: "material"},
        }
    )
    handler = NextcloudConnector(transport).provisioning_handler_for(capability_id)
    result = handler.apply(
        ProvisionApplyRequest(
            capability_id=capability_id,
            command_id="apply-roundtrip-primary",
            operation_ref="operation-roundtrip-primary",
            plan_hash=_DIGEST,
            step=_step(
                capability_id,
                {
                    "application_ref": "collaboration-primary",
                    "cleanup_required": True,
                    "expected_content_digest": _DIGEST,
                    "logical_path": "/dotmac/probe.txt",
                    "probe_content": "public probe",
                    "roundtrip_ref": "roundtrip-primary",
                    "user_ref": "user-primary",
                },
            ),
            config=_config(),
            secrets=_secrets(),
            idempotency_key="apply-roundtrip-primary/step-primary",
        )
    )
    assert result.status is ProvisionResultStatus.AMBIGUOUS
    assert result.error_code == "provider_contract_invalid"
