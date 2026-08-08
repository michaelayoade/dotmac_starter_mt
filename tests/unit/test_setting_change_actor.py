"""Settings history records WHO changed a value, not only what it became.

An earlier design left the actor out, reasoning that the audit trail owned it.
Two things were wrong: answering "who turned this off" then meant joining tables
on timestamp proximity, and it cost adopting products a capability they already
had. These tests pin the corrected behaviour, including what must NOT be
recorded.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest
from dotmac_kernel import settings_crypto as sc
from dotmac_kernel import settings_resolver as sr
from dotmac_kernel.setting_value_types import SettingValueType
from dotmac_kernel.settings_models import DomainSettingHistory, SettingDomain


def _history(db, key: str):
    return (
        db.query(DomainSettingHistory)
        .filter(DomainSettingHistory.key == key)
        .order_by(DomainSettingHistory.changed_at)
        .all()
    )


def test_the_actor_and_request_context_are_recorded(db, tenant_row, party_row):
    actor = sr.SettingChangeContext(
        actor_party_id=party_row.id,
        reason="tightening retention after the audit",
        ip_address="203.0.113.7",
        user_agent="Mozilla/5.0",
        request_id="req-abc123",
    )
    sr.upsert_by_key(
        db,
        SettingDomain.audit,
        "retention_days",
        30,
        tenant_id=tenant_row.id,
        changed_by=actor,
    )
    (row,) = _history(db, "retention_days")
    assert row.changed_by_party_id == party_row.id
    assert row.change_reason == "tightening retention after the audit"
    assert row.ip_address == "203.0.113.7"
    assert row.user_agent == "Mozilla/5.0"
    assert row.request_id == "req-abc123"


def test_a_change_with_no_actor_is_recorded_honestly(db, tenant_row):
    """A seed, a migration or a CLI genuinely has no actor. Recording NULL beats
    inventing one, and beats refusing the write."""
    sr.upsert_by_key(
        db, SettingDomain.audit, "retention_days", 30, tenant_id=tenant_row.id
    )
    (row,) = _history(db, "retention_days")
    assert row.changed_by_party_id is None
    assert row.request_id is None


def test_the_actor_is_recorded_but_a_secret_value_still_is_not(
    db, tenant_row, party_row, monkeypatch
):
    """Adding WHO must not weaken the rule about WHAT: a history table must not
    become the place a rotated credential outlives its rotation."""
    from cryptography.fernet import Fernet

    monkeypatch.setenv(sc.KEY_ENV_VAR, Fernet.generate_key().decode())
    before = set(sr._REGISTRY)
    sr.register_specs(
        [
            sr.SettingSpec(
                domain=SettingDomain.auth,
                key="test_actor_secret",
                value_type=SettingValueType("string"),
                default=None,
                is_secret=True,
            )
        ]
    )
    try:
        sr.upsert_by_key(
            db,
            SettingDomain.auth,
            "test_actor_secret",
            "hunter2",
            tenant_id=tenant_row.id,
            changed_by=sr.SettingChangeContext(actor_party_id=party_row.id),
        )
        (row,) = _history(db, "test_actor_secret")
        assert row.changed_by_party_id == party_row.id
        assert row.secret_changed is True
        assert row.value_before is None and row.value_after is None
        assert "hunter2" not in str(vars(row))
    finally:
        for key in set(sr._REGISTRY) - before:
            del sr._REGISTRY[key]


def test_the_context_record_is_immutable():
    """Frozen, and every field a scalar — no mutable container to be shared
    between two changes and then edited."""
    actor = sr.SettingChangeContext(actor_party_id=uuid4())
    with pytest.raises(FrozenInstanceError):
        actor.reason = "changed after the fact"  # type: ignore[misc]


def test_an_update_records_its_own_actor(db, tenant_row, party_row):
    """Each change carries who made THAT change, not who made the last one."""
    first, second = party_row.id, uuid4()
    sr.upsert_by_key(
        db,
        SettingDomain.audit,
        "retention_days",
        30,
        tenant_id=tenant_row.id,
        changed_by=sr.SettingChangeContext(actor_party_id=first),
    )
    sr.upsert_by_key(
        db,
        SettingDomain.audit,
        "retention_days",
        60,
        tenant_id=tenant_row.id,
        changed_by=sr.SettingChangeContext(actor_party_id=second),
    )
    rows = _history(db, "retention_days")
    assert [r.changed_by_party_id for r in rows] == [first, second]


# ---------------------------------------------------------------------------
# The five silent-failure gaps
#
# Ranked by how they fail: three of these produced no error at all, which is
# worse than a crash — the operator sees success and the system quietly does
# something else.
# ---------------------------------------------------------------------------


def test_the_writer_refuses_a_value_the_spec_forbids(db, tenant_row):
    """Previously written, then silently degraded to the default on read — so
    the operator saw success and the setting never took effect."""
    from dotmac_kernel.exceptions import BadRequestError

    spec = sr.get_spec(SettingDomain.audit, "retention_days")
    assert spec.min_value == 1

    with pytest.raises(BadRequestError):
        sr.upsert_by_key(
            db, SettingDomain.audit, "retention_days", 0, tenant_id=tenant_row.id
        )
    assert (
        sr.resolve_value(
            db, SettingDomain.audit, "retention_days", tenant_id=tenant_row.id
        )
        == spec.default
    )


def test_a_valid_value_still_writes(db, tenant_row):
    """Sensitivity companion: the refusal is about the CONSTRAINT, not about
    writing having broken."""
    sr.upsert_by_key(
        db, SettingDomain.audit, "retention_days", 30, tenant_id=tenant_row.id
    )
    assert (
        sr.resolve_value(
            db, SettingDomain.audit, "retention_days", tenant_id=tenant_row.id
        )
        == 30
    )


def test_an_override_can_be_removed(db, tenant_row):
    """Setting a value had no inverse — a tenant could override a platform
    default but never go back to inheriting it."""
    sr.upsert_by_key(db, SettingDomain.audit, "retention_days", 90, tenant_id=None)
    sr.upsert_by_key(
        db, SettingDomain.audit, "retention_days", 30, tenant_id=tenant_row.id
    )
    assert (
        sr.resolve_value(
            db, SettingDomain.audit, "retention_days", tenant_id=tenant_row.id
        )
        == 30
    )

    assert (
        sr.clear_by_key(
            db, SettingDomain.audit, "retention_days", tenant_id=tenant_row.id
        )
        is True
    )

    # Falls back to the platform value, which is untouched.
    value, source = sr.resolve_with_source(
        db, SettingDomain.audit, "retention_days", tenant_id=tenant_row.id
    )
    assert (value, source) == (90, "platform")


def test_clearing_something_unset_is_a_no_op(db, tenant_row):
    assert (
        sr.clear_by_key(
            db, SettingDomain.audit, "retention_days", tenant_id=tenant_row.id
        )
        is False
    )


def test_removal_is_recorded_in_history(db, tenant_row, party_row):
    sr.upsert_by_key(
        db, SettingDomain.audit, "retention_days", 30, tenant_id=tenant_row.id
    )
    sr.clear_by_key(
        db,
        SettingDomain.audit,
        "retention_days",
        tenant_id=tenant_row.id,
        changed_by=sr.SettingChangeContext(actor_party_id=party_row.id, reason="undo"),
    )
    rows = _history(db, "retention_days")
    assert [r.action.value for r in rows] == ["create", "delete"]
    assert rows[-1].changed_by_party_id == party_row.id
    assert rows[-1].change_reason == "undo"


def test_two_conflicting_declarations_of_one_setting_fail(db):
    """Last-writer-wins meant the effective spec depended on import order.
    Every other registry in the kernel fails here; this one overwrote quietly."""
    before = set(sr._REGISTRY)
    first = sr.SettingSpec(
        domain=SettingDomain.audit,
        key="test_conflict",
        value_type=SettingValueType("integer"),
        default=1,
    )
    second = sr.SettingSpec(
        domain=SettingDomain.audit,
        key="test_conflict",
        value_type=SettingValueType("integer"),
        default=99,
    )
    try:
        sr.register_specs([first])
        with pytest.raises(sr.DuplicateSettingSpecError):
            sr.register_specs([second])
    finally:
        for key in set(sr._REGISTRY) - before:
            del sr._REGISTRY[key]


def test_re_registering_the_same_declaration_is_still_allowed(db):
    """A module imported twice through different paths, or reloaded in a test,
    builds an equal-but-not-identical spec. That must stay harmless."""
    before = set(sr._REGISTRY)

    def _same(spec_default):
        return sr.SettingSpec(
            domain=SettingDomain.audit,
            key="test_idempotent",
            value_type=SettingValueType("integer"),
            default=spec_default,
        )

    try:
        sr.register_specs([_same(5)])
        sr.register_specs([_same(5)])  # must not raise
    finally:
        for key in set(sr._REGISTRY) - before:
            del sr._REGISTRY[key]


def test_a_spec_whose_default_violates_itself_is_refused(db):
    """Resolution degrades to the default, so a forbidden default is served to
    every reader — silently, forever."""
    before = set(sr._REGISTRY)
    try:
        with pytest.raises(sr.InvalidSpecDefaultError):
            sr.register_specs(
                [
                    sr.SettingSpec(
                        domain=SettingDomain.audit,
                        key="test_bad_default",
                        value_type=SettingValueType("string"),
                        default="nope",
                        allowed={"yes", "no"},
                    )
                ]
            )
    finally:
        for key in set(sr._REGISTRY) - before:
            del sr._REGISTRY[key]


def test_a_required_setting_may_declare_no_default(db):
    """`default=None` is legitimate — a setting that must be configured has no
    sensible built-in value — so the check must skip it rather than reject it."""
    before = set(sr._REGISTRY)
    try:
        sr.register_specs(
            [
                sr.SettingSpec(
                    domain=SettingDomain.audit,
                    key="test_required_nodefault",
                    value_type=SettingValueType("string"),
                    default=None,
                    required_at="tenant",
                    allowed={"yes", "no"},
                )
            ]
        )
    finally:
        for key in set(sr._REGISTRY) - before:
            del sr._REGISTRY[key]


def test_bulk_reads_are_served_from_and_warm_the_cache(db, tenant_row):
    """The bulk path neither read nor warmed the cache, so the screen that most
    needed it got no benefit AND left single-key reads still missing."""
    from dotmac_kernel import settings_cache as sc_mod
    from dotmac_kernel.cache import MemoryCache
    from dotmac_kernel.setting_scopes import SettingScope

    store = MemoryCache()
    sc_mod.install_settings_cache(store)
    try:
        scope = SettingScope.tenant(tenant_row.id)
        keys = [
            spec.key for spec in sr.all_specs() if spec.domain == SettingDomain.display
        ]
        sr.resolve_many(db, SettingDomain.display, keys, tenant_id=tenant_row.id)

        # Warmed: every key now has an entry.
        for key in keys:
            assert sc_mod.cached("display", key, scope=scope) is not sc_mod.MISS

        # Served: a poisoned entry proves the second call reads the cache.
        sc_mod.store_resolved(
            "display",
            keys[0],
            scope=scope,
            value="FROM-CACHE",
            source="tenant",
            is_secret=False,
        )
        again = sr.resolve_many(
            db, SettingDomain.display, keys, tenant_id=tenant_row.id
        )
        assert again[keys[0]] == "FROM-CACHE"
    finally:
        sc_mod.install_settings_cache(None)
