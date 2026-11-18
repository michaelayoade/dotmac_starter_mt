"""Synthetic fixture. Not Academy code: shapes only, documentation values."""

from __future__ import annotations

from app.services.email_outbox import enqueue_email


def sweep(db, tenant_id, person):
    # row 11: body assembled locally, then queued
    body = (
        "<p>Dear " + person.first_name + ", this is a reminder about your course.</p>"
    )
    return enqueue_email(
        db,
        tenant_id=tenant_id,
        idempotency_key="sweep:" + str(person.id),
        kind="sweep",
        recipient=person.email,
        subject="A reminder about your course",
        html_body=body,
    )


def _flush_queued(db, tenant_id, person):
    # row 12: subject and body as f-strings at the call site
    return enqueue_email(
        db,
        tenant_id=tenant_id,
        idempotency_key=f"_flush_queued:{person.id}",
        kind="_flush_queued",
        recipient=person.email,
        subject=f"Hello {person.first_name}, your update is ready",
        html_body=f"<p>Hi {person.first_name}, there is news about your account.</p>",
    )


def _render_resend(person):
    return (
        "Your update is ready",
        "<h1>Update</h1><p>Hello " + person.first_name + ", open the portal.</p>",
        "Hello, open the portal to see your update.",
    )


def resend(db, tenant_id, person):
    # row 13: renderer in the same module returns the copy
    subject, html, text = _render_resend(person)
    return enqueue_email(
        db,
        tenant_id=tenant_id,
        idempotency_key="resend:" + str(person.id),
        kind="resend",
        recipient=person.email,
        subject=subject,
        html_body=html,
        text_body=text,
    )
