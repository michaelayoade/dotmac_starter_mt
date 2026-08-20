"""Per-service access-policy canaries ported from Sub."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.models import Tenant
from dotmac_service_access_policy.contracts import (
    AccessSignal,
    DesiredAccess,
    RecordAccessInput,
    ResolveDesiredAccess,
)
from dotmac_service_access_policy.models import TENANT_TABLES
from dotmac_service_access_policy.service import (
    record_access_input,
    resolve_desired_access,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

TENANT_A = uuid.uuid4()
NOW = datetime(2026, 8, 20, tzinfo=UTC)


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_svc_access": None}},
    )
    Tenant.__table__.create(engine)
    from dotmac_service_access_policy import models

    for name in TENANT_TABLES:
        models.metadata_table(name).create(engine)
    with Session(engine) as session:
        session.add(Tenant(id=TENANT_A, slug="a", name="A"))
        session.flush()
        yield session
    engine.dispose()


def test_consequences_are_scoped_to_one_service(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    record_access_input(
        db,
        scope=scope,
        command=RecordAccessInput(
            "service:1", AccessSignal.COLLECTIONS_HOLD, True, "collections:1", NOW
        ),
    )
    denied = resolve_desired_access(
        db, scope=scope, command=ResolveDesiredAccess("service:1", NOW)
    )
    unaffected = resolve_desired_access(
        db, scope=scope, command=ResolveDesiredAccess("service:2", NOW)
    )
    assert (denied.desired_access, denied.reason_code) == (
        DesiredAccess.DENY,
        "COLLECTIONS_HOLD",
    )
    assert unaffected.desired_access == DesiredAccess.ALLOW


def test_fup_restricts_while_admin_hold_denies_and_clear_repairs(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    for signal in (AccessSignal.FUP_EXHAUSTED, AccessSignal.ADMIN_HOLD):
        record_access_input(
            db,
            scope=scope,
            command=RecordAccessInput("service:3", signal, True, "source", NOW),
        )
    decision = resolve_desired_access(
        db, scope=scope, command=ResolveDesiredAccess("service:3", NOW)
    )
    assert decision.desired_access == DesiredAccess.DENY
    record_access_input(
        db,
        scope=scope,
        command=RecordAccessInput(
            "service:3", AccessSignal.ADMIN_HOLD, False, "admin", NOW
        ),
    )
    repaired = resolve_desired_access(
        db, scope=scope, command=ResolveDesiredAccess("service:3", NOW)
    )
    assert repaired.desired_access == DesiredAccess.RESTRICT
