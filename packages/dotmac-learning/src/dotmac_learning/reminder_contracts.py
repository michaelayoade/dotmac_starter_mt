"""Detached selected fields for product-owned reminder occurrence detection."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RecentAssignment:
    enrollment_id: UUID
    cohort_name: str


@dataclass(frozen=True, slots=True)
class OpeningOffering:
    offering_id: UUID
    course_title: str
    starts_at: datetime


@dataclass(frozen=True, slots=True)
class RecentManualResult:
    score_id: UUID
    activity_title: str


@dataclass(frozen=True, slots=True)
class RecentCompletedCourse:
    completion_id: UUID
    course_title: str


@dataclass(frozen=True, slots=True)
class RecentCertificate:
    certificate_id: UUID
    course_title: str
