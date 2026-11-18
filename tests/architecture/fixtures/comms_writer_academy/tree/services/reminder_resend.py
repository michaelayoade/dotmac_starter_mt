"""Synthetic fixture. Not Academy code: shapes only, documentation values."""

from __future__ import annotations

from app.services.email_outbox import enqueue_email


def resend_reminder(db, *, tenant_id, log, person):
    # row 13: the renderer is imported INSIDE the function and the copy reaches
    # the enqueue through a ** mapping (the shape of the real reminder_resend).
    from app.services.reminders import _email_for

    subject, html, text = _email_for(log.title, log.link)
    expected_outbox: dict = dict(  # noqa: C408
        tenant_id=tenant_id,
        idempotency_key="reminder_resend:" + str(log.id),
        kind="reminder_resend",
        recipient=person.email,
        subject=subject,
        html_body=html,
        text_body=text,
        payload={},
    )
    return enqueue_email(db, **expected_outbox)
