"""Behavior canaries preserved from Sub's plan-family catalogue."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.models import Tenant
from dotmac_service_catalog.contracts import (
    CharacteristicKind,
    Conflict,
    CreateCharacteristic,
    CreateEligibilityInput,
    CreatePlanFamily,
    CreateServiceSpecification,
    NotFound,
)
from dotmac_service_catalog.models import TENANT_TABLES
from dotmac_service_catalog.service import (
    add_characteristic,
    add_eligibility_input,
    create_plan_family,
    create_specification,
    set_specification_active,
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
    specification = create_specification(
        db,
        scope=scope,
        command=CreateServiceSpecification("fiber", "Fibre Internet"),
    )
    family = create_plan_family(
        db,
        scope=scope,
        command=CreatePlanFamily("ftth", "FTTH", specification.id),
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
    assert (specification.code, family.code) == ("FIBER", "FTTH")
    assert characteristic.code == "DOWNLOAD_MBPS"
    assert eligibility.code == "BUILDING_TYPE"


def test_codes_are_unique_per_tenant_and_cross_tenant_links_are_refused(
    db: Session,
) -> None:
    command = CreateServiceSpecification("wireless", "Wireless")
    specification = create_specification(
        db, scope=TenantScope(TENANT_A), command=command
    )
    with pytest.raises(Conflict):
        create_specification(db, scope=TenantScope(TENANT_A), command=command)
    create_specification(db, scope=TenantScope(TENANT_B), command=command)
    with pytest.raises(NotFound):
        create_plan_family(
            db,
            scope=TenantScope(TENANT_B),
            command=CreatePlanFamily("foreign", "Foreign", specification.id),
        )


def test_active_state_is_a_technical_availability_switch_and_flush_only(
    db: Session,
) -> None:
    specification = create_specification(
        db,
        scope=TenantScope(TENANT_A),
        command=CreateServiceSpecification("lte", "LTE"),
    )
    set_specification_active(
        db, scope=TenantScope(TENANT_A), specification_id=specification.id, active=False
    )
    assert specification.is_active is False
    db.rollback()
    assert db.get(type(specification), specification.id) is None
