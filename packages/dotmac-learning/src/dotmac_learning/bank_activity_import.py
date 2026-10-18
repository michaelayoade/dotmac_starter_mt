"""Academy 0968881 CLI bank load and selected-field Activity upsert intent.

Parsing, lint-before-course lookup, messages and transaction outcome remain
product-owned. Reuse the curriculum loader's original Session and flushes;
leave final Activity changes staged with the source's raw policy values.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .bank_activity_contracts import (
    BankActivityImportResult,
    ImportBankWithActivityCommand,
)
from .curriculum import load_bank
from .models import Activity, Course
from .references import _add_activity, _assign_activity


def import_bank_with_activity(
    db: Session, command: ImportBankWithActivityCommand
) -> BankActivityImportResult | None:
    """Skip an absent tenant course; otherwise load its bank and stage Activity."""
    doc = command.doc
    course = db.scalars(
        select(Course).where(
            Course.tenant_id == command.tenant_id, Course.slug == doc.course
        )
    ).first()
    if course is None:
        return None

    bank = load_bank(
        db,
        tenant_id=command.tenant_id,
        course_id=course.id,
        doc=doc,
        missing_question_weight=command.missing_question_weight,
    )
    pass_threshold = doc.policy.get(
        "pass_threshold",
        command.terms.pass_threshold_by_kind.get(
            doc.kind, command.terms.fallback_pass_threshold
        ),
    )
    title = (
        f"Chapter {doc.chapter} test"
        if doc.kind == "chapter"
        else f"{doc.kind.title()} assessment"
    )
    activity = db.scalars(
        select(Activity).where(
            Activity.tenant_id == command.tenant_id,
            Activity.course_id == course.id,
            Activity.bank_id == bank.id,
        )
    ).first()
    created = activity is None
    if activity is None:
        activity = _add_activity(
            db,
            {
                "tenant_id": command.tenant_id,
                "course_id": course.id,
                "chapter_number": doc.chapter,
                "type": "mcq_test",
                "bank_id": bank.id,
                "title": title,
                "pass_threshold": pass_threshold,
                "assessment_mode": command.terms.new_activity_assessment_mode,
                "grading": command.terms.new_activity_grading,
                "weight": command.terms.new_activity_weight,
            },
        )
    else:
        _assign_activity(
            activity, (("title", title), ("pass_threshold", pass_threshold))
        )

    applied_policy: list[tuple[str, object]] = []
    for key, column in (
        ("pool", "question_count"),
        ("max_attempts", "max_attempts"),
        ("mode", "assessment_mode"),
    ):
        if key in doc.policy:
            _assign_activity(activity, ((column, doc.policy[key]),))
            applied_policy.append((key, doc.policy[key]))

    return BankActivityImportResult(
        bank_id=bank.id,
        title=title,
        created=created,
        applied_policy=tuple(applied_policy),
    )


__all__ = ["import_bank_with_activity"]
