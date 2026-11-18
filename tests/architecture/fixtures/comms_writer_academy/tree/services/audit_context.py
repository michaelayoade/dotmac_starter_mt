"""Synthetic fixture. Not Academy code: shapes only, documentation values."""

from __future__ import annotations

from app.services.email_outbox import enqueue_email


class _SuccessDelivery:
    # row 19: subject and body authored per send, delivered from a callable
    def __call__(self, db, tenant_id, person, subject, body):
        return enqueue_email(
            db,
            tenant_id=tenant_id,
            idempotency_key="segment:" + str(person.id),
            kind="segment_message",
            recipient=person.email,
            subject=f"Message from your instructor: {subject}",
            html_body=f"<p>{body}</p><p>Sent via the course portal.</p>",
        )
