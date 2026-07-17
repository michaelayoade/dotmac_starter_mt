"""TDD for the settings spec registry + resolver (Task 4).

`app.core.settings_resolver` holds the registry mechanism and resolution
logic; `app.features.settings.spec` declares the three initial specs and
registers them at import time (imported below purely for that side effect).

SQLite note: the model's partial-unique indexes
(`uq_domain_settings_platform` / `uq_domain_settings_tenant`) use
`postgresql_where=...`, which SQLite ignores — on SQLite they compile down to
plain (non-partial) unique indexes, so `uq_domain_settings_platform` becomes a
global UNIQUE(domain, key) that blocks a platform row and a tenant row for the
same key from coexisting (fine on real Postgres, where the partial WHERE
clause scopes it to `tenant_id IS NULL` rows only). That's why the
tenant-vs-platform precedence test below monkeypatches the resolver's
row-lookup helper instead of inserting both rows for real, and why the
idempotency/race tests assert row-count behavior rather than relying on a
real constraint violation for the non-race paths.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core import settings_resolver as sr
from app.core.settings_models import DomainSetting, SettingDomain, SettingValueType

# Import for the side effect: registers custom_fields/max_per_entity,
# branding/ui_branding, audit/retention_days.
from app.features.settings import spec as _settings_spec  # noqa: F401

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_get_spec_returns_registered_spec():
    spec = sr.get_spec(SettingDomain.audit, "retention_days")
    assert spec.default == 365
    assert spec.min_value == 1
    assert spec.value_type == SettingValueType.integer


def test_get_spec_unregistered_key_raises_keyerror():
    with pytest.raises(KeyError):
        sr.get_spec(SettingDomain.audit, "does-not-exist")


def test_all_specs_includes_feature_declared_specs():
    keys = {(s.domain, s.key) for s in sr.all_specs()}
    assert (SettingDomain.custom_fields, "max_per_entity") in keys
    assert (SettingDomain.branding, "ui_branding") in keys
    assert (SettingDomain.audit, "retention_days") in keys


# ---------------------------------------------------------------------------
# resolve_value: unknown key handling
# ---------------------------------------------------------------------------


def test_resolve_value_unknown_key_without_default_raises_keyerror(db):
    with pytest.raises(KeyError):
        sr.resolve_value(db, SettingDomain.audit, "nope", tenant_id=None)


def test_resolve_value_unknown_key_with_default_kwarg_returns_it(db):
    assert (
        sr.resolve_value(db, SettingDomain.audit, "nope", tenant_id=None, default=20)
        == 20
    )


def test_resolve_value_unknown_key_with_default_none_returns_none(db):
    # default=None must be distinguished from "no default kwarg passed".
    assert (
        sr.resolve_value(db, SettingDomain.audit, "nope", tenant_id=None, default=None)
        is None
    )


# ---------------------------------------------------------------------------
# resolve_value: precedence (tenant > platform > spec default)
# ---------------------------------------------------------------------------


def test_resolve_value_falls_back_to_spec_default_when_no_rows(db, tenant_row):
    value = sr.resolve_value(
        db, SettingDomain.audit, "retention_days", tenant_id=tenant_row.id
    )
    assert value == 365


def test_resolve_value_platform_row_wins_over_spec_default(db, tenant_row):
    db.add(
        DomainSetting(
            tenant_id=None,
            domain=SettingDomain.audit,
            key="retention_days",
            value_type=SettingValueType.integer,
            value_text="200",
        )
    )
    db.flush()
    value = sr.resolve_value(
        db, SettingDomain.audit, "retention_days", tenant_id=tenant_row.id
    )
    assert value == 200


def test_resolve_value_tenant_row_present_no_platform_row(db, tenant_row):
    db.add(
        DomainSetting(
            tenant_id=tenant_row.id,
            domain=SettingDomain.audit,
            key="retention_days",
            value_type=SettingValueType.integer,
            value_text="45",
        )
    )
    db.flush()
    value = sr.resolve_value(
        db, SettingDomain.audit, "retention_days", tenant_id=tenant_row.id
    )
    assert value == 45


def test_resolve_value_deactivated_tenant_row_falls_through_to_default(db, tenant_row):
    """Final-review Group 4(c): `is_active` was a dead column — `_select_row`
    never filtered on it, so a deactivated row still resolved as if active.
    A deactivated tenant row (no platform row either) must fall all the way
    through to the spec default, not resolve to its own stale value."""
    db.add(
        DomainSetting(
            tenant_id=tenant_row.id,
            domain=SettingDomain.audit,
            key="retention_days",
            value_type=SettingValueType.integer,
            value_text="45",
            is_active=False,
        )
    )
    db.flush()
    value, source = sr.resolve_with_source(
        db, SettingDomain.audit, "retention_days", tenant_id=tenant_row.id
    )
    assert value == 365
    assert source == "default"


def test_resolve_value_deactivated_platform_row_falls_through_to_default(
    db, tenant_row
):
    db.add(
        DomainSetting(
            tenant_id=None,
            domain=SettingDomain.audit,
            key="retention_days",
            value_type=SettingValueType.integer,
            value_text="200",
            is_active=False,
        )
    )
    db.flush()
    value, source = sr.resolve_with_source(
        db, SettingDomain.audit, "retention_days", tenant_id=tenant_row.id
    )
    assert value == 365
    assert source == "default"


def test_resolve_value_tenant_row_wins_over_platform_row(db, tenant_row, monkeypatch):
    """SQLite can't hold both a platform and a tenant row for the same key at
    once (see module docstring), so this exercises the precedence *logic*
    (tenant lookup checked first, platform lookup only on a miss) directly by
    stubbing the row-lookup helper rather than inserting both rows for real.
    """
    platform_setting = DomainSetting(
        tenant_id=None,
        domain=SettingDomain.audit,
        key="retention_days",
        value_type=SettingValueType.integer,
        value_text="200",
    )
    tenant_setting = DomainSetting(
        tenant_id=tenant_row.id,
        domain=SettingDomain.audit,
        key="retention_days",
        value_type=SettingValueType.integer,
        value_text="45",
    )

    def fake_select_row(db_, domain_, key_, tenant_id_):
        return tenant_setting if tenant_id_ == tenant_row.id else platform_setting

    monkeypatch.setattr(sr, "_select_row", fake_select_row)
    value = sr.resolve_value(
        db, SettingDomain.audit, "retention_days", tenant_id=tenant_row.id
    )
    assert value == 45


# ---------------------------------------------------------------------------
# resolve_value: coercion, range, allowed-set
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _register_coercion_test_specs():
    """Registers test-only specs in the module-level `sr._REGISTRY`.

    Teardown snapshots the registry's keys before registering and removes
    exactly the keys this fixture added afterward, so these test-only specs
    (`auth/test_bool`, `auth/test_string`, `auth/test_allowed`,
    `auth/test_int_range`) don't leak into other test modules that inspect
    `all_specs()`/`_REGISTRY` (e.g. a full-suite run where import order
    differs from a single-file run). The registry module's public API
    (`register_specs`/`all_specs`/`get_spec`) is unchanged — teardown pokes
    `_REGISTRY` directly since there's no `unregister` helper.
    """
    before_keys = set(sr._REGISTRY.keys())
    sr.register_specs(
        [
            sr.SettingSpec(
                domain=SettingDomain.auth,
                key="test_bool",
                value_type=SettingValueType.boolean,
                default=False,
            ),
            sr.SettingSpec(
                domain=SettingDomain.auth,
                key="test_string",
                value_type=SettingValueType.string,
                default="x",
            ),
            sr.SettingSpec(
                domain=SettingDomain.auth,
                key="test_allowed",
                value_type=SettingValueType.string,
                default="lax",
                allowed={"lax", "strict"},
            ),
            sr.SettingSpec(
                domain=SettingDomain.auth,
                key="test_int_range",
                value_type=SettingValueType.integer,
                default=10,
                min_value=1,
                max_value=100,
            ),
        ]
    )
    yield
    added_keys = set(sr._REGISTRY.keys()) - before_keys
    for key in added_keys:
        del sr._REGISTRY[key]


def test_resolve_value_coerces_boolean_from_string(db):
    db.add(
        DomainSetting(
            tenant_id=None,
            domain=SettingDomain.auth,
            key="test_bool",
            value_type=SettingValueType.boolean,
            value_text="true",
        )
    )
    db.flush()
    assert sr.resolve_value(db, SettingDomain.auth, "test_bool", tenant_id=None) is True


def test_resolve_value_coerces_integer_from_string(db):
    db.add(
        DomainSetting(
            tenant_id=None,
            domain=SettingDomain.audit,
            key="retention_days",
            value_type=SettingValueType.integer,
            value_text="90",
        )
    )
    db.flush()
    value = sr.resolve_value(db, SettingDomain.audit, "retention_days", tenant_id=None)
    assert value == 90


def test_resolve_value_string_passthrough(db):
    db.add(
        DomainSetting(
            tenant_id=None,
            domain=SettingDomain.auth,
            key="test_string",
            value_type=SettingValueType.string,
            value_text="hello",
        )
    )
    db.flush()
    value = sr.resolve_value(db, SettingDomain.auth, "test_string", tenant_id=None)
    assert value == "hello"


def test_resolve_value_json_passthrough(db):
    db.add(
        DomainSetting(
            tenant_id=None,
            domain=SettingDomain.branding,
            key="ui_branding",
            value_type=SettingValueType.json,
            value_json={"logo": "a.png"},
        )
    )
    db.flush()
    value = sr.resolve_value(db, SettingDomain.branding, "ui_branding", tenant_id=None)
    assert value == {"logo": "a.png"}


def test_resolve_value_json_default_is_not_aliased_across_calls(db, tenant_row):
    """Regression: `resolve_value` used to return the spec's `default` object
    directly when no row exists. Since `SettingSpec.default` is stored once on
    the (module-level, effectively process-lifetime) frozen dataclass
    instance in `_REGISTRY`, returning it by reference meant a caller
    mutating its result mutated the spec default for every future
    resolution — and every other caller — for the rest of the process.
    `branding/ui_branding`'s default is `{}` (see spec.py); resolving it
    twice with no DB row must yield two independent dicts.
    """
    first = sr.resolve_value(
        db, SettingDomain.branding, "ui_branding", tenant_id=tenant_row.id
    )
    assert first == {}
    first["logo"] = "corrupted.png"

    second = sr.resolve_value(
        db, SettingDomain.branding, "ui_branding", tenant_id=tenant_row.id
    )
    assert second == {}


def test_resolve_value_out_of_range_int_falls_back_to_default(db):
    db.add(
        DomainSetting(
            tenant_id=None,
            domain=SettingDomain.auth,
            key="test_int_range",
            value_type=SettingValueType.integer,
            value_text="500",
        )
    )
    db.flush()
    value = sr.resolve_value(db, SettingDomain.auth, "test_int_range", tenant_id=None)
    assert value == 10


def test_resolve_value_below_min_int_falls_back_to_default(db):
    db.add(
        DomainSetting(
            tenant_id=None,
            domain=SettingDomain.auth,
            key="test_int_range",
            value_type=SettingValueType.integer,
            value_text="0",
        )
    )
    db.flush()
    value = sr.resolve_value(db, SettingDomain.auth, "test_int_range", tenant_id=None)
    assert value == 10


def test_resolve_value_allowed_violation_falls_back_to_default(db):
    db.add(
        DomainSetting(
            tenant_id=None,
            domain=SettingDomain.auth,
            key="test_allowed",
            value_type=SettingValueType.string,
            value_text="bogus",
        )
    )
    db.flush()
    value = sr.resolve_value(db, SettingDomain.auth, "test_allowed", tenant_id=None)
    assert value == "lax"


def test_resolve_value_unparseable_int_falls_back_to_default(db):
    db.add(
        DomainSetting(
            tenant_id=None,
            domain=SettingDomain.audit,
            key="retention_days",
            value_type=SettingValueType.integer,
            value_text="not-a-number",
        )
    )
    db.flush()
    value = sr.resolve_value(db, SettingDomain.audit, "retention_days", tenant_id=None)
    assert value == 365


# ---------------------------------------------------------------------------
# ensure_by_key / upsert_by_key
# ---------------------------------------------------------------------------


def _row_count(db, domain: SettingDomain, key: str) -> int:
    return db.scalar(
        select(func.count())
        .select_from(DomainSetting)
        .where(DomainSetting.domain == domain, DomainSetting.key == key)
    )


def test_ensure_by_key_is_idempotent(db):
    row1 = sr.ensure_by_key(
        db, SettingDomain.custom_fields, "max_per_entity", 20, tenant_id=None
    )
    row2 = sr.ensure_by_key(
        db, SettingDomain.custom_fields, "max_per_entity", 999, tenant_id=None
    )
    assert row1.id == row2.id
    assert row2.value_text == "20"  # second call is a no-op, doesn't overwrite
    assert _row_count(db, SettingDomain.custom_fields, "max_per_entity") == 1


def test_ensure_by_key_does_not_overwrite_operator_value(db):
    sr.ensure_by_key(
        db, SettingDomain.custom_fields, "max_per_entity", 20, tenant_id=None
    )
    # Simulate an operator override.
    row = sr._select_row(db, SettingDomain.custom_fields, "max_per_entity", None)
    row.value_text = "77"
    db.flush()

    sr.ensure_by_key(
        db, SettingDomain.custom_fields, "max_per_entity", 20, tenant_id=None
    )
    row = sr._select_row(db, SettingDomain.custom_fields, "max_per_entity", None)
    assert row.value_text == "77"


def test_ensure_by_key_race_safe_reselects_winner(db, monkeypatch):
    """Simulates a concurrent writer beating us to the insert: the winner row
    is committed for real (so it survives the later rollback), then the
    pre-check is stubbed to miss it once (TOCTOU), forcing our own insert to
    collide with the real unique index and raise IntegrityError.
    """
    winner = DomainSetting(
        tenant_id=None,
        domain=SettingDomain.audit,
        key="retention_days",
        value_type=SettingValueType.integer,
        value_text="400",
    )
    db.add(winner)
    db.commit()

    real_select_row = sr._select_row
    calls = {"n": 0}

    def fake_select_row(db_, domain_, key_, tenant_id_):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return real_select_row(db_, domain_, key_, tenant_id_)

    monkeypatch.setattr(sr, "_select_row", fake_select_row)

    result = sr.ensure_by_key(
        db, SettingDomain.audit, "retention_days", 999, tenant_id=None
    )
    assert result.id == winner.id
    assert result.value_text == "400"
    assert calls["n"] == 2
    assert _row_count(db, SettingDomain.audit, "retention_days") == 1


def test_upsert_by_key_creates_row(db):
    row = sr.upsert_by_key(
        db, SettingDomain.branding, "ui_branding", {"a": 1}, tenant_id=None
    )
    assert row.value_json == {"a": 1}
    assert _row_count(db, SettingDomain.branding, "ui_branding") == 1


def test_upsert_by_key_overwrites_existing_row(db):
    row1 = sr.upsert_by_key(
        db, SettingDomain.branding, "ui_branding", {"a": 1}, tenant_id=None
    )
    row2 = sr.upsert_by_key(
        db, SettingDomain.branding, "ui_branding", {"a": 2}, tenant_id=None
    )
    assert row1.id == row2.id
    assert row2.value_json == {"a": 2}
    assert _row_count(db, SettingDomain.branding, "ui_branding") == 1
