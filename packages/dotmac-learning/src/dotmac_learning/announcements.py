"""Caller-transaction announcements with optional product notification fanout."""

from __future__ import annotations

import logging
from uuid import UUID

from dotmac_kernel.audit import write_audit_event
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .announcement_contracts import (
    AnnouncementFact,
    AnnouncementFanout,
    CreateAnnouncementCommand,
    DeleteAnnouncementCommand,
)
from .attempt_contracts import AttemptAuditContext
from .delivery_models import Announcement
from .participation_models import Enrollment
from .results import _transaction, _unchanged

logger = logging.getLogger(__name__)


def _fact(announcement: Announcement) -> AnnouncementFact:
    return AnnouncementFact(
        id=announcement.id,
        tenant_id=announcement.tenant_id,
        cohort_id=announcement.cohort_id,
        author_person_id=announcement.author_person_id,
        title=announcement.title,
        body_md=announcement.body_md,
        body_html=announcement.body_html,
        created_at=announcement.created_at,
        updated_at=announcement.updated_at,
    )


def create(
    db: Session,
    command: CreateAnnouncementCommand,
    *,
    audit_context: AttemptAuditContext,
    fanout: AnnouncementFanout,
) -> AnnouncementFact:
    """Flush the announcement, isolate fanout failure, then require Kernel audit.

    The product supplies rendered HTML and trusted audit attribution. Required
    failures propagate; the caller owns the transaction and any rollback.
    """
    boundary = _transaction(db)
    if audit_context.tenant_id != command.tenant_id:
        raise ValueError("Audit context tenant scope does not match the command.")
    ann = Announcement(
        tenant_id=command.tenant_id,
        cohort_id=command.cohort_id,
        author_person_id=command.author_person_id,
        title=command.title,
        body_md=command.body_md,
        body_html=command.body_html,
    )
    db.add(ann)
    db.flush()

    if command.cohort_id is not None:
        audience_ids = tuple(
            db.scalars(
                select(Enrollment.person_id)
                .where(Enrollment.tenant_id == command.tenant_id)
                .where(Enrollment.cohort_id == command.cohort_id)
                .where(Enrollment.status == "active")
                .where(Enrollment.role_in_cohort == "student")
            ).all()
        )
    else:
        audience_ids = tuple(
            db.scalars(
                select(Enrollment.person_id)
                .where(Enrollment.tenant_id == command.tenant_id)
                .where(Enrollment.status == "active")
                .distinct()
            ).all()
        )
    fact = _fact(ann)
    _unchanged(db, boundary)
    if audience_ids:
        try:
            with db.begin_nested():
                fanout(db, fact, audience_ids)
        except Exception as exc:
            logger.warning("announcement notify fan-out failed: %s", exc)
    _unchanged(db, boundary)

    write_audit_event(
        db,
        tenant_id=command.tenant_id,
        actor_type=audit_context.actor_type,
        actor_id=audit_context.actor_id,
        actor_label=audit_context.actor_label,
        actor_party_id=command.author_person_id,
        source_application=audit_context.source_application,
        action="announcement.created",
        entity_type="announcement",
        entity_id=str(ann.id),
    )
    _unchanged(db, boundary)
    return fact


def for_person(
    db: Session,
    *,
    tenant_id: UUID,
    person_id: UUID,
    limit: int,
) -> tuple[AnnouncementFact, ...]:
    """Return tenant-wide announcements and those for any active enrollment."""
    enrolled_cohort_ids = list(
        db.scalars(
            select(Enrollment.cohort_id)
            .where(Enrollment.tenant_id == tenant_id)
            .where(Enrollment.person_id == person_id)
            .where(Enrollment.status == "active")
        ).all()
    )
    if enrolled_cohort_ids:
        cohort_filter = or_(
            Announcement.cohort_id.is_(None),
            Announcement.cohort_id.in_(enrolled_cohort_ids),
        )
    else:
        cohort_filter = Announcement.cohort_id.is_(None)
    return tuple(
        _fact(ann)
        for ann in db.scalars(
            select(Announcement)
            .where(Announcement.tenant_id == tenant_id)
            .where(cohort_filter)
            .order_by(Announcement.created_at.desc())
            .limit(limit)
        ).all()
    )


def list_for_tenant(
    db: Session,
    *,
    tenant_id: UUID,
    limit: int,
) -> tuple[AnnouncementFact, ...]:
    return tuple(
        _fact(ann)
        for ann in db.scalars(
            select(Announcement)
            .where(Announcement.tenant_id == tenant_id)
            .order_by(Announcement.created_at.desc())
            .limit(limit)
        ).all()
    )


def delete(db: Session, command: DeleteAnnouncementCommand) -> None:
    """Delete the scoped row if present, with no audit or missing-row flush."""
    boundary = _transaction(db)
    ann = db.scalars(
        select(Announcement)
        .where(Announcement.tenant_id == command.tenant_id)
        .where(Announcement.id == command.announcement_id)
    ).first()
    if ann is not None:
        db.delete(ann)
        db.flush()
    _unchanged(db, boundary)


__all__ = ["create", "for_person", "list_for_tenant", "delete"]
