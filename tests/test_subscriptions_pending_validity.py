# ruff: noqa: F811, S608
# (the scratch-database fixture is imported by name so pytest registers it)
"""PostgreSQL proofs for su_0006: ``pending_validity`` and owner evidence."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

from tests.test_subscriptions_catalog_price_coverage_overlap import (  # noqa: F401
    _OCC_SQL,
    NOV1,
    OCT1,
    OVERLAP,
    SEP1,
    SEP15,
    _occurrence,
    _platform_line,
    _platform_occurrence,
    _run_occurrence,
    _seed_platform,
    subscriptions_scratch,
)
from tests.test_subscriptions_isolation import _make_tenants, _seed_tenant_line
from tests.test_subscriptions_released_migration_upgrade import (
    CURRENT_SUBSCRIPTIONS,
    _config,
    _scratch_database,
)

TABLE = "mod_subscriptions.recurring_charge_occurrences"
RESOLVED_AT = datetime(2026, 10, 2, tzinfo=UTC)
_HOLD_SQL = _OCC_SQL.replace(
    "corrects_occurrence_id, generation,",
    "corrects_occurrence_id, validity_hold_reason, generation,",
).replace(":corrects, 1, :state", ":corrects, :hold, 1, :state")
assert ":hold" in _HOLD_SQL


@pytest.fixture
def admin(subscriptions_scratch: tuple[str, str, str]) -> Iterator[Connection]:
    engine = create_engine(subscriptions_scratch[0], isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        yield conn
    engine.dispose()


def _held(
    conn: Connection,
    ids: dict[str, uuid.UUID],
    tenant: uuid.UUID,
    start: datetime,
    end: datetime,
    *,
    hold: str | None = "prepaid",
    state: str = "pending_validity",
) -> uuid.UUID:
    return _run_occurrence(
        conn,
        _HOLD_SQL,
        {
            "tenant": tenant,
            "contract": ids["contract"],
            "version": ids["contract_version"],
            "key": ids["line_key"],
            "hold": hold,
        },
        start,
        end,
        coverage=None,
        state=state,
        corrects=None,
    )


def _resolve(conn: Connection, occurrence: uuid.UUID, state: str = "emitted") -> None:
    # A cancellation keeps the row's emitted_at (the freeze trigger refuses a change).
    emitted = ", emitted_at = :at" if state == "emitted" else ""
    conn.execute(
        text(
            f"UPDATE {TABLE} SET state = :state{emitted}, "
            "validity_owner_code = 'wallet', "
            "validity_evidence_ref = gen_random_uuid(), "
            "validity_resolved_at = :at, "
            "validity_resolution_command_id = gen_random_uuid(), "
            "validity_actor_ref = 'ops:alice' WHERE id = :id"
        ),
        {"state": state, "at": RESOLVED_AT, "id": occurrence},
    )


def test_a_pending_row_needs_a_hold_and_no_evidence(
    admin: Connection, subscriptions_scratch: tuple[str, str, str]
) -> None:
    left, _ = _make_tenants(subscriptions_scratch[0])
    ids = _seed_tenant_line(subscriptions_scratch[0], left)
    with pytest.raises(DBAPIError, match="ck_occurrences_validity_evidence"):
        _held(admin, ids, left, OCT1, NOV1, hold=None)
    with pytest.raises(DBAPIError, match="ck_occurrences_validity_hold"):
        _held(admin, ids, left, OCT1, NOV1, hold="mystery")
    _held(admin, ids, left, OCT1, NOV1)


def test_a_pending_row_blocks_its_coverage_until_it_is_cancelled(
    admin: Connection, subscriptions_scratch: tuple[str, str, str]
) -> None:
    left, _ = _make_tenants(subscriptions_scratch[0])
    ids = _seed_tenant_line(subscriptions_scratch[0], left)
    pending = _held(admin, ids, left, OCT1, NOV1)
    with pytest.raises(DBAPIError, match=OVERLAP):
        _occurrence(admin, ids, left, OCT1, NOV1)
    _resolve(admin, pending, "cancelled")  # the owner rejects the hold
    _occurrence(admin, ids, left, OCT1, NOV1)


def test_confirmation_needs_an_actor_a_matching_emitted_at_and_a_past_instant(
    admin: Connection, subscriptions_scratch: tuple[str, str, str]
) -> None:
    left, _ = _make_tenants(subscriptions_scratch[0])
    ids = _seed_tenant_line(subscriptions_scratch[0], left)
    pending = _held(admin, ids, left, OCT1, NOV1)
    evidence = (
        "validity_owner_code = 'wallet', validity_evidence_ref = gen_random_uuid(), "
        "validity_resolution_command_id = gen_random_uuid()"
    )
    sql = f"UPDATE {TABLE} SET state = 'emitted', {evidence}, %s WHERE id = :id"
    # No actor reference.
    with pytest.raises(DBAPIError, match="owner evidence"):
        admin.execute(
            text(sql % "emitted_at = :at, validity_resolved_at = :at"),
            {"id": pending, "at": RESOLVED_AT},
        )
    actor = ", validity_actor_ref = 'ops:alice'"
    # emitted_at differs from the resolution instant.
    with pytest.raises(DBAPIError, match="owner evidence"):
        admin.execute(
            text(sql % ("emitted_at = :at, validity_resolved_at = :at2" + actor)),
            {
                "id": pending,
                "at": RESOLVED_AT,
                "at2": RESOLVED_AT + timedelta(hours=1),
            },
        )
    # A resolution instant in the future (DB clock) is refused.
    future = datetime.now(UTC) + timedelta(days=1)
    with pytest.raises(DBAPIError, match="owner evidence"):
        admin.execute(
            text(sql % ("emitted_at = :at, validity_resolved_at = :at" + actor)),
            {"id": pending, "at": future},
        )
    _resolve(admin, pending)


def test_only_owner_evidence_resolves_a_pending_row(
    admin: Connection, subscriptions_scratch: tuple[str, str, str]
) -> None:
    left, _ = _make_tenants(subscriptions_scratch[0])
    ids = _seed_tenant_line(subscriptions_scratch[0], left)
    pending = _held(admin, ids, left, OCT1, NOV1)
    with pytest.raises(DBAPIError, match="owner evidence"):
        admin.execute(
            text(f"UPDATE {TABLE} SET state = 'emitted' WHERE id = :id"),
            {"id": pending},
        )
    with pytest.raises(DBAPIError, match="owner evidence"):
        admin.execute(
            text(f"UPDATE {TABLE} SET output_acknowledged_at = now() WHERE id = :id"),
            {"id": pending},
        )
    with pytest.raises(DBAPIError, match="immutable"):
        admin.execute(
            text(
                f"UPDATE {TABLE} SET state = 'emitted', pre_tax_amount = 1, "
                "validity_owner_code = 'wallet', "
                "validity_evidence_ref = gen_random_uuid(), "
                "validity_resolved_at = now(), "
                "validity_resolution_command_id = gen_random_uuid(), "
                "validity_actor_ref = 'ops:alice' WHERE id = :id"
            ),
            {"id": pending},
        )
    _resolve(admin, pending)
    row = admin.execute(
        text(f"SELECT state, validity_owner_code FROM {TABLE} WHERE id = :id"),
        {"id": pending},
    ).one()
    assert tuple(row) == ("emitted", "wallet")


def test_a_resolved_row_is_terminal_and_acknowledgement_still_works(
    admin: Connection, subscriptions_scratch: tuple[str, str, str]
) -> None:
    left, _ = _make_tenants(subscriptions_scratch[0])
    ids = _seed_tenant_line(subscriptions_scratch[0], left)
    emitted = ids["occurrence"]
    # emitted -> cancelled stays forbidden, with or without evidence.
    with pytest.raises(DBAPIError, match="terminal"):
        admin.execute(
            text(f"UPDATE {TABLE} SET state = 'cancelled' WHERE id = :id"),
            {"id": emitted},
        )
    with pytest.raises(DBAPIError, match="terminal"):
        _resolve(admin, emitted, "cancelled")
    # The su_0001 rules are intact.
    with pytest.raises(DBAPIError, match="immutable"):
        admin.execute(
            text(f"UPDATE {TABLE} SET pre_tax_amount = 1 WHERE id = :id"),
            {"id": emitted},
        )
    with pytest.raises(DBAPIError, match="cannot be deleted"):
        admin.execute(text(f"DELETE FROM {TABLE} WHERE id = :id"), {"id": emitted})
    ack = f"UPDATE {TABLE} SET output_acknowledged_at = now() WHERE id = :id"
    admin.execute(text(ack), {"id": emitted})
    with pytest.raises(DBAPIError, match="append-only"):
        admin.execute(text(ack), {"id": emitted})

    resolved = _held(admin, ids, left, OCT1, NOV1)
    _resolve(admin, resolved)
    with pytest.raises(DBAPIError, match="terminal"):
        _resolve(admin, resolved, "cancelled")


def test_the_platform_plane_holds_and_resolves_the_same_way(admin: Connection) -> None:
    ids = _seed_platform(admin)
    _platform_line(admin, ids)
    _platform_occurrence(admin, ids, SEP1, SEP15)
    pending = _run_occurrence(
        admin,
        _HOLD_SQL.replace(
            "mod_subscriptions.recurring_charge_occurrences",
            "mod_subscriptions.platform_recurring_charge_occurrences",
        )
        .replace("(id, tenant_id, contract_id,", "(id, contract_id,")
        .replace("(:id, :tenant, :contract,", "(:id, :contract,"),
        {
            "contract": ids["contract"],
            "version": ids["version"],
            "key": ids["line_key"],
            "hold": "capped",
        },
        SEP15,
        OCT1,
        coverage=None,
        state="pending_validity",
        corrects=None,
    )
    with pytest.raises(DBAPIError, match=OVERLAP):
        _platform_occurrence(admin, ids, SEP15, OCT1)
    admin.execute(
        text(
            "UPDATE mod_subscriptions.platform_recurring_charge_occurrences SET "
            "state = 'emitted', emitted_at = :at, validity_owner_code = 'cap', "
            "validity_evidence_ref = gen_random_uuid(), validity_resolved_at = :at, "
            "validity_resolution_command_id = gen_random_uuid(), "
            "validity_actor_ref = 'ops:alice' WHERE id = :id"
        ),
        {"at": RESOLVED_AT, "id": pending},
    )


def test_su_0006_downgrade_restores_su_0001_and_refuses_to_erase_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alembic import command

    with _scratch_database(monkeypatch) as admin_url:
        cfg = _config(CURRENT_SUBSCRIPTIONS)
        command.upgrade(cfg, "heads")
        engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        body = (
            "SELECT prosrc FROM pg_proc WHERE proname = 'freeze_occurrence_content' "
            "AND pronamespace = 'mod_subscriptions'::regnamespace"
        )
        with engine.connect() as conn:
            assert "pending_validity" in conn.scalar(text(body))
            ids: dict[str, Any] = _seed_platform(conn)
            _platform_line(conn, ids)
            hold_sql = (
                _HOLD_SQL.replace(
                    "mod_subscriptions.recurring_charge_occurrences",
                    "mod_subscriptions.platform_recurring_charge_occurrences",
                )
                .replace("(id, tenant_id, contract_id,", "(id, contract_id,")
                .replace("(:id, :tenant, :contract,", "(:id, :contract,")
            )
            _run_occurrence(
                conn,
                hold_sql,
                {
                    "contract": ids["contract"],
                    "version": ids["version"],
                    "key": ids["line_key"],
                    "hold": "prepaid",
                },
                SEP1,
                OCT1,
                coverage=None,
                state="pending_validity",
                corrects=None,
            )
        with pytest.raises(RuntimeError, match="would erase that evidence"):
            command.downgrade(cfg, "su_0005_line_coverage_overlap")
        engine.dispose()

    with _scratch_database(monkeypatch) as admin_url:
        cfg = _config(CURRENT_SUBSCRIPTIONS)
        command.upgrade(cfg, "heads")
        command.downgrade(cfg, "su_0005_line_coverage_overlap")
        engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with engine.connect() as conn:
            source = conn.scalar(text(body))
            assert "pending_validity" not in source
            assert "occurrence acknowledgement is append-only" in source
            assert not conn.scalar(
                text(
                    "SELECT 1 FROM information_schema.columns WHERE table_schema = "
                    "'mod_subscriptions' AND column_name = 'validity_hold_reason'"
                )
            )
        engine.dispose()
        command.upgrade(cfg, "heads")
