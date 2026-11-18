"""Synthetic fixture. Not Academy code: shapes only, documentation values."""

from __future__ import annotations

from app.services.email_outbox import enqueue_email


def send_weekly_digests(db, tenant_id, person):
    # row 14: body assembled locally, then queued
    body = (
        "<p>Dear " + person.first_name + ", this is a reminder about your course.</p>"
    )
    return enqueue_email(
        db,
        tenant_id=tenant_id,
        idempotency_key="send_weekly_digests:" + str(person.id),
        kind="send_weekly_digests",
        recipient=person.email,
        subject="A reminder about your course",
        html_body=body,
    )
