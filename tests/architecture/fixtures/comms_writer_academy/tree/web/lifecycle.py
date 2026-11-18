"""Synthetic fixture. Not Academy code: shapes only, documentation values."""

from __future__ import annotations

from app.services.email_outbox import enqueue_email


def _render_forgot_submit(person):
    return (
        "Your update is ready",
        "<h1>Update</h1><p>Hello " + person.first_name + ", open the portal.</p>",
        "Hello, open the portal to see your update.",
    )


def forgot_submit(db, tenant_id, person):
    # row 10b: renderer in the same module returns the copy
    subject, html, text = _render_forgot_submit(person)
    return enqueue_email(
        db,
        tenant_id=tenant_id,
        idempotency_key="forgot_submit:" + str(person.id),
        kind="forgot_submit",
        recipient=person.email,
        subject=subject,
        html_body=html,
        text_body=text,
    )
