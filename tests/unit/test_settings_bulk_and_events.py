"""Bulk reads cost one query per level, and a change announces itself.

Two independent problems with one thing in common: both were things the kernel
already had the machinery for and simply was not using — the chain for reads,
the outbox for propagation.
"""

from __future__ import annotations

import pytest
from dotmac_kernel import settings_resolver as sr
from dotmac_kernel.config import settings as kernel_settings
from dotmac_kernel.messaging.models import OutboxEvent
from dotmac_kernel.settings_models import SettingDomain
from sqlalchemy import event, select


@pytest.fixture()
def counted(db):
    """Count SELECTs against domain_settings, so the N+1 claim is measured
    rather than asserted."""
    counter = {"n": 0}

    def before(conn, cursor, statement, params, context, executemany):
        if "domain_settings" in statement and statement.lstrip().upper().startswith(
            "SELECT"
        ):
            counter["n"] += 1

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", before)
    yield counter
    event.remove(engine, "before_cursor_execute", before)


# ── resolve_many ────────────────────────────────────────────────────────────


def test_bulk_costs_one_query_per_level_not_per_key(db, tenant_row, counted) -> None:
    """The whole point: cost tracks the DEPTH of the hierarchy, not the number
    of settings on the page."""
    keys = [spec.key for spec in sr.all_specs() if spec.domain == SettingDomain.display]
    assert len(keys) >= 3, "need several keys for this to mean anything"

    counted["n"] = 0
    resolved = sr.resolve_many(db, SettingDomain.display, keys, tenant_id=tenant_row.id)

    assert set(resolved) == set(keys)
    # Two levels in the chain (tenant, platform) => at most two queries, however
    # many keys were asked for.
    assert counted["n"] <= 2, f"{counted['n']} queries for {len(keys)} keys"


def test_bulk_agrees_with_single_key_resolution(db, tenant_row) -> None:
    """The rules are shared (`_finish`), and this is what stops them drifting:
    a page reading in bulk must not answer differently from the same settings
    read one at a time."""
    keys = [spec.key for spec in sr.all_specs() if spec.domain == SettingDomain.display]
    bulk = sr.resolve_many(db, SettingDomain.display, keys, tenant_id=tenant_row.id)
    for key in keys:
        assert bulk[key] == sr.resolve_value(
            db, SettingDomain.display, key, tenant_id=tenant_row.id
        )


def test_bulk_honours_precedence(db, tenant_row) -> None:
    sr.upsert_by_key(db, SettingDomain.audit, "retention_days", 90, tenant_id=None)
    sr.upsert_by_key(
        db, SettingDomain.audit, "retention_days", 30, tenant_id=tenant_row.id
    )
    assert sr.resolve_many(
        db, SettingDomain.audit, ["retention_days"], tenant_id=tenant_row.id
    ) == {"retention_days": 30}
    assert sr.resolve_many(
        db, SettingDomain.audit, ["retention_days"], tenant_id=None
    ) == {"retention_days": 90}


def test_bulk_with_no_keys_returns_the_whole_domain(db, tenant_row) -> None:
    """What a settings screen actually wants."""
    resolved = sr.resolve_many(db, SettingDomain.display, tenant_id=tenant_row.id)
    expected = {
        spec.key for spec in sr.all_specs() if spec.domain == SettingDomain.display
    }
    assert set(resolved) == expected


def test_bulk_ignores_an_unregistered_key(db, tenant_row) -> None:
    resolved = sr.resolve_many(
        db, SettingDomain.audit, ["retention_days", "no-such-key"], tenant_id=None
    )
    assert "no-such-key" not in resolved


# ── change events ───────────────────────────────────────────────────────────


@pytest.fixture()
def events_on(monkeypatch):
    monkeypatch.setattr(kernel_settings, "settings_change_events", True)


def _events(db):
    return db.scalars(
        select(OutboxEvent).where(OutboxEvent.event_type == sr.SETTING_CHANGED_EVENT)
    ).all()


def test_a_tenant_write_announces_itself(db, tenant_row, events_on) -> None:
    sr.upsert_by_key(
        db, SettingDomain.audit, "retention_days", 30, tenant_id=tenant_row.id
    )
    (emitted,) = _events(db)
    assert emitted.tenant_id == tenant_row.id
    assert emitted.payload["domain"] == "audit"
    assert emitted.payload["key"] == "retention_days"
    assert emitted.payload["action"] == "create"
    assert emitted.payload["scope_kind"] == "tenant"


def test_the_event_never_carries_the_value(db, tenant_row, events_on) -> None:
    """A subscriber that needs the value resolves it. Keeping it out means one
    reader of the value, and no secret travelling through a delivery pipeline
    with its own retention and logging."""
    sr.upsert_by_key(
        db, SettingDomain.audit, "retention_days", 30, tenant_id=tenant_row.id
    )
    (emitted,) = _events(db)
    assert "value" not in emitted.payload
    assert "30" not in str(emitted.payload)


def test_no_events_unless_enabled(db, tenant_row) -> None:
    """An event with no relay running is a row that accumulates forever."""
    sr.upsert_by_key(
        db, SettingDomain.audit, "retention_days", 30, tenant_id=tenant_row.id
    )
    assert _events(db) == []


def test_a_failed_enqueue_does_not_roll_back_the_write(
    db, tenant_row, events_on, monkeypatch
) -> None:
    """A notification that cannot be sent must not lose the change it was
    describing."""

    def boom(*_args, **_kwargs):
        raise RuntimeError("outbox unavailable")

    # Patch the SOURCE module: the resolver imports it lazily inside the
    # emit, so patching the resolver's own namespace would miss.
    monkeypatch.setattr("dotmac_kernel.messaging.outbox.enqueue_event", boom)
    sr.upsert_by_key(
        db, SettingDomain.audit, "retention_days", 30, tenant_id=tenant_row.id
    )
    assert (
        sr.resolve_value(
            db, SettingDomain.audit, "retention_days", tenant_id=tenant_row.id
        )
        == 30
    )


def test_an_update_is_distinguishable_from_a_create(db, tenant_row, events_on) -> None:
    sr.upsert_by_key(
        db, SettingDomain.audit, "retention_days", 30, tenant_id=tenant_row.id
    )
    sr.upsert_by_key(
        db, SettingDomain.audit, "retention_days", 60, tenant_id=tenant_row.id
    )
    assert [e.payload["action"] for e in _events(db)] == ["create", "update"]
