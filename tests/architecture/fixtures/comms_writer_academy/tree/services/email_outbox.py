"""Synthetic fixture: the product-owned enqueue writer."""

from __future__ import annotations

from app.models.email_outbox import EmailOutbox


def enqueue_email(
    db,
    *,
    tenant_id,
    idempotency_key,
    kind,
    recipient,
    subject,
    html_body,
    text_body=None,
):
    return db.add(EmailOutbox())
