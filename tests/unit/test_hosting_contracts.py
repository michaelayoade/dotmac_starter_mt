from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import uuid4

import pytest
from dotmac_hosting.contracts import (
    HOSTING_ACCOUNT_CAPABILITY,
    HOSTING_ACCOUNT_OPERATIONS,
    ApprovalObservationState,
    ChangeHostingPackageV1,
    ChangeHostingSuspensionV1,
    ContractError,
    HostingAccountIdentityV1,
    HostingAllowance,
    HostingChangeRules,
    HostingObservationV1,
    HostingOutcomeEvidenceV1,
    HostingResourceFactV1,
    ObserveHostingAccountV1,
    ProvisionHostingAccountV1,
    PublishHostingSpecificationVersion,
    ReconcileHostingAccountV1,
    RequestTermination,
    SuspensionAction,
    TerminateHostingAccountV1,
    TerminationApprovalObservationV1,
    termination_content_digest,
)
from dotmac_hosting.fakes import FakeHostingAccountCapabilityV1
from dotmac_hosting.testing import check_hosting_account_capability_v1
from dotmac_hosting.vocabulary import HostingVocabularyRegistry

NOW = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)


def _wire(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _wire(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_wire(item) for item in value]
    return value


def _reconstruct_provider_request(
    operation: str, payload: dict[str, Any]
) -> object:
    if operation == "provision":
        return ProvisionHostingAccountV1(
            operation_reference=payload["operation_reference"],
            package_ref=payload["package_ref"],
            primary_domain=payload["primary_domain"],
            account_identity=HostingAccountIdentityV1(**payload["account_identity"]),
        )
    if operation == "package":
        return ChangeHostingPackageV1(**payload)
    if operation == "termination":
        return TerminateHostingAccountV1(**payload)
    if operation == "observation":
        return ObserveHostingAccountV1(**payload)
    if operation == "reconcile":
        return ReconcileHostingAccountV1(**payload)
    if operation in {"suspend", "restore"}:
        return ChangeHostingSuspensionV1(
            operation_reference=payload["operation_reference"],
            account_ref=payload["account_ref"],
            action=SuspensionAction(payload["action"]),
            reason_ref=payload["reason_ref"],
        )
    raise AssertionError(f"unknown operation {operation}")


def _forbidden_payload_paths(payload: object, prefix: str = "") -> set[str]:
    violations: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            normalized = str(key).lower()
            if normalized.endswith("_ref") and normalized not in {
                "operation_reference",
                "package_ref",
                "target_package_ref",
                "reason_ref",
                "account_ref",
            }:
                violations.add(path)
            if any(
                word == normalized or word in normalized.split("_")
                for word in (
                    "password",
                    "credential",
                    "token",
                    "secret",
                    "private_key",
                    "auth_code",
                    "api_key",
                )
            ):
                violations.add(path)
            violations.update(_forbidden_payload_paths(value, path))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            violations.update(_forbidden_payload_paths(value, f"{prefix}[{index}]"))
    return violations


def test_hosting_account_v1_is_one_six_operation_family() -> None:
    assert HOSTING_ACCOUNT_CAPABILITY == "hosting.account.v1"
    assert HOSTING_ACCOUNT_OPERATIONS == {
        "provision",
        "package",
        "suspension",
        "termination",
        "observation",
        "reconcile",
    }
    fake = FakeHostingAccountCapabilityV1()
    check_hosting_account_capability_v1(fake)
    assert {operation for operation, _ in fake.calls} == HOSTING_ACCOUNT_OPERATIONS


def test_every_provider_operation_payload_reconstructs_exactly_without_local_or_secret_data() -> None:
    requests = (
        (
            "provision",
            ProvisionHostingAccountV1(
                operation_reference="operation-provision",
                package_ref="business-hosting:v1",
                primary_domain="customer.ng",
                account_identity=HostingAccountIdentityV1(
                    account_label="Customer Limited",
                    administrative_email="admin@customer.ng",
                    country_code="NG",
                ),
            ),
        ),
        (
            "package",
            ChangeHostingPackageV1(
                operation_reference="operation-package",
                account_ref="account-1",
                target_package_ref="business-hosting:v2",
            ),
        ),
        (
            "suspend",
            ChangeHostingSuspensionV1(
                operation_reference="operation-suspend",
                account_ref="account-1",
                action=SuspensionAction.SUSPEND,
                reason_ref="abuse",
            ),
        ),
        (
            "restore",
            ChangeHostingSuspensionV1(
                operation_reference="operation-restore",
                account_ref="account-1",
                action=SuspensionAction.RESTORE,
                reason_ref="abuse",
            ),
        ),
        (
            "termination",
            TerminateHostingAccountV1(
                operation_reference="operation-termination",
                account_ref="account-1",
            ),
        ),
        (
            "observation",
            ObserveHostingAccountV1(
                operation_reference="operation-observation",
                account_ref="account-1",
            ),
        ),
        (
            "reconcile",
            ReconcileHostingAccountV1(
                operation_reference="operation-reconcile",
                account_ref="account-1",
            ),
        ),
    )
    for operation, request in requests:
        payload = _wire(asdict(request))
        assert isinstance(payload, dict)
        reconstructed = _reconstruct_provider_request(operation, payload)
        assert reconstructed == request
        assert _wire(asdict(reconstructed)) == payload
        assert _forbidden_payload_paths(payload) == set()


def test_provider_payload_guard_detects_local_references_and_secrets() -> None:
    assert _forbidden_payload_paths(
        {
            "operation_reference": "operation-1",
            "owner_contact_ref": "local-party:1",
            "account_identity": {"api_key": "planted"},
        }
    ) == {"owner_contact_ref", "account_identity.api_key"}


def test_observation_keeps_verbatim_status_and_typed_resource_period() -> None:
    fact = HostingObservationV1(
        provider_account_ref="account-1",
        provider_event_id="event-1",
        capability_binding_ref="binding-1",
        observation_kind="active",
        provider_statuses=("SUSPENDED-BY-REMOTE",),
        observed_at=NOW,
        resources=(
            HostingResourceFactV1(
                resource_kind="mailbox_count",
                quantity=Decimal("3"),
                unit="count",
                period_start=NOW,
                period_end=NOW,
            ),
        ),
    )
    observation_status = fact.provider_statuses
    assert observation_status
    assert observation_status == ("SUSPENDED-BY-REMOTE",)
    assert fact.resources[0].quantity == Decimal("3")
    assert not hasattr(fact.resources[0], "mailbox_id")
    assert not hasattr(fact.resources[0], "mailbox_address")


def test_mailbox_is_observation_only_not_a_lifecycle_vocabulary() -> None:
    registry = HostingVocabularyRegistry()
    registry.require_resource_kind("mailbox_count")
    assert all("mailbox" not in reason for reason in registry.suspension_restorers)
    with pytest.raises(KeyError, match="not registered"):
        registry.require_resource_kind("mailbox_address")


def test_provisioning_contract_is_a_closed_self_contained_snapshot() -> None:
    request = ProvisionHostingAccountV1(
        operation_reference="operation-1",
        package_ref="business-hosting:v1",
        primary_domain="customer.ng",
        account_identity=HostingAccountIdentityV1(
            account_label="Customer Limited",
            administrative_email="admin@customer.ng",
            country_code="NG",
        ),
    )
    assert request.account_identity.administrative_email == "admin@customer.ng"
    fields = request.__dataclass_fields__
    assert set(fields) == {
        "operation_reference",
        "package_ref",
        "primary_domain",
        "account_identity",
    }
    assert "password" not in repr(request).lower()
    assert "secret" not in repr(request).lower()


def test_provider_outcome_evidence_is_closed_and_carries_no_transport_blob() -> None:
    evidence = HostingOutcomeEvidenceV1(
        provider_statuses=("accepted",),
        diagnostic_codes=("panel.account.accepted",),
    )
    assert set(evidence.__dataclass_fields__) == {
        "provider_statuses",
        "diagnostic_codes",
    }


def test_specification_publication_has_typed_content_and_no_caller_version() -> None:
    publication = PublishHostingSpecificationVersion(
        specification_code="business-hosting",
        package_ref="business-hosting:v1",
        package_rank=10,
        allowances=(
            HostingAllowance(
                resource_kind="disk_bytes",
                quantity=Decimal("21474836480"),
                unit="bytes",
            ),
        ),
        included_artifacts=("tls", "backup"),
        capability_codes=("php", "database"),
        change_rules=HostingChangeRules(
            upgrade_allowed=True,
            downgrade_allowed=True,
            downgrade_requires_review=True,
            same_level_allowed=True,
        ),
        published_at=NOW,
    )
    assert "version" not in publication.__dataclass_fields__
    assert publication.allowances[0].quantity == Decimal("21474836480")


def test_termination_approval_is_bound_to_exact_request_content() -> None:
    service_id = uuid4()
    request = RequestTermination(
        hosting_service_id=service_id,
        expected_version=3,
        requested_at=NOW,
        approval_request_id=uuid4(),
    )
    assert set(request.__dataclass_fields__) == {
        "hosting_service_id",
        "expected_version",
        "requested_at",
        "approval_request_id",
    }
    assert "approval" not in request.__dataclass_fields__
    tenant_id = uuid4()
    digest = termination_content_digest(
        tenant_id, service_id, request.expected_version, request.requested_at
    )
    assert digest.startswith("sha256:") and len(digest) == 71
    approval_content = {
        "tenant_id": str(tenant_id),
        "operation": "hosting.termination",
        "subject_type": "hosting_service",
        "subject_id": str(service_id),
        "expected_version": 3,
        "requested_at": NOW.isoformat(),
    }
    expected = hashlib.sha256(
        json.dumps(
            approval_content,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    assert digest == f"sha256:{expected}"
    assert digest != termination_content_digest(
        tenant_id,
        service_id,
        4,
        NOW,
    )
    approved = TerminationApprovalObservationV1(
        event_type="approval.approved",
        request_id=request.approval_request_id,
        subject_type="hosting_service",
        subject_id=str(service_id),
        policy_code="hosting.termination.v1",
        policy_version=1,
        content_digest=digest,
        state=ApprovalObservationState.APPROVED,
    )
    assert approved.content_digest == digest
    assert set(approved.__dataclass_fields__) == {
        "request_id",
        "event_type",
        "subject_type",
        "subject_id",
        "policy_code",
        "policy_version",
        "content_digest",
        "state",
    }
    with pytest.raises(ContractError, match="sha256:<hex>"):
        TerminationApprovalObservationV1(
            event_type="approval.approved",
            request_id=uuid4(),
            subject_type="hosting_service",
            subject_id=str(service_id),
            policy_code="hosting.termination.v1",
            policy_version=1,
            content_digest=digest.removeprefix("sha256:"),
            state=ApprovalObservationState.APPROVED,
        )


def test_contract_times_must_be_timezone_aware() -> None:
    with pytest.raises(ContractError, match="timezone-aware"):
        HostingObservationV1(
            provider_account_ref="account-1",
            provider_event_id="event-1",
            capability_binding_ref="binding-1",
            observation_kind="active",
            provider_statuses=("active",),
            observed_at=NOW.replace(tzinfo=None),
        )
