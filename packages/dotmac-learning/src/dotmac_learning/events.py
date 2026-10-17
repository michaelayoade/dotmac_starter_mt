"""Source-derived event append and primitive queries on a supplied Session.

The caller owns the transaction. ``record`` propagates errors; ``emit`` isolates
an optional append in a SAVEPOINT and reports its actual local outcome. Neither
method commits, delivers, or creates a session. Day throttling deliberately keeps
the source's SELECT race; product timezone selection belongs to the adapter.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import (
    UTC,
    datetime,
    time,
    timedelta,
    tzinfo,
)
from uuid import UUID

from dotmac_kernel.models import Party, PartyType
from pydantic import JsonValue, TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from dotmac_learning.event_contracts import (
    EventEmission,
    EventFact,
    EventWrite,
    WeekBucket,
)
from dotmac_learning.models import (
    EVENT_KINDS,
    KIND_CERTIFICATE_EARNED,
    KIND_CHAPTER_COMPLETED,
    KIND_CHAPTER_VIEWED,
    KIND_COURSE_VIEWED,
    LearningEvent,
)

_ONCE_PER_SUBJECT = (KIND_CHAPTER_COMPLETED, KIND_CERTIFICATE_EARNED)
_ONCE_PER_LOCAL_DAY = (KIND_COURSE_VIEWED, KIND_CHAPTER_VIEWED)
_DETAIL = TypeAdapter(JsonValue)


def _local_day_start_utc(now: datetime, zone: tzinfo) -> datetime:
    local = now.astimezone(zone)
    return datetime.combine(local.date(), time.min, tzinfo=zone).astimezone(UTC)


def _fact(event: LearningEvent) -> EventFact:
    return EventFact(
        id=event.id,
        tenant_id=event.tenant_id,
        person_id=event.person_id,
        kind=event.kind,
        course_id=event.course_id,
        subject_id=event.subject_id,
        detail=deepcopy(_DETAIL.validate_python(event.detail)),
        occurred_at=event.occurred_at,
        created_at=event.created_at,
        updated_at=event.updated_at,
    )


def record(
    db: Session,
    payload: EventWrite,
    *,
    local_day_zone: tzinfo | None = None,
) -> EventFact | None:
    """Append one event, returning None only when a dedupe rule suppresses it.

    The actual tenant-scoped Party must be a person, even if a malformed
    organization has a person profile. The real dual identity FKs remain the
    existence/cascade backstop; this path takes no explicit identity locks.
    """
    if payload.kind not in EVENT_KINDS:
        raise ValueError("unknown learning-event kind")
    if payload.kind in _ONCE_PER_LOCAL_DAY and local_day_zone is None:
        raise ValueError("daily learning events require local_day_zone")
    party_type = db.scalar(
        select(Party.party_type)
        .where(Party.tenant_id == payload.tenant_id)
        .where(Party.id == payload.person_id)
    )
    if party_type != PartyType.person:
        raise ValueError("learning-event identity must be a tenant-scoped person")
    now = payload.occurred_at or datetime.now(UTC)
    if payload.kind in _ONCE_PER_LOCAL_DAY:
        # Already required above: retain a concrete zone at this typed seam.
        assert local_day_zone is not None
        dupe = (
            select(LearningEvent.id)
            .where(LearningEvent.tenant_id == payload.tenant_id)
            .where(LearningEvent.person_id == payload.person_id)
            .where(LearningEvent.kind == payload.kind)
            .where(LearningEvent.subject_id == payload.subject_id)
            .where(
                LearningEvent.occurred_at >= _local_day_start_utc(now, local_day_zone)
            )
        )
        if db.scalars(dupe.limit(1)).first() is not None:
            return None
    if payload.kind in _ONCE_PER_SUBJECT:
        # Exact 0047 predicate and ordinary PostgreSQL NULL subject semantics.
        stmt = (
            pg_insert(LearningEvent)
            .values(
                tenant_id=payload.tenant_id,
                person_id=payload.person_id,
                kind=payload.kind,
                course_id=payload.course_id,
                subject_id=payload.subject_id,
                detail=payload.detail,
                occurred_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=["tenant_id", "person_id", "kind", "subject_id"],
                index_where=LearningEvent.kind.in_(_ONCE_PER_SUBJECT),
            )
            .returning(LearningEvent.id)
        )
        inserted_id = db.execute(stmt).scalar()
        if inserted_id is None:
            return None
        db.flush()
        event = db.get(LearningEvent, inserted_id)
        if event is None:
            raise RuntimeError("appended learning event is unavailable")
        return _fact(event)
    event = LearningEvent(
        tenant_id=payload.tenant_id,
        person_id=payload.person_id,
        kind=payload.kind,
        course_id=payload.course_id,
        subject_id=payload.subject_id,
        detail=payload.detail,
        occurred_at=now,
    )
    db.add(event)
    db.flush()
    return _fact(event)


def emit(
    db: Session,
    payload: EventWrite,
    *,
    local_day_zone: tzinfo | None = None,
) -> EventEmission:
    """Optionally append, catching failures outside SAVEPOINT exit.

    This module explicitly flushes required pending host state before the
    optional failure handler: a host SQL failure propagates and requires caller
    rollback. Only this append's failures are optional and savepoint-isolated.
    This tightens the Academy source wrapper's implicit-preflush behavior;
    Academy adoption must qualify it at the actual producer boundaries.
    """
    db.flush()
    try:
        with db.begin_nested():
            event = record(db, payload, local_day_zone=local_day_zone)
        return (
            EventEmission("recorded", event)
            if event is not None
            else EventEmission("suppressed")
        )
    except Exception:
        return EventEmission("failed")


def last_activity_at(
    db: Session, *, tenant_id: UUID, person_id: UUID
) -> datetime | None:
    return db.scalar(
        select(func.max(LearningEvent.occurred_at))
        .where(LearningEvent.tenant_id == tenant_id)
        .where(LearningEvent.person_id == person_id)
    )


def weekly_buckets(
    db: Session,
    *,
    tenant_id: UUID,
    person_id: UUID,
    weeks: int = 8,
    kinds: tuple[str, ...] | None = None,
    now: datetime | None = None,
) -> list[WeekBucket]:
    """Source trailing-week date buckets, oldest first (not calendar realignment)."""
    now = now or datetime.now(UTC)
    start = now - timedelta(weeks=weeks)
    q = (
        select(LearningEvent.occurred_at)
        .where(LearningEvent.tenant_id == tenant_id)
        .where(LearningEvent.person_id == person_id)
        .where(LearningEvent.occurred_at >= start)
    )
    if kinds:
        q = q.where(LearningEvent.kind.in_(kinds))
    stamps = list(db.scalars(q).all())
    buckets: list[WeekBucket] = []
    for i in range(weeks):
        w_start = (now - timedelta(weeks=weeks - i)).date()
        w_end = (now - timedelta(weeks=weeks - i - 1)).date()
        count = sum(1 for stamp in stamps if w_start <= stamp.date() < w_end)
        buckets.append(WeekBucket(week_start=w_start, count=count))
    return buckets


def active_person_ids(
    db: Session,
    *,
    tenant_id: UUID,
    person_ids: list[UUID],
    since: datetime,
) -> set[UUID]:
    if not person_ids:
        return set()
    rows = db.scalars(
        select(LearningEvent.person_id)
        .where(LearningEvent.tenant_id == tenant_id)
        .where(LearningEvent.person_id.in_(person_ids))
        .where(LearningEvent.occurred_at >= since)
        .distinct()
    ).all()
    return set(rows)


def kind_counts(
    db: Session,
    *,
    tenant_id: UUID,
    kinds: tuple[str, ...],
    course_ids: list[UUID] | None = None,
    person_ids: list[UUID] | None = None,
    since: datetime | None = None,
) -> dict[UUID | None, dict[str, int]]:
    """Source per-course/kind counts, with the same optional roster semantics."""
    q = (
        select(LearningEvent.course_id, LearningEvent.kind, func.count())
        .where(LearningEvent.tenant_id == tenant_id)
        .where(LearningEvent.kind.in_(kinds))
        .group_by(LearningEvent.course_id, LearningEvent.kind)
    )
    if course_ids:
        q = q.where(LearningEvent.course_id.in_(course_ids))
    if person_ids is not None:
        q = q.where(LearningEvent.person_id.in_(person_ids))
    if since is not None:
        q = q.where(LearningEvent.occurred_at >= since)
    out: dict[UUID | None, dict[str, int]] = {}
    for course_id, kind, count in db.execute(q).all():
        out.setdefault(course_id, {})[kind] = int(count)
    return out


__all__ = [
    "record",
    "emit",
    "last_activity_at",
    "weekly_buckets",
    "active_person_ids",
    "kind_counts",
]
