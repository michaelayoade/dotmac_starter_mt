"""Behavior canaries preserved from Sub's plan-family catalogue."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.models import Tenant
from dotmac_service_catalog.contracts import (
    CharacteristicKind,
    CharacteristicValueInput,
    Conflict,
    CreateCharacteristic,
    CreateEligibilityInput,
    CreatePlanFamily,
    CreateServiceSpecification,
    NotFound,
    PublishPlanFamilyVersion,
    PublishServiceSpecificationVersion,
)
from dotmac_service_catalog.models import TENANT_TABLES
from dotmac_service_catalog.service import (
    add_characteristic,
    add_eligibility_input,
    create_plan_family,
    create_specification,
    effective_service_specification,
    publish_plan_family_version,
    publish_service_specification_version,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_svc_cat": None}},
    )
    Tenant.__table__.create(engine)
    from dotmac_service_catalog import models

    for name in TENANT_TABLES:
        models.metadata_table(name).create(engine)
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


def test_spec_family_characteristics_and_eligibility_are_one_technical_contract(
    db: Session,
) -> None:
    scope = TenantScope(TENANT_A)
    family = create_plan_family(
        db,
        scope=scope,
        command=CreatePlanFamily("ftth"),
    )
    specification = create_specification(
        db,
        scope=scope,
        command=CreateServiceSpecification("fiber-25", family.id),
    )
    characteristic = add_characteristic(
        db,
        scope=scope,
        command=CreateCharacteristic(
            specification.id,
            "download_mbps",
            "Download speed",
            CharacteristicKind.INTEGER,
            required=True,
        ),
    )
    eligibility = add_eligibility_input(
        db,
        scope=scope,
        command=CreateEligibilityInput(
            specification.id, "building_type", "Building type", required=True
        ),
    )
    assert (specification.code, family.code) == ("FIBER-25", "FTTH")
    assert characteristic.code == "DOWNLOAD_MBPS"
    assert eligibility.code == "BUILDING_TYPE"


def test_codes_are_unique_per_tenant_and_cross_tenant_links_are_refused(
    db: Session,
) -> None:
    family = create_plan_family(
        db, scope=TenantScope(TENANT_A), command=CreatePlanFamily("wireless")
    )
    with pytest.raises(Conflict):
        create_plan_family(
            db, scope=TenantScope(TENANT_A), command=CreatePlanFamily("wireless")
        )
    create_plan_family(
        db, scope=TenantScope(TENANT_B), command=CreatePlanFamily("wireless")
    )
    with pytest.raises(NotFound):
        create_specification(
            db,
            scope=TenantScope(TENANT_B),
            command=CreateServiceSpecification("foreign", family.id),
        )


def test_versioned_shape_carries_typed_values_and_flushes_only(
    db: Session,
) -> None:
    now = datetime(2026, 8, 23, 12, tzinfo=UTC)
    scope = TenantScope(TENANT_A)
    family = create_plan_family(db, scope=scope, command=CreatePlanFamily("dedicated"))
    family_version = publish_plan_family_version(
        db,
        scope=scope,
        command=PublishPlanFamilyVersion(
            family.id,
            1,
            "Dedicated Internet",
            now,
            "dotmac_sub.plan_family",
            uuid.uuid4(),
            1,
            uuid.uuid4(),
        ),
    )
    specification = create_specification(
        db,
        scope=scope,
        command=CreateServiceSpecification("fiber-100", family.id),
    )
    download = add_characteristic(
        db,
        scope=scope,
        command=CreateCharacteristic(
            specification.id,
            "download_mbps",
            "Download speed",
            CharacteristicKind.INTEGER,
            required=True,
            unit="Mbps",
        ),
    )
    access = add_characteristic(
        db,
        scope=scope,
        command=CreateCharacteristic(
            specification.id,
            "access_type",
            "Access type",
            CharacteristicKind.STRING,
            required=True,
        ),
    )
    aggregation = add_characteristic(
        db,
        scope=scope,
        command=CreateCharacteristic(
            specification.id,
            "aggregation",
            "Aggregation ratio",
            CharacteristicKind.DECIMAL,
            required=True,
        ),
    )
    published = publish_service_specification_version(
        db,
        scope=scope,
        command=PublishServiceSpecificationVersion(
            specification.id,
            family_version.id,
            1,
            "100 Mbps Dedicated Fibre",
            now,
            "dotmac_sub.catalog_offer",
            uuid.uuid4(),
            1,
            uuid.uuid4(),
            characteristics=(
                CharacteristicValueInput(download.id, 100),
                CharacteristicValueInput(access.id, "fiber"),
                CharacteristicValueInput(aggregation.id, Decimal("1")),
            ),
        ),
    )
    resolved = effective_service_specification(
        db, scope=scope, specification_id=specification.id, effective_at=now
    )
    assert resolved is not None
    assert resolved.version_id == published.id
    assert resolved.characteristics == {
        "ACCESS_TYPE": "fiber",
        "AGGREGATION": Decimal("1.000000"),
        "DOWNLOAD_MBPS": 100,
    }
    db.rollback()
    assert db.get(type(specification), specification.id) is None


def test_new_specification_version_supersedes_without_duplicating_family(
    db: Session,
) -> None:
    now = datetime(2026, 8, 23, 12, tzinfo=UTC)
    scope = TenantScope(TENANT_A)
    family = create_plan_family(db, scope=scope, command=CreatePlanFamily("home"))
    family_version = publish_plan_family_version(
        db,
        scope=scope,
        command=PublishPlanFamilyVersion(
            family.id,
            1,
            "Home",
            now,
            "seed",
            uuid.uuid4(),
            1,
            uuid.uuid4(),
        ),
    )
    specification = create_specification(
        db,
        scope=scope,
        command=CreateServiceSpecification("home-25", family.id),
    )
    characteristic = add_characteristic(
        db,
        scope=scope,
        command=CreateCharacteristic(
            specification.id,
            "download_mbps",
            "Download speed",
            CharacteristicKind.INTEGER,
            required=True,
        ),
    )
    first = PublishServiceSpecificationVersion(
        specification.id,
        family_version.id,
        1,
        "Home 25",
        now,
        "seed",
        uuid.uuid4(),
        1,
        uuid.uuid4(),
        characteristics=(CharacteristicValueInput(characteristic.id, 25),),
    )
    publish_service_specification_version(db, scope=scope, command=first)
    second_at = now + timedelta(days=30)
    publish_service_specification_version(
        db,
        scope=scope,
        command=PublishServiceSpecificationVersion(
            specification.id,
            family_version.id,
            2,
            "Home 50",
            second_at,
            "seed",
            first.source_id,
            2,
            uuid.uuid4(),
            characteristics=(CharacteristicValueInput(characteristic.id, 50),),
        ),
    )
    before = effective_service_specification(
        db,
        scope=scope,
        specification_id=specification.id,
        effective_at=second_at - timedelta(seconds=1),
    )
    after = effective_service_specification(
        db,
        scope=scope,
        specification_id=specification.id,
        effective_at=second_at,
    )
    assert before is not None and before.characteristics["DOWNLOAD_MBPS"] == 25
    assert after is not None and after.characteristics["DOWNLOAD_MBPS"] == 50
