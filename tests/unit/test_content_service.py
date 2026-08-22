"""Mkt editorial parity for the product-neutral content owner."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime

import pytest
from dotmac_content import (
    AttachItemCreative,
    AttachPlanCreative,
    Conflict,
    ContentItemState,
    ContentPlanStatus,
    CreateContentItem,
    CreateContentPlan,
    CreateContentVariant,
    NotFound,
    UpdateContentItem,
    UpdateContentPlan,
    attach_item_creative,
    attach_plan_creative,
    build_content_snapshot,
    count_content_items,
    count_content_plans,
    create_content_item,
    create_content_plan,
    create_content_variant,
    get_content_item,
    get_content_plan,
    list_content_items,
    list_content_plans,
    update_content_item,
    update_content_plan,
)
from dotmac_content.models import TENANT_TABLES, metadata_table
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.models import Tenant
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()
ACTOR = uuid.uuid4()
NOW = datetime(2026, 8, 19, 9, 30, tzinfo=UTC)


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_content": None}},
    )
    Tenant.__table__.create(engine)
    for table_name in TENANT_TABLES:
        metadata_table(table_name).create(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id=TENANT_A, slug="alpha", name="Alpha"),
                Tenant(id=TENANT_B, slug="bravo", name="Bravo"),
            ]
        )
        session.flush()
        yield session
    engine.dispose()


def _plan(db: Session, *, tenant: uuid.UUID = TENANT_A, name: str = "Launch"):
    return create_content_plan(
        db,
        scope=TenantScope(tenant),
        command=CreateContentPlan(
            name=name,
            description="Editorial launch plan",
            starts_on=date(2026, 9, 1),
            ends_on=date(2026, 9, 30),
            created_by_ref=ACTOR,
        ),
    )


def _item(db: Session, plan_id: uuid.UUID, *, title: str = "Welcome"):
    return create_content_item(
        db,
        scope=TenantScope(TENANT_A),
        command=CreateContentItem(
            content_plan_id=plan_id,
            title=title,
            body="Canonical copy",
            created_by_ref=ACTOR,
            planned_for=NOW,
        ),
    )


def test_plan_crud_filters_counts_and_transitions_inside_the_tenant(
    db: Session,
) -> None:
    plan = _plan(db, name="  Launch  ")
    _plan(db, tenant=TENANT_B, name="Other")

    assert plan.name == "Launch"
    assert get_content_plan(db, scope=TenantScope(TENANT_A), plan_id=plan.id) is plan
    assert list_content_plans(db, scope=TenantScope(TENANT_A)) == (plan,)
    assert count_content_plans(db, scope=TenantScope(TENANT_A)) == 1
    with pytest.raises(NotFound):
        get_content_plan(db, scope=TenantScope(TENANT_B), plan_id=plan.id)

    updated = update_content_plan(
        db,
        scope=TenantScope(TENANT_A),
        plan_id=plan.id,
        command=UpdateContentPlan(
            name="Launch revised",
            description=None,
            status=ContentPlanStatus.ACTIVE,
            starts_on=date(2026, 9, 2),
            ends_on=date(2026, 10, 1),
        ),
    )
    assert updated.name == "Launch revised"
    assert updated.description is None
    assert updated.status == ContentPlanStatus.ACTIVE


def test_plan_update_refuses_an_inverted_date_range(db: Session) -> None:
    plan = _plan(db)
    with pytest.raises(ValueError, match="ends_on"):
        update_content_plan(
            db,
            scope=TenantScope(TENANT_A),
            plan_id=plan.id,
            command=UpdateContentPlan(
                name=plan.name,
                description=plan.description,
                status=ContentPlanStatus.DRAFT,
                starts_on=date(2026, 10, 1),
                ends_on=date(2026, 9, 1),
            ),
        )


def test_item_crud_is_editorial_and_cannot_reference_another_tenants_plan(
    db: Session,
) -> None:
    plan = _plan(db)
    foreign_plan = _plan(db, tenant=TENANT_B, name="Foreign")
    item = _item(db, plan.id, title="  Welcome  ")

    assert item.title == "Welcome"
    assert get_content_item(db, scope=TenantScope(TENANT_A), item_id=item.id) is item
    assert list_content_items(
        db, scope=TenantScope(TENANT_A), content_plan_id=plan.id
    ) == (item,)
    assert count_content_items(db, scope=TenantScope(TENANT_A)) == 1

    with pytest.raises(NotFound, match="plan"):
        _item(db, foreign_plan.id)

    updated = update_content_item(
        db,
        scope=TenantScope(TENANT_A),
        item_id=item.id,
        command=UpdateContentItem(
            title="Ready copy",
            body="Reviewed canonical copy",
            state=ContentItemState.READY,
            planned_for=NOW,
        ),
    )
    assert updated.state == ContentItemState.READY
    assert updated.body == "Reviewed canonical copy"


def test_variant_key_is_open_but_unique_per_item(db: Session) -> None:
    item = _item(db, _plan(db).id)
    command = CreateContentVariant(
        content_item_id=item.id,
        variant_key="linkedin-long",
        title_override="A longer title",
        body_override=None,
        sort_order=2,
    )
    variant = create_content_variant(db, scope=TenantScope(TENANT_A), command=command)
    assert variant.variant_key == "linkedin-long"
    with pytest.raises(Conflict, match="variant"):
        create_content_variant(db, scope=TenantScope(TENANT_A), command=command)


def test_snapshot_orders_variants_and_creatives_without_orm_objects(
    db: Session,
) -> None:
    plan = _plan(db)
    item = _item(db, plan.id)
    for key, order in (("long", 20), ("short", 10)):
        create_content_variant(
            db,
            scope=TenantScope(TENANT_A),
            command=CreateContentVariant(
                content_item_id=item.id,
                variant_key=key,
                title_override=None,
                body_override=f"{key} copy",
                sort_order=order,
            ),
        )

    plan_file = uuid.uuid4()
    item_file_later = uuid.uuid4()
    item_file_first = uuid.uuid4()
    attach_plan_creative(
        db,
        scope=TenantScope(TENANT_A),
        command=AttachPlanCreative(
            content_plan_id=plan.id,
            file_ref=plan_file,
            role="hero",
            caption=None,
            alt_text="Launch hero",
            sort_order=0,
        ),
    )
    for file_ref, order in ((item_file_later, 2), (item_file_first, 1)):
        attach_item_creative(
            db,
            scope=TenantScope(TENANT_A),
            command=AttachItemCreative(
                content_item_id=item.id,
                file_ref=file_ref,
                role="inline",
                caption=None,
                alt_text=None,
                sort_order=order,
            ),
        )

    snapshot = build_content_snapshot(
        db, scope=TenantScope(TENANT_A), content_item_id=item.id
    )
    assert snapshot.content_item_id == item.id
    assert snapshot.content_plan_id == plan.id
    assert [variant.variant_key for variant in snapshot.variants] == ["short", "long"]
    assert [creative.file_ref for creative in snapshot.item_creatives] == [
        item_file_first,
        item_file_later,
    ]
    assert [creative.file_ref for creative in snapshot.plan_creatives] == [plan_file]
    assert all(not hasattr(value, "__table__") for value in snapshot.variants)


def test_explicit_tenant_scope_is_required(db: Session) -> None:
    with pytest.raises(TypeError, match="TenantScope"):
        create_content_plan(  # type: ignore[arg-type]
            db,
            scope=TENANT_A,
            command=CreateContentPlan(name="No scope", created_by_ref=ACTOR),
        )
