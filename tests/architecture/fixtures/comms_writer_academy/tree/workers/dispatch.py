"""Synthetic fixture: a worker that is already clean (template slug, no copy)."""

from __future__ import annotations


def drain(delivery, tenant_id, person):
    return delivery.send_email(
        tenant_id=tenant_id,
        template="welcome",
        recipient=person.email,
        variables={"name": person.first_name},
    )
