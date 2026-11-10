# ruff: noqa: F811
# (the scratch-database fixture is imported by name so pytest registers it)
"""PostgreSQL proofs for su_0004 (named catalogue price) and su_0005 (coverage).

The unit suite proves the service refuses a mismatched price; these prove the
TABLES refuse it for a writer that bypasses the service, and that one contract
line can never be charged twice for the same instant of service. Postgres only.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

from tests.test_subscriptions_isolation import (  # noqa: F401  (fixture re-export)
    _CONTRACT_VERSION_INSERT,
    _NEXT_CONTRACT_VERSION_INSERT,
    _OCCURRENCE_INSERT,
    _SEED_FINGERPRINT,
    _make_tenants,
    _seed_tenant_line,
    subscriptions_scratch,
)
from tests.test_subscriptions_released_migration_upgrade import (
    CURRENT_SUBSCRIPTIONS,
    _config,
    _scratch_database,
)

SEP1 = datetime(2026, 9, 1, tzinfo=UTC)
SEP10 = datetime(2026, 9, 10, tzinfo=UTC)
SEP15 = datetime(2026, 9, 15, tzinfo=UTC)
OCT1 = datetime(2026, 10, 1, tzinfo=UTC)
OCT10 = datetime(2026, 10, 10, tzinfo=UTC)
OCT15 = datetime(2026, 10, 15, tzinfo=UTC)
NOV1 = datetime(2026, 11, 1, tzinfo=UTC)
OVERLAP = "overlaps another charge"
SAME_LINE = "same contract line"
TRIGGERS = {"occurrences_coverage_overlap", "platform_occurrences_coverage_overlap"}

# The shared occurrence insert, widened so coverage can differ from the period,
# a correction can be named, and the state is a parameter.
_OCC_SQL = (
    _OCCURRENCE_INSERT.replace(
        "100, 2, :start, :end, 100, 1,", "100, 2, :cstart, :cend, 100, 1,"
    )
    .replace(
        "idempotency_key, generation,",
        "idempotency_key, corrects_occurrence_id, generation,",
    )
    .replace(":idempotency, 1, 'emitted'", ":idempotency, :corrects, 1, :state")
)
assert ":cstart" in _OCC_SQL and ":corrects" in _OCC_SQL and ":state" in _OCC_SQL
_PLATFORM_OCC_SQL = (
    _OCC_SQL.replace(
        "mod_subscriptions.recurring_charge_occurrences",
        "mod_subscriptions.platform_recurring_charge_occurrences",
    )
    .replace("(id, tenant_id, contract_id,", "(id, contract_id,")
    .replace("(:id, :tenant, :contract,", "(:id, :contract,")
)
assert "platform_recurring" in _PLATFORM_OCC_SQL and "tenant" not in _PLATFORM_OCC_SQL


@pytest.fixture
def admin(subscriptions_scratch: tuple[str, str, str]) -> Iterator[Connection]:
    engine = create_engine(subscriptions_scratch[0], isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        yield conn
    engine.dispose()


def _run_occurrence(
    conn: Connection,
    sql: str,
    params: dict[str, Any],
    start: datetime,
    end: datetime,
    *,
    coverage: tuple[datetime, datetime] | None,
    state: str,
    corrects: uuid.UUID | None,
) -> uuid.UUID:
    occurrence_id = uuid.uuid4()
    cstart, cend = coverage or (start, end)
    conn.execute(
        text(sql),
        {
            **params,
            "id": occurrence_id,
            "start": start,
            "end": end,
            "cstart": cstart,
            "cend": cend,
            "idempotency": f"occ-{uuid.uuid4().hex}",
            "fingerprint": _SEED_FINGERPRINT,
            "state": state,
            "corrects": corrects,
        },
    )
    return occurrence_id


def _occurrence(
    conn: Connection,
    ids: dict[str, uuid.UUID],
    tenant: uuid.UUID,
    start: datetime,
    end: datetime,
    *,
    coverage: tuple[datetime, datetime] | None = None,
    version: uuid.UUID | None = None,
    key: uuid.UUID | None = None,
    state: str = "emitted",
    corrects: uuid.UUID | None = None,
) -> uuid.UUID:
    return _run_occurrence(
        conn,
        _OCC_SQL,
        {
            "tenant": tenant,
            "contract": ids["contract"],
            "version": version or ids["contract_version"],
            "key": key or ids["line_key"],
        },
        start,
        end,
        coverage=coverage,
        state=state,
        corrects=corrects,
    )


_LINE_INSERT = (
    "INSERT INTO mod_subscriptions.subscription_contract_lines "
    "(id, tenant_id, contract_version_id, contract_line_key, charge_model_code, "
    "source_code, source_id, source_version, description, product_link_ref, "
    "quantity, unit_price, currency, scale, offer_version_id, offer_version, "
    "price_key, entitlement_codes) "
    "VALUES (gen_random_uuid(), :tenant, :version, :key, 'recurring_access', "
    "'seed', gen_random_uuid(), 1, 'Access', :link, 1, :amount, :currency, :scale, "
    ":offer_version, 1, :price_key, '[]'::jsonb)"
)


def _line(
    conn: Connection,
    tenant: uuid.UUID,
    version: uuid.UUID,
    offer_version: uuid.UUID,
    **overrides: Any,
) -> None:
    params: dict[str, Any] = {
        "tenant": tenant,
        "version": version,
        "key": uuid.uuid4(),
        "link": f"product:{uuid.uuid4().hex}",
        "amount": 100,
        "currency": "EUR",
        "scale": 2,
        "offer_version": offer_version,
        "price_key": "base",
    }
    params.update(overrides)
    conn.execute(text(_LINE_INSERT), params)


def _catalog_offer(conn: Connection, tenant: uuid.UUID) -> uuid.UUID:
    offer, version = uuid.uuid4(), uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO mod_subscriptions.offers (id, tenant_id, code, name, status) "
            "VALUES (:id, :tenant, :code, 'Catalog', 'published')"
        ),
        {"id": offer, "tenant": tenant, "code": f"c-{uuid.uuid4().hex[:8]}"},
    )
    conn.execute(
        text(
            "INSERT INTO mod_subscriptions.offer_versions "
            "(id, tenant_id, offer_id, version, charge_model_code, pricing_mode, "
            "state, effective_from, source_code, source_id, source_version, "
            "command_id, content_digest) VALUES (:id, :tenant, :offer, 1, "
            "'recurring_access', 'catalog_price', 'published', :start, 'seed', "
            "gen_random_uuid(), 1, gen_random_uuid(), :digest)"
        ),
        {
            "id": version,
            "tenant": tenant,
            "offer": offer,
            "start": SEP1,
            "digest": "b" * 64,
        },
    )
    conn.execute(
        text(
            "INSERT INTO mod_subscriptions.offer_version_prices "
            "(id, tenant_id, offer_version_id, price_key, charge_model_code, "
            "amount, currency, scale, quantity) VALUES (gen_random_uuid(), "
            ":tenant, :version, 'base', 'recurring_access', 100, 'EUR', 2, 1)"
        ),
        {"tenant": tenant, "version": version},
    )
    return version


def _seed_platform(conn: Connection) -> dict[str, uuid.UUID]:
    ids = {
        name: uuid.uuid4()
        for name in ("offer", "catalog", "contract", "version", "line_key")
    }
    conn.execute(
        text(
            "INSERT INTO mod_subscriptions.platform_offers "
            "(id, code, name, status) VALUES (:id, 'p.catalog', 'P', 'published')"
        ),
        {"id": ids["offer"]},
    )
    conn.execute(
        text(
            "INSERT INTO mod_subscriptions.platform_offer_versions "
            "(id, offer_id, version, charge_model_code, pricing_mode, state, "
            "effective_from, source_code, source_id, source_version, "
            "command_id, content_digest) VALUES (:id, :offer, 1, "
            "'recurring_access', 'catalog_price', 'published', :start, 'seed', "
            "gen_random_uuid(), 1, gen_random_uuid(), :digest)"
        ),
        {
            "id": ids["catalog"],
            "offer": ids["offer"],
            "start": SEP1,
            "digest": "a" * 64,
        },
    )
    conn.execute(
        text(
            "INSERT INTO mod_subscriptions.platform_offer_version_prices "
            "(id, offer_version_id, price_key, charge_model_code, amount, "
            "currency, scale, quantity) VALUES (gen_random_uuid(), :version, "
            "'base', 'recurring_access', 100, 'EUR', 2, 1)"
        ),
        {"version": ids["catalog"]},
    )
    conn.execute(
        text(
            "INSERT INTO mod_subscriptions.platform_subscription_contracts "
            "(id, source_code, source_id) VALUES (:id, 'seed', gen_random_uuid())"
        ),
        {"id": ids["contract"]},
    )
    conn.execute(
        text(
            _CONTRACT_VERSION_INSERT.replace(
                "subscription_contract_versions",
                "platform_subscription_contract_versions",
            )
            .replace("id, tenant_id, contract_id", "id, contract_id")
            .replace(":id, :tenant, :contract", ":id, :contract")
        ),
        {
            "id": ids["version"],
            "contract": ids["contract"],
            "start": SEP1,
            "key": f"p-{uuid.uuid4().hex}",
        },
    )
    return ids


_PLATFORM_LINE = (
    "INSERT INTO mod_subscriptions.platform_subscription_contract_lines "
    "(id, contract_version_id, contract_line_key, charge_model_code, "
    "source_code, source_id, source_version, description, product_link_ref, "
    "quantity, unit_price, currency, scale, offer_version_id, offer_version, "
    "price_key, entitlement_codes) VALUES (gen_random_uuid(), :version, "
    ":key, 'recurring_access', 'seed', gen_random_uuid(), 1, 'Access', "
    ":link, 1, :amount, 'EUR', 2, :offer_version, 1, :price_key, '[]'::jsonb)"
)


def _platform_line(
    conn: Connection, ids: dict[str, uuid.UUID], **overrides: Any
) -> None:
    params: dict[str, Any] = {
        "version": ids["version"],
        "key": ids["line_key"],
        "link": f"product:{uuid.uuid4().hex}",
        "amount": 100,
        "offer_version": ids["catalog"],
        "price_key": "base",
    }
    params.update(overrides)
    conn.execute(text(_PLATFORM_LINE), params)


def _platform_occurrence(
    conn: Connection,
    ids: dict[str, uuid.UUID],
    start: datetime,
    end: datetime,
    *,
    state: str = "emitted",
    corrects: uuid.UUID | None = None,
) -> uuid.UUID:
    return _run_occurrence(
        conn,
        _PLATFORM_OCC_SQL,
        {
            "contract": ids["contract"],
            "version": ids["version"],
            "key": ids["line_key"],
        },
        start,
        end,
        coverage=None,
        state=state,
        corrects=corrects,
    )


def test_a_catalog_price_line_must_name_and_equal_the_published_price(
    admin: Connection, subscriptions_scratch: tuple[str, str, str]
) -> None:
    left, _ = _make_tenants(subscriptions_scratch[0])
    ids = _seed_tenant_line(subscriptions_scratch[0], left)
    offer_version = _catalog_offer(admin, left)
    version = ids["contract_version"]

    _line(admin, left, version, offer_version)  # equal to the published price

    for overrides, message in (
        ({"price_key": None}, "must name a price_key"),
        ({"price_key": "nope"}, "does not publish"),
        ({"amount": 99}, "must equal the published"),
        ({"currency": "USD"}, "must equal the published"),
        ({"scale": 3}, "must equal the published"),
    ):
        with pytest.raises(DBAPIError, match=message):
            _line(admin, left, version, offer_version, **overrides)

    # The line states the version it copies: a lie about it is refused too.
    with pytest.raises(DBAPIError, match="differs from its offer version"):
        admin.execute(
            text(_LINE_INSERT.replace(":offer_version, 1,", ":offer_version, 2,")),
            {
                "tenant": left,
                "version": version,
                "key": uuid.uuid4(),
                "link": "product:other",
                "amount": 100,
                "currency": "EUR",
                "scale": 2,
                "offer_version": offer_version,
                "price_key": "base",
            },
        )

    # A contract_price line is its own price and must not name a catalogue key.
    with pytest.raises(DBAPIError, match="must not name a catalogue"):
        _line(admin, left, version, ids["offer_version"], price_key="base")
    _line(admin, left, version, ids["offer_version"], price_key=None)


def test_the_named_price_is_a_composite_foreign_key_on_both_planes(
    admin: Connection,
) -> None:
    rows = admin.execute(
        text(
            "SELECT conname, convalidated, pg_get_constraintdef(oid) "
            "FROM pg_constraint WHERE conname IN "
            "('fk_contract_lines_catalog_price', "
            "'fk_platform_contract_lines_catalog_price')"
        )
    ).all()
    found = {name: (validated, definition) for name, validated, definition in rows}
    assert set(found) == {
        "fk_contract_lines_catalog_price",
        "fk_platform_contract_lines_catalog_price",
    }
    assert all(validated for validated, _ in found.values())
    tenant_definition = found["fk_contract_lines_catalog_price"][1]
    platform_definition = found["fk_platform_contract_lines_catalog_price"][1]
    assert "(tenant_id, offer_version_id, price_key)" in tenant_definition
    assert "(offer_version_id, price_key)" in platform_definition


def test_the_overlap_guard_is_a_plain_trigger_with_no_extension(
    admin: Connection,
) -> None:
    triggers = set(
        admin.scalars(
            text(
                "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal "
                "AND tgname LIKE '%coverage_overlap'"
            )
        )
    )
    assert triggers == TRIGGERS
    assert not admin.scalar(
        text("SELECT 1 FROM pg_extension WHERE extname = 'btree_gist'")
    )
    assert not admin.scalar(
        text(
            "SELECT 1 FROM pg_constraint WHERE contype = 'x' AND "
            "connamespace = 'mod_subscriptions'::regnamespace"
        )
    )
    unique = set(
        admin.scalars(
            text(
                "SELECT indexname FROM pg_indexes WHERE schemaname = 'mod_subscriptions' "
                "AND indexdef LIKE 'CREATE UNIQUE INDEX%' "
                "AND indexdef LIKE '%WHERE%cancelled%' AND indexname IN "
                "('uq_occurrences_correction', 'uq_platform_occurrences_correction')"
            )
        )
    )
    assert unique == {"uq_occurrences_correction", "uq_platform_occurrences_correction"}


def test_overlapping_coverage_on_one_line_is_refused(
    admin: Connection, subscriptions_scratch: tuple[str, str, str]
) -> None:
    left, _ = _make_tenants(subscriptions_scratch[0])
    ids = _seed_tenant_line(subscriptions_scratch[0], left)  # Sep 1 - Oct 1
    for start, end in ((SEP15, OCT15), (SEP1, OCT1), (SEP1, SEP15)):
        with pytest.raises(DBAPIError, match=OVERLAP):
            _occurrence(admin, ids, left, start, end)
    # Half-open: the next period starts exactly where this one ends.
    _occurrence(admin, ids, left, OCT1, NOV1)


def test_the_rule_is_on_coverage_not_on_the_period(
    admin: Connection, subscriptions_scratch: tuple[str, str, str]
) -> None:
    left, _ = _make_tenants(subscriptions_scratch[0])
    ids = _seed_tenant_line(subscriptions_scratch[0], left)
    key = uuid.uuid4()
    _line(
        admin,
        left,
        ids["contract_version"],
        ids["offer_version"],
        key=key,
        price_key=None,
    )
    _occurrence(admin, ids, left, SEP1, OCT1, coverage=(SEP1, SEP15), key=key)
    # Periods overlap, coverage does not: allowed.
    _occurrence(admin, ids, left, SEP10, OCT10, coverage=(SEP15, OCT10), key=key)
    # Coverage overlaps: refused.
    with pytest.raises(DBAPIError, match=OVERLAP):
        _occurrence(admin, ids, left, SEP10, OCT10, coverage=(SEP10, SEP15), key=key)


def test_the_same_coverage_under_a_new_contract_version_is_refused(
    admin: Connection, subscriptions_scratch: tuple[str, str, str]
) -> None:
    left, _ = _make_tenants(subscriptions_scratch[0])
    ids = _seed_tenant_line(subscriptions_scratch[0], left)
    next_version = uuid.uuid4()
    admin.execute(
        text(_NEXT_CONTRACT_VERSION_INSERT),
        {
            "id": next_version,
            "tenant": left,
            "contract": ids["contract"],
            "start": OCT1,
            "key": f"seed-{uuid.uuid4().hex}",
        },
    )
    # The same stable line key, carried into the new contract version.
    _line(
        admin,
        left,
        next_version,
        ids["offer_version"],
        key=ids["line_key"],
        price_key=None,
    )
    with pytest.raises(DBAPIError, match=OVERLAP):
        _occurrence(admin, ids, left, SEP1, OCT1, version=next_version)
    _occurrence(admin, ids, left, OCT1, NOV1, version=next_version)


def test_cancelled_rows_do_not_block_and_other_lines_and_tenants_are_free(
    admin: Connection, subscriptions_scratch: tuple[str, str, str]
) -> None:
    left, right = _make_tenants(subscriptions_scratch[0])
    ids = _seed_tenant_line(subscriptions_scratch[0], left)
    other_tenant = _seed_tenant_line(subscriptions_scratch[0], right)

    _occurrence(admin, ids, left, SEP1, OCT1, state="cancelled")
    _occurrence(admin, ids, left, OCT1, NOV1, state="cancelled")
    _occurrence(admin, ids, left, OCT1, NOV1)  # a live row over a cancelled one
    with pytest.raises(DBAPIError, match=OVERLAP):
        _occurrence(admin, ids, left, OCT15, NOV1)
    # A cancelled row is not itself checked either.
    _occurrence(admin, ids, left, OCT15, NOV1, state="cancelled")

    second_key = uuid.uuid4()
    _line(
        admin,
        left,
        ids["contract_version"],
        ids["offer_version"],
        key=second_key,
        price_key=None,
    )
    _occurrence(admin, ids, left, SEP1, OCT1, key=second_key)
    # The other tenant already holds Sep 1 - Oct 1 on its own line; tenants and
    # lines are independent, so it can also take the next period.
    _occurrence(admin, other_tenant, right, OCT1, NOV1)


def test_a_correction_may_replace_its_chain_but_only_once_and_on_one_line(
    admin: Connection, subscriptions_scratch: tuple[str, str, str]
) -> None:
    left, _ = _make_tenants(subscriptions_scratch[0])
    ids = _seed_tenant_line(subscriptions_scratch[0], left)
    original = ids["occurrence"]  # emitted, Sep 1 - Oct 1

    # Billing reads a correction as a REPLACEMENT, so it may overlap what it replaces.
    first = _occurrence(admin, ids, left, SEP1, SEP15, corrects=original)
    with pytest.raises(DBAPIError, match="uq_occurrences_correction"):
        _occurrence(admin, ids, left, SEP1, SEP15, corrects=original)
    second = _occurrence(admin, ids, left, SEP1, OCT1, corrects=first)

    # A further correction replaces the head; only the head blocks others.
    _occurrence(admin, ids, left, SEP15, NOV1, corrects=second)
    with pytest.raises(DBAPIError, match=OVERLAP):
        _occurrence(admin, ids, left, OCT1, NOV1)

    # A correction names an occurrence of the SAME contract line.
    other_key = uuid.uuid4()
    _line(
        admin,
        left,
        ids["contract_version"],
        ids["offer_version"],
        key=other_key,
        price_key=None,
    )
    foreign = _occurrence(admin, ids, left, SEP1, OCT1, key=other_key)
    with pytest.raises(DBAPIError, match=SAME_LINE):
        _occurrence(
            admin, ids, left, NOV1, datetime(2026, 12, 1, tzinfo=UTC), corrects=foreign
        )


def test_a_cancelled_correction_neither_extends_exemption_nor_uses_the_slot(
    admin: Connection, subscriptions_scratch: tuple[str, str, str]
) -> None:
    left, _ = _make_tenants(subscriptions_scratch[0])
    ids = _seed_tenant_line(subscriptions_scratch[0], left)
    original = ids["occurrence"]  # emitted, Sep 1 - Oct 1

    # P corrects A and is cancelled, so A is live again.
    cancelled = _occurrence(
        admin, ids, left, SEP1, SEP15, corrects=original, state="cancelled"
    )
    # A correction of the cancelled P would overlap the live A: refused.
    with pytest.raises(DBAPIError, match="only an emitted"):
        _occurrence(admin, ids, left, SEP1, OCT1, corrects=cancelled)
    with pytest.raises(DBAPIError, match=OVERLAP):
        _occurrence(admin, ids, left, SEP15, OCT15)
    # The cancelled successor did not use up A's one-successor slot.
    live = _occurrence(admin, ids, left, SEP1, SEP15, corrects=original)
    with pytest.raises(DBAPIError, match="uq_occurrences_correction"):
        _occurrence(admin, ids, left, SEP1, SEP15, corrects=original)
    # A chain continues from the live head.
    _occurrence(admin, ids, left, SEP1, SEP15, corrects=live)


def test_supersession_recovers_the_old_straddler_then_the_new_version_fits(
    admin: Connection, subscriptions_scratch: tuple[str, str, str]
) -> None:
    left, _ = _make_tenants(subscriptions_scratch[0])
    ids = _seed_tenant_line(subscriptions_scratch[0], left)
    original = ids["occurrence"]  # old emitted straddler, covers [Sep 1, Oct 1)
    # The new version starts at S = Sep 15 and would overlap the straddler...
    with pytest.raises(DBAPIError, match=OVERLAP):
        _occurrence(admin, ids, left, SEP15, NOV1)
    # ...until a correction re-covers the old row as [P0, S).
    _occurrence(admin, ids, left, SEP1, SEP15, corrects=original)
    _occurrence(admin, ids, left, SEP15, NOV1)


def test_the_guard_refuses_to_run_under_repeatable_read(
    subscriptions_scratch: tuple[str, str, str],
) -> None:
    admin_url = subscriptions_scratch[0]
    left, _ = _make_tenants(admin_url)
    ids = _seed_tenant_line(admin_url, left)
    engine = create_engine(admin_url, isolation_level="REPEATABLE READ")
    try:
        with engine.connect() as conn:
            with pytest.raises(DBAPIError, match="REPEATABLE READ"):
                _occurrence(conn, ids, left, OCT1, NOV1)
            conn.rollback()
    finally:
        engine.dispose()


def test_the_platform_plane_has_the_same_rules(
    admin: Connection,
) -> None:
    ids = _seed_platform(admin)
    with pytest.raises(DBAPIError, match="must equal the published"):
        _platform_line(admin, ids, amount=99)
    with pytest.raises(DBAPIError, match="must name a price_key"):
        _platform_line(admin, ids, price_key=None)
    _platform_line(admin, ids)

    first = _platform_occurrence(admin, ids, SEP1, OCT1)
    with pytest.raises(DBAPIError, match=OVERLAP):
        _platform_occurrence(admin, ids, SEP15, OCT15)
    _platform_occurrence(admin, ids, SEP15, OCT15, state="cancelled")
    _platform_occurrence(admin, ids, OCT1, NOV1)

    # One successor per occurrence; a correction may overlap what it replaces.
    _platform_occurrence(admin, ids, SEP1, SEP15, corrects=first)
    with pytest.raises(DBAPIError, match="uq_platform_occurrences_correction"):
        _platform_occurrence(admin, ids, SEP1, SEP15, corrects=first)


def test_migrations_roll_back_and_forward_and_refuse_to_lose_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alembic import command

    with _scratch_database(monkeypatch) as admin_url:
        cfg = _config(CURRENT_SUBSCRIPTIONS)
        command.upgrade(cfg, "heads")
        engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")

        def present(sql: str) -> bool:
            with engine.connect() as conn:
                return bool(conn.scalar(text(sql)))

        trigger = (
            "SELECT 1 FROM pg_trigger WHERE tgname = "
            "'platform_occurrences_coverage_overlap'"
        )
        assert present(trigger)
        assert not present("SELECT 1 FROM pg_extension WHERE extname = 'btree_gist'")

        # su_0005 down: trigger, function, index and uniqueness go.
        command.downgrade(cfg, "su_0004_catalog_price_line")
        assert not present(trigger)
        assert not present(
            "SELECT 1 FROM pg_proc WHERE proname = "
            "'prevent_platform_occurrence_coverage_overlap'"
        )
        assert not present(
            "SELECT 1 FROM pg_indexes WHERE indexname = "
            "'uq_platform_occurrences_correction'"
        )

        # Existing overlapping evidence makes su_0005 refuse BEFORE any DDL.
        with engine.connect() as conn:
            ids = _seed_platform(conn)
            _platform_line(conn, ids)
            _platform_occurrence(conn, ids, SEP1, OCT1)
            _platform_occurrence(conn, ids, SEP15, OCT15)
        with pytest.raises(RuntimeError, match="overlapping current occurrence"):
            command.upgrade(cfg, "heads")
        assert not present(trigger)

        engine.dispose()


def test_su_0004_rolls_back_and_refuses_to_erase_a_named_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alembic import command

    with _scratch_database(monkeypatch) as admin_url:
        cfg = _config(CURRENT_SUBSCRIPTIONS)
        command.upgrade(cfg, "heads")
        engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with engine.connect() as conn:
            ids = _seed_platform(conn)
            _platform_line(conn, ids)  # names price_key 'base'
        with pytest.raises(RuntimeError, match="name a catalogue price"):
            command.downgrade(cfg, "su_0003_billing_treatments")
        engine.dispose()


def test_su_0004_downgrade_and_reupgrade_are_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alembic import command

    with _scratch_database(monkeypatch) as admin_url:
        cfg = _config(CURRENT_SUBSCRIPTIONS)
        command.upgrade(cfg, "heads")
        command.downgrade(cfg, "su_0003_billing_treatments")
        engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with engine.connect() as conn:
            assert not conn.scalar(
                text(
                    "SELECT 1 FROM information_schema.columns WHERE table_schema = "
                    "'mod_subscriptions' AND column_name = 'price_key' "
                    "AND table_name LIKE '%contract_lines'"
                )
            )
            assert not conn.scalar(
                text(
                    "SELECT 1 FROM pg_trigger WHERE tgname = "
                    "'platform_contract_lines_catalog_price'"
                )
            )
        engine.dispose()
        command.upgrade(cfg, "heads")
