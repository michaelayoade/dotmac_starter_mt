"""Synthetic fixture. Not Academy code: shapes only, documentation values."""

from __future__ import annotations

from app.services.email_outbox import enqueue_email


def reports_student_email(db, tenant_id, person):
    # row 20: body assembled locally, then queued
    body = (
        "<p>Dear " + person.first_name + ", this is a reminder about your course.</p>"
    )
    return enqueue_email(
        db,
        tenant_id=tenant_id,
        idempotency_key="reports_student_email:" + str(person.id),
        kind="reports_student_email",
        recipient=person.email,
        subject="A reminder about your course",
        html_body=body,
    )


def reports_cohort_email(db, tenant_id, person):
    # row 21: subject and body as f-strings at the call site
    return enqueue_email(
        db,
        tenant_id=tenant_id,
        idempotency_key=f"reports_cohort_email:{person.id}",
        kind="reports_cohort_email",
        recipient=person.email,
        subject=f"Hello {person.first_name}, your update is ready",
        html_body=f"<p>Hi {person.first_name}, there is news about your account.</p>",
    )
