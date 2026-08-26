"""Approval-bound provisioning command, execution and receipt canaries."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from dotmac_integration import (
    CapabilityBinding,
    ConnectorConfigRevision,
    ConnectorInstallation,
    add_binding,
    create_draft,
    enable,
    put_config_revision,
    set_binding_enabled,
)
from dotmac_integration.conformance import (
    FAKE_CAPABILITY,
    FakePlugin,
    fake_plugin,
    fake_registry,
)
from dotmac_integration.execution import payload_digest
from dotmac_integration.provisioning import (
    ApprovalRefused,
    CommandIdentityCollision,
    ExpectedProvisioningPin,
    PrerequisiteEvidenceBinding,
    PrerequisiteReceiptPin,
    ProvisioningCapabilityOperationPin,
    ProvisioningCommand,
    ProvisioningRefused,
    VerifiedApprovalGrant,
    accept_provisioning_command,
    invoke_prepared_cancellation,
    invoke_prepared_observation,
    invoke_prepared_plan,
    invoke_prepared_provisioning,
    prepare_cancellation,
    prepare_next_apply,
    prepare_next_observation,
    prepare_provisioning_plan,
    provisioning_command_template_digest,
    read_provisioning_plan_receipt,
    read_provisioning_receipts,
    settle_cancellation,
    settle_observation,
    settle_provisioning,
    settle_provisioning_plan,
)
from dotmac_integration.provisioning_models import (
    ProvisioningCommandReceipt,
    ProvisioningCommandRecord,
    ProvisioningOperation,
    ProvisioningReceipt,
    ProvisioningStep,
)
from dotmac_integration.spi import (
    CapabilityDeclaration,
    ProvisioningResult,
    ProvisionPlanResult,
    ProvisionResultStatus,
    ProvisionStep,
)
from dotmac_kernel import CapabilitySchemaDocument
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

NOW = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
PLAN_HASH = "sha256:" + "1" * 64
APPROVAL_DIGEST = "sha256:" + "b" * 64
CONNECTOR_ARTIFACT_DIGEST = "sha256:" + "c" * 64


def _field_values(value: object) -> list[object]:
    return [getattr(value, item.name) for item in fields(value)]  # type: ignore[arg-type]


@pytest.fixture()
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_intg": None}},
    )
    for model in (
        ConnectorInstallation,
        ConnectorConfigRevision,
        CapabilityBinding,
        ProvisioningOperation,
        ProvisioningStep,
        ProvisioningReceipt,
        ProvisioningCommandRecord,
        ProvisioningCommandReceipt,
    ):
        model.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _enabled(db: Session, plugin=None, *, name: str = "primary"):
    chosen = plugin or fake_plugin()
    registry = fake_registry(plugins=[chosen])
    installation = create_draft(
        db,
        registry=registry,
        connector_key="conformance_fake",
        name=name,
        connector_artifact_digest=CONNECTOR_ARTIFACT_DIGEST,
    )
    revision, _ = put_config_revision(
        db,
        installation,
        config={"endpoint": "https://service.example"},
        secret_refs={"credential": "bao://managed-services/test-only"},
    )
    enable(db, installation, registry=registry)
    binding = add_binding(
        db,
        installation,
        registry=registry,
        capability_id=FAKE_CAPABILITY,
        capability_instance_ref="primary",
    )
    set_binding_enabled(db, installation, binding, registry=registry, enabled=True)
    return registry, chosen, installation, revision, binding


def _plugin_requiring_action_target(
    action: str,
    *,
    field_schema: dict[str, object] | None = None,
) -> tuple[FakePlugin, CapabilityDeclaration]:
    base = fake_plugin(
        provisioning_result=ProvisioningResult(
            status=ProvisionResultStatus.ACCEPTED,
            provider_operation_ref="provider-operation-1",
        )
    )
    declaration = base.manifest.require_declares(FAKE_CAPABILITY)
    contract = declaration.contract_snapshot
    assert contract is not None
    operation = next(
        item for item in contract.operations if item.operation_code == action
    )
    schema = declaration.require_schema(
        operation.input_schema_ref,
        operation.input_schema_digest,
    )
    mapping = dict(schema.to_mapping())
    mapping["properties"] = {
        **dict(mapping["properties"]),  # type: ignore[arg-type]
        "required_target": field_schema or {"type": "string"},
    }
    mapping["required"] = ["required_target"]
    required_schema = CapabilitySchemaDocument.from_mapping(mapping)
    required_operation = replace(
        operation,
        input_schema_digest=required_schema.digest,
    )
    required_declaration = CapabilityDeclaration(
        capability_id=FAKE_CAPABILITY,
        contract_snapshot=replace(
            contract,
            operations=tuple(
                required_operation if item.operation_code == action else item
                for item in contract.operations
            ),
        ),
        schema_documents=tuple(
            sorted(
                (
                    required_schema if item.schema_ref == schema.schema_ref else item
                    for item in declaration.schema_documents
                ),
                key=lambda item: item.schema_ref,
            )
        ),
    )
    return (
        replace(
            base,
            manifest_=replace(base.manifest, capabilities=(required_declaration,)),
        ),
        required_declaration,
    )


def _command(
    db: Session,
    *,
    revision: ConnectorConfigRevision,
    binding: CapabilityBinding,
    **over,
):
    seed_plan = bool(over.pop("_seed_plan", True))
    installation = db.get(ConnectorInstallation, revision.installation_id)
    assert installation is not None
    declaration = over.pop(
        "_declaration",
        fake_plugin().manifest.require_declares(FAKE_CAPABILITY),
    )
    assert declaration.contract_snapshot is not None
    operations = tuple(
        ProvisioningCapabilityOperationPin(
            operation_code=operation.operation_code,
            input_schema_ref=operation.input_schema_ref,
            input_schema_digest=operation.input_schema_digest,
            output_schema_ref=operation.output_schema_ref,
            output_schema_digest=operation.output_schema_digest,
        )
        for operation in declaration.contract_snapshot.operations
    )
    command_id = str(over.get("command_id", "command-1"))
    plan_command_id = str(over.get("plan_command_id", f"plan-{command_id}"))
    request_body_digest = (
        "sha256:" + hashlib.sha256(plan_command_id.encode("utf-8")).hexdigest()
    )
    result_digest = "sha256:" + "6" * 64
    command_fingerprint = hashlib.sha256(plan_command_id.encode("utf-8")).hexdigest()
    capability_instance_ref = "primary"
    receipt_material = {
        "command_id": plan_command_id,
        "command_fingerprint": command_fingerprint,
        "capability_instance_ref": capability_instance_ref,
        "request_body_digest": request_body_digest,
        "result_digest": result_digest,
    }
    module_receipt_hash = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(receipt_material, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
    )
    steps = over.get(
        "steps",
        (
            ProvisionStep(
                step_key="first",
                endpoint_code=FAKE_CAPABILITY,
                depends_on=(),
                input={"desired_ref": "resource-1"},
            ),
        ),
    )
    deployment_ref = str(over.get("deployment_ref", "deployment-1"))
    plan_hash = str(over.get("plan_hash", PLAN_HASH))
    config_digest = "sha256:" + revision.config_digest
    record = db.scalar(
        select(ProvisioningCommandRecord).where(
            ProvisioningCommandRecord.command_id == plan_command_id
        )
    )
    if seed_plan and record is None:
        record = ProvisioningCommandRecord(
            command_id=plan_command_id,
            command_kind="plan",
            command_fingerprint=command_fingerprint,
            request_json={
                "deployment_ref": deployment_ref,
                "request_body_digest": request_body_digest,
                "capability_id": FAKE_CAPABILITY,
                "capability_instance_ref": capability_instance_ref,
                "binding_id": str(binding.id),
                "config_digest": config_digest,
                "plan_hash": plan_hash,
                "steps": [],
            },
            state="settled",
        )
        db.add(record)
        db.flush()
        db.add(
            ProvisioningCommandReceipt(
                command_record_id=record.id,
                command_id=plan_command_id,
                command_fingerprint=command_fingerprint,
                capability_instance_ref=capability_instance_ref,
                request_body_digest=request_body_digest,
                result_digest=result_digest,
                receipt_hash=module_receipt_hash,
            )
        )
        db.flush()
    values = {
        "command_id": command_id,
        "deployment_ref": deployment_ref,
        "desired_state_revision": 1,
        "desired_state_version_id": UUID("11111111-1111-4111-8111-111111111111"),
        "desired_state_hash": "sha256:" + "d" * 64,
        "saved_plan_id": UUID("22222222-2222-4222-8222-222222222222"),
        "approval_request_id": UUID("33333333-3333-4333-8333-333333333333"),
        "approval_request_binding_hash": "sha256:" + "e" * 64,
        "plan_command_id": plan_command_id,
        "plan_validation_receipt_id": UUID("44444444-4444-4444-8444-444444444444"),
        "plan_validation_receipt_digest": "sha256:" + "5" * 64,
        "plan_validation_request_body_digest": request_body_digest,
        "module_plan_receipt_hash": module_receipt_hash,
        "profile_version_id": UUID("55555555-5555-4555-8555-555555555555"),
        "profile_code": "managed.application",
        "profile_version": 1,
        "profile_schema_version": 1,
        "profile_content_hash": "sha256:" + "7" * 64,
        "command_schema_version": "integrator.provisioning-command.v1",
        "capability_id": FAKE_CAPABILITY,
        "capability_instance_ref": capability_instance_ref,
        "capability_owner_code": declaration.contract_snapshot.owner_code,
        "capability_code": FAKE_CAPABILITY.rsplit(".v", 1)[0],
        "capability_schema_version": declaration.contract_snapshot.schema_version,
        "capability_contract_attestation_id": UUID(
            "66666666-6666-4666-8666-666666666666"
        ),
        "capability_contract_digest": declaration.contract_snapshot.digest,
        "capability_operations": operations,
        "capability_binding_id": binding.id,
        "binding_ref": binding.id,
        "installation_id": installation.id,
        "installation_ref": installation.name,
        "connector_key": installation.connector_key,
        "connector_version": installation.connector_version,
        "connector_manifest_digest": "sha256:" + installation.manifest_digest,
        "connector_configuration_revision_id": revision.id,
        "configuration_snapshot_ref": "configuration-snapshot-1",
        "configuration_schema_version": 1,
        "configuration_hash": "sha256:" + "8" * 64,
        "plan_hash": plan_hash,
        "expected_plan_hash": PLAN_HASH,
        "artifact_digest": CONNECTOR_ARTIFACT_DIGEST,
        "component_artifact_digest": None,
        "config_digest": config_digest,
        "execution_policy_digest": (
            "sha256:c8b475b054fffe618dc26b02ca8f8fcc3ca4da13bc5670449343497bb475b536"
        ),
        "approval": VerifiedApprovalGrant(
            grant_ref="approval-1",
            approval_request_id=UUID("33333333-3333-4333-8333-333333333333"),
            approval_request_binding_hash="sha256:" + "e" * 64,
            saved_plan_id=UUID("22222222-2222-4222-8222-222222222222"),
            approved_plan_hash=PLAN_HASH,
            plan_command_id=plan_command_id,
            plan_validation_receipt_id=UUID("44444444-4444-4444-8444-444444444444"),
            plan_validation_receipt_digest="sha256:" + "5" * 64,
            plan_validation_request_body_digest=request_body_digest,
            module_plan_receipt_hash=module_receipt_hash,
            digest=APPROVAL_DIGEST,
            expires_at=NOW + timedelta(hours=1),
            verified_at=NOW - timedelta(minutes=1),
            approved_command_template_digest="sha256:" + "0" * 64,
        ),
        "steps": steps,
    }
    values.update(over)
    command = ProvisioningCommand(**values)
    return replace(
        command,
        approval=replace(
            command.approval,
            approved_command_template_digest=provisioning_command_template_digest(
                command
            ),
        ),
    )


def _expected_pin(db: Session, operation_id) -> ExpectedProvisioningPin:
    operation = db.get(ProvisioningOperation, operation_id)
    step = db.scalar(
        select(ProvisioningStep).where(ProvisioningStep.operation_id == operation_id)
    )
    assert operation is not None and step is not None
    assert step.provider_operation_ref is not None
    return ExpectedProvisioningPin(
        step_key=step.step_key,
        provider_operation_ref=step.provider_operation_ref,
        deployment_ref=operation.deployment_ref,
        capability_instance_ref=operation.capability_instance_ref,
        plan_hash=operation.expected_plan_hash,
        artifact_digest=operation.artifact_digest,
        config_digest=operation.config_digest,
        approval_digest=operation.approval_digest,
    )


def _finish_successfully(
    db: Session, *, command: ProvisioningCommand, registry
) -> tuple[ProvisioningOperation, ProvisioningReceipt]:
    accepted = accept_provisioning_command(db, command, registry=registry, now=NOW)
    prepared = prepare_next_apply(
        db, operation_id=accepted.operation_id, registry=registry, now=NOW
    )
    assert prepared is not None
    result = invoke_prepared_provisioning(
        prepared,
        registry=registry,
        resolve_secrets=lambda refs: {"credential": "held-test-material"},
    )
    operation = settle_provisioning(db, prepared=prepared, result=result, now=NOW)
    latest = db.scalar(
        select(ProvisioningReceipt)
        .where(ProvisioningReceipt.operation_id == operation.id)
        .order_by(ProvisioningReceipt.sequence.desc())
    )
    assert latest is not None
    return operation, latest


@pytest.mark.parametrize("case", ["wrong", "unverified", "expired"])
def test_wrong_unverified_and_expired_approval_refuse_before_plugin_io(
    db: Session, case: str
) -> None:
    registry, plugin, _, revision, binding = _enabled(db)
    command = _command(db, revision=revision, binding=binding)
    if case == "wrong":
        command = _command(
            db,
            revision=revision,
            binding=binding,
            expected_plan_hash="sha256:" + "2" * 64,
        )
    elif case == "unverified":
        command = _command(
            db,
            revision=revision,
            binding=binding,
            approval=replace(command.approval, verified_at=None),
        )
    else:
        command = _command(
            db,
            revision=revision,
            binding=binding,
            approval=replace(
                command.approval,
                expires_at=NOW - timedelta(seconds=1),
                verified_at=NOW - timedelta(hours=1),
            ),
        )
    with pytest.raises(ApprovalRefused):
        accept_provisioning_command(db, command, registry=registry, now=NOW)
    assert plugin.provisioning_seen == []
    assert db.scalar(select(ProvisioningOperation)) is None


def test_approval_is_rechecked_after_intake_before_plugin_io(db: Session) -> None:
    registry, plugin, _, revision, binding = _enabled(db)
    base = _command(db, revision=revision, binding=binding)
    command = _command(
        db,
        revision=revision,
        binding=binding,
        approval=replace(
            base.approval,
            expires_at=NOW + timedelta(seconds=30),
            verified_at=NOW,
        ),
    )
    accepted = accept_provisioning_command(db, command, registry=registry, now=NOW)
    with pytest.raises(ApprovalRefused, match="expired"):
        prepare_next_apply(
            db,
            operation_id=accepted.operation_id,
            registry=registry,
            now=NOW + timedelta(minutes=1),
        )
    assert plugin.provisioning_seen == []


def test_command_identity_replays_only_for_the_same_fingerprint(db: Session) -> None:
    registry, _, _, revision, binding = _enabled(db)
    command = _command(db, revision=revision, binding=binding)
    first = accept_provisioning_command(db, command, registry=registry, now=NOW)
    second = accept_provisioning_command(db, command, registry=registry, now=NOW)
    assert first.is_new is True
    assert second.is_new is False
    assert second.operation_id == first.operation_id

    changed = _command(
        db,
        revision=revision,
        binding=binding,
        artifact_digest="sha256:" + "9" * 64,
    )
    with pytest.raises(CommandIdentityCollision):
        accept_provisioning_command(db, changed, registry=registry, now=NOW)


def test_command_identity_collides_across_instances_of_the_same_capability(
    db: Session,
) -> None:
    registry, _, _, revision, binding = _enabled(db)
    accepted = _command(db, revision=revision, binding=binding)
    accept_provisioning_command(db, accepted, registry=registry, now=NOW)
    other_instance = replace(accepted, capability_instance_ref="secondary")
    other_instance = replace(
        other_instance,
        approval=replace(
            other_instance.approval,
            approved_command_template_digest=provisioning_command_template_digest(
                other_instance
            ),
        ),
    )

    with pytest.raises(CommandIdentityCollision):
        accept_provisioning_command(db, other_instance, registry=registry, now=NOW)


def test_apply_refuses_an_instance_that_differs_from_the_local_binding(
    db: Session,
) -> None:
    registry, plugin, _, revision, binding = _enabled(db)
    base = _command(db, revision=revision, binding=binding)
    wrong = replace(
        base,
        command_id="different-instance-command",
        capability_instance_ref="secondary",
    )
    wrong = replace(
        wrong,
        approval=replace(
            wrong.approval,
            approved_command_template_digest=provisioning_command_template_digest(
                wrong
            ),
        ),
    )

    with pytest.raises(ProvisioningRefused, match="does not serve capability"):
        accept_provisioning_command(db, wrong, registry=registry, now=NOW)
    assert plugin.provisioning_seen == []
    assert db.scalar(select(ProvisioningOperation)) is None


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        ("installation_id", UUID("77777777-7777-4777-8777-777777777777")),
        ("installation_ref", "different-installation"),
        ("artifact_digest", "sha256:" + "9" * 64),
        (
            "connector_configuration_revision_id",
            UUID("88888888-8888-4888-8888-888888888888"),
        ),
        ("capability_contract_digest", "sha256:" + "9" * 64),
        ("execution_policy_digest", "sha256:" + "9" * 64),
        ("module_plan_receipt_hash", "sha256:" + "9" * 64),
    ],
)
def test_static_execution_pin_tampering_refuses_before_state_or_plugin_io(
    db: Session, field_name: str, bad_value: object
) -> None:
    registry, plugin, _, revision, binding = _enabled(db)
    command = _command(
        db,
        revision=revision,
        binding=binding,
        command_id=f"tampered-{field_name}",
        **{field_name: bad_value},
    )

    with pytest.raises(ProvisioningRefused):
        accept_provisioning_command(db, command, registry=registry, now=NOW)

    assert plugin.provisioning_seen == []
    assert db.scalar(select(ProvisioningOperation)) is None
    assert (
        db.scalar(
            select(ProvisioningCommandRecord).where(
                ProvisioningCommandRecord.command_id == command.command_id
            )
        )
        is None
    )


def test_apply_requires_a_locally_settled_plan_receipt_before_mutation(
    db: Session,
) -> None:
    registry, plugin, _, revision, binding = _enabled(db)
    command = _command(
        db,
        revision=revision,
        binding=binding,
        command_id="missing-local-plan",
        _seed_plan=False,
    )

    with pytest.raises(ProvisioningRefused, match="approved PLAN"):
        accept_provisioning_command(db, command, registry=registry, now=NOW)

    assert plugin.provisioning_seen == []
    assert db.scalar(select(ProvisioningOperation)) is None


def test_cross_binding_apply_requires_exact_latest_succeeded_receipt(
    db: Session,
) -> None:
    registry_a, _, installation_a, revision_a, binding_a = _enabled(db)
    upstream, receipt = _finish_successfully(
        db,
        command=_command(db, revision=revision_a, binding=binding_a),
        registry=registry_a,
    )
    set_binding_enabled(
        db,
        installation_a,
        binding_a,
        registry=registry_a,
        enabled=False,
    )
    registry_b, _, _, revision_b, binding_b = _enabled(db, name="secondary")
    pin = PrerequisiteReceiptPin(
        capability_binding_id=binding_a.id,
        operation_id=upstream.id,
        terminal_receipt_sequence=receipt.sequence,
        terminal_receipt_digest=receipt.receipt_hash,
    )
    downstream = _command(
        db,
        revision=revision_b,
        binding=binding_b,
        command_id="command-downstream",
        prerequisite_capability_binding_ids=(binding_a.id,),
        prerequisite_receipt_pins=(pin,),
    )
    accepted = accept_provisioning_command(db, downstream, registry=registry_b, now=NOW)
    assert accepted.is_new is True

    changed_pin = replace(
        downstream,
        prerequisite_receipt_pins=(
            replace(pin, terminal_receipt_digest="sha256:" + "f" * 64),
        ),
    )
    with pytest.raises(CommandIdentityCollision):
        accept_provisioning_command(db, changed_pin, registry=registry_b, now=NOW)


def test_prerequisite_evidence_binding_is_public_frozen_and_canonical() -> None:
    first = PrerequisiteEvidenceBinding(
        source_capability_binding_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        source_step_key="first",
        source_schema_ref="schema:conformance/apply-output@v1",
        source_schema_digest="sha256:" + "1" * 64,
        source_pointer="/phase",
        target_step_key="second",
        target_schema_ref="schema:conformance/apply-input@v1",
        target_schema_digest="sha256:" + "2" * 64,
        target_pointer="/phase",
        required=True,
    )
    second = replace(first, source_pointer="/public_ref", target_pointer="/public_ref")

    assert "source_pointer" in {field.name for field in fields(first)}
    assert "phase" not in repr(first)
    with pytest.raises(ProvisioningRefused, match="sorted"):
        _validate_prerequisite_binding_fixture((second, first))


def _validate_prerequisite_binding_fixture(
    bindings: tuple[PrerequisiteEvidenceBinding, ...],
) -> None:
    """Drive the public command validator rather than copy its ordering rule."""

    from dotmac_integration.provisioning import _validate_evidence_bindings

    _validate_evidence_bindings(bindings)


def test_duplicate_prerequisite_target_is_refused() -> None:
    base = PrerequisiteEvidenceBinding(
        source_capability_binding_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        source_step_key="first",
        source_schema_ref="schema:conformance/apply-output@v1",
        source_schema_digest="sha256:" + "1" * 64,
        source_pointer="/phase",
        target_step_key="second",
        target_schema_ref="schema:conformance/apply-input@v1",
        target_schema_digest="sha256:" + "2" * 64,
        target_pointer="/phase",
        required=True,
    )

    with pytest.raises(ProvisioningRefused, match="target"):
        _validate_prerequisite_binding_fixture(
            (base, replace(base, source_pointer="/public_ref"))
        )


def test_prepare_injects_only_attested_public_evidence_and_records_digest(
    db: Session,
) -> None:
    upstream_plugin = fake_plugin(
        provisioning_result=ProvisioningResult(
            status=ProvisionResultStatus.SUCCEEDED,
            evidence={"phase": "ready", "detail": "must-not-persist"},
        )
    )
    registry_a, _, installation_a, revision_a, binding_a = _enabled(db, upstream_plugin)
    upstream, receipt = _finish_successfully(
        db,
        command=_command(db, revision=revision_a, binding=binding_a),
        registry=registry_a,
    )
    assert receipt.evidence_json["public_evidence"] == {"phase": "ready"}
    assert "must-not-persist" not in repr(receipt.evidence_json)
    set_binding_enabled(
        db, installation_a, binding_a, registry=registry_a, enabled=False
    )

    registry_b, _, _, revision_b, binding_b = _enabled(db, name="secondary")
    declaration = fake_plugin().manifest.require_declares(FAKE_CAPABILITY)
    apply_operation = next(
        item
        for item in declaration.contract_snapshot.operations
        if item.operation_code == "apply"
    )
    target_schema = declaration.require_schema(
        apply_operation.input_schema_ref,
        apply_operation.input_schema_digest,
    )
    assert "x-dotmac-data-classification" not in target_schema.require_instance_pointer(
        "/phase"
    )
    mapping = PrerequisiteEvidenceBinding(
        source_capability_binding_id=binding_a.id,
        source_step_key="first",
        source_schema_ref=apply_operation.output_schema_ref,
        source_schema_digest=apply_operation.output_schema_digest,
        source_pointer="/phase",
        target_step_key="first",
        target_schema_ref=apply_operation.input_schema_ref,
        target_schema_digest=apply_operation.input_schema_digest,
        target_pointer="/phase",
        required=True,
    )
    pin = PrerequisiteReceiptPin(
        capability_binding_id=binding_a.id,
        operation_id=upstream.id,
        terminal_receipt_sequence=receipt.sequence,
        terminal_receipt_digest=receipt.receipt_hash,
    )
    invalid_target = _command(
        db,
        revision=revision_b,
        binding=binding_b,
        command_id="invalid-evidence-target",
        prerequisite_capability_binding_ids=(binding_a.id,),
        prerequisite_receipt_pins=(pin,),
        prerequisite_evidence_bindings=(
            replace(mapping, target_pointer="/not_declared"),
        ),
    )
    with pytest.raises(ProvisioningRefused, match="target pointer"):
        accept_provisioning_command(
            db,
            invalid_target,
            registry=registry_b,
            now=NOW,
        )
    assert (
        db.scalar(
            select(ProvisioningCommandRecord).where(
                ProvisioningCommandRecord.command_id == invalid_target.command_id
            )
        )
        is None
    )

    command = _command(
        db,
        revision=revision_b,
        binding=binding_b,
        command_id="evidence-downstream",
        prerequisite_capability_binding_ids=(binding_a.id,),
        prerequisite_receipt_pins=(pin,),
        prerequisite_evidence_bindings=(mapping,),
    )
    accepted = accept_provisioning_command(db, command, registry=registry_b, now=NOW)
    prepared = prepare_next_apply(
        db, operation_id=accepted.operation_id, registry=registry_b, now=NOW
    )

    assert prepared is not None
    assert prepared.step.input["phase"] == "ready"
    stored_step = db.get(ProvisioningStep, prepared.step_id)
    assert stored_step is not None
    assert stored_step.resolved_input_digest == "sha256:" + payload_digest(
        dict(prepared.step.input)
    )
    command_record = db.scalar(
        select(ProvisioningCommandRecord).where(
            ProvisioningCommandRecord.command_id == command.command_id
        )
    )
    assert command_record is not None
    assert command_record.request_json["steps"][0]["input"] == {
        "desired_ref": "resource-1"
    }


def test_cross_binding_refuses_missing_or_nonterminal_evidence_before_mutation(
    db: Session,
) -> None:
    registry_a, _, installation_a, revision_a, binding_a = _enabled(db)
    upstream = accept_provisioning_command(
        db,
        _command(db, revision=revision_a, binding=binding_a),
        registry=registry_a,
        now=NOW,
    )
    first_receipt = db.scalar(
        select(ProvisioningReceipt).where(
            ProvisioningReceipt.operation_id == upstream.operation_id
        )
    )
    assert first_receipt is not None
    set_binding_enabled(
        db,
        installation_a,
        binding_a,
        registry=registry_a,
        enabled=False,
    )
    registry_b, plugin_b, _, revision_b, binding_b = _enabled(db, name="secondary")

    missing = _command(
        db,
        revision=revision_b,
        binding=binding_b,
        command_id="missing-evidence",
        prerequisite_capability_binding_ids=(binding_a.id,),
    )
    with pytest.raises(ProvisioningRefused, match="exactly cover"):
        accept_provisioning_command(db, missing, registry=registry_b, now=NOW)

    nonterminal = _command(
        db,
        revision=revision_b,
        binding=binding_b,
        command_id="nonterminal-evidence",
        prerequisite_capability_binding_ids=(binding_a.id,),
        prerequisite_receipt_pins=(
            PrerequisiteReceiptPin(
                capability_binding_id=binding_a.id,
                operation_id=upstream.operation_id,
                terminal_receipt_sequence=first_receipt.sequence,
                terminal_receipt_digest=first_receipt.receipt_hash,
            ),
        ),
    )
    with pytest.raises(ProvisioningRefused, match="required terminal status"):
        accept_provisioning_command(db, nonterminal, registry=registry_b, now=NOW)
    assert plugin_b.provisioning_seen == []
    assert (
        db.scalar(
            select(ProvisioningCommandRecord).where(
                ProvisioningCommandRecord.command_id == "nonterminal-evidence"
            )
        )
        is None
    )


def test_template_digest_binds_symbolic_edges_but_not_later_receipt_bytes(
    db: Session,
) -> None:
    _, _, _, revision, binding = _enabled(db)
    other_binding_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    evidence_binding = PrerequisiteEvidenceBinding(
        source_capability_binding_id=other_binding_id,
        source_step_key="first",
        source_schema_ref="schema:conformance/apply-output@v1",
        source_schema_digest="sha256:" + "a" * 64,
        source_pointer="/phase",
        target_step_key="first",
        target_schema_ref="schema:conformance/apply-input@v1",
        target_schema_digest="sha256:" + "b" * 64,
        target_pointer="/phase",
        required=True,
    )
    command = _command(
        db,
        revision=revision,
        binding=binding,
        prerequisite_capability_binding_ids=(other_binding_id,),
        prerequisite_evidence_bindings=(evidence_binding,),
        prerequisite_receipt_pins=(
            PrerequisiteReceiptPin(
                capability_binding_id=other_binding_id,
                operation_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
                terminal_receipt_sequence=2,
                terminal_receipt_digest="sha256:" + "c" * 64,
            ),
        ),
    )
    changed_receipt = replace(
        command,
        prerequisite_receipt_pins=(
            replace(
                command.prerequisite_receipt_pins[0],
                terminal_receipt_digest="sha256:" + "d" * 64,
            ),
        ),
    )
    assert provisioning_command_template_digest(changed_receipt) == (
        provisioning_command_template_digest(command)
    )

    changed_edge = replace(
        command,
        prerequisite_capability_binding_ids=(
            UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
        ),
    )
    assert provisioning_command_template_digest(changed_edge) != (
        provisioning_command_template_digest(command)
    )

    changed_mapping = replace(
        command,
        prerequisite_evidence_bindings=(
            replace(evidence_binding, target_pointer="/public_ref"),
        ),
    )
    assert provisioning_command_template_digest(changed_mapping) != (
        provisioning_command_template_digest(command)
    )


def test_reverification_time_is_evidence_not_command_identity(db: Session) -> None:
    registry, _, _, revision, binding = _enabled(db)
    first = _command(db, revision=revision, binding=binding)
    accepted = accept_provisioning_command(db, first, registry=registry, now=NOW)
    reverified = _command(
        db,
        revision=revision,
        binding=binding,
        approval=replace(first.approval, verified_at=NOW + timedelta(seconds=5)),
    )
    replay = accept_provisioning_command(
        db, reverified, registry=registry, now=NOW + timedelta(seconds=5)
    )
    assert replay.operation_id == accepted.operation_id
    assert replay.is_new is False


def test_apply_is_three_phase_and_receipts_chain_exact_provenance(db: Session) -> None:
    registry, plugin, installation, revision, binding = _enabled(db)
    accepted = accept_provisioning_command(
        db,
        _command(db, revision=revision, binding=binding),
        registry=registry,
        now=NOW,
    )
    prepared = prepare_next_apply(
        db, operation_id=accepted.operation_id, registry=registry, now=NOW
    )
    assert prepared is not None
    assert not any(isinstance(value, Session) for value in _field_values(prepared))

    result = invoke_prepared_provisioning(
        prepared,
        registry=registry,
        resolve_secrets=lambda refs: {"credential": "materialized-for-call"},
    )
    assert result.status is ProvisionResultStatus.SUCCEEDED
    assert plugin.provisioning_seen
    request = plugin.provisioning_seen[-1]
    assert not any(isinstance(value, Session) for value in _field_values(request))

    operation = settle_provisioning(db, prepared=prepared, result=result, now=NOW)
    assert operation.state == "succeeded"
    receipts = list(
        db.scalars(
            select(ProvisioningReceipt)
            .where(ProvisioningReceipt.operation_id == operation.id)
            .order_by(ProvisioningReceipt.sequence)
        )
    )
    assert [receipt.receipt_kind for receipt in receipts] == [
        "command_accepted",
        "step_succeeded",
    ]
    assert receipts[0].previous_receipt_hash is None
    assert receipts[1].previous_receipt_hash == receipts[0].receipt_hash
    assert receipts[0].receipt_hash != receipts[1].receipt_hash
    for receipt in receipts:
        assert receipt.plan_hash == PLAN_HASH
        assert receipt.capability_instance_ref == "primary"
        assert receipt.connector_key == installation.connector_key
        assert receipt.connector_version == installation.connector_version
        assert receipt.manifest_digest == "sha256:" + installation.manifest_digest
        assert receipt.artifact_digest == CONNECTOR_ARTIFACT_DIGEST
        assert receipt.config_digest == "sha256:" + revision.config_digest
        assert receipt.approval_digest == APPROVAL_DIGEST


def test_ambiguous_or_raising_provider_requires_reconciliation(db: Session) -> None:
    ambiguous = fake_plugin(
        provisioning_result=ProvisioningResult(
            status=ProvisionResultStatus.AMBIGUOUS,
            provider_operation_ref="provider-unknown",
            evidence={"phase": "write"},
        )
    )
    registry, _, _, revision, binding = _enabled(db, ambiguous)
    accepted = accept_provisioning_command(
        db,
        _command(db, revision=revision, binding=binding),
        registry=registry,
        now=NOW,
    )
    prepared = prepare_next_apply(
        db, operation_id=accepted.operation_id, registry=registry, now=NOW
    )
    assert prepared is not None
    result = invoke_prepared_provisioning(
        prepared, registry=registry, resolve_secrets=lambda refs: {}
    )
    operation = settle_provisioning(db, prepared=prepared, result=result, now=NOW)
    assert operation.state == "reconciliation_required"
    step = db.scalar(
        select(ProvisioningStep).where(ProvisioningStep.operation_id == operation.id)
    )
    assert step is not None
    assert step.state == "reconciliation_required"
    assert step.next_attempt_at is None


def test_plugin_exception_is_ambiguous_and_never_persists_its_message(
    db: Session,
) -> None:
    sentinel = "SENTINEL-MATERIALIZED-PROVISIONING-SECRET-718c"
    broken = fake_plugin(
        provisioning_raises=RuntimeError(f"provider accepted {sentinel}")
    )
    registry, _, _, revision, binding = _enabled(db, broken)
    accepted = accept_provisioning_command(
        db,
        _command(db, revision=revision, binding=binding),
        registry=registry,
        now=NOW,
    )
    prepared = prepare_next_apply(
        db, operation_id=accepted.operation_id, registry=registry, now=NOW
    )
    assert prepared is not None
    result = invoke_prepared_provisioning(
        prepared,
        registry=registry,
        resolve_secrets=lambda refs: {"credential": sentinel},
    )
    assert result.status is ProvisionResultStatus.AMBIGUOUS
    assert result.error_detail == "RuntimeError"
    assert sentinel not in repr(result)
    operation = settle_provisioning(db, prepared=prepared, result=result, now=NOW)
    assert sentinel not in (operation.error_detail or "")


def test_direct_apply_settlement_never_persists_caller_error_prose(
    db: Session,
) -> None:
    sentinel = "SENTINEL-DIRECT-SETTLE-MATERIALIZED-SECRET-1037"
    registry, _, _, revision, binding = _enabled(db, fake_plugin())
    accepted = accept_provisioning_command(
        db,
        _command(db, revision=revision, binding=binding),
        registry=registry,
        now=NOW,
    )
    prepared = prepare_next_apply(
        db, operation_id=accepted.operation_id, registry=registry, now=NOW
    )
    assert prepared is not None

    operation = settle_provisioning(
        db,
        prepared=prepared,
        result=ProvisioningResult(
            status=ProvisionResultStatus.TERMINAL,
            error_code="provider_refused",
            error_detail=sentinel,
        ),
        now=NOW,
    )
    step = db.scalar(
        select(ProvisioningStep).where(ProvisioningStep.operation_id == operation.id)
    )
    receipt = read_provisioning_receipts(db, operation_id=operation.id)[-1]

    assert step is not None
    assert operation.error_detail is None
    assert step.error_detail is None
    assert receipt.evidence["error_detail"] is None
    assert sentinel not in repr(receipt)


def test_connector_evidence_values_are_digested_never_persisted(db: Session) -> None:
    sentinel = "SENTINEL-PROVISION-EVIDENCE-SECRET-9981"
    plugin = fake_plugin(
        provisioning_result=ProvisioningResult(
            status=ProvisionResultStatus.SUCCEEDED,
            evidence={"detail": sentinel},
        )
    )
    registry, _, _, revision, binding = _enabled(db, plugin)
    accepted = accept_provisioning_command(
        db,
        _command(db, revision=revision, binding=binding),
        registry=registry,
        now=NOW,
    )
    prepared = prepare_next_apply(
        db, operation_id=accepted.operation_id, registry=registry, now=NOW
    )
    assert prepared is not None
    result = invoke_prepared_provisioning(
        prepared, registry=registry, resolve_secrets=lambda refs: {}
    )
    operation = settle_provisioning(db, prepared=prepared, result=result, now=NOW)
    receipts = read_provisioning_receipts(db, operation_id=operation.id)
    assert sentinel not in repr(receipts)
    assert "provider_evidence_digest" in receipts[-1].evidence

    # Sensitivity: the projection verifies, rather than merely displaying, the
    # chain. Mark the in-memory mutation committed so the ORM guard is not what
    # catches this deliberately corrupted read.
    from sqlalchemy.orm.attributes import set_committed_value

    row = db.scalar(
        select(ProvisioningReceipt).where(
            ProvisioningReceipt.operation_id == operation.id,
            ProvisioningReceipt.sequence == 1,
        )
    )
    assert row is not None
    set_committed_value(row, "receipt_hash", "sha256:" + "0" * 64)
    with pytest.raises(ProvisioningRefused, match="receipt chain"):
        read_provisioning_receipts(db, operation_id=operation.id)


def test_apply_result_must_match_the_exact_held_output_schema(db: Session) -> None:
    plugin = fake_plugin(
        provisioning_result=ProvisioningResult(
            status=ProvisionResultStatus.SUCCEEDED,
            evidence={"phase": 7},
        )
    )
    registry, _, _, revision, binding = _enabled(db, plugin)
    accepted = accept_provisioning_command(
        db,
        _command(db, revision=revision, binding=binding),
        registry=registry,
        now=NOW,
    )
    prepared = prepare_next_apply(
        db, operation_id=accepted.operation_id, registry=registry, now=NOW
    )
    assert prepared is not None

    result = invoke_prepared_provisioning(
        prepared, registry=registry, resolve_secrets=lambda refs: {}
    )

    assert result.status is ProvisionResultStatus.AMBIGUOUS
    assert result.error_code == "connector_contract"
    assert result.evidence == {}


def test_non_success_result_does_not_owe_a_success_output_document(
    db: Session,
) -> None:
    from dotmac_integration.spi import CapabilityDeclaration
    from dotmac_kernel import CapabilitySchemaDocument

    base_plugin = fake_plugin()
    base_declaration = base_plugin.manifest.require_declares(FAKE_CAPABILITY)
    contract = base_declaration.contract_snapshot
    assert contract is not None
    apply_operation = next(
        item for item in contract.operations if item.operation_code == "apply"
    )
    old_schema = base_declaration.require_schema(
        apply_operation.output_schema_ref,
        apply_operation.output_schema_digest,
    )
    required_mapping = dict(old_schema.to_mapping())
    required_mapping["required"] = ["public_ref"]
    required_schema = CapabilitySchemaDocument.from_mapping(required_mapping)
    required_operation = replace(
        apply_operation,
        output_schema_digest=required_schema.digest,
    )
    required_contract = replace(
        contract,
        operations=tuple(
            required_operation if item.operation_code == "apply" else item
            for item in contract.operations
        ),
    )
    required_declaration = CapabilityDeclaration(
        capability_id=FAKE_CAPABILITY,
        contract_snapshot=required_contract,
        schema_documents=tuple(
            sorted(
                (
                    required_schema
                    if item.schema_ref == required_schema.schema_ref
                    else item
                    for item in base_declaration.schema_documents
                ),
                key=lambda item: item.schema_ref,
            )
        ),
    )
    plugin = replace(
        base_plugin,
        manifest_=replace(
            base_plugin.manifest,
            capabilities=(required_declaration,),
        ),
        provisioning_result=ProvisioningResult(
            status=ProvisionResultStatus.RETRYABLE,
            error_code="temporarily_unavailable",
        ),
    )
    registry, _, _, revision, binding = _enabled(db, plugin)
    accepted = accept_provisioning_command(
        db,
        _command(
            db,
            revision=revision,
            binding=binding,
            _declaration=required_declaration,
        ),
        registry=registry,
        now=NOW,
    )
    prepared = prepare_next_apply(
        db, operation_id=accepted.operation_id, registry=registry, now=NOW
    )
    assert prepared is not None

    result = invoke_prepared_provisioning(
        prepared, registry=registry, resolve_secrets=lambda refs: {}
    )

    assert result.status is ProvisionResultStatus.RETRYABLE
    operation = settle_provisioning(db, prepared=prepared, result=result, now=NOW)
    assert operation.state == "retryable"


def test_explicit_retry_is_scheduled_but_ambiguous_never_is(db: Session) -> None:
    retrying = fake_plugin(
        provisioning_result=ProvisioningResult(
            status=ProvisionResultStatus.RETRYABLE,
            error_code="temporarily_unavailable",
        )
    )
    registry, _, _, revision, binding = _enabled(db, retrying)
    accepted = accept_provisioning_command(
        db,
        _command(db, revision=revision, binding=binding),
        registry=registry,
        now=NOW,
    )
    prepared = prepare_next_apply(
        db, operation_id=accepted.operation_id, registry=registry, now=NOW
    )
    assert prepared is not None
    result = invoke_prepared_provisioning(
        prepared, registry=registry, resolve_secrets=lambda refs: {}
    )
    operation = settle_provisioning(db, prepared=prepared, result=result, now=NOW)
    assert operation.state == "retryable"
    step = db.scalar(
        select(ProvisioningStep).where(ProvisioningStep.operation_id == operation.id)
    )
    assert step is not None
    assert step.state == "retryable"
    assert step.next_attempt_at is not None
    due = step.next_attempt_at
    if due.tzinfo is None:  # SQLite drops timezone information.
        due = due.replace(tzinfo=UTC)
    assert due > NOW


def test_shell_and_secret_shaped_step_inputs_are_refused_before_persistence(
    db: Session,
) -> None:
    registry, plugin, _, revision, binding = _enabled(db)
    for unsafe in (
        {"shell": "do something"},
        {"shell_command": "do something"},
        {"startup_script": "do something"},
        {"exec_args": ["tool"]},
        {"argv": ["tool", "--flag"]},
        {"api_token": "literal-material"},
    ):
        with pytest.raises(ProvisioningRefused):
            _command(
                db,
                revision=revision,
                binding=binding,
                command_id=f"unsafe-{next(iter(unsafe))}",
                steps=(
                    ProvisionStep(
                        step_key="unsafe",
                        endpoint_code=FAKE_CAPABILITY,
                        depends_on=(),
                        input=unsafe,
                    ),
                ),
            )
    assert plugin.provisioning_seen == []


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_step_inputs_are_refused_before_canonicalization(
    db: Session, value: float
) -> None:
    registry, plugin, _, revision, binding = _enabled(db)
    with pytest.raises(ProvisioningRefused, match="non-finite number"):
        _command(
            db,
            revision=revision,
            binding=binding,
            command_id="non-finite",
            steps=(
                ProvisionStep(
                    step_key="non-finite",
                    endpoint_code=FAKE_CAPABILITY,
                    depends_on=(),
                    input={"quantity": value},
                ),
            ),
        )

    assert plugin.provisioning_seen == []


def test_prepared_request_repr_never_contains_secret_references(db: Session) -> None:
    registry, _, _, revision, binding = _enabled(db)
    accepted = accept_provisioning_command(
        db,
        _command(db, revision=revision, binding=binding),
        registry=registry,
        now=NOW,
    )
    prepared = prepare_next_apply(
        db, operation_id=accepted.operation_id, registry=registry, now=NOW
    )
    assert prepared is not None
    assert "bao://managed-services/test-only" not in repr(prepared)


def test_plan_is_prepare_invoke_settle_and_exact_hash_bound(db: Session) -> None:
    registry, plugin, _, revision, binding = _enabled(db)
    command = _command(db, revision=revision, binding=binding, _seed_plan=False)
    prepared = prepare_provisioning_plan(
        db,
        command_id="plan-command-1",
        deployment_ref=command.deployment_ref,
        request_body_digest="sha256:" + "7" * 64,
        capability_id=command.capability_id,
        capability_instance_ref=command.capability_instance_ref,
        binding_id=command.capability_binding_id,
        config_digest=command.config_digest,
        plan_hash=command.plan_hash,
        steps=command.steps,
        registry=registry,
    )
    assert not any(isinstance(value, Session) for value in _field_values(prepared))
    result = invoke_prepared_plan(
        prepared, registry=registry, resolve_secrets=lambda refs: {}
    )
    with pytest.raises(ProvisioningRefused, match="plan hash"):
        settle_provisioning_plan(
            db,
            prepared=prepared,
            result=type(result)(
                plan_hash="sha256:" + "9" * 64,
                steps=result.steps,
            ),
        )
    settled = settle_provisioning_plan(db, prepared=prepared, result=result)
    assert settled.plan_hash == PLAN_HASH
    receipt = read_provisioning_plan_receipt(db, command_id="plan-command-1")
    assert receipt.capability_instance_ref == "primary"
    assert receipt.request_body_digest == "sha256:" + "7" * 64
    assert receipt.result_digest.startswith("sha256:")
    assert receipt.receipt_hash.startswith("sha256:")
    assert db.scalar(select(ProvisioningOperation)) is None
    assert type(plugin.provisioning_seen[-1]).__name__ == "ProvisionPlanRequest"
    assert (
        prepare_provisioning_plan(
            db,
            command_id="plan-command-1",
            deployment_ref=command.deployment_ref,
            request_body_digest="sha256:" + "7" * 64,
            capability_id=command.capability_id,
            capability_instance_ref=command.capability_instance_ref,
            binding_id=command.capability_binding_id,
            config_digest=command.config_digest,
            plan_hash=command.plan_hash,
            steps=command.steps,
            registry=registry,
        )
        is None
    )
    with pytest.raises(CommandIdentityCollision):
        prepare_provisioning_plan(
            db,
            command_id="plan-command-1",
            deployment_ref=command.deployment_ref,
            request_body_digest="sha256:" + "7" * 64,
            capability_id=command.capability_id,
            capability_instance_ref=command.capability_instance_ref,
            binding_id=command.capability_binding_id,
            config_digest=command.config_digest,
            plan_hash="sha256:" + "8" * 64,
            steps=command.steps,
            registry=registry,
        )


def test_plan_input_is_validated_before_command_mutation_or_plugin_io(
    db: Session,
) -> None:
    registry, plugin, _, revision, binding = _enabled(db)

    with pytest.raises(ProvisioningRefused, match="plan input"):
        prepare_provisioning_plan(
            db,
            command_id="plan-invalid-input",
            deployment_ref="deployment-1",
            request_body_digest="sha256:" + "7" * 64,
            capability_id=FAKE_CAPABILITY,
            capability_instance_ref="primary",
            binding_id=binding.id,
            config_digest="sha256:" + revision.config_digest,
            plan_hash=PLAN_HASH,
            steps=(
                ProvisionStep(
                    step_key="invalid",
                    endpoint_code=FAKE_CAPABILITY,
                    depends_on=(),
                    input={"desired_ref": 7},
                ),
            ),
            registry=registry,
        )

    assert plugin.provisioning_seen == []
    assert (
        db.scalar(
            select(ProvisioningCommandRecord).where(
                ProvisioningCommandRecord.command_id == "plan-invalid-input"
            )
        )
        is None
    )


def test_plan_input_enforces_declared_json_schema_formats_before_io(
    db: Session,
) -> None:
    plugin, _ = _plugin_requiring_action_target(
        "plan",
        field_schema={"format": "email", "type": "string"},
    )
    registry, selected, _, revision, binding = _enabled(db, plugin=plugin)

    with pytest.raises(ProvisioningRefused, match="plan input"):
        prepare_provisioning_plan(
            db,
            command_id="plan-invalid-format",
            deployment_ref="deployment-1",
            request_body_digest="sha256:" + "7" * 64,
            capability_id=FAKE_CAPABILITY,
            capability_instance_ref="primary",
            binding_id=binding.id,
            config_digest="sha256:" + revision.config_digest,
            plan_hash=PLAN_HASH,
            steps=(
                ProvisionStep(
                    step_key="invalid",
                    endpoint_code=FAKE_CAPABILITY,
                    depends_on=(),
                    input={"required_target": "not-an-email"},
                ),
            ),
            registry=registry,
        )

    assert selected.provisioning_seen == []
    assert (
        db.scalar(
            select(ProvisioningCommandRecord).where(
                ProvisioningCommandRecord.command_id == "plan-invalid-format"
            )
        )
        is None
    )


def test_plan_refuses_a_different_instance_of_the_same_capability_before_io(
    db: Session,
) -> None:
    registry, plugin, _, revision, binding = _enabled(db)

    with pytest.raises(ProvisioningRefused, match="does not serve capability"):
        prepare_provisioning_plan(
            db,
            command_id="plan-wrong-instance",
            deployment_ref="deployment-1",
            request_body_digest="sha256:" + "7" * 64,
            capability_id=FAKE_CAPABILITY,
            capability_instance_ref="secondary",
            binding_id=binding.id,
            config_digest="sha256:" + revision.config_digest,
            plan_hash=PLAN_HASH,
            steps=(
                ProvisionStep(
                    step_key="first",
                    endpoint_code=FAKE_CAPABILITY,
                    input={"desired_ref": "resource-1"},
                ),
            ),
            registry=registry,
        )

    assert plugin.provisioning_seen == []


def test_plan_output_is_validated_and_only_its_digest_is_receipted(
    db: Session,
) -> None:
    sentinel = "SENTINEL-PLAN-PRIVATE-EVIDENCE-2341"
    plugin = fake_plugin(
        provisioning_plan_result=ProvisionPlanResult(
            plan_hash=PLAN_HASH,
            steps=(),
            evidence={"phase": "ready", "detail": sentinel},
        )
    )
    registry, _, _, revision, binding = _enabled(db, plugin)
    prepared = prepare_provisioning_plan(
        db,
        command_id="plan-evidence",
        deployment_ref="deployment-1",
        request_body_digest="sha256:" + "7" * 64,
        capability_id=FAKE_CAPABILITY,
        capability_instance_ref="primary",
        binding_id=binding.id,
        config_digest="sha256:" + revision.config_digest,
        plan_hash=PLAN_HASH,
        steps=(),
        registry=registry,
    )
    assert prepared is not None

    result = invoke_prepared_plan(
        prepared, registry=registry, resolve_secrets=lambda refs: {}
    )
    with pytest.raises(TypeError):
        result.evidence["changed"] = True  # type: ignore[index]
    settle_provisioning_plan(db, prepared=prepared, result=result)
    receipt = read_provisioning_plan_receipt(db, command_id="plan-evidence")

    evidence_digest = "sha256:" + payload_digest(dict(result.evidence))
    expected_result_digest = "sha256:" + payload_digest(
        {
            "evidence_digest": evidence_digest,
            "plan_hash": result.plan_hash,
            "steps": [],
        }
    )
    assert receipt.result_digest == expected_result_digest
    assert sentinel not in repr(receipt)


def test_plan_invalid_output_refuses_at_invoke_and_settlement(db: Session) -> None:
    invalid = ProvisionPlanResult(
        plan_hash=PLAN_HASH,
        steps=(),
        evidence={"phase": 7},
    )
    plugin = fake_plugin(provisioning_plan_result=invalid)
    registry, _, _, revision, binding = _enabled(db, plugin)
    prepared = prepare_provisioning_plan(
        db,
        command_id="plan-invalid-output",
        deployment_ref="deployment-1",
        request_body_digest="sha256:" + "7" * 64,
        capability_id=FAKE_CAPABILITY,
        capability_instance_ref="primary",
        binding_id=binding.id,
        config_digest="sha256:" + revision.config_digest,
        plan_hash=PLAN_HASH,
        steps=(),
        registry=registry,
    )
    assert prepared is not None

    with pytest.raises(ProvisioningRefused, match="plan evidence"):
        invoke_prepared_plan(
            prepared, registry=registry, resolve_secrets=lambda refs: {}
        )
    with pytest.raises(ProvisioningRefused, match="plan evidence"):
        settle_provisioning_plan(db, prepared=prepared, result=invalid)
    assert (
        db.scalar(
            select(ProvisioningCommandReceipt).where(
                ProvisioningCommandReceipt.command_id == "plan-invalid-output"
            )
        )
        is None
    )


def test_apply_retry_reuses_one_stable_provider_idempotency_key(db: Session) -> None:
    retrying = fake_plugin(
        provisioning_result=ProvisioningResult(
            status=ProvisionResultStatus.RETRYABLE,
            error_code="temporarily_unavailable",
        )
    )
    registry, plugin, _, revision, binding = _enabled(db, retrying)
    accepted = accept_provisioning_command(
        db,
        _command(db, revision=revision, binding=binding),
        registry=registry,
        now=NOW,
    )
    first = prepare_next_apply(
        db, operation_id=accepted.operation_id, registry=registry, now=NOW
    )
    assert first is not None
    settle_provisioning(
        db,
        prepared=first,
        result=invoke_prepared_provisioning(
            first, registry=registry, resolve_secrets=lambda refs: {}
        ),
        now=NOW,
    )
    second = prepare_next_apply(
        db,
        operation_id=accepted.operation_id,
        registry=registry,
        now=NOW + timedelta(minutes=10),
    )
    assert second is not None
    invoke_prepared_provisioning(
        second, registry=registry, resolve_secrets=lambda refs: {}
    )
    requests = [
        item
        for item in plugin.provisioning_seen
        if type(item).__name__ == "ProvisionApplyRequest"
    ]
    assert len(requests) == 2
    assert requests[0].idempotency_key == requests[1].idempotency_key


def test_observe_has_its_own_durable_phase_and_never_reapplies(db: Session) -> None:
    accepted_result = ProvisioningResult(
        status=ProvisionResultStatus.ACCEPTED,
        provider_operation_ref="provider-operation-1",
    )
    applying = fake_plugin(provisioning_result=accepted_result)
    registry, _, _, revision, binding = _enabled(db, applying)
    accepted = accept_provisioning_command(
        db,
        _command(
            db,
            revision=revision,
            binding=binding,
            steps=(
                ProvisionStep(
                    step_key="first",
                    endpoint_code=FAKE_CAPABILITY,
                    depends_on=(),
                    input={
                        "desired_ref": "resource-1",
                        "apply_only": "do-not-forward",
                    },
                ),
            ),
        ),
        registry=registry,
        now=NOW,
    )
    apply = prepare_next_apply(
        db, operation_id=accepted.operation_id, registry=registry, now=NOW
    )
    assert apply is not None
    result = invoke_prepared_provisioning(
        apply, registry=registry, resolve_secrets=lambda refs: {}
    )
    settle_provisioning(db, prepared=apply, result=result, now=NOW)

    observing_plugin = fake_plugin()
    observing_registry = fake_registry(plugins=[observing_plugin])
    expected = _expected_pin(db, accepted.operation_id)
    with pytest.raises(ProvisioningRefused, match="pins do not match"):
        prepare_next_observation(
            db,
            command_id="observe-wrong-instance",
            operation_id=accepted.operation_id,
            expected=replace(expected, capability_instance_ref="secondary"),
            registry=observing_registry,
            now=NOW,
        )
    with pytest.raises(ProvisioningRefused, match="pins do not match"):
        prepare_next_observation(
            db,
            command_id="observe-wrong-pin",
            operation_id=accepted.operation_id,
            expected=ExpectedProvisioningPin(
                step_key=expected.step_key,
                provider_operation_ref=expected.provider_operation_ref,
                deployment_ref=expected.deployment_ref,
                capability_instance_ref=expected.capability_instance_ref,
                plan_hash="sha256:" + "7" * 64,
                artifact_digest=expected.artifact_digest,
                config_digest=expected.config_digest,
                approval_digest=expected.approval_digest,
            ),
            registry=observing_registry,
            now=NOW,
        )
    assert observing_plugin.provisioning_seen == []
    observation = prepare_next_observation(
        db,
        command_id="observe-command-1",
        operation_id=accepted.operation_id,
        expected=expected,
        registry=observing_registry,
        now=NOW,
    )
    assert observation is not None
    assert not any(isinstance(value, Session) for value in _field_values(observation))
    observed = invoke_prepared_observation(
        observation,
        registry=observing_registry,
        resolve_secrets=lambda refs: {},
    )
    observe_request = observing_plugin.provisioning_seen[-1]
    assert observe_request.target == {"desired_ref": "resource-1"}
    operation = settle_observation(db, prepared=observation, result=observed, now=NOW)
    assert operation.state == "succeeded"
    assert [type(item).__name__ for item in observing_plugin.provisioning_seen] == [
        "ProvisionObserveRequest"
    ]
    assert (
        prepare_next_observation(
            db,
            command_id="observe-command-1",
            operation_id=operation.id,
            expected=_expected_pin(db, operation.id),
            registry=observing_registry,
            now=NOW,
        )
        is None
    )
    with pytest.raises(CommandIdentityCollision):
        prepare_cancellation(
            db,
            command_id="observe-command-1",
            operation_id=operation.id,
            expected=_expected_pin(db, operation.id),
            reason="customer_approved",
            registry=observing_registry,
            now=NOW,
        )


def test_cancel_is_durable_and_emits_an_immutable_projected_receipt(
    db: Session,
) -> None:
    applying = fake_plugin(
        provisioning_result=ProvisioningResult(
            status=ProvisionResultStatus.ACCEPTED,
            provider_operation_ref="provider-operation-1",
        )
    )
    registry, _, _, revision, binding = _enabled(db, applying)
    accepted = accept_provisioning_command(
        db,
        _command(
            db,
            revision=revision,
            binding=binding,
            steps=(
                ProvisionStep(
                    step_key="first",
                    endpoint_code=FAKE_CAPABILITY,
                    depends_on=(),
                    input={
                        "desired_ref": "resource-1",
                        "apply_only": "do-not-forward",
                    },
                ),
            ),
        ),
        registry=registry,
        now=NOW,
    )
    apply = prepare_next_apply(
        db, operation_id=accepted.operation_id, registry=registry, now=NOW
    )
    assert apply is not None
    settle_provisioning(
        db,
        prepared=apply,
        result=invoke_prepared_provisioning(
            apply, registry=registry, resolve_secrets=lambda refs: {}
        ),
        now=NOW,
    )

    cancelling_plugin = fake_plugin()
    cancelling_registry = fake_registry(plugins=[cancelling_plugin])
    expected = _expected_pin(db, accepted.operation_id)
    with pytest.raises(ProvisioningRefused, match="pins do not match"):
        prepare_cancellation(
            db,
            command_id="cancel-wrong-instance",
            operation_id=accepted.operation_id,
            expected=replace(expected, capability_instance_ref="secondary"),
            reason="customer_approved",
            registry=cancelling_registry,
            now=NOW,
        )
    cancellation = prepare_cancellation(
        db,
        command_id="cancel-command-1",
        operation_id=accepted.operation_id,
        expected=expected,
        reason="customer_approved",
        registry=cancelling_registry,
        now=NOW,
    )
    assert cancellation is not None
    assert not any(isinstance(value, Session) for value in _field_values(cancellation))
    cancelled = invoke_prepared_cancellation(
        cancellation,
        registry=cancelling_registry,
        resolve_secrets=lambda refs: {},
    )
    cancel_request = cancelling_plugin.provisioning_seen[-1]
    assert cancel_request.target == {"desired_ref": "resource-1"}
    operation = settle_cancellation(
        db, prepared=cancellation, result=cancelled, now=NOW
    )
    assert operation.state == "cancelled"
    receipts = read_provisioning_receipts(db, operation_id=operation.id)
    assert receipts[-1].receipt_kind == "step_cancelled"
    assert receipts[-1].previous_receipt_hash == receipts[-2].receipt_hash
    with pytest.raises(TypeError):
        receipts[-1].evidence["changed"] = True  # type: ignore[index]
    assert (
        prepare_cancellation(
            db,
            command_id="cancel-command-1",
            operation_id=operation.id,
            expected=_expected_pin(db, operation.id),
            reason="customer_approved",
            registry=cancelling_registry,
            now=NOW,
        )
        is None
    )
    with pytest.raises(CommandIdentityCollision):
        prepare_cancellation(
            db,
            command_id="cancel-command-1",
            operation_id=operation.id,
            expected=_expected_pin(db, operation.id),
            reason="operator_approved",
            registry=cancelling_registry,
            now=NOW,
        )


@pytest.mark.parametrize("action", ["observe", "cancel"])
def test_observe_and_cancel_validate_held_target_before_command_mutation(
    db: Session, action: str
) -> None:
    applying, declaration = _plugin_requiring_action_target(action)
    registry, _, _, revision, binding = _enabled(db, applying)
    accepted = accept_provisioning_command(
        db,
        _command(
            db,
            revision=revision,
            binding=binding,
            _declaration=declaration,
        ),
        registry=registry,
        now=NOW,
    )
    prepared = prepare_next_apply(
        db, operation_id=accepted.operation_id, registry=registry, now=NOW
    )
    assert prepared is not None
    settle_provisioning(
        db,
        prepared=prepared,
        result=invoke_prepared_provisioning(
            prepared, registry=registry, resolve_secrets=lambda refs: {}
        ),
        now=NOW,
    )
    operation = db.get(ProvisioningOperation, accepted.operation_id)
    assert operation is not None
    command_id = f"{action}-invalid-target"

    with pytest.raises(ProvisioningRefused, match=f"{action} target"):
        if action == "observe":
            prepare_next_observation(
                db,
                command_id=command_id,
                operation_id=operation.id,
                expected=_expected_pin(db, operation.id),
                registry=registry,
                now=NOW,
            )
        else:
            prepare_cancellation(
                db,
                command_id=command_id,
                operation_id=operation.id,
                expected=_expected_pin(db, operation.id),
                reason="customer_approved",
                registry=registry,
                now=NOW,
            )

    assert (
        db.scalar(
            select(ProvisioningCommandRecord).where(
                ProvisioningCommandRecord.command_id == command_id
            )
        )
        is None
    )


@pytest.mark.parametrize("action", ["observe", "cancel"])
def test_observe_and_cancel_validate_output_and_project_only_public_evidence(
    db: Session, action: str
) -> None:
    applying = fake_plugin(
        provisioning_result=ProvisioningResult(
            status=ProvisionResultStatus.ACCEPTED,
            provider_operation_ref="provider-operation-1",
        )
    )
    registry, _, _, revision, binding = _enabled(db, applying)
    accepted = accept_provisioning_command(
        db,
        _command(db, revision=revision, binding=binding),
        registry=registry,
        now=NOW,
    )
    apply = prepare_next_apply(
        db, operation_id=accepted.operation_id, registry=registry, now=NOW
    )
    assert apply is not None
    settle_provisioning(
        db,
        prepared=apply,
        result=invoke_prepared_provisioning(
            apply, registry=registry, resolve_secrets=lambda refs: {}
        ),
        now=NOW,
    )
    sentinel = "SENTINEL-OBSERVE-CANCEL-PRIVATE-7812"
    outcome = ProvisioningResult(
        status=(
            ProvisionResultStatus.SUCCEEDED
            if action == "observe"
            else ProvisionResultStatus.CANCELLED
        ),
        provider_operation_ref="provider-operation-1",
        evidence={"phase": "ready", "detail": sentinel},
        error_detail=sentinel,
    )
    plugin = fake_plugin(provisioning_result=outcome)
    action_registry = fake_registry(plugins=[plugin])
    command_id = f"{action}-public-output"
    if action == "observe":
        prepared_action = prepare_next_observation(
            db,
            command_id=command_id,
            operation_id=accepted.operation_id,
            expected=_expected_pin(db, accepted.operation_id),
            registry=action_registry,
            now=NOW,
        )
        assert prepared_action is not None
        result = outcome
        settled = settle_observation(
            db, prepared=prepared_action, result=result, now=NOW
        )
    else:
        prepared_action = prepare_cancellation(
            db,
            command_id=command_id,
            operation_id=accepted.operation_id,
            expected=_expected_pin(db, accepted.operation_id),
            reason="customer_approved",
            registry=action_registry,
            now=NOW,
        )
        assert prepared_action is not None
        result = outcome
        settled = settle_cancellation(
            db, prepared=prepared_action, result=result, now=NOW
        )
    receipt = read_provisioning_receipts(db, operation_id=accepted.operation_id)[-1]
    step = db.scalar(
        select(ProvisioningStep).where(
            ProvisioningStep.operation_id == accepted.operation_id,
            ProvisioningStep.step_key == "first",
        )
    )
    assert step is not None
    assert settled.error_detail is None
    assert step.error_detail is None
    assert receipt.evidence["error_detail"] is None
    assert receipt.evidence["public_evidence"] == {"phase": "ready"}
    assert receipt.evidence["provider_evidence_digest"] == (
        "sha256:" + payload_digest(dict(result.evidence))
    )
    assert sentinel not in repr(receipt)


@pytest.mark.parametrize("action", ["observe", "cancel"])
def test_observe_and_cancel_invalid_success_output_becomes_ambiguous(
    db: Session, action: str
) -> None:
    applying = fake_plugin(
        provisioning_result=ProvisioningResult(
            status=ProvisionResultStatus.ACCEPTED,
            provider_operation_ref="provider-operation-1",
        )
    )
    registry, _, _, revision, binding = _enabled(db, applying)
    accepted = accept_provisioning_command(
        db,
        _command(db, revision=revision, binding=binding),
        registry=registry,
        now=NOW,
    )
    apply = prepare_next_apply(
        db, operation_id=accepted.operation_id, registry=registry, now=NOW
    )
    assert apply is not None
    settle_provisioning(
        db,
        prepared=apply,
        result=invoke_prepared_provisioning(
            apply, registry=registry, resolve_secrets=lambda refs: {}
        ),
        now=NOW,
    )
    invalid = ProvisioningResult(
        status=(
            ProvisionResultStatus.SUCCEEDED
            if action == "observe"
            else ProvisionResultStatus.CANCELLED
        ),
        evidence={"phase": 7},
    )
    plugin = fake_plugin(provisioning_result=invalid)
    action_registry = fake_registry(plugins=[plugin])
    if action == "observe":
        prepared_action = prepare_next_observation(
            db,
            command_id="observe-invalid-output",
            operation_id=accepted.operation_id,
            expected=_expected_pin(db, accepted.operation_id),
            registry=action_registry,
            now=NOW,
        )
        assert prepared_action is not None
        result = invoke_prepared_observation(
            prepared_action,
            registry=action_registry,
            resolve_secrets=lambda refs: {},
        )
    else:
        prepared_action = prepare_cancellation(
            db,
            command_id="cancel-invalid-output",
            operation_id=accepted.operation_id,
            expected=_expected_pin(db, accepted.operation_id),
            reason="customer_approved",
            registry=action_registry,
            now=NOW,
        )
        assert prepared_action is not None
        result = invoke_prepared_cancellation(
            prepared_action,
            registry=action_registry,
            resolve_secrets=lambda refs: {},
        )

    assert result.status is ProvisionResultStatus.AMBIGUOUS
    assert result.error_code == "connector_contract"
    assert result.evidence == {}
    with pytest.raises(ProvisioningRefused, match=f"{action} evidence"):
        if action == "observe":
            settle_observation(
                db,
                prepared=prepared_action,
                result=invalid,
                now=NOW,
            )
        else:
            settle_cancellation(
                db,
                prepared=prepared_action,
                result=invalid,
                now=NOW,
            )
