"""Custom field definitions + values API.

Guard pattern matches `app.features.settings.router` / `app.features.rbac.
router` exactly: router-level `Depends(require_tenant)` plus a per-route
`Depends(require_role("admin"))`. The VALUES endpoints (`GET`/`PUT
.../values`) could arguably be opened to any authenticated user once this
app grows real UI roles — noted here rather than guessed at: **loosen
per-route in phase 2b when UI roles land.** For phase 2a, every route below
uses the admin guard for consistency with the rest of the tenant-admin
surface (settings, rbac).

Router is a thin wrapper — validate -> authorize -> delegate to
`app.features.custom_fields.service`. No direct DB queries here (enforced by
`tests/architecture/test_thin_wrappers.py`).

**`entity_type`-filter decision (GET /custom-fields/definitions):** the Task
9 service only exposes `list_for_entity(db, tenant_id, entity_type, ...)` —
there is no "list every definition for every entity_type" service function.
Rather than add new service surface for a list-all path this task doesn't
otherwise need, `entity_type` is a REQUIRED query parameter here — the route
delegates straight to `list_for_entity` with no added service logic. Pagination
(`limit`/`offset`, `ge=0`/`le=200` matching `rbac`'s bounds) is applied by
this router via a plain list slice over `list_for_entity`'s result, since
that service function has no `limit`/`offset` parameters of its own (unlike
`rbac_service.list_roles`, which paginates at the DB layer) and this task's
file list doesn't include modifying `service.py`. This is still just
Python-level slicing, not a DB query, so it doesn't trip the thin-wrapper
check.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_role, require_tenant
from app.core.models import Party, Tenant
from app.features.custom_fields import service as custom_fields_service
from app.features.custom_fields.models import CustomFieldDefinition
from app.features.custom_fields.schemas import (
    CustomFieldCreate,
    CustomFieldRead,
    CustomFieldUpdate,
    CustomFieldValues,
)

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

router = APIRouter(
    prefix="/custom-fields",
    tags=["custom-fields"],
    dependencies=[Depends(require_tenant)],
)


@router.post(
    "/definitions",
    response_model=CustomFieldRead,
    status_code=status.HTTP_201_CREATED,
)
def create_definition(
    payload: CustomFieldCreate,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    _: Party = Depends(require_role("admin")),
) -> CustomFieldDefinition:
    return custom_fields_service.create_field(db, tenant.id, payload)


@router.get("/definitions", response_model=list[CustomFieldRead])
def list_definitions(
    entity_type: str = Query(...),
    limit: int = Query(default=DEFAULT_LIMIT, ge=0, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    _: Party = Depends(require_role("admin")),
) -> list[CustomFieldDefinition]:
    definitions = custom_fields_service.list_for_entity(db, tenant.id, entity_type)
    return definitions[offset : offset + limit]


@router.get("/definitions/{field_id}", response_model=CustomFieldRead)
def get_definition(
    field_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    _: Party = Depends(require_role("admin")),
) -> CustomFieldDefinition:
    return custom_fields_service.get_field(db, tenant.id, field_id)


@router.patch("/definitions/{field_id}", response_model=CustomFieldRead)
def update_definition(
    field_id: UUID,
    payload: CustomFieldUpdate,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    _: Party = Depends(require_role("admin")),
) -> CustomFieldDefinition:
    updates = payload.model_dump(exclude_unset=True)
    return custom_fields_service.update_field(db, tenant.id, field_id, updates)


@router.delete(
    "/definitions/{field_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
def deactivate_definition(
    field_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    _: Party = Depends(require_role("admin")),
) -> None:
    custom_fields_service.deactivate_field(db, tenant.id, field_id)


@router.get("/{entity_type}/{entity_id}/values", response_model=CustomFieldValues)
def get_values(
    entity_type: str,
    entity_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    _: Party = Depends(require_role("admin")),
) -> dict[str, Any]:
    return custom_fields_service.get_values(db, tenant.id, entity_type, entity_id)


@router.put("/{entity_type}/{entity_id}/values", response_model=CustomFieldValues)
def set_values(
    entity_type: str,
    entity_id: UUID,
    payload: CustomFieldValues,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    _: Party = Depends(require_role("admin")),
) -> dict[str, Any]:
    return custom_fields_service.set_values(
        db, tenant.id, entity_type, entity_id, payload.root
    )
