"""The one writer of Dotmac domain-service lifecycle state.

Every function receives the caller's SQLAlchemy session, repeats tenant scope in
its query, flushes, and never commits or rolls back.  Provider effects leave in
the kernel outbox; provider observations arrive through the kernel inbox.  An
observation is evidence only: only ``reconcile_domain`` may interpret it into a
Dotmac lifecycle transition.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Final
from uuid import UUID

from dotmac_kernel.audit import write_audit_event
from dotmac_kernel.idempotency import execute_once, fingerprint_of
from dotmac_kernel.messaging.envelope import CommandEnvelope
from dotmac_kernel.messaging.inbox import process_once
from dotmac_kernel.messaging.outbox import enqueue_event
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from dotmac_domains.contracts import (
    Actor,
    ApplyDNSRecordSetsV1,
    ApprovalDecision,
    ApprovalReceipt,
    ClearDomainHold,
    ConfigureDNSZoneV1,
    ConsequenceOutcome,
    ConsequenceRequest,
    DomainCommandKind,
    DomainCommandReceipt,
    DomainContactsIntent,
    DomainDNSRecordsetsIntent,
    DomainDNSZoneIntent,
    DomainLifecycleState,
    DomainNameserversIntent,
    DomainObservationV1,
    DNSObservationV1,
    ObservationReceipt,
    OutcomeClass,
    OutcomeKind,
    ReconciliationResult,
    RecordRegistrarOutcome,
    RegisterDomain,
    RegisterDomainV1,
    RenewDomain,
    RequestTransferDomain,
    SetDomainIntent,
    TransferDirection,
    TransferDomainV1,
    UpdateDomainContactsV1,
    UpdateDomainNameserversV1,
    fingerprint,
    transfer_out_content_digest,
)
from dotmac_domains.engine import (
    LifecycleInput,
    decide_lifecycle_transition,
    derive_drift,
)
from dotmac_domains.models import (
    DomainAttentionCondition,
    DomainCommand,
    DomainCommandOutcome,
    DNSObservation,
    DomainHold,
    DomainIntent,
    DomainObservation,
    DomainService,
)

REGISTRATION_REQUESTED_EVENT: Final[str] = "domains.registrar.registration.requested.v1"
RENEWAL_REQUESTED_EVENT: Final[str] = "domains.registrar.renewal.requested.v1"
TRANSFER_REQUESTED_EVENT: Final[str] = "domains.registrar.transfer.requested.v1"
CONTACTS_REQUESTED_EVENT: Final[str] = "domains.registrar.contacts.requested.v1"
NAMESERVERS_REQUESTED_EVENT: Final[str] = "domains.registrar.nameservers.requested.v1"
DNS_ZONE_REQUESTED_EVENT: Final[str] = "dns.authoritative.zone.requested.v1"
DNS_RECORDSET_REQUESTED_EVENT: Final[str] = "dns.authoritative.recordset.requested.v1"
LIFECYCLE_CHANGED_EVENT: Final[str] = "domains.lifecycle.changed.v1"
ATTENTION_REQUIRED_EVENT: Final[str] = "domains.attention.required.v1"
PROVIDER_COMMAND_FAILED_EVENT: Final[str] = "domains.provider_command.failed.v1"
RENEWAL_SCHEDULE_CHANGED_EVENT: Final[str] = "domains.renewal_schedule.changed.v1"
CONSEQUENCE_DECIDED_EVENT: Final[str] = "domains.consequence.decided.v1"

PUBLIC_EVENT_TYPES: Final[tuple[str, ...]] = (
    REGISTRATION_REQUESTED_EVENT,
    RENEWAL_REQUESTED_EVENT,
    TRANSFER_REQUESTED_EVENT,
    CONTACTS_REQUESTED_EVENT,
    NAMESERVERS_REQUESTED_EVENT,
    DNS_ZONE_REQUESTED_EVENT,
    DNS_RECORDSET_REQUESTED_EVENT,
    LIFECYCLE_CHANGED_EVENT,
    ATTENTION_REQUIRED_EVENT,
    PROVIDER_COMMAND_FAILED_EVENT,
    RENEWAL_SCHEDULE_CHANGED_EVENT,
    CONSEQUENCE_DECIDED_EVENT,
)

TRANSFER_OUT_AUDIT_ACTION: Final[str] = "domains.transfer_out.requested"
HOLD_AUDIT_ACTION: Final[str] = "domains.hold.changed"

# Safety window for a registrar POLL fact used to authorize renewal. This is
# intentionally not a notice cadence or commercial grace period.
RENEWAL_POLL_MAX_AGE: Final[timedelta] = timedelta(hours=24)
_TERMINAL_INTENT_STATES: Final[frozenset[DomainLifecycleState]] = frozenset(
    {DomainLifecycleState.REGISTRATION_FAILED, DomainLifecycleState.RELEASED}
)
_INACTIVE_NAME_STATES: Final[frozenset[str]] = frozenset(
    state.value for state in _TERMINAL_INTENT_STATES
)


class DomainError(ValueError):
    """Base for stable, fail-closed domain-owner errors."""


class DomainNotFound(DomainError):
    pass


class DomainAlreadyExists(DomainError):
    pass


class InvalidDomainTransition(DomainError):
    pass


class StaleDomainVersion(DomainError):
    pass


class StaleRegistrarObservation(DomainError):
    pass


class ReleaseNotPermitted(DomainError):
    pass


class CommandNotFound(DomainError):
    pass


def _aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainError(f"{name} must be timezone-aware")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _json_value(value: object) -> object:
    """Convert typed contracts to a deterministic JSON-compatible value."""

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
    # Money leaves as its recursively converted dataclass fields. Decimal and
    # other exact scalar values have no native JSON representation.
    json.dumps(value, default=str)
    return str(value)


def _payload(value: object) -> dict[str, object]:
    converted = _json_value(value)
    if not isinstance(converted, dict):
        raise DomainError("command payload must serialize to an object")
    return converted


def _domain(
    db: Session, tenant_id: UUID, domain_service_id: UUID, *, lock: bool = False
) -> DomainService:
    statement = select(DomainService).where(
        DomainService.tenant_id == tenant_id,
        DomainService.id == domain_service_id,
    )
    if lock:
        statement = statement.with_for_update()
    row = db.scalar(statement)
    if row is None:
        raise DomainNotFound(f"domain service {domain_service_id} was not found")
    return row


def _command(db: Session, tenant_id: UUID, domain_command_id: UUID) -> DomainCommand:
    row = db.scalar(
        select(DomainCommand).where(
            DomainCommand.tenant_id == tenant_id,
            DomainCommand.id == domain_command_id,
        )
    )
    if row is None:
        raise CommandNotFound(f"domain command {domain_command_id} was not found")
    return row


def _new_command(
    db: Session,
    *,
    tenant_id: UUID,
    service: DomainService,
    kind: DomainCommandKind,
    idempotency_scope: str,
    idempotency_key: str,
    request_fingerprint: str,
    payload: dict[str, object],
    requested_at: datetime,
    correlation_id: str | None,
) -> DomainCommand:
    row = DomainCommand(
        tenant_id=tenant_id,
        domain_service_id=service.id,
        command_kind=kind.value,
        idempotency_scope=idempotency_scope,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        correlation_id=correlation_id,
        payload=payload,
        requested_at=requested_at,
    )
    db.add(row)
    db.flush()
    return row


def _append_intent(
    db: Session,
    *,
    tenant_id: UUID,
    service: DomainService,
    intent_kind: str,
    content: dict[str, object],
    requested_at: datetime,
) -> DomainIntent:
    previous = db.scalar(
        select(func.max(DomainIntent.version)).where(
            DomainIntent.tenant_id == tenant_id,
            DomainIntent.domain_service_id == service.id,
            DomainIntent.intent_kind == intent_kind,
        )
    )
    row = DomainIntent(
        tenant_id=tenant_id,
        domain_service_id=service.id,
        intent_kind=intent_kind,
        version=int(previous or 0) + 1,
        content=content,
        content_digest=fingerprint(content),
        requested_at=requested_at,
    )
    db.add(row)
    db.flush()
    return row


def _latest_intent(
    db: Session, tenant_id: UUID, service_id: UUID, kind: str
) -> DomainIntent | None:
    return db.scalar(
        select(DomainIntent)
        .where(
            DomainIntent.tenant_id == tenant_id,
            DomainIntent.domain_service_id == service_id,
            DomainIntent.intent_kind == kind,
        )
        .order_by(DomainIntent.version.desc())
        .limit(1)
    )


def _relevant_registrar_observation(
    db: Session,
    *,
    tenant_id: UUID,
    service: DomainService,
    as_of: datetime,
) -> DomainObservation | None:
    statement = select(DomainObservation).where(
        DomainObservation.tenant_id == tenant_id,
        DomainObservation.registered_name == service.registered_name,
        or_(
            DomainObservation.domain_service_id == service.id,
            (
                DomainObservation.domain_service_id.is_(None)
                & (DomainObservation.observed_at >= service.state_effective_at)
            ),
        ),
        DomainObservation.observed_at <= as_of,
        DomainObservation.received_at <= as_of,
    )
    if service.registrar_binding_ref is not None:
        statement = statement.where(
            DomainObservation.capability_binding_ref
            == service.registrar_binding_ref
        )
    return db.scalar(
        statement.order_by(
            DomainObservation.observed_at.desc(),
            DomainObservation.received_at.desc(),
            DomainObservation.id.desc(),
        ).limit(1)
    )


def _initial_registrar_binding_observation(
    db: Session,
    *,
    tenant_id: UUID,
    service: DomainService,
    as_of: datetime,
) -> DomainObservation | None:
    """Select the first acquisition confirmation, never a later competing fact."""

    return db.scalar(
        select(DomainObservation)
        .where(
            DomainObservation.tenant_id == tenant_id,
            DomainObservation.registered_name == service.registered_name,
            DomainObservation.observation_kind == "registered",
            or_(
                DomainObservation.domain_service_id == service.id,
                (
                    DomainObservation.domain_service_id.is_(None)
                    & (
                        DomainObservation.observed_at
                        >= service.state_effective_at
                    )
                ),
            ),
            DomainObservation.observed_at <= as_of,
            DomainObservation.received_at <= as_of,
        )
        .order_by(
            DomainObservation.observed_at.asc(),
            DomainObservation.received_at.asc(),
            DomainObservation.id.asc(),
        )
        .limit(1)
    )


def _relevant_dns_observation(
    db: Session,
    *,
    tenant_id: UUID,
    service: DomainService,
    as_of: datetime,
) -> DNSObservation | None:
    statement = select(DNSObservation).where(
        DNSObservation.tenant_id == tenant_id,
        DNSObservation.zone_name == service.registered_name,
        or_(
            DNSObservation.domain_service_id == service.id,
            (
                DNSObservation.domain_service_id.is_(None)
                & (DNSObservation.observed_at >= service.state_effective_at)
            ),
        ),
        DNSObservation.observed_at <= as_of,
        DNSObservation.received_at <= as_of,
    )
    if service.dns_binding_ref is not None:
        statement = statement.where(
            DNSObservation.capability_binding_ref == service.dns_binding_ref
        )
    return db.scalar(
        statement.order_by(
            DNSObservation.observed_at.desc(),
            DNSObservation.received_at.desc(),
            DNSObservation.id.desc(),
        ).limit(1)
    )


def _initial_dns_binding_observation(
    db: Session,
    *,
    tenant_id: UUID,
    service: DomainService,
    as_of: datetime,
) -> DNSObservation | None:
    return db.scalar(
        select(DNSObservation)
        .where(
            DNSObservation.tenant_id == tenant_id,
            DNSObservation.zone_name == service.registered_name,
            or_(
                DNSObservation.domain_service_id == service.id,
                (
                    DNSObservation.domain_service_id.is_(None)
                    & (
                        DNSObservation.observed_at
                        >= service.state_effective_at
                    )
                ),
            ),
            DNSObservation.observed_at <= as_of,
            DNSObservation.received_at <= as_of,
        )
        .order_by(
            DNSObservation.observed_at.asc(),
            DNSObservation.received_at.asc(),
            DNSObservation.id.asc(),
        )
        .limit(1)
    )


def _renewal_poll_observation(
    db: Session,
    *,
    tenant_id: UUID,
    service: DomainService,
    observation_id: UUID,
    requested_at: datetime,
) -> DomainObservation:
    if service.registrar_binding_ref is None:
        raise StaleRegistrarObservation(
            "renewal requires an active registrar capability binding"
        )
    observation = db.scalar(
        select(DomainObservation).where(
            DomainObservation.tenant_id == tenant_id,
            DomainObservation.id == observation_id,
        )
    )
    if observation is None:
        raise StaleRegistrarObservation("renewal poll observation does not exist")
    if (
        observation.registered_name != service.registered_name
        or (
            observation.domain_service_id is not None
            and observation.domain_service_id != service.id
        )
    ):
        raise StaleRegistrarObservation(
            "renewal poll observation belongs to another domain"
        )
    if observation.capability_binding_ref != service.registrar_binding_ref:
        raise StaleRegistrarObservation(
            "renewal poll observation is not from the active registrar binding"
        )
    if observation.source_mode != "poll":
        raise StaleRegistrarObservation("renewal requires a registrar POLL fact")
    if (
        observation.observed_at > requested_at
        or observation.received_at > requested_at
    ):
        raise StaleRegistrarObservation("renewal poll observation is future-dated")
    if (
        requested_at - observation.observed_at > RENEWAL_POLL_MAX_AGE
        or requested_at - observation.received_at > RENEWAL_POLL_MAX_AGE
    ):
        raise StaleRegistrarObservation(
            "renewal poll observation is older than the owner safety window"
        )
    if observation.expires_at is None or observation.observation_kind not in {
        "registered",
        "renewed",
        "expiry_observed",
    }:
        raise StaleRegistrarObservation(
            "renewal poll observation does not prove registrar expiry"
        )
    latest_poll = db.scalar(
        select(DomainObservation)
        .where(
            DomainObservation.tenant_id == tenant_id,
            DomainObservation.registered_name == service.registered_name,
            DomainObservation.capability_binding_ref
            == service.registrar_binding_ref,
            DomainObservation.source_mode == "poll",
            or_(
                DomainObservation.domain_service_id == service.id,
                (
                    DomainObservation.domain_service_id.is_(None)
                    & (
                        DomainObservation.observed_at
                        >= service.state_effective_at
                    )
                ),
            ),
            DomainObservation.observed_at <= requested_at,
            DomainObservation.received_at <= requested_at,
        )
        .order_by(
            DomainObservation.observed_at.desc(),
            DomainObservation.received_at.desc(),
            DomainObservation.id.desc(),
        )
        .limit(1)
    )
    if latest_poll is None or latest_poll.id != observation.id:
        raise StaleRegistrarObservation(
            "renewal must name the latest relevant registrar POLL fact"
        )
    return observation


def _emit(
    db: Session,
    *,
    tenant_id: UUID,
    event_type: str,
    payload: dict[str, object],
    correlation_id: str | None,
) -> None:
    enqueue_event(
        db,
        tenant_id=tenant_id,
        event_type=event_type,
        payload=payload,
        correlation_id=correlation_id,
    )


def _write_domain_audit(
    db: Session,
    *,
    tenant_id: UUID,
    actor: Actor,
    action: str,
    entity_id: UUID,
    occurred_at: datetime,
    details: dict[str, object],
) -> None:
    """Keep the actor kind literal at the forensic writer boundary."""

    if actor.actor_type == "user":
        write_audit_event(
            db,
            tenant_id=tenant_id,
            actor_type="user",
            actor_id=str(actor.actor_party_id),
            actor_label=actor.actor_label,
            actor_party_id=actor.actor_party_id,
            action=action,
            entity_type="domain_service",
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
            entity_type="domain_service",
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
            entity_type="domain_service",
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
            entity_type="domain_service",
            entity_id=str(entity_id),
            occurred_at=occurred_at,
            details=details,
        )


def _receipt(
    db: Session,
    *,
    tenant_id: UUID,
    result: dict[str, object],
    replayed: bool,
) -> DomainCommandReceipt:
    service = _domain(db, tenant_id, UUID(str(result["domain_service_id"])))
    command = _command(db, tenant_id, UUID(str(result["command_id"])))
    return DomainCommandReceipt(
        domain_service_id=service.id,
        command_id=command.id,
        command_kind=DomainCommandKind(command.command_kind),
        lifecycle_state=DomainLifecycleState(service.lifecycle_state),
        replayed=replayed,
    )


def request_registration(
    db: Session,
    *,
    tenant_id: UUID,
    command: RegisterDomain,
    idempotency_key: str,
    idempotency_expires_at: datetime,
    correlation_id: str | None = None,
) -> DomainCommandReceipt:
    _aware("idempotency_expires_at", idempotency_expires_at)
    request_fingerprint = fingerprint_of(command)

    def operation(session: Session) -> dict[str, object]:
        existing = session.scalar(
            select(DomainService).where(
                DomainService.tenant_id == tenant_id,
                DomainService.registered_name == command.name,
                DomainService.lifecycle_state.not_in(_INACTIVE_NAME_STATES),
            )
        )
        if existing is not None:
            raise DomainAlreadyExists(f"domain {command.name!r} already exists")
        service = DomainService(
            tenant_id=tenant_id,
            registered_name=command.name,
            lifecycle_state=DomainLifecycleState.REGISTRATION_REQUESTED.value,
            state_effective_at=command.requested_at,
            order_line_ref=command.order_line_ref,
            offer_version_ref=command.offer_version_ref,
            commercial_renewal_at=command.commercial_renewal_at,
            row_version=0,
            created_at=command.requested_at,
            updated_at=command.requested_at,
        )
        session.add(service)
        session.flush()
        _append_intent(
            session,
            tenant_id=tenant_id,
            service=service,
            intent_kind="contacts",
            content={
                "contact_set": command.contact_set.to_payload(),
                "contact_content_digest": (
                    command.contact_set.contact_content_digest
                ),
                "provenance_digest": command.contact_set.provenance_digest,
            },
            requested_at=command.requested_at,
        )
        _append_intent(
            session,
            tenant_id=tenant_id,
            service=service,
            intent_kind="nameservers",
            content={"nameservers": list(command.nameservers)},
            requested_at=command.requested_at,
        )
        row = _new_command(
            session,
            tenant_id=tenant_id,
            service=service,
            kind=DomainCommandKind.REGISTRATION,
            idempotency_scope="domains.registration",
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            payload=_payload(command),
            requested_at=command.requested_at,
            correlation_id=correlation_id,
        )
        provider_request = RegisterDomainV1(
            operation_reference=str(row.id),
            name=service.registered_name,
            term_months=command.term_months,
            contact_set=command.contact_set,
            nameservers=command.nameservers,
            privacy_requested=command.privacy_requested,
        )
        _emit(
            session,
            tenant_id=tenant_id,
            event_type=REGISTRATION_REQUESTED_EVENT,
            correlation_id=correlation_id,
            payload=provider_request.to_payload(),
        )
        _emit(
            session,
            tenant_id=tenant_id,
            event_type=RENEWAL_SCHEDULE_CHANGED_EVENT,
            correlation_id=correlation_id,
            payload={
                "domain_service_id": str(service.id),
                "commercial_renewal_at": command.commercial_renewal_at.isoformat(),
                "source_version": service.row_version,
            },
        )
        return {"domain_service_id": str(service.id), "command_id": str(row.id)}

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="domains.registration",
        key=idempotency_key,
        fingerprint=request_fingerprint,
        operation=operation,
        operation_name="domains.request_registration",
        correlation_id=correlation_id,
        expires_at=idempotency_expires_at,
    )
    return _receipt(
        db, tenant_id=tenant_id, result=dict(outcome.result), replayed=outcome.replayed
    )


def request_renewal(
    db: Session,
    *,
    tenant_id: UUID,
    command: RenewDomain,
    idempotency_key: str,
    idempotency_expires_at: datetime,
    correlation_id: str | None = None,
) -> DomainCommandReceipt:
    _aware("idempotency_expires_at", idempotency_expires_at)
    request_fingerprint = fingerprint_of(command)
    service = _domain(db, tenant_id, command.domain_service_id, lock=True)

    def operation(session: Session) -> dict[str, object]:
        if service.lifecycle_state not in {
            DomainLifecycleState.ACTIVE.value,
            DomainLifecycleState.EXPIRED.value,
        }:
            raise InvalidDomainTransition(
                f"cannot renew a domain in state {service.lifecycle_state!r}"
            )
        poll_observation = _renewal_poll_observation(
            session,
            tenant_id=tenant_id,
            service=service,
            observation_id=command.registrar_observation_id,
            requested_at=command.requested_at,
        )
        observed_expires_at = poll_observation.expires_at
        if observed_expires_at is None:
            raise StaleRegistrarObservation(
                "renewal poll observation does not prove registrar expiry"
            )
        service.lifecycle_state = DomainLifecycleState.RENEWAL_REQUESTED.value
        service.state_effective_at = command.requested_at
        service.commercial_renewal_at = command.commercial_renewal_at
        service.row_version += 1
        service.updated_at = command.requested_at
        row = _new_command(
            session,
            tenant_id=tenant_id,
            service=service,
            kind=DomainCommandKind.RENEWAL,
            idempotency_scope="domains.renewal",
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            payload=_payload(command),
            requested_at=command.requested_at,
            correlation_id=correlation_id,
        )
        _emit(
            session,
            tenant_id=tenant_id,
            event_type=RENEWAL_REQUESTED_EVENT,
            correlation_id=correlation_id,
            payload=RenewDomainV1(
                operation_reference=str(row.id),
                name=service.registered_name,
                term_months=command.term_months,
                observed_expires_at=observed_expires_at,
            ).to_payload(),
        )
        _emit(
            session,
            tenant_id=tenant_id,
            event_type=RENEWAL_SCHEDULE_CHANGED_EVENT,
            correlation_id=correlation_id,
            payload={
                "domain_service_id": str(service.id),
                "commercial_renewal_at": command.commercial_renewal_at.isoformat(),
                "source_version": service.row_version,
            },
        )
        return {"domain_service_id": str(service.id), "command_id": str(row.id)}

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="domains.renewal",
        key=idempotency_key,
        fingerprint=request_fingerprint,
        operation=operation,
        operation_name="domains.request_renewal",
        correlation_id=correlation_id,
        expires_at=idempotency_expires_at,
    )
    return _receipt(
        db, tenant_id=tenant_id, result=dict(outcome.result), replayed=outcome.replayed
    )


def set_domain_intent(
    db: Session,
    *,
    tenant_id: UUID,
    command: SetDomainIntent,
    idempotency_key: str,
    idempotency_expires_at: datetime,
    correlation_id: str | None = None,
) -> DomainCommandReceipt:
    _aware("idempotency_expires_at", idempotency_expires_at)
    request_fingerprint = fingerprint_of(command)
    service = _domain(db, tenant_id, command.domain_service_id, lock=True)
    event_by_kind = {
        "contacts": CONTACTS_REQUESTED_EVENT,
        "nameservers": NAMESERVERS_REQUESTED_EVENT,
        "dns_zone": DNS_ZONE_REQUESTED_EVENT,
        "dns_recordset": DNS_RECORDSET_REQUESTED_EVENT,
    }
    command_kind_by_intent = {
        "contacts": DomainCommandKind.CONTACTS,
        "nameservers": DomainCommandKind.NAMESERVERS,
        "dns_zone": DomainCommandKind.DNS_ZONE,
        "dns_recordset": DomainCommandKind.DNS_RECORDSET,
    }

    def operation(session: Session) -> dict[str, object]:
        current_state = DomainLifecycleState(service.lifecycle_state)
        if current_state in _TERMINAL_INTENT_STATES:
            raise InvalidDomainTransition(
                f"cannot change desired state for terminal domain {current_state.value!r}"
            )
        service.row_version += 1
        service.updated_at = command.requested_at
        _append_intent(
            session,
            tenant_id=tenant_id,
            service=service,
            intent_kind=command.intent_kind,
            content=dict(command.content),
            requested_at=command.requested_at,
        )
        row = _new_command(
            session,
            tenant_id=tenant_id,
            service=service,
            kind=command_kind_by_intent[command.intent_kind],
            idempotency_scope=f"domains.intent.{command.intent_kind}",
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            payload=_payload(command),
            requested_at=command.requested_at,
            correlation_id=correlation_id,
        )
        if isinstance(command.intent, DomainContactsIntent):
            provider_payload = UpdateDomainContactsV1(
                operation_reference=str(row.id),
                name=service.registered_name,
                contact_set=command.intent.contact_set,
            ).to_payload()
        elif isinstance(command.intent, DomainNameserversIntent):
            provider_payload = UpdateDomainNameserversV1(
                operation_reference=str(row.id),
                name=service.registered_name,
                nameservers=command.intent.nameservers,
            ).to_payload()
        elif isinstance(command.intent, DomainDNSZoneIntent):
            provider_payload = ConfigureDNSZoneV1(
                operation_reference=str(row.id),
                zone_name=service.registered_name,
                nameservers=command.intent.nameservers,
            ).to_payload()
        elif isinstance(command.intent, DomainDNSRecordsetsIntent):
            provider_payload = ApplyDNSRecordSetsV1(
                operation_reference=str(row.id),
                zone_name=service.registered_name,
                recordsets=command.intent.recordsets,
            ).to_payload()
        else:
            raise DomainError("unsupported domain intent contract")
        _emit(
            session,
            tenant_id=tenant_id,
            event_type=event_by_kind[command.intent_kind],
            correlation_id=correlation_id,
            payload=provider_payload,
        )
        return {"domain_service_id": str(service.id), "command_id": str(row.id)}

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope=f"domains.intent.{command.intent_kind}",
        key=idempotency_key,
        fingerprint=request_fingerprint,
        operation=operation,
        operation_name="domains.set_domain_intent",
        correlation_id=correlation_id,
        expires_at=idempotency_expires_at,
    )
    return _receipt(
        db, tenant_id=tenant_id, result=dict(outcome.result), replayed=outcome.replayed
    )


def request_transfer(
    db: Session,
    *,
    tenant_id: UUID,
    command: RequestTransferDomain,
    idempotency_key: str,
    idempotency_expires_at: datetime,
    actor: Actor,
    correlation_id: str | None = None,
) -> DomainCommandReceipt:
    _aware("idempotency_expires_at", idempotency_expires_at)
    request_fingerprint = fingerprint_of(command)
    service = _domain(db, tenant_id, command.domain_service_id, lock=True)

    def operation(session: Session) -> dict[str, object]:
        if command.direction is TransferDirection.APPROVE_OUT:
            kind = DomainCommandKind.TRANSFER_OUT
            next_state = DomainLifecycleState.TRANSFER_OUT_REQUESTED
            _assert_destructive_permission(
                session,
                tenant_id=tenant_id,
                service=service,
                expected_version=command.expected_version,
                approval=command.approval,
                content_digest=transfer_out_content_digest(
                    service.registered_name, command
                ),
                expected_policy_code="domains.transfer_out",
                action_at=command.requested_at,
            )
            if service.lifecycle_state != DomainLifecycleState.ACTIVE.value:
                raise InvalidDomainTransition(
                    f"cannot transfer out a domain in state {service.lifecycle_state!r}"
                )
        elif command.direction is TransferDirection.CANCEL:
            kind = DomainCommandKind.TRANSFER_CANCEL
            if service.lifecycle_state != DomainLifecycleState.TRANSFER_OUT_REQUESTED.value:
                raise InvalidDomainTransition("no transfer is in flight")
            next_state = DomainLifecycleState(service.lifecycle_state)
        else:
            raise InvalidDomainTransition(f"unsupported transfer {command.direction!r}")
        service.lifecycle_state = next_state.value
        if command.direction is not TransferDirection.CANCEL:
            service.state_effective_at = command.requested_at
        service.row_version += 1
        service.updated_at = command.requested_at
        row = _new_command(
            session,
            tenant_id=tenant_id,
            service=service,
            kind=kind,
            idempotency_scope="domains.transfer",
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            payload=_payload(command),
            requested_at=command.requested_at,
            correlation_id=correlation_id,
        )
        if command.direction is TransferDirection.APPROVE_OUT:
            _write_domain_audit(
                session,
                tenant_id=tenant_id,
                actor=actor,
                action=TRANSFER_OUT_AUDIT_ACTION,
                entity_id=service.id,
                occurred_at=command.requested_at,
                details={"name": service.registered_name, "command_id": str(row.id)},
            )
        _emit(
            session,
            tenant_id=tenant_id,
            event_type=TRANSFER_REQUESTED_EVENT,
            correlation_id=correlation_id,
            payload=TransferDomainV1(
                operation_reference=str(row.id),
                name=service.registered_name,
                direction=command.direction,
            ).to_payload(),
        )
        return {"domain_service_id": str(service.id), "command_id": str(row.id)}

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="domains.transfer",
        key=idempotency_key,
        fingerprint=request_fingerprint,
        operation=operation,
        operation_name="domains.request_transfer",
        correlation_id=correlation_id,
        expires_at=idempotency_expires_at,
    )
    return _receipt(
        db, tenant_id=tenant_id, result=dict(outcome.result), replayed=outcome.replayed
    )


def _assert_destructive_permission(
    db: Session,
    *,
    tenant_id: UUID,
    service: DomainService,
    expected_version: int | None,
    approval: ApprovalReceipt | None,
    content_digest: str,
    expected_policy_code: str,
    action_at: datetime,
) -> ApprovalReceipt:
    if expected_version is None or expected_version != service.row_version:
        raise StaleDomainVersion("the approved domain version is stale")
    if approval is None:
        raise ReleaseNotPermitted("destructive domain action requires approval")
    if (
        approval.decision is not ApprovalDecision.APPROVED
        or approval.content_digest != content_digest
        or approval.policy_code != expected_policy_code
        or approval.decided_at > action_at
    ):
        raise ReleaseNotPermitted("approval does not authorize this exact content")
    active_hold = db.scalar(
        select(DomainHold.id).where(
            DomainHold.tenant_id == tenant_id,
            DomainHold.domain_service_id == service.id,
            DomainHold.cleared_at.is_(None),
        )
    )
    if active_hold is not None:
        raise ReleaseNotPermitted("an active domain hold refuses the action")
    return approval


def receive_registrar_observation(
    db: Session,
    *,
    envelope: CommandEnvelope,
    observation: DomainObservationV1,
    received_at: datetime,
) -> ObservationReceipt:
    _aware("received_at", received_at)
    if observation.observed_at > received_at:
        raise DomainError("registrar observation is future-dated")
    observation_payload = _payload(observation)
    payload_digest = fingerprint(observation_payload)

    def handler(session: Session, delivered: CommandEnvelope) -> dict[str, object]:
        existing = session.scalar(
            select(DomainObservation).where(
                DomainObservation.tenant_id == delivered.tenant_id,
                DomainObservation.capability_binding_ref
                == observation.capability_binding_ref,
                DomainObservation.provider_event_id == observation.provider_event_id,
            )
        )
        if existing is not None:
            if existing.payload_digest != payload_digest:
                raise DomainError(
                    "provider event identity was reused with different observation data"
                )
            return {"observation_id": str(existing.id), "duplicate": True}
        service_id = session.scalar(
            select(DomainService.id).where(
                DomainService.tenant_id == delivered.tenant_id,
                DomainService.registered_name == observation.name,
                DomainService.lifecycle_state.not_in(_INACTIVE_NAME_STATES),
            )
        )
        row = DomainObservation(
            tenant_id=delivered.tenant_id,
            domain_service_id=service_id,
            registered_name=observation.name,
            capability_binding_ref=observation.capability_binding_ref,
            provider_event_id=observation.provider_event_id,
            observation_kind=observation.observation_kind,
            provider_statuses=list(observation.provider_statuses),
            expires_at=observation.expires_at,
            redemption_ends_at=observation.redemption_ends_at,
            observed_nameservers=list(observation.nameservers),
            observed_contact_digest=observation.contact_set_digest,
            source_mode=observation.source_mode,
            payload_digest=payload_digest,
            observed_at=observation.observed_at,
            received_at=received_at,
        )
        session.add(row)
        session.flush()
        return {"observation_id": str(row.id), "duplicate": False}

    outcome = process_once(db, envelope, handler)
    return ObservationReceipt(
        observation_id=UUID(str(outcome.result["observation_id"])),
        name=observation.name,
        duplicate=outcome.was_duplicate or bool(outcome.result.get("duplicate")),
    )


def receive_dns_observation(
    db: Session,
    *,
    envelope: CommandEnvelope,
    observation: DNSObservationV1,
    received_at: datetime,
) -> ObservationReceipt:
    """Persist immutable DNS facts; only reconciliation interprets them."""

    _aware("received_at", received_at)
    if observation.observed_at > received_at:
        raise DomainError("DNS observation is future-dated")
    observation_payload = _payload(observation)
    payload_digest = fingerprint(observation_payload)

    def handler(session: Session, delivered: CommandEnvelope) -> dict[str, object]:
        existing = session.scalar(
            select(DNSObservation).where(
                DNSObservation.tenant_id == delivered.tenant_id,
                DNSObservation.capability_binding_ref
                == observation.capability_binding_ref,
                DNSObservation.provider_event_id == observation.provider_event_id,
            )
        )
        if existing is not None:
            if existing.payload_digest != payload_digest:
                raise DomainError(
                    "DNS provider event identity was reused with different data"
                )
            return {"observation_id": str(existing.id), "duplicate": True}
        service_id = session.scalar(
            select(DomainService.id).where(
                DomainService.tenant_id == delivered.tenant_id,
                DomainService.registered_name == observation.zone_name,
                DomainService.lifecycle_state.not_in(_INACTIVE_NAME_STATES),
            )
        )
        row = DNSObservation(
            tenant_id=delivered.tenant_id,
            domain_service_id=service_id,
            zone_name=observation.zone_name,
            capability_binding_ref=observation.capability_binding_ref,
            provider_event_id=observation.provider_event_id,
            observed_nameservers=list(observation.nameservers),
            observed_recordsets=[item.to_payload() for item in observation.recordsets],
            observed_recordsets_digest=observation.recordsets_digest,
            source_mode=observation.source_mode,
            payload_digest=payload_digest,
            observed_at=observation.observed_at,
            received_at=received_at,
        )
        session.add(row)
        session.flush()
        return {"observation_id": str(row.id), "duplicate": False}

    processed = process_once(db, envelope, handler)
    return ObservationReceipt(
        observation_id=UUID(str(processed.result["observation_id"])),
        name=observation.zone_name,
        duplicate=processed.was_duplicate
        or bool(processed.result.get("duplicate")),
    )


def record_registrar_outcome(
    db: Session,
    *,
    envelope: CommandEnvelope,
    outcome: RecordRegistrarOutcome,
    received_at: datetime,
) -> UUID:
    _aware("received_at", received_at)
    if outcome.occurred_at > received_at:
        raise DomainError("provider outcome is future-dated")
    outcome_payload_digest = fingerprint(_payload(outcome))

    def handler(session: Session, delivered: CommandEnvelope) -> dict[str, object]:
        command = _command(session, delivered.tenant_id, outcome.domain_command_id)
        existing = session.scalar(
            select(DomainCommandOutcome).where(
                DomainCommandOutcome.tenant_id == delivered.tenant_id,
                DomainCommandOutcome.domain_command_id == command.id,
                DomainCommandOutcome.evidence_key == outcome.evidence_key,
            )
        )
        if existing is not None:
            if existing.payload_digest != outcome_payload_digest:
                raise DomainError(
                    "provider outcome evidence key was reused with different data"
                )
            return {"outcome_id": str(existing.id)}
        row = DomainCommandOutcome(
            tenant_id=delivered.tenant_id,
            domain_service_id=command.domain_service_id,
            domain_command_id=command.id,
            evidence_key=outcome.evidence_key,
            outcome_kind=outcome.outcome_kind.value,
            outcome_class=outcome.outcome_class.value,
            provider_reference=outcome.provider_reference,
            reason_code=outcome.reason_code,
            details=dict(outcome.details),
            payload_digest=outcome_payload_digest,
            occurred_at=outcome.occurred_at,
            recorded_at=received_at,
        )
        session.add(row)
        session.flush()
        if (
            command.command_kind == DomainCommandKind.RENEWAL.value
            and outcome.outcome_kind is OutcomeKind.FAILED
        ):
            _open_attention(
                session,
                tenant_id=delivered.tenant_id,
                service_id=command.domain_service_id,
                command_id=command.id,
                condition_code="paid_renewal_failed",
                reason_code=outcome.reason_code or "registrar_renewal_failed",
                opened_at=received_at,
                details={
                    "outcome_class": outcome.outcome_class.value,
                    "evidence_key": outcome.evidence_key,
                },
                correlation_id=delivered.correlation_id,
            )
        if (
            outcome.outcome_kind is OutcomeKind.FAILED
            and outcome.outcome_class is OutcomeClass.TERMINAL
            and command.command_kind
            in {
                DomainCommandKind.REGISTRATION.value,
                DomainCommandKind.TRANSFER_OUT.value,
                DomainCommandKind.TRANSFER_CANCEL.value,
            }
        ):
            service = _domain(
                session,
                delivered.tenant_id,
                command.domain_service_id,
                lock=True,
            )
            previous_state = DomainLifecycleState(service.lifecycle_state)
            next_state = previous_state
            if (
                command.command_kind == DomainCommandKind.REGISTRATION.value
                and previous_state is DomainLifecycleState.REGISTRATION_REQUESTED
            ):
                next_state = DomainLifecycleState.REGISTRATION_FAILED
            elif (
                command.command_kind == DomainCommandKind.TRANSFER_OUT.value
                and previous_state is DomainLifecycleState.TRANSFER_OUT_REQUESTED
            ):
                next_state = DomainLifecycleState.ACTIVE
            if next_state is not previous_state:
                service.lifecycle_state = next_state.value
                service.state_effective_at = outcome.occurred_at
                service.row_version += 1
                service.updated_at = received_at
                _emit(
                    session,
                    tenant_id=delivered.tenant_id,
                    event_type=LIFECYCLE_CHANGED_EVENT,
                    correlation_id=delivered.correlation_id,
                    payload={
                        "domain_service_id": str(service.id),
                        "name": service.registered_name,
                        "previous_state": previous_state.value,
                        "current_state": next_state.value,
                        "reason_code": outcome.reason_code
                        or "terminal_provider_failure",
                        "outcome_id": str(row.id),
                        "row_version": service.row_version,
                    },
                )
            _emit(
                session,
                tenant_id=delivered.tenant_id,
                event_type=PROVIDER_COMMAND_FAILED_EVENT,
                correlation_id=delivered.correlation_id,
                payload={
                    "domain_service_id": str(service.id),
                    "command_id": str(command.id),
                    "command_kind": command.command_kind,
                    "outcome_id": str(row.id),
                    "classification": outcome.outcome_class.value,
                    "reason_code": outcome.reason_code
                    or "terminal_provider_failure",
                    "repair_state": next_state.value,
                },
            )
            _open_attention(
                session,
                tenant_id=delivered.tenant_id,
                service_id=command.domain_service_id,
                command_id=command.id,
                condition_code=f"terminal_{command.command_kind}_failure",
                reason_code=outcome.reason_code or "terminal_provider_failure",
                opened_at=received_at,
                details={
                    "outcome_id": str(row.id),
                    "evidence_key": outcome.evidence_key,
                    "repair_state": next_state.value,
                },
                correlation_id=delivered.correlation_id,
                classification=OutcomeClass.TERMINAL,
            )
        return {"outcome_id": str(row.id)}

    processed = process_once(db, envelope, handler)
    return UUID(str(processed.result["outcome_id"]))


def _open_attention(
    db: Session,
    *,
    tenant_id: UUID,
    service_id: UUID,
    command_id: UUID | None,
    condition_code: str,
    reason_code: str,
    opened_at: datetime,
    details: dict[str, object],
    correlation_id: str | None,
    classification: OutcomeClass = OutcomeClass.RECONCILIATION_REQUIRED,
) -> DomainAttentionCondition:
    existing = db.scalar(
        select(DomainAttentionCondition).where(
            DomainAttentionCondition.tenant_id == tenant_id,
            DomainAttentionCondition.domain_service_id == service_id,
            DomainAttentionCondition.condition_code == condition_code,
            DomainAttentionCondition.resolved_at.is_(None),
        )
    )
    if existing is not None:
        return existing
    row = DomainAttentionCondition(
        tenant_id=tenant_id,
        domain_service_id=service_id,
        source_command_id=command_id,
        condition_code=condition_code,
        classification=classification.value,
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
        correlation_id=correlation_id,
        payload={
            "attention_id": str(row.id),
            "domain_service_id": str(service_id),
            "condition_code": condition_code,
            "classification": row.classification,
            "reason_code": reason_code,
        },
    )
    return row


def _desired_nameservers(intent: DomainIntent | None) -> tuple[str, ...]:
    if intent is None:
        return ()
    values = intent.content.get("nameservers", [])
    if not isinstance(values, list):
        return ()
    return tuple(str(item) for item in values)


def _desired_contact_digest(intent: DomainIntent | None) -> str | None:
    if intent is None:
        return None
    digest = intent.content.get("contact_content_digest")
    if isinstance(digest, str):
        return digest
    return None


def _desired_recordsets_digest(intent: DomainIntent | None) -> str | None:
    if intent is None:
        return None
    digest = intent.content.get("recordsets_digest")
    return digest if isinstance(digest, str) else None


def reconcile_domain(
    db: Session,
    *,
    tenant_id: UUID,
    domain_service_id: UUID,
    reconciled_at: datetime,
    correlation_id: str | None = None,
) -> ReconciliationResult:
    _aware("reconciled_at", reconciled_at)
    service = _domain(db, tenant_id, domain_service_id, lock=True)
    previous = DomainLifecycleState(service.lifecycle_state)
    observation = (
        _initial_registrar_binding_observation(
            db, tenant_id=tenant_id, service=service, as_of=reconciled_at
        )
        if service.registrar_binding_ref is None
        else _relevant_registrar_observation(
            db, tenant_id=tenant_id, service=service, as_of=reconciled_at
        )
    )
    dns_observation = (
        _initial_dns_binding_observation(
            db, tenant_id=tenant_id, service=service, as_of=reconciled_at
        )
        if service.dns_binding_ref is None
        else _relevant_dns_observation(
            db, tenant_id=tenant_id, service=service, as_of=reconciled_at
        )
    )
    decision = decide_lifecycle_transition(
        LifecycleInput(
            current_state=previous,
            state_effective_at=_as_utc(service.state_effective_at),
            observation_kind=(observation.observation_kind if observation else None),
            observation_observed_at=(
                _as_utc(observation.observed_at) if observation else None
            ),
            registrar_expires_at=(
                _as_utc(observation.expires_at)
                if observation is not None and observation.expires_at is not None
                else None
            ),
            reconciled_at=reconciled_at,
        )
    )
    owner_changed = decision.changed
    if service.registrar_binding_ref is None and observation is not None:
        service.registrar_binding_ref = observation.capability_binding_ref
        owner_changed = True
    if service.dns_binding_ref is None and dns_observation is not None:
        service.dns_binding_ref = dns_observation.capability_binding_ref
        owner_changed = True
    if decision.changed:
        service.lifecycle_state = decision.next_state.value
        service.state_effective_at = decision.effective_at
    if owner_changed:
        service.row_version += 1
        service.updated_at = reconciled_at
    if decision.changed:
        _emit(
            db,
            tenant_id=tenant_id,
            event_type=LIFECYCLE_CHANGED_EVENT,
            correlation_id=correlation_id,
            payload={
                "domain_service_id": str(service.id),
                "name": service.registered_name,
                "previous_state": previous.value,
                "current_state": decision.next_state.value,
                "reason_code": decision.reason_code,
                "observation_id": str(observation.id) if observation else None,
                "row_version": service.row_version,
            },
        )
    if (
        observation is not None
        and observation.observation_kind == "renewed"
        and decision.next_state is DomainLifecycleState.ACTIVE
    ):
        attention = db.scalar(
            select(DomainAttentionCondition).where(
                DomainAttentionCondition.tenant_id == tenant_id,
                DomainAttentionCondition.domain_service_id == service.id,
                DomainAttentionCondition.condition_code == "paid_renewal_failed",
                DomainAttentionCondition.resolved_at.is_(None),
            )
        )
        if attention is not None:
            attention.resolved_at = reconciled_at
            attention.resolution_code = "confirmed_renewal_observation"
    if observation is not None and observation.observation_kind == "transfer_rejected":
        _open_attention(
            db,
            tenant_id=tenant_id,
            service_id=service.id,
            command_id=None,
            condition_code="transfer_rejected",
            reason_code="registrar_transfer_rejected",
            opened_at=reconciled_at,
            details={"observation_id": str(observation.id)},
            correlation_id=correlation_id,
        )
    if (
        observation is not None
        and observation.observation_kind == "deleted"
        and decision.next_state is not DomainLifecycleState.RELEASED
    ):
        _open_attention(
            db,
            tenant_id=tenant_id,
            service_id=service.id,
            command_id=None,
            condition_code="unexpected_provider_deletion",
            reason_code="deleted_without_expiry_or_redemption",
            opened_at=reconciled_at,
            details={"observation_id": str(observation.id)},
            correlation_id=correlation_id,
        )

    nameserver_intent = _latest_intent(db, tenant_id, service.id, "nameservers")
    contact_intent = _latest_intent(db, tenant_id, service.id, "contacts")
    dns_zone_intent = _latest_intent(db, tenant_id, service.id, "dns_zone")
    dns_recordsets_intent = _latest_intent(
        db, tenant_id, service.id, "dns_recordset"
    )
    drift = derive_drift(
        commercial_renewal_at=(
            _as_utc(service.commercial_renewal_at)
            if service.commercial_renewal_at is not None
            else None
        ),
        registrar_expires_at=(
            _as_utc(observation.expires_at)
            if observation is not None and observation.expires_at is not None
            else None
        ),
        desired_nameservers=_desired_nameservers(nameserver_intent),
        observed_nameservers=(
            tuple(observation.observed_nameservers) if observation else ()
        ),
        desired_contact_digest=_desired_contact_digest(contact_intent),
        observed_contact_digest=(
            observation.observed_contact_digest if observation else None
        ),
        desired_dns_nameservers=_desired_nameservers(dns_zone_intent),
        observed_dns_nameservers=(
            tuple(dns_observation.observed_nameservers) if dns_observation else ()
        ),
        desired_dns_recordsets_digest=_desired_recordsets_digest(
            dns_recordsets_intent
        ),
        observed_dns_recordsets_digest=(
            dns_observation.observed_recordsets_digest
            if dns_observation
            else None
        ),
    )
    db.flush()
    return ReconciliationResult(
        domain_service_id=service.id,
        previous_state=previous,
        current_state=DomainLifecycleState(service.lifecycle_state),
        observation_id=observation.id if observation else None,
        dns_observation_id=dns_observation.id if dns_observation else None,
        changed=owner_changed,
        drift=drift,
    )


def apply_consequence_request(
    db: Session,
    *,
    tenant_id: UUID,
    command: ConsequenceRequest,
    idempotency_key: str,
    idempotency_expires_at: datetime,
    actor: Actor,
    correlation_id: str | None = None,
) -> ConsequenceOutcome:
    _aware("idempotency_expires_at", idempotency_expires_at)
    request_fingerprint = fingerprint_of(command)
    service = _domain(db, tenant_id, command.domain_service_id, lock=True)

    def operation(session: Session) -> dict[str, object]:
        command_row = _new_command(
            session,
            tenant_id=tenant_id,
            service=service,
            kind=DomainCommandKind.CONSEQUENCE,
            idempotency_scope="domains.consequence",
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            payload=_payload(command),
            requested_at=command.requested_at,
            correlation_id=correlation_id,
        )
        decision = "refused"
        reason = "destructive_consequence_requires_domain_approval"
        if command.consequence_kind in {"renewal_review", "transfer_hold"}:
            existing = session.scalar(
                select(DomainHold).where(
                    DomainHold.tenant_id == tenant_id,
                    DomainHold.domain_service_id == service.id,
                    DomainHold.hold_code == command.consequence_kind,
                    DomainHold.source_owner == command.source_owner,
                    DomainHold.source_reference == command.source_reference,
                    DomainHold.cleared_at.is_(None),
                )
            )
            if existing is None:
                session.add(
                    DomainHold(
                        tenant_id=tenant_id,
                        domain_service_id=service.id,
                        hold_code=command.consequence_kind,
                        source_owner=command.source_owner,
                        source_reference=command.source_reference,
                        reason_code=command.reason_code,
                        opened_at=command.requested_at,
                    )
                )
                session.flush()
            decision = "applied"
            reason = "domain_hold_recorded"
            _write_domain_audit(
                session,
                tenant_id=tenant_id,
                actor=actor,
                action=HOLD_AUDIT_ACTION,
                entity_id=service.id,
                occurred_at=command.requested_at,
                details={
                    "hold_code": command.consequence_kind,
                    "source_owner": command.source_owner,
                    "source_reference": command.source_reference,
                },
            )
        evidence = DomainCommandOutcome(
            tenant_id=tenant_id,
            domain_service_id=service.id,
            domain_command_id=command_row.id,
            evidence_key="owner-decision",
            outcome_kind=(
                OutcomeKind.CONFIRMED.value
                if decision == "applied"
                else OutcomeKind.REFUSED.value
            ),
            outcome_class=(
                OutcomeClass.SUCCEEDED.value
                if decision == "applied"
                else OutcomeClass.TERMINAL.value
            ),
            reason_code=reason,
            details={},
            payload_digest=fingerprint(
                {
                    "evidence_key": "owner-decision",
                    "decision": decision,
                    "reason_code": reason,
                }
            ),
            occurred_at=command.requested_at,
            recorded_at=command.requested_at,
        )
        session.add(evidence)
        session.flush()
        _emit(
            session,
            tenant_id=tenant_id,
            event_type=CONSEQUENCE_DECIDED_EVENT,
            correlation_id=correlation_id,
            payload={
                "domain_service_id": str(service.id),
                "command_id": str(command_row.id),
                "consequence_kind": command.consequence_kind,
                "decision": decision,
                "reason_code": reason,
                "source_reference": command.source_reference,
            },
        )
        return {
            "domain_service_id": str(service.id),
            "command_id": str(command_row.id),
            "decision": decision,
            "reason_code": reason,
        }

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="domains.consequence",
        key=idempotency_key,
        fingerprint=request_fingerprint,
        operation=operation,
        operation_name="domains.apply_consequence_request",
        correlation_id=correlation_id,
        expires_at=idempotency_expires_at,
    )
    return ConsequenceOutcome(
        domain_service_id=UUID(str(outcome.result["domain_service_id"])),
        consequence_kind=command.consequence_kind,
        decision=str(outcome.result["decision"]),
        reason_code=str(outcome.result["reason_code"]),
        command_id=UUID(str(outcome.result["command_id"])),
        replayed=outcome.replayed,
    )


def clear_domain_hold(
    db: Session,
    *,
    tenant_id: UUID,
    command: ClearDomainHold,
    idempotency_key: str,
    idempotency_expires_at: datetime,
    actor: Actor,
    correlation_id: str | None = None,
) -> ConsequenceOutcome:
    """Clear one source-owned hold without reopening or rewriting its evidence."""

    _aware("idempotency_expires_at", idempotency_expires_at)
    request_fingerprint = fingerprint_of(command)
    service = _domain(db, tenant_id, command.domain_service_id, lock=True)

    def operation(session: Session) -> dict[str, object]:
        command_row = _new_command(
            session,
            tenant_id=tenant_id,
            service=service,
            kind=DomainCommandKind.CONSEQUENCE,
            idempotency_scope="domains.hold.clear",
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            payload=_payload(command),
            requested_at=command.requested_at,
            correlation_id=correlation_id,
        )
        hold = session.scalar(
            select(DomainHold).where(
                DomainHold.tenant_id == tenant_id,
                DomainHold.domain_service_id == service.id,
                DomainHold.hold_code == command.hold_code,
                DomainHold.source_owner == command.source_owner,
                DomainHold.source_reference == command.source_reference,
                DomainHold.cleared_at.is_(None),
            )
        )
        decision = "no_change"
        reason = "no_active_hold"
        if hold is not None:
            hold.cleared_at = command.requested_at
            hold.cleared_reason = command.reason_code
            decision = "applied"
            reason = "domain_hold_cleared"
        else:
            another_source = session.scalar(
                select(DomainHold.id).where(
                    DomainHold.tenant_id == tenant_id,
                    DomainHold.domain_service_id == service.id,
                    DomainHold.hold_code == command.hold_code,
                    DomainHold.cleared_at.is_(None),
                )
            )
            if another_source is not None:
                raise DomainError(
                    "hold identity requires the matching source owner and reference"
                )
        evidence = DomainCommandOutcome(
            tenant_id=tenant_id,
            domain_service_id=service.id,
            domain_command_id=command_row.id,
            evidence_key="owner-decision",
            outcome_kind=OutcomeKind.CONFIRMED.value,
            outcome_class=OutcomeClass.SUCCEEDED.value,
            reason_code=reason,
            details={"hold_code": command.hold_code},
            payload_digest=fingerprint(
                {
                    "evidence_key": "owner-decision",
                    "decision": decision,
                    "reason_code": reason,
                    "hold_code": command.hold_code,
                }
            ),
            occurred_at=command.requested_at,
            recorded_at=command.requested_at,
        )
        session.add(evidence)
        session.flush()
        _write_domain_audit(
            session,
            tenant_id=tenant_id,
            actor=actor,
            action=HOLD_AUDIT_ACTION,
            entity_id=service.id,
            occurred_at=command.requested_at,
            details={
                "hold_code": command.hold_code,
                "source_owner": command.source_owner,
                "source_reference": command.source_reference,
                "decision": decision,
            },
        )
        _emit(
            session,
            tenant_id=tenant_id,
            event_type=CONSEQUENCE_DECIDED_EVENT,
            correlation_id=correlation_id,
            payload={
                "domain_service_id": str(service.id),
                "command_id": str(command_row.id),
                "consequence_kind": f"clear:{command.hold_code}",
                "decision": decision,
                "reason_code": reason,
                "source_reference": command.source_reference,
            },
        )
        return {
            "domain_service_id": str(service.id),
            "command_id": str(command_row.id),
            "decision": decision,
            "reason_code": reason,
        }

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="domains.hold.clear",
        key=idempotency_key,
        fingerprint=request_fingerprint,
        operation=operation,
        operation_name="domains.clear_domain_hold",
        correlation_id=correlation_id,
        expires_at=idempotency_expires_at,
    )
    return ConsequenceOutcome(
        domain_service_id=UUID(str(outcome.result["domain_service_id"])),
        consequence_kind=f"clear:{command.hold_code}",
        decision=str(outcome.result["decision"]),
        reason_code=str(outcome.result["reason_code"]),
        command_id=UUID(str(outcome.result["command_id"])),
        replayed=outcome.replayed,
    )


__all__ = [
    "ATTENTION_REQUIRED_EVENT",
    "CommandNotFound",
    "DomainAlreadyExists",
    "DomainError",
    "DomainNotFound",
    "HOLD_AUDIT_ACTION",
    "InvalidDomainTransition",
    "LIFECYCLE_CHANGED_EVENT",
    "PUBLIC_EVENT_TYPES",
    "ReleaseNotPermitted",
    "StaleDomainVersion",
    "StaleRegistrarObservation",
    "TRANSFER_OUT_AUDIT_ACTION",
    "apply_consequence_request",
    "clear_domain_hold",
    "receive_dns_observation",
    "receive_registrar_observation",
    "reconcile_domain",
    "record_registrar_outcome",
    "request_registration",
    "request_renewal",
    "request_transfer",
    "set_domain_intent",
]
