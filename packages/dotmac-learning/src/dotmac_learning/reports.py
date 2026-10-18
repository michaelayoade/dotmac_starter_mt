"""Source reporting selections in the caller's transaction.

Cohort reports use the assessment owner's ranking. Transcripts deliberately
use strict fraction comparison, retaining the first equal-fraction SQL row.
Neither query's unspecified ties are strengthened here. Kernel Party and its
PartyPerson subtype replace legacy Person without account-availability filters.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar
from uuid import UUID

from dotmac_kernel.models import Party, PartyPerson
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from .models import Activity, Score, Submission
from .participation_contracts import ParticipationNotFound
from .participation_models import Cohort, CourseOffering, Enrollment
from .progress_contracts import ScoreFact
from .projections import best_scores_for
from .references import _activity_fact
from .reports_contracts import (
    CohortMatrix,
    MatrixCell,
    MatrixRow,
    ReportCohort,
    ReportNotFound,
    ReportPerson,
    ReportScore,
    StudentTranscript,
    TranscriptRow,
)


def _cohort(db: Session, tenant_id: UUID, cohort_id: UUID) -> ReportCohort:
    row = db.scalars(
        select(Cohort).where(Cohort.tenant_id == tenant_id, Cohort.id == cohort_id)
    ).first()
    if row is None:
        raise ParticipationNotFound("cohort not found for tenant")
    return ReportCohort(
        **{field: getattr(row, field) for field in ReportCohort.__dataclass_fields__}
    )


def _cohort_activities(
    db: Session, tenant_id: UUID, cohort_id: UUID
) -> tuple[list[Activity], list[UUID]]:
    course_ids = db.scalars(
        select(CourseOffering.course_id)
        .where(CourseOffering.tenant_id == tenant_id)
        .where(CourseOffering.cohort_id == cohort_id)
        .where(CourseOffering.status == "active")
    ).all()
    if not course_ids:
        return [], []
    activities = db.scalars(
        select(Activity)
        .where(Activity.tenant_id == tenant_id)
        .where(Activity.course_id.in_(course_ids))
        .order_by(Activity.chapter_number, Activity.type)
    ).all()
    return list(activities), list(course_ids)


def _best_for_person(
    db: Session, tenant_id: UUID, person_id: UUID, course_ids: Sequence[UUID]
) -> dict[UUID, ScoreFact]:
    best: dict[UUID, ScoreFact] = {}
    for cid in course_ids:
        best.update(
            best_scores_for(db, tenant_id=tenant_id, person_id=person_id, course_id=cid)
        )
    return best


def _person(party: Party, profile: PartyPerson) -> ReportPerson:
    return ReportPerson(
        party.id, party.tenant_id, profile.first_name, profile.last_name, party.email
    )


def _students(
    db: Session, tenant_id: UUID, cohort_id: UUID
) -> tuple[ReportPerson, ...]:
    rows = db.execute(
        select(Party, PartyPerson)
        .join(PartyPerson, PartyPerson.party_id == Party.id)
        .join(
            Enrollment,
            (Enrollment.person_id == Party.id)
            & (Enrollment.tenant_id == Party.tenant_id),
        )
        .where(Enrollment.tenant_id == tenant_id)
        .where(Enrollment.cohort_id == cohort_id)
        .where(Enrollment.role_in_cohort == "student")
        .where(Enrollment.status == "active")
        .order_by(PartyPerson.last_name, PartyPerson.first_name, Party.email)
    ).all()
    return tuple(_person(party, profile) for party, profile in rows)


def _score(score: Score | ScoreFact) -> ReportScore:
    return ReportScore(
        **{field: getattr(score, field) for field in ReportScore.__dataclass_fields__}
    )


def cohort_matrix(db: Session, *, tenant_id: UUID, cohort_id: UUID) -> CohortMatrix:
    cohort = _cohort(db, tenant_id, cohort_id)
    activities, course_ids = _cohort_activities(db, tenant_id, cohort_id)
    total = len(activities)
    rows = []
    for p in _students(db, tenant_id, cohort_id):
        best = _best_for_person(db, tenant_id, p.id, course_ids)
        cells = tuple(
            MatrixCell(a.id, _score(best[a.id]) if a.id in best else None)
            for a in activities
        )
        passing = sum(
            1 for a in activities if best.get(a.id) is not None and best[a.id].passed
        )
        rows.append(
            MatrixRow(
                p.id,
                p.first_name,
                p.last_name,
                p.email,
                cells,
                (passing / total) if total else 0.0,
            )
        )
    return CohortMatrix(
        cohort, tuple(_activity_fact(a) for a in activities), tuple(rows)
    )


_S = TypeVar("_S", Score, ScoreFact)


def _fraction_winner(current: _S | None, candidate: _S) -> _S:
    return (
        candidate
        if current is None or candidate.fraction > current.fraction
        else current
    )


def _transcript_query(
    tenant_id: UUID, person_id: UUID
) -> Select[tuple[Activity, Score]]:
    return (
        select(Activity, Score)
        .join(
            Submission,
            (Submission.activity_id == Activity.id)
            & (Submission.tenant_id == Activity.tenant_id),
        )
        .join(
            Score,
            (Score.submission_id == Submission.id)
            & (Score.tenant_id == Submission.tenant_id),
        )
        .where(Activity.tenant_id == tenant_id)
        .where(Submission.person_id == person_id)
        .order_by(Activity.chapter_number, Activity.type)
    )


def student_transcript(
    db: Session, *, tenant_id: UUID, person_id: UUID
) -> StudentTranscript:
    row = db.execute(
        select(Party, PartyPerson)
        .join(PartyPerson, PartyPerson.party_id == Party.id)
        .where(Party.tenant_id == tenant_id, Party.id == person_id)
    ).first()
    if row is None:
        raise ReportNotFound("person not found for tenant")
    person = _person(row[0], row[1])
    pairs = db.execute(_transcript_query(tenant_id, person_id)).all()
    best: dict[UUID, tuple[Activity, Score]] = {}
    for activity, score in pairs:
        cur = best.get(activity.id)
        if cur is None or _fraction_winner(cur[1], score) is score:
            best[activity.id] = (activity, score)
    rows = tuple(
        TranscriptRow(
            _activity_fact(activity),
            _score(score),
            score.fraction,
            score.passed,
            score.created_at,
            score.source,
        )
        for activity, score in sorted(
            best.values(),
            key=lambda t: (
                t[0].chapter_number if t[0].chapter_number is not None else 0,
                t[0].type,
            ),
        )
    )
    return StudentTranscript(person, rows)
