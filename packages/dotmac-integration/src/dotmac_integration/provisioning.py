"""Approval-bound provisioning command intake and three-phase execution.

Vendor CP owns desired state, plan construction and approval. This module owns
the durable command identity, provider execution state, retry scheduling and
evidence. Connector I/O occurs only in the ``invoke_prepared_*`` functions,
whose signatures cannot accept a database session.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Literal, cast
from uuid import UUID

from dotmac_kernel.capability_contract import (
    CapabilityOperation,
    CapabilitySchemaDocument,
)
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError
from sqlalchemy import func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_integration.capability_instances import require_capability_instance_ref
from dotmac_integration.discovery import ConnectorRegistry
from dotmac_integration.execution import payload_digest
from dotmac_integration.models import (
    CapabilityBinding,
    ConnectorConfigRevision,
    ConnectorInstallation,
)
from dotmac_integration.policy import (
    DEFAULT_POLICY,
    ExecutionPolicy,
    execution_policy_digest,
)
from dotmac_integration.provisioning_models import (
    ProvisioningCommandReceipt,
    ProvisioningCommandRecord,
    ProvisioningOperation,
    ProvisioningReceipt,
    ProvisioningStep,
)
from dotmac_integration.retry import retry_delay_seconds
from dotmac_integration.secret_refs import verify_capability_configuration
from dotmac_integration.selection import SelectionError, resolve_binding
from dotmac_integration.spi import (
    CapabilityDeclaration,
    ConnectorMode,
    ProvisionApplyRequest,
    ProvisionCancelRequest,
    ProvisioningResult,
    ProvisionObserveRequest,
    ProvisionPlanRequest,
    ProvisionPlanResult,
    ProvisionPlugin,
    ProvisionResultStatus,
    ProvisionStep,
    accepts_manifest_digest,
    require_mode,
)

_FORMAT_CHECKER = FormatChecker()

__all__ = [
    "ApprovalRefused",
    "CommandAcceptance",
    "CommandIdentityCollision",
    "ExpectedProvisioningPin",
    "LostProvisioningClaim",
    "PrerequisiteEvidenceBinding",
    "PrerequisiteReceiptPin",
    "PreparedCancellation",
    "PreparedObservation",
    "PreparedPlan",
    "PreparedProvisioning",
    "ProvisioningCapabilityOperationPin",
    "ProvisioningCommand",
    "ProvisioningPlanReceiptView",
    "ProvisioningRefused",
    "ProvisioningReceiptView",
    "VerifiedApprovalGrant",
    "accept_provisioning_command",
    "provisioning_command_template_digest",
    "invoke_prepared_cancellation",
    "invoke_prepared_observation",
    "invoke_prepared_plan",
    "invoke_prepared_provisioning",
    "prepare_next_apply",
    "prepare_next_observation",
    "prepare_cancellation",
    "prepare_provisioning_plan",
    "read_provisioning_receipts",
    "read_provisioning_plan_receipt",
    "settle_cancellation",
    "settle_observation",
    "settle_provisioning_plan",
    "settle_provisioning",
]

_DIGEST = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_CODE = re.compile(r"^[a-z][a-z0-9_.:-]{0,158}[a-z0-9]$")
_SAFE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,319}$")
_SCHEMA_REFERENCE = re.compile(
    r"^schema:[a-z0-9](?:[a-z0-9._/-]{0,218}[a-z0-9])?@v[1-9][0-9]*$"
)
_FORBIDDEN_EXECUTION_TOKENS = frozenset({"argv", "command", "exec", "script", "shell"})
_SECRET_TOKENS = frozenset({"credential", "password", "secret", "token"})
_SECRET_COMPOUNDS = frozenset({"apikey", "privatekey"})
_TERMINAL_STATES = frozenset(
    {"succeeded", "terminal", "reconciliation_required", "cancelled"}
)
_OBSERVE_OUTPUT_STATUSES = frozenset({ProvisionResultStatus.SUCCEEDED})
_CANCEL_OUTPUT_STATUSES = frozenset(
    {
        ProvisionResultStatus.SUCCEEDED,
        ProvisionResultStatus.CANCELLED,
        ProvisionResultStatus.NOT_FOUND,
    }
)


class ProvisioningRefused(RuntimeError):
    """A command or attempt cannot proceed safely."""


class ApprovalRefused(ProvisioningRefused):
    """The approval is absent, mismatched, unverified or expired."""


class CommandIdentityCollision(ProvisioningRefused):
    """One command id was reused for different immutable content."""


class LostProvisioningClaim(ProvisioningRefused):
    """Another worker owns or settled this attempt."""


@dataclass(frozen=True, slots=True, repr=False)
class VerifiedApprovalGrant:
    grant_ref: str
    approval_request_id: UUID
    approval_request_binding_hash: str
    saved_plan_id: UUID
    approved_plan_hash: str
    plan_command_id: str
    plan_validation_receipt_id: UUID
    plan_validation_receipt_digest: str
    plan_validation_request_body_digest: str
    module_plan_receipt_hash: str
    digest: str
    expires_at: datetime
    verified_at: datetime | None
    approved_command_template_digest: str


@dataclass(frozen=True, slots=True, repr=False)
class ProvisioningCapabilityOperationPin:
    operation_code: str
    input_schema_ref: str
    input_schema_digest: str
    output_schema_ref: str
    output_schema_digest: str


@dataclass(frozen=True, slots=True, repr=False)
class PrerequisiteReceiptPin:
    """Exact terminal evidence satisfying one approved cross-binding edge.

    Receipt values are necessarily produced after plan approval.  The approved
    command template therefore binds the prerequisite *binding* while this pin
    supplies the later immutable execution evidence for that binding.
    """

    capability_binding_id: UUID
    operation_id: UUID
    terminal_receipt_sequence: int
    terminal_receipt_digest: str
    required_terminal_status: Literal["succeeded"] = "succeeded"


@dataclass(frozen=True, slots=True, repr=False)
class PrerequisiteEvidenceBinding:
    """One approved public value copied between two capability operations.

    The mapping is abstract plan material: it identifies held schemas and JSON
    instance pointers, never a runtime value. The exact source operation and
    terminal receipt arrive later through :class:`PrerequisiteReceiptPin`.
    """

    source_capability_binding_id: UUID
    source_step_key: str
    source_schema_ref: str
    source_schema_digest: str
    source_pointer: str
    target_step_key: str
    target_schema_ref: str
    target_schema_digest: str
    target_pointer: str
    required: bool


@dataclass(frozen=True, slots=True, repr=False)
class ProvisioningCommand:
    command_id: str
    deployment_ref: str
    desired_state_revision: int
    desired_state_version_id: UUID
    desired_state_hash: str
    saved_plan_id: UUID
    approval_request_id: UUID
    approval_request_binding_hash: str
    plan_command_id: str
    plan_validation_receipt_id: UUID
    plan_validation_receipt_digest: str
    plan_validation_request_body_digest: str
    module_plan_receipt_hash: str
    profile_version_id: UUID
    profile_code: str
    profile_version: int
    profile_schema_version: int
    profile_content_hash: str
    command_schema_version: str
    capability_id: str
    capability_instance_ref: str
    capability_owner_code: str
    capability_code: str
    capability_schema_version: int
    capability_contract_attestation_id: UUID
    capability_contract_digest: str
    capability_operations: tuple[ProvisioningCapabilityOperationPin, ...]
    capability_binding_id: UUID
    binding_ref: UUID
    installation_id: UUID
    installation_ref: str
    connector_key: str
    connector_version: str
    connector_manifest_digest: str
    connector_configuration_revision_id: UUID
    configuration_snapshot_ref: str
    configuration_schema_version: int
    configuration_hash: str
    plan_hash: str
    expected_plan_hash: str
    artifact_digest: str
    component_artifact_digest: str | None
    config_digest: str
    execution_policy_digest: str
    approval: VerifiedApprovalGrant
    steps: tuple[ProvisionStep, ...]
    prerequisite_capability_binding_ids: tuple[UUID, ...] = ()
    prerequisite_evidence_bindings: tuple[PrerequisiteEvidenceBinding, ...] = ()
    prerequisite_receipt_pins: tuple[PrerequisiteReceiptPin, ...] = ()


@dataclass(frozen=True, slots=True)
class CommandAcceptance:
    operation_id: UUID
    state: str
    is_new: bool


@dataclass(frozen=True, slots=True, repr=False)
class ExpectedProvisioningPin:
    """Caller-signed pins checked against the durable operation and step."""

    step_key: str
    provider_operation_ref: str
    deployment_ref: str
    capability_instance_ref: str
    plan_hash: str
    artifact_digest: str
    config_digest: str
    approval_digest: str


@dataclass(frozen=True, slots=True, repr=False)
class PreparedProvisioning:
    operation_id: UUID
    step_id: UUID
    command_id: str
    deployment_ref: str
    connector_key: str
    connector_version: str
    manifest_digest: str
    capability_id: str
    capability_instance_ref: str
    binding_id: UUID
    config_revision_id: UUID
    config_digest: str
    plan_hash: str
    artifact_digest: str
    approval_digest: str
    step: ProvisionStep
    output_schema: CapabilitySchemaDocument
    resolved_input_digest: str
    config: dict[str, object]
    secret_refs: dict[str, str]
    attempt_number: int
    leased_until: datetime


@dataclass(frozen=True, slots=True, repr=False)
class PreparedPlan:
    command_record_id: UUID
    command_id: str
    deployment_ref: str
    request_body_digest: str
    connector_key: str
    connector_version: str
    manifest_digest: str
    capability_id: str
    capability_instance_ref: str
    binding_id: UUID
    config_revision_id: UUID
    config_digest: str
    plan_hash: str
    steps: tuple[ProvisionStep, ...]
    input_schema: CapabilitySchemaDocument
    output_schema: CapabilitySchemaDocument
    config: dict[str, object]
    secret_refs: dict[str, str]


@dataclass(frozen=True, slots=True, repr=False)
class PreparedObservation:
    command_record_id: UUID
    command_id: str
    operation_id: UUID
    step_id: UUID
    connector_key: str
    capability_id: str
    capability_instance_ref: str
    plan_hash: str
    step_key: str
    provider_operation_ref: str
    target: Mapping[str, object]
    output_schema: CapabilitySchemaDocument
    config: dict[str, object]
    secret_refs: dict[str, str]
    attempt_number: int
    leased_until: datetime


@dataclass(frozen=True, slots=True, repr=False)
class PreparedCancellation:
    command_record_id: UUID
    command_id: str
    operation_id: UUID
    step_id: UUID
    connector_key: str
    capability_id: str
    capability_instance_ref: str
    plan_hash: str
    step_key: str
    provider_operation_ref: str
    target: Mapping[str, object]
    output_schema: CapabilitySchemaDocument
    reason: str
    config: dict[str, object]
    secret_refs: dict[str, str]
    attempt_number: int
    leased_until: datetime


@dataclass(frozen=True, slots=True)
class ProvisioningReceiptView:
    sequence: int
    receipt_kind: str
    step_key: str | None
    provider_operation_ref: str | None
    previous_receipt_hash: str | None
    receipt_hash: str
    plan_hash: str
    capability_instance_ref: str
    connector_key: str
    connector_version: str
    manifest_digest: str
    artifact_digest: str
    config_digest: str
    approval_digest: str
    evidence: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ProvisioningPlanReceiptView:
    command_id: str
    command_fingerprint: str
    capability_instance_ref: str
    request_body_digest: str
    result_digest: str
    receipt_hash: str


SecretResolver = Callable[[Mapping[str, str]], Mapping[str, str]]


def accept_provisioning_command(
    db: Session,
    command: ProvisioningCommand,
    *,
    registry: ConnectorRegistry,
    policy: ExecutionPolicy = DEFAULT_POLICY,
    now: datetime | None = None,
) -> CommandAcceptance:
    """Persist one command identity or replay the exact prior acceptance.

    A command row is a durable intake ledger, not a second at-most-once effect
    ledger: provider I/O is outside this transaction and ambiguity is recorded
    explicitly rather than hidden behind an "executed" marker.
    """

    moment = now or datetime.now(UTC)
    _validate_command_shape(command)
    fingerprint = _command_fingerprint(command)
    _require_approval(command, moment)
    existing_command = _find_command(db, command.command_id)
    if existing_command is not None:
        _require_same_command(existing_command, "apply", fingerprint)
        if existing_command.operation_id is None:
            raise ProvisioningRefused("accepted apply command has no operation")
        existing = db.get(ProvisioningOperation, existing_command.operation_id)
        if existing is None:
            raise ProvisioningRefused("accepted apply command operation disappeared")
        return CommandAcceptance(existing.id, existing.state, False)

    # Cross-binding receipt pins are produced only after the immutable plan was
    # approved.  Validate them before this command creates any state.  Locks are
    # acquired in UUID order so two downstream commands sharing prerequisites
    # cannot deadlock by presenting the same set in a different order.
    _require_prerequisite_receipts(db, command)

    installation, revision, binding = _resolve_execution_pin(
        db,
        capability_id=command.capability_id,
        capability_instance_ref=command.capability_instance_ref,
        binding_id=command.capability_binding_id,
        config_digest=command.config_digest,
        registry=registry,
    )
    _require_approved_execution_pin(
        command,
        installation=installation,
        revision=revision,
        binding=binding,
        registry=registry,
        policy=policy,
    )
    declaration = registry.plugin(installation.connector_key).manifest.require_declares(
        command.capability_id
    )
    _require_local_plan_validation(db, command)
    operation = ProvisioningOperation(
        apply_command_id=command.command_id,
        deployment_ref=command.deployment_ref,
        capability_id=command.capability_id,
        capability_instance_ref=command.capability_instance_ref,
        capability_binding_id=binding.id,
        desired_state_revision=command.desired_state_revision,
        desired_state_version_id=command.desired_state_version_id,
        desired_state_hash=command.desired_state_hash,
        saved_plan_id=command.saved_plan_id,
        approval_request_id=command.approval_request_id,
        approval_request_binding_hash=command.approval_request_binding_hash,
        plan_command_id=command.plan_command_id,
        plan_validation_receipt_id=command.plan_validation_receipt_id,
        plan_validation_receipt_digest=command.plan_validation_receipt_digest,
        plan_validation_request_body_digest=(
            command.plan_validation_request_body_digest
        ),
        module_plan_receipt_hash=command.module_plan_receipt_hash,
        profile_version_id=command.profile_version_id,
        profile_code=command.profile_code,
        profile_version=command.profile_version,
        profile_schema_version=command.profile_schema_version,
        profile_content_hash=command.profile_content_hash,
        command_schema_version=command.command_schema_version,
        capability_owner_code=command.capability_owner_code,
        capability_schema_version=command.capability_schema_version,
        capability_contract_attestation_id=(command.capability_contract_attestation_id),
        capability_contract_digest=command.capability_contract_digest,
        capability_operations_json=[
            _capability_operation_document(operation)
            for operation in command.capability_operations
        ],
        capability_schemas_json=[
            _capability_schema_document(document)
            for document in declaration.schema_documents
        ],
        prerequisite_evidence_bindings_json=[
            _evidence_binding_document(binding)
            for binding in command.prerequisite_evidence_bindings
        ],
        prerequisite_receipt_pins_json=[
            _prerequisite_pin_document(pin) for pin in command.prerequisite_receipt_pins
        ],
        installation_id=installation.id,
        installation_ref=command.installation_ref,
        expected_plan_hash=command.expected_plan_hash,
        approval_grant_ref=command.approval.grant_ref,
        approval_digest=command.approval.digest,
        approval_verified_at=cast(datetime, command.approval.verified_at),
        approval_expires_at=command.approval.expires_at,
        artifact_digest=command.artifact_digest,
        connector_key=command.connector_key,
        connector_version=command.connector_version,
        manifest_digest=command.connector_manifest_digest,
        config_revision_id=revision.id,
        config_digest=command.config_digest,
        configuration_snapshot_ref=command.configuration_snapshot_ref,
        configuration_schema_version=command.configuration_schema_version,
        configuration_hash=command.configuration_hash,
        component_artifact_digest=command.component_artifact_digest,
        execution_policy_digest=command.execution_policy_digest,
        approved_command_template_digest=(
            command.approval.approved_command_template_digest
        ),
        state="pending",
        next_attempt_at=moment,
    )

    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(operation)
            db.flush()
            db.add(
                ProvisioningCommandRecord(
                    command_id=command.command_id,
                    command_kind="apply",
                    command_fingerprint=fingerprint,
                    operation_id=operation.id,
                    request_json=_command_document(command),
                    state="accepted",
                )
            )
            for ordinal, step in enumerate(command.steps, start=1):
                db.add(
                    ProvisioningStep(
                        operation_id=operation.id,
                        step_key=step.step_key,
                        ordinal=ordinal,
                        endpoint_code=step.endpoint_code,
                        depends_on_json=list(step.depends_on),
                        input_digest=payload_digest(dict(step.input)),
                        input_json=dict(step.input),
                        resolved_input_digest=None,
                        state="pending",
                        next_attempt_at=moment,
                    )
                )
            db.flush()
            _append_receipt(
                db,
                operation=operation,
                step=None,
                receipt_kind="command_accepted",
                evidence={
                    "command_fingerprint": fingerprint,
                    "approval_verified_at": cast(
                        datetime, command.approval.verified_at
                    ).isoformat(),
                    "step_count": len(command.steps),
                    "prerequisite_receipt_pins": [
                        _prerequisite_pin_document(pin)
                        for pin in command.prerequisite_receipt_pins
                    ],
                },
            )
            db.flush()
    except IntegrityError:
        winner = _find_command(db, command.command_id)
        if winner is None:
            raise
        _require_same_command(winner, "apply", fingerprint)
        if winner.operation_id is None:
            raise ProvisioningRefused(
                "accepted apply command has no operation"
            ) from None
        winner_operation = db.get(ProvisioningOperation, winner.operation_id)
        if winner_operation is None:
            raise ProvisioningRefused(
                "accepted apply command operation disappeared"
            ) from None
        return CommandAcceptance(winner_operation.id, winner_operation.state, False)
    return CommandAcceptance(operation.id, operation.state, True)


def prepare_next_apply(
    db: Session,
    *,
    operation_id: UUID,
    registry: ConnectorRegistry,
    policy: ExecutionPolicy = DEFAULT_POLICY,
    now: datetime | None = None,
) -> PreparedProvisioning | None:
    """Validate a pin, then claim one dependency-ready step. Database only."""

    moment = now or datetime.now(UTC)
    operation = db.execute(
        select(ProvisioningOperation)
        .where(ProvisioningOperation.id == operation_id)
        .with_for_update()
    ).scalar_one_or_none()
    if operation is None:
        raise ProvisioningRefused(f"provisioning operation {operation_id} not found")
    if operation.state in {
        "succeeded",
        "terminal",
        "reconciliation_required",
        "cancelled",
    }:
        return None
    if operation.leased_until is not None and _aware(operation.leased_until) >= moment:
        return None
    if _aware(operation.approval_expires_at) <= moment:
        raise ApprovalRefused(
            f"approval {operation.approval_grant_ref!r} expired before provider I/O"
        )

    installation, revision, binding = _resolve_execution_pin(
        db,
        capability_id=operation.capability_id,
        capability_instance_ref=operation.capability_instance_ref,
        binding_id=operation.capability_binding_id,
        config_digest=operation.config_digest,
        registry=registry,
    )
    if (
        installation.connector_key != operation.connector_key
        or installation.connector_version != operation.connector_version
        or _canonical_stored_digest(installation.manifest_digest)
        != operation.manifest_digest
        or installation.connector_artifact_digest != operation.artifact_digest
        or revision.id != operation.config_revision_id
        or execution_policy_digest(policy) != operation.execution_policy_digest
    ):
        raise ProvisioningRefused(
            "connector or configuration pin changed after approval; build and "
            "approve a new plan rather than executing stale intent"
        )

    steps = list(
        db.scalars(
            select(ProvisioningStep)
            .where(ProvisioningStep.operation_id == operation.id)
            .order_by(ProvisioningStep.ordinal)
        )
    )
    states = {step.step_key: step.state for step in steps}
    candidate = next(
        (
            step
            for step in steps
            if step.state in {"pending", "retryable", "in_flight"}
            and (step.next_attempt_at is None or _aware(step.next_attempt_at) <= moment)
            and (step.leased_until is None or _aware(step.leased_until) < moment)
            and all(
                states.get(str(dependency)) == "succeeded"
                for dependency in step.depends_on_json
            )
        ),
        None,
    )
    if candidate is None:
        return None
    resolved_input = _resolve_prerequisite_inputs(
        db,
        operation=operation,
        step=candidate,
    )
    resolved_input_digest = "sha256:" + payload_digest(resolved_input)
    apply_operation = _stored_operation_pin(operation, "apply")
    input_schema = _stored_schema(
        operation,
        schema_ref=apply_operation.input_schema_ref,
        schema_digest=apply_operation.input_schema_digest,
    )
    output_schema = _stored_schema(
        operation,
        schema_ref=apply_operation.output_schema_ref,
        schema_digest=apply_operation.output_schema_digest,
    )
    _validate_json_schema_instance(
        input_schema,
        resolved_input,
        context="resolved apply input",
    )
    command_record = db.execute(
        select(ProvisioningCommandRecord)
        .where(ProvisioningCommandRecord.command_id == operation.apply_command_id)
        .with_for_update()
    ).scalar_one()
    attempt_number = candidate.attempt_count + 1
    lease = moment + timedelta(seconds=policy.lease_seconds)
    result = cast(
        CursorResult[tuple[()]],
        db.execute(
            update(ProvisioningStep)
            .where(
                ProvisioningStep.id == candidate.id,
                ProvisioningStep.state == candidate.state,
                ProvisioningStep.attempt_count == candidate.attempt_count,
                or_(
                    ProvisioningStep.leased_until.is_(None),
                    ProvisioningStep.leased_until < moment,
                ),
            )
            .values(
                state="in_flight",
                attempt_count=attempt_number,
                leased_until=lease,
                next_attempt_at=None,
                resolved_input_digest=resolved_input_digest,
            )
            .execution_options(synchronize_session=False)
        ),
    )
    if result.rowcount != 1:
        return None
    operation.state = "in_flight"
    operation.attempt_count += 1
    operation.leased_until = lease
    operation.next_attempt_at = None
    command_record.state = "in_flight"
    db.flush()
    db.refresh(candidate)
    return PreparedProvisioning(
        operation_id=operation.id,
        step_id=candidate.id,
        command_id=operation.apply_command_id,
        deployment_ref=operation.deployment_ref,
        connector_key=operation.connector_key,
        connector_version=operation.connector_version,
        manifest_digest=operation.manifest_digest,
        capability_id=operation.capability_id,
        capability_instance_ref=operation.capability_instance_ref,
        binding_id=binding.id,
        config_revision_id=revision.id,
        config_digest=operation.config_digest,
        plan_hash=operation.expected_plan_hash,
        artifact_digest=operation.artifact_digest,
        approval_digest=operation.approval_digest,
        step=ProvisionStep(
            step_key=candidate.step_key,
            endpoint_code=candidate.endpoint_code,
            depends_on=tuple(str(item) for item in candidate.depends_on_json),
            input=resolved_input,
        ),
        output_schema=output_schema,
        resolved_input_digest=resolved_input_digest,
        config=dict(revision.config_json),
        secret_refs={str(k): str(v) for k, v in revision.secret_refs.items()},
        attempt_number=attempt_number,
        leased_until=lease,
    )


def invoke_prepared_provisioning(
    prepared: PreparedProvisioning,
    *,
    registry: ConnectorRegistry,
    resolve_secrets: SecretResolver,
) -> ProvisioningResult:
    """Call the connector with no session in scope or in the request shape."""

    plugin = registry.plugin(prepared.connector_key)
    require_mode(plugin, ConnectorMode.PROVISION)
    handler = cast(ProvisionPlugin, plugin).provisioning_handler_for(
        prepared.capability_id
    )
    request = ProvisionApplyRequest(
        capability_id=prepared.capability_id,
        command_id=prepared.command_id,
        operation_ref=str(prepared.operation_id),
        plan_hash=prepared.plan_hash,
        step=prepared.step,
        config=prepared.config,
        secrets=dict(resolve_secrets(prepared.secret_refs)),
        idempotency_key=f"{prepared.command_id}/{prepared.step.step_key}",
    )
    try:
        result = handler.apply(request)
    except Exception as exc:
        return ProvisioningResult(
            status=ProvisionResultStatus.AMBIGUOUS,
            error_code="connector_raised",
            error_detail=_connector_error_detail(exc),
        )
    if not isinstance(result, ProvisioningResult):
        return ProvisioningResult(
            status=ProvisionResultStatus.AMBIGUOUS,
            error_code="connector_contract",
            error_detail="invalid_result_type",
        )
    try:
        _validate_result(result, output_schema=prepared.output_schema)
    except ProvisioningRefused:
        return ProvisioningResult(
            status=ProvisionResultStatus.AMBIGUOUS,
            error_code="connector_contract",
            error_detail="unsafe_result",
        )
    # Connector exception and error prose can contain materialized secrets.
    # Codes and structured evidence survive; arbitrary prose does not.
    return ProvisioningResult(
        status=result.status,
        provider_operation_ref=result.provider_operation_ref,
        evidence=dict(result.evidence),
        error_code=result.error_code,
        error_detail=None,
    )


def settle_provisioning(
    db: Session,
    *,
    prepared: PreparedProvisioning,
    result: ProvisioningResult,
    policy: ExecutionPolicy = DEFAULT_POLICY,
    now: datetime | None = None,
) -> ProvisioningOperation:
    """Conditionally settle a claimed step and append immutable evidence."""

    moment = now or datetime.now(UTC)
    _validate_result(result, output_schema=prepared.output_schema)
    result = _result_safe_for_settlement(result)
    public_evidence = (
        _public_non_secret_projection(
            prepared.output_schema,
            dict(result.evidence),
        )
        if result.status is ProvisionResultStatus.SUCCEEDED
        else {}
    )
    operation = db.execute(
        select(ProvisioningOperation)
        .where(ProvisioningOperation.id == prepared.operation_id)
        .with_for_update()
    ).scalar_one_or_none()
    step = db.execute(
        select(ProvisioningStep)
        .where(ProvisioningStep.id == prepared.step_id)
        .with_for_update()
    ).scalar_one_or_none()
    if operation is None or step is None:
        raise LostProvisioningClaim("provisioning operation or step disappeared")
    if (
        step.state != "in_flight"
        or step.attempt_count != prepared.attempt_number
        or step.leased_until is None
        or _aware(step.leased_until) < moment
        or operation.leased_until is None
        or _aware(operation.leased_until) < moment
    ):
        raise LostProvisioningClaim(
            "this worker no longer owns the provisioning step claim"
        )
    command_record = db.execute(
        select(ProvisioningCommandRecord)
        .where(ProvisioningCommandRecord.command_id == prepared.command_id)
        .with_for_update()
    ).scalar_one()

    state, receipt_kind = _settled_state(result, prepared.attempt_number, policy)
    next_attempt_at: datetime | None = None
    completed_at: datetime | None = None
    provider_ref = result.provider_operation_ref
    if state == "retryable":
        next_attempt_at = moment + timedelta(
            seconds=retry_delay_seconds(prepared.attempt_number, policy=policy)
        )
    elif state in {
        "succeeded",
        "terminal",
        "reconciliation_required",
        "cancelled",
    }:
        completed_at = moment

    settled = cast(
        CursorResult[tuple[()]],
        db.execute(
            update(ProvisioningStep)
            .where(
                ProvisioningStep.id == prepared.step_id,
                ProvisioningStep.state == "in_flight",
                ProvisioningStep.attempt_count == prepared.attempt_number,
                ProvisioningStep.leased_until.is_not(None),
                ProvisioningStep.leased_until >= moment,
            )
            .values(
                state=state,
                leased_until=None,
                next_attempt_at=next_attempt_at,
                provider_operation_ref=provider_ref,
                error_code=result.error_code,
                error_detail=result.error_detail,
                completed_at=completed_at,
            )
            .execution_options(synchronize_session=False)
        ),
    )
    if settled.rowcount != 1:
        raise LostProvisioningClaim(
            "another worker settled the provisioning step first"
        )
    db.refresh(step)
    operation.leased_until = None
    operation.error_code = result.error_code
    operation.error_detail = result.error_detail
    if state == "succeeded":
        unfinished = db.scalar(
            select(func.count())
            .select_from(ProvisioningStep)
            .where(
                ProvisioningStep.operation_id == operation.id,
                ProvisioningStep.state != "succeeded",
            )
        )
        operation.state = "succeeded" if int(unfinished or 0) == 0 else "pending"
        operation.next_attempt_at = None if operation.state == "succeeded" else moment
        operation.completed_at = moment if operation.state == "succeeded" else None
    else:
        operation.state = state
        operation.next_attempt_at = next_attempt_at
        operation.completed_at = completed_at
    if state == "retryable":
        command_record.state = "accepted"
    else:
        command_record.state = "settled"
        command_record.completed_at = moment
    _append_receipt(
        db,
        operation=operation,
        step=step,
        receipt_kind=receipt_kind,
        evidence={
            "status": result.status.value,
            "provider_operation_ref": result.provider_operation_ref,
            "error_code": result.error_code,
            "error_detail": result.error_detail,
            "provider_evidence_digest": (
                "sha256:" + payload_digest(dict(result.evidence))
            ),
            "public_evidence": public_evidence,
            "resolved_input_digest": prepared.resolved_input_digest,
        },
    )
    db.flush()
    return operation


def prepare_provisioning_plan(
    db: Session,
    *,
    command_id: str,
    deployment_ref: str,
    request_body_digest: str,
    capability_id: str,
    capability_instance_ref: str,
    binding_id: UUID,
    config_digest: str,
    plan_hash: str,
    steps: tuple[ProvisionStep, ...],
    registry: ConnectorRegistry,
) -> PreparedPlan | None:
    """Record one plan command and copy its connector pin. Database only."""

    _require_command_id(command_id)
    if not deployment_ref.strip():
        raise ProvisioningRefused("a deployment reference is required")
    _require_instance_ref(capability_instance_ref)
    _require_canonical_digest(request_body_digest, "PLAN request body digest")
    _require_digest(plan_hash, "plan hash")
    _validate_steps_for_capability(steps, capability_id=capability_id)
    document: dict[str, object] = {
        "deployment_ref": deployment_ref,
        "request_body_digest": request_body_digest,
        "capability_id": capability_id,
        "capability_instance_ref": capability_instance_ref,
        "binding_id": str(binding_id),
        "config_digest": config_digest,
        "plan_hash": plan_hash,
        "steps": [_step_document(step) for step in steps],
    }
    fingerprint = hashlib.sha256(_canonical(document)).hexdigest()
    prior = _find_command(db, command_id)
    if prior is not None:
        _require_same_command(prior, "plan", fingerprint)
        if prior.state == "settled":
            return None
    installation, revision, binding = _resolve_execution_pin(
        db,
        capability_id=capability_id,
        capability_instance_ref=capability_instance_ref,
        binding_id=binding_id,
        config_digest=config_digest,
        registry=registry,
    )
    declaration = registry.plugin(installation.connector_key).manifest.require_declares(
        capability_id
    )
    input_schema, output_schema = _declared_operation_schemas(declaration, "plan")
    for step in steps:
        _validate_json_schema_instance(
            input_schema,
            dict(step.input),
            context=f"plan input for step {step.step_key!r}",
        )
    try:
        verify_capability_configuration(
            declaration,
            config=revision.config_json or {},
            secret_refs=revision.secret_refs or {},
            required_operation_codes=("plan",),
        )
    except ValueError as exc:
        raise ProvisioningRefused(f"capability configuration refused: {exc}") from None
    record, is_new = _accept_action_command(
        db,
        command_id=command_id,
        command_kind="plan",
        fingerprint=fingerprint,
        request_json=document,
    )
    if not is_new and record.state == "settled":
        return None
    record.state = "in_flight"
    db.flush()
    return PreparedPlan(
        command_record_id=record.id,
        command_id=command_id,
        deployment_ref=deployment_ref,
        request_body_digest=request_body_digest,
        connector_key=installation.connector_key,
        connector_version=installation.connector_version,
        manifest_digest=installation.manifest_digest,
        capability_id=capability_id,
        capability_instance_ref=capability_instance_ref,
        binding_id=binding.id,
        config_revision_id=revision.id,
        config_digest=config_digest,
        plan_hash=plan_hash,
        steps=steps,
        input_schema=input_schema,
        output_schema=output_schema,
        config=dict(revision.config_json),
        secret_refs={str(k): str(v) for k, v in revision.secret_refs.items()},
    )


def invoke_prepared_plan(
    prepared: PreparedPlan,
    *,
    registry: ConnectorRegistry,
    resolve_secrets: SecretResolver,
) -> ProvisionPlanResult:
    """Invoke connector planning without a database session in scope."""

    plugin = registry.plugin(prepared.connector_key)
    require_mode(plugin, ConnectorMode.PROVISION)
    handler = cast(ProvisionPlugin, plugin).provisioning_handler_for(
        prepared.capability_id
    )
    request = ProvisionPlanRequest(
        capability_id=prepared.capability_id,
        command_id=prepared.command_id,
        plan_hash=prepared.plan_hash,
        steps=prepared.steps,
        config=prepared.config,
        secrets=dict(resolve_secrets(prepared.secret_refs)),
    )
    try:
        result = handler.plan(request)
    except Exception as exc:
        raise ProvisioningRefused(
            f"connector plan raised {_connector_error_detail(exc)}"
        ) from None
    if not isinstance(result, ProvisionPlanResult):
        raise ProvisioningRefused("connector plan returned an invalid result type")
    _validate_plan_result(result, output_schema=prepared.output_schema)
    return result


def settle_provisioning_plan(
    db: Session,
    *,
    prepared: PreparedPlan,
    result: ProvisionPlanResult,
) -> ProvisionPlanResult:
    """Recheck the execution pin and close the exact plan command."""

    _validate_plan_result(result, output_schema=prepared.output_schema)
    record = db.execute(
        select(ProvisioningCommandRecord)
        .where(ProvisioningCommandRecord.id == prepared.command_record_id)
        .with_for_update()
    ).scalar_one_or_none()
    if record is None or record.state != "in_flight":
        raise LostProvisioningClaim("plan command is no longer in flight")
    installation, revision, _ = _resolve_stored_pin(
        db,
        capability_id=prepared.capability_id,
        capability_instance_ref=prepared.capability_instance_ref,
        binding_id=prepared.binding_id,
        config_digest=prepared.config_digest,
    )
    if (
        installation.connector_version != prepared.connector_version
        or installation.manifest_digest != prepared.manifest_digest
        or revision.id != prepared.config_revision_id
    ):
        raise ProvisioningRefused("connector or configuration pin changed during plan")
    if result.plan_hash != prepared.plan_hash:
        raise ProvisioningRefused(
            "connector plan hash differs from requested plan hash"
        )
    if result.steps != prepared.steps:
        raise ProvisioningRefused("connector changed the provider-neutral plan steps")
    evidence_digest = _canonical_digest(dict(result.evidence))
    result_digest = _canonical_digest(
        {
            "evidence_digest": evidence_digest,
            "plan_hash": result.plan_hash,
            "steps": [_step_document(step) for step in result.steps],
        }
    )
    receipt_material = {
        "command_id": record.command_id,
        "command_fingerprint": record.command_fingerprint,
        "capability_instance_ref": prepared.capability_instance_ref,
        "request_body_digest": prepared.request_body_digest,
        "result_digest": result_digest,
    }
    db.add(
        ProvisioningCommandReceipt(
            command_record_id=record.id,
            command_id=record.command_id,
            command_fingerprint=record.command_fingerprint,
            capability_instance_ref=prepared.capability_instance_ref,
            request_body_digest=prepared.request_body_digest,
            result_digest=result_digest,
            receipt_hash=_canonical_digest(receipt_material),
        )
    )
    record.state = "settled"
    record.completed_at = datetime.now(UTC)
    db.flush()
    return result


def prepare_next_observation(
    db: Session,
    *,
    command_id: str,
    operation_id: UUID,
    expected: ExpectedProvisioningPin,
    registry: ConnectorRegistry,
    policy: ExecutionPolicy = DEFAULT_POLICY,
    now: datetime | None = None,
) -> PreparedObservation | None:
    """Claim one remote observation under the operation row lock."""

    moment = now or datetime.now(UTC)
    _require_command_id(command_id)
    prior = _find_command(db, command_id)
    if prior is not None:
        if (
            prior.command_kind != "observe"
            or prior.request_json.get("operation_id") != str(operation_id)
            or any(
                prior.request_json.get(key) != value
                for key, value in _expected_pin_document(expected).items()
            )
        ):
            raise CommandIdentityCollision(
                f"command id {command_id!r} was reused with different content"
            )
        if prior.state == "settled":
            return None
    operation = _locked_operation(db, operation_id)
    if operation is None or operation.state in _TERMINAL_STATES:
        return None
    if operation.leased_until is not None and _aware(operation.leased_until) >= moment:
        return None
    step = (
        db.execute(
            select(ProvisioningStep)
            .where(
                ProvisioningStep.operation_id == operation.id,
                ProvisioningStep.state.in_(
                    ("observing", "observe_retryable", "observe_in_flight")
                ),
            )
            .order_by(ProvisioningStep.ordinal)
            .with_for_update()
        )
        .scalars()
        .first()
    )
    if step is None or not step.provider_operation_ref:
        return None
    _require_expected_pin(operation, step, expected)
    if step.next_attempt_at is not None and _aware(step.next_attempt_at) > moment:
        return None
    if step.leased_until is not None and _aware(step.leased_until) >= moment:
        return None
    document: dict[str, object] = {
        "operation_id": str(operation.id),
        "step_id": str(step.id),
        "provider_operation_ref": step.provider_operation_ref,
        "plan_hash": operation.expected_plan_hash,
        **_expected_pin_document(expected),
    }
    fingerprint = hashlib.sha256(_canonical(document)).hexdigest()
    installation, revision, _ = _resolve_operation_pin(
        db,
        operation,
        registry,
        required_operation_code="observe",
        policy=policy,
    )
    operation_pin = _stored_operation_pin(operation, "observe")
    input_schema = _stored_schema(
        operation,
        schema_ref=operation_pin.input_schema_ref,
        schema_digest=operation_pin.input_schema_digest,
    )
    output_schema = _stored_schema(
        operation,
        schema_ref=operation_pin.output_schema_ref,
        schema_digest=operation_pin.output_schema_digest,
    )
    target = _operation_target(
        input_schema,
        step.input_json,
        context="observe target",
    )
    record, is_new = _accept_action_command(
        db,
        command_id=command_id,
        command_kind="observe",
        fingerprint=fingerprint,
        request_json=document,
        operation_id=operation.id,
        step_id=step.id,
    )
    if not is_new and record.state == "settled":
        return None
    attempt = step.attempt_count + 1
    lease = moment + timedelta(seconds=policy.lease_seconds)
    step.state = "observe_in_flight"
    step.attempt_count = attempt
    step.leased_until = lease
    step.next_attempt_at = None
    operation.state = "observe_in_flight"
    operation.leased_until = lease
    operation.attempt_count += 1
    record.state = "in_flight"
    db.flush()
    return PreparedObservation(
        command_record_id=record.id,
        command_id=command_id,
        operation_id=operation.id,
        step_id=step.id,
        connector_key=installation.connector_key,
        capability_id=operation.capability_id,
        capability_instance_ref=operation.capability_instance_ref,
        plan_hash=operation.expected_plan_hash,
        step_key=step.step_key,
        provider_operation_ref=step.provider_operation_ref,
        target=MappingProxyType(target),
        output_schema=output_schema,
        config=dict(revision.config_json),
        secret_refs={str(k): str(v) for k, v in revision.secret_refs.items()},
        attempt_number=attempt,
        leased_until=lease,
    )


def invoke_prepared_observation(
    prepared: PreparedObservation,
    *,
    registry: ConnectorRegistry,
    resolve_secrets: SecretResolver,
) -> ProvisioningResult:
    """Observe one provider operation with no database session in scope."""

    plugin = registry.plugin(prepared.connector_key)
    require_mode(plugin, ConnectorMode.PROVISION)
    handler = cast(ProvisionPlugin, plugin).provisioning_handler_for(
        prepared.capability_id
    )
    request = ProvisionObserveRequest(
        capability_id=prepared.capability_id,
        command_id=prepared.command_id,
        operation_ref=str(prepared.operation_id),
        plan_hash=prepared.plan_hash,
        step_key=prepared.step_key,
        provider_operation_ref=prepared.provider_operation_ref,
        target=prepared.target,
        config=prepared.config,
        secrets=dict(resolve_secrets(prepared.secret_refs)),
    )
    return _invoke_result(
        lambda: handler.observe(request),
        output_schema=prepared.output_schema,
        success_statuses=_OBSERVE_OUTPUT_STATUSES,
        context="connector observe evidence",
    )


def settle_observation(
    db: Session,
    *,
    prepared: PreparedObservation,
    result: ProvisioningResult,
    policy: ExecutionPolicy = DEFAULT_POLICY,
    now: datetime | None = None,
) -> ProvisioningOperation:
    """Settle a remote observation and append its receipt under one row lock."""

    moment = now or datetime.now(UTC)
    _validate_result(
        result,
        output_schema=prepared.output_schema,
        success_statuses=_OBSERVE_OUTPUT_STATUSES,
        context="connector observe evidence",
    )
    result = _result_safe_for_settlement(result)
    public_evidence = _result_public_evidence(
        result,
        output_schema=prepared.output_schema,
        success_statuses=_OBSERVE_OUTPUT_STATUSES,
    )
    operation, step, record = _locked_action(db, prepared, "observe_in_flight", moment)
    status = result.status
    next_attempt: datetime | None = None
    if status is ProvisionResultStatus.SUCCEEDED:
        step.state = "succeeded"
        step.completed_at = moment
        _finish_successful_step(db, operation, moment)
        receipt_kind = "observation_succeeded"
    elif status in {ProvisionResultStatus.ACCEPTED, ProvisionResultStatus.PENDING}:
        step.state = "observing"
        operation.state = "observing"
        next_attempt = moment + timedelta(
            seconds=retry_delay_seconds(prepared.attempt_number, policy=policy)
        )
        receipt_kind = "observation_pending"
    elif status is ProvisionResultStatus.RETRYABLE:
        step.state = "observe_retryable"
        operation.state = "observe_retryable"
        next_attempt = moment + timedelta(
            seconds=retry_delay_seconds(prepared.attempt_number, policy=policy)
        )
        receipt_kind = "observation_retryable"
    elif status is ProvisionResultStatus.TERMINAL:
        step.state = operation.state = "terminal"
        operation.completed_at = step.completed_at = moment
        receipt_kind = "observation_terminal"
    else:
        step.state = operation.state = "reconciliation_required"
        operation.completed_at = step.completed_at = moment
        receipt_kind = "reconciliation_required"
    step.leased_until = operation.leased_until = None
    step.next_attempt_at = operation.next_attempt_at = next_attempt
    step.error_code = operation.error_code = result.error_code
    step.error_detail = operation.error_detail = result.error_detail
    record.state = (
        "settled" if status is not ProvisionResultStatus.RETRYABLE else "accepted"
    )
    record.completed_at = moment if record.state == "settled" else None
    _append_result_receipt(
        db,
        operation,
        step,
        receipt_kind,
        result,
        public_evidence=public_evidence,
    )
    db.flush()
    return operation


def prepare_cancellation(
    db: Session,
    *,
    command_id: str,
    operation_id: UUID,
    expected: ExpectedProvisioningPin,
    reason: str,
    registry: ConnectorRegistry,
    policy: ExecutionPolicy = DEFAULT_POLICY,
    now: datetime | None = None,
) -> PreparedCancellation | None:
    """Claim cancellation of one provider operation. Database only."""

    moment = now or datetime.now(UTC)
    _require_command_id(command_id)
    if _CODE.fullmatch(reason) is None:
        raise ProvisioningRefused("cancellation reason must be a provider-neutral code")
    prior = _find_command(db, command_id)
    if prior is not None:
        if (
            prior.command_kind != "cancel"
            or prior.request_json.get("operation_id") != str(operation_id)
            or prior.request_json.get("reason") != reason
            or any(
                prior.request_json.get(key) != value
                for key, value in _expected_pin_document(expected).items()
            )
        ):
            raise CommandIdentityCollision(
                f"command id {command_id!r} was reused with different content"
            )
        if prior.state == "settled":
            return None
    operation = _locked_operation(db, operation_id)
    if operation is None or operation.state in _TERMINAL_STATES:
        return None
    if operation.leased_until is not None and _aware(operation.leased_until) >= moment:
        return None
    step = (
        db.execute(
            select(ProvisioningStep)
            .where(
                ProvisioningStep.operation_id == operation.id,
                ProvisioningStep.state.in_(
                    (
                        "observing",
                        "observe_retryable",
                        "cancel_retryable",
                        "cancel_in_flight",
                    )
                ),
            )
            .order_by(ProvisioningStep.ordinal)
            .with_for_update()
        )
        .scalars()
        .first()
    )
    if step is None or not step.provider_operation_ref:
        return None
    _require_expected_pin(operation, step, expected)
    if step.leased_until is not None and _aware(step.leased_until) >= moment:
        return None
    document: dict[str, object] = {
        "operation_id": str(operation.id),
        "step_id": str(step.id),
        "provider_operation_ref": step.provider_operation_ref,
        "reason": reason,
        "plan_hash": operation.expected_plan_hash,
        **_expected_pin_document(expected),
    }
    fingerprint = hashlib.sha256(_canonical(document)).hexdigest()
    installation, revision, _ = _resolve_operation_pin(
        db,
        operation,
        registry,
        required_operation_code="cancel",
        policy=policy,
    )
    operation_pin = _stored_operation_pin(operation, "cancel")
    input_schema = _stored_schema(
        operation,
        schema_ref=operation_pin.input_schema_ref,
        schema_digest=operation_pin.input_schema_digest,
    )
    output_schema = _stored_schema(
        operation,
        schema_ref=operation_pin.output_schema_ref,
        schema_digest=operation_pin.output_schema_digest,
    )
    target = _operation_target(
        input_schema,
        step.input_json,
        context="cancel target",
    )
    record, is_new = _accept_action_command(
        db,
        command_id=command_id,
        command_kind="cancel",
        fingerprint=fingerprint,
        request_json=document,
        operation_id=operation.id,
        step_id=step.id,
    )
    if not is_new and record.state == "settled":
        return None
    attempt = step.attempt_count + 1
    lease = moment + timedelta(seconds=policy.lease_seconds)
    step.state = "cancel_in_flight"
    step.attempt_count = attempt
    step.leased_until = lease
    step.next_attempt_at = None
    operation.state = "cancel_in_flight"
    operation.leased_until = lease
    operation.attempt_count += 1
    record.state = "in_flight"
    db.flush()
    return PreparedCancellation(
        command_record_id=record.id,
        command_id=command_id,
        operation_id=operation.id,
        step_id=step.id,
        connector_key=installation.connector_key,
        capability_id=operation.capability_id,
        capability_instance_ref=operation.capability_instance_ref,
        plan_hash=operation.expected_plan_hash,
        step_key=step.step_key,
        provider_operation_ref=step.provider_operation_ref,
        target=MappingProxyType(target),
        output_schema=output_schema,
        reason=reason,
        config=dict(revision.config_json),
        secret_refs={str(k): str(v) for k, v in revision.secret_refs.items()},
        attempt_number=attempt,
        leased_until=lease,
    )


def invoke_prepared_cancellation(
    prepared: PreparedCancellation,
    *,
    registry: ConnectorRegistry,
    resolve_secrets: SecretResolver,
) -> ProvisioningResult:
    """Cancel one provider operation with no database session in scope."""

    plugin = registry.plugin(prepared.connector_key)
    require_mode(plugin, ConnectorMode.PROVISION)
    handler = cast(ProvisionPlugin, plugin).provisioning_handler_for(
        prepared.capability_id
    )
    request = ProvisionCancelRequest(
        capability_id=prepared.capability_id,
        command_id=prepared.command_id,
        operation_ref=str(prepared.operation_id),
        plan_hash=prepared.plan_hash,
        step_key=prepared.step_key,
        provider_operation_ref=prepared.provider_operation_ref,
        target=prepared.target,
        reason=prepared.reason,
        idempotency_key=f"{prepared.command_id}/{prepared.step_key}",
        config=prepared.config,
        secrets=dict(resolve_secrets(prepared.secret_refs)),
    )
    return _invoke_result(
        lambda: handler.cancel(request),
        output_schema=prepared.output_schema,
        success_statuses=_CANCEL_OUTPUT_STATUSES,
        context="connector cancel evidence",
    )


def settle_cancellation(
    db: Session,
    *,
    prepared: PreparedCancellation,
    result: ProvisioningResult,
    policy: ExecutionPolicy = DEFAULT_POLICY,
    now: datetime | None = None,
) -> ProvisioningOperation:
    """Settle cancellation and append its immutable receipt."""

    moment = now or datetime.now(UTC)
    _validate_result(
        result,
        output_schema=prepared.output_schema,
        success_statuses=_CANCEL_OUTPUT_STATUSES,
        context="connector cancel evidence",
    )
    result = _result_safe_for_settlement(result)
    public_evidence = _result_public_evidence(
        result,
        output_schema=prepared.output_schema,
        success_statuses=_CANCEL_OUTPUT_STATUSES,
    )
    operation, step, record = _locked_action(db, prepared, "cancel_in_flight", moment)
    status = result.status
    next_attempt: datetime | None = None
    if status in {
        ProvisionResultStatus.SUCCEEDED,
        ProvisionResultStatus.CANCELLED,
        ProvisionResultStatus.NOT_FOUND,
    }:
        step.state = operation.state = "cancelled"
        operation.completed_at = step.completed_at = moment
        receipt_kind = "step_cancelled"
    elif status in {
        ProvisionResultStatus.ACCEPTED,
        ProvisionResultStatus.PENDING,
        ProvisionResultStatus.RETRYABLE,
    }:
        step.state = operation.state = "cancel_retryable"
        next_attempt = moment + timedelta(
            seconds=retry_delay_seconds(prepared.attempt_number, policy=policy)
        )
        receipt_kind = "cancellation_retryable"
    elif status is ProvisionResultStatus.TERMINAL:
        step.state = operation.state = "terminal"
        operation.completed_at = step.completed_at = moment
        receipt_kind = "cancellation_terminal"
    else:
        step.state = operation.state = "reconciliation_required"
        operation.completed_at = step.completed_at = moment
        receipt_kind = "reconciliation_required"
    step.leased_until = operation.leased_until = None
    step.next_attempt_at = operation.next_attempt_at = next_attempt
    step.error_code = operation.error_code = result.error_code
    step.error_detail = operation.error_detail = result.error_detail
    record.state = "accepted" if next_attempt is not None else "settled"
    record.completed_at = None if next_attempt is not None else moment
    _append_result_receipt(
        db,
        operation,
        step,
        receipt_kind,
        result,
        public_evidence=public_evidence,
    )
    db.flush()
    return operation


def read_provisioning_receipts(
    db: Session, *, operation_id: UUID
) -> tuple[ProvisioningReceiptView, ...]:
    """Project immutable structured receipts without exposing ORM mutation."""

    rows = tuple(
        db.scalars(
            select(ProvisioningReceipt)
            .where(ProvisioningReceipt.operation_id == operation_id)
            .order_by(ProvisioningReceipt.sequence)
        )
    )
    _verify_receipt_chain(rows)
    return tuple(
        ProvisioningReceiptView(
            sequence=row.sequence,
            receipt_kind=row.receipt_kind,
            step_key=row.step_key,
            provider_operation_ref=row.provider_operation_ref,
            previous_receipt_hash=row.previous_receipt_hash,
            receipt_hash=row.receipt_hash,
            plan_hash=row.plan_hash,
            capability_instance_ref=row.capability_instance_ref,
            connector_key=row.connector_key,
            connector_version=row.connector_version,
            manifest_digest=row.manifest_digest,
            artifact_digest=row.artifact_digest,
            config_digest=row.config_digest,
            approval_digest=row.approval_digest,
            evidence=MappingProxyType(dict(row.evidence_json)),
        )
        for row in rows
    )


def read_provisioning_plan_receipt(
    db: Session, *, command_id: str
) -> ProvisioningPlanReceiptView:
    """Read and re-verify the immutable PLAN settlement receipt."""

    row = db.execute(
        select(ProvisioningCommandReceipt).where(
            ProvisioningCommandReceipt.command_id == command_id
        )
    ).scalar_one_or_none()
    if row is None:
        raise ProvisioningRefused(
            f"settled PLAN command {command_id!r} has no receipt evidence"
        )
    material = {
        "command_id": row.command_id,
        "command_fingerprint": row.command_fingerprint,
        "capability_instance_ref": row.capability_instance_ref,
        "request_body_digest": row.request_body_digest,
        "result_digest": row.result_digest,
    }
    if row.receipt_hash != _canonical_digest(material):
        raise ProvisioningRefused(
            f"PLAN receipt for command {command_id!r} has an invalid hash"
        )
    return ProvisioningPlanReceiptView(
        command_id=row.command_id,
        command_fingerprint=row.command_fingerprint,
        capability_instance_ref=row.capability_instance_ref,
        request_body_digest=row.request_body_digest,
        result_digest=row.result_digest,
        receipt_hash=row.receipt_hash,
    )


def _verify_receipt_chain(rows: tuple[ProvisioningReceipt, ...]) -> None:
    prior_hash: str | None = None
    for expected_sequence, row in enumerate(rows, start=1):
        material = {
            "operation_id": str(row.operation_id),
            "step_id": str(row.step_id) if row.step_id is not None else None,
            "sequence": row.sequence,
            "receipt_kind": row.receipt_kind,
            "step_key": row.step_key,
            "provider_operation_ref": row.provider_operation_ref,
            "previous_receipt_hash": row.previous_receipt_hash,
            "plan_hash": row.plan_hash,
            "capability_instance_ref": row.capability_instance_ref,
            "connector_key": row.connector_key,
            "connector_version": row.connector_version,
            "manifest_digest": row.manifest_digest,
            "artifact_digest": row.artifact_digest,
            "config_digest": row.config_digest,
            "approval_digest": row.approval_digest,
            "evidence": row.evidence_json,
        }
        expected_hash = "sha256:" + hashlib.sha256(_canonical(material)).hexdigest()
        if (
            row.sequence != expected_sequence
            or row.previous_receipt_hash != prior_hash
            or row.receipt_hash != expected_hash
        ):
            raise ProvisioningRefused(
                f"provisioning receipt chain for {row.operation_id} is invalid"
            )
        prior_hash = row.receipt_hash


def _resolve_execution_pin(
    db: Session,
    *,
    capability_id: str,
    capability_instance_ref: str,
    binding_id: UUID,
    config_digest: str,
    registry: ConnectorRegistry,
) -> tuple[ConnectorInstallation, ConnectorConfigRevision, CapabilityBinding]:
    try:
        binding = resolve_binding(
            db,
            capability_id=capability_id,
            capability_instance_ref=capability_instance_ref,
            capability_binding_id=binding_id,
        )
    except (SelectionError, ValueError) as exc:
        raise ProvisioningRefused(str(exc)) from None
    installation = db.get(ConnectorInstallation, binding.installation_id)
    if installation is None:
        raise ProvisioningRefused("capability binding has no installation")
    plugin = registry.plugin(installation.connector_key)
    registry.require_compatible(installation.connector_key)
    if not accepts_manifest_digest(plugin, installation.manifest_digest):
        raise ProvisioningRefused(
            "connector no longer honours the installation manifest pin"
        )
    require_mode(plugin, ConnectorMode.PROVISION)
    revision = (
        db.get(ConnectorConfigRevision, installation.current_config_revision_id)
        if installation.current_config_revision_id is not None
        else None
    )
    if revision is None or _canonical_stored_digest(revision.config_digest) != (
        config_digest
    ):
        raise ProvisioningRefused(
            "approved configuration digest is stale or not selected"
        )
    return installation, revision, binding


def _require_approved_execution_pin(
    command: ProvisioningCommand,
    *,
    installation: ConnectorInstallation,
    revision: ConnectorConfigRevision,
    binding: CapabilityBinding,
    registry: ConnectorRegistry,
    policy: ExecutionPolicy,
) -> None:
    plugin = registry.plugin(installation.connector_key)
    declaration = plugin.manifest.require_declares(command.capability_id)
    try:
        verified = verify_capability_configuration(
            declaration,
            config=revision.config_json or {},
            secret_refs=revision.secret_refs or {},
            required_operation_codes=("apply",),
        )
    except ValueError as exc:
        raise ProvisioningRefused(f"capability configuration refused: {exc}") from None
    snapshot = declaration.contract_snapshot
    if snapshot is None:  # verified above makes this unreachable, but typed.
        raise ProvisioningRefused("PROVISION capability has no owner contract")
    local_operations = tuple(
        ProvisioningCapabilityOperationPin(
            operation_code=operation.operation_code,
            input_schema_ref=operation.input_schema_ref,
            input_schema_digest=operation.input_schema_digest,
            output_schema_ref=operation.output_schema_ref,
            output_schema_digest=operation.output_schema_digest,
        )
        for operation in snapshot.operations
    )
    _validate_capability_schemas(local_operations, declaration.schema_documents)
    apply_operation = next(
        operation
        for operation in local_operations
        if operation.operation_code == "apply"
    )
    apply_input_schema = declaration.require_schema(
        apply_operation.input_schema_ref,
        apply_operation.input_schema_digest,
    )
    for evidence_binding in command.prerequisite_evidence_bindings:
        if (
            evidence_binding.target_schema_ref != apply_operation.input_schema_ref
            or evidence_binding.target_schema_digest
            != apply_operation.input_schema_digest
        ):
            raise ProvisioningRefused(
                "prerequisite evidence target is not the local apply input schema"
            )
        try:
            apply_input_schema.require_instance_pointer(evidence_binding.target_pointer)
        except ValueError as exc:
            raise ProvisioningRefused(
                "prerequisite evidence target pointer is not declared by the "
                "input schema"
            ) from exc
    local_artifact_digest = installation.connector_artifact_digest
    if local_artifact_digest is None:
        raise ProvisioningRefused(
            "PROVISION installation has no connector artifact digest"
        )
    comparisons = (
        (command.installation_id, installation.id, "installation id"),
        (command.installation_ref, installation.name, "installation reference"),
        (command.connector_key, installation.connector_key, "connector key"),
        (
            command.connector_version,
            installation.connector_version,
            "connector version",
        ),
        (
            command.connector_manifest_digest,
            _canonical_stored_digest(installation.manifest_digest),
            "connector manifest digest",
        ),
        (
            command.artifact_digest,
            local_artifact_digest,
            "connector artifact digest",
        ),
        (
            command.connector_configuration_revision_id,
            revision.id,
            "connector configuration revision id",
        ),
        (
            command.config_digest,
            _canonical_stored_digest(revision.config_digest),
            "connector configuration digest",
        ),
        (command.binding_ref, binding.id, "binding reference"),
        (
            command.capability_instance_ref,
            binding.capability_instance_ref,
            "capability instance reference",
        ),
        (command.capability_owner_code, verified.owner_code, "capability owner"),
        (command.capability_code, verified.capability_code, "capability code"),
        (
            command.capability_schema_version,
            verified.schema_version,
            "capability schema version",
        ),
        (
            command.capability_contract_digest,
            verified.contract_digest,
            "capability contract digest",
        ),
        (command.capability_operations, local_operations, "capability operations"),
        (
            command.execution_policy_digest,
            execution_policy_digest(policy),
            "execution policy digest",
        ),
    )
    for approved, actual, name in comparisons:
        if approved != actual:
            raise ProvisioningRefused(f"approved {name} does not match local state")


def _require_local_plan_validation(db: Session, command: ProvisioningCommand) -> None:
    record = db.execute(
        select(ProvisioningCommandRecord)
        .where(ProvisioningCommandRecord.command_id == command.plan_command_id)
        .with_for_update()
    ).scalar_one_or_none()
    receipt = db.execute(
        select(ProvisioningCommandReceipt)
        .where(ProvisioningCommandReceipt.command_id == command.plan_command_id)
        .with_for_update()
    ).scalar_one_or_none()
    if (
        record is None
        or receipt is None
        or record.command_kind != "plan"
        or record.state != "settled"
    ):
        raise ProvisioningRefused("approved PLAN has no settled local evidence")
    request = record.request_json
    expected_receipt_hash = _canonical_digest(
        {
            "command_id": receipt.command_id,
            "command_fingerprint": receipt.command_fingerprint,
            "capability_instance_ref": receipt.capability_instance_ref,
            "request_body_digest": receipt.request_body_digest,
            "result_digest": receipt.result_digest,
        }
    )
    if (
        receipt.command_record_id != record.id
        or receipt.command_fingerprint != record.command_fingerprint
        or receipt.receipt_hash != expected_receipt_hash
        or request.get("deployment_ref") != command.deployment_ref
        or request.get("capability_id") != command.capability_id
        or request.get("capability_instance_ref") != command.capability_instance_ref
        or request.get("binding_id") != str(command.capability_binding_id)
        or request.get("plan_hash") != command.plan_hash
        or request.get("config_digest") != command.config_digest
        or receipt.request_body_digest != command.plan_validation_request_body_digest
        or receipt.receipt_hash != command.module_plan_receipt_hash
    ):
        raise ProvisioningRefused(
            "approval validation evidence does not match the local PLAN receipt"
        )


def _canonical_stored_digest(value: str) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", value):
        return "sha256:" + value
    _require_canonical_digest(value, "stored digest")
    return value


def _resolve_stored_pin(
    db: Session,
    *,
    capability_id: str,
    capability_instance_ref: str,
    binding_id: UUID,
    config_digest: str,
) -> tuple[ConnectorInstallation, ConnectorConfigRevision, CapabilityBinding]:
    try:
        binding = resolve_binding(
            db,
            capability_id=capability_id,
            capability_instance_ref=capability_instance_ref,
            capability_binding_id=binding_id,
        )
    except (SelectionError, ValueError) as exc:
        raise ProvisioningRefused(str(exc)) from None
    installation = db.get(ConnectorInstallation, binding.installation_id)
    if installation is None:
        raise ProvisioningRefused("capability binding has no installation")
    revision = (
        db.get(ConnectorConfigRevision, installation.current_config_revision_id)
        if installation.current_config_revision_id is not None
        else None
    )
    if revision is None or _canonical_stored_digest(revision.config_digest) != (
        config_digest
    ):
        raise ProvisioningRefused("configuration pin changed during provisioning")
    return installation, revision, binding


def _resolve_operation_pin(
    db: Session,
    operation: ProvisioningOperation,
    registry: ConnectorRegistry,
    *,
    required_operation_code: Literal["cancel", "observe"],
    policy: ExecutionPolicy,
) -> tuple[ConnectorInstallation, ConnectorConfigRevision, CapabilityBinding]:
    installation, revision, binding = _resolve_execution_pin(
        db,
        capability_id=operation.capability_id,
        capability_instance_ref=operation.capability_instance_ref,
        binding_id=operation.capability_binding_id,
        config_digest=operation.config_digest,
        registry=registry,
    )
    if (
        installation.connector_key != operation.connector_key
        or installation.connector_version != operation.connector_version
        or _canonical_stored_digest(installation.manifest_digest)
        != operation.manifest_digest
        or installation.connector_artifact_digest != operation.artifact_digest
        or revision.id != operation.config_revision_id
        or execution_policy_digest(policy) != operation.execution_policy_digest
    ):
        raise ProvisioningRefused(
            "connector or configuration pin changed after plan approval"
        )
    declaration = registry.plugin(installation.connector_key).manifest.require_declares(
        operation.capability_id
    )
    try:
        verify_capability_configuration(
            declaration,
            config=revision.config_json or {},
            secret_refs=revision.secret_refs or {},
            required_operation_codes=(required_operation_code,),
        )
    except ValueError as exc:
        raise ProvisioningRefused(f"capability configuration refused: {exc}") from None
    return installation, revision, binding


def _expected_pin_document(expected: ExpectedProvisioningPin) -> dict[str, object]:
    return {
        "step_key": expected.step_key,
        "provider_operation_ref": expected.provider_operation_ref,
        "deployment_ref": expected.deployment_ref,
        "capability_instance_ref": expected.capability_instance_ref,
        "plan_hash": expected.plan_hash,
        "artifact_digest": expected.artifact_digest,
        "config_digest": expected.config_digest,
        "approval_digest": expected.approval_digest,
    }


def _require_expected_pin(
    operation: ProvisioningOperation,
    step: ProvisioningStep,
    expected: ExpectedProvisioningPin,
) -> None:
    if not expected.deployment_ref.strip():
        raise ProvisioningRefused("expected deployment reference is required")
    _require_instance_ref(expected.capability_instance_ref)
    for digest, name in (
        (expected.plan_hash, "expected plan hash"),
        (expected.artifact_digest, "expected artifact digest"),
        (expected.config_digest, "expected configuration digest"),
        (expected.approval_digest, "expected approval digest"),
    ):
        _require_digest(digest, name)
    actual = {
        "step_key": step.step_key,
        "provider_operation_ref": step.provider_operation_ref,
        "deployment_ref": operation.deployment_ref,
        "capability_instance_ref": operation.capability_instance_ref,
        "plan_hash": operation.expected_plan_hash,
        "artifact_digest": operation.artifact_digest,
        "config_digest": operation.config_digest,
        "approval_digest": operation.approval_digest,
    }
    if _expected_pin_document(expected) != actual:
        raise ProvisioningRefused(
            "caller provisioning pins do not match durable operation state"
        )


def _find_command(db: Session, command_id: str) -> ProvisioningCommandRecord | None:
    return db.execute(
        select(ProvisioningCommandRecord).where(
            ProvisioningCommandRecord.command_id == command_id
        )
    ).scalar_one_or_none()


def _require_same_command(
    record: ProvisioningCommandRecord, command_kind: str, fingerprint: str
) -> None:
    if record.command_kind != command_kind or record.command_fingerprint != fingerprint:
        raise CommandIdentityCollision(
            f"command id {record.command_id!r} was reused with different content"
        )


def _accept_action_command(
    db: Session,
    *,
    command_id: str,
    command_kind: str,
    fingerprint: str,
    request_json: dict[str, object],
    operation_id: UUID | None = None,
    step_id: UUID | None = None,
) -> tuple[ProvisioningCommandRecord, bool]:
    existing = _find_command(db, command_id)
    if existing is not None:
        _require_same_command(existing, command_kind, fingerprint)
        return existing, False

    from dotmac_kernel.db import conflict_savepoint

    record = ProvisioningCommandRecord(
        command_id=command_id,
        command_kind=command_kind,
        command_fingerprint=fingerprint,
        operation_id=operation_id,
        step_id=step_id,
        request_json=request_json,
        state="accepted",
    )
    try:
        with conflict_savepoint(db):
            db.add(record)
            db.flush()
    except IntegrityError:
        winner = _find_command(db, command_id)
        if winner is None:
            raise
        _require_same_command(winner, command_kind, fingerprint)
        return winner, False
    return record, True


def _locked_operation(db: Session, operation_id: UUID) -> ProvisioningOperation | None:
    return db.execute(
        select(ProvisioningOperation)
        .where(ProvisioningOperation.id == operation_id)
        .with_for_update()
    ).scalar_one_or_none()


def _locked_action(
    db: Session,
    prepared: PreparedObservation | PreparedCancellation,
    expected_state: str,
    moment: datetime,
) -> tuple[ProvisioningOperation, ProvisioningStep, ProvisioningCommandRecord]:
    operation = _locked_operation(db, prepared.operation_id)
    step = db.execute(
        select(ProvisioningStep)
        .where(ProvisioningStep.id == prepared.step_id)
        .with_for_update()
    ).scalar_one_or_none()
    record = db.execute(
        select(ProvisioningCommandRecord)
        .where(ProvisioningCommandRecord.id == prepared.command_record_id)
        .with_for_update()
    ).scalar_one_or_none()
    if operation is None or step is None or record is None:
        raise LostProvisioningClaim("provisioning action state disappeared")
    if (
        step.state != expected_state
        or step.attempt_count != prepared.attempt_number
        or step.leased_until is None
        or _aware(step.leased_until) < moment
        or operation.leased_until is None
        or _aware(operation.leased_until) < moment
        or record.state != "in_flight"
    ):
        raise LostProvisioningClaim("this worker no longer owns the action claim")
    return operation, step, record


def _finish_successful_step(
    db: Session, operation: ProvisioningOperation, moment: datetime
) -> None:
    db.flush()
    unfinished = db.scalar(
        select(func.count())
        .select_from(ProvisioningStep)
        .where(
            ProvisioningStep.operation_id == operation.id,
            ProvisioningStep.state != "succeeded",
        )
    )
    operation.state = "succeeded" if int(unfinished or 0) == 0 else "pending"
    operation.next_attempt_at = None if operation.state == "succeeded" else moment
    operation.completed_at = moment if operation.state == "succeeded" else None


def _append_result_receipt(
    db: Session,
    operation: ProvisioningOperation,
    step: ProvisioningStep,
    receipt_kind: str,
    result: ProvisioningResult,
    *,
    public_evidence: dict[str, object],
) -> None:
    _append_receipt(
        db,
        operation=operation,
        step=step,
        receipt_kind=receipt_kind,
        evidence={
            "status": result.status.value,
            "provider_operation_ref": result.provider_operation_ref,
            "error_code": result.error_code,
            "error_detail": result.error_detail,
            # Connector evidence is untrusted and may accidentally contain a
            # materialized credential under an innocent-looking key. Persist
            # its digest, never its values.
            "provider_evidence_digest": (
                "sha256:" + payload_digest(dict(result.evidence))
            ),
            "public_evidence": public_evidence,
        },
    )


def _invoke_result(
    call: Callable[[], object],
    *,
    output_schema: CapabilitySchemaDocument,
    success_statuses: Collection[ProvisionResultStatus],
    context: str,
) -> ProvisioningResult:
    try:
        result = call()
    except Exception as exc:
        return ProvisioningResult(
            status=ProvisionResultStatus.AMBIGUOUS,
            error_code="connector_raised",
            error_detail=_connector_error_detail(exc),
        )
    if not isinstance(result, ProvisioningResult):
        return ProvisioningResult(
            status=ProvisionResultStatus.AMBIGUOUS,
            error_code="connector_contract",
            error_detail="invalid_result_type",
        )
    try:
        _validate_result(
            result,
            output_schema=output_schema,
            success_statuses=success_statuses,
            context=context,
        )
    except ProvisioningRefused:
        return ProvisioningResult(
            status=ProvisionResultStatus.AMBIGUOUS,
            error_code="connector_contract",
            error_detail="unsafe_result",
        )
    return ProvisioningResult(
        status=result.status,
        provider_operation_ref=result.provider_operation_ref,
        evidence=dict(result.evidence),
        error_code=result.error_code,
        error_detail=None,
    )


def _validate_command_shape(command: ProvisioningCommand) -> None:
    _require_command_id(command.command_id)
    _require_instance_ref(command.capability_instance_ref)
    for reference, name in (
        (command.deployment_ref, "deployment reference"),
        (command.plan_command_id, "PLAN command id"),
        (command.profile_code, "profile code"),
        (command.installation_ref, "installation reference"),
        (command.connector_key, "connector key"),
        (command.connector_version, "connector version"),
        (command.configuration_snapshot_ref, "configuration snapshot reference"),
    ):
        if not reference.strip():
            raise ProvisioningRefused(f"{name} is required")
    for value, name in (
        (command.desired_state_revision, "desired state revision"),
        (command.profile_version, "profile version"),
        (command.profile_schema_version, "profile schema version"),
        (command.capability_schema_version, "capability schema version"),
        (command.configuration_schema_version, "configuration schema version"),
    ):
        if type(value) is not int or value < 1:
            raise ProvisioningRefused(f"{name} must be a positive integer")
    if command.command_schema_version != "integrator.provisioning-command.v1":
        raise ProvisioningRefused("unsupported provisioning command schema")
    if command.capability_id != (
        f"{command.capability_code}.v{command.capability_schema_version}"
    ):
        raise ProvisioningRefused(
            "capability_id must be capability_code versioned by schema version"
        )
    if command.binding_ref != command.capability_binding_id:
        raise ProvisioningRefused("binding_ref must equal capability_binding_id")
    for digest, name in (
        (command.desired_state_hash, "desired state hash"),
        (command.approval_request_binding_hash, "approval request binding hash"),
        (command.plan_validation_receipt_digest, "PLAN validation receipt digest"),
        (
            command.plan_validation_request_body_digest,
            "PLAN validation request body digest",
        ),
        (command.module_plan_receipt_hash, "module PLAN receipt hash"),
        (command.profile_content_hash, "profile content hash"),
        (command.capability_contract_digest, "capability contract digest"),
        (command.connector_manifest_digest, "connector manifest digest"),
        (command.configuration_hash, "configuration hash"),
        (command.plan_hash, "plan hash"),
        (command.expected_plan_hash, "expected plan hash"),
        (command.artifact_digest, "artifact digest"),
        (command.config_digest, "configuration digest"),
        (command.execution_policy_digest, "execution policy digest"),
        (command.approval.digest, "approval digest"),
        (
            command.approval.approved_command_template_digest,
            "approved command template digest",
        ),
    ):
        _require_canonical_digest(digest, name)
    if command.component_artifact_digest is not None:
        _require_canonical_digest(
            command.component_artifact_digest, "component artifact digest"
        )
    _validate_capability_operations(command.capability_operations)
    _validate_steps_for_capability(command.steps, capability_id=command.capability_id)
    _validate_prerequisite_shape(command)


def _require_command_id(command_id: str) -> None:
    if not command_id.strip() or len(command_id) > 240:
        raise ProvisioningRefused("a bounded command id is required")


def _require_instance_ref(value: str) -> None:
    try:
        require_capability_instance_ref(value)
    except ValueError as exc:
        raise ProvisioningRefused(str(exc)) from None


def _require_digest(digest: str, name: str) -> None:
    if _DIGEST.fullmatch(digest) is None:
        raise ProvisioningRefused(f"{name} is not a SHA-256 digest")


def _require_canonical_digest(digest: str, name: str) -> None:
    if re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
        raise ProvisioningRefused(
            f"{name} must be 'sha256:' plus 64 lowercase hex digits"
        )


def _canonical_digest(document: Mapping[str, object]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(document)).hexdigest()


def _validate_capability_operations(
    operations: tuple[ProvisioningCapabilityOperationPin, ...],
) -> None:
    if not operations:
        raise ProvisioningRefused("capability operation pins must not be empty")
    operation_codes = tuple(operation.operation_code for operation in operations)
    if operation_codes != tuple(sorted(set(operation_codes))):
        raise ProvisioningRefused("capability operation pins must be unique and sorted")
    try:
        for operation in operations:
            CapabilityOperation(**_capability_operation_document(operation))
    except (TypeError, ValueError) as exc:
        raise ProvisioningRefused(f"invalid capability operation pin: {exc}") from None


def _validate_capability_schemas(
    operations: tuple[ProvisioningCapabilityOperationPin, ...],
    schemas: tuple[CapabilitySchemaDocument, ...],
) -> None:
    expected = {
        (schema_ref, schema_digest)
        for operation in operations
        for schema_ref, schema_digest in (
            (operation.input_schema_ref, operation.input_schema_digest),
            (operation.output_schema_ref, operation.output_schema_digest),
        )
    }
    actual = {(schema.schema_ref, schema.digest) for schema in schemas}
    if len(actual) != len(schemas) or actual != expected:
        raise ProvisioningRefused(
            "capability schema documents must exactly cover operation schema pins"
        )
    if schemas != tuple(sorted(schemas, key=lambda item: item.schema_ref)):
        raise ProvisioningRefused(
            "capability schema documents must be schema-ref sorted"
        )
    for schema in schemas:
        try:
            Draft202012Validator.check_schema(
                cast(dict[str, object], json.loads(schema.to_json_bytes()))
            )
        except SchemaError as exc:
            raise ProvisioningRefused(
                f"capability schema {schema.schema_ref!r} is not valid draft 2020-12"
            ) from exc


def _capability_operation_document(
    operation: ProvisioningCapabilityOperationPin,
) -> dict[str, str]:
    return {
        "operation_code": operation.operation_code,
        "input_schema_ref": operation.input_schema_ref,
        "input_schema_digest": operation.input_schema_digest,
        "output_schema_ref": operation.output_schema_ref,
        "output_schema_digest": operation.output_schema_digest,
    }


def _validate_steps(steps: tuple[ProvisionStep, ...]) -> None:
    if not steps:
        raise ProvisioningRefused("a provisioning command needs at least one step")
    keys = [step.step_key for step in steps]
    if len(set(keys)) != len(keys):
        raise ProvisioningRefused("provisioning step keys must be unique")
    known = set(keys)
    for step in steps:
        if _CODE.fullmatch(step.step_key) is None:
            raise ProvisioningRefused(f"invalid step key {step.step_key!r}")
        if _CODE.fullmatch(step.endpoint_code) is None:
            raise ProvisioningRefused(f"invalid endpoint code {step.endpoint_code!r}")
        missing = set(step.depends_on) - known
        if missing or step.step_key in step.depends_on:
            raise ProvisioningRefused(
                f"step {step.step_key!r} has invalid dependencies"
            )
        _require_safe_structure(dict(step.input), path=f"step.{step.step_key}")
    _require_acyclic(steps)


def _validate_steps_for_capability(
    steps: tuple[ProvisionStep, ...], *, capability_id: str
) -> None:
    _validate_steps(steps)
    if any(step.endpoint_code != capability_id for step in steps):
        raise ProvisioningRefused(
            "SPI 1.2 provisioning step endpoint_code must equal capability_id"
        )


def _require_approval(command: ProvisioningCommand, moment: datetime) -> None:
    approval = command.approval
    if approval.verified_at is None:
        raise ApprovalRefused("approval grant was not verified")
    if _aware(approval.expires_at) <= moment:
        raise ApprovalRefused("approval grant is expired")
    if not (
        command.plan_hash == command.expected_plan_hash == approval.approved_plan_hash
    ):
        raise ApprovalRefused(
            "actual, expected and approved plan hashes must match exactly"
        )
    proof_pairs = (
        (command.approval_request_id, approval.approval_request_id),
        (
            command.approval_request_binding_hash,
            approval.approval_request_binding_hash,
        ),
        (command.saved_plan_id, approval.saved_plan_id),
        (command.plan_command_id, approval.plan_command_id),
        (command.plan_validation_receipt_id, approval.plan_validation_receipt_id),
        (
            command.plan_validation_receipt_digest,
            approval.plan_validation_receipt_digest,
        ),
        (
            command.plan_validation_request_body_digest,
            approval.plan_validation_request_body_digest,
        ),
        (command.module_plan_receipt_hash, approval.module_plan_receipt_hash),
    )
    if any(command_value != grant_value for command_value, grant_value in proof_pairs):
        raise ApprovalRefused(
            "approval grant does not match the saved plan and validation evidence"
        )
    actual_template_digest = provisioning_command_template_digest(command)
    if actual_template_digest != approval.approved_command_template_digest:
        raise ApprovalRefused(
            "command template differs from the template bound into the approved plan"
        )


def provisioning_command_template_digest(command: ProvisioningCommand) -> str:
    """Digest the static command template that approval can know in advance.

    The global plan hash cannot contain provider receipts that do not exist yet.
    It instead contains this exact template, including the symbolic upstream
    binding set.  Dispatch-time receipt pins are signed and replay-fingerprinted
    separately, then checked against that set and the immutable receipt ledger.
    """

    _validate_steps_for_capability(command.steps, capability_id=command.capability_id)
    return _canonical_digest(_approved_command_template_document(command))


def _approved_command_template_document(
    command: ProvisioningCommand,
) -> dict[str, object]:
    return {
        "deployment_ref": command.deployment_ref,
        "desired_state_revision": command.desired_state_revision,
        "desired_state_version_id": str(command.desired_state_version_id),
        "desired_state_hash": command.desired_state_hash,
        "saved_plan_id": str(command.saved_plan_id),
        "profile_version_id": str(command.profile_version_id),
        "profile_code": command.profile_code,
        "profile_version": command.profile_version,
        "profile_schema_version": command.profile_schema_version,
        "profile_content_hash": command.profile_content_hash,
        "command_schema_version": command.command_schema_version,
        "capability_id": command.capability_id,
        "capability_instance_ref": command.capability_instance_ref,
        "capability_owner_code": command.capability_owner_code,
        "capability_code": command.capability_code,
        "capability_schema_version": command.capability_schema_version,
        "capability_contract_attestation_id": str(
            command.capability_contract_attestation_id
        ),
        "capability_contract_digest": command.capability_contract_digest,
        "capability_operations": [
            _capability_operation_document(operation)
            for operation in command.capability_operations
        ],
        "capability_binding_id": str(command.capability_binding_id),
        "binding_ref": str(command.binding_ref),
        "installation_id": str(command.installation_id),
        "installation_ref": command.installation_ref,
        "connector_key": command.connector_key,
        "connector_version": command.connector_version,
        "connector_manifest_digest": command.connector_manifest_digest,
        "connector_configuration_revision_id": str(
            command.connector_configuration_revision_id
        ),
        "configuration_snapshot_ref": command.configuration_snapshot_ref,
        "configuration_schema_version": command.configuration_schema_version,
        "configuration_hash": command.configuration_hash,
        "artifact_digest": command.artifact_digest,
        "component_artifact_digest": command.component_artifact_digest,
        "config_digest": command.config_digest,
        "execution_policy_digest": command.execution_policy_digest,
        "prerequisite_capability_binding_ids": [
            str(binding_id)
            for binding_id in command.prerequisite_capability_binding_ids
        ],
        "prerequisite_evidence_bindings": [
            _evidence_binding_document(binding)
            for binding in command.prerequisite_evidence_bindings
        ],
        "steps": [_step_document(step) for step in command.steps],
    }


def _command_fingerprint(command: ProvisioningCommand) -> str:
    document = _command_document(command)
    approval = cast(dict[str, object], document["approval"])
    # Verification time proves when the held grant was checked; it does not
    # change the authority document and therefore is not command identity.
    approval.pop("verified_at")
    return hashlib.sha256(_canonical(document)).hexdigest()


def _command_document(command: ProvisioningCommand) -> dict[str, object]:
    document = _approved_command_template_document(command)
    document.update(
        {
            "command_id": command.command_id,
            "plan_hash": command.plan_hash,
            "expected_plan_hash": command.expected_plan_hash,
            "approval_request_id": str(command.approval_request_id),
            "approval_request_binding_hash": command.approval_request_binding_hash,
            "plan_command_id": command.plan_command_id,
            "plan_validation_receipt_id": str(command.plan_validation_receipt_id),
            "plan_validation_receipt_digest": command.plan_validation_receipt_digest,
            "plan_validation_request_body_digest": (
                command.plan_validation_request_body_digest
            ),
            "module_plan_receipt_hash": command.module_plan_receipt_hash,
            "approval": {
                "grant_ref": command.approval.grant_ref,
                "approval_request_id": str(command.approval.approval_request_id),
                "approval_request_binding_hash": (
                    command.approval.approval_request_binding_hash
                ),
                "saved_plan_id": str(command.approval.saved_plan_id),
                "approved_plan_hash": command.approval.approved_plan_hash,
                "plan_command_id": command.approval.plan_command_id,
                "plan_validation_receipt_id": str(
                    command.approval.plan_validation_receipt_id
                ),
                "plan_validation_receipt_digest": (
                    command.approval.plan_validation_receipt_digest
                ),
                "plan_validation_request_body_digest": (
                    command.approval.plan_validation_request_body_digest
                ),
                "module_plan_receipt_hash": command.approval.module_plan_receipt_hash,
                "digest": command.approval.digest,
                "expires_at": _aware(command.approval.expires_at).isoformat(),
                "verified_at": (
                    _aware(command.approval.verified_at).isoformat()
                    if command.approval.verified_at is not None
                    else None
                ),
                "approved_command_template_digest": (
                    command.approval.approved_command_template_digest
                ),
            },
            "prerequisite_capability_binding_ids": [
                str(binding_id)
                for binding_id in command.prerequisite_capability_binding_ids
            ],
            "prerequisite_receipt_pins": [
                _prerequisite_pin_document(pin)
                for pin in command.prerequisite_receipt_pins
            ],
        }
    )
    return document


def _prerequisite_pin_document(pin: PrerequisiteReceiptPin) -> dict[str, object]:
    return {
        "capability_binding_id": str(pin.capability_binding_id),
        "operation_id": str(pin.operation_id),
        "terminal_receipt_sequence": pin.terminal_receipt_sequence,
        "terminal_receipt_digest": pin.terminal_receipt_digest,
        "required_terminal_status": pin.required_terminal_status,
    }


def _evidence_binding_document(
    binding: PrerequisiteEvidenceBinding,
) -> dict[str, object]:
    return {
        "source_capability_binding_id": str(binding.source_capability_binding_id),
        "source_step_key": binding.source_step_key,
        "source_schema_ref": binding.source_schema_ref,
        "source_schema_digest": binding.source_schema_digest,
        "source_pointer": binding.source_pointer,
        "target_step_key": binding.target_step_key,
        "target_schema_ref": binding.target_schema_ref,
        "target_schema_digest": binding.target_schema_digest,
        "target_pointer": binding.target_pointer,
        "required": binding.required,
    }


def _capability_schema_document(
    document: CapabilitySchemaDocument,
) -> dict[str, str]:
    return {
        "schema_ref": document.schema_ref,
        "schema_digest": document.digest,
        "canonical_json": document.to_json_bytes().decode("utf-8"),
    }


def _step_document(step: ProvisionStep) -> dict[str, object]:
    return {
        "step_key": step.step_key,
        "endpoint_code": step.endpoint_code,
        "depends_on": list(step.depends_on),
        "input": dict(step.input),
    }


def _validate_prerequisite_shape(command: ProvisioningCommand) -> None:
    binding_ids = command.prerequisite_capability_binding_ids
    canonical_binding_ids = tuple(sorted(set(binding_ids), key=str))
    if binding_ids != canonical_binding_ids:
        raise ProvisioningRefused(
            "prerequisite capability binding ids must be unique and UUID-sorted"
        )
    if command.capability_binding_id in binding_ids:
        raise ProvisioningRefused("a command cannot depend on its own binding")
    _validate_evidence_bindings(command.prerequisite_evidence_bindings)
    evidence_source_ids = {
        binding.source_capability_binding_id
        for binding in command.prerequisite_evidence_bindings
    }
    if not evidence_source_ids <= set(binding_ids):
        raise ProvisioningRefused(
            "prerequisite evidence binding source must be an approved "
            "prerequisite capability binding id"
        )
    step_keys = {step.step_key for step in command.steps}
    if any(
        binding.target_step_key not in step_keys
        for binding in command.prerequisite_evidence_bindings
    ):
        raise ProvisioningRefused(
            "prerequisite evidence binding names an unknown target step"
        )

    pins = command.prerequisite_receipt_pins
    canonical_pins = tuple(sorted(pins, key=lambda pin: str(pin.operation_id)))
    if pins != canonical_pins:
        raise ProvisioningRefused(
            "prerequisite receipt pins must be sorted by operation UUID"
        )
    pin_binding_ids = tuple(pin.capability_binding_id for pin in pins)
    if len(set(pin_binding_ids)) != len(pin_binding_ids):
        raise ProvisioningRefused(
            "one prerequisite receipt pin is required per prerequisite binding"
        )
    if set(pin_binding_ids) != set(binding_ids):
        raise ProvisioningRefused(
            "receipt pins must exactly cover the approved prerequisite bindings"
        )
    operation_ids = tuple(pin.operation_id for pin in pins)
    if len(set(operation_ids)) != len(operation_ids):
        raise ProvisioningRefused("prerequisite operation ids must be unique")
    for pin in pins:
        if pin.terminal_receipt_sequence < 1:
            raise ProvisioningRefused(
                "prerequisite terminal receipt sequence must be positive"
            )
        _require_digest(
            pin.terminal_receipt_digest, "prerequisite terminal receipt digest"
        )
        if pin.required_terminal_status != "succeeded":
            raise ProvisioningRefused(
                "the only safe prerequisite terminal status is succeeded"
            )


def _validate_evidence_bindings(
    bindings: tuple[PrerequisiteEvidenceBinding, ...],
) -> None:
    canonical = tuple(
        sorted(
            bindings,
            key=lambda binding: (
                str(binding.source_capability_binding_id),
                binding.source_step_key,
                binding.source_pointer,
                binding.target_step_key,
                binding.target_pointer,
            ),
        )
    )
    if bindings != canonical:
        raise ProvisioningRefused(
            "prerequisite evidence bindings must use canonical sorted order"
        )
    sort_keys = tuple(
        (
            str(binding.source_capability_binding_id),
            binding.source_step_key,
            binding.source_pointer,
            binding.target_step_key,
            binding.target_pointer,
        )
        for binding in bindings
    )
    if len(set(sort_keys)) != len(sort_keys):
        raise ProvisioningRefused(
            "prerequisite evidence binding locators must be unique"
        )
    target_paths = tuple(
        (binding.target_step_key, binding.target_pointer) for binding in bindings
    )
    if len(set(target_paths)) != len(target_paths):
        raise ProvisioningRefused(
            "a target step and pointer may have only one prerequisite evidence source"
        )
    for binding in bindings:
        for code, name in (
            (binding.source_step_key, "source step key"),
            (binding.target_step_key, "target step key"),
        ):
            if _CODE.fullmatch(code) is None:
                raise ProvisioningRefused(f"invalid prerequisite {name}")
        for digest, name in (
            (binding.source_schema_digest, "source schema digest"),
            (binding.target_schema_digest, "target schema digest"),
        ):
            _require_canonical_digest(digest, name)
        for schema_ref, name in (
            (binding.source_schema_ref, "source schema reference"),
            (binding.target_schema_ref, "target schema reference"),
        ):
            if _SCHEMA_REFERENCE.fullmatch(schema_ref) is None:
                raise ProvisioningRefused(f"invalid prerequisite {name}")
        for pointer, name in (
            (binding.source_pointer, "source pointer"),
            (binding.target_pointer, "target pointer"),
        ):
            if (
                not pointer.startswith("/")
                or len(pointer) > 1024
                or re.search(r"~(?![01])", pointer) is not None
            ):
                raise ProvisioningRefused(f"invalid prerequisite {name}")
        if type(binding.required) is not bool:
            raise ProvisioningRefused(
                "prerequisite evidence required flag must be a boolean"
            )


def _require_prerequisite_receipts(db: Session, command: ProvisioningCommand) -> None:
    if not command.prerequisite_receipt_pins:
        return

    pins_by_operation = {
        pin.operation_id: pin for pin in command.prerequisite_receipt_pins
    }
    operation_ids = tuple(sorted(pins_by_operation, key=str))
    operations = tuple(
        db.execute(
            select(ProvisioningOperation)
            .where(ProvisioningOperation.id.in_(operation_ids))
            .order_by(ProvisioningOperation.id)
            .with_for_update()
        )
        .scalars()
        .all()
    )
    if len(operations) != len(operation_ids):
        raise ProvisioningRefused("a prerequisite operation does not exist")

    for operation in operations:
        pin = pins_by_operation[operation.id]
        if operation.capability_binding_id != pin.capability_binding_id:
            raise ProvisioningRefused(
                "prerequisite operation does not belong to the approved binding"
            )
        if operation.deployment_ref != command.deployment_ref:
            raise ProvisioningRefused(
                "prerequisite operation belongs to another deployment"
            )
        if operation.expected_plan_hash != command.expected_plan_hash:
            raise ProvisioningRefused(
                "prerequisite operation belongs to another approved plan"
            )
        if operation.state != pin.required_terminal_status:
            raise ProvisioningRefused(
                "prerequisite operation has not reached the required terminal status"
            )
        latest = db.execute(
            select(ProvisioningReceipt)
            .where(ProvisioningReceipt.operation_id == operation.id)
            .order_by(ProvisioningReceipt.sequence.desc())
            .limit(1)
            .with_for_update()
        ).scalar_one_or_none()
        if latest is None:
            raise ProvisioningRefused("prerequisite operation has no receipt evidence")
        if (
            latest.sequence != pin.terminal_receipt_sequence
            or latest.receipt_hash != pin.terminal_receipt_digest
        ):
            raise ProvisioningRefused(
                "prerequisite pin is not the operation's exact latest receipt"
            )
        source_mappings = tuple(
            binding
            for binding in command.prerequisite_evidence_bindings
            if binding.source_capability_binding_id == pin.capability_binding_id
        )
        if not source_mappings:
            continue
        apply_operation = _stored_operation_pin(operation, "apply")
        for binding in source_mappings:
            if (
                binding.source_schema_ref != apply_operation.output_schema_ref
                or binding.source_schema_digest != apply_operation.output_schema_digest
            ):
                raise ProvisioningRefused(
                    "prerequisite evidence source is not the source apply output schema"
                )
            source_schema = _stored_schema(
                operation,
                schema_ref=binding.source_schema_ref,
                schema_digest=binding.source_schema_digest,
            )
            try:
                source_schema.require_public_non_secret_pointer(binding.source_pointer)
            except ValueError as exc:
                raise ProvisioningRefused(
                    "prerequisite source pointer is not declared public evidence"
                ) from exc
            source_step = db.execute(
                select(ProvisioningStep)
                .where(
                    ProvisioningStep.operation_id == operation.id,
                    ProvisioningStep.step_key == binding.source_step_key,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if source_step is None or source_step.state != "succeeded":
                raise ProvisioningRefused(
                    "prerequisite evidence source step is not succeeded"
                )


def _resolve_prerequisite_inputs(
    db: Session,
    *,
    operation: ProvisioningOperation,
    step: ProvisioningStep,
) -> dict[str, object]:
    """Resolve approved public evidence while every supplying row is locked."""

    bindings = tuple(
        _parse_evidence_binding(cast(dict[str, object], item))
        for item in operation.prerequisite_evidence_bindings_json
    )
    relevant = tuple(
        binding for binding in bindings if binding.target_step_key == step.step_key
    )
    resolved = cast(dict[str, object], json.loads(_canonical(step.input_json)))
    if not relevant:
        return resolved

    pins = tuple(
        _parse_prerequisite_pin(cast(dict[str, object], item))
        for item in operation.prerequisite_receipt_pins_json
    )
    pins_by_binding = {pin.capability_binding_id: pin for pin in pins}
    required_operation_ids = tuple(
        sorted(
            {
                pins_by_binding[binding.source_capability_binding_id].operation_id
                for binding in relevant
            },
            key=str,
        )
    )
    sources = {
        row.id: row
        for row in db.scalars(
            select(ProvisioningOperation)
            .where(ProvisioningOperation.id.in_(required_operation_ids))
            .order_by(ProvisioningOperation.id)
            .with_for_update()
        )
    }
    if set(sources) != set(required_operation_ids):
        raise ProvisioningRefused("a prerequisite evidence operation disappeared")

    receipt_chains: dict[UUID, tuple[ProvisioningReceipt, ...]] = {}
    source_steps: dict[tuple[UUID, str], ProvisioningStep] = {}
    for source_id in required_operation_ids:
        source = sources[source_id]
        pin = next(pin for pin in pins if pin.operation_id == source_id)
        _require_live_prerequisite_operation(
            source,
            pin=pin,
            downstream=operation,
        )
        rows = tuple(
            db.scalars(
                select(ProvisioningReceipt)
                .where(ProvisioningReceipt.operation_id == source_id)
                .order_by(ProvisioningReceipt.sequence)
                .with_for_update()
            )
        )
        _verify_receipt_chain(rows)
        if (
            not rows
            or rows[-1].sequence != pin.terminal_receipt_sequence
            or rows[-1].receipt_hash != pin.terminal_receipt_digest
        ):
            raise ProvisioningRefused(
                "prerequisite evidence pin is not the exact latest terminal receipt"
            )
        receipt_chains[source_id] = rows

    for binding in relevant:
        pin = pins_by_binding[binding.source_capability_binding_id]
        source = sources[pin.operation_id]
        key = (source.id, binding.source_step_key)
        source_step = source_steps.get(key)
        if source_step is None:
            source_step = db.execute(
                select(ProvisioningStep)
                .where(
                    ProvisioningStep.operation_id == source.id,
                    ProvisioningStep.step_key == binding.source_step_key,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if source_step is None or source_step.state != "succeeded":
                raise ProvisioningRefused(
                    "prerequisite evidence source step is not succeeded"
                )
            source_steps[key] = source_step
        source_operation = _stored_operation_pin(source, "apply")
        if (
            binding.source_schema_ref != source_operation.output_schema_ref
            or binding.source_schema_digest != source_operation.output_schema_digest
        ):
            raise ProvisioningRefused(
                "prerequisite evidence source schema is not the source apply output"
            )
        source_schema = _stored_schema(
            source,
            schema_ref=binding.source_schema_ref,
            schema_digest=binding.source_schema_digest,
        )
        source_schema.require_public_non_secret_pointer(binding.source_pointer)

        target_operation = _stored_operation_pin(operation, "apply")
        if (
            binding.target_schema_ref != target_operation.input_schema_ref
            or binding.target_schema_digest != target_operation.input_schema_digest
        ):
            raise ProvisioningRefused(
                "prerequisite evidence target schema is not the target apply input"
            )
        target_schema = _stored_schema(
            operation,
            schema_ref=binding.target_schema_ref,
            schema_digest=binding.target_schema_digest,
        )
        target_schema.require_instance_pointer(binding.target_pointer)

        source_receipt = next(
            (
                row
                for row in reversed(receipt_chains[source.id])
                if row.step_key == binding.source_step_key
                and row.receipt_kind == "step_succeeded"
            ),
            None,
        )
        if source_receipt is None:
            raise ProvisioningRefused(
                "prerequisite source step has no succeeded receipt evidence"
            )
        public_evidence = source_receipt.evidence_json.get("public_evidence")
        if not isinstance(public_evidence, dict):
            raise ProvisioningRefused(
                "prerequisite source receipt has no public evidence projection"
            )
        try:
            value = source_schema.instance_value_at(
                cast(dict[str, object], public_evidence), binding.source_pointer
            )
        except ValueError:
            if binding.required:
                raise ProvisioningRefused(
                    "required prerequisite public evidence is absent"
                ) from None
            continue
        _write_instance_pointer(resolved, binding.target_pointer, value)
    return resolved


def _require_live_prerequisite_operation(
    source: ProvisioningOperation,
    *,
    pin: PrerequisiteReceiptPin,
    downstream: ProvisioningOperation,
) -> None:
    if source.capability_binding_id != pin.capability_binding_id:
        raise ProvisioningRefused(
            "prerequisite operation does not belong to the approved binding"
        )
    if (
        source.deployment_ref != downstream.deployment_ref
        or source.expected_plan_hash != downstream.expected_plan_hash
    ):
        raise ProvisioningRefused(
            "prerequisite operation belongs to another deployment or approved plan"
        )
    if source.state != pin.required_terminal_status:
        raise ProvisioningRefused(
            "prerequisite operation lost its required terminal status"
        )


def _stored_operation_pin(
    operation: ProvisioningOperation, operation_code: str
) -> ProvisioningCapabilityOperationPin:
    for raw in operation.capability_operations_json:
        document = cast(dict[str, object], raw)
        if document.get("operation_code") == operation_code:
            return ProvisioningCapabilityOperationPin(
                operation_code=cast(str, document["operation_code"]),
                input_schema_ref=cast(str, document["input_schema_ref"]),
                input_schema_digest=cast(str, document["input_schema_digest"]),
                output_schema_ref=cast(str, document["output_schema_ref"]),
                output_schema_digest=cast(str, document["output_schema_digest"]),
            )
    raise ProvisioningRefused(
        f"operation snapshot omits required {operation_code!r} schema pins"
    )


def _stored_schema(
    operation: ProvisioningOperation,
    *,
    schema_ref: str,
    schema_digest: str,
) -> CapabilitySchemaDocument:
    for raw in operation.capability_schemas_json:
        document = cast(dict[str, object], raw)
        if (
            document.get("schema_ref") == schema_ref
            and document.get("schema_digest") == schema_digest
        ):
            canonical = document.get("canonical_json")
            if not isinstance(canonical, str):
                break
            try:
                return CapabilitySchemaDocument.from_json_bytes(
                    canonical.encode("utf-8"),
                    expected_ref=schema_ref,
                    expected_digest=schema_digest,
                )
            except ValueError as exc:
                raise ProvisioningRefused(
                    "stored capability schema no longer matches its exact pins"
                ) from exc
    raise ProvisioningRefused(
        f"operation snapshot does not hold exact schema {schema_ref!r}"
    )


def _declared_operation_schemas(
    declaration: CapabilityDeclaration,
    operation_code: Literal["plan"],
) -> tuple[CapabilitySchemaDocument, CapabilitySchemaDocument]:
    operation = next(
        (
            candidate
            for candidate in declaration.operations
            if candidate.operation_code == operation_code
        ),
        None,
    )
    if operation is None:
        raise ProvisioningRefused(
            f"capability omits required {operation_code!r} schema pins"
        )
    try:
        return (
            declaration.require_schema(
                operation.input_schema_ref,
                operation.input_schema_digest,
            ),
            declaration.require_schema(
                operation.output_schema_ref,
                operation.output_schema_digest,
            ),
        )
    except ValueError as exc:
        raise ProvisioningRefused(
            f"capability does not hold exact {operation_code!r} schemas"
        ) from exc


def _operation_target(
    schema: CapabilitySchemaDocument,
    original_input: Mapping[str, object],
    *,
    context: str,
) -> dict[str, object]:
    """Copy only schema-declared top-level target fields from durable intent."""

    properties = schema.to_mapping().get("properties")
    if not isinstance(properties, Mapping):
        raise ProvisioningRefused(
            f"{context} schema must declare top-level object properties"
        )
    source = cast(dict[str, object], json.loads(_canonical(original_input)))
    target = {
        str(key): source[str(key)]
        for key in properties
        if isinstance(key, str) and key in source
    }
    _validate_json_schema_instance(schema, target, context=context)
    return target


def _parse_evidence_binding(document: dict[str, object]) -> PrerequisiteEvidenceBinding:
    try:
        return PrerequisiteEvidenceBinding(
            source_capability_binding_id=UUID(
                cast(str, document["source_capability_binding_id"])
            ),
            source_step_key=cast(str, document["source_step_key"]),
            source_schema_ref=cast(str, document["source_schema_ref"]),
            source_schema_digest=cast(str, document["source_schema_digest"]),
            source_pointer=cast(str, document["source_pointer"]),
            target_step_key=cast(str, document["target_step_key"]),
            target_schema_ref=cast(str, document["target_schema_ref"]),
            target_schema_digest=cast(str, document["target_schema_digest"]),
            target_pointer=cast(str, document["target_pointer"]),
            required=cast(bool, document["required"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProvisioningRefused(
            "stored prerequisite evidence binding is invalid"
        ) from exc


def _parse_prerequisite_pin(document: dict[str, object]) -> PrerequisiteReceiptPin:
    try:
        return PrerequisiteReceiptPin(
            capability_binding_id=UUID(cast(str, document["capability_binding_id"])),
            operation_id=UUID(cast(str, document["operation_id"])),
            terminal_receipt_sequence=cast(int, document["terminal_receipt_sequence"]),
            terminal_receipt_digest=cast(str, document["terminal_receipt_digest"]),
            required_terminal_status=cast(
                Literal["succeeded"], document["required_terminal_status"]
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProvisioningRefused("stored prerequisite receipt pin is invalid") from exc


def _write_instance_pointer(
    document: dict[str, object], pointer: str, value: object
) -> None:
    tokens = _pointer_tokens(pointer)
    current = document
    for token in tokens[:-1]:
        nested = current.get(token)
        if nested is None:
            nested = {}
            current[token] = nested
        if not isinstance(nested, dict):
            raise ProvisioningRefused(
                "prerequisite target pointer crosses a non-object input value"
            )
        current = cast(dict[str, object], nested)
    leaf = tokens[-1]
    if leaf in current and current[leaf] != value:
        raise ProvisioningRefused(
            "approved step input conflicts with resolved prerequisite evidence"
        )
    current[leaf] = value


def _pointer_tokens(pointer: str) -> tuple[str, ...]:
    if not pointer.startswith("/"):
        raise ProvisioningRefused("instance JSON pointer must start with '/'")
    return tuple(
        token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/")
    )


def _require_acyclic(steps: tuple[ProvisionStep, ...]) -> None:
    dependencies = {step.step_key: set(step.depends_on) for step in steps}
    remaining = dict(dependencies)
    completed: set[str] = set()
    while remaining:
        ready = sorted(key for key, needs in remaining.items() if needs <= completed)
        if not ready:
            raise ProvisioningRefused("provisioning step dependencies contain a cycle")
        for key in ready:
            completed.add(key)
            del remaining[key]


def _require_safe_structure(
    node: object,
    *,
    path: str,
    forbid_execution: bool = True,
    forbid_secret_shape: bool = True,
) -> None:
    if isinstance(node, Mapping):
        for raw_key, value in node.items():
            key = str(raw_key).lower()
            tokens = re.findall(r"[a-z0-9]+", key)
            compact = "".join(tokens)
            if forbid_execution and set(tokens) & _FORBIDDEN_EXECUTION_TOKENS:
                raise ProvisioningRefused(
                    f"{path}.{raw_key} attempts arbitrary process execution"
                )
            if forbid_secret_shape and (
                set(tokens) & _SECRET_TOKENS or compact in _SECRET_COMPOUNDS
            ):
                raise ProvisioningRefused(
                    f"{path}.{raw_key} may contain secret material; pass only "
                    "a configured secret reference"
                )
            _require_safe_structure(
                value,
                path=f"{path}.{raw_key}",
                forbid_execution=forbid_execution,
                forbid_secret_shape=forbid_secret_shape,
            )
        return
    if isinstance(node, list | tuple):
        for index, value in enumerate(node):
            _require_safe_structure(
                value,
                path=f"{path}[{index}]",
                forbid_execution=forbid_execution,
                forbid_secret_shape=forbid_secret_shape,
            )
        return
    if isinstance(node, float) and not math.isfinite(node):
        raise ProvisioningRefused(f"{path} contains a non-finite number")
    if node is None or isinstance(node, bool | int | float):
        return
    if isinstance(node, str) and len(node) <= 4096:
        return
    raise ProvisioningRefused(f"{path} contains unsupported structured evidence")


def _validate_result(
    result: ProvisioningResult,
    *,
    output_schema: CapabilitySchemaDocument | None = None,
    success_statuses: Collection[ProvisionResultStatus] | None = None,
    context: str = "connector apply evidence",
) -> None:
    if (
        result.provider_operation_ref is not None
        and _SAFE_REFERENCE.fullmatch(result.provider_operation_ref) is None
    ):
        raise ProvisioningRefused("connector returned an unsafe operation reference")
    if result.error_code is not None and _CODE.fullmatch(result.error_code) is None:
        raise ProvisioningRefused("connector returned an unsafe error code")
    _require_safe_structure(
        dict(result.evidence),
        path="provider_evidence",
        forbid_execution=False,
        forbid_secret_shape=False,
    )
    statuses = (
        _OBSERVE_OUTPUT_STATUSES if success_statuses is None else success_statuses
    )
    if output_schema is not None and result.status in statuses:
        _validate_json_schema_instance(
            output_schema,
            dict(result.evidence),
            context=context,
        )


def _result_safe_for_settlement(result: ProvisioningResult) -> ProvisioningResult:
    """Drop connector prose again at the durable persistence boundary.

    Invoke helpers already remove connector-supplied detail, but the public
    settle façades must be safe even when a caller passes a result directly.
    Error codes and schema-validated evidence retain the machine contract;
    arbitrary prose never reaches mutable state or immutable receipts.
    """

    return ProvisioningResult(
        status=result.status,
        provider_operation_ref=result.provider_operation_ref,
        evidence=dict(result.evidence),
        error_code=result.error_code,
        error_detail=None,
    )


def _validate_plan_result(
    result: ProvisionPlanResult,
    *,
    output_schema: CapabilitySchemaDocument,
) -> None:
    _require_safe_structure(
        dict(result.evidence),
        path="plan_evidence",
        forbid_execution=False,
        forbid_secret_shape=False,
    )
    _validate_json_schema_instance(
        output_schema,
        dict(result.evidence),
        context="connector plan evidence",
    )


def _result_public_evidence(
    result: ProvisioningResult,
    *,
    output_schema: CapabilitySchemaDocument,
    success_statuses: Collection[ProvisionResultStatus],
) -> dict[str, object]:
    if result.status not in success_statuses:
        return {}
    return _public_non_secret_projection(output_schema, dict(result.evidence))


def _validate_json_schema_instance(
    schema: CapabilitySchemaDocument,
    instance: object,
    *,
    context: str,
) -> None:
    document = schema.to_mapping()
    try:
        Draft202012Validator(
            document,
            format_checker=_FORMAT_CHECKER,
        ).validate(instance)
    except (SchemaError, ValidationError) as exc:
        # Validation errors can render the rejected instance. A connector may
        # have placed secret material there, so the refusal names only the held
        # schema and context.
        raise ProvisioningRefused(
            f"{context} does not conform to held schema {schema.schema_ref!r}"
        ) from exc


def _public_non_secret_projection(
    schema: CapabilitySchemaDocument,
    instance: dict[str, object],
) -> dict[str, object]:
    return dict(schema.public_non_secret_projection(instance))


def _settled_state(
    result: ProvisioningResult, attempt_number: int, policy: ExecutionPolicy
) -> tuple[str, str]:
    status = result.status
    if status is ProvisionResultStatus.SUCCEEDED:
        return "succeeded", "step_succeeded"
    if status in {ProvisionResultStatus.ACCEPTED, ProvisionResultStatus.PENDING}:
        if not result.provider_operation_ref:
            return "reconciliation_required", "reconciliation_required"
        return "observing", "step_accepted"
    if status is ProvisionResultStatus.RETRYABLE:
        if attempt_number >= policy.max_attempts:
            return "terminal", "step_terminal"
        return "retryable", "step_retryable"
    if status is ProvisionResultStatus.CANCELLED:
        return "cancelled", "step_cancelled"
    if status is ProvisionResultStatus.TERMINAL:
        return "terminal", "step_terminal"
    # AMBIGUOUS and NOT_FOUND after an attempt both mean the external result is
    # unknown. Retrying may duplicate a provider effect.
    return "reconciliation_required", "reconciliation_required"


def _append_receipt(
    db: Session,
    *,
    operation: ProvisioningOperation,
    step: ProvisioningStep | None,
    receipt_kind: str,
    evidence: dict[str, object],
) -> ProvisioningReceipt:
    _require_safe_structure(evidence, path="receipt", forbid_execution=False)
    prior = db.execute(
        select(ProvisioningReceipt)
        .where(ProvisioningReceipt.operation_id == operation.id)
        .order_by(ProvisioningReceipt.sequence.desc())
        .limit(1)
    ).scalar_one_or_none()
    sequence = 1 if prior is None else prior.sequence + 1
    previous_hash = None if prior is None else prior.receipt_hash
    material = {
        "operation_id": str(operation.id),
        "step_id": str(step.id) if step is not None else None,
        "sequence": sequence,
        "receipt_kind": receipt_kind,
        "step_key": step.step_key if step is not None else None,
        "provider_operation_ref": (
            step.provider_operation_ref if step is not None else None
        ),
        "previous_receipt_hash": previous_hash,
        "plan_hash": operation.expected_plan_hash,
        "capability_instance_ref": operation.capability_instance_ref,
        "connector_key": operation.connector_key,
        "connector_version": operation.connector_version,
        "manifest_digest": operation.manifest_digest,
        "artifact_digest": operation.artifact_digest,
        "config_digest": operation.config_digest,
        "approval_digest": operation.approval_digest,
        "evidence": evidence,
    }
    receipt = ProvisioningReceipt(
        operation_id=operation.id,
        step_id=step.id if step is not None else None,
        sequence=sequence,
        receipt_kind=receipt_kind,
        step_key=step.step_key if step is not None else None,
        provider_operation_ref=(
            step.provider_operation_ref if step is not None else None
        ),
        previous_receipt_hash=previous_hash,
        receipt_hash="sha256:" + hashlib.sha256(_canonical(material)).hexdigest(),
        plan_hash=operation.expected_plan_hash,
        capability_instance_ref=operation.capability_instance_ref,
        connector_key=operation.connector_key,
        connector_version=operation.connector_version,
        manifest_digest=operation.manifest_digest,
        artifact_digest=operation.artifact_digest,
        config_digest=operation.config_digest,
        approval_digest=operation.approval_digest,
        evidence_json=evidence,
    )
    db.add(receipt)
    return receipt


def _canonical(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ProvisioningRefused(
            "canonical document contains a non-JSON value"
        ) from exc
    return encoded.encode()


def _connector_error_detail(exc: BaseException) -> str:
    name = type(exc).__name__
    return name if name.isidentifier() else "Exception"


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
