"""Service-lifecycle behavior canaries ported from Sub."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.models import Tenant
from dotmac_services.contracts import (
    Conflict,
    CreateService,
    ServiceStatus,
    TransitionService,
)
from dotmac_services.models import TENANT_TABLES
from dotmac_services.service import create_service, transition_service
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()
NOW = datetime(2026, 8, 20, tzinfo=UTC)


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_services": None}},
    )
    Tenant.__table__.create(engine)
    from dotmac_services import models

    for name in TENANT_TABLES:
        models.metadata_table(name).create(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id=TENANT_A, slug="a", name="A"),
                Tenant(id=TENANT_B, slug="b", name="B"),
            ]
        )
        session.flush()
        yield session
    engine.dispose()


def test_service_status_has_one_owned_transition_path(db: Session) -> None:
    service = create_service(
        db,
        scope=TenantScope(TENANT_A),
        command=CreateService("customer:1", "spec:fiber", "qualification:1"),
    )
    event = transition_service(
        db,
        scope=TenantScope(TENANT_A),
        command=TransitionService(
            service.id,
            ServiceStatus.ACTIVE,
            "installation accepted",
            NOW,
        ),
    )
    assert service.status == ServiceStatus.ACTIVE
    assert service.activated_at == NOW
    assert (event.from_status, event.to_status) == (
        ServiceStatus.ORDERED,
        ServiceStatus.ACTIVE,
    )


def test_invalid_or_cross_tenant_transition_is_refused(db: Session) -> None:
    service = create_service(
        db,
        scope=TenantScope(TENANT_A),
        command=CreateService("customer:2", "spec:lte"),
    )
    with pytest.raises(Conflict, match="transition"):
        transition_service(
            db,
            scope=TenantScope(TENANT_A),
            command=TransitionService(
                service.id,
                ServiceStatus.SUSPENDED,
                "not active",
                NOW,
            ),
        )
    with pytest.raises(Conflict, match="not found"):
        transition_service(
            db,
            scope=TenantScope(TENANT_B),
            command=TransitionService(
                service.id,
                ServiceStatus.ACTIVE,
                "wrong tenant",
                NOW,
            ),
        )


def test_services_are_flush_only_and_caller_rollback_discards(db: Session) -> None:
    service = create_service(
        db,
        scope=TenantScope(TENANT_A),
        command=CreateService("customer:3", "spec:voice"),
    )
    db.rollback()
    assert db.get(type(service), service.id) is None
