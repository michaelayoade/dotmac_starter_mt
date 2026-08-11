"""Settings hierarchies have arbitrary depth, and isolation is not part of it.

The design claim under test: `tenant_id` carries isolation and ONLY isolation,
while `scope_kind`/`scope_id` carry precedence. Conflating them is what capped
the old model at two levels, and separating them is what leaves RLS untouched.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from dotmac_kernel import settings_resolver as sr
from dotmac_kernel.setting_scopes import (
    KERNEL_SCOPE_KINDS,
    PLATFORM,
    TENANT,
    DuplicateScopeKindError,
    ScopeError,
    ScopeKindRegistry,
    ScopeKindSpec,
    SettingScope,
    UndeclaredScopeKindError,
    install_scope_kinds,
    resolution_chain,
)
from dotmac_kernel.settings_models import DomainSetting, SettingDomain
from sqlalchemy import CheckConstraint

SITE = ScopeKindSpec(kind="site", rank=150, description="One physical site.")
USER = ScopeKindSpec(kind="user", rank=200, description="One person.")


@pytest.fixture()
def deep_registry():
    """A four-level hierarchy: user > site > tenant > platform."""
    registry = ScopeKindRegistry.from_specs([*KERNEL_SCOPE_KINDS, SITE, USER])
    install_scope_kinds(registry)
    yield registry
    install_scope_kinds(ScopeKindRegistry.from_specs(KERNEL_SCOPE_KINDS))


# ── The scope value object ──────────────────────────────────────────────────


def test_platform_belongs_to_no_tenant() -> None:
    assert SettingScope.platform().tenant_id is None


def test_every_other_scope_must_name_its_tenant() -> None:
    """The property that keeps isolation a STORED fact: there is no such thing
    as a site-scoped row floating outside a tenant."""
    with pytest.raises(ScopeError, match="needs a tenant_id"):
        SettingScope(kind="site", scope_id=uuid4())


def test_the_platform_scope_cannot_carry_a_tenant() -> None:
    with pytest.raises(ScopeError):
        SettingScope(kind=PLATFORM, tenant_id=uuid4())


def test_the_tenant_scope_has_no_instance() -> None:
    with pytest.raises(ScopeError):
        SettingScope(kind=TENANT, tenant_id=uuid4(), scope_id=uuid4())


def test_database_default_for_an_unscoped_raw_write_is_platform() -> None:
    """A DB-side default cannot inspect tenant_id, so it takes the safe shape.

    ORM writes use ``_default_scope_kind`` and still derive tenant scope when a
    tenant is present. The server default exists for raw SQL and migrations;
    those callers must name tenant scope explicitly rather than being allowed
    to create a tenant-scoped row with no tenant.
    """

    default = DomainSetting.__table__.c.scope_kind.server_default

    assert default is not None
    assert str(default.arg) == PLATFORM


def test_database_schema_enforces_scope_and_tenant_alignment() -> None:
    constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in DomainSetting.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert constraints["ck_domain_settings_scope_alignment"] == (
        "(scope_kind = 'platform' AND tenant_id IS NULL) "
        "OR (scope_kind <> 'platform' AND tenant_id IS NOT NULL)"
    )


# ── Declaring a hierarchy ───────────────────────────────────────────────────


def test_a_product_declares_its_own_levels(deep_registry) -> None:
    """The strategic point: depth is declared, not fixed by the kernel."""
    assert [spec.kind for spec in deep_registry.kinds()] == [
        "user",
        "site",
        TENANT,
        PLATFORM,
    ]


def test_two_kinds_may_not_share_a_rank() -> None:
    """Equal ranks means undefined precedence — which of two overrides wins
    would depend on dict ordering."""
    with pytest.raises(ScopeError, match="rank"):
        ScopeKindRegistry.from_specs(
            [ScopeKindSpec(kind="a", rank=5), ScopeKindSpec(kind="b", rank=5)]
        )


def test_two_declarations_of_one_kind_fail() -> None:
    with pytest.raises(DuplicateScopeKindError):
        ScopeKindRegistry.from_specs(
            [ScopeKindSpec(kind="site", rank=1), ScopeKindSpec(kind="site", rank=2)]
        )


def test_an_undeclared_kind_is_refused() -> None:
    with pytest.raises(UndeclaredScopeKindError):
        ScopeKindRegistry.from_specs(KERNEL_SCOPE_KINDS).require("site")


def test_a_kind_may_not_contain_the_key_separator() -> None:
    """It is written into cache keys, where a ':' could forge another shape."""
    with pytest.raises(ScopeError):
        ScopeKindSpec(kind="a:b", rank=1)


# ── The chain ───────────────────────────────────────────────────────────────


def test_a_deep_scope_falls_back_through_its_tenant_to_the_platform(
    deep_registry,
) -> None:
    tenant, site = uuid4(), uuid4()
    chain = resolution_chain(
        SettingScope(kind="site", tenant_id=tenant, scope_id=site), deep_registry
    )
    assert [scope.kind for scope in chain] == ["site", TENANT, PLATFORM]
    assert chain[1].tenant_id == tenant
    assert chain[2].tenant_id is None


def test_the_platform_chain_is_just_itself(deep_registry) -> None:
    assert resolution_chain(SettingScope.platform(), deep_registry) == (
        SettingScope.platform(),
    )


# ── Through the resolver, against real rows ─────────────────────────────────


def test_a_finer_scope_overrides_its_tenant(db, tenant_row, deep_registry) -> None:
    site = uuid4()
    sr.upsert_by_key(
        db, SettingDomain.audit, "retention_days", 30, tenant_id=tenant_row.id
    )
    sr.upsert_by_key(
        db,
        SettingDomain.audit,
        "retention_days",
        7,
        scope=SettingScope(kind="site", tenant_id=tenant_row.id, scope_id=site),
    )

    at_site = sr.resolve_with_source(
        db,
        SettingDomain.audit,
        "retention_days",
        scope=SettingScope(kind="site", tenant_id=tenant_row.id, scope_id=site),
    )
    assert at_site == (7, "site")

    # The tenant's own value is untouched, and another site still gets it.
    assert (
        sr.resolve_value(
            db, SettingDomain.audit, "retention_days", tenant_id=tenant_row.id
        )
        == 30
    )
    assert (
        sr.resolve_value(
            db,
            SettingDomain.audit,
            "retention_days",
            scope=SettingScope(kind="site", tenant_id=tenant_row.id, scope_id=uuid4()),
        )
        == 30
    )


def test_a_site_with_no_value_falls_through_to_the_platform(
    db, tenant_row, deep_registry
) -> None:
    sr.upsert_by_key(db, SettingDomain.audit, "retention_days", 90, tenant_id=None)
    value, source = sr.resolve_with_source(
        db,
        SettingDomain.audit,
        "retention_days",
        scope=SettingScope(kind="site", tenant_id=tenant_row.id, scope_id=uuid4()),
    )
    assert (value, source) == (90, PLATFORM)


def test_naming_both_a_tenant_and_a_scope_is_an_error(db, tenant_row) -> None:
    """They mean the same thing and there is no rule for which would win."""
    with pytest.raises(TypeError, match="not both"):
        sr.resolve_value(
            db,
            SettingDomain.audit,
            "retention_days",
            tenant_id=tenant_row.id,
            scope=SettingScope.tenant(tenant_row.id),
        )


def test_writing_to_an_undeclared_kind_is_refused(db, tenant_row) -> None:
    with pytest.raises(UndeclaredScopeKindError):
        sr.upsert_by_key(
            db,
            SettingDomain.audit,
            "retention_days",
            1,
            scope=SettingScope(kind="warehouse", tenant_id=tenant_row.id),
        )


def test_isolation_is_still_a_stored_column(db, tenant_row, deep_registry) -> None:
    """The whole reason the axes were split: a row at any depth still carries
    its tenant, so RLS reads it directly rather than deriving it."""
    from dotmac_kernel.settings_models import DomainSetting

    site = uuid4()
    sr.upsert_by_key(
        db,
        SettingDomain.audit,
        "retention_days",
        7,
        scope=SettingScope(kind="site", tenant_id=tenant_row.id, scope_id=site),
    )
    row = db.query(DomainSetting).filter_by(scope_kind="site").one()
    assert row.tenant_id == tenant_row.id
    assert row.scope_id == site
