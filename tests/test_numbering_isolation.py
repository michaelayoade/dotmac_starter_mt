"""The PostgreSQL proofs for ``dotmac-numbering``.

This file is the module's entire correctness evidence base, and it is all new.
Neither source contributes a real-database numbering test: ERP's thirty-one
focused tests are `MagicMock` throughout, and Sub's run on SQLite, where
`with_for_update()` is a no-op and `FORCE ROW LEVEL SECURITY` does not exist.
That is how an identical locking shape went unproven for years.

Each guard has a **hostile companion** (ADR-0018) that removes the guard and
asserts the failure it was hiding. For the allocation race that means patching
the module to select WITHOUT `FOR UPDATE` and showing two concurrent callers
then take the same value — not merely showing that a hand-inserted duplicate
trips a unique index, which proves the index and says nothing about the lock.

Sessions sit on separate DBAPI connections and rendezvous on a
`threading.Barrier`, or the lock is trivially satisfied and every race passes
vacuously.
"""

from __future__ import annotations

import contextlib
import os
import threading
import uuid
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.idempotency import IdempotencyConflict
from dotmac_kernel.planes import ModulePlane, ModulePlaneSelection
from dotmac_numbering import (
    NumberingError,
    SeriesConfiguration,
    advance_to_at_least,
    allocate,
    configure_series,
    preview,
)
from dotmac_numbering.models import AllocationReceipt, NumberSeries, SeriesRepair
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
ASSEMBLY_VERSIONS = REPO_ROOT / "alembic/versions"
NUMBERING_VERSIONS = (
    REPO_ROOT / "packages/dotmac-numbering/src/dotmac_numbering/migrations/versions"
)

TENANT_MUTABLE = ("number_series", "series_counters")
TENANT_IMMUTABLE = ("allocation_receipts", "series_repairs")
PLATFORM_TABLES = (
    "platform_number_series",
    "platform_series_counters",
    "platform_allocation_receipts",
    "platform_series_repairs",
)
PLATFORM_IMMUTABLE = ("platform_allocation_receipts", "platform_series_repairs")

JAN = date(2026, 1, 15)
FEB = date(2026, 2, 15)
DEC = date(2026, 12, 31)
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
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        cfg.set_main_option(
            "version_locations",
            f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {NUMBERING_VERSIONS}",
        )
        cfg.attributes["module_plane_selections"] = (
            ModulePlaneSelection(
                module="numbering",
                planes=(ModulePlane.TENANT, ModulePlane.PLATFORM),
            ),
        )
        admin_url = _url_for(superuser, name, user="app_admin")
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(cfg, "heads")
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


def _make_tenants(admin_url: str, count: int = 2) -> list[uuid.UUID]:
    ids = [uuid.uuid4() for _ in range(count)]
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        for tenant in ids:
            conn.execute(
                text(
                    "INSERT INTO public.tenants (id, name, slug, is_active) "
                    "VALUES (:id, :n, :s, true)"
                ),
                {"id": tenant, "n": f"t-{tenant.hex[:8]}", "s": tenant.hex[:8]},
            )
    engine.dispose()
    return ids


def _tenant_engine(url: str, tenant_id: uuid.UUID | None = None):
    """An engine whose every connection carries the tenant GUC.

    Setting the GUC once on a session is not enough: `commit()` returns the
    connection to the pool, and the next statement can arrive on a different
    one with no `app.current_tenant` set — at which point the RLS policy
    refuses the write and the failure looks like a module bug rather than a
    harness bug.
    """
    engine = create_engine(url)
    if tenant_id is not None:

        @event.listens_for(engine, "connect")
        def _set_tenant(dbapi_connection, _record):
            with dbapi_connection.cursor() as cur:
                cur.execute(
                    "SELECT set_config('app.current_tenant', %s, false)",
                    (str(tenant_id),),
                )

    return engine


def _session(url: str, tenant_id: uuid.UUID | None = None) -> Session:
    """A session on its OWN engine, so concurrency is real."""
    return Session(_tenant_engine(url, tenant_id))


def _configure(session: Session, scope, **kw) -> None:
    base: dict[str, object] = {"series_code": "invoice", "prefix": "INV"}
    base.update(kw)
    configure_series(
        session,
        scope=scope,
        configuration=SeriesConfiguration(**base),  # type: ignore[arg-type]
    )
    session.commit()


def _seed_one_row(admin_url: str, table: str) -> None:
    """Insert one row into an append-only table, as the owning role.

    Needed because a `BEFORE UPDATE OR DELETE ... FOR EACH ROW` trigger does
    not fire when the statement matches no rows — an UPDATE against an empty
    table succeeds trivially and would make the refusal proof vacuous.
    """
    tenant_cols, tenant_vals = "", ""
    if not table.startswith("platform_"):
        (tenant,) = _make_tenants(admin_url, 1)
        tenant_cols, tenant_vals = "tenant_id, ", f"'{tenant}', "

    if table.endswith("allocation_receipts"):
        columns = (
            "series_code, period_key, allocated_value, formatted_number, "
            "reference_date, idempotency_key"
        )
        values = "'seed', '*', 1, 'SEED-000001', DATE '2026-01-15', 'seed-key'"
    else:
        columns = (
            "series_code, period_key, previous_next_value, new_next_value, "
            "proven_minimum, reason, repaired_by"
        )
        values = "'seed', '*', 1, 2, 1, 'seed', 'ops:seed'"

    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(
            text(
                # Every interpolated part is a literal chosen above, not input.
                f"INSERT INTO mod_numbering.{table} "  # noqa: S608
                f"(id, {tenant_cols}{columns}) "
                f"VALUES (gen_random_uuid(), {tenant_vals}{values})"
            )
        )
    engine.dispose()


# ── Proof 1 — tenant RLS ────────────────────────────────────────────────────


def test_proof_1_a_tenant_sees_neither_series_counters_nor_receipts_of_another(scratch):
    admin_url, user_url, _ = scratch
    left, right = _make_tenants(admin_url)

    with _session(user_url, left) as s:
        _configure(s, TenantScope(tenant_id=left))
        allocate(
            s,
            scope=TenantScope(tenant_id=left),
            series_code="invoice",
            reference_date=JAN,
            idempotency_key="left-1",
        )
        s.commit()

    with _session(user_url, right) as s:
        assert s.query(NumberSeries).count() == 0
        assert s.query(AllocationReceipt).count() == 0


def test_proof_1_hostile_without_the_policy_the_rows_are_visible(scratch):
    """Removes the guard and asserts the leak, so proof 1 is measuring RLS and
    not merely an empty table."""
    admin_url, user_url, _ = scratch
    left, right = _make_tenants(admin_url)
    with _session(user_url, left) as s:
        _configure(s, TenantScope(tenant_id=left))

    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        table = "mod_numbering.number_series"
        conn.execute(text(f"DROP POLICY number_series_tenant_isolation ON {table}"))
        conn.execute(text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        conn.execute(text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
    engine.dispose()

    with _session(user_url, right) as s:
        assert s.query(NumberSeries).count() == 1, (
            "with RLS removed the other tenant's series must be visible; if it "
            "is not, proof 1 is measuring something other than the policy"
        )


# ── Proof 2 — platform revocation, and control-plane reachability ───────────


@pytest.mark.parametrize("table", PLATFORM_TABLES)
def test_proof_2_the_tenant_role_is_revoked_from_every_platform_table(scratch, table):
    _, user_url, _ = scratch
    engine = create_engine(user_url)
    with engine.connect() as conn, pytest.raises(DBAPIError) as exc:
        # Table name is a module-level literal, not input. noqa: the
        # check cannot see that.
        conn.execute(
            text(f"SELECT 1 FROM mod_numbering.{table} LIMIT 1")  # noqa: S608
        )
    assert "permission denied" in str(exc.value).lower()
    engine.dispose()


@pytest.mark.parametrize("table", PLATFORM_TABLES)
def test_proof_2_the_control_plane_role_can_still_read(scratch, table):
    """A revocation that also locked out `platform_api` would pass the test
    above and break the control plane."""
    _, _, platform_url = scratch
    engine = create_engine(platform_url)
    with engine.connect() as conn:
        # Table name is a module-level literal, not input. noqa: the
        # check cannot see that.
        conn.execute(
            text(f"SELECT 1 FROM mod_numbering.{table} LIMIT 1")  # noqa: S608
        )
    engine.dispose()


@pytest.mark.parametrize("table", (*TENANT_MUTABLE, *TENANT_IMMUTABLE))
def test_proof_2_tenant_tables_force_rls(scratch, table):
    admin_url, _, _ = scratch
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE oid = CAST(:t AS regclass)"
            ),
            {"t": f"mod_numbering.{table}"},
        ).one()
    assert row == (True, True)
    engine.dispose()


# ── Proof 3 — the allocation race, and a hostile version without the lock ───


def _race(
    url: str,
    tenant: uuid.UUID,
    keys: tuple[str, str],
    *,
    barrier_before: bool = True,
    also_abort: tuple[threading.Barrier, ...] = (),
) -> tuple[list, list]:
    """Two allocations in parallel.

    `barrier_before` rendezvouses before `allocate()`, which is right when the
    lock is present: it proves both transactions are open. The hostile variant
    passes False and rendezvouses INSIDE the counter read instead — waiting
    twice would deadlock, and waiting only before the call is exactly the weak
    version this replaced.
    """
    barrier = threading.Barrier(2)
    results: list = []
    errors: list[BaseException] = []

    def worker(key: str) -> None:
        try:
            with _session(url, tenant) as s:
                s.execute(text("SELECT 1"))
                if barrier_before:
                    barrier.wait(timeout=15)
                out = allocate(
                    s,
                    scope=TenantScope(tenant_id=tenant),
                    series_code="invoice",
                    reference_date=JAN,
                    idempotency_key=key,
                )
                s.commit()
                results.append(out)
        except BaseException as exc:
            errors.append(exc)
            # Release a peer that may be waiting, on either barrier, so a
            # failure surfaces as an error rather than a 40-second hang.
            for b in (barrier, *also_abort):
                # A barrier already broken by the peer raises here; the point
                # is only to release anyone still waiting, so there is nothing
                # to handle and nothing to log.
                with contextlib.suppress(Exception):
                    b.abort()

    threads = [threading.Thread(target=worker, args=(k,)) for k in keys]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=40)
    return results, errors


def test_proof_3_concurrent_allocations_never_take_the_same_value(scratch):
    admin_url, user_url, _ = scratch
    (tenant,) = _make_tenants(admin_url, 1)
    with _session(user_url, tenant) as s:
        _configure(s, TenantScope(tenant_id=tenant))

    results, errors = _race(user_url, tenant, ("k1", "k2"))
    assert not errors, errors
    assert sorted(r.formatted_number for r in results) == ["INV-000001", "INV-000002"]


def test_proof_3_hostile_without_for_update_the_race_takes_the_same_value(
    scratch, monkeypatch
):
    """THE sensitivity proof for the lock, made deterministic.

    The barrier sits INSIDE the patched counter read, after both transactions
    have observed the same counter state, so neither can finish before the
    other looks. A barrier placed before `allocate()` only guarantees both
    threads *started*; one can still complete the whole allocation before the
    other reads, and the test then sees two valid numbers and passes for the
    wrong reason.

    Removing `FOR UPDATE` must make the race fail: either both format the same
    number, or the second insert trips a receipt unique constraint. A duplicate
    hand-inserted by the test would prove the index and say nothing about the
    lock.
    """
    admin_url, user_url, _ = scratch
    (tenant,) = _make_tenants(admin_url, 1)
    with _session(user_url, tenant) as s:
        _configure(s, TenantScope(tenant_id=tenant))

    from dotmac_numbering import service as svc

    read_barrier = threading.Barrier(2)

    def unlocked_counter(db, scope, series, period_key):
        counter_model = svc._models(scope)[1]
        stmt = select(counter_model).where(
            counter_model.series_code == series.series_code,
            counter_model.period_key == period_key,
        )
        stmt = svc._tenant_filter(stmt, scope, counter_model)
        row = db.execute(stmt).scalar_one_or_none()

        # Both actors have now read the SAME counter state — or both observed
        # its absence. Only then may either proceed.
        read_barrier.wait(timeout=15)

        if row is None:
            values = {
                "id": uuid.uuid4(),
                "series_code": series.series_code,
                "period_key": period_key,
                "next_value": series.start_value,
            }
            if isinstance(scope, TenantScope):
                values["tenant_id"] = scope.tenant_id
            row = counter_model(**values)
            db.add(row)
            db.flush()
        return row

    monkeypatch.setattr(svc, "_locked_counter", unlocked_counter)

    results, errors = _race(
        user_url,
        tenant,
        ("h1", "h2"),
        barrier_before=False,
        also_abort=(read_barrier,),
    )
    numbers = [r.formatted_number for r in results]
    assert errors or len(set(numbers)) < 2, (
        "with FOR UPDATE removed and both readers held at the same counter "
        f"state, the race must duplicate a value or fail; it produced {numbers} "
        "cleanly, so proof 3 is not measuring the lock"
    )


def test_proof_3_concurrent_identical_keys_replay_rather_than_conflict(scratch):
    """The hole a hand-rolled receipt lookup leaves.

    Both callers miss the receipt before locking, so a home-grown
    implementation has the loser allocate a second number and raise
    IntegrityError. The kernel ledger converts that race into a replay.
    """
    admin_url, user_url, _ = scratch
    (tenant,) = _make_tenants(admin_url, 1)
    with _session(user_url, tenant) as s:
        _configure(s, TenantScope(tenant_id=tenant))

    results, errors = _race(user_url, tenant, ("same", "same"))
    assert not errors, errors
    assert len({r.formatted_number for r in results}) == 1
    assert sorted(r.replayed for r in results) == [False, True]

    with _session(user_url, tenant) as s:
        assert s.query(AllocationReceipt).count() == 1


# ── Proof 4 — rollback with the consuming transaction ───────────────────────


def test_proof_4_a_rolled_back_caller_consumes_no_number(scratch):
    admin_url, user_url, _ = scratch
    (tenant,) = _make_tenants(admin_url, 1)
    scope = TenantScope(tenant_id=tenant)
    with _session(user_url, tenant) as s:
        _configure(s, scope)

    with _session(user_url, tenant) as s:
        allocate(
            s,
            scope=scope,
            series_code="invoice",
            reference_date=JAN,
            idempotency_key="doomed",
        )
        s.rollback()

    with _session(user_url, tenant) as s:
        assert s.query(AllocationReceipt).count() == 0
        out = allocate(
            s,
            scope=scope,
            series_code="invoice",
            reference_date=JAN,
            idempotency_key="real",
        )
        s.commit()
    assert out.formatted_number == "INV-000001"


# ── Proof 5 — replay and conflict, through the kernel ledger ────────────────


def test_proof_5_same_key_same_request_replays(scratch):
    admin_url, user_url, _ = scratch
    (tenant,) = _make_tenants(admin_url, 1)
    scope = TenantScope(tenant_id=tenant)
    with _session(user_url, tenant) as s:
        _configure(s, scope)
        first = allocate(
            s,
            scope=scope,
            series_code="invoice",
            reference_date=JAN,
            idempotency_key="retry",
        )
        s.commit()
        second = allocate(
            s,
            scope=scope,
            series_code="invoice",
            reference_date=JAN,
            idempotency_key="retry",
        )
        s.commit()
        assert second.formatted_number == first.formatted_number
        assert second.replayed is True
        assert s.query(AllocationReceipt).count() == 1


def test_proof_5_same_key_different_request_conflicts(scratch):
    """The kernel raises its own conflict type — this module does not
    reimplement the comparison (hard rule 23)."""
    admin_url, user_url, _ = scratch
    (tenant,) = _make_tenants(admin_url, 1)
    scope = TenantScope(tenant_id=tenant)
    with _session(user_url, tenant) as s:
        _configure(s, scope)
        allocate(
            s,
            scope=scope,
            series_code="invoice",
            reference_date=JAN,
            idempotency_key="reused",
        )
        s.commit()
        with pytest.raises(IdempotencyConflict):
            allocate(
                s,
                scope=scope,
                series_code="invoice",
                reference_date=FEB,
                idempotency_key="reused",
            )


# ── Proof 6 — per-period counters ───────────────────────────────────────────


def test_proof_6_each_period_has_its_own_counter(scratch):
    """Yearly reset reuses value 1, which is only sound because the receipt
    identity includes the period."""
    admin_url, user_url, _ = scratch
    (tenant,) = _make_tenants(admin_url, 1)
    scope = TenantScope(tenant_id=tenant)
    with _session(user_url, tenant) as s:
        _configure(s, scope, reset_policy="yearly", include_year=True)
        this_year = allocate(
            s,
            scope=scope,
            series_code="invoice",
            reference_date=JAN,
            idempotency_key="y1",
        )
        s.commit()
        next_year = allocate(
            s,
            scope=scope,
            series_code="invoice",
            reference_date=NEXT_YEAR,
            idempotency_key="y2",
        )
        s.commit()

    assert this_year.formatted_number == "INV-2026-000001"
    assert next_year.formatted_number == "INV-2027-000001"
    assert (this_year.period_key, next_year.period_key) == ("2026", "2027")


def test_proof_6_a_backdated_allocation_continues_its_own_period(scratch):
    """The failure a single counter cannot avoid.

    After rolling into 2027, a backdated 2026 allocation must take the NEXT
    2026 value — not the current 2027 counter formatted with a 2026 year,
    which could duplicate a 2026 number already issued.
    """
    admin_url, user_url, _ = scratch
    (tenant,) = _make_tenants(admin_url, 1)
    scope = TenantScope(tenant_id=tenant)
    with _session(user_url, tenant) as s:
        _configure(s, scope, reset_policy="yearly", include_year=True)
        for i in range(3):
            allocate(
                s,
                scope=scope,
                series_code="invoice",
                reference_date=DEC,
                idempotency_key=f"d{i}",
            )
            s.commit()
        allocate(
            s,
            scope=scope,
            series_code="invoice",
            reference_date=NEXT_YEAR,
            idempotency_key="n1",
        )
        s.commit()
        backdated = allocate(
            s,
            scope=scope,
            series_code="invoice",
            reference_date=DEC,
            idempotency_key="late",
        )
        s.commit()

    # Three 2026 numbers already issued, so the backdated one is the fourth of
    # 2026 — not the second of 2027 wearing a 2026 year.
    assert backdated.formatted_number == "INV-2026-000004"
    assert backdated.period_key == "2026"


def test_proof_6_preview_reads_the_period_of_the_date_it_is_given(scratch):
    admin_url, user_url, _ = scratch
    (tenant,) = _make_tenants(admin_url, 1)
    scope = TenantScope(tenant_id=tenant)
    with _session(user_url, tenant) as s:
        _configure(s, scope, reset_policy="yearly", include_year=True)
        allocate(
            s,
            scope=scope,
            series_code="invoice",
            reference_date=JAN,
            idempotency_key="p1",
        )
        s.commit()
        assert preview(s, scope=scope, series_code="invoice", reference_date=JAN) == (
            "INV-2026-000002"
        )
        assert (
            preview(s, scope=scope, series_code="invoice", reference_date=NEXT_YEAR)
            == "INV-2027-000001"
        )


# ── Proof 7 — structural immutability, and repair evidence ──────────────────


@pytest.mark.parametrize("table", TENANT_IMMUTABLE)
def test_proof_7_raw_update_and_delete_are_refused_on_append_only_tables(
    scratch, table
):
    """Not a service promise — a database property.

    A service-level rule would not survive the first hand-written migration
    that "fixes up" a number, which is exactly what ERP's reset_sequence does.
    """
    admin_url, user_url, _ = scratch
    (tenant,) = _make_tenants(admin_url, 1)
    scope = TenantScope(tenant_id=tenant)
    with _session(user_url, tenant) as s:
        _configure(s, scope)
        allocate(
            s,
            scope=scope,
            series_code="invoice",
            reference_date=JAN,
            idempotency_key="k1",
        )
        advance_to_at_least(
            s,
            scope=scope,
            series_code="invoice",
            period_key="*",
            proven_minimum=50,
            reason="backfill",
            repaired_by="ops:ada",
        )
        s.commit()

    engine = _tenant_engine(user_url, tenant)
    with engine.connect() as conn:
        with pytest.raises(DBAPIError):
            conn.execute(
                text(
                    # Table name is a literal from a fixed tuple, not input.
                    f"UPDATE mod_numbering.{table} SET series_code = 'x'"  # noqa: S608
                )
            )
    engine.dispose()

    engine = _tenant_engine(user_url, tenant)
    with engine.connect() as conn:
        with pytest.raises(DBAPIError):
            conn.execute(
                text(f"DELETE FROM mod_numbering.{table}")  # noqa: S608
            )
    engine.dispose()


@pytest.mark.parametrize("table", (*TENANT_IMMUTABLE, *PLATFORM_IMMUTABLE))
def test_proof_7_even_the_owner_role_cannot_rewrite_history(scratch, table):
    """The trigger covers roles the grants do not — `app_admin` owns the schema
    and would otherwise be able to update freely."""
    admin_url, _, _ = scratch
    # A row must exist: `BEFORE UPDATE ... FOR EACH ROW` never fires on an
    # empty table, so an UPDATE affecting nothing would "pass" this test while
    # proving nothing at all.
    _seed_one_row(admin_url, table)

    engine = create_engine(admin_url)
    with engine.connect() as conn, pytest.raises(DBAPIError) as exc:
        conn.execute(
            text(
                # Table name is a literal from a fixed tuple, not input.
                f"UPDATE mod_numbering.{table} SET series_code = 'x'"  # noqa: S608
            )
        )
    assert "append-only" in str(exc.value).lower()
    engine.dispose()


@pytest.mark.parametrize("table", (*TENANT_IMMUTABLE, *PLATFORM_IMMUTABLE))
def test_proof_7_append_only_tables_have_no_updated_at(scratch, table):
    """A column recording a change to a row that cannot change is dead or a lie."""
    admin_url, _, _ = scratch
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        columns = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'mod_numbering' AND table_name = :t"
                ),
                {"t": table},
            )
        }
    assert "updated_at" not in columns
    engine.dispose()


def test_proof_7_repair_advances_names_its_period_and_leaves_evidence(scratch):
    admin_url, user_url, _ = scratch
    (tenant,) = _make_tenants(admin_url, 1)
    scope = TenantScope(tenant_id=tenant)
    with _session(user_url, tenant) as s:
        _configure(s, scope, reset_policy="yearly", include_year=True)
        allocate(
            s,
            scope=scope,
            series_code="invoice",
            reference_date=JAN,
            idempotency_key="k1",
        )
        s.commit()

        repair = advance_to_at_least(
            s,
            scope=scope,
            series_code="invoice",
            period_key="2026",
            proven_minimum=500,
            reason="ledger backfill",
            repaired_by="ops:ada",
        )
        s.commit()
        assert (repair.changed, repair.new_next_value) == (True, 501)

        # Below the current value is a no-op, and still leaves evidence that it
        # was attempted.
        again = advance_to_at_least(
            s,
            scope=scope,
            series_code="invoice",
            period_key="2026",
            proven_minimum=3,
            reason="mistake",
            repaired_by="ops:ada",
        )
        s.commit()
        assert (again.changed, again.new_next_value) == (False, 501)

        rows = s.query(SeriesRepair).order_by(SeriesRepair.repaired_at).all()
        assert [r.proven_minimum for r in rows] == [500, 3]
        assert {r.repaired_by for r in rows} == {"ops:ada"}
        assert [r.period_key for r in rows] == ["2026", "2026"]

        # The other period is untouched: repair names one counter.
        nxt = allocate(
            s,
            scope=scope,
            series_code="invoice",
            reference_date=NEXT_YEAR,
            idempotency_key="k2",
        )
        s.commit()
    assert nxt.formatted_number == "INV-2027-000001"


def test_proof_7_a_malformed_period_key_is_refused(scratch):
    """A typo would silently create a counter nothing ever reads."""
    admin_url, user_url, _ = scratch
    (tenant,) = _make_tenants(admin_url, 1)
    scope = TenantScope(tenant_id=tenant)
    with _session(user_url, tenant) as s:
        _configure(s, scope, reset_policy="yearly", include_year=True)
        s.commit()
        for bad in ("*", "26", "2026-13", "2026-1", "nope"):
            with pytest.raises(NumberingError) as exc:
                advance_to_at_least(
                    s,
                    scope=scope,
                    series_code="invoice",
                    period_key=bad,
                    proven_minimum=5,
                    reason="r",
                    repaired_by="ops:ada",
                )
            assert exc.value.code.endswith("invalid_period_key")


# ── Proof 8 — both planes run the same behaviour ────────────────────────────


def test_proof_8_both_planes_produce_the_same_number_for_the_same_input(scratch):
    admin_url, user_url, platform_url = scratch
    (tenant,) = _make_tenants(admin_url, 1)

    with _session(user_url, tenant) as s:
        _configure(s, TenantScope(tenant_id=tenant))
        tenant_number = allocate(
            s,
            scope=TenantScope(tenant_id=tenant),
            series_code="invoice",
            reference_date=JAN,
            idempotency_key="same",
        ).formatted_number
        s.commit()

    with _session(platform_url) as s:
        _configure(s, PlatformScope())
        platform_number = allocate(
            s,
            scope=PlatformScope(),
            series_code="invoice",
            reference_date=JAN,
            idempotency_key="same",
        ).formatted_number
        s.commit()

    assert tenant_number == platform_number == "INV-000001"


def test_an_unconfigured_series_fails_closed(scratch):
    admin_url, user_url, _ = scratch
    (tenant,) = _make_tenants(admin_url, 1)
    with _session(user_url, tenant) as s, pytest.raises(NumberingError) as exc:
        allocate(
            s,
            scope=TenantScope(tenant_id=tenant),
            series_code="typo",
            reference_date=JAN,
            idempotency_key="k",
        )
    assert exc.value.code.endswith("series_not_configured")


# ── Configuration cannot reissue a rendered number ──────────────────────────


def test_reconfiguring_a_series_that_has_allocated_is_refused(scratch):
    """The gap the value-and-period key cannot close.

    Switching an allocated series to a new period scheme would restart at the
    start value and re-render a string already issued, and the counter-value
    unique would permit it because the period differs.
    """
    admin_url, user_url, _ = scratch
    (tenant,) = _make_tenants(admin_url, 1)
    scope = TenantScope(tenant_id=tenant)
    with _session(user_url, tenant) as s:
        _configure(s, scope)
        allocate(
            s,
            scope=scope,
            series_code="invoice",
            reference_date=JAN,
            idempotency_key="k1",
        )
        s.commit()

        with pytest.raises(NumberingError) as exc:
            _configure(s, scope, reset_policy="yearly", include_year=True)
    assert exc.value.code.endswith("unsafe_configuration_change")


def test_reconfiguring_before_any_allocation_is_allowed(scratch):
    """Sensitivity proof for the freeze: it must not refuse everything."""
    admin_url, user_url, _ = scratch
    (tenant,) = _make_tenants(admin_url, 1)
    scope = TenantScope(tenant_id=tenant)
    with _session(user_url, tenant) as s:
        _configure(s, scope)
        _configure(s, scope, reset_policy="yearly", include_year=True, min_digits=4)
        out = allocate(
            s,
            scope=scope,
            series_code="invoice",
            reference_date=JAN,
            idempotency_key="k1",
        )
        s.commit()
    assert out.formatted_number == "INV-2026-0001"


def test_the_rendered_number_is_unique_even_if_a_transition_slips(scratch):
    """The database backstop behind the service rule.

    Written raw, as a migration or a psql session would, so the constraint is
    proven rather than the service path that avoids it.
    """
    admin_url, user_url, _ = scratch
    (tenant,) = _make_tenants(admin_url, 1)
    scope = TenantScope(tenant_id=tenant)
    with _session(user_url, tenant) as s:
        _configure(s, scope)
        allocate(
            s,
            scope=scope,
            series_code="invoice",
            reference_date=JAN,
            idempotency_key="k1",
        )
        s.commit()

    engine = _tenant_engine(user_url, tenant)
    with engine.connect() as conn:
        with pytest.raises(DBAPIError) as exc:
            conn.execute(
                text(
                    "INSERT INTO mod_numbering.allocation_receipts "
                    "(id, tenant_id, series_code, period_key, allocated_value, "
                    " formatted_number, reference_date, idempotency_key) "
                    "VALUES (:id, :t, 'invoice', '2099', 1, 'INV-000001', "
                    " :d, 'other')"
                ),
                {"id": uuid.uuid4(), "t": tenant, "d": JAN},
            )
    assert "uq_allocation_receipts_rendered" in str(exc.value)
    engine.dispose()


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("min_digits", 0),
        ("min_digits", 19),
        ("year_digits", 3),
        ("start_value", 0),
    ],
)
def test_check_constraints_refuse_a_bad_configuration_written_directly(
    scratch, column, value
):
    """The online role can write this table, so Python validation alone is not
    enforcement."""
    admin_url, user_url, _ = scratch
    (tenant,) = _make_tenants(admin_url, 1)
    with _session(user_url, tenant) as s:
        _configure(s, TenantScope(tenant_id=tenant))

    engine = _tenant_engine(user_url, tenant)
    with engine.connect() as conn:
        with pytest.raises(DBAPIError) as exc:
            conn.execute(
                text(
                    # Column name is a parametrize literal, not input.
                    f"UPDATE mod_numbering.number_series SET {column} = :v "  # noqa: S608
                    "WHERE tenant_id = :t"
                ),
                {"v": value, "t": tenant},
            )
    assert "violates check constraint" in str(exc.value).lower()
    engine.dispose()


def test_a_resetting_series_must_print_its_period_at_the_database(scratch):
    admin_url, user_url, _ = scratch
    (tenant,) = _make_tenants(admin_url, 1)
    with _session(user_url, tenant) as s:
        _configure(s, TenantScope(tenant_id=tenant))

    engine = _tenant_engine(user_url, tenant)
    with engine.connect() as conn:
        with pytest.raises(DBAPIError) as exc:
            conn.execute(
                text(
                    "UPDATE mod_numbering.number_series "
                    "SET reset_policy = 'yearly', include_year = 0 "
                    "WHERE tenant_id = :t"
                ),
                {"t": tenant},
            )
    assert "violates check constraint" in str(exc.value).lower()
    engine.dispose()


# ── Configuration and allocation linearize on the series row ────────────────


def _configure_vs_allocate(
    user_url: str, tenant: uuid.UUID, *, lock_removed: bool = False
) -> tuple[list, list]:
    """Race a reconfiguration against the series' FIRST allocation.

    Both threads open a transaction, rendezvous, then proceed. With the series
    lock exactly two serial orders are possible:

    * allocate first — the counter and receipt exist, so the later
      `configure_series` sees history and refuses;
    * configure first — the allocation reads the NEW configuration and formats
      with it.

    The state that must never occur is the torn one: the configuration commits
    AND the allocation renders from the old configuration.
    """
    barrier = threading.Barrier(2)
    results: dict[str, object] = {}
    errors: list[BaseException] = []

    def allocator() -> None:
        try:
            with _session(user_url, tenant) as s:
                s.execute(text("SELECT 1"))
                barrier.wait(timeout=15)
                out = allocate(
                    s,
                    scope=TenantScope(tenant_id=tenant),
                    series_code="invoice",
                    reference_date=JAN,
                    idempotency_key="race",
                )
                s.commit()
                results["number"] = out.formatted_number
        except BaseException as exc:
            errors.append(exc)
            with contextlib.suppress(Exception):
                barrier.abort()

    def configurer() -> None:
        try:
            with _session(user_url, tenant) as s:
                s.execute(text("SELECT 1"))
                barrier.wait(timeout=15)
                _configure(s, TenantScope(tenant_id=tenant), min_digits=3)
                results["configured"] = True
        except NumberingError as exc:
            results["configure_refused"] = exc.code
        except BaseException as exc:
            errors.append(exc)
            with contextlib.suppress(Exception):
                barrier.abort()

    threads = [threading.Thread(target=t) for t in (allocator, configurer)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=40)
    return [results], errors


def test_configuration_and_first_allocation_linearize(scratch):
    admin_url, user_url, _ = scratch
    (tenant,) = _make_tenants(admin_url, 1)
    with _session(user_url, tenant) as s:
        _configure(s, TenantScope(tenant_id=tenant), min_digits=6)

    (results,), errors = _configure_vs_allocate(user_url, tenant)
    assert not errors, errors

    number = results.get("number")
    assert number is not None, "the allocation must complete under either order"
    if results.get("configured"):
        # Configure won the row: the allocation must have used the new width.
        assert number == "INV-001", (
            f"configuration committed but the number is {number!r} — that is a "
            "torn read of the series row"
        )
    else:
        # Allocate won: history now exists, so the configuration is refused.
        assert number == "INV-000001"
        assert results.get("configure_refused", "").endswith(
            "unsafe_configuration_change"
        )


def test_hostile_without_the_series_lock_configuration_and_allocation_tear(
    scratch, monkeypatch
):
    """Sensitivity proof for the series lock, driven rather than raced.

    A shared release barrier is not enough here: after it, either thread may
    win, and an allocation that wins legitimately causes the configuration to
    be refused. That outcome is correct, so the test would fail on it — the
    assertion could only be salvaged with an `or errors` escape that accepts
    almost anything.

    Directed events force the one interleaving that exposes the missing lock:

        allocation takes a STALE read  ->  configuration commits  ->  allocation resumes

    With the lock present that ordering cannot occur, because the
    configuration would block on the series row. With it removed the
    allocation renders from configuration that no longer exists, and the exact
    torn state is asserted — no disjunction, no error escape.
    """
    admin_url, user_url, _ = scratch
    (tenant,) = _make_tenants(admin_url, 1)
    with _session(user_url, tenant) as s:
        _configure(s, TenantScope(tenant_id=tenant), min_digits=6)

    from dotmac_numbering import service as svc

    allocation_has_read = threading.Event()
    configuration_committed = threading.Event()
    original = svc._find_series

    def unlocked_and_directed(db, scope, series_code, *, lock=False):
        # The guard under test: the FOR UPDATE is dropped.
        row = original(db, scope, series_code, lock=False)
        if threading.current_thread().name == "allocator":
            allocation_has_read.set()
            # Hold the stale read until the reconfiguration has committed.
            configuration_committed.wait(timeout=20)
        return row

    monkeypatch.setattr(svc, "_find_series", unlocked_and_directed)

    results: dict[str, object] = {}
    errors: list[BaseException] = []

    def allocator() -> None:
        try:
            with _session(user_url, tenant) as s:
                out = allocate(
                    s,
                    scope=TenantScope(tenant_id=tenant),
                    series_code="invoice",
                    reference_date=JAN,
                    idempotency_key="torn",
                )
                s.commit()
                results["number"] = out.formatted_number
        except BaseException as exc:
            errors.append(exc)
            configuration_committed.set()

    def configurer() -> None:
        try:
            assert allocation_has_read.wait(timeout=20), "allocator never read"
            with _session(user_url, tenant) as s:
                _configure(s, TenantScope(tenant_id=tenant), min_digits=3)
                results["configured"] = True
        except BaseException as exc:
            errors.append(exc)
        finally:
            configuration_committed.set()

    threads = [
        threading.Thread(target=allocator, name="allocator"),
        threading.Thread(target=configurer, name="configurer"),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=40)

    assert not errors, errors
    assert results.get("configured") is True, (
        "the reconfiguration must commit while the allocation holds its stale "
        "read — it is what makes the read stale"
    )
    assert results.get("number") == "INV-000001", (
        "with FOR UPDATE removed the allocation must render from the "
        f"superseded six-digit configuration; got {results.get('number')!r}. "
        "If this is 'INV-001' the allocation somehow saw the new value and the "
        "linearization proof is not measuring the series lock."
    )


# ── start_value is a prospective seed, never part of the identity ───────────


def test_start_value_only_seeds_periods_that_have_not_opened(scratch):
    """Three claims in one, because they are one rule.

    Raising `start_value` must leave an already-open period's counter exactly
    where it is, seed a period that has not opened yet, and — for a
    non-resetting series, whose single `*` counter is always already open —
    change nothing at all.
    """
    admin_url, user_url, _ = scratch
    (tenant,) = _make_tenants(admin_url, 1)
    scope = TenantScope(tenant_id=tenant)

    with _session(user_url, tenant) as s:
        _configure(s, scope, reset_policy="yearly", include_year=True, start_value=1)
        first = allocate(
            s,
            scope=scope,
            series_code="invoice",
            reference_date=JAN,
            idempotency_key="a",
        )
        s.commit()
        assert first.formatted_number == "INV-2026-000001"

        # Permitted after history: start_value is not an identity field.
        _configure(s, scope, reset_policy="yearly", include_year=True, start_value=500)

        # The OPEN period is untouched — its counter already exists.
        same_period = allocate(
            s,
            scope=scope,
            series_code="invoice",
            reference_date=FEB,
            idempotency_key="b",
        )
        s.commit()
        assert same_period.formatted_number == "INV-2026-000002"

        # A period that has not opened yet inherits the new seed.
        future = allocate(
            s,
            scope=scope,
            series_code="invoice",
            reference_date=NEXT_YEAR,
            idempotency_key="c",
        )
        s.commit()
        assert future.formatted_number == "INV-2027-000500"


def test_start_value_does_not_move_an_existing_non_resetting_counter(scratch):
    """The `*` counter is always already open, so nothing can reseed it."""
    admin_url, user_url, _ = scratch
    (tenant,) = _make_tenants(admin_url, 1)
    scope = TenantScope(tenant_id=tenant)

    with _session(user_url, tenant) as s:
        _configure(s, scope, start_value=1)
        allocate(
            s,
            scope=scope,
            series_code="invoice",
            reference_date=JAN,
            idempotency_key="a",
        )
        s.commit()
        _configure(s, scope, start_value=9000)
        nxt = allocate(
            s,
            scope=scope,
            series_code="invoice",
            reference_date=JAN,
            idempotency_key="b",
        )
        s.commit()
    assert nxt.formatted_number == "INV-000002"


def _reseed_while_a_period_opens(
    user_url: str, tenant: uuid.UUID, *, hold_seconds: float
) -> dict[str, object]:
    """Open a new period and hold the transaction; reconfigure concurrently.

    Returns what the reconfiguration was able to do WHILE the allocation still
    held its transaction. That is the observable that distinguishes a lock from
    no lock — checking only the final seed cannot, because 1 and 700 are both
    reachable either way.
    """
    scope = TenantScope(tenant_id=tenant)
    allocation_open = threading.Event()
    release_allocation = threading.Event()
    observed: dict[str, object] = {}
    errors: list[BaseException] = []

    def opener() -> None:
        try:
            with _session(user_url, tenant) as s:
                out = allocate(
                    s,
                    scope=scope,
                    series_code="invoice",
                    reference_date=NEXT_YEAR,
                    idempotency_key="new-period",
                )
                # Transaction still open, series row still held (when locked).
                allocation_open.set()
                release_allocation.wait(timeout=20)
                s.commit()
                observed["value"] = out.value
        except BaseException as exc:
            errors.append(exc)
            allocation_open.set()

    def reseeder() -> None:
        try:
            assert allocation_open.wait(timeout=20)
            with _session(user_url, tenant) as s:
                _configure(
                    s,
                    scope,
                    reset_policy="yearly",
                    include_year=True,
                    start_value=700,
                )
                observed["reconfigured"] = True
        except NumberingError as exc:
            observed["refused"] = exc.code
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=opener), threading.Thread(target=reseeder)]
    threads[0].start()
    threads[1].start()

    # Give the reseeder a real chance to finish while the allocation is held.
    # If it can, there is no lock.
    threads[1].join(timeout=hold_seconds)
    observed["completed_while_held"] = not threads[1].is_alive()
    release_allocation.set()
    for t in threads:
        t.join(timeout=40)

    observed["errors"] = errors
    return observed


def test_start_value_reconfiguration_blocks_while_a_period_is_opening(scratch):
    """The lock is observed by BLOCKING, not by the final value.

    A previous version of this test asserted only that the new period seeded
    from 1 or 700. Both are reachable without any lock at all, so it proved
    nothing about linearization — it was an allowed-outcomes smoke test.

    Here the allocation opens a new period and holds its transaction. The
    reconfiguration must not be able to complete during that window, because
    it needs the same series row.
    """
    admin_url, user_url, _ = scratch
    (tenant,) = _make_tenants(admin_url, 1)
    with _session(user_url, tenant) as s:
        _configure(
            s,
            TenantScope(tenant_id=tenant),
            reset_policy="yearly",
            include_year=True,
            start_value=1,
        )
        allocate(
            s,
            scope=TenantScope(tenant_id=tenant),
            series_code="invoice",
            reference_date=JAN,
            idempotency_key="seed",
        )
        s.commit()

    observed = _reseed_while_a_period_opens(user_url, tenant, hold_seconds=2.0)

    assert not observed["errors"], observed["errors"]
    assert observed["completed_while_held"] is False, (
        "the reconfiguration completed while the allocation still held the "
        "series row — the series lock is not being taken"
    )
    # Once the allocation commits, the reconfiguration proceeds and SUCCEEDS:
    # it changes only `start_value`, which is a prospective seed and therefore
    # deliberately mutable after history. It is the blocking above that the
    # lock is responsible for, not a refusal.
    assert observed.get("reconfigured") is True, (
        "changing only start_value must be permitted after history; a refusal "
        "here would mean the freeze is over-broad"
    )
    # The period that opened first seeded from the value in force at the time.
    assert observed["value"] == 1

    # And the new seed governs the next period that has not opened yet.
    with _session(user_url, tenant) as s:
        later = allocate(
            s,
            scope=TenantScope(tenant_id=tenant),
            series_code="invoice",
            reference_date=date(2028, 3, 1),
            idempotency_key="later",
        )
        s.commit()
    assert later.value == 700


def test_hostile_without_the_series_lock_the_reseed_does_not_block(
    scratch, monkeypatch
):
    """Guard-removal companion for the blocking proof.

    With `FOR UPDATE` dropped, the reconfiguration sails past an allocation
    that is still holding its transaction. If this test ever fails, the proof
    above is measuring something other than the lock.
    """
    admin_url, user_url, _ = scratch
    (tenant,) = _make_tenants(admin_url, 1)
    with _session(user_url, tenant) as s:
        _configure(
            s,
            TenantScope(tenant_id=tenant),
            reset_policy="yearly",
            include_year=True,
            start_value=1,
        )
        allocate(
            s,
            scope=TenantScope(tenant_id=tenant),
            series_code="invoice",
            reference_date=JAN,
            idempotency_key="seed",
        )
        s.commit()

    from dotmac_numbering import service as svc

    original = svc._find_series

    def unlocked(db, scope, series_code, *, lock=False):
        return original(db, scope, series_code, lock=False)

    monkeypatch.setattr(svc, "_find_series", unlocked)

    observed = _reseed_while_a_period_opens(user_url, tenant, hold_seconds=5.0)

    assert not observed["errors"], observed["errors"]
    assert observed["completed_while_held"] is True, (
        "with the series lock removed the reconfiguration must NOT block on an "
        "allocation that is still holding its transaction; it blocked anyway, "
        "so the blocking proof is not attributable to the lock"
    )


# ── The freeze is enforced by the database, not only by the service ─────────


@pytest.mark.parametrize(
    ("column", "value"),
    [("prefix", "'XX'"), ("min_digits", "3"), ("reset_policy", "'yearly'")],
)
def test_raw_sql_cannot_change_an_identity_field_after_history(scratch, column, value):
    """`configure_series` refuses first and explains better, but the online
    roles can write this table directly, so the rule has to live in the
    database too."""
    admin_url, user_url, _ = scratch
    (tenant,) = _make_tenants(admin_url, 1)
    scope = TenantScope(tenant_id=tenant)
    with _session(user_url, tenant) as s:
        _configure(s, scope)
        allocate(
            s,
            scope=scope,
            series_code="invoice",
            reference_date=JAN,
            idempotency_key="k1",
        )
        s.commit()

    engine = _tenant_engine(user_url, tenant)
    with engine.connect() as conn, pytest.raises(DBAPIError) as exc:
        conn.execute(
            text(
                # Column and value are parametrize literals, not input.
                f"UPDATE mod_numbering.number_series SET {column} = {value} "  # noqa: S608
                "WHERE tenant_id = :t"
            ),
            {"t": tenant},
        )
    assert "frozen" in str(exc.value).lower()
    engine.dispose()


def test_raw_sql_may_still_change_start_value_after_history(scratch):
    """Sensitivity proof for the freeze trigger: it must not refuse everything.

    `start_value` is a prospective seed, not part of the rendered identity, so
    the trigger has to let it through.
    """
    admin_url, user_url, _ = scratch
    (tenant,) = _make_tenants(admin_url, 1)
    scope = TenantScope(tenant_id=tenant)
    with _session(user_url, tenant) as s:
        _configure(s, scope)
        allocate(
            s,
            scope=scope,
            series_code="invoice",
            reference_date=JAN,
            idempotency_key="k1",
        )
        s.commit()

    engine = _tenant_engine(user_url, tenant)
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE mod_numbering.number_series SET start_value = 42 "
                "WHERE tenant_id = :t"
            ),
            {"t": tenant},
        )
    engine.dispose()

    with _session(user_url, tenant) as s:
        assert s.query(NumberSeries).one().start_value == 42
