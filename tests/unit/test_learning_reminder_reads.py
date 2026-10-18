"""Frozen minimal selected-field contracts; product occurrence policy is absent."""

from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from dotmac_learning.reminder_contracts import (
    OpeningOffering,
    RecentAssignment,
    RecentCertificate,
    RecentCompletedCourse,
    RecentManualResult,
)


def test_facts_only_carry_exact_selected_fields_and_are_frozen():
    now = datetime(2026, 8, 1, tzinfo=UTC)
    cases = (
        (RecentAssignment(uuid4(), "C"), ("enrollment_id", "cohort_name")),
        (
            OpeningOffering(uuid4(), "Course", now),
            ("offering_id", "course_title", "starts_at"),
        ),
        (RecentManualResult(uuid4(), "Activity"), ("score_id", "activity_title")),
        (RecentCompletedCourse(uuid4(), "Course"), ("completion_id", "course_title")),
        (RecentCertificate(uuid4(), "Course"), ("certificate_id", "course_title")),
    )
    for fact, names in cases:
        assert tuple(field.name for field in fields(fact)) == names
        with pytest.raises(FrozenInstanceError):
            setattr(fact, names[0], uuid4())
