"""Synthetic fixture. Not Academy code: shapes only, documentation values."""

from __future__ import annotations

from app.services.email_outbox import enqueue_email


def _render_invite_to_cohort(person):
    return (
        "Your update is ready",
        "<h1>Update</h1><p>Hello " + person.first_name + ", open the portal.</p>",
        "Hello, open the portal to see your update.",
    )


def invite_to_cohort(db, tenant_id, person):
    # row 9c: renderer in the same module returns the copy
    subject, html, text = _render_invite_to_cohort(person)
    return enqueue_email(
        db,
        tenant_id=tenant_id,
        idempotency_key="invite_to_cohort:" + str(person.id),
        kind="invite_to_cohort",
        recipient=person.email,
        subject=subject,
        html_body=html,
        text_body=text,
    )
