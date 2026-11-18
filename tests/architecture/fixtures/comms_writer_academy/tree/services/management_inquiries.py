"""Synthetic fixture. Not Academy code: shapes only, documentation values."""

from __future__ import annotations

from app.services.email_outbox import enqueue_email


def queue_management_inquiry(db, tenant_id, person):
    # row 18: subject and body as f-strings at the call site
    return enqueue_email(
        db,
        tenant_id=tenant_id,
        idempotency_key=f"queue_management_inquiry:{person.id}",
        kind="queue_management_inquiry",
        recipient=person.email,
        subject=f"Hello {person.first_name}, your update is ready",
        html_body=f"<p>Hi {person.first_name}, there is news about your account.</p>",
    )
