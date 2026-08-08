"""The settings read cache: scoped by construction, invalidated at the write.

The failure this guards against raises no exception — tenant B is served the
entry tenant A populated — so these tests assert on the KEY and on what survives
an invalidation, not merely on hit/miss counts.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from dotmac_kernel import settings_cache as sc
from dotmac_kernel import settings_crypto as scrypto
from dotmac_kernel import settings_resolver as sr
from dotmac_kernel.cache import MemoryCache
from dotmac_kernel.setting_scopes import SettingScope
from dotmac_kernel.settings_models import SettingDomain, SettingValueType


def _scope(tenant_id):
    """Scope for a tenant id, or the platform scope for None."""
    return (
        SettingScope.platform() if tenant_id is None else SettingScope.tenant(tenant_id)
    )


TENANT_A = uuid4()
TENANT_B = uuid4()


@pytest.fixture(autouse=True)
def _store():
    store = MemoryCache()
    sc.install_settings_cache(store)
    yield store
    sc.install_settings_cache(None)


# ── Keys ────────────────────────────────────────────────────────────────────


def test_two_tenants_never_share_a_key():
    """The whole design in one assertion."""
    a = sc.setting_cache_key(
        "auth", "registration_policy", scope=SettingScope.tenant(TENANT_A)
    )
    b = sc.setting_cache_key(
        "auth", "registration_policy", scope=SettingScope.tenant(TENANT_B)
    )
    platform = sc.setting_cache_key(
        "auth", "registration_policy", scope=SettingScope.platform()
    )
    assert len({a, b, platform}) == 3


def test_the_scope_is_the_last_segment():
    """Identity first, scope last — what makes "this setting, all scopes" a
    prefix and "everything of one tenant's" deliberately not one."""
    key = sc.setting_cache_key(
        "auth", "registration_policy", scope=SettingScope.tenant(TENANT_A)
    )
    assert key.endswith(f":t={TENANT_A}")
    assert key.startswith("settings:auth:k=registration_policy:")


def test_the_prefix_covers_every_scope_for_one_setting():
    prefix = sc.setting_key_prefix("auth", "registration_policy")
    for tenant_id in (TENANT_A, TENANT_B, None):
        assert sc.setting_cache_key(
            "auth", "registration_policy", scope=_scope(tenant_id)
        ).startswith(prefix)
    # ...and not a different setting's.
    assert not sc.setting_cache_key(
        "auth", "other", scope=SettingScope.platform()
    ).startswith(prefix)


# ── Read-through ────────────────────────────────────────────────────────────


def test_a_resolved_value_is_cached_and_served(db, _store):
    sr.upsert_by_key(db, SettingDomain.audit, "retention_days", 30, tenant_id=None)
    assert (
        sr.resolve_value(db, SettingDomain.audit, "retention_days", tenant_id=None)
        == 30
    )
    assert _store.get(
        sc.setting_cache_key("audit", "retention_days", scope=SettingScope.platform())
    ) == (
        30,
        "platform",
    )


def test_a_cached_none_is_not_re_resolved_forever(db, _store):
    """`None` is a legitimate resolved value; treating it as a miss would make
    every such read hit the database on every request."""
    sc.store_resolved(
        "audit",
        "retention_days",
        scope=SettingScope.platform(),
        value=None,
        source="default",
        is_secret=False,
    )
    assert sc.cached("audit", "retention_days", scope=SettingScope.platform()) == (
        None,
        "default",
    )


def test_a_json_value_cannot_be_mutated_through_the_cache(db, _store):
    """A caller that mutates a dict it was handed must not corrupt the entry for
    every later reader in the process."""
    sr.upsert_by_key(
        db, SettingDomain.branding, "ui_branding", {"logo": "a"}, tenant_id=None
    )
    first = sr.resolve_value(db, SettingDomain.branding, "ui_branding", tenant_id=None)
    first["logo"] = "tampered"
    second = sr.resolve_value(db, SettingDomain.branding, "ui_branding", tenant_id=None)
    assert second == {"logo": "a"}


def test_a_secret_is_never_cached(db, monkeypatch, _store):
    """Encrypting at rest and then putting the plaintext in a shared cache gives
    most of that back."""
    from cryptography.fernet import Fernet

    monkeypatch.setenv(scrypto.KEY_ENV_VAR, Fernet.generate_key().decode())
    before = set(sr._REGISTRY)
    sr.register_specs(
        [
            sr.SettingSpec(
                domain=SettingDomain.auth,
                key="test_cached_secret",
                value_type=SettingValueType.string,
                default=None,
                is_secret=True,
            )
        ]
    )
    try:
        sr.upsert_by_key(
            db, SettingDomain.auth, "test_cached_secret", "hunter2", tenant_id=None
        )
        assert (
            sr.resolve_value(
                db, SettingDomain.auth, "test_cached_secret", tenant_id=None
            )
            == "hunter2"
        )
        assert (
            _store.get(
                sc.setting_cache_key(
                    "auth", "test_cached_secret", scope=SettingScope.platform()
                )
            )
            is None
        )
    finally:
        for key in set(sr._REGISTRY) - before:
            del sr._REGISTRY[key]


# ── Invalidation ────────────────────────────────────────────────────────────


def test_a_tenant_write_drops_only_that_tenants_entry(db, _store, tenant_row):
    other = uuid4()
    sc.store_resolved(
        "audit",
        "retention_days",
        scope=SettingScope.tenant(tenant_row.id),
        value=1,
        source="tenant",
        is_secret=False,
    )
    sc.store_resolved(
        "audit",
        "retention_days",
        scope=SettingScope.tenant(other),
        value=2,
        source="tenant",
        is_secret=False,
    )
    sc.store_resolved(
        "audit",
        "retention_days",
        scope=SettingScope.platform(),
        value=3,
        source="platform",
        is_secret=False,
    )

    sr.upsert_by_key(
        db,
        SettingDomain.audit,
        "retention_days",
        99,
        scope=SettingScope.tenant(tenant_row.id),
    )

    assert (
        sc.cached("audit", "retention_days", scope=SettingScope.tenant(tenant_row.id))
        is sc.MISS
    )
    assert sc.cached("audit", "retention_days", scope=SettingScope.tenant(other)) == (
        2,
        "tenant",
    )
    assert sc.cached("audit", "retention_days", scope=SettingScope.platform()) == (
        3,
        "platform",
    )


def test_a_platform_write_drops_every_tenants_entry(db, _store):
    """Every tenant without a row of its own inherits the platform value, so a
    platform write silently changes what they resolve."""
    for tenant_id, value in ((TENANT_A, 1), (TENANT_B, 2), (None, 3)):
        sc.store_resolved(
            "audit",
            "retention_days",
            scope=_scope(tenant_id),
            value=value,
            source="platform",
            is_secret=False,
        )

    sr.upsert_by_key(db, SettingDomain.audit, "retention_days", 99, tenant_id=None)

    for tenant_id in (TENANT_A, TENANT_B, None):
        assert sc.cached("audit", "retention_days", scope=_scope(tenant_id)) is sc.MISS


def test_invalidation_does_not_touch_a_different_setting(db, _store):
    sc.store_resolved(
        "custom_fields",
        "max_per_entity",
        scope=SettingScope.platform(),
        value=5,
        source="platform",
        is_secret=False,
    )
    sr.upsert_by_key(db, SettingDomain.audit, "retention_days", 99, tenant_id=None)
    assert sc.cached(
        "custom_fields", "max_per_entity", scope=SettingScope.platform()
    ) == (
        5,
        "platform",
    )


def test_a_write_makes_the_next_read_see_it(db, _store):
    """End to end: the read-through and the invalidation agree."""
    sr.upsert_by_key(db, SettingDomain.audit, "retention_days", 30, tenant_id=None)
    assert (
        sr.resolve_value(db, SettingDomain.audit, "retention_days", tenant_id=None)
        == 30
    )
    sr.upsert_by_key(db, SettingDomain.audit, "retention_days", 90, tenant_id=None)
    assert (
        sr.resolve_value(db, SettingDomain.audit, "retention_days", tenant_id=None)
        == 90
    )


# ── Off by default ──────────────────────────────────────────────────────────


def test_no_store_means_no_caching(db):
    """A cache's absence must not change what anything resolves."""
    sc.install_settings_cache(None)
    sr.upsert_by_key(db, SettingDomain.audit, "retention_days", 30, tenant_id=None)
    assert (
        sc.cached("audit", "retention_days", scope=SettingScope.platform()) is sc.MISS
    )
    assert (
        sr.resolve_value(db, SettingDomain.audit, "retention_days", tenant_id=None)
        == 30
    )
    assert sc.invalidate("audit", "retention_days", scope=SettingScope.platform()) == 0
