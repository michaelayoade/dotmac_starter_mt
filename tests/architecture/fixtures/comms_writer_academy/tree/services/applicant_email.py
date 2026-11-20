"""Synthetic fixture. Not Academy code: shapes only, documentation values."""

from __future__ import annotations

from app.services.email_outbox import enqueue_email


def send_application_received(db, tenant_id, person):
    # row 3: body assembled locally, then queued
    body = (
        "<p>Dear " + person.first_name + ", this is a reminder about your course.</p>"
    )
    return enqueue_email(
        db,
        tenant_id=tenant_id,
        idempotency_key="send_application_received:" + str(person.id),
        kind="send_application_received",
        recipient=person.email,
        subject="A reminder about your course",
        html_body=body,
    )


def send_exam_invite(db, tenant_id, person):
    # row 4: subject and body as f-strings at the call site
    return enqueue_email(
        db,
        tenant_id=tenant_id,
        idempotency_key=f"send_exam_invite:{person.id}",
        kind="send_exam_invite",
        recipient=person.email,
        subject=f"Hello {person.first_name}, your update is ready",
        html_body=f"<p>Hi {person.first_name}, there is news about your account.</p>",
    )


def _render_send_waitlist_notice(person):
    return (
        "Your update is ready",
        "<h1>Update</h1><p>Hello " + person.first_name + ", open the portal.</p>",
        "Hello, open the portal to see your update.",
    )


def send_waitlist_notice(db, tenant_id, person):
    # row 5: renderer in the same module returns the copy
    subject, html, text = _render_send_waitlist_notice(person)
    return enqueue_email(
        db,
        tenant_id=tenant_id,
        idempotency_key="send_waitlist_notice:" + str(person.id),
        kind="send_waitlist_notice",
        recipient=person.email,
        subject=subject,
        html_body=html,
        text_body=text,
    )


def send_results_received(db, tenant_id, person):
    # row 6: body assembled locally, then queued
    body = (
        "<p>Dear " + person.first_name + ", this is a reminder about your course.</p>"
    )
    return enqueue_email(
        db,
        tenant_id=tenant_id,
        idempotency_key="send_results_received:" + str(person.id),
        kind="send_results_received",
        recipient=person.email,
        subject="A reminder about your course",
        html_body=body,
    )


def send_onboarding_invite(db, tenant_id, person):
    # row 7: subject and body as f-strings at the call site
    return enqueue_email(
        db,
        tenant_id=tenant_id,
        idempotency_key=f"send_onboarding_invite:{person.id}",
        kind="send_onboarding_invite",
        recipient=person.email,
        subject=f"Hello {person.first_name}, your update is ready",
        html_body=f"<p>Hi {person.first_name}, there is news about your account.</p>",
    )


def _render_send_enrollment_welcome(person):
    return (
        "Your update is ready",
        "<h1>Update</h1><p>Hello " + person.first_name + ", open the portal.</p>",
        "Hello, open the portal to see your update.",
    )


def send_enrollment_welcome(db, tenant_id, person):
    # row 8: renderer in the same module returns the copy
    subject, html, text = _render_send_enrollment_welcome(person)
    return enqueue_email(
        db,
        tenant_id=tenant_id,
        idempotency_key="send_enrollment_welcome:" + str(person.id),
        kind="send_enrollment_welcome",
        recipient=person.email,
        subject=subject,
        html_body=html,
        text_body=text,
    )
