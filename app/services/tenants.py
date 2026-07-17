"""Platform-admin tenant service — provision/list tenants.

Business logic and the only `select()`/session-mutation calls for the tenant
domain live here; `app/api/tenants.py` only resolves dependencies, calls
these functions, and shapes the response.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError
from app.models.tenant import Tenant


def list_tenants(db: Session) -> list[Tenant]:
    return list(db.scalars(select(Tenant).order_by(Tenant.created_at.desc())).all())


def create_tenant(db: Session, payload: Any) -> Tenant:
    tenant = Tenant(slug=payload.slug, name=payload.name)
    db.add(tenant)
    try:
        db.flush()
        db.refresh(tenant)
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("Slug already in use") from exc
    return tenant
