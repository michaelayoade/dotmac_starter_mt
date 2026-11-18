"""Synthetic fixture. Not Academy code: shapes only, documentation values."""

from __future__ import annotations

from app.services.email_outbox import enqueue_email


def _render_message_segment(person):
    return (
        "Your update is ready",
        "<h1>Update</h1><p>Hello " + person.first_name + ", open the portal.</p>",
        "Hello, open the portal to see your update.",
    )


def message_segment(db, tenant_id, person):
    # row 19: renderer in the same module returns the copy
    subject, html, text = _render_message_segment(person)
    return enqueue_email(
        db,
        tenant_id=tenant_id,
        idempotency_key="message_segment:" + str(person.id),
        kind="message_segment",
        recipient=person.email,
        subject=subject,
        html_body=html,
        text_body=text,
    )
