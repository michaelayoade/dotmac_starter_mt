"""Synthetic fixture. Not Academy code: shapes only, documentation values."""

from __future__ import annotations

from app.services.email_outbox import enqueue_email


def reinvite_stranded(db, tenant_id, person):
    # row 9d: body assembled locally, then queued
    body = (
        "<p>Dear " + person.first_name + ", this is a reminder about your course.</p>"
    )
    return enqueue_email(
        db,
        tenant_id=tenant_id,
        idempotency_key="reinvite_stranded:" + str(person.id),
        kind="reinvite_stranded",
        recipient=person.email,
        subject="A reminder about your course",
        html_body=body,
    )
