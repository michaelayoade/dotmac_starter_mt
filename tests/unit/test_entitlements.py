"""Unit tests for WS2 tenant entitlements (SQLite, no RLS — isolation is proven
on Postgres in `tests/test_entitlements_isolation.py`).

Covers the evaluator contract: a grant may only reference a DECLARED capability
code (WS1); `is_entitled` is an explainable local read; revocation is explicit.
"""

from __future__ import annotations

import pytest
from dotmac_kernel import (
    CapabilityCatalogue,
    FeatureManifest,
    TenantEntitlementGrant,
    grant_entitlement,
    is_entitled,
)
from dotmac_kernel.capabilities import UndeclaredCapabilityError
from dotmac_kernel.models import Tenant
from sqlalchemy import func, select
from sqlalchemy.orm import Session

_CATALOGUE = CapabilityCatalogue.from_manifests(
    [FeatureManifest(name="billing", capabilities=("billing.use", "billing.export"))]
)


def test_grant_then_is_entitled(db: Session, tenant_row: Tenant) -> None:
    grant_entitlement(
        db,
        tenant_id=tenant_row.id,
        capability_code="billing.use",
        catalogue=_CATALOGUE,
        limits={"seats": 5},
        source="vendor_allocation",
    )
    decision = is_entitled(db, tenant_id=tenant_row.id, capability_code="billing.use")
    assert decision.allowed
    assert decision.reason == "granted"
    assert decision.limits == {"seats": 5}


def test_ungranted_code_is_not_allowed(db: Session, tenant_row: Tenant) -> None:
    decision = is_entitled(
        db, tenant_id=tenant_row.id, capability_code="billing.export"
    )
    assert not decision.allowed
    assert decision.reason == "not_granted"


def test_grant_of_undeclared_code_is_rejected(db: Session, tenant_row: Tenant) -> None:
    with pytest.raises(UndeclaredCapabilityError):
        grant_entitlement(
            db,
            tenant_id=tenant_row.id,
            capability_code="inventory.use",  # not declared by _CATALOGUE
            catalogue=_CATALOGUE,
        )


def test_revocation_is_explicit_and_explainable(
    db: Session, tenant_row: Tenant
) -> None:
    grant_entitlement(
        db, tenant_id=tenant_row.id, capability_code="billing.use", catalogue=_CATALOGUE
    )
    # Re-grant with granted=False (revoke) — upserts, keeps provenance.
    grant_entitlement(
        db,
        tenant_id=tenant_row.id,
        capability_code="billing.use",
        catalogue=_CATALOGUE,
        granted=False,
    )
    # Scoped to the code under test: the shared `tenant_row` fixture arrives
    # already entitled to every capability the assembly declares (mirroring
    # migration a004), so a global count would measure the fixture, not the
    # upsert this test is about.
    assert (
        db.scalar(
            select(func.count())
            .select_from(TenantEntitlementGrant)
            .where(TenantEntitlementGrant.capability_code == "billing.use")
        )
        == 1
    )
    decision = is_entitled(db, tenant_id=tenant_row.id, capability_code="billing.use")
    assert not decision.allowed
    assert decision.reason == "revoked"
