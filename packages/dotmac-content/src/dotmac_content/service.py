"""Tenant-scoped editorial decisions extracted from Mkt.

Every mutator runs inside the caller's transaction, changes only content-owned
rows, and flushes. It never authorizes an actor, resolves a file, commits,
rolls back, publishes, or performs provider I/O.
"""

from __future__ import annotations

from typing import TypeVar
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_content.contracts import (
    AttachItemCreative,
    AttachPlanCreative,
    Conflict,
    ContentCreativeReferenceV1,
    ContentSnapshotV1,
    ContentVariantSnapshotV1,
    CreateContentItem,
    CreateContentPlan,
    CreateContentVariant,
    NotFound,
    UpdateContentItem,
    UpdateContentPlan,
)
from dotmac_content.lifecycle import (
    check_item_transition,
    check_plan_transition,
    validate_plan_date_range,
)
from dotmac_content.models import (
    ContentItem,
    ContentItemCreative,
    ContentPlan,
    ContentPlanCreative,
    ContentVariant,
)

_Model = TypeVar("_Model")


def _tenant(scope: TenantScope) -> UUID:
    if not isinstance(scope, TenantScope):
        raise TypeError("dotmac-content requires an explicit TenantScope")
    return scope.tenant_id


def _required_text(value: str, *, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    return normalized


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _one(db: Session, statement: Select[tuple[_Model]], *, detail: str) -> _Model:
    result = db.scalar(statement)
    if result is None:
        raise NotFound(detail)
    return result


def _flush_new(db: Session, record: _Model, *, detail: str) -> _Model:
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(record)
            db.flush()
    except IntegrityError as exc:
        raise Conflict(detail) from exc
    return record


def _plan(db: Session, tenant_id: UUID, plan_id: UUID) -> ContentPlan:
    return _one(
        db,
        select(ContentPlan).where(
            ContentPlan.tenant_id == tenant_id, ContentPlan.id == plan_id
        ),
        detail=f"content plan {plan_id} was not found",
    )


def _item(db: Session, tenant_id: UUID, item_id: UUID) -> ContentItem:
    return _one(
        db,
        select(ContentItem).where(
            ContentItem.tenant_id == tenant_id, ContentItem.id == item_id
        ),
        detail=f"content item {item_id} was not found",
    )


def create_content_plan(
    db: Session, *, scope: TenantScope, command: CreateContentPlan
) -> ContentPlan:
    tenant_id = _tenant(scope)
    validate_plan_date_range(command.starts_on, command.ends_on)
    return _flush_new(
        db,
        ContentPlan(
            tenant_id=tenant_id,
            name=_required_text(command.name, field="content plan name"),
            description=_optional_text(command.description),
            starts_on=command.starts_on,
            ends_on=command.ends_on,
            created_by_ref=command.created_by_ref,
        ),
        detail="content plan conflicts",
    )


def get_content_plan(db: Session, *, scope: TenantScope, plan_id: UUID) -> ContentPlan:
    return _plan(db, _tenant(scope), plan_id)


def list_content_plans(db: Session, *, scope: TenantScope) -> tuple[ContentPlan, ...]:
    tenant_id = _tenant(scope)
    return tuple(
        db.scalars(
            select(ContentPlan)
            .where(ContentPlan.tenant_id == tenant_id)
            .order_by(ContentPlan.created_at, ContentPlan.id)
        )
    )


def count_content_plans(db: Session, *, scope: TenantScope) -> int:
    tenant_id = _tenant(scope)
    return int(
        db.scalar(
            select(func.count(ContentPlan.id)).where(ContentPlan.tenant_id == tenant_id)
        )
        or 0
    )


def update_content_plan(
    db: Session,
    *,
    scope: TenantScope,
    plan_id: UUID,
    command: UpdateContentPlan,
) -> ContentPlan:
    plan = _plan(db, _tenant(scope), plan_id)
    validate_plan_date_range(command.starts_on, command.ends_on)
    check_plan_transition(plan.status, command.status)
    plan.name = _required_text(command.name, field="content plan name")
    plan.description = _optional_text(command.description)
    plan.status = command.status
    plan.starts_on = command.starts_on
    plan.ends_on = command.ends_on
    db.flush()
    return plan


def create_content_item(
    db: Session, *, scope: TenantScope, command: CreateContentItem
) -> ContentItem:
    tenant_id = _tenant(scope)
    _plan(db, tenant_id, command.content_plan_id)
    return _flush_new(
        db,
        ContentItem(
            tenant_id=tenant_id,
            content_plan_id=command.content_plan_id,
            title=_required_text(command.title, field="content item title"),
            body=_required_text(command.body, field="content item body"),
            planned_for=command.planned_for,
            created_by_ref=command.created_by_ref,
        ),
        detail="content item conflicts",
    )


def get_content_item(db: Session, *, scope: TenantScope, item_id: UUID) -> ContentItem:
    return _item(db, _tenant(scope), item_id)


def list_content_items(
    db: Session,
    *,
    scope: TenantScope,
    content_plan_id: UUID | None = None,
) -> tuple[ContentItem, ...]:
    tenant_id = _tenant(scope)
    statement = select(ContentItem).where(ContentItem.tenant_id == tenant_id)
    if content_plan_id is not None:
        statement = statement.where(ContentItem.content_plan_id == content_plan_id)
    return tuple(db.scalars(statement.order_by(ContentItem.created_at, ContentItem.id)))


def count_content_items(
    db: Session,
    *,
    scope: TenantScope,
    content_plan_id: UUID | None = None,
) -> int:
    tenant_id = _tenant(scope)
    statement = select(func.count(ContentItem.id)).where(
        ContentItem.tenant_id == tenant_id
    )
    if content_plan_id is not None:
        statement = statement.where(ContentItem.content_plan_id == content_plan_id)
    return int(db.scalar(statement) or 0)


def update_content_item(
    db: Session,
    *,
    scope: TenantScope,
    item_id: UUID,
    command: UpdateContentItem,
) -> ContentItem:
    item = _item(db, _tenant(scope), item_id)
    check_item_transition(item.state, command.state)
    item.title = _required_text(command.title, field="content item title")
    item.body = _required_text(command.body, field="content item body")
    item.state = command.state
    item.planned_for = command.planned_for
    db.flush()
    return item


def create_content_variant(
    db: Session, *, scope: TenantScope, command: CreateContentVariant
) -> ContentVariant:
    tenant_id = _tenant(scope)
    _item(db, tenant_id, command.content_item_id)
    key = _required_text(command.variant_key, field="variant key")
    if db.scalar(
        select(ContentVariant.id).where(
            ContentVariant.tenant_id == tenant_id,
            ContentVariant.content_item_id == command.content_item_id,
            ContentVariant.variant_key == key,
        )
    ):
        raise Conflict(f"variant {key!r} already exists for this item")
    return _flush_new(
        db,
        ContentVariant(
            tenant_id=tenant_id,
            content_item_id=command.content_item_id,
            variant_key=key,
            title_override=_optional_text(command.title_override),
            body_override=_optional_text(command.body_override),
            sort_order=command.sort_order,
        ),
        detail=f"variant {key!r} conflicts",
    )


def attach_plan_creative(
    db: Session, *, scope: TenantScope, command: AttachPlanCreative
) -> ContentPlanCreative:
    tenant_id = _tenant(scope)
    _plan(db, tenant_id, command.content_plan_id)
    return _flush_new(
        db,
        ContentPlanCreative(
            tenant_id=tenant_id,
            content_plan_id=command.content_plan_id,
            file_ref=command.file_ref,
            role=_required_text(command.role, field="creative role"),
            caption=_optional_text(command.caption),
            alt_text=_optional_text(command.alt_text),
            sort_order=command.sort_order,
        ),
        detail="plan creative conflicts",
    )


def attach_item_creative(
    db: Session, *, scope: TenantScope, command: AttachItemCreative
) -> ContentItemCreative:
    tenant_id = _tenant(scope)
    _item(db, tenant_id, command.content_item_id)
    return _flush_new(
        db,
        ContentItemCreative(
            tenant_id=tenant_id,
            content_item_id=command.content_item_id,
            file_ref=command.file_ref,
            role=_required_text(command.role, field="creative role"),
            caption=_optional_text(command.caption),
            alt_text=_optional_text(command.alt_text),
            sort_order=command.sort_order,
        ),
        detail="item creative conflicts",
    )


def _creative_value(
    creative: ContentPlanCreative | ContentItemCreative,
) -> ContentCreativeReferenceV1:
    return ContentCreativeReferenceV1(
        creative_id=creative.id,
        file_ref=creative.file_ref,
        role=creative.role,
        caption=creative.caption,
        alt_text=creative.alt_text,
        sort_order=creative.sort_order,
    )


def build_content_snapshot(
    db: Session, *, scope: TenantScope, content_item_id: UUID
) -> ContentSnapshotV1:
    tenant_id = _tenant(scope)
    item = _item(db, tenant_id, content_item_id)
    variants = tuple(
        ContentVariantSnapshotV1(
            variant_id=value.id,
            variant_key=value.variant_key,
            title_override=value.title_override,
            body_override=value.body_override,
            sort_order=value.sort_order,
        )
        for value in db.scalars(
            select(ContentVariant)
            .where(
                ContentVariant.tenant_id == tenant_id,
                ContentVariant.content_item_id == item.id,
            )
            .order_by(ContentVariant.sort_order, ContentVariant.id)
        )
    )
    plan_creatives = tuple(
        _creative_value(value)
        for value in db.scalars(
            select(ContentPlanCreative)
            .where(
                ContentPlanCreative.tenant_id == tenant_id,
                ContentPlanCreative.content_plan_id == item.content_plan_id,
            )
            .order_by(ContentPlanCreative.sort_order, ContentPlanCreative.id)
        )
    )
    item_creatives = tuple(
        _creative_value(value)
        for value in db.scalars(
            select(ContentItemCreative)
            .where(
                ContentItemCreative.tenant_id == tenant_id,
                ContentItemCreative.content_item_id == item.id,
            )
            .order_by(ContentItemCreative.sort_order, ContentItemCreative.id)
        )
    )
    return ContentSnapshotV1(
        content_item_id=item.id,
        content_plan_id=item.content_plan_id,
        title=item.title,
        body=item.body,
        state=item.state,
        planned_for=item.planned_for,
        created_by_ref=item.created_by_ref,
        variants=variants,
        plan_creatives=plan_creatives,
        item_creatives=item_creatives,
    )


__all__ = [
    "attach_item_creative",
    "attach_plan_creative",
    "build_content_snapshot",
    "count_content_items",
    "count_content_plans",
    "create_content_item",
    "create_content_plan",
    "create_content_variant",
    "get_content_item",
    "get_content_plan",
    "list_content_items",
    "list_content_plans",
    "update_content_item",
    "update_content_plan",
]
