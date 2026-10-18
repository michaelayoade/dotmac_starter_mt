"""Detached instructor facts and trusted product account/authority observations."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from .curriculum_contracts import ChapterFact
from .participation_contracts import EnrollmentFact
from .references import ActivityFact, CourseFact
from .reports_contracts import ReportCohort, ReportPerson, ReportScore
from .tracks import CohortTrackItem


class InstructorCourseNotFound(ValueError):
    """The source course lookup returned no tenant-scoped row (HTTP 404)."""


class InstructorCourseForbidden(PermissionError):
    """A non-admin instructor is not assigned the existing course (HTTP 403)."""


@dataclass(frozen=True, slots=True)
class InstructorAuthority:
    tenant_id: UUID
    person_id: UUID
    actor_is_admin: bool


@dataclass(frozen=True, slots=True)
class InstructorRosterMember:
    enrollment: EnrollmentFact
    person: ReportPerson


@dataclass(frozen=True, slots=True)
class InstructorCohortSummary:
    cohort: ReportCohort
    roster: tuple[InstructorRosterMember, ...]
    active_student_count: int
    facilitators: tuple[ReportPerson, ...]
    active_courses: tuple[CourseFact, ...]
    tracks: tuple[CohortTrackItem, ...]


@dataclass(frozen=True, slots=True)
class InstructorCohorts:
    cohorts: tuple[InstructorCohortSummary, ...]
    selectable_courses: tuple[CourseFact, ...]
    draft_course_count: int


@dataclass(frozen=True, slots=True)
class InstructorOffering:
    id: UUID
    tenant_id: UUID
    cohort_id: UUID
    course_id: UUID
    status: str
    starts_at: datetime | None
    ends_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class InstructorStudent:
    enrollment: EnrollmentFact
    person: ReportPerson
    completion_pct: float
    last_activity_at: datetime | None
    at_risk: bool


@dataclass(frozen=True, slots=True)
class InstructorChapter:
    chapter: ChapterFact
    activities: tuple[ActivityFact, ...]


@dataclass(frozen=True, slots=True)
class InstructorCourseWorkspace:
    offering: InstructorOffering
    course: CourseFact
    chapters: tuple[InstructorChapter, ...]
    standalone_activities: tuple[ActivityFact, ...]
    activity_count: int
    track_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class InstructorWorkspace:
    cohort: ReportCohort
    students: tuple[InstructorStudent, ...]
    student_total: int
    active_student_count: int
    at_risk_count: int
    average_progress: float
    facilitators: tuple[InstructorRosterMember, ...]
    courses: tuple[InstructorCourseWorkspace, ...]
    tracks: tuple[CohortTrackItem, ...]


@dataclass(frozen=True, slots=True)
class InstructorResult:
    email: str | None
    activity_title: str
    score: ReportScore


class InstructorChapterNotFound(ValueError):
    """The requested preview chapter is missing (source HTTP 404)."""


@dataclass(frozen=True, slots=True)
class InstructorPreview:
    course: CourseFact
    chapters: tuple[ChapterFact, ...]
    chapter: ChapterFact | None
    activity: ActivityFact | None
    previous_chapter: ChapterFact | None
    next_chapter: ChapterFact | None
