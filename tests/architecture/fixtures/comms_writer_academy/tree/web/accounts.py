"""Synthetic fixture. Not Academy code: shapes only, documentation values."""

from __future__ import annotations

from app.services.email_outbox import enqueue_email


def users_invite(db, tenant_id, person):
    # row 9a: body assembled locally, then queued
    body = (
        "<p>Dear " + person.first_name + ", this is a reminder about your course.</p>"
    )
    return enqueue_email(
        db,
        tenant_id=tenant_id,
        idempotency_key="users_invite:" + str(person.id),
        kind="users_invite",
        recipient=person.email,
        subject="A reminder about your course",
        html_body=body,
    )


def users_invite_link(db, tenant_id, person):
    # row 9b: subject and body as f-strings at the call site
    return enqueue_email(
        db,
        tenant_id=tenant_id,
        idempotency_key=f"users_invite_link:{person.id}",
        kind="users_invite_link",
        recipient=person.email,
        subject=f"Hello {person.first_name}, your update is ready",
        html_body=f"<p>Hi {person.first_name}, there is news about your account.</p>",
    )


def users_reset_link(db, tenant_id, person):
    # row 10a: subject and body as f-strings at the call site
    return enqueue_email(
        db,
        tenant_id=tenant_id,
        idempotency_key=f"users_reset_link:{person.id}",
        kind="users_reset_link",
        recipient=person.email,
        subject=f"Hello {person.first_name}, your update is ready",
        html_body=f"<p>Hi {person.first_name}, there is news about your account.</p>",
    )
