"""Fresh, fail-closed reads share compatibility resolution's authority."""

from dataclasses import replace
from uuid import uuid4

import pytest
from dotmac_kernel import settings_cache as sc
from dotmac_kernel import settings_resolver as sr
from dotmac_kernel.cache import MemoryCache
from dotmac_kernel.setting_scopes import (
    KERNEL_SCOPE_KINDS,
    ScopeKindRegistry,
    ScopeKindSpec,
    SettingScope,
    active_scope_kinds,
    install_scope_kinds,
)
from dotmac_kernel.setting_value_types import SettingValueType
from dotmac_kernel.settings_models import DomainSetting, SettingDomain
from sqlalchemy import JSON, update
from sqlalchemy.orm.attributes import flag_modified


@pytest.fixture
def spec():
    declared = sr.SettingSpec(
        domain=SettingDomain.auth,
        key=f"strict_{uuid4().hex}",
        value_type=SettingValueType.string,
        default="closed",
        allowed={"open", "closed"},
    )
    sr.register_specs([declared])
    previous = dict(sr.active_setting_defaults())
    yield declared
    sr._REGISTRY.pop((declared.domain, declared.key), None)
    sr.install_setting_defaults(previous)
    sc.install_settings_cache(None)


def plant(db, spec, value, scope):
    row = DomainSetting(
        domain=spec.domain,
        key=spec.key,
        value_type=spec.value_type,
        value_text=value,
        # JSON null occupies a storage column (satisfying the SQL alignment
        # constraint) but decodes to no usable value on the read path.
        value_json=JSON.NULL if value is None else None,
        tenant_id=scope.tenant_id,
        scope_kind=scope.kind,
        scope_id=scope.scope_id,
    )
    db.add(row)
    db.flush()
    return row


def test_tenant_rows_are_isolated_and_precede_platform(db, spec):
    tenant = SettingScope.tenant(uuid4())
    other = SettingScope.tenant(uuid4())
    plant(db, spec, "open", SettingScope.platform())
    plant(db, spec, "closed", tenant)
    assert sr.resolve_strict(db, spec, scope=tenant) == "closed"
    assert sr.resolve_strict(db, spec, scope=other) == "open"
    assert sr.resolve_strict(db, spec, scope=SettingScope.platform()) == "open"


def test_absence_uses_profile_then_spec_default(db, spec):
    scope = SettingScope.platform()
    assert sr.resolve_strict(db, spec, scope=scope) == "closed"
    sr.install_setting_defaults({f"{spec.domain}/{spec.key}": "open"})
    assert sr.resolve_strict(db, spec, scope=scope) == "open"


@pytest.mark.parametrize("value", ["malicious-secret-value", None])
def test_invalid_winner_never_falls_through_or_leaks(db, spec, value):
    tenant = SettingScope.tenant(uuid4())
    plant(db, spec, "open", SettingScope.platform())
    plant(db, spec, value, tenant)
    sr.install_setting_defaults({f"{spec.domain}/{spec.key}": "open"})
    with pytest.raises(sr.StrictSettingResolutionError) as caught:
        sr.resolve_strict(db, spec, scope=tenant)
    error = caught.value
    assert set(vars(error)) == {"domain", "key", "scope_kind", "reason"}
    assert (error.domain, error.key, error.scope_kind) == (
        str(spec.domain),
        spec.key,
        tenant.kind,
    )
    assert error.reason in {
        sr.StrictSettingResolutionReason.INVALID_VALUE,
        sr.StrictSettingResolutionReason.NO_VALUE,
    }
    assert "malicious-secret-value" not in str(error)
    assert error.__cause__ is None and error.__context__ is None
    assert sr.resolve(db, spec, scope=tenant) == ("open" if value is None else "closed")


@pytest.mark.parametrize("value", ["malicious-secret-value", None])
def test_invalid_profile_refuses_instead_of_using_default(db, spec, value):
    sr.install_setting_defaults({f"{spec.domain}/{spec.key}": value})
    with pytest.raises(sr.StrictSettingResolutionError):
        sr.resolve_strict(db, spec, scope=SettingScope.platform())


def test_cache_is_neither_read_nor_written(db, spec):
    scope = SettingScope.platform()
    store = MemoryCache()
    sc.install_settings_cache(store)
    plant(db, spec, "open", scope)
    assert sr.resolve(db, spec, scope=scope) == "open"
    db.execute(
        update(DomainSetting)
        .where(DomainSetting.key == spec.key)
        .values(value_text="closed")
        .execution_options(synchronize_session=False)
    )
    assert sr.resolve_strict(db, spec, scope=scope) == "closed"
    assert sr.resolve(db, spec, scope=scope) == "open"
    sc.install_settings_cache(None)


def test_scalar_snapshot_ignores_identity_map_and_preserves_pending_state(db, spec):
    scope = SettingScope.platform()
    row = plant(db, spec, "open", scope)
    db.execute(
        update(DomainSetting)
        .where(DomainSetting.id == row.id)
        .values(value_text="closed")
        .execution_options(synchronize_session=False)
    )
    assert row.value_text == "open"
    row.value_text = "unflushed-secret"
    db.autoflush = True
    assert sr.resolve_strict(db, spec, scope=scope) == "closed"
    assert row in db.dirty and row.value_text == "unflushed-secret"
    row.value_text = "open"
    # The requested write equals the stale identity's original value; mark it
    # explicitly so SQLAlchemy writes rather than cancelling the edit history.
    flag_modified(row, "value_text")
    db.flush()
    assert sr.resolve_strict(db, spec, scope=scope) == "open"


def test_pending_insert_is_not_flushed_or_discarded(db, spec, monkeypatch):
    scope = SettingScope.platform()
    row = DomainSetting(
        domain=spec.domain,
        key=spec.key,
        value_type=spec.value_type,
        value_text="open",
        scope_kind=scope.kind,
    )
    db.add(row)
    db.autoflush = True

    def forbidden(*args, **kwargs):
        pytest.fail("strict read changed the caller's transaction")

    with monkeypatch.context() as patch:
        for name in ("flush", "commit", "rollback", "close"):
            patch.setattr(db, name, forbidden)
        assert sr.resolve_strict(db, spec, scope=scope) == "closed"
    assert row in db.new
    db.flush()
    assert sr.resolve_strict(db, spec, scope=scope) == "open"


def test_extra_scope_and_non_inheriting_precedence(db, spec):
    previous = active_scope_kinds()
    install_scope_kinds(
        ScopeKindRegistry.from_specs(
            [*KERNEL_SCOPE_KINDS, ScopeKindSpec(kind="site", rank=150)]
        )
    )
    tenant = SettingScope.tenant(uuid4())
    site = SettingScope(kind="site", tenant_id=tenant.tenant_id, scope_id=uuid4())
    try:
        plant(db, spec, "open", SettingScope.platform())
        tenant_row = plant(db, spec, "closed", tenant)
        assert sr.resolve_strict(db, spec, scope=site) == "closed"
        site_row = plant(db, spec, "open", site)
        assert sr.resolve_strict(db, spec, scope=site) == "open"
        site_row.is_active = False
        db.flush()
        assert sr.resolve_strict(db, spec, scope=site) == "closed"
        # Replace registry authority rather than passing a mismatching spec.
        tenant_row.value_text = "open"
        db.flush()
        isolated = replace(spec, inherits=False)
        sr._REGISTRY[(spec.domain, spec.key)] = isolated
        assert sr.resolve_strict(db, isolated, scope=site) == "closed"
        assert sr.resolve_strict(db, isolated, scope=SettingScope.platform()) == "open"
        sr.install_setting_defaults({f"{spec.domain}/{spec.key}": "open"})
        assert sr.resolve_strict(db, isolated, scope=site) == "open"
        site_row.is_active = True
        site_row.value_text = "invalid-winning-site"
        db.flush()
        with pytest.raises(sr.StrictSettingResolutionError):
            sr.resolve_strict(db, isolated, scope=site)
    finally:
        install_scope_kinds(previous)


@pytest.mark.parametrize("raw", ["3", "0", "6", "garbled", "4"])
def test_coercion_range_and_validator_share_compatibility_rules(db, spec, raw):
    def validate(value):
        if value == 4:
            raise ValueError("malicious-secret-validator-text")

    numeric = replace(
        spec,
        value_type=SettingValueType.integer,
        default=2,
        allowed=None,
        min_value=1,
        max_value=5,
        validator=validate,
    )
    sr._REGISTRY[(spec.domain, spec.key)] = numeric
    scope = SettingScope.platform()
    plant(db, numeric, raw, scope)
    expected, error = sr._check_against_spec(numeric, raw)
    if error is None:
        assert sr.resolve_strict(db, numeric, scope=scope) == expected == 3
    else:
        with pytest.raises(sr.StrictSettingResolutionError) as caught:
            sr.resolve_strict(db, numeric, scope=scope)
        assert caught.value.reason == sr.StrictSettingResolutionReason.INVALID_VALUE
        assert "malicious-secret" not in str(caught.value)
        assert sr.resolve(db, numeric, scope=scope) == 2


def test_invalid_spec_default_and_null_are_refused_even_after_registration(db, spec):
    # Registration validates defaults; mutable/replaced registry state must not
    # make the strict runtime check depend on registration-time success.
    for default in (None, "malicious-secret-default"):
        changed = replace(spec, default=default)
        sr._REGISTRY[(spec.domain, spec.key)] = changed
        with pytest.raises(sr.StrictSettingResolutionError):
            sr.resolve_strict(db, changed, scope=SettingScope.platform())


def test_undeclared_scope_refuses_even_without_inheritance(db, spec):
    isolated = replace(spec, inherits=False)
    sr._REGISTRY[(spec.domain, spec.key)] = isolated
    with pytest.raises(sr.StrictSettingResolutionError) as caught:
        sr.resolve_strict(
            db, isolated, scope=SettingScope(kind="missing", tenant_id=uuid4())
        )
    assert caught.value.reason == sr.StrictSettingResolutionReason.INVALID_SCOPE


def test_type_registry_and_decrypt_failures_refuse_safely(db, spec, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("malicious-secret-exception")

    scope = SettingScope.platform()
    row = plant(db, spec, "open", scope)
    row.is_secret = True
    db.flush()
    with monkeypatch.context() as patch:
        patch.setattr(sr, "decrypt_value", fail)
        with pytest.raises(sr.StrictSettingResolutionError) as caught:
            sr.resolve_strict(db, spec, scope=scope)
        assert caught.value.reason == sr.StrictSettingResolutionReason.EXTRACTION_FAILED
        assert "malicious-secret" not in repr(caught.value)
        assert caught.value.__context__ is None
    with monkeypatch.context() as patch:
        patch.setattr(sr, "active_setting_value_types", fail)
        with pytest.raises(sr.StrictSettingResolutionError) as caught:
            sr.resolve_strict(db, spec, scope=scope)
        assert caught.value.reason == sr.StrictSettingResolutionReason.TYPE_UNAVAILABLE
        assert "malicious-secret" not in repr(caught.value)


def test_scope_cannot_be_none_or_replaced_with_tenant_keyword(db, spec):
    with pytest.raises(sr.StrictSettingResolutionError) as caught:
        sr.resolve_strict(db, spec, scope=None)
    assert caught.value.reason == sr.StrictSettingResolutionReason.INVALID_SCOPE
    with pytest.raises(TypeError):
        sr.resolve_strict(db, spec, tenant_id=uuid4())


def test_public_submodule_contract():
    for name in (
        "resolve_strict",
        "StrictSettingResolutionError",
        "StrictSettingResolutionReason",
    ):
        assert name in sr.__all__


@pytest.mark.parametrize(
    ("value_type", "default"),
    [
        (SettingValueType.boolean, False),
        (SettingValueType.integer, 0),
        (SettingValueType.string, ""),
    ],
)
def test_false_zero_and_empty_string_are_usable_defaults(db, spec, value_type, default):
    changed = replace(spec, value_type=value_type, default=default, allowed=None)
    sr._REGISTRY[(spec.domain, spec.key)] = changed
    scope = SettingScope.platform()
    assert sr.resolve_strict(db, changed, scope=scope) == default
    sr.install_setting_defaults({f"{spec.domain}/{spec.key}": default})
    assert sr.resolve_strict(db, changed, scope=scope) == default


def test_mutable_default_is_copied(db, spec):
    changed = replace(spec, value_type=SettingValueType.json, default={}, allowed=None)
    sr._REGISTRY[(spec.domain, spec.key)] = changed
    scope = SettingScope.platform()
    first = sr.resolve_strict(db, changed, scope=scope)
    first["changed"] = True
    assert sr.resolve_strict(db, changed, scope=scope) == {}


def test_strict_read_does_not_populate_empty_cache(db, spec):
    scope = SettingScope.platform()
    sc.install_settings_cache(MemoryCache())
    assert sc.cached(spec.domain, spec.key, scope=scope) is sc.MISS
    assert sr.resolve_strict(db, spec, scope=scope) == "closed"
    assert sc.cached(spec.domain, spec.key, scope=scope) is sc.MISS


def test_validator_exception_is_sanitized(db, spec):
    def validate(value):
        raise RuntimeError(f"malicious-secret-validator: {value}")

    changed = replace(spec, validator=validate)
    sr._REGISTRY[(spec.domain, spec.key)] = changed
    with pytest.raises(sr.StrictSettingResolutionError) as caught:
        sr.resolve_strict(db, changed, scope=SettingScope.platform())
    assert caught.value.reason == sr.StrictSettingResolutionReason.INVALID_VALUE
    assert "malicious-secret" not in str(caught.value)
    assert caught.value.__context__ is None


def test_malformed_storage_codec_exception_is_sanitized(db, spec, monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError("malicious-secret-malformed-storage")

    plant(db, spec, "open", SettingScope.platform())
    monkeypatch.setattr(sr, "_coerce", fail)
    with pytest.raises(sr.StrictSettingResolutionError) as caught:
        sr.resolve_strict(db, spec, scope=SettingScope.platform())
    assert caught.value.reason == sr.StrictSettingResolutionReason.INVALID_VALUE
    assert "malicious-secret" not in repr(caught.value)
    assert caught.value.__context__ is None


def test_environment_is_never_a_read_fallback(db, spec, monkeypatch):
    changed = replace(spec, env_var="STRICT_READ_TEST_VALUE")
    sr._REGISTRY[(spec.domain, spec.key)] = changed
    monkeypatch.setenv("STRICT_READ_TEST_VALUE", "open")
    assert sr.resolve_strict(db, changed, scope=SettingScope.platform()) == "closed"


def test_unregistered_and_mismatched_declarations_refuse(db, spec):
    with pytest.raises(sr.StrictSettingResolutionError) as caught:
        sr.resolve_strict(
            db, replace(spec, allowed=None), scope=SettingScope.platform()
        )
    assert caught.value.reason == sr.StrictSettingResolutionReason.SPEC_MISMATCH
    sr._REGISTRY.pop((spec.domain, spec.key))
    with pytest.raises(sr.StrictSettingResolutionError) as caught:
        sr.resolve_strict(db, spec, scope=SettingScope.platform())
    assert caught.value.reason == sr.StrictSettingResolutionReason.UNDECLARED_SPEC


def test_database_failure_has_bounded_diagnostics(db, spec, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("malicious-secret-value SQL parameters")

    monkeypatch.setattr(db, "execute", fail)
    with pytest.raises(sr.StrictSettingResolutionError) as caught:
        sr.resolve_strict(db, spec, scope=SettingScope.platform())
    assert caught.value.reason == sr.StrictSettingResolutionReason.READ_FAILED
    assert "malicious-secret" not in repr(caught.value)
    assert caught.value.__context__ is None


def test_scope_is_required(db, spec):
    with pytest.raises(TypeError):
        sr.resolve_strict(db, spec)
