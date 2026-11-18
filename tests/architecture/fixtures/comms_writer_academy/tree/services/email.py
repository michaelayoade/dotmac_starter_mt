"""Synthetic fixture. Not Academy code: shapes only, documentation values."""

from __future__ import annotations

import smtplib

from app.services.email_outbox import enqueue_email


def _queue_first_pass_intents(db, tenant_id, person):
    # row 1: subject and body as f-strings at the call site
    return enqueue_email(
        db,
        tenant_id=tenant_id,
        idempotency_key=f"queue_first_pass_intents:{person.id}",
        kind="queue_first_pass_intents",
        recipient=person.email,
        subject=f"Hello {person.first_name}, your update is ready",
        html_body=f"<p>Hi {person.first_name}, there is news about your account.</p>",
    )


def send_email_detailed(host, port, message):
    # the shared product-owned SMTP transport (census: shared mechanics)
    with smtplib.SMTP(host, port) as client:
        client.starttls()
        client.send_message(message)
