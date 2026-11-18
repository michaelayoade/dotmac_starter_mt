"""Synthetic fixture. Not Academy code: shapes only, documentation values."""

from __future__ import annotations

from app.services.email_outbox import enqueue_email


def sweep(db, tenant_id, person):
    # row 11: body assembled locally, then queued
    body = (
        "<p>Dear " + person.first_name + ", this is a reminder about your course.</p>"
    )
    return enqueue_email(
        db,
        tenant_id=tenant_id,
        idempotency_key="sweep:" + str(person.id),
        kind="sweep",
        recipient=person.email,
        subject="A reminder about your course",
        html_body=body,
    )


def _email_for(title, link):
    return (
        "Reminder: " + title,
        "<p>You have a reminder about " + title + ", open " + link + " now.</p>",
        "You have a reminder, open the link to see it.",
    )


def _flush_queued(db, tenant_id, person):
    # row 12: subject and body as f-strings at the call site
    return enqueue_email(
        db,
        tenant_id=tenant_id,
        idempotency_key=f"flush_queued:{person.id}",
        kind="flush_queued",
        recipient=person.email,
        subject=f"Hello {person.first_name}, your update is ready",
        html_body=f"<p>Hi {person.first_name}, there is news about your account.</p>",
    )
