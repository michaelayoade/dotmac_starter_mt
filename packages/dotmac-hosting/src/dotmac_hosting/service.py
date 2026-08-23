"""The one writer of Dotmac hosting-service lifecycle state."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Final
from uuid import UUID, uuid4

from dotmac_kernel.audit import write_audit_event
from dotmac_kernel.idempotency import execute_once, fingerprint_of
from dotmac_kernel.messaging.envelope import CommandEnvelope
from dotmac_kernel.messaging.inbox import process_once
from dotmac_kernel.messaging.outbox import enqueue_event
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from dotmac_hosting.contracts import (
    Actor,
    ApprovalObservationState,
    ChangeHostingPackage,
    ChangeHostingPackageV1,
    ChangeHostingSuspensionV1,
    ClearRetentionHold,
    ConsequenceDisposition,
    HostingCommandKind,
    HostingCommandReceipt,
    HostingConsequenceOutcome,
    HostingLifecycleState,
    HostingObservationReceipt,
    HostingObservationV1,
    HostingPackageChangeOutcome,
    HostingReconciliationResult,
    HostingRestorationOutcome,
    OutcomeClass,
    OutcomeKind,
    ProvisionHostingAccountV1,
    ProvisionHostingService,
    PublishHostingSpecificationVersion,
    RecordHostingOutcome,
    RequestTermination,
    ReconcileHostingAccountV1,
    RestoreSuspensionRequest,
    RetentionHoldOutcome,
    RetentionHoldRequest,
    SuspensionRequest,
    SuspensionAction,
    SpecificationPublicationReceipt,
    TerminateHostingAccountV1,
    TerminationApprovalEvidenceReceipt,
    TerminationApprovalObservationV1,
    fingerprint,
    termination_content_digest,
)
from dotmac_hosting.engine import (
    LifecycleInput,
    decide_lifecycle_transition,
    derive_hosting_drift,
)
from dotmac_hosting.models import (
    HostingAttentionCondition,
    HostingCommand,
    HostingCommandOutcome,
    HostingDesiredRevision,
    HostingObservation,
    HostingObservationResource,
    HostingRetentionHold,
    SCHEMA,
    HostingService,
    HostingSpecification,
    HostingSpecificationVersion,
    HostingSuspensionLock,
    HostingTerminationApprovalEvidence,
)
from dotmac_hosting.vocabulary import active_hosting_vocabulary

PROVISION_REQUESTED_EVENT: Final[str] = "hosting.account.provision.requested.v1"
PACKAGE_REQUESTED_EVENT: Final[str] = "hosting.account.package.requested.v1"
SUSPENSION_REQUESTED_EVENT: Final[str] = "hosting.account.suspension.requested.v1"
TERMINATION_REQUESTED_EVENT: Final[str] = "hosting.account.termination.requested.v1"
RECONCILE_REQUESTED_EVENT: Final[str] = "hosting.account.reconcile.requested.v1"
SPECIFICATION_PUBLISHED_EVENT: Final[str] = "hosting.specification.published.v1"
LIFECYCLE_CHANGED_EVENT: Final[str] = "hosting.lifecycle.changed.v1"
CONSEQUENCE_DECIDED_EVENT: Final[str] = "hosting.consequence.decided.v1"
ATTENTION_REQUIRED_EVENT: Final[str] = "hosting.attention.required.v1"

PUBLIC_EVENT_TYPES: Final[tuple[str, ...]] = (
    PROVISION_REQUESTED_EVENT,
    PACKAGE_REQUESTED_EVENT,
    SUSPENSION_REQUESTED_EVENT,
    TERMINATION_REQUESTED_EVENT,
    RECONCILE_REQUESTED_EVENT,
    SPECIFICATION_PUBLISHED_EVENT,
    LIFECYCLE_CHANGED_EVENT,
    CONSEQUENCE_DECIDED_EVENT,
    ATTENTION_REQUIRED_EVENT,
)

RETENTION_AUDIT_ACTION: Final[str] = "hosting.retention_hold.changed"
TERMINATION_AUDIT_ACTION: Final[str] = "hosting.termination.requested"
REPAIR_AUDIT_ACTION: Final[str] = "hosting.reconciliation.requested"
PACKAGE_AUDIT_ACTION: Final[str] = "hosting.package_change.decided"
SUSPENSION_AUDIT_ACTION: Final[str] = "hosting.suspension.decided"
TERMINATION_POLICY_CODE: Final[str] = "hosting.termination.v1"
TERMINATION_POLICY_VERSION: Final[int] = 1


class HostingError(ValueError):
    pass


class HostingNotFound(HostingError):
    pass


class HostingSpecificationNotFound(HostingError):
    pass


class HostingAlreadyExists(HostingError):
    pass


class InvalidHostingTransition(HostingError):
    pass


class StaleHostingVersion(HostingError):
    pass


class ApprovalRequired(HostingError):
    pass


class HostingCommandNotFound(HostingError):
    pass


def _aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HostingError(f"{name} must be timezone-aware")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _json_value(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, UUID | datetime):
        return str(value)
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple | list | set | frozenset):
        return [_json_value(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    return str(value)


def _payload(value: object) -> dict[str, object]:
    converted = _json_value(value)
    if not isinstance(converted, dict):
        raise HostingError("command payload must serialize to an object")
    return converted


def _service(
    db: Session,
    tenant_id: UUID,
    hosting_service_id: UUID,
    *,
    lock: bool = False,
) -> HostingService:
    statement = select(HostingService).where(
        HostingService.tenant_id == tenant_id,
        HostingService.id == hosting_service_id,
    )
    if lock:
        statement = statement.with_for_update()
    row = db.scalar(statement)
    if row is None:
        raise HostingNotFound(f"hosting service {hosting_service_id} was not found")
    return row


def _mutate_service(
    db: Session,
    *,
    service: HostingService,
    mutation_kind: str,
    updated_at: datetime,
    specification_code: str | None = None,
    specification_version: int | None = None,
    lifecycle_state: HostingLifecycleState | None = None,
    state_effective_at: datetime | None = None,
    observation_id: UUID | None = None,
) -> HostingService:
    """Use the module's sole database-authorized aggregate mutation seam."""

    expected_version = service.row_version
    next_version = db.scalar(
        text(
            f"SELECT {SCHEMA}.mutate_hosting_service("
            "CAST(:tenant_id AS uuid), CAST(:service_id AS uuid), "
            "CAST(:expected_version AS integer), CAST(:mutation_kind AS text), "
            "CAST(:updated_at AS timestamp with time zone), "
            "CAST(:specification_code AS text), "
            "CAST(:specification_version AS integer), "
            "CAST(:lifecycle_state AS text), "
            "CAST(:state_effective_at AS timestamp with time zone), "
            "CAST(:observation_id AS uuid))"
        ),
        {
            "tenant_id": service.tenant_id,
            "service_id": service.id,
            "expected_version": expected_version,
            "mutation_kind": mutation_kind,
            "updated_at": updated_at,
            "specification_code": specification_code,
            "specification_version": specification_version,
            "lifecycle_state": (
                lifecycle_state.value if lifecycle_state is not None else None
            ),
            "state_effective_at": state_effective_at,
            "observation_id": observation_id,
        },
    )
    if next_version != expected_version + 1:
        raise HostingError("hosting mutation returned an invalid aggregate version")
    db.refresh(service)
    if service.row_version != next_version:
        raise HostingError("hosting mutation refresh did not observe its committed version")
    return service


def _command(
    db: Session, tenant_id: UUID, hosting_command_id: UUID
) -> HostingCommand:
    row = db.scalar(
        select(HostingCommand).where(
            HostingCommand.tenant_id == tenant_id,
            HostingCommand.id == hosting_command_id,
        )
    )
    if row is None:
        raise HostingCommandNotFound(
            f"hosting command {hosting_command_id} was not found"
        )
    return row


def _specification_version(
    db: Session, tenant_id: UUID, code: str, version: int
) -> HostingSpecificationVersion:
    row = db.scalar(
        select(HostingSpecificationVersion).where(
            HostingSpecificationVersion.tenant_id == tenant_id,
            HostingSpecificationVersion.specification_code == code,
            HostingSpecificationVersion.version == version,
        )
    )
    if row is None:
        raise HostingSpecificationNotFound(
            f"hosting specification {code!r} version {version} was not found"
        )
    return row


def _new_command(
    db: Session,
    *,
    tenant_id: UUID,
    service: HostingService,
    kind: HostingCommandKind,
    scope: str,
    key: str,
    request_fingerprint: str,
    payload: dict[str, object],
    requested_at: datetime,
    correlation_id: str | None,
    command_id: UUID | None = None,
) -> HostingCommand:
    row = HostingCommand(
        id=command_id or uuid4(),
        tenant_id=tenant_id,
        hosting_service_id=service.id,
        command_kind=kind.value,
        idempotency_scope=scope,
        idempotency_key=key,
        request_fingerprint=request_fingerprint,
        correlation_id=correlation_id,
        payload=payload,
        requested_at=requested_at,
    )
    db.add(row)
    db.flush()
    return row


def _lock_specification_identity(
    db: Session, tenant_id: UUID, specification_code: str
) -> None:
    """Serialize both first creation and later publication for one identity."""

    db.scalar(
        select(
            func.pg_advisory_xact_lock(
                func.hashtextextended(
                    f"hosting-specification:{tenant_id}:{specification_code}", 0
                )
            )
        )
    )


def _append_outcome(
    db: Session,
    *,
    tenant_id: UUID,
    service: HostingService,
    command: HostingCommand,
    evidence_key: str,
    outcome_kind: OutcomeKind,
    outcome_class: OutcomeClass,
    occurred_at: datetime,
    reason_code: str | None = None,
    provider_reference: str | None = None,
    details: dict[str, object] | None = None,
) -> HostingCommandOutcome:
    row = HostingCommandOutcome(
        tenant_id=tenant_id,
        hosting_service_id=service.id,
        hosting_command_id=command.id,
        evidence_key=evidence_key,
        outcome_kind=outcome_kind.value,
        outcome_class=outcome_class.value,
        provider_reference=provider_reference,
        reason_code=reason_code,
        details=details or {},
        occurred_at=occurred_at,
    )
    db.add(row)
    db.flush()
    return row


def _append_desired(
    db: Session,
    *,
    tenant_id: UUID,
    service: HostingService,
    desired_account_state: str,
    specification: HostingSpecificationVersion,
    requested_at: datetime,
) -> HostingDesiredRevision:
    previous = db.scalar(
        select(func.max(HostingDesiredRevision.version)).where(
            HostingDesiredRevision.tenant_id == tenant_id,
            HostingDesiredRevision.hosting_service_id == service.id,
        )
    )
    version = int(previous or 0) + 1
    content = {
        "desired_account_state": desired_account_state,
        "specification_code": specification.specification_code,
        "specification_version": specification.version,
        "package_ref": specification.package_ref,
    }
    row = HostingDesiredRevision(
        tenant_id=tenant_id,
        hosting_service_id=service.id,
        version=version,
        desired_account_state=desired_account_state,
        specification_code=specification.specification_code,
        specification_version=specification.version,
        package_ref=specification.package_ref,
        content_digest=fingerprint(content),
        requested_at=requested_at,
    )
    db.add(row)
    db.flush()
    return row


def _latest_desired(
    db: Session, tenant_id: UUID, hosting_service_id: UUID
) -> HostingDesiredRevision:
    row = db.scalar(
        select(HostingDesiredRevision)
        .where(
            HostingDesiredRevision.tenant_id == tenant_id,
            HostingDesiredRevision.hosting_service_id == hosting_service_id,
        )
        .order_by(HostingDesiredRevision.version.desc())
        .limit(1)
    )
    if row is None:
        raise HostingError("hosting service has no desired-state revision")
    return row


def _latest_observation(
    db: Session, tenant_id: UUID, hosting_service_id: UUID
) -> HostingObservation | None:
    service = _service(db, tenant_id, hosting_service_id)
    operation_references = tuple(
        str(command_id)
        for command_id in db.scalars(
            select(HostingCommand.id).where(
                HostingCommand.tenant_id == tenant_id,
                HostingCommand.hosting_service_id == hosting_service_id,
            )
        )
    )
    correlations = [HostingObservation.hosting_service_id == hosting_service_id]
    if service.provider_account_ref is None and operation_references:
        correlations.append(
            HostingObservation.operation_reference.in_(operation_references)
        )
    if (
        service.capability_binding_ref is not None
        and service.provider_account_ref is not None
    ):
        correlations.append(
            (
                HostingObservation.capability_binding_ref
                == service.capability_binding_ref
            )
            & (
                HostingObservation.provider_account_ref
                == service.provider_account_ref
            )
        )
    return db.scalar(
        select(HostingObservation)
        .where(
            HostingObservation.tenant_id == tenant_id,
            or_(*correlations),
        )
        .order_by(
            HostingObservation.observed_at.desc(),
            HostingObservation.received_at.desc(),
        )
        .limit(1)
    )


def _active_holds(
    db: Session, tenant_id: UUID, hosting_service_id: UUID
) -> list[HostingRetentionHold]:
    return list(
        db.scalars(
            select(HostingRetentionHold).where(
                HostingRetentionHold.tenant_id == tenant_id,
                HostingRetentionHold.hosting_service_id == hosting_service_id,
                HostingRetentionHold.cleared_at.is_(None),
            )
        )
    )


def _active_locks(
    db: Session, tenant_id: UUID, hosting_service_id: UUID
) -> list[HostingSuspensionLock]:
    return list(
        db.scalars(
            select(HostingSuspensionLock).where(
                HostingSuspensionLock.tenant_id == tenant_id,
                HostingSuspensionLock.hosting_service_id == hosting_service_id,
                HostingSuspensionLock.cleared_at.is_(None),
            )
        )
    )


def _emit(
    db: Session,
    *,
    tenant_id: UUID,
    event_type: str,
    payload: dict[str, object],
    correlation_id: str | None = None,
) -> None:
    enqueue_event(
        db,
        tenant_id=tenant_id,
        event_type=event_type,
        payload=payload,
        correlation_id=correlation_id,
    )


def _write_hosting_audit(
    db: Session,
    *,
    tenant_id: UUID,
    actor: Actor,
    action: str,
    entity_id: UUID,
    occurred_at: datetime,
    details: dict[str, object],
) -> None:
    if actor.actor_type == "user":
        write_audit_event(
            db,
            tenant_id=tenant_id,
            actor_type="user",
            actor_id=str(actor.actor_party_id),
            actor_label=actor.actor_label,
            actor_party_id=actor.actor_party_id,
            action=action,
            entity_type="hosting_service",
            entity_id=str(entity_id),
            occurred_at=occurred_at,
            details=details,
        )
    elif actor.actor_type == "api_key":
        write_audit_event(
            db,
            tenant_id=tenant_id,
            actor_type="api_key",
            actor_id=actor.actor_id,
            actor_label=actor.actor_label,
            actor_party_id=actor.actor_party_id,
            action=action,
            entity_type="hosting_service",
            entity_id=str(entity_id),
            occurred_at=occurred_at,
            details=details,
        )
    elif actor.actor_type == "service":
        write_audit_event(
            db,
            tenant_id=tenant_id,
            actor_type="service",
            actor_id=actor.actor_id,
            actor_label=actor.actor_label,
            action=action,
            entity_type="hosting_service",
            entity_id=str(entity_id),
            occurred_at=occurred_at,
            details=details,
        )
    else:
        write_audit_event(
            db,
            tenant_id=tenant_id,
            actor_type="system",
            actor_id=actor.actor_id,
            actor_label=actor.actor_label,
            action=action,
            entity_type="hosting_service",
            entity_id=str(entity_id),
            occurred_at=occurred_at,
            details=details,
        )


def _command_receipt(
    db: Session, *, tenant_id: UUID, result: dict[str, object], replayed: bool
) -> HostingCommandReceipt:
    command = _command(db, tenant_id, UUID(str(result["command_id"])))
    return HostingCommandReceipt(
        hosting_service_id=UUID(str(result["hosting_service_id"])),
        command_id=command.id,
        command_kind=HostingCommandKind(command.command_kind),
        lifecycle_state=HostingLifecycleState(str(result["lifecycle_state"])),
        replayed=replayed,
    )


def publish_specification_version(
    db: Session,
    *,
    tenant_id: UUID,
    command: PublishHostingSpecificationVersion,
    idempotency_key: str,
    idempotency_expires_at: datetime,
    correlation_id: str | None = None,
) -> SpecificationPublicationReceipt:
    _aware("idempotency_expires_at", idempotency_expires_at)
    request_fingerprint = fingerprint_of(command)

    def operation(session: Session) -> dict[str, object]:
        _lock_specification_identity(
            session, tenant_id, command.specification_code
        )
        specification = session.scalar(
            select(HostingSpecification).where(
                HostingSpecification.tenant_id == tenant_id,
                HostingSpecification.specification_code == command.specification_code,
            ).with_for_update()
        )
        if specification is None:
            specification = HostingSpecification(
                tenant_id=tenant_id,
                specification_code=command.specification_code,
                created_at=command.published_at,
            )
            session.add(specification)
            session.flush()
        previous = session.scalar(
            select(HostingSpecificationVersion)
            .where(
                HostingSpecificationVersion.tenant_id == tenant_id,
                HostingSpecificationVersion.specification_code
                == command.specification_code,
            )
            .order_by(HostingSpecificationVersion.version.desc())
            .limit(1)
        )
        assigned_version = 1 if previous is None else previous.version + 1
        allowances = [
            {
                "resource_kind": allowance.resource_kind,
                "quantity": str(allowance.quantity),
                "unit": allowance.unit,
            }
            for allowance in command.allowances
        ]
        change_rules = asdict(command.change_rules)
        content = {
            "package_ref": command.package_ref,
            "package_rank": command.package_rank,
            "allowances": allowances,
            "included_artifacts": command.included_artifacts,
            "capability_codes": command.capability_codes,
            "change_rules": change_rules,
            "previous_version": previous.version if previous else None,
            "previous_content_digest": previous.content_digest if previous else None,
        }
        row = HostingSpecificationVersion(
            tenant_id=tenant_id,
            specification_id=specification.id,
            specification_code=command.specification_code,
            version=assigned_version,
            package_ref=command.package_ref,
            package_rank=command.package_rank,
            allowances=allowances,
            included_artifacts=list(command.included_artifacts),
            capability_codes=list(command.capability_codes),
            change_rules=change_rules,
            content_digest=fingerprint(content),
            previous_version=previous.version if previous else None,
            previous_content_digest=previous.content_digest if previous else None,
            published_at=command.published_at,
        )
        session.add(row)
        session.flush()
        _emit(
            session,
            tenant_id=tenant_id,
            event_type=SPECIFICATION_PUBLISHED_EVENT,
            correlation_id=correlation_id,
            payload={
                "specification_code": row.specification_code,
                "version": row.version,
                "content_digest": row.content_digest,
                "previous_version": row.previous_version,
                "previous_content_digest": row.previous_content_digest,
            },
        )
        return {
            "specification_version_id": str(row.id),
            "specification_code": row.specification_code,
            "assigned_version": row.version,
            "previous_version": row.previous_version,
            "content_digest": row.content_digest,
            "previous_content_digest": row.previous_content_digest,
        }

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="hosting.specification.publish",
        key=idempotency_key,
        fingerprint=request_fingerprint,
        operation=operation,
        operation_name="hosting.publish_specification_version",
        correlation_id=correlation_id,
        expires_at=idempotency_expires_at,
    )
    return SpecificationPublicationReceipt(
        specification_version_id=UUID(str(outcome.result["specification_version_id"])),
        specification_code=str(outcome.result["specification_code"]),
        assigned_version=int(outcome.result["assigned_version"]),
        previous_version=(
            int(outcome.result["previous_version"])
            if outcome.result.get("previous_version") is not None
            else None
        ),
        content_digest=str(outcome.result["content_digest"]),
        previous_content_digest=(
            str(outcome.result["previous_content_digest"])
            if outcome.result.get("previous_content_digest") is not None
            else None
        ),
        replayed=outcome.replayed,
    )


def request_provisioning(
    db: Session,
    *,
    tenant_id: UUID,
    command: ProvisionHostingService,
    idempotency_key: str,
    idempotency_expires_at: datetime,
    correlation_id: str | None = None,
) -> HostingCommandReceipt:
    _aware("idempotency_expires_at", idempotency_expires_at)
    request_fingerprint = fingerprint_of(command)

    def operation(session: Session) -> dict[str, object]:
        existing = session.scalar(
            select(HostingService).where(
                HostingService.tenant_id == tenant_id,
                HostingService.order_line_ref == command.order_line_ref,
            )
        )
        if existing is not None:
            raise HostingAlreadyExists(
                f"hosting service for {command.order_line_ref!r} already exists"
            )
        specification = _specification_version(
            session,
            tenant_id,
            command.specification_code,
            command.specification_version,
        )
        service = HostingService(
            tenant_id=tenant_id,
            customer_ref=command.customer_ref,
            order_line_ref=command.order_line_ref,
            offer_version_ref=command.offer_version_ref,
            specification_code=command.specification_code,
            specification_version=command.specification_version,
            primary_domain=command.primary_domain,
            account_label=command.account_identity.account_label,
            administrative_email=command.account_identity.administrative_email,
            country_code=command.account_identity.country_code,
            lifecycle_state=HostingLifecycleState.PROVISIONING.value,
            state_effective_at=command.requested_at,
            row_version=0,
            created_at=command.requested_at,
            updated_at=command.requested_at,
        )
        session.add(service)
        session.flush()
        _append_desired(
            session,
            tenant_id=tenant_id,
            service=service,
            desired_account_state="active",
            specification=specification,
            requested_at=command.requested_at,
        )
        command_id = uuid4()
        provider_request = ProvisionHostingAccountV1(
            operation_reference=str(command_id),
            package_ref=specification.package_ref,
            primary_domain=service.primary_domain,
            account_identity=command.account_identity,
        )
        delivery_payload = _payload(provider_request)
        row = _new_command(
            session,
            tenant_id=tenant_id,
            service=service,
            kind=HostingCommandKind.PROVISION,
            scope="hosting.provision",
            key=idempotency_key,
            request_fingerprint=request_fingerprint,
            payload=delivery_payload,
            requested_at=command.requested_at,
            correlation_id=correlation_id,
            command_id=command_id,
        )
        _emit(
            session,
            tenant_id=tenant_id,
            event_type=PROVISION_REQUESTED_EVENT,
            correlation_id=correlation_id,
            payload=delivery_payload,
        )
        return {
            "hosting_service_id": str(service.id),
            "command_id": str(row.id),
            "lifecycle_state": service.lifecycle_state,
        }

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="hosting.provision",
        key=idempotency_key,
        fingerprint=request_fingerprint,
        operation=operation,
        operation_name="hosting.request_provisioning",
        correlation_id=correlation_id,
        expires_at=idempotency_expires_at,
    )
    return _command_receipt(
        db, tenant_id=tenant_id, result=dict(outcome.result), replayed=outcome.replayed
    )


def request_package_change(
    db: Session,
    *,
    tenant_id: UUID,
    command: ChangeHostingPackage,
    actor: Actor,
    idempotency_key: str,
    idempotency_expires_at: datetime,
    correlation_id: str | None = None,
) -> HostingPackageChangeOutcome:
    _aware("idempotency_expires_at", idempotency_expires_at)
    request_fingerprint = fingerprint(
        {"command": _payload(command), "actor": _payload(actor)}
    )

    def operation(session: Session) -> dict[str, object]:
        service = _service(
            session, tenant_id, command.hosting_service_id, lock=True
        )
        current = _specification_version(
            session,
            tenant_id,
            service.specification_code,
            service.specification_version,
        )
        target = _specification_version(
            session, tenant_id, command.specification_code, command.specification_version
        )
        command_id = uuid4()
        rules = dict(current.change_rules)
        if target.package_rank > current.package_rank:
            direction = "upgrade"
            permitted = rules.get("upgrade_allowed") is True
            reason = "upgrade_allowed" if permitted else "upgrade_refused"
        elif target.package_rank < current.package_rank:
            direction = "downgrade"
            permitted = rules.get("downgrade_allowed") is True
            reason = "downgrade_allowed" if permitted else "downgrade_refused"
            if permitted and rules.get("downgrade_requires_review") is True:
                permitted = False
                reason = "manual_required"
        elif target.specification_code == current.specification_code:
            direction = "same_level"
            permitted = (
                target.version != current.version
                and rules.get("same_level_allowed") is True
            )
            reason = (
                "same_level_change_allowed"
                if permitted
                else "target_already_current"
                if target.version == current.version
                else "same_level_change_refused"
            )
        else:
            direction = "incomparable"
            permitted = False
            reason = "manual_required"
        if service.lifecycle_state not in {
            HostingLifecycleState.ACTIVE.value,
            HostingLifecycleState.SUSPENDED.value,
        }:
            permitted = False
            reason = "service_transition_in_progress"
        if service.provider_account_ref is None:
            permitted = False
            reason = "provider_account_unconfirmed"
        provider_request = (
            ChangeHostingPackageV1(
                operation_reference=str(command_id),
                account_ref=service.provider_account_ref,
                target_package_ref=target.package_ref,
            )
            if permitted and service.provider_account_ref is not None
            else None
        )
        delivery_payload = (
            _payload(provider_request) if provider_request is not None else _payload(command)
        )
        row = _new_command(
            session,
            tenant_id=tenant_id,
            service=service,
            kind=HostingCommandKind.PACKAGE,
            scope="hosting.package",
            key=idempotency_key,
            request_fingerprint=request_fingerprint,
            payload=delivery_payload,
            requested_at=command.requested_at,
            correlation_id=correlation_id,
            command_id=command_id,
        )
        disposition = (
            ConsequenceDisposition.DEFERRED
            if provider_request is not None
            else ConsequenceDisposition.REFUSED
        )
        if provider_request is not None:
            _append_desired(
                session,
                tenant_id=tenant_id,
                service=service,
                desired_account_state=(
                    "suspended"
                    if service.lifecycle_state == HostingLifecycleState.SUSPENDED.value
                    else "active"
                ),
                specification=target,
                requested_at=command.requested_at,
            )
            _mutate_service(
                session,
                service=service,
                mutation_kind="specification_change",
                updated_at=command.requested_at,
                specification_code=target.specification_code,
                specification_version=target.version,
            )
            _emit(
                session,
                tenant_id=tenant_id,
                event_type=PACKAGE_REQUESTED_EVENT,
                correlation_id=correlation_id,
                payload=delivery_payload,
            )
        evidence = _append_outcome(
            session,
            tenant_id=tenant_id,
            service=service,
            command=row,
            evidence_key="business-decision",
            outcome_kind=OutcomeKind(disposition.value),
            outcome_class=(
                OutcomeClass.RECONCILIATION_REQUIRED
                if disposition is ConsequenceDisposition.DEFERRED
                else OutcomeClass.TERMINAL
            ),
            reason_code=reason,
            details={"direction": direction},
            occurred_at=command.requested_at,
        )
        _emit(
            session,
            tenant_id=tenant_id,
            event_type=CONSEQUENCE_DECIDED_EVENT,
            correlation_id=correlation_id,
            payload={
                "hosting_service_id": str(service.id),
                "command_id": str(row.id),
                "outcome_id": str(evidence.id),
                "disposition": disposition.value,
                "reason_code": reason,
            },
        )
        _write_hosting_audit(
            session,
            tenant_id=tenant_id,
            actor=actor,
            action=PACKAGE_AUDIT_ACTION,
            entity_id=service.id,
            occurred_at=command.requested_at,
            details={
                "direction": direction,
                "disposition": disposition.value,
                "reason_code": reason,
            },
        )
        return {
            "hosting_service_id": str(service.id),
            "command_id": str(row.id),
            "outcome_id": str(evidence.id),
            "disposition": disposition.value,
            "direction": direction,
            "lifecycle_state": service.lifecycle_state,
            "reason_code": reason,
        }

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="hosting.package",
        key=idempotency_key,
        fingerprint=request_fingerprint,
        operation=operation,
        operation_name="hosting.request_package_change",
        correlation_id=correlation_id,
        expires_at=idempotency_expires_at,
    )
    return HostingPackageChangeOutcome(
        hosting_service_id=UUID(str(outcome.result["hosting_service_id"])),
        command_id=UUID(str(outcome.result["command_id"])),
        outcome_id=UUID(str(outcome.result["outcome_id"])),
        disposition=ConsequenceDisposition(str(outcome.result["disposition"])),
        direction=str(outcome.result["direction"]),
        lifecycle_state=HostingLifecycleState(str(outcome.result["lifecycle_state"])),
        reason_code=str(outcome.result["reason_code"]),
        replayed=outcome.replayed,
    )


def apply_suspension_request(
    db: Session,
    *,
    tenant_id: UUID,
    command: SuspensionRequest,
    actor: Actor,
    idempotency_key: str,
    idempotency_expires_at: datetime,
    correlation_id: str | None = None,
) -> HostingConsequenceOutcome:
    _aware("idempotency_expires_at", idempotency_expires_at)
    request_fingerprint = fingerprint(
        {"command": _payload(command), "actor": _payload(actor)}
    )

    def operation(session: Session) -> dict[str, object]:
        service = _service(
            session, tenant_id, command.hosting_service_id, lock=True
        )
        previous_lifecycle = HostingLifecycleState(service.lifecycle_state)
        if service.lifecycle_state in {
            HostingLifecycleState.PROVISIONING.value,
            HostingLifecycleState.TERMINATING.value,
            HostingLifecycleState.TERMINATED.value,
        }:
            reason = "service_not_suspendable"
        elif command.reason_code == "delinquency" and _active_holds(
            session, tenant_id, service.id
        ):
            reason = "retention_hold_active"
        elif any(lock.reason_code == command.reason_code for lock in _active_locks(session, tenant_id, service.id)):
            reason = "reason_already_active"
        else:
            reason = "suspension_accepted"
        accepted = reason == "suspension_accepted"
        command_id = uuid4()
        provider_request: ChangeHostingSuspensionV1 | None = None
        disposition = ConsequenceDisposition.REFUSED
        if accepted:
            active_before = _active_locks(session, tenant_id, service.id)
            lock = HostingSuspensionLock(
                tenant_id=tenant_id,
                hosting_service_id=service.id,
                reason_code=command.reason_code,
                source_owner=command.source_owner,
                source_reference=command.source_reference,
                allowed_restorer_codes=sorted(
                    active_hosting_vocabulary().suspension_restorers[
                        command.reason_code
                    ]
                ),
                opened_at=command.requested_at,
            )
            session.add(lock)
            specification = _specification_version(
                session,
                tenant_id,
                service.specification_code,
                service.specification_version,
            )
            _append_desired(
                session,
                tenant_id=tenant_id,
                service=service,
                desired_account_state="suspended",
                specification=specification,
                requested_at=command.requested_at,
            )
            needs_delivery = not active_before and service.lifecycle_state in {
                HostingLifecycleState.ACTIVE.value,
                HostingLifecycleState.RESTORATION_REQUESTED.value,
            }
            if needs_delivery:
                if service.provider_account_ref is None:
                    raise InvalidHostingTransition(
                        "suspension requires an observed account reference"
                    )
                provider_request = ChangeHostingSuspensionV1(
                    operation_reference=str(command_id),
                    account_ref=service.provider_account_ref,
                    action=SuspensionAction.SUSPEND,
                    reason_ref=command.reason_code,
                )
                _mutate_service(
                    session,
                    service=service,
                    mutation_kind="lifecycle_request",
                    updated_at=command.requested_at,
                    lifecycle_state=HostingLifecycleState.SUSPENSION_REQUESTED,
                    state_effective_at=command.requested_at,
                )
                disposition = ConsequenceDisposition.DEFERRED
                reason = "suspension_requested"
            elif service.lifecycle_state == HostingLifecycleState.SUSPENDED.value:
                disposition = ConsequenceDisposition.APPLIED
                reason = "already_suspended_lock_added"
            else:
                disposition = ConsequenceDisposition.DEFERRED
                reason = "suspension_already_requested"
        command_payload = (
            _payload(provider_request) if provider_request is not None else _payload(command)
        )
        row = _new_command(
            session,
            tenant_id=tenant_id,
            service=service,
            kind=HostingCommandKind.SUSPENSION,
            scope="hosting.suspension",
            key=idempotency_key,
            request_fingerprint=request_fingerprint,
            payload=command_payload,
            requested_at=command.requested_at,
            correlation_id=correlation_id,
            command_id=command_id,
        )
        if provider_request is not None:
            if previous_lifecycle is HostingLifecycleState.RESTORATION_REQUESTED:
                _supersede_deferred_consequences(
                    session,
                    tenant_id=tenant_id,
                    service=service,
                    command_scope="hosting.restoration",
                    superseded_by_command_id=row.id,
                    occurred_at=command.requested_at,
                )
            _emit(
                session,
                tenant_id=tenant_id,
                event_type=SUSPENSION_REQUESTED_EVENT,
                correlation_id=correlation_id,
                payload=_payload(provider_request),
            )
        outcome = _append_outcome(
            session,
            tenant_id=tenant_id,
            service=service,
            command=row,
            evidence_key="business-decision",
            outcome_kind=OutcomeKind(disposition.value),
            outcome_class=(
                OutcomeClass.RECONCILIATION_REQUIRED
                if disposition is ConsequenceDisposition.DEFERRED
                else OutcomeClass.SUCCEEDED
                if disposition is ConsequenceDisposition.APPLIED
                else OutcomeClass.TERMINAL
            ),
            occurred_at=command.requested_at,
            reason_code=reason,
            details={"source_owner": command.source_owner, "source_reference": command.source_reference},
        )
        _emit(
            session,
            tenant_id=tenant_id,
            event_type=CONSEQUENCE_DECIDED_EVENT,
            correlation_id=correlation_id,
            payload={
                "hosting_service_id": str(service.id),
                "command_id": str(row.id),
                "outcome_id": str(outcome.id),
                "disposition": disposition.value,
                "reason_code": reason,
            },
        )
        _write_hosting_audit(
            session,
            tenant_id=tenant_id,
            actor=actor,
            action=SUSPENSION_AUDIT_ACTION,
            entity_id=service.id,
            occurred_at=command.requested_at,
            details={
                "transition": "suspension",
                "disposition": disposition.value,
                "reason_code": reason,
            },
        )
        return {
            "hosting_service_id": str(service.id),
            "command_id": str(row.id),
            "outcome_id": str(outcome.id),
            "disposition": disposition.value,
            "reason_code": reason,
            "lifecycle_state": service.lifecycle_state,
        }

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="hosting.suspension",
        key=idempotency_key,
        fingerprint=request_fingerprint,
        operation=operation,
        operation_name="hosting.apply_suspension_request",
        correlation_id=correlation_id,
        expires_at=idempotency_expires_at,
    )
    return HostingConsequenceOutcome(
        hosting_service_id=UUID(str(outcome.result["hosting_service_id"])),
        command_id=UUID(str(outcome.result["command_id"])),
        outcome_id=UUID(str(outcome.result["outcome_id"])),
        disposition=ConsequenceDisposition(str(outcome.result["disposition"])),
        lifecycle_state=HostingLifecycleState(str(outcome.result["lifecycle_state"])),
        reason_code=str(outcome.result["reason_code"]),
        replayed=outcome.replayed,
    )


def restore_suspension(
    db: Session,
    *,
    tenant_id: UUID,
    command: RestoreSuspensionRequest,
    actor: Actor,
    idempotency_key: str,
    idempotency_expires_at: datetime,
    correlation_id: str | None = None,
) -> HostingRestorationOutcome:
    _aware("idempotency_expires_at", idempotency_expires_at)
    request_fingerprint = fingerprint(
        {"command": _payload(command), "actor": _payload(actor)}
    )

    def operation(session: Session) -> dict[str, object]:
        service = _service(
            session, tenant_id, command.hosting_service_id, lock=True
        )
        previous_lifecycle = HostingLifecycleState(service.lifecycle_state)
        command_id = uuid4()
        active = _active_locks(session, tenant_id, service.id)
        target = next(
            (lock for lock in active if lock.reason_code == command.reason_code),
            None,
        )
        if target is None:
            remaining = tuple(sorted(lock.reason_code for lock in active))
            reason = "reason_not_active"
            disposition = ConsequenceDisposition.REFUSED
        elif command.restorer_code not in target.allowed_restorer_codes:
            remaining = tuple(sorted(lock.reason_code for lock in active))
            reason = "restorer_not_permitted"
            disposition = ConsequenceDisposition.REFUSED
        else:
            target.cleared_at = command.requested_at
            target.cleared_by = command.restorer_code
            session.flush()
            remaining = tuple(
                sorted(
                    lock.reason_code
                    for lock in _active_locks(session, tenant_id, service.id)
                )
            )
            disposition = ConsequenceDisposition.APPLIED
            reason = "lock_cleared" if not remaining else "remaining_blockers"
        provider_request: ChangeHostingSuspensionV1 | None = None
        if (
            target is not None
            and disposition is not ConsequenceDisposition.REFUSED
            and not remaining
            and service.lifecycle_state in {
            HostingLifecycleState.SUSPENDED.value,
            HostingLifecycleState.SUSPENSION_REQUESTED.value,
            }
        ):
            if service.provider_account_ref is None:
                raise InvalidHostingTransition(
                    "restoration requires an observed account reference"
                )
            provider_request = ChangeHostingSuspensionV1(
                operation_reference=str(command_id),
                account_ref=service.provider_account_ref,
                action=SuspensionAction.RESTORE,
                reason_ref=command.reason_code,
            )
            disposition = ConsequenceDisposition.DEFERRED
            reason = "restoration_requested"
        command_payload = (
            _payload(provider_request) if provider_request is not None else _payload(command)
        )
        row = _new_command(
            session,
            tenant_id=tenant_id,
            service=service,
            kind=HostingCommandKind.SUSPENSION,
            scope="hosting.restoration",
            key=idempotency_key,
            request_fingerprint=request_fingerprint,
            payload=command_payload,
            requested_at=command.requested_at,
            correlation_id=correlation_id,
            command_id=command_id,
        )
        if provider_request is not None:
            if previous_lifecycle is HostingLifecycleState.SUSPENSION_REQUESTED:
                _supersede_deferred_consequences(
                    session,
                    tenant_id=tenant_id,
                    service=service,
                    command_scope="hosting.suspension",
                    superseded_by_command_id=row.id,
                    occurred_at=command.requested_at,
                )
            specification = _specification_version(
                session,
                tenant_id,
                service.specification_code,
                service.specification_version,
            )
            _append_desired(
                session,
                tenant_id=tenant_id,
                service=service,
                desired_account_state="active",
                specification=specification,
                requested_at=command.requested_at,
            )
            _mutate_service(
                session,
                service=service,
                mutation_kind="lifecycle_request",
                updated_at=command.requested_at,
                lifecycle_state=HostingLifecycleState.RESTORATION_REQUESTED,
                state_effective_at=command.requested_at,
            )
            _emit(
                session,
                tenant_id=tenant_id,
                event_type=SUSPENSION_REQUESTED_EVENT,
                correlation_id=correlation_id,
                payload=_payload(provider_request),
            )
        evidence = _append_outcome(
            session,
            tenant_id=tenant_id,
            service=service,
            command=row,
            evidence_key="business-decision",
            outcome_kind=OutcomeKind(disposition.value),
            outcome_class=(
                OutcomeClass.RECONCILIATION_REQUIRED
                if disposition is ConsequenceDisposition.DEFERRED
                else OutcomeClass.SUCCEEDED
                if disposition is ConsequenceDisposition.APPLIED
                else OutcomeClass.TERMINAL
            ),
            occurred_at=command.requested_at,
            reason_code=reason,
            details={"remaining_blockers": remaining},
        )
        _emit(
            session,
            tenant_id=tenant_id,
            event_type=CONSEQUENCE_DECIDED_EVENT,
            correlation_id=correlation_id,
            payload={
                "hosting_service_id": str(service.id),
                "command_id": str(row.id),
                "outcome_id": str(evidence.id),
                "disposition": disposition.value,
                "reason_code": reason,
            },
        )
        _write_hosting_audit(
            session,
            tenant_id=tenant_id,
            actor=actor,
            action=SUSPENSION_AUDIT_ACTION,
            entity_id=service.id,
            occurred_at=command.requested_at,
            details={
                "transition": "restoration",
                "disposition": disposition.value,
                "reason_code": reason,
            },
        )
        return {
            "command_id": str(row.id),
            "outcome_id": str(evidence.id),
            "disposition": disposition.value,
            "reason_code": reason,
            "remaining_blockers": list(remaining),
            "hosting_service_id": str(service.id),
            "lifecycle_state": service.lifecycle_state,
        }

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="hosting.restoration",
        key=idempotency_key,
        fingerprint=request_fingerprint,
        operation=operation,
        operation_name="hosting.restore_suspension",
        correlation_id=correlation_id,
        expires_at=idempotency_expires_at,
    )
    raw_blockers = outcome.result["remaining_blockers"]
    if not isinstance(raw_blockers, list):
        raise HostingError("stored restoration blockers are malformed")
    return HostingRestorationOutcome(
        hosting_service_id=UUID(str(outcome.result["hosting_service_id"])),
        command_id=UUID(str(outcome.result["command_id"])),
        outcome_id=UUID(str(outcome.result["outcome_id"])),
        disposition=ConsequenceDisposition(str(outcome.result["disposition"])),
        lifecycle_state=HostingLifecycleState(str(outcome.result["lifecycle_state"])),
        remaining_blockers=tuple(str(item) for item in raw_blockers),
        reason_code=str(outcome.result["reason_code"]),
        replayed=outcome.replayed,
    )


def place_retention_hold(
    db: Session,
    *,
    tenant_id: UUID,
    command: RetentionHoldRequest,
    actor: Actor,
    idempotency_key: str,
    idempotency_expires_at: datetime,
    correlation_id: str | None = None,
) -> RetentionHoldOutcome:
    _aware("idempotency_expires_at", idempotency_expires_at)
    request_fingerprint = fingerprint(
        {"command": _payload(command), "actor": _payload(actor)}
    )

    def operation(session: Session) -> dict[str, object]:
        service = _service(
            session, tenant_id, command.hosting_service_id, lock=True
        )
        command_row = _new_command(
            session,
            tenant_id=tenant_id,
            service=service,
            kind=HostingCommandKind.RETENTION_HOLD,
            scope="hosting.retention_hold.place",
            key=idempotency_key,
            request_fingerprint=request_fingerprint,
            payload=_payload(command),
            requested_at=command.requested_at,
            correlation_id=correlation_id,
        )
        if service.lifecycle_state in {
            HostingLifecycleState.TERMINATING.value,
            HostingLifecycleState.TERMINATED.value,
        }:
            evidence = _append_outcome(
                session,
                tenant_id=tenant_id,
                service=service,
                command=command_row,
                evidence_key="business-decision",
                outcome_kind=OutcomeKind.REFUSED,
                outcome_class=OutcomeClass.TERMINAL,
                reason_code="termination_in_flight_manual_required",
                details={"hold_code": command.hold_code},
                occurred_at=command.requested_at,
            )
            _open_attention(
                session,
                tenant_id=tenant_id,
                service=service,
                condition_code="retention_hold_after_termination",
                classification="urgent_manual_required",
                reason_code="termination_in_flight_manual_required",
                details={
                    "hold_code": command.hold_code,
                    "command_id": str(command_row.id),
                },
                opened_at=command.requested_at,
                source_command_id=command_row.id,
            )
            _write_hosting_audit(
                session,
                tenant_id=tenant_id,
                actor=actor,
                action=RETENTION_AUDIT_ACTION,
                entity_id=service.id,
                occurred_at=command.requested_at,
                details={
                    "hold_code": command.hold_code,
                    "transition": "refused",
                    "reason_code": "termination_in_flight_manual_required",
                },
            )
            return {
                "retention_hold_id": None,
                "command_id": str(command_row.id),
                "outcome_id": str(evidence.id),
                "disposition": ConsequenceDisposition.REFUSED.value,
                "reason_code": "termination_in_flight_manual_required",
            }
        existing = session.scalar(
            select(HostingRetentionHold).where(
                HostingRetentionHold.tenant_id == tenant_id,
                HostingRetentionHold.hosting_service_id == service.id,
                HostingRetentionHold.hold_code == command.hold_code,
                HostingRetentionHold.cleared_at.is_(None),
            )
        )
        if existing is not None:
            raise HostingAlreadyExists(
                f"active retention hold {command.hold_code!r} already exists"
            )
        row = HostingRetentionHold(
            tenant_id=tenant_id,
            hosting_service_id=service.id,
            hold_code=command.hold_code,
            source_owner=command.source_owner,
            source_reference=command.source_reference,
            reason_code=command.reason_code,
            opened_at=command.requested_at,
        )
        session.add(row)
        session.flush()
        evidence = _append_outcome(
            session,
            tenant_id=tenant_id,
            service=service,
            command=command_row,
            evidence_key="business-decision",
            outcome_kind=OutcomeKind.APPLIED,
            outcome_class=OutcomeClass.SUCCEEDED,
            reason_code="retention_hold_opened",
            details={"hold_code": command.hold_code},
            occurred_at=command.requested_at,
        )
        _write_hosting_audit(
            session,
            tenant_id=tenant_id,
            actor=actor,
            action=RETENTION_AUDIT_ACTION,
            entity_id=service.id,
            occurred_at=command.requested_at,
            details={"hold_code": command.hold_code, "transition": "opened"},
        )
        return {
            "retention_hold_id": str(row.id),
            "command_id": str(command_row.id),
            "outcome_id": str(evidence.id),
            "disposition": ConsequenceDisposition.APPLIED.value,
            "reason_code": "retention_hold_opened",
        }

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="hosting.retention_hold.place",
        key=idempotency_key,
        fingerprint=request_fingerprint,
        operation=operation,
        operation_name="hosting.place_retention_hold",
        correlation_id=correlation_id,
        expires_at=idempotency_expires_at,
    )
    raw_hold_id = outcome.result.get("retention_hold_id")
    return RetentionHoldOutcome(
        retention_hold_id=UUID(str(raw_hold_id)) if raw_hold_id is not None else None,
        command_id=UUID(str(outcome.result["command_id"])),
        outcome_id=UUID(str(outcome.result["outcome_id"])),
        disposition=ConsequenceDisposition(str(outcome.result["disposition"])),
        reason_code=str(outcome.result["reason_code"]),
        replayed=outcome.replayed,
    )


def clear_retention_hold(
    db: Session,
    *,
    tenant_id: UUID,
    command: ClearRetentionHold,
    actor: Actor,
    idempotency_key: str,
    idempotency_expires_at: datetime,
    correlation_id: str | None = None,
) -> RetentionHoldOutcome:
    _aware("idempotency_expires_at", idempotency_expires_at)
    request_fingerprint = fingerprint(
        {"command": _payload(command), "actor": _payload(actor)}
    )

    def operation(session: Session) -> dict[str, object]:
        service = _service(
            session, tenant_id, command.hosting_service_id, lock=True
        )
        command_row = _new_command(
            session,
            tenant_id=tenant_id,
            service=service,
            kind=HostingCommandKind.RETENTION_HOLD,
            scope="hosting.retention_hold.clear",
            key=idempotency_key,
            request_fingerprint=request_fingerprint,
            payload=_payload(command),
            requested_at=command.requested_at,
            correlation_id=correlation_id,
        )
        row = session.scalar(
            select(HostingRetentionHold).where(
                HostingRetentionHold.tenant_id == tenant_id,
                HostingRetentionHold.hosting_service_id == service.id,
                HostingRetentionHold.hold_code == command.hold_code,
                HostingRetentionHold.cleared_at.is_(None),
            )
        )
        if row is None:
            evidence = _append_outcome(
                session,
                tenant_id=tenant_id,
                service=service,
                command=command_row,
                evidence_key="business-decision",
                outcome_kind=OutcomeKind.REFUSED,
                outcome_class=OutcomeClass.TERMINAL,
                reason_code="retention_hold_not_active",
                details={"hold_code": command.hold_code},
                occurred_at=command.requested_at,
            )
            _write_hosting_audit(
                session,
                tenant_id=tenant_id,
                actor=actor,
                action=RETENTION_AUDIT_ACTION,
                entity_id=service.id,
                occurred_at=command.requested_at,
                details={
                    "hold_code": command.hold_code,
                    "transition": "clear_refused",
                    "reason_code": "retention_hold_not_active",
                },
            )
            return {
                "retention_hold_id": None,
                "command_id": str(command_row.id),
                "outcome_id": str(evidence.id),
                "disposition": ConsequenceDisposition.REFUSED.value,
                "reason_code": "retention_hold_not_active",
            }
        if (
            row.source_owner != command.source_owner
            or row.source_reference != command.source_reference
        ):
            evidence = _append_outcome(
                session,
                tenant_id=tenant_id,
                service=service,
                command=command_row,
                evidence_key="business-decision",
                outcome_kind=OutcomeKind.REFUSED,
                outcome_class=OutcomeClass.TERMINAL,
                reason_code="retention_hold_source_not_authorized",
                details={"hold_code": command.hold_code},
                occurred_at=command.requested_at,
            )
            _write_hosting_audit(
                session,
                tenant_id=tenant_id,
                actor=actor,
                action=RETENTION_AUDIT_ACTION,
                entity_id=service.id,
                occurred_at=command.requested_at,
                details={
                    "hold_code": command.hold_code,
                    "transition": "clear_refused",
                    "reason_code": "retention_hold_source_not_authorized",
                },
            )
            return {
                "retention_hold_id": str(row.id),
                "command_id": str(command_row.id),
                "outcome_id": str(evidence.id),
                "disposition": ConsequenceDisposition.REFUSED.value,
                "reason_code": "retention_hold_source_not_authorized",
            }
        row.cleared_at = command.requested_at
        row.cleared_reason = command.reason_code
        session.flush()
        evidence = _append_outcome(
            session,
            tenant_id=tenant_id,
            service=service,
            command=command_row,
            evidence_key="business-decision",
            outcome_kind=OutcomeKind.APPLIED,
            outcome_class=OutcomeClass.SUCCEEDED,
            reason_code="retention_hold_cleared",
            details={"hold_code": command.hold_code},
            occurred_at=command.requested_at,
        )
        _write_hosting_audit(
            session,
            tenant_id=tenant_id,
            actor=actor,
            action=RETENTION_AUDIT_ACTION,
            entity_id=service.id,
            occurred_at=command.requested_at,
            details={"hold_code": command.hold_code, "transition": "cleared"},
        )
        return {
            "retention_hold_id": str(row.id),
            "command_id": str(command_row.id),
            "outcome_id": str(evidence.id),
            "disposition": ConsequenceDisposition.APPLIED.value,
            "reason_code": "retention_hold_cleared",
        }

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="hosting.retention_hold.clear",
        key=idempotency_key,
        fingerprint=request_fingerprint,
        operation=operation,
        operation_name="hosting.clear_retention_hold",
        correlation_id=correlation_id,
        expires_at=idempotency_expires_at,
    )
    raw_hold_id = outcome.result.get("retention_hold_id")
    return RetentionHoldOutcome(
        retention_hold_id=UUID(str(raw_hold_id)) if raw_hold_id is not None else None,
        command_id=UUID(str(outcome.result["command_id"])),
        outcome_id=UUID(str(outcome.result["outcome_id"])),
        disposition=ConsequenceDisposition(str(outcome.result["disposition"])),
        reason_code=str(outcome.result["reason_code"]),
        replayed=outcome.replayed,
    )


def receive_termination_approval(
    db: Session,
    *,
    envelope: CommandEnvelope,
    source_event_id: UUID,
    observation: TerminationApprovalObservationV1,
    received_at: datetime,
) -> TerminationApprovalEvidenceReceipt:
    """Record the released Approvals event as immutable local evidence."""

    _aware("received_at", received_at)
    event_digest = fingerprint(
        {
            "source_event_id": str(source_event_id),
            "observation": _payload(observation),
        }
    )

    def handler(session: Session, delivered: CommandEnvelope) -> dict[str, object]:
        if delivered.command_type != observation.event_type:
            raise ApprovalRequired("approval envelope type contradicts the event")
        if observation.subject_type != "hosting_service":
            raise ApprovalRequired("termination approval subject_type is not hosting_service")
        try:
            subject_id = UUID(observation.subject_id)
        except ValueError as exc:
            raise ApprovalRequired("termination approval subject_id is not a UUID") from exc
        _service(session, delivered.tenant_id, subject_id)
        if (
            observation.policy_code != TERMINATION_POLICY_CODE
            or observation.policy_version != TERMINATION_POLICY_VERSION
        ):
            raise ApprovalRequired("termination approval policy is not supported")
        if (
            observation.event_type != "approval.approved"
            or observation.state is not ApprovalObservationState.APPROVED
        ):
            raise ApprovalRequired(
                "Hosting records only final approval.approved evidence"
            )
        source_existing = session.scalar(
            select(HostingTerminationApprovalEvidence).where(
                HostingTerminationApprovalEvidence.tenant_id == delivered.tenant_id,
                HostingTerminationApprovalEvidence.source_event_id == source_event_id,
            )
        )
        if source_existing is not None:
            if source_existing.event_digest != event_digest:
                raise HostingError(
                    "approval source event identity was reused with different evidence"
                )
            return {
                "approval_evidence_id": str(source_existing.id),
                "duplicate": True,
            }
        existing = session.scalar(
            select(HostingTerminationApprovalEvidence).where(
                HostingTerminationApprovalEvidence.tenant_id == delivered.tenant_id,
                HostingTerminationApprovalEvidence.request_id == observation.request_id,
            )
        )
        if existing is not None:
            if (
                existing.source_event_id != source_event_id
                or existing.event_digest != event_digest
            ):
                raise HostingError(
                    "approval request identity was reused by another source event"
                )
            return {"approval_evidence_id": str(existing.id), "duplicate": True}
        row = HostingTerminationApprovalEvidence(
            tenant_id=delivered.tenant_id,
            event_type=observation.event_type,
            source_event_id=source_event_id,
            request_id=observation.request_id,
            subject_type=observation.subject_type,
            subject_id=observation.subject_id,
            policy_code=observation.policy_code,
            policy_version=observation.policy_version,
            content_digest=observation.content_digest,
            state=observation.state.value,
            event_digest=event_digest,
            received_at=received_at,
        )
        session.add(row)
        session.flush()
        return {"approval_evidence_id": str(row.id), "duplicate": False}

    processed = process_once(db, envelope, handler)
    return TerminationApprovalEvidenceReceipt(
        approval_evidence_id=UUID(str(processed.result["approval_evidence_id"])),
        duplicate=processed.was_duplicate or bool(processed.result.get("duplicate")),
    )


def request_termination(
    db: Session,
    *,
    tenant_id: UUID,
    command: RequestTermination,
    actor: Actor,
    idempotency_key: str,
    idempotency_expires_at: datetime,
    correlation_id: str | None = None,
) -> HostingConsequenceOutcome:
    _aware("idempotency_expires_at", idempotency_expires_at)
    request_fingerprint = fingerprint(
        {"command": _payload(command), "actor": _payload(actor)}
    )

    def operation(session: Session) -> dict[str, object]:
        service = _service(
            session, tenant_id, command.hosting_service_id, lock=True
        )
        if command.expected_version != service.row_version:
            raise StaleHostingVersion(
                f"expected service version {command.expected_version}, "
                f"found {service.row_version}"
            )
        approval = session.scalar(
            select(HostingTerminationApprovalEvidence).where(
                HostingTerminationApprovalEvidence.tenant_id == tenant_id,
                HostingTerminationApprovalEvidence.request_id
                == command.approval_request_id,
            )
        )
        if approval is None:
            raise ApprovalRequired("termination approval evidence was not received")
        if approval.state != ApprovalObservationState.APPROVED.value:
            raise ApprovalRequired("termination approval is not approved")
        if approval.subject_type != "hosting_service" or approval.subject_id != str(
            service.id
        ):
            raise ApprovalRequired("termination approval covers another subject")
        expected_digest = termination_content_digest(
            tenant_id,
            service.id,
            command.expected_version,
            command.requested_at,
        )
        if approval.content_digest != expected_digest:
            raise ApprovalRequired("termination approval does not cover the exact request")
        if _active_holds(session, tenant_id, service.id):
            row = _new_command(
                session,
                tenant_id=tenant_id,
                service=service,
                kind=HostingCommandKind.TERMINATION,
                scope="hosting.termination",
                key=idempotency_key,
                request_fingerprint=request_fingerprint,
                payload=_payload(command),
                requested_at=command.requested_at,
                correlation_id=correlation_id,
            )
            evidence = _append_outcome(
                session,
                tenant_id=tenant_id,
                service=service,
                command=row,
                evidence_key="business-decision",
                outcome_kind=OutcomeKind.REFUSED,
                outcome_class=OutcomeClass.TERMINAL,
                reason_code="retention_hold_active",
                details={"approval_request_id": str(approval.request_id)},
                occurred_at=command.requested_at,
            )
            _emit(
                session,
                tenant_id=tenant_id,
                event_type=CONSEQUENCE_DECIDED_EVENT,
                correlation_id=correlation_id,
                payload={
                    "hosting_service_id": str(service.id),
                    "command_id": str(row.id),
                    "outcome_id": str(evidence.id),
                    "disposition": ConsequenceDisposition.REFUSED.value,
                    "reason_code": "retention_hold_active",
                },
            )
            _write_hosting_audit(
                session,
                tenant_id=tenant_id,
                actor=actor,
                action=TERMINATION_AUDIT_ACTION,
                entity_id=service.id,
                occurred_at=command.requested_at,
                details={
                    "approval_request_id": str(approval.request_id),
                    "disposition": ConsequenceDisposition.REFUSED.value,
                    "reason_code": "retention_hold_active",
                },
            )
            return {
                "hosting_service_id": str(service.id),
                "command_id": str(row.id),
                "outcome_id": str(evidence.id),
                "disposition": ConsequenceDisposition.REFUSED.value,
                "lifecycle_state": service.lifecycle_state,
                "reason_code": "retention_hold_active",
            }
        if service.provider_account_ref is None:
            raise InvalidHostingTransition(
                "termination requires an observed account reference"
            )
        provider_account_ref = service.provider_account_ref
        if service.lifecycle_state not in {
            HostingLifecycleState.ACTIVE.value,
            HostingLifecycleState.SUSPENDED.value,
        }:
            raise InvalidHostingTransition("service is not in a terminable state")
        specification = _specification_version(
            session,
            tenant_id,
            service.specification_code,
            service.specification_version,
        )
        _append_desired(
            session,
            tenant_id=tenant_id,
            service=service,
            desired_account_state="terminated",
            specification=specification,
            requested_at=command.requested_at,
        )
        _mutate_service(
            session,
            service=service,
            mutation_kind="lifecycle_request",
            updated_at=command.requested_at,
            lifecycle_state=HostingLifecycleState.TERMINATING,
            state_effective_at=command.requested_at,
        )
        command_id = uuid4()
        provider_request = TerminateHostingAccountV1(
            operation_reference=str(command_id),
            account_ref=provider_account_ref,
        )
        delivery_payload = _payload(provider_request)
        row = _new_command(
            session,
            tenant_id=tenant_id,
            service=service,
            kind=HostingCommandKind.TERMINATION,
            scope="hosting.termination",
            key=idempotency_key,
            request_fingerprint=request_fingerprint,
            payload=delivery_payload,
            requested_at=command.requested_at,
            correlation_id=correlation_id,
            command_id=command_id,
        )
        _emit(
            session,
            tenant_id=tenant_id,
            event_type=TERMINATION_REQUESTED_EVENT,
            correlation_id=correlation_id,
            payload=delivery_payload,
        )
        evidence = _append_outcome(
            session,
            tenant_id=tenant_id,
            service=service,
            command=row,
            evidence_key="business-decision",
            outcome_kind=OutcomeKind.DEFERRED,
            outcome_class=OutcomeClass.RECONCILIATION_REQUIRED,
            reason_code="termination_requested",
            details={"approval_request_id": str(approval.request_id)},
            occurred_at=command.requested_at,
        )
        _emit(
            session,
            tenant_id=tenant_id,
            event_type=CONSEQUENCE_DECIDED_EVENT,
            correlation_id=correlation_id,
            payload={
                "hosting_service_id": str(service.id),
                "command_id": str(row.id),
                "outcome_id": str(evidence.id),
                "disposition": ConsequenceDisposition.DEFERRED.value,
                "reason_code": "termination_requested",
            },
        )
        _write_hosting_audit(
            session,
            tenant_id=tenant_id,
            actor=actor,
            action=TERMINATION_AUDIT_ACTION,
            entity_id=service.id,
            occurred_at=command.requested_at,
            details={
                "approval_request_id": str(approval.request_id),
                "disposition": ConsequenceDisposition.DEFERRED.value,
                "reason_code": "termination_requested",
            },
        )
        return {
            "hosting_service_id": str(service.id),
            "command_id": str(row.id),
            "outcome_id": str(evidence.id),
            "disposition": ConsequenceDisposition.DEFERRED.value,
            "lifecycle_state": service.lifecycle_state,
            "reason_code": "termination_requested",
        }

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="hosting.termination",
        key=idempotency_key,
        fingerprint=request_fingerprint,
        operation=operation,
        operation_name="hosting.request_termination",
        correlation_id=correlation_id,
        expires_at=idempotency_expires_at,
    )
    return HostingConsequenceOutcome(
        hosting_service_id=UUID(str(outcome.result["hosting_service_id"])),
        command_id=UUID(str(outcome.result["command_id"])),
        outcome_id=UUID(str(outcome.result["outcome_id"])),
        disposition=ConsequenceDisposition(str(outcome.result["disposition"])),
        lifecycle_state=HostingLifecycleState(str(outcome.result["lifecycle_state"])),
        reason_code=str(outcome.result["reason_code"]),
        replayed=outcome.replayed,
    )


def receive_hosting_observation(
    db: Session,
    *,
    envelope: CommandEnvelope,
    hosting_service_id: UUID | None,
    observation: HostingObservationV1,
    received_at: datetime,
) -> HostingObservationReceipt:
    _aware("received_at", received_at)
    observation_payload = _payload(observation)
    payload_digest = fingerprint(observation_payload)

    def handler(session: Session, delivered: CommandEnvelope) -> dict[str, object]:
        if hosting_service_id is not None:
            service = _service(session, delivered.tenant_id, hosting_service_id)
            if service.provider_account_ref is None:
                if observation.operation_reference is None:
                    raise HostingError(
                        "first observation correlation requires an operation_reference"
                    )
                try:
                    operation_id = UUID(observation.operation_reference)
                except ValueError as exc:
                    raise HostingError(
                        "first observation operation_reference is not a Hosting command"
                    ) from exc
                operation = session.scalar(
                    select(HostingCommand.id).where(
                        HostingCommand.tenant_id == delivered.tenant_id,
                        HostingCommand.hosting_service_id == hosting_service_id,
                        HostingCommand.id == operation_id,
                    )
                )
                if operation is None:
                    raise HostingError(
                        "operation_reference does not belong to the hinted hosting service"
                    )
            elif (
                observation.capability_binding_ref
                != service.capability_binding_ref
                or observation.provider_account_ref != service.provider_account_ref
            ):
                raise HostingError(
                    "observation provider correlation does not match the frozen binding/account pair"
                )
        existing = session.scalar(
            select(HostingObservation).where(
                HostingObservation.tenant_id == delivered.tenant_id,
                HostingObservation.capability_binding_ref == observation.capability_binding_ref,
                HostingObservation.provider_event_id == observation.provider_event_id,
            )
        )
        if existing is not None:
            if existing.payload_digest != payload_digest:
                raise HostingError(
                    "provider event identity was reused with different observation data"
                )
            return {"observation_id": str(existing.id), "duplicate": True}
        row = HostingObservation(
            tenant_id=delivered.tenant_id,
            hosting_service_id=hosting_service_id,
            operation_reference=observation.operation_reference,
            provider_account_ref=observation.provider_account_ref,
            capability_binding_ref=observation.capability_binding_ref,
            provider_event_id=observation.provider_event_id,
            observation_kind=observation.observation_kind,
            provider_statuses=list(observation.provider_statuses),
            observed_package_ref=observation.observed_package_ref,
            source_mode=observation.source_mode,
            payload_digest=payload_digest,
            observed_at=observation.observed_at,
            received_at=received_at,
        )
        session.add(row)
        session.flush()
        for fact in observation.resources:
            period_identity = f"{fact.period_start.isoformat() if fact.period_start else '*'}:{fact.period_end.isoformat() if fact.period_end else '*'}"
            session.add(
                HostingObservationResource(
                    tenant_id=delivered.tenant_id,
                    hosting_observation_id=row.id,
                    resource_kind=fact.resource_kind,
                    quantity=fact.quantity,
                    unit=fact.unit,
                    period_start=fact.period_start,
                    period_end=fact.period_end,
                    period_identity=period_identity,
                )
            )
        session.flush()
        return {"observation_id": str(row.id), "duplicate": False}

    outcome = process_once(db, envelope, handler)
    return HostingObservationReceipt(
        observation_id=UUID(str(outcome.result["observation_id"])),
        duplicate=outcome.was_duplicate or bool(outcome.result.get("duplicate")),
    )


def _open_attention(
    db: Session,
    *,
    tenant_id: UUID,
    service: HostingService,
    condition_code: str,
    classification: str,
    reason_code: str,
    details: dict[str, object],
    opened_at: datetime,
    source_command_id: UUID | None = None,
) -> HostingAttentionCondition:
    existing = db.scalar(
        select(HostingAttentionCondition).where(
            HostingAttentionCondition.tenant_id == tenant_id,
            HostingAttentionCondition.hosting_service_id == service.id,
            HostingAttentionCondition.condition_code == condition_code,
            HostingAttentionCondition.resolved_at.is_(None),
        )
    )
    if existing is not None:
        return existing
    row = HostingAttentionCondition(
        tenant_id=tenant_id,
        hosting_service_id=service.id,
        source_command_id=source_command_id,
        condition_code=condition_code,
        classification=classification,
        reason_code=reason_code,
        details=details,
        opened_at=opened_at,
    )
    db.add(row)
    db.flush()
    _emit(
        db,
        tenant_id=tenant_id,
        event_type=ATTENTION_REQUIRED_EVENT,
        payload={
            "hosting_service_id": str(service.id),
            "attention_id": str(row.id),
            "condition_code": condition_code,
            "reason_code": reason_code,
        },
    )
    return row


def _complete_deferred_consequences(
    db: Session,
    *,
    tenant_id: UUID,
    service: HostingService,
    observation: HostingObservation,
    command_scope: str,
    occurred_at: datetime,
) -> None:
    commands = db.scalars(
        select(HostingCommand)
        .join(
            HostingCommandOutcome,
            (
                HostingCommandOutcome.tenant_id == HostingCommand.tenant_id
            )
            & (
                HostingCommandOutcome.hosting_command_id == HostingCommand.id
            ),
        )
        .where(
            HostingCommand.tenant_id == tenant_id,
            HostingCommand.hosting_service_id == service.id,
            HostingCommand.idempotency_scope == command_scope,
            HostingCommandOutcome.evidence_key == "business-decision",
            HostingCommandOutcome.outcome_kind == OutcomeKind.DEFERRED.value,
        )
        .order_by(HostingCommand.requested_at, HostingCommand.id)
    )
    evidence_key = f"provider-confirmation:{observation.id}"
    for command in commands:
        already_confirmed = db.scalar(
            select(HostingCommandOutcome.id)
            .where(
                HostingCommandOutcome.tenant_id == tenant_id,
                HostingCommandOutcome.hosting_command_id == command.id,
                HostingCommandOutcome.evidence_key != "business-decision",
                HostingCommandOutcome.outcome_kind.in_(
                    (
                        OutcomeKind.APPLIED.value,
                        OutcomeKind.FAILED.value,
                        OutcomeKind.SUPERSEDED.value,
                    )
                ),
            )
            .limit(1)
        )
        if already_confirmed is not None:
            continue
        outcome = _append_outcome(
            db,
            tenant_id=tenant_id,
            service=service,
            command=command,
            evidence_key=evidence_key,
            outcome_kind=OutcomeKind.APPLIED,
            outcome_class=OutcomeClass.SUCCEEDED,
            provider_reference=observation.provider_account_ref,
            reason_code=f"confirmed_by:{observation.observation_kind}",
            details={"observation_id": str(observation.id)},
            occurred_at=occurred_at,
        )
        _emit(
            db,
            tenant_id=tenant_id,
            event_type=CONSEQUENCE_DECIDED_EVENT,
            payload={
                "hosting_service_id": str(service.id),
                "command_id": str(command.id),
                "outcome_id": str(outcome.id),
                "disposition": ConsequenceDisposition.APPLIED.value,
                "reason_code": f"confirmed_by:{observation.observation_kind}",
            },
        )
        attention = db.scalar(
            select(HostingAttentionCondition).where(
                HostingAttentionCondition.tenant_id == tenant_id,
                HostingAttentionCondition.hosting_service_id == service.id,
                HostingAttentionCondition.source_command_id == command.id,
                HostingAttentionCondition.condition_code
                == f"command:{command.command_kind}:unconfirmed",
                HostingAttentionCondition.resolved_at.is_(None),
            )
        )
        if attention is not None:
            attention.resolved_at = occurred_at
            attention.resolution_code = "provider_observation_confirmed"


def _supersede_deferred_consequences(
    db: Session,
    *,
    tenant_id: UUID,
    service: HostingService,
    command_scope: str,
    superseded_by_command_id: UUID,
    occurred_at: datetime,
) -> None:
    commands = db.scalars(
        select(HostingCommand)
        .join(
            HostingCommandOutcome,
            (
                HostingCommandOutcome.tenant_id == HostingCommand.tenant_id
            )
            & (HostingCommandOutcome.hosting_command_id == HostingCommand.id),
        )
        .where(
            HostingCommand.tenant_id == tenant_id,
            HostingCommand.hosting_service_id == service.id,
            HostingCommand.idempotency_scope == command_scope,
            HostingCommandOutcome.evidence_key == "business-decision",
            HostingCommandOutcome.outcome_kind == OutcomeKind.DEFERRED.value,
        )
        .order_by(HostingCommand.requested_at, HostingCommand.id)
    )
    for command in commands:
        final = db.scalar(
            select(HostingCommandOutcome.id)
            .where(
                HostingCommandOutcome.tenant_id == tenant_id,
                HostingCommandOutcome.hosting_command_id == command.id,
                HostingCommandOutcome.evidence_key != "business-decision",
                HostingCommandOutcome.outcome_kind.in_(
                    (
                        OutcomeKind.APPLIED.value,
                        OutcomeKind.FAILED.value,
                        OutcomeKind.SUPERSEDED.value,
                    )
                ),
            )
            .limit(1)
        )
        if final is not None:
            continue
        outcome = _append_outcome(
            db,
            tenant_id=tenant_id,
            service=service,
            command=command,
            evidence_key=f"superseded-by:{superseded_by_command_id}",
            outcome_kind=OutcomeKind.SUPERSEDED,
            outcome_class=OutcomeClass.TERMINAL,
            reason_code="superseded_by_inverse_command",
            details={"superseded_by_command_id": str(superseded_by_command_id)},
            occurred_at=occurred_at,
        )
        _emit(
            db,
            tenant_id=tenant_id,
            event_type=CONSEQUENCE_DECIDED_EVENT,
            payload={
                "hosting_service_id": str(service.id),
                "command_id": str(command.id),
                "outcome_id": str(outcome.id),
                "disposition": ConsequenceDisposition.SUPERSEDED.value,
                "reason_code": "superseded_by_inverse_command",
            },
        )
        attention = db.scalar(
            select(HostingAttentionCondition).where(
                HostingAttentionCondition.tenant_id == tenant_id,
                HostingAttentionCondition.hosting_service_id == service.id,
                HostingAttentionCondition.source_command_id == command.id,
                HostingAttentionCondition.condition_code
                == f"command:{command.command_kind}:unconfirmed",
                HostingAttentionCondition.resolved_at.is_(None),
            )
        )
        if attention is not None:
            attention.resolved_at = occurred_at
            attention.resolution_code = "superseded_by_inverse_command"


def _complete_deferred_package_changes(
    db: Session,
    *,
    tenant_id: UUID,
    service: HostingService,
    observation: HostingObservation,
    occurred_at: datetime,
) -> None:
    if observation.observed_package_ref is None:
        return
    latest_desired = _latest_desired(db, tenant_id, service.id)
    commands = db.scalars(
        select(HostingCommand)
        .join(
            HostingCommandOutcome,
            (
                HostingCommandOutcome.tenant_id == HostingCommand.tenant_id
            )
            & (HostingCommandOutcome.hosting_command_id == HostingCommand.id),
        )
        .where(
            HostingCommand.tenant_id == tenant_id,
            HostingCommand.hosting_service_id == service.id,
            HostingCommand.idempotency_scope == "hosting.package",
            HostingCommandOutcome.evidence_key == "business-decision",
            HostingCommandOutcome.outcome_kind == OutcomeKind.DEFERRED.value,
        )
        .order_by(HostingCommand.requested_at, HostingCommand.id)
    )
    for command in commands:
        final = db.scalar(
            select(HostingCommandOutcome.id)
            .where(
                HostingCommandOutcome.tenant_id == tenant_id,
                HostingCommandOutcome.hosting_command_id == command.id,
                HostingCommandOutcome.evidence_key != "business-decision",
                HostingCommandOutcome.outcome_kind.in_(
                    (
                        OutcomeKind.APPLIED.value,
                        OutcomeKind.FAILED.value,
                        OutcomeKind.SUPERSEDED.value,
                    )
                ),
            )
            .limit(1)
        )
        if final is not None:
            continue
        target = command.payload.get("target_package_ref")
        if target == observation.observed_package_ref:
            outcome_kind = OutcomeKind.APPLIED
            disposition = ConsequenceDisposition.APPLIED
            reason_code = "package_confirmed_by_observation"
        elif target != latest_desired.package_ref:
            outcome_kind = OutcomeKind.SUPERSEDED
            disposition = ConsequenceDisposition.SUPERSEDED
            reason_code = "package_superseded_by_later_desired_revision"
        else:
            continue
        outcome = _append_outcome(
            db,
            tenant_id=tenant_id,
            service=service,
            command=command,
            evidence_key=f"package-observation:{observation.id}",
            outcome_kind=outcome_kind,
            outcome_class=(
                OutcomeClass.SUCCEEDED
                if outcome_kind is OutcomeKind.APPLIED
                else OutcomeClass.TERMINAL
            ),
            provider_reference=observation.provider_account_ref,
            reason_code=reason_code,
            details={"observation_id": str(observation.id)},
            occurred_at=occurred_at,
        )
        _emit(
            db,
            tenant_id=tenant_id,
            event_type=CONSEQUENCE_DECIDED_EVENT,
            payload={
                "hosting_service_id": str(service.id),
                "command_id": str(command.id),
                "outcome_id": str(outcome.id),
                "disposition": disposition.value,
                "reason_code": reason_code,
            },
        )
        attention = db.scalar(
            select(HostingAttentionCondition).where(
                HostingAttentionCondition.tenant_id == tenant_id,
                HostingAttentionCondition.hosting_service_id == service.id,
                HostingAttentionCondition.source_command_id == command.id,
                HostingAttentionCondition.condition_code
                == f"command:{command.command_kind}:unconfirmed",
                HostingAttentionCondition.resolved_at.is_(None),
            )
        )
        if attention is not None:
            attention.resolved_at = occurred_at
            attention.resolution_code = reason_code


def _resolve_observed_command_attention(
    db: Session,
    *,
    tenant_id: UUID,
    service: HostingService,
    observation: HostingObservation,
    resolved_at: datetime,
) -> None:
    if observation.operation_reference is None:
        return
    try:
        command_id = UUID(observation.operation_reference)
    except ValueError:
        return
    command = db.scalar(
        select(HostingCommand).where(
            HostingCommand.tenant_id == tenant_id,
            HostingCommand.hosting_service_id == service.id,
            HostingCommand.id == command_id,
        )
    )
    if command is None:
        return
    attention = db.scalar(
        select(HostingAttentionCondition).where(
            HostingAttentionCondition.tenant_id == tenant_id,
            HostingAttentionCondition.hosting_service_id == service.id,
            HostingAttentionCondition.source_command_id == command.id,
            HostingAttentionCondition.condition_code
            == f"command:{command.command_kind}:unconfirmed",
            HostingAttentionCondition.resolved_at.is_(None),
        )
    )
    if attention is not None:
        attention.resolved_at = resolved_at
        attention.resolution_code = "provider_observation_confirmed"


def record_hosting_outcome(
    db: Session,
    *,
    envelope: CommandEnvelope,
    outcome: RecordHostingOutcome,
    received_at: datetime,
) -> UUID:
    _aware("received_at", received_at)

    def handler(session: Session, delivered: CommandEnvelope) -> dict[str, object]:
        command = _command(session, delivered.tenant_id, outcome.hosting_command_id)
        existing = session.scalar(
            select(HostingCommandOutcome).where(
                HostingCommandOutcome.tenant_id == delivered.tenant_id,
                HostingCommandOutcome.hosting_command_id == command.id,
                HostingCommandOutcome.evidence_key == outcome.evidence_key,
            )
        )
        if existing is not None:
            expected = {
                "outcome_kind": outcome.outcome_kind.value,
                "outcome_class": outcome.outcome_class.value,
                "provider_reference": outcome.provider_reference,
                "reason_code": outcome.reason_code,
                "details": _payload(outcome.evidence),
                "occurred_at": _as_utc(outcome.occurred_at),
            }
            actual = {
                "outcome_kind": existing.outcome_kind,
                "outcome_class": existing.outcome_class,
                "provider_reference": existing.provider_reference,
                "reason_code": existing.reason_code,
                "details": existing.details,
                "occurred_at": _as_utc(existing.occurred_at),
            }
            if actual != expected:
                raise HostingError(
                    "outcome evidence_key was reused with different immutable content"
                )
            return {"outcome_id": str(existing.id)}
        service = _service(session, delivered.tenant_id, command.hosting_service_id)
        row = _append_outcome(
            session,
            tenant_id=delivered.tenant_id,
            service=service,
            command=command,
            evidence_key=outcome.evidence_key,
            outcome_kind=outcome.outcome_kind,
            outcome_class=outcome.outcome_class,
            provider_reference=outcome.provider_reference,
            reason_code=outcome.reason_code,
            details=_payload(outcome.evidence),
            occurred_at=outcome.occurred_at,
        )
        if outcome.outcome_class in {
            OutcomeClass.RETRYABLE,
            OutcomeClass.RECONCILIATION_REQUIRED,
        } or outcome.outcome_kind is OutcomeKind.FAILED:
            _open_attention(
                session,
                tenant_id=delivered.tenant_id,
                service=service,
                condition_code=f"command:{command.command_kind}:unconfirmed",
                classification="reconciliation_required",
                reason_code=outcome.reason_code or "provider_outcome_requires_attention",
                details={"command_id": str(command.id), "outcome_id": str(row.id)},
                opened_at=received_at,
                source_command_id=command.id,
            )
        if (
            command.idempotency_scope == "hosting.package"
            and outcome.outcome_kind is OutcomeKind.FAILED
        ):
            _emit(
                session,
                tenant_id=delivered.tenant_id,
                event_type=CONSEQUENCE_DECIDED_EVENT,
                payload={
                    "hosting_service_id": str(service.id),
                    "command_id": str(command.id),
                    "outcome_id": str(row.id),
                    "disposition": ConsequenceDisposition.FAILED.value,
                    "reason_code": outcome.reason_code
                    or "package_delivery_failed",
                },
            )
        return {"outcome_id": str(row.id)}

    processed = process_once(db, envelope, handler)
    return UUID(str(processed.result["outcome_id"]))


def reconcile_hosting_service(
    db: Session,
    *,
    tenant_id: UUID,
    hosting_service_id: UUID,
    reconciled_at: datetime,
) -> HostingReconciliationResult:
    _aware("reconciled_at", reconciled_at)
    service = _service(db, tenant_id, hosting_service_id, lock=True)
    desired = _latest_desired(db, tenant_id, hosting_service_id)
    observation = _latest_observation(db, tenant_id, hosting_service_id)
    previous = HostingLifecycleState(service.lifecycle_state)
    decision = decide_lifecycle_transition(
        LifecycleInput(
            current_state=previous,
            state_effective_at=_as_utc(service.state_effective_at),
            observation_kind=observation.observation_kind if observation else None,
            observation_observed_at=(
                _as_utc(observation.observed_at) if observation else None
            ),
        )
    )
    observed_state = observation.observation_kind if observation and observation.observation_kind in {"active", "suspended", "terminated"} else None
    drift = derive_hosting_drift(
        desired_account_state=desired.desired_account_state,
        desired_package_ref=desired.package_ref,
        observed_account_state=observed_state,
        observed_package_ref=observation.observed_package_ref if observation else None,
    )
    provider_pair_assigned = (
        observation is not None and service.provider_account_ref is None
    )
    if decision.changed:
        if observation is None:
            raise HostingError("a changed lifecycle decision requires an observation")
        _mutate_service(
            db,
            service=service,
            mutation_kind="observation_confirmation",
            updated_at=reconciled_at,
            lifecycle_state=decision.next_state,
            state_effective_at=decision.effective_at,
            observation_id=observation.id,
        )
        _emit(
            db,
            tenant_id=tenant_id,
            event_type=LIFECYCLE_CHANGED_EVENT,
            payload={
                "hosting_service_id": str(service.id),
                "previous_state": previous.value,
                "current_state": decision.next_state.value,
                "source_version": service.row_version,
            },
        )
        if observation is not None and previous is HostingLifecycleState.SUSPENSION_REQUESTED:
            _complete_deferred_consequences(
                db,
                tenant_id=tenant_id,
                service=service,
                observation=observation,
                command_scope="hosting.suspension",
                occurred_at=reconciled_at,
            )
        elif observation is not None and previous is HostingLifecycleState.RESTORATION_REQUESTED:
            _complete_deferred_consequences(
                db,
                tenant_id=tenant_id,
                service=service,
                observation=observation,
                command_scope="hosting.restoration",
                occurred_at=reconciled_at,
            )
        elif observation is not None and previous is HostingLifecycleState.TERMINATING:
            _complete_deferred_consequences(
                db,
                tenant_id=tenant_id,
                service=service,
                observation=observation,
                command_scope="hosting.termination",
                occurred_at=reconciled_at,
            )
    elif provider_pair_assigned:
        if observation is None:
            raise HostingError("provider correlation requires an observation")
        _mutate_service(
            db,
            service=service,
            mutation_kind="provider_correlation",
            updated_at=reconciled_at,
            observation_id=observation.id,
        )
    if observation is not None:
        _complete_deferred_package_changes(
            db,
            tenant_id=tenant_id,
            service=service,
            observation=observation,
            occurred_at=reconciled_at,
        )
        _resolve_observed_command_attention(
            db,
            tenant_id=tenant_id,
            service=service,
            observation=observation,
            resolved_at=reconciled_at,
        )
    if drift.reasons:
        _open_attention(
            db,
            tenant_id=tenant_id,
            service=service,
            condition_code="desired_observed_drift",
            classification="reconciliation_required",
            reason_code=drift.reasons[0],
            details={"reasons": list(drift.reasons)},
            opened_at=reconciled_at,
        )
    else:
        condition = db.scalar(
            select(HostingAttentionCondition).where(
                HostingAttentionCondition.tenant_id == tenant_id,
                HostingAttentionCondition.hosting_service_id == service.id,
                HostingAttentionCondition.condition_code == "desired_observed_drift",
                HostingAttentionCondition.resolved_at.is_(None),
            )
        )
        if condition is not None:
            condition.resolved_at = reconciled_at
            condition.resolution_code = "observed_state_converged"
    db.flush()
    return HostingReconciliationResult(
        hosting_service_id=service.id,
        previous_state=previous,
        current_state=HostingLifecycleState(service.lifecycle_state),
        changed=decision.changed,
        reason_code=decision.reason_code,
        drift=drift,
    )


def request_hosting_reconcile(
    db: Session,
    *,
    tenant_id: UUID,
    hosting_service_id: UUID,
    requested_at: datetime,
    actor: Actor,
    idempotency_key: str,
    idempotency_expires_at: datetime,
    correlation_id: str | None = None,
) -> HostingCommandReceipt:
    _aware("requested_at", requested_at)
    _aware("idempotency_expires_at", idempotency_expires_at)
    payload = {"hosting_service_id": str(hosting_service_id), "requested_at": requested_at.isoformat()}
    request_fingerprint = fingerprint({"request": payload, "actor": _payload(actor)})

    def operation(session: Session) -> dict[str, object]:
        service = _service(session, tenant_id, hosting_service_id, lock=True)
        if service.provider_account_ref is None:
            raise InvalidHostingTransition(
                "reconcile requires an observed account reference"
            )
        command_id = uuid4()
        provider_request = ReconcileHostingAccountV1(
            operation_reference=str(command_id),
            account_ref=service.provider_account_ref,
        )
        delivery_payload = _payload(provider_request)
        row = _new_command(
            session,
            tenant_id=tenant_id,
            service=service,
            kind=HostingCommandKind.RECONCILE,
            scope="hosting.reconcile",
            key=idempotency_key,
            request_fingerprint=request_fingerprint,
            payload=delivery_payload,
            requested_at=requested_at,
            correlation_id=correlation_id,
            command_id=command_id,
        )
        _emit(
            session,
            tenant_id=tenant_id,
            event_type=RECONCILE_REQUESTED_EVENT,
            correlation_id=correlation_id,
            payload=delivery_payload,
        )
        _write_hosting_audit(
            session,
            tenant_id=tenant_id,
            actor=actor,
            action=REPAIR_AUDIT_ACTION,
            entity_id=service.id,
            occurred_at=requested_at,
            details={"command_id": str(row.id)},
        )
        return {
            "hosting_service_id": str(service.id),
            "command_id": str(row.id),
            "lifecycle_state": service.lifecycle_state,
        }

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="hosting.reconcile",
        key=idempotency_key,
        fingerprint=request_fingerprint,
        operation=operation,
        operation_name="hosting.request_reconcile",
        correlation_id=correlation_id,
        expires_at=idempotency_expires_at,
    )
    return _command_receipt(
        db, tenant_id=tenant_id, result=dict(outcome.result), replayed=outcome.replayed
    )


__all__ = [
    "ATTENTION_REQUIRED_EVENT",
    "CONSEQUENCE_DECIDED_EVENT",
    "LIFECYCLE_CHANGED_EVENT",
    "PACKAGE_AUDIT_ACTION",
    "PACKAGE_REQUESTED_EVENT",
    "PROVISION_REQUESTED_EVENT",
    "PUBLIC_EVENT_TYPES",
    "RECONCILE_REQUESTED_EVENT",
    "REPAIR_AUDIT_ACTION",
    "RETENTION_AUDIT_ACTION",
    "SPECIFICATION_PUBLISHED_EVENT",
    "SUSPENSION_REQUESTED_EVENT",
    "SUSPENSION_AUDIT_ACTION",
    "TERMINATION_AUDIT_ACTION",
    "TERMINATION_REQUESTED_EVENT",
    "ApprovalRequired",
    "HostingAlreadyExists",
    "HostingCommandNotFound",
    "HostingError",
    "HostingNotFound",
    "HostingSpecificationNotFound",
    "InvalidHostingTransition",
    "StaleHostingVersion",
    "apply_suspension_request",
    "clear_retention_hold",
    "place_retention_hold",
    "publish_specification_version",
    "receive_hosting_observation",
    "receive_termination_approval",
    "reconcile_hosting_service",
    "record_hosting_outcome",
    "request_hosting_reconcile",
    "request_package_change",
    "request_provisioning",
    "request_termination",
    "restore_suspension",
]
