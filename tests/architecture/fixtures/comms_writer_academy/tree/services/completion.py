"""Synthetic fixture. Not Academy code: shapes only, documentation values."""

from __future__ import annotations

from app.services.email_outbox import enqueue_email


def _render_email_certificate(person):
    return (
        "Your update is ready",
        "<h1>Update</h1><p>Hello " + person.first_name + ", open the portal.</p>",
        "Hello, open the portal to see your update.",
    )


def _email_certificate(db, tenant_id, person):
    # row 2: renderer in the same module returns the copy
    subject, html, text = _render_email_certificate(person)
    return enqueue_email(
        db,
        tenant_id=tenant_id,
        idempotency_key="_email_certificate:" + str(person.id),
        kind="_email_certificate",
        recipient=person.email,
        subject=subject,
        html_body=html,
        text_body=text,
    )
