"""Academy scheduling policy in the caller transaction, returning detached facts.

The product parses/localizes times and applies its global instructor guard before
commands. Invalid updates retain source mutations; the caller must roll back.
"""

from __future__ import annotations

from dataclasses import fields
from datetime import datetime
from uuid import UUID

from dotmac_kernel.audit import write_audit_event
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from .attempt_contracts import AttemptAuditContext
from .delivery_contracts import (
    CancelSessionCommand,
    ClassSessionFact,
    CohortDeliveryFact,
    CreateSessionCommand,
    PersonSession,
    PersonTimetable,
    SchedulingBadRequest,
    SchedulingNotFound,
    SessionChange,
    SessionUpdate,
    SetDeliveryModeCommand,
    UpdateSessionCommand,
)
from .delivery_models import SESSION_STATUSES, SESSION_TYPES, ClassSession
from .participation_models import Cohort, Enrollment
from .results import _transaction, _unchanged

DELIVERY_MODES = frozenset({"self_paced", "live", "blended"})
_TYPES = frozenset(SESSION_TYPES)
_STATUSES = frozenset(SESSION_STATUSES)


def _clean_join_url(value: str | None) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    if not value.lower().startswith(("http://", "https://")):
        raise SchedulingBadRequest("Join URL must start with http:// or https://.")
    return value


def _fact(row: ClassSession) -> ClassSessionFact:
    return ClassSessionFact(
        **{field.name: getattr(row, field.name) for field in fields(ClassSessionFact)}
    )


def _cohort(db: Session, tenant_id: UUID, cohort_id: UUID) -> Cohort:
    row = db.scalar(
        select(Cohort).where(Cohort.tenant_id == tenant_id, Cohort.id == cohort_id)
    )
    if row is None:
        raise SchedulingNotFound("Cohort not found.")
    return row


def _session(db: Session, tenant_id: UUID, session_id: UUID) -> ClassSession:
    row = db.scalar(
        select(ClassSession).where(
            ClassSession.tenant_id == tenant_id, ClassSession.id == session_id
        )
    )
    if row is None:
        raise SchedulingNotFound("Session not found.")
    return row


def set_delivery_mode(
    db: Session, payload: SetDeliveryModeCommand
) -> CohortDeliveryFact:
    boundary = _transaction(db)
    if payload.mode not in DELIVERY_MODES:
        raise SchedulingBadRequest(f"Unknown delivery mode: {payload.mode}")
    cohort = _cohort(db, payload.tenant_id, payload.cohort_id)
    cohort.delivery_mode = payload.mode
    db.flush()
    _unchanged(db, boundary)
    return CohortDeliveryFact(cohort.id, cohort.tenant_id, cohort.delivery_mode)


def create_session(db: Session, payload: CreateSessionCommand) -> ClassSessionFact:
    boundary = _transaction(db)
    if payload.session_type not in _TYPES:
        raise SchedulingBadRequest(f"Unknown session type: {payload.session_type}")
    if payload.ends_at is not None and payload.ends_at <= payload.starts_at:
        raise SchedulingBadRequest("ends_at must be after starts_at.")
    join_url = _clean_join_url(payload.join_url)
    cohort = _cohort(db, payload.tenant_id, payload.cohort_id)
    if cohort.delivery_mode == "self_paced":
        cohort.delivery_mode = "blended"
    row = ClassSession(
        **payload.model_dump(exclude={"title", "location", "join_url", "notes"}),
        title=payload.title.strip(),
        location=payload.location or None,
        join_url=join_url or None,
        notes=payload.notes or None,
        status="scheduled",
    )
    db.add(row)
    db.flush()
    _unchanged(db, boundary)
    return _fact(row)


def get_session(db: Session, *, tenant_id: UUID, session_id: UUID) -> ClassSessionFact:
    return _fact(_session(db, tenant_id, session_id))


def list_for_cohort(
    db: Session,
    *,
    tenant_id: UUID,
    cohort_id: UUID,
    upcoming_only: bool = False,
    now: datetime | None = None,
) -> tuple[ClassSessionFact, ...]:
    stmt = select(ClassSession).where(
        ClassSession.tenant_id == tenant_id, ClassSession.cohort_id == cohort_id
    )
    if upcoming_only:
        stmt = stmt.where(
            ClassSession.status == "scheduled",
            ClassSession.starts_at > (now or datetime.now(tz=None).astimezone()),
        )
    return tuple(
        _fact(row) for row in db.scalars(stmt.order_by(ClassSession.starts_at)).all()
    )


def update_session(
    db: Session,
    payload: UpdateSessionCommand,
    *,
    audit_context: AttemptAuditContext,
    actor_party_id: UUID | None = None,
) -> SessionUpdate:
    """Flush even unchanged updates; required Kernel audit follows actual changes.

    Audit failure and invalid resulting intervals propagate without undo. The
    caller owns rollback, including mutations already flushed before audit.
    """
    boundary = _transaction(db)
    if audit_context.tenant_id != payload.tenant_id:
        raise SchedulingBadRequest("Audit context belongs to another tenant.")
    row = _session(db, payload.tenant_id, payload.session_id)
    supplied = payload.model_fields_set
    if "session_type" in supplied and payload.session_type not in _TYPES:
        raise SchedulingBadRequest(f"Unknown session type: {payload.session_type}")
    if "status" in supplied and payload.status not in _STATUSES:
        raise SchedulingBadRequest(f"Unknown status: {payload.status}")
    updates: dict[str, str | datetime | None] = {}
    for key in (
        "title",
        "starts_at",
        "ends_at",
        "session_type",
        "location",
        "join_url",
        "notes",
        "status",
    ):
        if key not in supplied:
            continue
        value = getattr(payload, key)
        if key == "title":
            value = (value or "").strip()
        elif key in {"location", "notes"}:
            value = value or None
        elif key == "join_url":
            value = _clean_join_url(value)
        updates[key] = value
    changes: list[SessionChange] = []
    for key, value in updates.items():
        old = getattr(row, key)
        if old != value:
            changes.append(
                SessionChange(
                    key,
                    old.isoformat() if isinstance(old, datetime) else old,
                    value.isoformat() if isinstance(value, datetime) else value,
                )
            )
            setattr(row, key, value)
    if row.ends_at is not None and row.ends_at <= row.starts_at:
        raise SchedulingBadRequest("ends_at must be after starts_at.")
    db.flush()
    _unchanged(db, boundary)
    if changes:
        write_audit_event(
            db,
            tenant_id=payload.tenant_id,
            actor_type=audit_context.actor_type,
            actor_id=audit_context.actor_id,
            actor_label=audit_context.actor_label,
            actor_party_id=actor_party_id,
            source_application=audit_context.source_application,
            action="scheduling.update_session",
            entity_type="class_session",
            entity_id=str(row.id),
            details={
                "changes": {
                    change.field: {"from": change.from_value, "to": change.to_value}
                    for change in changes
                }
            },
        )
        _unchanged(db, boundary)
    return SessionUpdate(_fact(row), tuple(changes))


def cancel_session(db: Session, payload: CancelSessionCommand) -> ClassSessionFact:
    boundary = _transaction(db)
    row = _session(db, payload.tenant_id, payload.session_id)
    row.status = "cancelled"
    db.flush()
    _unchanged(db, boundary)
    return _fact(row)


def _person_sessions(
    tenant_id: UUID, person_id: UUID
) -> Select[tuple[ClassSession, str]]:
    return (
        select(ClassSession, Cohort.name)
        .join(
            Cohort,
            (Cohort.id == ClassSession.cohort_id)
            & (Cohort.tenant_id == ClassSession.tenant_id),
        )
        .join(
            Enrollment,
            (Enrollment.cohort_id == ClassSession.cohort_id)
            & (Enrollment.tenant_id == ClassSession.tenant_id),
        )
        .where(
            ClassSession.tenant_id == tenant_id,
            Enrollment.person_id == person_id,
            Enrollment.status == "active",
        )
    )


def list_for_person(
    db: Session, *, tenant_id: UUID, person_id: UUID, now: datetime
) -> PersonTimetable:
    upcoming: list[PersonSession] = []
    past: list[PersonSession] = []
    for row, cohort_name in db.execute(
        _person_sessions(tenant_id, person_id).order_by(ClassSession.starts_at)
    ).all():
        entry = PersonSession(_fact(row), cohort_name)
        if row.status == "scheduled" and (row.ends_at or row.starts_at) >= now:
            upcoming.append(entry)
        elif row.status != "cancelled":
            past.append(entry)
    past.reverse()
    return PersonTimetable(tuple(upcoming), tuple(past))


def get_session_for_person(
    db: Session, *, tenant_id: UUID, person_id: UUID, session_id: UUID
) -> PersonSession:
    row = db.execute(
        _person_sessions(tenant_id, person_id).where(ClassSession.id == session_id)
    ).first()
    if row is None:
        raise SchedulingNotFound("Session not found.")
    return PersonSession(_fact(row[0]), row[1])


def join_is_open(
    session: ClassSessionFact, *, now: datetime, lead_minutes: int = 60
) -> bool:
    if not session.join_url or session.status != "scheduled":
        return False
    end = session.ends_at or session.starts_at
    return (session.starts_at - now).total_seconds() <= lead_minutes * 60 and end >= now
