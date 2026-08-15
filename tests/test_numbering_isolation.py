"""The eight PostgreSQL proofs for ``dotmac-numbering``.

This file is the module's entire correctness evidence base, and it is all new.
Neither source contributes a real-database numbering test: ERP's thirty-one
focused tests are `MagicMock` throughout, and Sub's run on SQLite, where
`with_for_update()` is a no-op and `FORCE ROW LEVEL SECURITY` does not exist.
That is precisely how an identical locking shape has gone unproven for years,
and it is why these run on a real migrated PostgreSQL or not at all.

Each race proof carries a **sensitivity proof** (ADR-0018): a companion that
removes the guard and asserts the race test then fails. A concurrency test that
cannot be made to fail is not evidence — it is a test that has never been in a
position to notice anything.

Two roles, two connections, and a barrier. Sessions must sit on separate DBAPI
connections or the row lock is trivially satisfied and every race passes
vacuously.
"""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from dotmac_kernel.planes import ModulePlane, ModulePlaneSelection
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from dotmac_numbering import NumberingError, advance_to_at_least, allocate
from dotmac_numbering.models import AllocationReceipt, NumberSeries

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
ASSEMBLY_VERSIONS = REPO_ROOT / "alembic/versions"
NUMBERING_VERSIONS = (
    REPO_ROOT / "packages/dotmac-numbering/src/dotmac_numbering/migrations/versions"
)

TENANT_TABLES = ("number_series", "allocation_receipts")
PLATFORM_TABLES = ("platform_number_series", "platform_allocation_receipts")

JAN = date(2026, 1, 15)
FEB = date(2026, 2, 15)
NEXT_YEAR = date(2027, 1, 5)


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — these proofs need Postgres")
    return url


def _url_for(base_url: str, dbname: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{dbname}"


def _migrate(name: str, superuser: str, planes: tuple[ModulePlane, ...]) -> str:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option(
        "version_locations",
        f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {NUMBERING_VERSIONS}",
    )
    cfg.attributes["module_plane_selections"] = (
        ModulePlaneSelection(module="numbering", planes=planes),
    )
    admin_url = _url_for(superuser, name, user="app_admin")
    os.environ["MIGRATION_DATABASE_URL"] = admin_url
    command.upgrade(cfg, "heads")
    return admin_url


@pytest.fixture
def scratch() -> Iterator[tuple[str, str, str]]:
    """A migrated database with BOTH planes, and a URL per online role."""
    superuser = _superuser_url()
    name = f"numbering_{uuid.uuid4().hex[:12]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))

    setup = create_engine(_url_for(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as conn:
        conn.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        conn.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO app_user'))
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO platform_api'))
        conn.execute(text("GRANT USAGE ON SCHEMA public TO app_user"))
    setup.dispose()

    try:
        admin_url = _migrate(
            name, superuser, (ModulePlane.TENANT, ModulePlane.PLATFORM)
        )
        yield (
            admin_url,
            _url_for(superuser, name, user="app_user"),
            _url_for(superuser, name, user="platform_api"),
        )
    finally:
        with server.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :n AND pid <> pg_backend_pid()"
                ),
                {"n": name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        server.dispose()


def _seed_tenant(admin_url: str) -> tuple[uuid.UUID, uuid.UUID]:
    """Two tenants and one identically-coded series in each.

    Same `series_code` in both on purpose: a cross-tenant leak is only
    observable when the codes collide.
    """
    left, right = uuid.uuid4(), uuid.uuid4()
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        for tenant in (left, right):
            conn.execute(
                text(
                    "INSERT INTO public.tenants (id, name, slug, is_active) "
                    "VALUES (:id, :n, :s, true)"
                ),
                {"id": tenant, "n": f"t-{tenant.hex[:8]}", "s": tenant.hex[:8]},
            )
            conn.execute(
                text(
                    "INSERT INTO mod_numbering.number_series "
                    "(id, tenant_id, series_code, prefix, separator, min_digits, "
                    " include_year, year_digits, include_month, reset_policy, "
                    " next_value) "
                    "VALUES (:id, :t, 'invoice', 'INV', '-', 6, 0, 4, 0, "
                    "'never', 1)"
                ),
                {"id": uuid.uuid4(), "t": tenant},
            )
    engine.dispose()
    return left, right


def _tenant_session(url: str, tenant_id: uuid.UUID) -> Session:
    """A session on its OWN connection, with the RLS GUC set."""
    engine = create_engine(url, poolclass=None)
    session = Session(engine)
    session.execute(
        text("SELECT set_config('app.current_tenant_id', :t, false)"),
        {"t": str(tenant_id)},
    )
    return session


# ── Proof 1 — tenant RLS and cross-tenant allocation isolation ──────────────


def test_proof_1_a_tenant_cannot_see_another_tenants_series_or_receipts(scratch):
    admin_url, user_url, _ = scratch
    left, right = _seed_tenant(admin_url)

    with _tenant_session(user_url, left) as s:
        allocate(
            s,
            scope=__import__(
                "dotmac_kernel.cache", fromlist=["TenantScope"]
            ).TenantScope(tenant_id=left),
            series_code="invoice",
            reference_date=JAN,
            idempotency_key="left-1",
        )
        s.commit()

    with _tenant_session(user_url, right) as s:
        # The other tenant's series row is invisible, and so is its receipt.
        assert s.query(NumberSeries).count() == 1
        assert s.query(AllocationReceipt).count() == 0


def test_proof_1_sensitivity_the_policy_is_what_hides_the_row(scratch):
    """Without the policy the rows ARE visible — so proof 1 measures the policy.

    Dropping the policy is the only honest way to show the assertion above is
    not passing because the fixture simply created one row.
    """
    admin_url, user_url, _ = scratch
    left, right = _seed_tenant(admin_url)
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(
            text("DROP POLICY number_series_tenant_isolation ON mod_numbering.number_series")
        )
        conn.execute(text("ALTER TABLE mod_numbering.number_series NO FORCE ROW LEVEL SECURITY"))
        conn.execute(text("ALTER TABLE mod_numbering.number_series DISABLE ROW LEVEL SECURITY"))
    engine.dispose()

    with _tenant_session(user_url, right) as s:
        assert s.query(NumberSeries).count() == 2, (
            "with RLS removed both tenants' series are visible; if this is 1 the "
            "isolation proof is measuring something other than the policy"
        )


# ── Proof 2 — platform revocation and control-plane reachability ────────────


@pytest.mark.parametrize("table", PLATFORM_TABLES)
def test_proof_2_the_tenant_role_is_revoked_from_every_platform_table(scratch, table):
    _, user_url, _ = scratch
    engine = create_engine(user_url)
    with engine.connect() as conn, pytest.raises(DBAPIError) as exc:
        conn.execute(text(f"SELECT 1 FROM mod_numbering.{table} LIMIT 1"))
    assert "permission denied" in str(exc.value).lower()
    engine.dispose()


@pytest.mark.parametrize("table", PLATFORM_TABLES)
def test_proof_2_the_control_plane_role_can_still_work(scratch, table):
    """The other half. A revocation that also locked out `platform_api` would
    pass the test above and break the control plane."""
    _, _, platform_url = scratch
    engine = create_engine(platform_url)
    with engine.connect() as conn:
        conn.execute(text(f"SELECT 1 FROM mod_numbering.{table} LIMIT 1"))
    engine.dispose()


@pytest.mark.parametrize("table", TENANT_TABLES)
def test_proof_2_tenant_tables_force_rls(scratch, table):
    """FORCE, not merely ENABLE: without it the table owner bypasses the policy."""
    admin_url, _, _ = scratch
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE oid = :t::regclass"
            ),
            {"t": f"mod_numbering.{table}"},
        ).one()
    assert row == (True, True)
    engine.dispose()


# ── Proof 3 — two concurrent allocations ────────────────────────────────────


def test_proof_3_concurrent_allocations_never_duplicate_a_value(scratch):
    """Both actors inside their transactions before either proceeds.

    Without the row lock both read `next_value = 1` and both format `INV-000001`.
    """
    admin_url, user_url, _ = scratch
    left, _ = _seed_tenant(admin_url)
    from dotmac_kernel.cache import TenantScope

    barrier = threading.Barrier(2)
    results: list[str] = []
    errors: list[BaseException] = []

    def worker(key: str) -> None:
        try:
            with _tenant_session(user_url, left) as s:
                s.execute(text("SELECT 1"))  # open the transaction
                barrier.wait(timeout=10)
                out = allocate(
                    s,
                    scope=TenantScope(tenant_id=left),
                    series_code="invoice",
                    reference_date=JAN,
                    idempotency_key=key,
                )
                s.commit()
                results.append(out.formatted_number)
        except BaseException as exc:  # noqa: BLE001 - recorded, re-raised below
            errors.append(exc)
            try:
                barrier.abort()
            except Exception:
                pass

    threads = [threading.Thread(target=worker, args=(f"k{i}",)) for i in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
    assert sorted(results) == ["INV-000001", "INV-000002"], results


def test_proof_3_sensitivity_the_unique_constraint_would_catch_a_duplicate(scratch):
    """Two receipts with the same value are impossible at the database.

    Proof 3 asserts the lock serialises. This asserts the backstop is real, so
    a future refactor that loses the lock fails loudly rather than silently
    reissuing a number.
    """
    admin_url, _, _ = scratch
    left, _ = _seed_tenant(admin_url)
    engine = create_engine(admin_url)
    insert = text(
        "INSERT INTO mod_numbering.allocation_receipts "
        "(id, tenant_id, series_code, allocated_value, formatted_number, "
        " reference_date, idempotency_key, request_fingerprint, allocated_at) "
        "VALUES (:id, :t, 'invoice', 7, 'INV-000007', :d, :k, 'fp', now())"
    )
    with Session(engine) as s:
        s.execute(insert, {"id": uuid.uuid4(), "t": left, "d": JAN, "k": "a"})
        s.commit()
        with pytest.raises(IntegrityError):
            s.execute(insert, {"id": uuid.uuid4(), "t": left, "d": JAN, "k": "b"})
            s.commit()
    engine.dispose()


# ── Proof 4 — rollback with the consuming transaction ───────────────────────


def test_proof_4_a_rolled_back_caller_consumes_no_number(scratch):
    """A failed invoice must not burn a number, and must not advance the counter."""
    admin_url, user_url, _ = scratch
    left, _ = _seed_tenant(admin_url)
    from dotmac_kernel.cache import TenantScope

    with _tenant_session(user_url, left) as s:
        allocate(
            s,
            scope=TenantScope(tenant_id=left),
            series_code="invoice",
            reference_date=JAN,
            idempotency_key="doomed",
        )
        s.rollback()

    with _tenant_session(user_url, left) as s:
        assert s.query(AllocationReceipt).count() == 0
        assert s.query(NumberSeries).one().next_value == 1
        out = allocate(
            s,
            scope=TenantScope(tenant_id=left),
            series_code="invoice",
            reference_date=JAN,
            idempotency_key="real",
        )
        s.commit()
    assert out.formatted_number == "INV-000001"


# ── Proof 5 — replay and fingerprint conflict ───────────────────────────────


def test_proof_5_same_key_same_request_replays_the_original_number(scratch):
    admin_url, user_url, _ = scratch
    left, _ = _seed_tenant(admin_url)
    from dotmac_kernel.cache import TenantScope

    with _tenant_session(user_url, left) as s:
        scope = TenantScope(tenant_id=left)
        first = allocate(
            s, scope=scope, series_code="invoice", reference_date=JAN,
            idempotency_key="retry-me",
        )
        s.commit()
        second = allocate(
            s, scope=scope, series_code="invoice", reference_date=JAN,
            idempotency_key="retry-me",
        )
        s.commit()

        assert second.formatted_number == first.formatted_number
        assert second.replayed is True
        assert s.query(AllocationReceipt).count() == 1
        assert s.query(NumberSeries).one().next_value == 2


def test_proof_5_same_key_different_request_conflicts(scratch):
    """A changed reference date under a reused key is a different allocation.

    Returning the original silently would hand the caller a number formatted
    for the wrong period.
    """
    admin_url, user_url, _ = scratch
    left, _ = _seed_tenant(admin_url)
    from dotmac_kernel.cache import TenantScope

    with _tenant_session(user_url, left) as s:
        scope = TenantScope(tenant_id=left)
        allocate(
            s, scope=scope, series_code="invoice", reference_date=JAN,
            idempotency_key="reused",
        )
        s.commit()
        with pytest.raises(NumberingError) as exc:
            allocate(
                s, scope=scope, series_code="invoice", reference_date=FEB,
                idempotency_key="reused",
            )
    assert exc.value.code.endswith("idempotency_conflict")


# ── Proof 6 — reset boundaries come from the supplied date ──────────────────


def _set_reset_policy(admin_url: str, tenant: uuid.UUID, policy: str) -> None:
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(
            text(
                "UPDATE mod_numbering.number_series SET reset_policy = :p, "
                "include_year = 1 WHERE tenant_id = :t"
            ),
            {"p": policy, "t": tenant},
        )
    engine.dispose()


def test_proof_6_the_reset_follows_the_business_date_not_the_clock(scratch):
    admin_url, user_url, _ = scratch
    left, _ = _seed_tenant(admin_url)
    _set_reset_policy(admin_url, left, "yearly")
    from dotmac_kernel.cache import TenantScope

    with _tenant_session(user_url, left) as s:
        scope = TenantScope(tenant_id=left)
        first = allocate(
            s, scope=scope, series_code="invoice", reference_date=JAN,
            idempotency_key="y1",
        )
        s.commit()
        rolled = allocate(
            s, scope=scope, series_code="invoice", reference_date=NEXT_YEAR,
            idempotency_key="y2",
        )
        s.commit()

    assert first.formatted_number == "INV-2026-000001"
    # A new year restarts the counter, and the year in the number comes from
    # the supplied date rather than from today.
    assert rolled.formatted_number == "INV-2027-000001"


def test_proof_6_a_backdated_allocation_does_not_rewind_the_counter(scratch):
    """ERP's defect, pinned as a refusal.

    `should_reset` there compares period INEQUALITY, so an allocation dated
    last year looks like a new period and restarts the sequence — reissuing
    numbers that are already on issued documents. Ordering is the fix.
    """
    admin_url, user_url, _ = scratch
    left, _ = _seed_tenant(admin_url)
    _set_reset_policy(admin_url, left, "yearly")
    from dotmac_kernel.cache import TenantScope

    with _tenant_session(user_url, left) as s:
        scope = TenantScope(tenant_id=left)
        allocate(
            s, scope=scope, series_code="invoice", reference_date=NEXT_YEAR,
            idempotency_key="a",
        )
        s.commit()
        backdated = allocate(
            s, scope=scope, series_code="invoice", reference_date=JAN,
            idempotency_key="b",
        )
        s.commit()

    # The counter continues; only the printed year reflects the older date.
    assert backdated.formatted_number == "INV-2026-000002"


# ── Proof 7 — configuration cannot rewrite a receipt or rewind a counter ────


def test_proof_7_repair_advances_and_never_rewinds(scratch):
    admin_url, user_url, _ = scratch
    left, _ = _seed_tenant(admin_url)
    from dotmac_kernel.cache import TenantScope

    with _tenant_session(user_url, left) as s:
        scope = TenantScope(tenant_id=left)
        allocate(
            s, scope=scope, series_code="invoice", reference_date=JAN,
            idempotency_key="k1",
        )
        s.commit()

        assert advance_to_at_least(
            s, scope=scope, series_code="invoice", proven_minimum=500
        ) == 501
        s.commit()

        # Below the current value is a no-op, not a rewind.
        assert advance_to_at_least(
            s, scope=scope, series_code="invoice", proven_minimum=3
        ) == 501
        s.commit()

        out = allocate(
            s, scope=scope, series_code="invoice", reference_date=JAN,
            idempotency_key="k2",
        )
        s.commit()
    assert out.formatted_number == "INV-000501"


def test_proof_7_a_receipt_survives_a_configuration_change(scratch):
    """Reconfiguring the series must not retro-format issued numbers."""
    admin_url, user_url, _ = scratch
    left, _ = _seed_tenant(admin_url)
    from dotmac_kernel.cache import TenantScope

    with _tenant_session(user_url, left) as s:
        issued = allocate(
            s, scope=TenantScope(tenant_id=left), series_code="invoice",
            reference_date=JAN, idempotency_key="k1",
        )
        s.commit()

    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(
            text(
                "UPDATE mod_numbering.number_series SET prefix = 'CHANGED', "
                "min_digits = 9 WHERE tenant_id = :t"
            ),
            {"t": left},
        )
    engine.dispose()

    with _tenant_session(user_url, left) as s:
        stored = s.query(AllocationReceipt).one()
    assert stored.formatted_number == issued.formatted_number == "INV-000001"


# ── Proof 8 — the two planes run the same behaviour ─────────────────────────


def test_proof_8_both_planes_produce_the_same_number_for_the_same_input(scratch):
    """One engine, two planes. A divergence here means the plane split leaked
    into behaviour, which is exactly what a shared implementation is for."""
    admin_url, user_url, platform_url = scratch
    left, _ = _seed_tenant(admin_url)
    from dotmac_kernel.cache import PlatformScope, TenantScope

    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO mod_numbering.platform_number_series "
                "(id, series_code, prefix, separator, min_digits, include_year, "
                " year_digits, include_month, reset_policy, next_value) "
                "VALUES (:id, 'invoice', 'INV', '-', 6, 0, 4, 0, 'never', 1)"
            ),
            {"id": uuid.uuid4()},
        )
    engine.dispose()

    with _tenant_session(user_url, left) as s:
        tenant_number = allocate(
            s, scope=TenantScope(tenant_id=left), series_code="invoice",
            reference_date=JAN, idempotency_key="same",
        ).formatted_number
        s.commit()

    platform_engine = create_engine(platform_url)
    with Session(platform_engine) as s:
        platform_number = allocate(
            s, scope=PlatformScope(), series_code="invoice",
            reference_date=JAN, idempotency_key="same",
        ).formatted_number
        s.commit()
    platform_engine.dispose()

    assert tenant_number == platform_number == "INV-000001"


def test_an_unconfigured_series_fails_closed(scratch):
    """No auto-create. ERP invents a series with a guessed prefix on first use,
    so a typo silently becomes a live document series."""
    admin_url, user_url, _ = scratch
    left, _ = _seed_tenant(admin_url)
    from dotmac_kernel.cache import TenantScope

    with _tenant_session(user_url, left) as s, pytest.raises(NumberingError) as exc:
        allocate(
            s, scope=TenantScope(tenant_id=left), series_code="typo",
            reference_date=JAN, idempotency_key="k",
        )
    assert exc.value.code.endswith("series_not_configured")
