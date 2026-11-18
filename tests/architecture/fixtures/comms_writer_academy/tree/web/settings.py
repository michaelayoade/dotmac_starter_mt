"""Synthetic fixture. Not Academy code: shapes only, documentation values."""

from __future__ import annotations

from app.services.email_outbox import enqueue_email


def _render_settings_test_email(person):
    return (
        "Your update is ready",
        "<h1>Update</h1><p>Hello " + person.first_name + ", open the portal.</p>",
        "Hello, open the portal to see your update.",
    )


def settings_test_email(db, tenant_id, person):
    # row 22: renderer in the same module returns the copy
    subject, html, text = _render_settings_test_email(person)
    return enqueue_email(
        db,
        tenant_id=tenant_id,
        idempotency_key="settings_test_email:" + str(person.id),
        kind="settings_test_email",
        recipient=person.email,
        subject=subject,
        html_body=html,
        text_body=text,
    )
